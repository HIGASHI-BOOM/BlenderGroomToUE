# BlenderGroomToUE

这是一个 Blender 插件，用来把 Blender 粒子毛发整理成 Unreal Engine 可导入的 Groom Alembic，并尽量保留每根发丝的 CV 分段点。

## 安装

1. 打开 Blender。
2. 进入 `编辑 > 偏好设置 > 插件`。
3. 选择从磁盘安装，并选择：
   `E:\UE\GroomSegmentExporter\groom_segment_exporter\__init__.py`
4. 启用 `BlenderGroomToUE`。
5. 在 3D 视图右侧边栏打开 `UE Groom` 标签。

也可以运行 `install_addon.ps1`，把插件复制到 Blender 用户插件目录。

## 使用流程

1. 打开包含 MetaHuman 头部和粒子毛发的 `.blend`。
2. 点击 `扫描毛发状态`。
   - JSON 报告会写入 Blender 文本块 `Groom Segment Exporter Scan`。
   - 重点看 `min_points_per_strand` 和 `max_points_per_strand`；UE 中的发丝分段数大约等于点数减 1。
3. 如果只是想检查父发丝的分段，点击 `生成父发丝检查曲线`。
   - 每个 Blender 毛发粒子系统会变成集合 `UE_Groom_Export` 中的一个 Curve 对象。
   - 每根父发丝会变成一条 poly spline。
   - 原始 `hair_keys` 会作为曲线 CV 点保留下来。
   - 生成对象会写入 `groom_group_id`、`source_emitter`、`source_particle_system` 等自定义属性。
   - 注意：这个步骤只生成父发丝/引导线，不会把 child hairs 烘焙成曲线，所以不适合作为最终浓密毛发导入 UE。
4. 如果想撤销插件生成内容，点击 `清理已生成曲线`。
   - 这个按钮只删除插件生成的 `Groom_...` 曲线对象和空的 `UE_Groom_Export` 集合。
   - 原始头部、头皮源网格、粒子系统和已有毛发不会被删除。
5. 设置 `导出目录` 和 `文件名`。
   - `Y 坐标镜像补偿` 默认开启，用来抵消 UE Groom 导入后出现的 Y 方向镜像。
   - 如果某个模型导入 UE 后反而被二次镜像，再关闭这个选项重新导出。
   - `Groom 宽度(cm)` 会写入 `groom_width`，默认 `0.01cm`。
   - `Root UV 贴图` 留空时使用发射体活动 UV，并写入每根曲线的 `groom_root_uv`。
6. 最终导出请优先选择 `UE Groom Schema 单文件分组（推荐）`，然后点击 `导出 UE Groom Alembic`。
   - 插件会先临时导出 Blender 粒子毛发。
   - 再把临时 Alembic 导回 Blender，提取其中真正的 `CURVES` 对象。
   - 最后调用插件自带的 `bin\groom_abc_writer.exe` 写出真正的 UE Groom Alembic schema。
   - 每个粒子系统会写成一个 `ICurves` 对象，并写入 `groom_group_id`、`groom_width`、`groom_root_uv`。
   - `groom_group_id` 和 `groom_width` 使用 Constant scope；`groom_root_uv` 使用 Uniform scope。
   - 导出前会把对象矩阵和 Y 镜像补偿烘焙进曲线点数据，最终文件为 `+Z 朝上、+Y 朝前`，Curves 对象矩阵为 identity。
7. 如果单文件 schema writer 出现异常，可临时选择 `按粒子系统拆分文件（备用）`。
   - 每个粒子系统会导出一个独立 curves-only `.abc`。
   - UE 中分别导入这些 `.abc`，每个 Groom 对应一个粒子系统。
   - 这是备用方案，不是首选方案。
8. `原始粒子毛发（调试）` 会混入 Mesh，UE 可能不会识别为 Groom，只建议排查时使用。
9. 在 Unreal Engine 中把 `.abc` 按 Groom 导入，然后给 MetaHuman 头部 Skeletal Mesh 创建 Groom Binding。

## 当前场景备注

已经检查过的当前 Blender 场景包含：

- `MH_Head`：两个 HAIR 粒子系统，目前一组是 11 根父发丝和 550 根 child hairs，另一组是 3 根父发丝和 600 根 child hairs。
- `MH_Head_Scalp_GroomSource`：当前对象还存在，但当前会话里没有粒子系统 modifier，因此不会产生 Groom 分组。
- `MH_Head_Scalp_GroomSource` 本身是一个 mesh，有 3918 个互不相连的三角面岛。这些三角面岛不会自动成为 UE Groom 分组。

如果需要在 UE 中保留区域分组，建议在 Blender 里整理成少量明确的毛发粒子系统或曲线组，例如刘海、鬓角、头顶、后发。不要把几千个三角面岛一一导成几千个 Groom Group，UE 里会很难管理。

## UE 导入检查

- 当前 UE 项目已经启用了 `AlembicHairImporter`。
- 导入 `.abc` 时选择 Groom。需要分组时，请优先使用 `UE Groom Schema 单文件分组（推荐）` 导出的单个文件。
- 如果 UE 导入窗口的 `分组` 里仍只显示一个 `Group_0`，说明导入的不是 schema writer 生成的新文件。
- 如果 UE 中方向又变成反向镜像，回到 Blender 插件里关闭 `Y 坐标镜像补偿` 后重导。
- 如果创建 Groom Binding 时提示缺失 `groom_width` 或 `root_uv`，请重新导出当前版本；旧文件没有这两个属性。
- 检查分段保留效果时，先把导入简化比例调低。
- 导入后先打开 Groom 资源确认曲线数量和点数量，再创建 Groom Binding。
