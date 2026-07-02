from __future__ import annotations

bl_info = {
    "name": "BlenderGroomToUE",
    "author": "HIGASHI-BOOM, Codex",
    "version": (0, 1, 0),
    "blender": (5, 1, 0),
    "location": "3D 视图 > 侧边栏 > UE Groom",
    "description": "把 Blender 粒子毛发整理为 Unreal Engine Groom Alembic，保留分组和发丝分段点。",
    "category": "Import-Export",
}

import json
import os
import re
import subprocess
import tempfile
from collections import Counter, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty
from bpy.types import Operator, Panel, PropertyGroup
from mathutils import Matrix, Vector


ADDON_COLLECTION = "UE_Groom_Export"


def clean_name(value: str) -> str:
    value = re.sub(r"[^\w.-]+", "_", value.strip(), flags=re.UNICODE)
    return value.strip("_") or "Groom"


def clean_abc_object_name(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    value = value.strip("_") or "Groom"
    if value[0].isdigit():
        value = f"G_{value}"
    return value


def groom_abc_writer_path() -> Path:
    candidates = [
        Path(__file__).resolve().parent / "bin" / "groom_abc_writer.exe",
        Path(r"E:\UE\GroomSegmentExporter\groom_segment_exporter\bin\groom_abc_writer.exe"),
        Path(r"E:\UE\GroomSegmentExporter\tools\groom_abc_writer.exe"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise RuntimeError("找不到 groom_abc_writer.exe。请重新运行 install_addon.ps1，或确认插件 bin 目录存在。")


def ensure_collection(name: str = ADDON_COLLECTION) -> bpy.types.Collection:
    collection = bpy.data.collections.get(name)
    if collection is None:
        collection = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(collection)
    return collection


def unlink_from_collections(obj: bpy.types.Object) -> None:
    for collection in list(obj.users_collection):
        collection.objects.unlink(obj)


def iter_particle_modifiers(obj: bpy.types.Object) -> Iterable[bpy.types.ParticleSystemModifier]:
    for modifier in obj.modifiers:
        if modifier.type == "PARTICLE_SYSTEM" and modifier.particle_system:
            yield modifier


def iter_hair_systems(scene: bpy.types.Scene):
    for obj in scene.objects:
        for modifier in iter_particle_modifiers(obj):
            system = modifier.particle_system
            settings = system.settings
            if settings and settings.type == "HAIR":
                yield obj, modifier, system


def selected_or_all_emitters(scene: bpy.types.Scene, selected_only: bool) -> list[bpy.types.Object]:
    if not selected_only:
        return list(scene.objects)
    selected = set(bpy.context.selected_objects)
    return [obj for obj in scene.objects if obj in selected]


def mesh_component_summary(obj: bpy.types.Object) -> dict:
    if obj.type != "MESH" or not obj.data:
        return {}

    mesh = obj.data
    vertex_edges: list[list[int]] = [[] for _ in mesh.vertices]
    for edge in mesh.edges:
        a, b = edge.vertices
        vertex_edges[a].append(edge.index)
        vertex_edges[b].append(edge.index)

    visited: set[int] = set()
    components: list[tuple[int, int]] = []
    for vertex in mesh.vertices:
        if vertex.index in visited:
            continue
        queue: deque[int] = deque([vertex.index])
        visited.add(vertex.index)
        vertex_count = 0
        edge_indices: set[int] = set()
        while queue:
            vertex_index = queue.popleft()
            vertex_count += 1
            for edge_index in vertex_edges[vertex_index]:
                edge_indices.add(edge_index)
                for linked_vertex in mesh.edges[edge_index].vertices:
                    if linked_vertex not in visited:
                        visited.add(linked_vertex)
                        queue.append(linked_vertex)
        components.append((vertex_count, len(edge_indices)))

    polygon_sizes = Counter(len(poly.vertices) for poly in mesh.polygons)
    component_sizes = Counter(components)
    return {
        "vertices": len(mesh.vertices),
        "edges": len(mesh.edges),
        "polygons": len(mesh.polygons),
        "components": len(components),
        "component_size_counts": [
            {"vertices": vertices, "edges": edges, "count": count}
            for (vertices, edges), count in component_sizes.most_common(8)
        ],
        "polygon_vertex_counts": dict(sorted(polygon_sizes.items())),
    }


def hair_key_object_space(
    emitter: bpy.types.Object,
    modifier: bpy.types.ParticleSystemModifier,
    particle: bpy.types.Particle,
    hair_key: bpy.types.ParticleHairKey,
) -> Vector:
    try:
        return Vector(hair_key.co_object(emitter, modifier, particle))
    except Exception:
        return Vector(particle.location) + Vector(hair_key.co_local)


def get_hair_points(
    emitter: bpy.types.Object,
    modifier: bpy.types.ParticleSystemModifier,
    particle: bpy.types.Particle,
    world_space: bool,
) -> list[Vector]:
    points = [
        hair_key_object_space(emitter, modifier, particle, hair_key)
        for hair_key in particle.hair_keys
    ]
    if world_space:
        return [emitter.matrix_world @ point for point in points]
    return points


@dataclass
class BuiltGroom:
    object: bpy.types.Object
    emitter: str
    particle_system: str
    group_id: int
    strands: int
    min_points: int
    max_points: int


def make_curve_from_particle_system(
    emitter: bpy.types.Object,
    modifier: bpy.types.ParticleSystemModifier,
    group_id: int,
    settings: "GroomSegmentExporterSettings",
) -> BuiltGroom | None:
    particle_system = modifier.particle_system
    particles = [
        particle
        for particle in particle_system.particles
        if particle.is_exist and particle.is_visible and len(particle.hair_keys) >= 2
    ]
    if not particles:
        return None

    curve_name = clean_name(f"Groom_{group_id:03d}_{emitter.name}_{particle_system.name}")
    old_obj = bpy.data.objects.get(curve_name)
    if old_obj and settings.replace_existing:
        bpy.data.objects.remove(old_obj, do_unlink=True)

    curve = bpy.data.curves.new(curve_name, "CURVE")
    curve.dimensions = "3D"
    curve.resolution_u = 1
    curve.bevel_depth = settings.preview_bevel_depth
    curve.bevel_resolution = 0

    point_counts: list[int] = []
    max_strands = settings.max_strands_per_system
    for particle_index, particle in enumerate(particles):
        if max_strands > 0 and particle_index >= max_strands:
            break

        points = get_hair_points(emitter, modifier, particle, settings.world_space_curves)
        if settings.minimum_points_per_strand > 2 and len(points) < settings.minimum_points_per_strand:
            continue

        spline = curve.splines.new("POLY")
        spline.points.add(len(points) - 1)
        for spline_point, position in zip(spline.points, points):
            spline_point.co = (position.x, position.y, position.z, 1.0)
        point_counts.append(len(points))

    if not point_counts:
        bpy.data.curves.remove(curve)
        return None

    obj = bpy.data.objects.new(curve_name, curve)
    collection = ensure_collection()
    collection.objects.link(obj)
    if not settings.world_space_curves:
        obj.matrix_world = emitter.matrix_world.copy()

    obj["groom_group_id"] = int(group_id)
    obj["groom_group_name"] = particle_system.name
    obj["source_emitter"] = emitter.name
    obj["source_particle_system"] = particle_system.name
    obj["strand_count"] = len(point_counts)
    obj["min_points_per_strand"] = min(point_counts)
    obj["max_points_per_strand"] = max(point_counts)
    obj["segment_policy"] = "Blender hair_keys are preserved as polyline CVs; UE strand segments are CV count - 1."
    curve["groom_group_id"] = int(group_id)
    curve["source_particle_system"] = particle_system.name

    return BuiltGroom(
        object=obj,
        emitter=emitter.name,
        particle_system=particle_system.name,
        group_id=group_id,
        strands=len(point_counts),
        min_points=min(point_counts),
        max_points=max(point_counts),
    )


def write_manifest(filepath: str, payload: dict) -> str:
    manifest_path = str(Path(filepath).with_suffix(".manifest.json"))
    with open(manifest_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return manifest_path


def hair_system_report_for_objects(objects: Iterable[bpy.types.Object]) -> list[dict]:
    allowed = set(objects)
    report = []
    for emitter, _, system in iter_hair_systems(bpy.context.scene):
        if emitter not in allowed:
            continue
        point_counts = [len(particle.hair_keys) for particle in system.particles if particle.is_exist]
        report.append(
            {
                "emitter": emitter.name,
                "particle_system": system.name,
                "parent_strands": len(point_counts),
                "child_strands": len(system.child_particles) if hasattr(system, "child_particles") else 0,
                "child_type": system.settings.child_type,
                "rendered_child_count": system.settings.rendered_child_count,
                "child_percent": system.settings.child_percent,
                "min_parent_points_per_strand": min(point_counts) if point_counts else 0,
                "max_parent_points_per_strand": max(point_counts) if point_counts else 0,
                "density_vertex_group": system.vertex_group_density,
            }
        )
    return report


def export_selected_alembic(filepath: str, selected_objects: list[bpy.types.Object], export_hair: bool) -> None:
    original_active = bpy.context.view_layer.objects.active
    original_selection = list(bpy.context.selected_objects)
    original_visibility = {
        obj.name: {
            "hide_viewport": obj.hide_viewport,
            "hide_render": obj.hide_render,
            "hide_get": obj.hide_get(),
        }
        for obj in selected_objects
        if obj.name in bpy.data.objects
    }
    try:
        bpy.ops.object.select_all(action="DESELECT")
        for obj in selected_objects:
            obj.hide_viewport = False
            obj.hide_render = False
            obj.hide_set(False)
            obj.select_set(True)
        if selected_objects:
            bpy.context.view_layer.objects.active = selected_objects[0]

        kwargs = {
            "filepath": filepath,
            "selected": True,
            "start": bpy.context.scene.frame_current,
            "end": bpy.context.scene.frame_current,
            "uvs": True,
            "normals": True,
            "vcolors": True,
            "orcos": True,
            "export_custom_properties": True,
            "export_hair": bool(export_hair),
            "export_particles": bool(export_hair),
            "global_scale": 1.0,
            "evaluation_mode": "RENDER",
        }
        bpy.ops.wm.alembic_export(**kwargs)
    finally:
        bpy.ops.object.select_all(action="DESELECT")
        for obj in selected_objects:
            if obj.name in bpy.data.objects and obj.name in original_visibility:
                state = original_visibility[obj.name]
                obj.hide_viewport = state["hide_viewport"]
                obj.hide_render = state["hide_render"]
                obj.hide_set(state["hide_get"])
        for obj in original_selection:
            if obj.name in bpy.data.objects:
                obj.select_set(True)
        if original_active and original_active.name in bpy.data.objects:
            bpy.context.view_layer.objects.active = original_active


def transform_curve_object_data(obj: bpy.types.Object, matrix: Matrix) -> dict:
    transformed_points = 0

    if obj.type == "CURVES":
        position = obj.data.attributes.get("position")
        if position:
            for item in position.data:
                item.vector = matrix @ item.vector
                transformed_points += 1
    elif obj.type == "CURVE":
        for spline in obj.data.splines:
            if hasattr(spline, "points"):
                for point in spline.points:
                    co = matrix @ Vector((point.co.x, point.co.y, point.co.z))
                    point.co = (co.x, co.y, co.z, point.co.w)
                    transformed_points += 1
            else:
                for point in spline.bezier_points:
                    point.co = matrix @ point.co
                    point.handle_left = matrix @ point.handle_left
                    point.handle_right = matrix @ point.handle_right
                    transformed_points += 1

    obj.matrix_world = Matrix.Identity(4)
    return {"object": obj.name, "transformed_points": transformed_points}


def set_curve_group_attribute(obj: bpy.types.Object, group_id: int) -> dict:
    obj["groom_group_id"] = int(group_id)
    obj["groom_group_id_AbcGeomScope"] = "con"
    obj.data["groom_group_id"] = int(group_id)
    obj.data["groom_group_id_AbcGeomScope"] = "con"

    if obj.type != "CURVES":
        return {
            "object": obj.name,
            "groom_group_id": group_id,
            "constant_custom_properties_written": True,
            "curve_attribute_written": False,
        }

    attr = obj.data.attributes.get("groom_group_id")
    if attr is None:
        attr = obj.data.attributes.new("groom_group_id", "INT", "CURVE")
    elif attr.domain != "CURVE" or attr.data_type not in {"INT", "INT32"}:
        obj.data.attributes.remove(attr)
        attr = obj.data.attributes.new("groom_group_id", "INT", "CURVE")

    for item in attr.data:
        item.value = int(group_id)

    return {
        "object": obj.name,
        "groom_group_id": group_id,
        "constant_custom_properties_written": True,
        "curve_attribute_written": True,
        "curve_count": len(attr.data),
    }


def iter_curve_point_lists(obj: bpy.types.Object):
    if obj.type == "CURVES":
        for curve in obj.data.curves:
            points = []
            for point in curve.points:
                co = point.position
                points.append((float(co.x), float(co.y), float(co.z)))
            if len(points) >= 2:
                yield points
    elif obj.type == "CURVE":
        for spline in obj.data.splines:
            points = []
            if hasattr(spline, "points"):
                for point in spline.points:
                    w = point.co.w if point.co.w else 1.0
                    points.append((float(point.co.x / w), float(point.co.y / w), float(point.co.z / w)))
            else:
                for point in spline.bezier_points:
                    points.append((float(point.co.x), float(point.co.y), float(point.co.z)))
            if len(points) >= 2:
                yield points


def write_gse_curve_data(data_path: str, curve_objects: list[bpy.types.Object], system_reports: list[dict]) -> dict:
    groups = []
    with open(data_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("GSE_CURVES_V1\n")
        for index, obj in enumerate(curve_objects):
            report = system_reports[index] if index < len(system_reports) else {}
            raw_name = f"G{index:03d}_{report.get('emitter', obj.name)}_{report.get('particle_system', obj.name)}"
            abc_name = clean_abc_object_name(raw_name)
            curve_count = 0
            point_count = 0
            handle.write(f"GROUP {index} {abc_name}\n")
            for points in iter_curve_point_lists(obj):
                curve_count += 1
                point_count += len(points)
                handle.write(f"CURVE {len(points)}\n")
                for x, y, z in points:
                    handle.write(f"{x:.9g} {y:.9g} {z:.9g}\n")
            handle.write("ENDGROUP\n")
            groups.append(
                {
                    "object": obj.name,
                    "abc_object": abc_name,
                    "groom_group_id": index,
                    "curve_count": curve_count,
                    "point_count": point_count,
                    "source_emitter": report.get("emitter", ""),
                    "source_particle_system": report.get("particle_system", ""),
                }
            )
        handle.write("END\n")
    return {"data_path": data_path, "groups": groups}


def export_curve_objects_with_groom_schema(
    filepath: str,
    curve_objects: list[bpy.types.Object],
    system_reports: list[dict],
) -> dict:
    writer = groom_abc_writer_path()
    data_path = os.path.join(tempfile.gettempdir(), "groom_segment_exporter_curves.gsedata")
    data_summary = write_gse_curve_data(data_path, curve_objects, system_reports)
    command = [str(writer), data_path, filepath]
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if result.returncode != 0:
            raise RuntimeError(f"Groom Alembic writer 失败：{result.stderr or result.stdout}")
    finally:
        try:
            os.remove(data_path)
        except OSError:
            pass
    return {
        "writer": str(writer),
        "writer_mode": "UE Groom Alembic schema writer",
        "schema_attributes": {
            "groom_group_id": "int32 Constant scope per ICurves group",
        },
        "groups": data_summary["groups"],
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


def export_particle_hair_as_curves_only(
    filepath: str,
    emitter_objects: list[bpy.types.Object],
    system_reports_override: list[dict] | None = None,
    mirror_y_before_export: bool = True,
) -> dict:
    original_active = bpy.context.view_layer.objects.active
    original_selection = list(bpy.context.selected_objects)
    original_names = set(bpy.data.objects.keys())
    imported_objects: list[bpy.types.Object] = []
    temp_path = os.path.join(tempfile.gettempdir(), "groom_segment_exporter_particle_hair_tmp.abc")

    try:
        export_selected_alembic(temp_path, emitter_objects, export_hair=True)

        before_import = set(bpy.data.objects.keys())
        bpy.ops.wm.alembic_import(filepath=temp_path, as_background_job=False)
        imported_objects = [
            bpy.data.objects[name]
            for name in bpy.data.objects.keys()
            if name not in before_import
        ]
        curve_objects = [obj for obj in imported_objects if obj.type in {"CURVES", "CURVE"}]
        if not curve_objects:
            raise RuntimeError("临时 Alembic 中没有提取到 Curves，无法生成 UE Groom curves-only 文件。")

        system_reports = system_reports_override or hair_system_report_for_objects(emitter_objects)
        axis_matrix = Matrix.Rotation(3.141592653589793, 4, "Z")
        mirror_matrix = Matrix.Diagonal((1.0, -1.0, 1.0, 1.0)) if mirror_y_before_export else Matrix.Identity(4)
        export_matrix_note = "+Z 朝上，+Y 朝前；已开启 Y 坐标镜像补偿。" if mirror_y_before_export else "+Z 朝上，+Y 朝前；未开启 Y 坐标镜像补偿。"
        transform_summary = []
        group_attribute_summary = []
        for index, obj in enumerate(curve_objects):
            system_report = system_reports[index] if index < len(system_reports) else {}
            group_label = clean_name(
                f"{system_report.get('emitter', 'Emitter')}_{system_report.get('particle_system', obj.name)}"
            )
            obj.name = f"UEGroom_G{index:03d}_{group_label}"
            obj["groom_group_id"] = index
            obj["groom_group_name"] = system_report.get("particle_system", obj.name)
            obj["source_emitter"] = system_report.get("emitter", "")
            obj["source_particle_system"] = system_report.get("particle_system", "")
            obj["source_pipeline"] = "Blender 粒子毛发临时 Alembic -> 提取 Curves -> UE curves-only Alembic"
            obj["axis_policy"] = f"{export_matrix_note} 对象矩阵已烘焙到曲线点数据。"
            transform_summary.append(transform_curve_object_data(obj, mirror_matrix @ axis_matrix @ obj.matrix_world))
            group_attribute_summary.append(set_curve_group_attribute(obj, index))

        writer_summary = export_curve_objects_with_groom_schema(filepath, curve_objects, system_reports)

        curve_summary = []
        for obj in curve_objects:
            item = {
                "name": obj.name,
                "type": obj.type,
                "groom_group_id": obj.get("groom_group_id", None),
            }
            if obj.type == "CURVES":
                item["curves"] = len(obj.data.curves)
                item["points"] = len(obj.data.points)
            elif obj.type == "CURVE":
                item["splines"] = len(obj.data.splines)
                item["points"] = sum(
                    len(spline.points) if hasattr(spline, "points") else len(spline.bezier_points)
                    for spline in obj.data.splines
                )
            curve_summary.append(item)

        return {
            "temp_alembic": temp_path,
            "axis_policy": f"{export_matrix_note} 对象矩阵已烘焙到曲线点数据，最终 Curves 对象为 identity transform。",
            "mirror_y_before_export": bool(mirror_y_before_export),
            "transform_summary": transform_summary,
            "group_attribute_summary": group_attribute_summary,
            "writer_summary": writer_summary,
            "extracted_curve_objects": curve_summary,
            "curves_object_count": len(curve_objects),
        }
    finally:
        for obj in list(imported_objects):
            if obj and obj.name in bpy.data.objects and obj.name not in original_names:
                bpy.data.objects.remove(obj, do_unlink=True)
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass
        bpy.ops.object.select_all(action="DESELECT")
        for obj in original_selection:
            if obj.name in bpy.data.objects:
                obj.select_set(True)
        if original_active and original_active.name in bpy.data.objects:
            bpy.context.view_layer.objects.active = original_active


def particle_system_report(emitter: bpy.types.Object, system: bpy.types.ParticleSystem) -> dict:
    point_counts = [len(particle.hair_keys) for particle in system.particles if particle.is_exist]
    return {
        "emitter": emitter.name,
        "particle_system": system.name,
        "parent_strands": len(point_counts),
        "child_strands": len(system.child_particles) if hasattr(system, "child_particles") else 0,
        "child_type": system.settings.child_type,
        "rendered_child_count": system.settings.rendered_child_count,
        "child_percent": system.settings.child_percent,
        "min_parent_points_per_strand": min(point_counts) if point_counts else 0,
        "max_parent_points_per_strand": max(point_counts) if point_counts else 0,
        "density_vertex_group": system.vertex_group_density,
    }


def export_particle_systems_split_by_system(
    directory: str,
    filename: str,
    allowed_emitters: set[bpy.types.Object],
    mirror_y_before_export: bool = True,
) -> dict:
    stem = Path(filename).stem
    exported = []

    for group_index, (emitter, target_modifier, target_system) in enumerate(iter_hair_systems(bpy.context.scene)):
        if emitter not in allowed_emitters:
            continue

        modifier_states = []
        for modifier in iter_particle_modifiers(emitter):
            modifier_states.append((modifier, modifier.show_viewport, modifier.show_render))

        try:
            for modifier, _, _ in modifier_states:
                enabled = modifier == target_modifier
                modifier.show_viewport = enabled
                modifier.show_render = enabled

            label = clean_name(f"G{group_index:03d}_{emitter.name}_{target_system.name}")
            filepath = os.path.join(directory, f"{stem}_{label}.abc")
            report = particle_system_report(emitter, target_system)
            roundtrip = export_particle_hair_as_curves_only(filepath, [emitter], [report], mirror_y_before_export)
            manifest_path = write_manifest(
                filepath,
                {
                    "alembic": filepath,
                    "mode": "SPLIT_FILES",
                    "mode_note": "按粒子系统拆分导出。每个 .abc 只包含一个粒子系统对应的 Curves，UE 中分别导入为独立 Groom。",
                    "hair_systems": [report],
                    "roundtrip_summary": roundtrip,
                    "ue_import_notes": [
                        "每个 .abc 单独导入 UE Groom。",
                        "需要多个系统同时显示时，在角色上挂多个 Groom Component 或分别绑定。",
                        "这是单文件 Groom schema writer 异常时的备用方案。",
                    ],
                },
            )
            exported.append(
                {
                    "alembic": filepath,
                    "manifest": manifest_path,
                    "group_index": group_index,
                    "emitter": emitter.name,
                    "particle_system": target_system.name,
                    "roundtrip_summary": roundtrip,
                }
            )
        finally:
            for modifier, show_viewport, show_render in modifier_states:
                if modifier and modifier.id_data:
                    modifier.show_viewport = show_viewport
                    modifier.show_render = show_render

    return {
        "split_file_count": len(exported),
        "files": exported,
    }


class GroomSegmentExporterSettings(PropertyGroup):
    export_directory: StringProperty(
        name="导出目录",
        subtype="DIR_PATH",
        default="//groom_exports",
    )
    export_filename: StringProperty(
        name="文件名",
        default="mh_groom_segments.abc",
    )
    selected_emitters_only: BoolProperty(
        name="只处理已选发射体",
        default=False,
    )
    replace_existing: BoolProperty(
        name="替换已生成曲线",
        default=True,
    )
    world_space_curves: BoolProperty(
        name="曲线烘焙到世界空间",
        default=False,
        description="MetaHuman 头部通常建议关闭，让曲线保留发射体变换。",
    )
    mirror_y_before_export: BoolProperty(
        name="Y 坐标镜像补偿",
        default=True,
        description="导出前把最终 Groom 曲线的 Y 坐标乘以 -1，用来抵消 UE Groom 导入后的 Y 方向镜像。",
    )
    preview_bevel_depth: FloatProperty(
        name="预览粗细",
        default=0.005,
        min=0.0,
        max=1.0,
    )
    max_strands_per_system: IntProperty(
        name="每组最多发丝",
        default=0,
        min=0,
        description="0 表示导出全部可见父发丝。",
    )
    minimum_points_per_strand: IntProperty(
        name="每根最少点数",
        default=2,
        min=2,
        max=128,
    )
    export_mode: EnumProperty(
        name="导出模式",
        items=(
            ("UE_CURVES", "UE Groom Schema 单文件分组（推荐）", "先烘焙粒子毛发为 Curves，再用专用 writer 写入 groom_group_id，生成单个可分组 Groom Alembic。"),
            ("SPLIT_FILES", "按粒子系统拆分文件（备用）", "每个粒子系统导出一个独立 curves-only Alembic。只在单文件分组异常时使用。"),
            ("PARTICLES", "原始粒子毛发（调试）", "直接导出发射体对象和粒子毛发。此文件会混入 Mesh，UE 可能不会识别为 Groom。"),
            ("CURVES", "父发丝检查曲线", "只导出插件生成的父发丝/引导线曲线，适合检查发丝 CV 分段，不适合作为最终浓密毛发。"),
        ),
        default="UE_CURVES",
    )


class GROOMSEGMENT_OT_scan(Operator):
    bl_idname = "groom_segment.scan"
    bl_label = "扫描毛发状态"
    bl_options = {"REGISTER"}

    def execute(self, context):
        settings = context.scene.groom_segment_exporter
        allowed_emitters = set(selected_or_all_emitters(context.scene, settings.selected_emitters_only))
        rows = []
        for emitter, modifier, system in iter_hair_systems(context.scene):
            if emitter not in allowed_emitters:
                continue
            point_counts = [len(particle.hair_keys) for particle in system.particles if particle.is_exist]
            rows.append(
                {
                    "emitter": emitter.name,
                    "particle_system": system.name,
                    "settings": system.settings.name,
                    "visible_parent_strands": len(point_counts),
                    "child_strands": len(system.child_particles) if hasattr(system, "child_particles") else 0,
                    "min_points_per_strand": min(point_counts) if point_counts else 0,
                    "max_points_per_strand": max(point_counts) if point_counts else 0,
                    "child_type": system.settings.child_type,
                    "rendered_child_count": system.settings.rendered_child_count,
                    "child_percent": system.settings.child_percent,
                    "density_vertex_group": system.vertex_group_density,
                    "mesh_components": mesh_component_summary(emitter),
                }
            )

        text_name = "Groom Segment Exporter Scan"
        text = bpy.data.texts.get(text_name) or bpy.data.texts.new(text_name)
        text.clear()
        text.write(json.dumps(rows, ensure_ascii=False, indent=2))
        self.report({"INFO"}, f"已扫描 {len(rows)} 个毛发粒子系统。查看文本块：{text_name}")
        return {"FINISHED"}


class GROOMSEGMENT_OT_build_curves(Operator):
    bl_idname = "groom_segment.build_curves"
    bl_label = "生成父发丝检查曲线"
    bl_description = "只把父发丝/引导线转为曲线，方便检查分段；不会烘焙 child hairs，因此不适合作为最终浓密 Groom。"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.groom_segment_exporter
        allowed_emitters = set(selected_or_all_emitters(context.scene, settings.selected_emitters_only))
        built: list[BuiltGroom] = []
        group_id = 0
        for emitter, modifier, system in iter_hair_systems(context.scene):
            if emitter not in allowed_emitters:
                continue
            groom = make_curve_from_particle_system(emitter, modifier, group_id, settings)
            if groom:
                built.append(groom)
                group_id += 1

        if not built:
            self.report({"WARNING"}, "没有找到至少包含 2 个点的可见发丝。")
            return {"CANCELLED"}

        text_name = "Groom Segment Exporter Build"
        text = bpy.data.texts.get(text_name) or bpy.data.texts.new(text_name)
        text.clear()
        text.write(
            json.dumps(
                [
                    {
                        "object": groom.object.name,
                        "source_emitter": groom.emitter,
                        "source_particle_system": groom.particle_system,
                        "groom_group_id": groom.group_id,
                        "strand_count": groom.strands,
                        "min_points_per_strand": groom.min_points,
                        "max_points_per_strand": groom.max_points,
                        "segments_range": [groom.min_points - 1, groom.max_points - 1],
                    }
                    for groom in built
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
        self.report({"INFO"}, f"已在集合 {ADDON_COLLECTION} 中生成 {len(built)} 个父发丝检查曲线分组。最终浓密导出请用“UE Groom Schema 单文件分组”。")
        return {"FINISHED"}


class GROOMSEGMENT_OT_cleanup_generated(Operator):
    bl_idname = "groom_segment.cleanup_generated"
    bl_label = "清理已生成曲线"
    bl_description = "删除本插件生成的 Groom 曲线对象和空集合，恢复到生成曲线前的场景状态。不会删除原始粒子毛发或发射体。"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        collection = bpy.data.collections.get(ADDON_COLLECTION)
        candidates: list[bpy.types.Object] = []

        if collection:
            candidates.extend(list(collection.objects))

        for obj in bpy.data.objects:
            if obj.get("source_particle_system") and obj.get("groom_group_id") is not None:
                candidates.append(obj)

        unique_candidates = list(dict.fromkeys(candidates))
        removed = 0
        for obj in unique_candidates:
            if obj.name not in bpy.data.objects:
                continue
            if obj.type == "CURVE" and obj.name.startswith("Groom_"):
                bpy.data.objects.remove(obj, do_unlink=True)
                removed += 1

        if collection and not collection.objects:
            bpy.data.collections.remove(collection)

        self.report({"INFO"}, f"已清理 {removed} 个插件生成的曲线对象。原始毛发和发射体未改动。")
        return {"FINISHED"}


class GROOMSEGMENT_OT_export_alembic(Operator):
    bl_idname = "groom_segment.export_alembic"
    bl_label = "导出 UE Groom Alembic"
    bl_options = {"REGISTER"}

    def execute(self, context):
        settings = context.scene.groom_segment_exporter
        directory = bpy.path.abspath(settings.export_directory)
        filename = settings.export_filename
        if not filename.lower().endswith(".abc"):
            filename += ".abc"
        os.makedirs(directory, exist_ok=True)
        filepath = os.path.join(directory, filename)

        if settings.export_mode == "SPLIT_FILES":
            allowed_emitters = set(selected_or_all_emitters(context.scene, settings.selected_emitters_only))
            split_summary = export_particle_systems_split_by_system(
                directory,
                filename,
                allowed_emitters,
                settings.mirror_y_before_export,
            )
            manifest_path = write_manifest(
                filepath,
                {
                    "mode": "SPLIT_FILES",
                    "mode_note": "按粒子系统拆分导出。每个 .abc 单独导入 UE Groom，作为可靠分组方案。",
                    "split_summary": split_summary,
                },
            )
            self.report({"INFO"}, f"已按粒子系统导出 {split_summary['split_file_count']} 个文件；总清单 {manifest_path}")
            return {"FINISHED"}

        roundtrip_summary = {}
        if settings.export_mode == "CURVES":
            collection = bpy.data.collections.get(ADDON_COLLECTION)
            export_objects = [obj for obj in collection.objects if obj.type == "CURVE"] if collection else []
            export_hair = False
            if not export_objects:
                self.report({"WARNING"}, "没有找到已生成的检查曲线对象。请先点击“生成父发丝检查曲线”。")
                return {"CANCELLED"}
        else:
            allowed_emitters = set(selected_or_all_emitters(context.scene, settings.selected_emitters_only))
            export_objects = [
                emitter
                for emitter, _, _ in iter_hair_systems(context.scene)
                if emitter in allowed_emitters
            ]
            export_objects = list(dict.fromkeys(export_objects))
            export_hair = True
            if not export_objects:
                self.report({"WARNING"}, "没有找到可导出的粒子毛发发射体。")
                return {"CANCELLED"}

        if settings.export_mode == "UE_CURVES":
            roundtrip_summary = export_particle_hair_as_curves_only(
                filepath,
                export_objects,
                mirror_y_before_export=settings.mirror_y_before_export,
            )
        else:
            export_selected_alembic(filepath, export_objects, export_hair=export_hair)

        manifest_path = write_manifest(
            filepath,
            {
                "alembic": filepath,
                "mode": settings.export_mode,
                "mode_note": "UE Groom Schema 单文件分组会先从 Blender 粒子毛发导出中提取 Alembic Curves，再用专用 writer 写出 ICurves + groom_group_id。原始粒子毛发调试模式可能不会被 UE 识别为 Groom。父发丝检查曲线只包含 parent/guide strands。",
                "mirror_y_before_export": bool(settings.mirror_y_before_export),
                "roundtrip_summary": roundtrip_summary,
                "objects": [
                    {
                        "name": obj.name,
                        "type": obj.type,
                        "groom_group_id": obj.get("groom_group_id", None),
                        "source_emitter": obj.get("source_emitter", obj.name),
                        "source_particle_system": obj.get("source_particle_system", None),
                        "strand_count": obj.get("strand_count", None),
                        "min_points_per_strand": obj.get("min_points_per_strand", None),
                        "max_points_per_strand": obj.get("max_points_per_strand", None),
                    }
                    for obj in export_objects
                ],
                "hair_systems": hair_system_report_for_objects(export_objects) if settings.export_mode in {"PARTICLES", "UE_CURVES"} else [],
                "ue_import_notes": [
                    "在 UE 中启用 AlembicHairImporter 插件。",
                    "把 .abc 按 Groom 资源导入。",
                    "如果 UE 导入后出现 Y 方向镜像，请保持“Y 坐标镜像补偿”开启；如果方向被反向修正过度，再关闭此选项重导。",
                    "最终毛发密度请优先使用“UE Groom Schema 单文件分组”模式；“父发丝检查曲线”只会包含 guide/parent strands。",
                    "针对 MetaHuman 头部 Skeletal Mesh 创建 Groom Binding 资源。",
                    "如果需要保留发丝分段，请先把曲线/顶点简化比例调低。",
                ],
            },
        )
        self.report({"INFO"}, f"已导出 {filepath}；清单 {manifest_path}")
        return {"FINISHED"}


class GROOMSEGMENT_PT_panel(Panel):
    bl_label = "UE Groom 分段导出器"
    bl_idname = "GROOMSEGMENT_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "UE Groom"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.groom_segment_exporter

        layout.prop(settings, "selected_emitters_only")
        layout.prop(settings, "replace_existing")
        layout.prop(settings, "world_space_curves")
        layout.prop(settings, "mirror_y_before_export")
        layout.prop(settings, "preview_bevel_depth")
        layout.prop(settings, "max_strands_per_system")
        layout.prop(settings, "minimum_points_per_strand")
        layout.operator("groom_segment.scan", icon="VIEWZOOM")
        layout.operator("groom_segment.build_curves", icon="CURVE_DATA")
        layout.operator("groom_segment.cleanup_generated", icon="TRASH")
        layout.separator()
        layout.prop(settings, "export_mode")
        if settings.export_mode == "SPLIT_FILES":
            box = layout.box()
            box.label(text="每个粒子系统导出一个独立 .abc。", icon="CHECKMARK")
            box.label(text="这是单文件分组异常时的备用方案。")
        elif settings.export_mode == "CURVES":
            box = layout.box()
            box.label(text="此模式只导出父发丝/引导线。", icon="ERROR")
            box.label(text="最终进 UE 建议改用“UE Groom Schema 单文件分组”。")
        elif settings.export_mode == "PARTICLES":
            box = layout.box()
            box.label(text="此模式会混入 Mesh，UE 可能不识别为 Groom。", icon="ERROR")
            box.label(text="最终进 UE 建议改用“UE Groom Schema 单文件分组”。")
        elif settings.export_mode == "UE_CURVES":
            box = layout.box()
            box.label(text="单文件 Groom；按粒子系统写入 groom_group_id。", icon="CHECKMARK")
            box.label(text="+Z 朝上，+Y 朝前；默认启用 Y 镜像补偿。")
        layout.prop(settings, "export_directory")
        layout.prop(settings, "export_filename")
        layout.operator("groom_segment.export_alembic", icon="EXPORT")


classes = (
    GroomSegmentExporterSettings,
    GROOMSEGMENT_OT_scan,
    GROOMSEGMENT_OT_build_curves,
    GROOMSEGMENT_OT_cleanup_generated,
    GROOMSEGMENT_OT_export_alembic,
    GROOMSEGMENT_PT_panel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.groom_segment_exporter = bpy.props.PointerProperty(type=GroomSegmentExporterSettings)


def unregister():
    if hasattr(bpy.types.Scene, "groom_segment_exporter"):
        del bpy.types.Scene.groom_segment_exporter
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
