#include <Alembic/Abc/All.h>
#include <Alembic/AbcCoreOgawa/All.h>
#include <Alembic/AbcGeom/All.h>

#include <cstdint>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

namespace Abc = Alembic::Abc;
namespace AbcGeom = Alembic::AbcGeom;
namespace Ogawa = Alembic::AbcCoreOgawa;

struct GroupData {
    int32_t group_id = 0;
    std::string name;
    float width = 0.01f;
    std::vector<Abc::V3f> positions;
    std::vector<int32_t> counts;
    std::vector<Abc::V2f> root_uvs;
};

static void WriteGroup(Abc::OObject parent, const GroupData& group)
{
    if (group.counts.empty() || group.positions.empty()) {
        return;
    }

    AbcGeom::OCurves curves(parent, group.name);
    AbcGeom::OCurvesSchema& schema = curves.getSchema();

    AbcGeom::OCurvesSchema::Sample sample(
        AbcGeom::V3fArraySample(group.positions),
        Abc::Int32ArraySample(group.counts),
        AbcGeom::kLinear);
    sample.setWrap(AbcGeom::kNonPeriodic);
    schema.set(sample);

    Abc::OCompoundProperty arb = schema.getArbGeomParams();

    AbcGeom::OInt32GeomParam group_param(
        arb,
        "groom_group_id",
        false,
        AbcGeom::kConstantScope,
        1);
    AbcGeom::OInt32GeomParam::Sample group_sample(
        Abc::Int32ArraySample(&group.group_id, 1),
        AbcGeom::kConstantScope);
    group_param.set(group_sample);

    AbcGeom::OFloatGeomParam width_param(
        arb,
        "groom_width",
        false,
        AbcGeom::kConstantScope,
        1);
    AbcGeom::OFloatGeomParam::Sample width_sample(
        Abc::FloatArraySample(&group.width, 1),
        AbcGeom::kConstantScope);
    width_param.set(width_sample);

    std::vector<Abc::V2f> root_uvs = group.root_uvs;
    if (root_uvs.size() != group.counts.size()) {
        root_uvs.assign(group.counts.size(), Abc::V2f(0.0f, 0.0f));
    }
    AbcGeom::OV2fGeomParam root_uv_param(
        arb,
        "groom_root_uv",
        false,
        AbcGeom::kUniformScope,
        1);
    AbcGeom::OV2fGeomParam::Sample root_uv_sample(
        Abc::V2fArraySample(root_uvs),
        AbcGeom::kUniformScope);
    root_uv_param.set(root_uv_sample);

    // Leave groom_guide absent so Unreal can generate guide strands from the
    // import settings instead of seeing an explicit all-zero guide attribute.
}

static void FlushGroup(Abc::OObject top, GroupData& group, bool& has_group, int& written_groups)
{
    if (!has_group) {
        return;
    }
    WriteGroup(top, group);
    ++written_groups;
    group = GroupData();
    has_group = false;
}

int main(int argc, char** argv)
{
    if (argc < 3) {
        std::cerr << "usage: groom_abc_writer.exe input.gsedata output.abc\n";
        return 2;
    }

    try {
        std::ifstream input(argv[1]);
        if (!input) {
            throw std::runtime_error("Cannot open input data file.");
        }

        Abc::OArchive archive(
            Ogawa::WriteArchive(),
            argv[2],
            Abc::ErrorHandler::kThrowPolicy);
        Abc::OObject top = archive.getTop();

        int16_t major = 1;
        int16_t minor = 5;
        Abc::OInt16Property(top.getProperties(), "groom_version_major").set(major);
        Abc::OInt16Property(top.getProperties(), "groom_version_minor").set(minor);
        Abc::OStringProperty(top.getProperties(), "groom_tool").set("GroomSegmentExporter");

        std::string token;
        GroupData group;
        bool has_group = false;
        int written_groups = 0;

        input >> token;
        bool has_root_uv = false;
        if (token == "GSE_CURVES_V2") {
            has_root_uv = true;
        }
        else if (token != "GSE_CURVES_V1") {
            throw std::runtime_error("Input data is not GSE_CURVES_V1 or GSE_CURVES_V2.");
        }

        while (input >> token) {
            if (token == "GROUP") {
                FlushGroup(top, group, has_group, written_groups);
                input >> group.group_id >> group.name;
                if (has_root_uv) {
                    input >> group.width;
                }
                if (!input) {
                    throw std::runtime_error("Invalid GROUP line.");
                }
                has_group = true;
            }
            else if (token == "CURVE") {
                if (!has_group) {
                    throw std::runtime_error("CURVE appears before GROUP.");
                }
                int32_t count = 0;
                float root_u = 0.0f;
                float root_v = 0.0f;
                input >> count;
                if (has_root_uv) {
                    input >> root_u >> root_v;
                }
                if (!input || count < 2) {
                    throw std::runtime_error("Invalid CURVE point count.");
                }
                group.counts.push_back(count);
                group.root_uvs.emplace_back(root_u, root_v);
                for (int32_t i = 0; i < count; ++i) {
                    float x = 0.0f;
                    float y = 0.0f;
                    float z = 0.0f;
                    input >> x >> y >> z;
                    if (!input) {
                        throw std::runtime_error("Invalid point data.");
                    }
                    group.positions.emplace_back(x, y, z);
                }
            }
            else if (token == "ENDGROUP") {
                FlushGroup(top, group, has_group, written_groups);
            }
            else if (token == "END") {
                break;
            }
            else {
                throw std::runtime_error("Unexpected token: " + token);
            }
        }

        FlushGroup(top, group, has_group, written_groups);
        if (written_groups == 0) {
            throw std::runtime_error("No curve groups were written.");
        }
    }
    catch (const std::exception& e) {
        std::cerr << e.what() << "\n";
        return 1;
    }

    return 0;
}
