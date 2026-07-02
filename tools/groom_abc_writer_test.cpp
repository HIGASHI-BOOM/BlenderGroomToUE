#include <Alembic/Abc/All.h>
#include <Alembic/AbcCoreOgawa/All.h>
#include <Alembic/AbcGeom/All.h>

#include <cstdint>
#include <iostream>
#include <string>
#include <vector>

namespace Abc = Alembic::Abc;
namespace AbcGeom = Alembic::AbcGeom;
namespace Ogawa = Alembic::AbcCoreOgawa;

static void WriteGroup(
    Abc::OObject parent,
    const std::string& name,
    int32_t group_id,
    const std::vector<Abc::V3f>& positions,
    const std::vector<int32_t>& counts)
{
    AbcGeom::OCurves curves(parent, name);
    AbcGeom::OCurvesSchema& schema = curves.getSchema();

    AbcGeom::OCurvesSchema::Sample sample(
        AbcGeom::V3fArraySample(positions),
        Abc::Int32ArraySample(counts),
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
        Abc::Int32ArraySample(&group_id, 1),
        AbcGeom::kConstantScope);
    group_param.set(group_sample);

    int32_t guide = 0;
    AbcGeom::OInt32GeomParam guide_param(
        arb,
        "groom_guide",
        false,
        AbcGeom::kConstantScope,
        1);
    AbcGeom::OInt32GeomParam::Sample guide_sample(
        Abc::Int32ArraySample(&guide, 1),
        AbcGeom::kConstantScope);
    guide_param.set(guide_sample);
}

int main(int argc, char** argv)
{
    if (argc < 2) {
        std::cerr << "usage: groom_abc_writer_test.exe output.abc\n";
        return 2;
    }

    try {
        Abc::OArchive archive(
            Ogawa::WriteArchive(),
            argv[1],
            Abc::ErrorHandler::kThrowPolicy);
        Abc::OObject top = archive.getTop();

        int16_t major = 1;
        int16_t minor = 5;
        Abc::OInt16Property(top.getProperties(), "groom_version_major").set(major);
        Abc::OInt16Property(top.getProperties(), "groom_version_minor").set(minor);
        Abc::OStringProperty(top.getProperties(), "groom_tool").set("GroomSegmentExporter test writer");

        WriteGroup(
            top,
            "ParticleSystem_0",
            0,
            {Abc::V3f(0.0f, 0.0f, 0.0f), Abc::V3f(0.0f, 0.0f, 5.0f), Abc::V3f(0.0f, 0.0f, 10.0f)},
            {3});

        WriteGroup(
            top,
            "ParticleSystem_1",
            1,
            {Abc::V3f(2.0f, 0.0f, 0.0f), Abc::V3f(2.0f, 0.0f, 5.0f), Abc::V3f(2.0f, 0.0f, 10.0f)},
            {3});
    }
    catch (const std::exception& e) {
        std::cerr << e.what() << "\n";
        return 1;
    }

    return 0;
}
