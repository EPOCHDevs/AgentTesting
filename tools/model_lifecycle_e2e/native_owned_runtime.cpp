// Engineering integration harness. Uses the real native compiler/runtime and
// only an explicitly owned source-free fixture. Public API/SDK/UI checks remain
// separate. The caller must run this process inside a bounded managed scope.
#include <epoch_script/core/model_artifact_store.h>
#include <epoch_script/core/serialization/compiled_plan_glaze.h>
#include <epoch_script/strategy/metadata.h>
#include <epoch_script/strategy/response_output.h>
#include <epoch_script/transforms/core/registration.h>
#include <epoch_script/transforms/runtime/iorchestrator.h>
#include <epoch_script/transforms/runtime/transform_manager/itransform_manager.h>
#include "transforms/runtime/execution/source_provider.h"
#include "transforms/runtime/orchestrator.h"
#include "transforms/runtime/sql/relational_program_executor.h"
#include <arrow/api.h>
#include <jsoncons/json.hpp>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <iterator>
#include <mutex>

namespace {
namespace script = epoch_script::strategy;
namespace runtime = epoch_script::runtime;
namespace models = epoch_script::model_artifact;
namespace fs = std::filesystem;
using OwnedJson = jsoncons::json;
constexpr auto owner = "owned-model-lifecycle";
constexpr auto producer = "owned-fit-run";

// This fixture has no provider inputs. Fail closed if its source ever changes
// to request vendor, uploaded, retained or other external observations.
class NoExternalSources final : public runtime::IDataSourceProvider {
public:
    void SetHandleRetainer(runtime::SourceHandleRetainer) override {}
    std::vector<runtime::EnvironmentSourceTask> SealPlan(
        std::vector<runtime::SourceRequestDescriptor> requests,
        const std::vector<std::string>& assets) override {
        if (!requests.empty() || !assets.empty())
            throw std::runtime_error("Owned runtime fixture requested external inputs");
        return {};
    }
    std::vector<epoch_script::data::SourceInputCoordinate> PlannedInputs() const override { return {}; }
    runtime::SourceLoadResult Load(const runtime::DataSliceKey&,
        data_sdk::events::CancellationTokenPtr) override {
        throw std::runtime_error("Owned runtime fixture attempted an external load");
    }
    void PrepareAsync(std::vector<runtime::DataSliceKey> requests,
        runtime::SourcePreparationCallback completion,
        data_sdk::events::CancellationTokenPtr) override {
        if (!requests.empty()) throw std::runtime_error("Owned runtime fixture attempted external preparation");
        completion(nullptr);
    }
    runtime::LoadedSourceFrames SnapshotLoadedSources() const override { return {}; }
    void VisitLoadedSourceHandles(const runtime::LoadedSourceHandleVisitor&) const override {}
};

std::string Read(const fs::path& path) {
    if (fs::file_size(path) > (256U << 10)) throw std::runtime_error("Source exceeds fixture bound");
    std::ifstream stream(path, std::ios::binary);
    if (!stream) throw std::runtime_error("Fixture source is unavailable");
    return {std::istreambuf_iterator<char>(stream), {}};
}
void Write(const fs::path& path, std::string_view bytes) {
    std::ofstream stream(path, std::ios::binary);
    stream.write(bytes.data(), static_cast<std::streamsize>(bytes.size()));
    if (!stream) throw std::runtime_error("Cannot retain owned fixture evidence");
}
OwnedJson Table(const arrow::Table& table) {
    if (table.num_rows() > 1000) throw std::runtime_error("Runtime answer exceeds fixture row bound");
    OwnedJson result;
    result["columns"] = table.ColumnNames();
    result["rows"] = OwnedJson::array();
    for (std::int64_t row = 0; row < table.num_rows(); ++row) {
        OwnedJson cells = OwnedJson::array();
        for (const auto& column : table.columns()) {
            const auto value = column->GetScalar(row);
            if (!value.ok()) throw std::runtime_error(value.status().ToString());
            const auto scalar = *value;
            if (!scalar->is_valid) cells.push_back(OwnedJson::null());
            else if (scalar->type->id() == arrow::Type::DOUBLE)
                cells.push_back(static_cast<const arrow::DoubleScalar&>(*scalar).value);
            else if (scalar->type->id() == arrow::Type::INT64)
                cells.push_back(static_cast<const arrow::Int64Scalar&>(*scalar).value);
            else if (scalar->type->id() == arrow::Type::TIMESTAMP)
                cells.push_back(static_cast<const arrow::TimestampScalar&>(*scalar).value);
            else throw std::runtime_error("Unexpected owned fixture answer type");
        }
        result["rows"].push_back(std::move(cells));
    }
    return result;
}
OwnedJson Run(const fs::path& root, std::string stage, const fs::path& source_path,
         const std::string& build, const models::IModelArtifactStorePtr& store,
         std::size_t expected_fits) {
    const auto source = Read(source_path);
    script::CompileOptions options;
    options.study_period = data_sdk::extent::StudyPeriod{
        .from = std::chrono::sys_days{std::chrono::year{2022}/1/1},
        .to = std::chrono::sys_days{std::chrono::year{2025}/1/31}};
    options.model_artifact_store = store;
    options.model_artifact_owner_user_id = owner;
    options.model_artifact_build_git_sha = build;
    options.model_artifact_created_at = "2026-11-25T00:00:00Z";
    options.relational_execution_envelope = epoch_script::sql::DefaultInProcessRelationalExecutionEnvelope();
    const script::PythonSource compiled(source, false, std::move(options));
    const auto plan = compiled.ToCompiledPlan();
    std::size_t fits{};
    for (const auto& node : plan.nodes) fits += node.type == "ols_fit";
    if (fits != expected_fits || plan.ValidationError())
        throw std::runtime_error("Unexpected owned fixture fit count or invalid plan");
    Write(root/(stage+".source.epoch"), source);
    Write(root/(stage+".plan.json"), glz::write_json(plan).value());
    OwnedJson tables;
    std::mutex table_mutex;
    auto outputs = std::make_shared<runtime::RunOutputRuntime>();
    for (const auto& output : plan.response_outputs) {
        if (output.kind != script::ResponseOutputKind::Table)
            throw std::runtime_error("Owned fixture declares a non-table answer");
        std::vector<runtime::RunOutputField> fields;
        for (const auto& field : script::ResolveResponseRelation(plan.nodes, output.binding).ordered_fields)
            fields.push_back({output.binding.node_id, output.binding.query_id+"."+field.sql_name, field.sql_name});
        outputs->nodes.push_back({.id="owned-answer:"+output.name, .type="response_table",
            .inputs=fields, .execute=[&,fields,name=output.name](const auto& reader) {
                const auto table = reader(fields);
                if (!table) throw std::runtime_error("Complete owned answer is missing");
                const auto retained = Table(*table);
                std::lock_guard lock(table_mutex);
                tables[name] = retained;
            }});
    }
    const auto execution = stage == "fit" ? std::string{producer} : "owned-"+stage+"-run";
    auto orchestrator = runtime::CreateDataFlowRuntimeOrchestrator(
        {}, runtime::CreateTransformManager(script::PythonSource::Compiled(source, plan)),
        epoch_core::RunScope{.userId=owner, .entityId=execution}, root/(stage+"-scratch"),
        std::make_shared<NoExternalSources>(), std::nullopt,
        runtime::RuntimeMemoryBudget{.capacity_bytes=512U<<20,.use_process_rss=false}, store,
        runtime::RelationalRuntimeOption{
            .memory_governance=epoch_core::RelationalMemoryGovernance::InProcessMeasured,
            .route=epoch_script::sql::RelationalProgramExecutionRoute::SealedArrowProduction,
            .spill_volume_encrypted=false});
    orchestrator->SetRunOutputRuntime(outputs);
    auto* observable = dynamic_cast<runtime::DataFlowRuntimeOrchestrator*>(orchestrator.get());
    if (!observable) throw std::runtime_error("Native fixture runtime exposes no complete event stream");
    OwnedJson fit_events = OwnedJson::array();
    std::mutex event_mutex;
    const auto connection = observable->OnEvent([&](const auto& event) {
        const auto* progress = std::get_if<runtime::events::TransformProgressEvent>(&event);
        if (!progress) return;
        const auto metadata = OwnedJson::parse(glz::write_json(progress->metadata).value());
        if (!metadata.contains("model_operation") || metadata.at("model_operation").template as<std::string>() != "fit") return;
        OwnedJson retained;
        retained["node_id"] = progress->node_id;
        retained["metadata"] = metadata;
        std::lock_guard lock(event_mutex);
        fit_events.push_back(std::move(retained));
    }, runtime::events::EventFilter::TransformProgressOnly());
    if (!connection.connected()) throw std::runtime_error("Native fixture event capture is disconnected");
    data_sdk::events::ScopedProgressEmitter emitter;
    orchestrator->ExecutePipeline(emitter);
    std::size_t started{}, completed{};
    for (const auto& event : fit_events.array_range()) {
        const auto phase = event.at("metadata").at("model_fit_phase").as<std::string>();
        if (phase == "started") ++started;
        else if (phase == "completed") ++completed;
        else throw std::runtime_error("Unknown model fit event phase");
    }
    if (started != expected_fits || completed != expected_fits)
        throw std::runtime_error("Native model fit telemetry differs from the declared fixture effects");
    if (!tables.contains("predictions") || !tables.contains("scores"))
        throw std::runtime_error("Owned runtime did not deliver both complete answers");
    OwnedJson receipt;
    receipt["stage"] = stage;
    receipt["execution"] = execution;
    receipt["compiled_fit_nodes"] = fits;
    receipt["fit_events"] = fit_events;
    receipt["measured_fit_started"] = started;
    receipt["measured_fit_completed"] = completed;
    receipt["tables"] = tables;
    Write(root/(stage+".results.json"), receipt.to_string());
    return receipt;
}
}

int main(int argc, char** argv) {
    if (argc != 6) {
        std::cerr << "usage: native_owned_runtime NEW_EVIDENCE_ROOT FIT_SOURCE CONTINUE_SOURCE SAVED_SOURCE BUILD_SHA\n";
        return 2;
    }
    try {
        const fs::path root{argv[1]};
        if (!root.is_absolute() || !fs::create_directory(root))
            throw std::runtime_error("Evidence root must be a new absolute owned directory");
        fs::permissions(root, fs::perms::owner_all, fs::perm_options::replace);
        epoch_script::transform::InitializeTransforms();
        auto store = models::MakeFileModelArtifactStore(root/"personal");
        Run(root,"fit",argv[2],argv[5],store,1);
        const auto selected = store->ResolveExecutionFitAuthorized(producer,"owned_fixture",owner);
        if (!store->ListAuthorized(owner).empty()) throw std::runtime_error("Fit polluted saved catalogue");
        Write(root/"fit.artifact.bin",selected.bytes);
        Run(root,"continue",argv[3],argv[5],store,0);
        if (!store->ListAuthorized(owner).empty()) throw std::runtime_error("Continuation polluted saved catalogue");
        const auto saved = store->SaveExecutionFit(producer,"owned_fixture","owned_saved",owner);
        if (saved.digest != selected.binding.digest) throw std::runtime_error("Save changed the fit digest");
        // Reopen the independently persisted store before fresh consumption.
        store = models::MakeFileModelArtifactStore(root/"personal");
        Run(root,"saved",argv[4],argv[5],store,0);
        if (store->ListAuthorized(owner).size()!=1 || store->ResolveAuthorized("owned_saved",owner).bytes!=selected.bytes)
            throw std::runtime_error("Saved consumption changed fitted bytes or catalogue");
        OwnedJson receipt;
        receipt["owner"]=owner; receipt["producer_execution"]=producer;
        receipt["digest"]=saved.digest; receipt["saved_ref"]=saved.name;
        receipt["scope"]="native graph/file store with synchronous fit-event capture; HTTP/SDK/UI unqualified";
        Write(root/"native-receipt.json",receipt.to_string());
        std::cout << "OWNED_NATIVE_GRAPH_OK digest=" << saved.digest << " evidence=" << root << "\n";
        return 0;
    } catch (const std::exception& error) { std::cerr << error.what() << "\n"; return 1; }
}
