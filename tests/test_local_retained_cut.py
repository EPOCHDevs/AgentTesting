"""Retained rollback admits immutable artifacts without weakening normal checkout guards."""

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from tools import local_retained_cut as cuts, local_stack, local_ui_refresh as refresh
from tools.agent_testing import Stack


@pytest.fixture
def reviewed_cut(tmp_path, monkeypatch):
    state = tmp_path / "state"
    bundle = state / "ui-builds/old-source-original-key"
    (bundle / "assets").mkdir(parents=True)
    (bundle / "index.html").write_text('<script type="module" src="/assets/main.js"></script>')
    (bundle / "assets/main.js").write_text("// original source\n")
    paths = {"ai_dir": tmp_path / "EpochAI", "backend_binary": tmp_path / "EpochBackend/build/bin/server"}
    paths["ai_dir"].mkdir()
    paths["backend_binary"].parent.mkdir(parents=True)
    paths["backend_binary"].write_bytes(b"native")
    paths["backend_binary"].with_name("epoch_reference_files").write_bytes(b"exporter")
    for name in ("langgraph.json", "requirements.txt"):
        (paths["ai_dir"] / name).write_text(name + "\n")
    stack = Stack.load(Path("environments.toml"), "local-up")
    workspace = {name: str(i) * 40 for i, name in enumerate(sorted(cuts.PROJECTS), 1)}
    runtime = {**workspace, "EpochAI": "a" * 40, "EpochClientPortal": "b" * 40}
    aliases = {"EpochAI": "ai", "EpochBackend": "backend", "EpochClientPortal": "portal", "EpochPlatform": "platform"}
    image = "sha256:" + "f" * 64
    receipt = tmp_path / "original-receipt.json"
    receipt.write_text(json.dumps({"source_pins": {alias: runtime[name] for name, alias in aliases.items()},
                                   "image": image, "ui_bundle": str(bundle),
                                   "ui_index_sha256": cuts.file_hash(bundle / "index.html"),
                                   "ui_asset": "/assets/main.js", "ui_asset_sha256": cuts.file_hash(bundle / "assets/main.js"),
                                   "health": {"services": stack.identity(),
                                              "ai": {"deployment": {"git_sha": runtime["EpochAI"]}, "reference_version": "e" * 16},
                                              "backend": {"git_sha": runtime["EpochBackend"]}}}))
    manifest = tmp_path / "manifest.json"
    admission = tmp_path / "admission.json"
    value = {"schema_version": 1, "mode": "retained-runtime-rollback", "stack": stack.identity(),
             "workspace_sources": workspace, "runtime_sources": runtime, "ai_image_id": image,
             "ui_bundle": str(bundle), "ui_files": cuts.bundle_hashes(bundle),
             "ui_configuration": "c" * 64, "preview_inputs": "d" * 64,
             "configuration_inputs": cuts.input_hashes(tmp_path),
             "native_binary_sha256": cuts.file_hash(paths["backend_binary"]),
             "reference_exporter_sha256": cuts.file_hash(paths["backend_binary"].with_name("epoch_reference_files")),
             "native_reference_version": "e" * 16,
             "source_receipt": {"path": str(receipt), "sha256": cuts.file_hash(receipt)}}

    def load(data=None, review=None):
        manifest.write_text(json.dumps(value if data is None else data))
        reviewed = {"schema_version": 1, "purpose": "retained-runtime-rollback",
                    "manifest_sha256": cuts.file_hash(manifest), "verdict": "pass", "reviewer_thread_id": "reviewer"}
        admission.write_text(json.dumps(reviewed if review is None else review))
        return cuts.RetainedCut.load(manifest, admission, cuts.file_hash(admission))

    calls = []

    def git(command):
        calls.append(command)
        return (paths["ai_dir"] / command[-1].split(":", 1)[1]).read_bytes()

    def docker(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=image + "\n")

    monkeypatch.setattr(cuts.subprocess, "check_output", git)
    monkeypatch.setattr(cuts.subprocess, "run", docker)
    cut = load()
    args = {"root": tmp_path, "state": state, "paths": paths,
            "workspace": {name: {"commit": sha} for name, sha in workspace.items()},
            "identity": stack.identity(), "environment": "local-up",
            "ui_configuration": value["ui_configuration"], "preview_inputs": value["preview_inputs"]}
    return SimpleNamespace(cut=cut, load=load, value=value, args=args, calls=calls, stack=stack,
                           manifest=manifest, admission=admission, receipt=receipt)


def test_original_artifacts_are_accepted_without_build_pull_or_relabel(reviewed_cut):
    f = reviewed_cut
    f.cut.validate(**f.args)
    assert [command[0] for command in f.calls] == ["git", "git", "docker"]
    assert f.calls[-1] == ["docker", "image", "inspect", f.cut.ai_image_id, "--format", "{{.Id}}"]
    assert f.cut.metadata()["workspace_sources"] != f.cut.metadata()["runtime_sources"]
    assert f.cut.metadata()["ui_bundle"] == str(f.cut.ui_bundle)


@pytest.mark.parametrize("field,bad", [
    ("schema_version", True), ("mode", "fallback"), ("extra", 1),
    ("runtime_sources", {}), ("ai_image_id", "epoch-ai-up:old-tag"), ("ui_files", {}),
    ("preview_inputs", "short"), ("configuration_inputs", {}), ("ui_bundle", "relative"),
    ("source_receipt", {"path": "/receipt", "sha256": "short"}),
])
def test_malformed_or_unreviewed_cut_has_no_process_effects(reviewed_cut, field, bad):
    f = reviewed_cut
    value = deepcopy(f.value)
    value[field] = bad
    with pytest.raises(ValueError):
        f.load(value)
    assert not f.calls


@pytest.mark.parametrize("review", [[], None, {"schema_version": True}, {"verdict": "fail"}])
def test_incomplete_admission_is_not_accepted(reviewed_cut, review):
    f = reviewed_cut
    f.admission.write_text(json.dumps(review))
    with pytest.raises(ValueError):
        cuts.RetainedCut.load(f.manifest, f.admission, cuts.file_hash(f.admission))
    assert not f.calls


@pytest.mark.parametrize("change", ["workspace", "environment", "configuration", "preview", "bundle", "binary", "exporter", "receipt", "manifest", "admission", "env"])
def test_drift_refused_before_docker_or_other_service_effects(reviewed_cut, change):
    f = reviewed_cut
    args = deepcopy(f.args)
    if change == "workspace":
        args["workspace"]["EpochAI"]["commit"] = "0" * 40
    elif change == "environment":
        args["environment"] = "dev"
    elif change == "configuration":
        args["ui_configuration"] = "0" * 64
    elif change == "preview":
        args["preview_inputs"] = "0" * 64
    else:
        path = {"bundle": f.cut.ui_bundle / "assets/main.js", "binary": f.args["paths"]["backend_binary"],
                "exporter": f.args["paths"]["backend_binary"].with_name("epoch_reference_files"),
                "receipt": f.receipt, "manifest": f.manifest, "admission": f.admission,
                "env": f.args["root"] / "EpochAI/.env.local"}[change]
        path.write_text("changed")
    with pytest.raises(ValueError):
        f.cut.validate(**args)
    assert not f.calls


def test_symlink_bundle_cannot_redirect_retained_artifact(reviewed_cut):
    f = reviewed_cut
    asset = f.cut.ui_bundle / "assets/main.js"
    target = f.args["root"] / "elsewhere.js"
    target.write_bytes(asset.read_bytes())
    asset.unlink()
    asset.symlink_to(target)
    with pytest.raises(ValueError, match="symlinks"):
        f.cut.validate(**f.args)
    assert not f.calls


def test_existing_retained_image_is_required_and_never_built(reviewed_cut, monkeypatch):
    f = reviewed_cut
    monkeypatch.setattr(cuts.subprocess, "run", lambda command, **_: subprocess.CompletedProcess(command, 1, stdout=""))
    with pytest.raises(ValueError, match="never builds or pulls"):
        f.cut.validate(**f.args)


@pytest.mark.parametrize("wrong", [False, True])
def test_served_ui_must_match_original_admission_not_just_current_disk(reviewed_cut, wrong):
    cut = reviewed_cut.cut
    checked = {"index_sha256": cut.ui_files["index.html"],
               "entry_assets": {"/assets/main.js": cut.ui_files["assets/main.js"]}}
    if wrong:
        checked["entry_assets"]["/assets/main.js"] = "0" * 64
        with pytest.raises(ValueError, match="admitted artifact inventory"):
            cut.validate_served_ui(checked)
    else:
        cut.validate_served_ui(checked)


def test_changed_old_graph_is_rejected_even_with_current_manifest(reviewed_cut, monkeypatch):
    f = reviewed_cut
    monkeypatch.setattr(cuts.subprocess, "check_output", lambda *_: b"different old graph")
    with pytest.raises(ValueError, match="graph/dependency"):
        f.cut.validate(**f.args)


@pytest.mark.parametrize("wrong", ["health", "asset"])
def test_manifest_cannot_override_original_actual_health_or_asset_evidence(reviewed_cut, wrong):
    f = reviewed_cut
    receipt = json.loads(f.receipt.read_text())
    if wrong == "health":
        receipt["health"]["ai"]["deployment"]["git_sha"] = "0" * 40
    else:
        receipt["ui_asset_sha256"] = "0" * 64
    f.receipt.write_text(json.dumps(receipt))
    f.value["source_receipt"]["sha256"] = cuts.file_hash(f.receipt)
    cut = f.load()
    with pytest.raises(ValueError, match="qualified runtime receipt"):
        cut.validate(**f.args)
    assert not f.calls


def test_missing_retained_image_fails_before_generated_config(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(local_stack.shutil, "which", lambda *_args, **_kwargs: "/bin/docker")
    def inspect(command, **_kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 1, stdout="")
    monkeypatch.setattr(local_stack.subprocess, "run", inspect)
    with pytest.raises(ValueError, match="no build or pull"):
        local_stack.prepare_up(tmp_path, tmp_path / "langgraph", {},
                               Stack.load(Path("environments.toml"), "local-up"), 2124, "a" * 40,
                               tmp_path, {}, retained_image_id="sha256:" + "f" * 64)
    assert len(calls) == 1 and calls[0][1:3] == ["image", "inspect"]
    assert list(tmp_path.iterdir()) == []


def test_retained_cut_never_bypasses_normal_fresh_main_preflight(tmp_path, monkeypatch):
    monkeypatch.setattr(local_stack, "primary_paths", lambda *_: ({}, {}))
    def stale():
        raise ValueError("Primary source does not contain current origin/main")
    monkeypatch.setattr(local_stack, "verify_primary_checkouts", stale)
    with pytest.raises(ValueError, match="current origin/main"):
        local_stack.prepare(tmp_path / "local.toml", Stack.load(Path("environments.toml"), "local-up"),
                            tmp_path, "up", "build", retained_cut=SimpleNamespace())


def test_retained_mode_explicitly_disables_ui_refresh_before_request_write(tmp_path):
    refresh.write_json(tmp_path / refresh.CAPABILITY,
                       {"version": 1, "launcher_pid": os.getpid(),
                        "launcher_start": refresh.process_start(os.getpid()), "status": "disabled"})
    with pytest.raises(ValueError, match="disabled in retained-runtime"):
        refresh.request_refresh(tmp_path, "a" * 40, "b" * 64)
    assert not (tmp_path / refresh.REQUEST).exists()


@pytest.mark.parametrize("failure_phase", ["startup", "after-ready"])
def test_retained_ui_failure_still_stops_all_owned_services(reviewed_cut, tmp_path, monkeypatch, failure_phase):
    f = reviewed_cut
    state = tmp_path / "launcher-state"
    state.mkdir()
    children, stopped, commands = [], [], []
    readiness = {"checked": False}
    names = ["Backend", "Platform", "PlatformIdentity", "FrontDoor", "AI", "UI"]
    services = [(name, [name], tmp_path, {}, "http://localhost:1") for name in names]
    monkeypatch.setattr(sys, "argv", ["local_stack", "--ai-runtime", "up", "--ui-runtime", "build",
                                     "--config", str(local_stack.PROJECT_ROOT / "AgentTesting/local-stack.toml"),
                                     "--environments", str(local_stack.PROJECT_ROOT / "AgentTesting/environments.toml"),
                                     "--retained-cut", str(f.manifest), "--retained-cut-admission", str(f.admission),
                                     "--retained-cut-admission-sha", cuts.file_hash(f.admission)])
    monkeypatch.setattr(local_stack, "private_dir", lambda *_: state)
    monkeypatch.setattr(local_stack.Stack, "load", lambda *_: f.stack)
    monkeypatch.setattr(local_stack, "check_ports", lambda *_: None)
    monkeypatch.setattr(local_stack, "prepare", lambda *_args, **_kwargs: services)
    monkeypatch.setattr(local_stack, "token_path", lambda: f.manifest)
    monkeypatch.setattr(local_stack, "cached_token", lambda **_: "fixture")
    monkeypatch.setattr(local_stack, "wait_ready", lambda *_: None)
    monkeypatch.setattr(local_stack, "verify_platform_identity", lambda service: {"role": "identity", "native_jwks_match": True})
    async def health(*_args):
        readiness["checked"] = True
        return {"ai": {"deployment": {"git_sha": f.cut.runtime_sources["EpochAI"]},
                       "reference_version": f.cut.native_reference_version}}
    monkeypatch.setattr(local_stack, "check_stack", health)
    monkeypatch.setattr(local_stack, "probe", lambda *_: {
        "index_sha256": f.cut.ui_files["index.html"],
        "entry_assets": {"/assets/main.js": f.cut.ui_files["assets/main.js"]}})
    def spawn(command, **_kwargs):
        child = SimpleNamespace(pid=100 + len(children), poll=lambda: 1 if command == ["UI"] and
                                (failure_phase == "startup" or readiness["checked"]) else None)
        children.append(child)
        return child
    monkeypatch.setattr(local_stack.subprocess, "Popen", spawn)
    monkeypatch.setattr(local_stack.subprocess, "run", lambda command, **_: commands.append(command))
    monkeypatch.setattr(local_stack, "stop", lambda values: stopped.extend(values))
    monkeypatch.setattr(local_stack, "primary_paths", lambda *_: ({}, {"ai_dir": tmp_path}))
    monkeypatch.setattr(local_stack, "ui_refresh_controller", lambda *_: pytest.fail("retained UI must keep baseline liveness"))
    with pytest.raises(SystemExit) as exited:
        local_stack.main()
    assert exited.value.code == 1
    assert stopped == children and len(stopped) == 6
    assert len(commands) == 1 and commands[0][:2] == ["docker", "compose"]
    assert not (state / refresh.CAPABILITY).exists()
    if failure_phase == "startup":
        assert not readiness["checked"]
        assert not (state / "health.json").exists()
    else:
        receipt = json.loads((state / "health.json").read_text())
        assert receipt["retained_runtime_cut"]["runtime_sources"] == f.cut.runtime_sources
        assert receipt["platform_identity"] == {"role": "identity", "native_jwks_match": True}


def test_preflight_failure_never_composes_down_an_existing_stack(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr(sys, "argv", ["local_stack", "--ai-runtime", "up"])
    monkeypatch.setattr(local_stack, "private_dir", lambda *_: state)
    stack = Stack.load(Path("environments.toml"), "local-up")
    monkeypatch.setattr(local_stack.Stack, "load", lambda *_: stack)
    def occupied(*_):
        raise ValueError("Port occupied")
    monkeypatch.setattr(local_stack, "check_ports", occupied)
    monkeypatch.setattr(local_stack, "stop", lambda children: None)
    monkeypatch.setattr(local_stack.subprocess, "run", lambda *_args, **_kwargs: pytest.fail("Must not stop another launcher"))
    with pytest.raises(SystemExit):
        local_stack.main()
