#include <epoch_script/strategy/metadata.h>
#include <epoch_script/transforms/core/registration.h>
#include <epoch_script/core/model_artifact_store.h>
#include <chrono>
#include <fstream>
#include <iostream>
#include <iterator>
int main(int argc, char** argv) {
    if (argc != 2) return 2;
    try {
        std::ifstream file(argv[1]);
        if (!file) throw std::runtime_error("Owned fixture source is unavailable");
        const std::string source{std::istreambuf_iterator<char>(file), {}};
        if (source.size() > (256U << 10)) throw std::runtime_error("Owned fixture source exceeds probe bound");
        epoch_script::transform::InitializeTransforms();
        epoch_script::strategy::CompileOptions options;
        options.study_period = data_sdk::extent::StudyPeriod{
            .from = std::chrono::sys_days{std::chrono::year{2022}/1/1},
            .to = std::chrono::sys_days{std::chrono::year{2025}/1/31}};
        options.model_artifact_store = epoch_script::model_artifact::MakeInMemoryModelArtifactStore();
        options.model_artifact_owner_user_id = "owned-fixture-compiler-probe";
        options.model_artifact_build_git_sha = std::string(40, 'a');
        options.model_artifact_created_at = "2026-11-25T00:00:00Z";
        const epoch_script::strategy::PythonSource compiled(source, false, std::move(options));
        const auto plan = compiled.ToCompiledPlan();
        std::size_t fits{}, consumers{};
        for (const auto& node : plan.nodes) { fits += node.type == "ols_fit"; consumers += node.type == "ols_infer"; }
        if (fits != 1 || consumers != 1) throw std::runtime_error("Fixture graph is not one fit and one consumer");
        if (plan.ValidationError()) throw std::runtime_error("Fixture plan validation failed");
        std::cout << "OWNED_FIXTURE_COMPILE_OK nodes=" << plan.nodes.size() << " fits=" << fits << " consumers=" << consumers << "\n";
        return 0;
    } catch (const std::exception& error) { std::cerr << error.what() << "\n"; return 1; }
}
