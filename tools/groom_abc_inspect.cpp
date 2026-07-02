#include <Alembic/Abc/All.h>
#include <Alembic/AbcCoreFactory/All.h>
#include <Alembic/AbcGeom/All.h>

#include <iostream>
#include <string>

namespace Abc = Alembic::Abc;
namespace AbcGeom = Alembic::AbcGeom;
namespace Factory = Alembic::AbcCoreFactory;

static const char* ScopeName(AbcGeom::GeometryScope scope)
{
    switch (scope) {
    case AbcGeom::kConstantScope: return "Constant";
    case AbcGeom::kUniformScope: return "Uniform";
    case AbcGeom::kVertexScope: return "Vertex";
    case AbcGeom::kVaryingScope: return "Varying";
    case AbcGeom::kFacevaryingScope: return "Facevarying";
    default: return "Unknown";
    }
}

static void PrintObject(const Abc::IObject& obj, int depth)
{
    std::string indent(depth * 2, ' ');
    std::cout << indent << obj.getFullName() << " schema=" << obj.getMetaData().get("schema") << "\n";

    if (AbcGeom::ICurves::matches(obj.getMetaData())) {
        AbcGeom::ICurves curves(obj, Abc::kWrapExisting);
        AbcGeom::ICurvesSchema schema = curves.getSchema();
        AbcGeom::ICurvesSchema::Sample sample;
        schema.get(sample);
        std::cout << indent << "  curves=" << sample.getNumCurves()
                  << " points=" << sample.getPositions()->size() << "\n";

        Abc::ICompoundProperty arb = schema.getArbGeomParams();
        std::cout << indent << "  arbGeomParams children=" << arb.getNumProperties() << "\n";
        for (size_t i = 0; i < arb.getNumProperties(); ++i) {
            const Abc::PropertyHeader& header = arb.getPropertyHeader(i);
            std::cout << indent << "    " << header.getName()
                      << " meta=" << header.getMetaData().serialize() << "\n";
            if (header.getName() == "groom_group_id") {
                AbcGeom::IInt32GeomParam param(arb, "groom_group_id");
                std::cout << indent << "      scope=" << ScopeName(param.getScope())
                          << " indexed=" << (param.isIndexed() ? "true" : "false") << "\n";
                auto value = param.getExpandedValue();
                auto vals = value.getVals();
                if (vals && vals->size() > 0) {
                    std::cout << indent << "      value=" << (*vals)[0] << "\n";
                }
            }
        }
    }

    for (size_t i = 0; i < obj.getNumChildren(); ++i) {
        PrintObject(obj.getChild(i), depth + 1);
    }
}

int main(int argc, char** argv)
{
    if (argc < 2) {
        std::cerr << "usage: groom_abc_inspect.exe file.abc\n";
        return 2;
    }

    try {
        Factory::IFactory factory;
        factory.setPolicy(Abc::ErrorHandler::kThrowPolicy);
        Factory::IFactory::CoreType core_type;
        Abc::IArchive archive = factory.getArchive(argv[1], core_type);
        PrintObject(archive.getTop(), 0);
    }
    catch (const std::exception& e) {
        std::cerr << e.what() << "\n";
        return 1;
    }

    return 0;
}
