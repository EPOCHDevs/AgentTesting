"""Portal staging/swap/rollback cannot stop the other local services."""

import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from unittest.mock import Mock
from uuid import uuid4

import httpx
import pytest

from tools import local_stack, local_ui_refresh as ui


class Child:
    def __init__(self, pid):
        self.pid, self.dead = pid, False

    def poll(self):
        return 1 if self.dead else None


def preview(tmp_path, commit="a" * 40):
    bundle = tmp_path / commit
    (bundle / "assets").mkdir(parents=True, exist_ok=True)
    (bundle / "assets/main.js").write_text(f"// {commit}\n")
    (bundle / "index.html").write_text('<script type="module" src="/assets/main.js"></script>')
    return ui.Preview(["corepack", "pnpm", "exec", "vite", "preview", "--outDir", str(bundle),
                       "--host", "127.0.0.1", "--port", "3000", "--strictPort"],
                      tmp_path, {"VITE_API_BASE_URL": "http://localhost:9000"},
                      "http://localhost:3000", commit, "c" * 64)


def controller(tmp_path, *, failure=None):
    old = preview(tmp_path)
    new = preview(tmp_path, "b" * 40)
    others, active, events = [Child(i) for i in range(100, 104)], Child(104), []
    children = [*others, active]

    def stage(commit, configuration):
        events.append("stage")
        if failure == "build":
            raise ValueError("build failed")
        assert (commit, configuration) == (new.commit, new.configuration)
        return new

    def start(value, log):
        shadow = value.url != old.url
        role = "shadow" if shadow else "new" if value.commit == new.commit else "rollback"
        events.append("start:" + role)
        if failure == "bind" and role == "new":
            raise OSError("address in use")
        child = Child(200 + len(events))
        child.role = role
        return child

    def stop(values):
        for child in values:
            assert child not in others
            events.append("stop:" + getattr(child, "role", "old"))
            child.dead = True

    def check(value, child, remaining):
        assert [x.pid for x in remaining] == [x.pid for x in others]
        role = getattr(child, "role", "old")
        events.append("check:" + role)
        if (failure == "shadow" and role == "shadow"
                or failure in ("health", "rollback") and role == "new"
                or failure == "rollback" and role == "rollback"):
            raise ValueError("asset/health check failed")
        return {"index_sha256": "d" * 64, "entry_assets": {"/assets/main.js": "e" * 64}}

    value = ui.Controller(tmp_path, old, active, children, stage, start, stop, check)
    return value, others, active, events


def submit(value, **changes):
    request = {"version": 1, "request_id": str(uuid4()), "launcher_pid": value.pid,
               "launcher_start": value.started, "commit": "b" * 40, "configuration": "c" * 64,
               **changes}
    ui.write_json(value.state / ui.REQUEST, request)
    value.tick()
    return json.loads((value.state / ui.RESULT).read_text())


def test_stages_before_stopping_old_ui_and_only_replaces_ui(tmp_path):
    value, others, old, events = controller(tmp_path)
    result = submit(value)
    assert result["status"] == "ready"
    assert events == ["stage", "start:shadow", "check:shadow", "stop:old",
                      "start:new", "check:new", "stop:shadow"]
    assert value.child is not old and value.preview.commit == "b" * 40
    assert result["non_ui_pids"] == [x.pid for x in others]
    assert all(not x.dead for x in others)
    assert {p.pid for p in value.children} == {p.pid for p in others} | {value.child.pid}
    assert (tmp_path / ("a" * 40) / "index.html").exists()


def test_same_source_and_configuration_is_read_only(tmp_path):
    value, others, old, events = controller(tmp_path)
    result = submit(value, commit="a" * 40)
    assert result["status"] == "unchanged" and value.child is old
    assert events == ["check:old"] and all(not p.dead for p in others)


@pytest.mark.parametrize("failure", ["build", "shadow", "bind", "health", "rollback"])
def test_stage_or_swap_failure_retains_or_rolls_back_ui_without_stopping_other_services(tmp_path, failure):
    value, others, old, events = controller(tmp_path, failure=failure)
    result = submit(value)
    assert result["status"] == "failed"
    assert all(not x.dead and x in value.children for x in others)
    assert value.preview.commit == "a" * 40
    if failure in ("build", "shadow"):
        assert value.child is old and not old.dead and "stop:old" not in events
    elif failure == "rollback":
        assert result["rollback_healthy"] is False and value.child is None
        assert json.loads((tmp_path / ui.CAPABILITY).read_text())["status"] == "degraded"
    else:
        assert result["rollback_healthy"] is True and value.child.role == "rollback"


@pytest.mark.parametrize("changes", [
    {"commit": "wrong"}, {"configuration": "wrong"}, {"launcher_start": "old"},
    {"launcher_pid": -1}, {"version": True}, {"extra": "ignored?"}, {"request_id": "not-a-uuid"},
])
def test_invalid_or_stale_request_cannot_start_or_stop_anything(tmp_path, changes):
    value, others, old, events = controller(tmp_path)
    assert submit(value, **changes)["status"] == "failed"
    assert events == [] and value.child is old and all(not p.dead for p in others)


def test_configuration_change_cannot_replace_or_reconfigure_running_ui(tmp_path):
    value, others, old, events = controller(tmp_path)
    assert submit(value, configuration="d" * 64)["status"] == "failed"
    assert events == [] and value.child is old and all(not p.dead for p in others)


def test_config_drift_during_shadow_validation_keeps_original_ui(tmp_path):
    value, others, old, events = controller(tmp_path)
    calls = []

    def guard():
        calls.append(1)
        if len(calls) == 2:
            raise ValueError("configuration changed during staging")

    value.guard = guard
    assert submit(value)["status"] == "failed"
    assert "stop:old" not in events and value.child is old
    assert events[-1] == "stop:shadow" and all(not p.dead for p in others)


def test_ui_exit_does_not_stop_backend_ai_platform_or_front_door(tmp_path):
    value, others, old, events = controller(tmp_path)
    old.dead = True
    value.tick()
    assert value.child is None and events == ["stop:old"]
    assert all(p in value.children and not p.dead for p in others)


def test_old_launcher_or_stale_capability_is_refused_before_request_write(tmp_path):
    with pytest.raises(ValueError, match="coordinated restart"):
        ui.request_refresh(tmp_path, "a" * 40, "c" * 64)
    assert not (tmp_path / ui.REQUEST).exists()
    value, *_ = controller(tmp_path)
    cap = json.loads((tmp_path / ui.CAPABILITY).read_text())
    cap["launcher_start"] = "old-process"
    ui.write_json(tmp_path / ui.CAPABILITY, cap)
    with pytest.raises(ValueError, match="stale"):
        ui.request_refresh(tmp_path, "b" * 40, "c" * 64)
    assert not (tmp_path / ui.REQUEST).exists()


def test_client_and_owner_exchange_one_result_and_serialize_requests(tmp_path):
    value, others, old, events = controller(tmp_path)
    result = []
    thread = threading.Thread(target=lambda: result.append(
        ui.request_refresh(tmp_path, "b" * 40, "c" * 64, timeout=3)))
    thread.start()
    deadline = time.monotonic() + 1
    while not (tmp_path / ui.REQUEST).exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    with pytest.raises(ValueError, match="already being observed"):
        ui.request_refresh(tmp_path, "b" * 40, "c" * 64, timeout=1)
    value.tick()
    thread.join(timeout=1)
    assert not thread.is_alive() and result[0]["status"] == "ready"
    assert events.count("stage") == 1 and all(not p.dead for p in others)


def test_unknown_client_outcome_cannot_overwrite_pending_request(tmp_path):
    value, *_ = controller(tmp_path)
    with pytest.raises(ValueError, match="not yet reconciled"):
        ui.request_refresh(tmp_path, "b" * 40, "c" * 64, timeout=0)
    previous = (tmp_path / ui.REQUEST).read_bytes()
    with pytest.raises(ValueError, match="still pending"):
        ui.request_refresh(tmp_path, "b" * 40, "c" * 64, timeout=0)
    assert (tmp_path / ui.REQUEST).read_bytes() == previous


def test_failed_acknowledgement_write_cannot_repeat_the_ui_replacement(tmp_path, monkeypatch):
    value, others, old, events = controller(tmp_path)
    write = ui.write_json
    failed = []

    def fail_result_once(path, body):
        if path.name == ui.RESULT and not failed:
            failed.append(1)
            raise OSError("receipt disk write failed")
        return write(path, body)

    monkeypatch.setattr(ui, "write_json", fail_result_once)
    with pytest.raises(OSError, match="receipt disk write failed"):
        submit(value)
    first_replacement = value.child
    value.tick()
    assert value.child is first_replacement and events.count("stage") == 1
    assert json.loads((tmp_path / ui.RESULT).read_text())["status"] == "ready"
    assert all(not p.dead for p in others)


@pytest.mark.parametrize("bad", [False, True])
def test_probe_checks_actual_index_and_module_asset_only(tmp_path, monkeypatch, bad):
    value = preview(tmp_path)
    calls = []
    original = httpx.Client

    def route(request):
        calls.append((request.method, request.url.path))
        path = value.bundle / ("index.html" if request.url.path == "/" else request.url.path.lstrip("/"))
        data = b"wrong source" if bad and request.url.path.endswith(".js") else path.read_bytes()
        return httpx.Response(200, content=data, headers={
            "content-type": "text/javascript" if request.url.path.endswith(".js") else "text/html"})

    monkeypatch.setattr(ui.httpx, "Client", lambda **kwargs: original(
        transport=httpx.MockTransport(route), **kwargs))
    if bad:
        with pytest.raises(ValueError, match="entry asset differs"):
            ui.probe(value, Child(200), [Child(100)])
    else:
        checked = ui.probe(value, Child(200), [Child(100)])
        assert checked["index_sha256"] == hashlib.sha256((value.bundle / "index.html").read_bytes()).hexdigest()
    assert calls == [("GET", "/"), ("GET", "/assets/main.js")]


def test_probe_rejects_remote_or_proxy_entry_script_before_http(tmp_path, monkeypatch):
    value = preview(tmp_path)
    (value.bundle / "index.html").write_text('<script type="module" src="/lg-api/threads"></script>')
    network = Mock(side_effect=AssertionError("no HTTP"))
    monkeypatch.setattr(ui.httpx, "Client", network)
    with pytest.raises(ValueError, match="local /assets"):
        ui.probe(value, Child(200), [])
    network.assert_not_called()


def test_corrupted_capability_is_refused_without_writing_a_request(tmp_path):
    ui.write_json(tmp_path / ui.CAPABILITY, {"version": True, "launcher_pid": "not-a-pid"})
    with pytest.raises(ValueError, match="capability is invalid"):
        ui.request_refresh(tmp_path, "b" * 40, "c" * 64)
    assert not (tmp_path / ui.REQUEST).exists()


def test_refresh_factory_requires_exact_primary_source_and_configuration(tmp_path, monkeypatch):
    value = preview(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    ui.write_json(state / "source-checkouts.json", {"EpochClientPortal": {"commit": value.commit}})
    stack = local_stack.Stack.load(Path("environments.toml"), "local")
    monkeypatch.setattr(local_stack, "verify_primary_checkouts", lambda: {
        "EpochClientPortal": {"path": str(tmp_path), "commit": "b" * 40}})
    build = Mock()
    monkeypatch.setattr(local_stack, "prepare_ui_build", build)
    child = Child(200)
    manager = local_stack.ui_refresh_controller(state, stack,
        ("UI", value.command, value.cwd, value.env, value.url), child, [child])
    with pytest.raises(ValueError, match="qualified target"):
        manager.stage("d" * 40, value.configuration)
    with pytest.raises(ValueError, match="configuration differs"):
        manager.stage("b" * 40, "f" * 64)
    build.assert_not_called()


def test_refresh_factory_refuses_changed_proxy_or_dependency_inputs(tmp_path, monkeypatch):
    value = preview(tmp_path)
    (tmp_path / "vite.config.ts").write_text("old proxy configuration")
    state = tmp_path / "state"
    state.mkdir()
    ui.write_json(state / "source-checkouts.json", {"EpochClientPortal": {"commit": value.commit}})
    stack = local_stack.Stack.load(Path("environments.toml"), "local")
    child = Child(200)
    manager = local_stack.ui_refresh_controller(state, stack,
        ("UI", value.command, value.cwd, value.env, value.url), child, [child])
    (tmp_path / "vite.config.ts").write_text("new proxy configuration")
    with pytest.raises(ValueError, match="configuration/dependencies changed"):
        manager.guard()


def test_ui_build_timeout_cannot_remove_retained_bundle(tmp_path, monkeypatch):
    retained = tmp_path / "ui-builds" / "retained"
    retained.mkdir(parents=True)
    (retained / "index.html").write_text("old bundle")
    stack = local_stack.Stack.load(Path("environments.toml"), "local")
    monkeypatch.setattr(local_stack.subprocess, "run", Mock(side_effect=subprocess.TimeoutExpired("build", 180)))
    with pytest.raises(ValueError, match="UI build timed out"):
        local_stack.prepare_ui_build(tmp_path, "corepack", {}, stack, 3000, "b" * 40, tmp_path)
    assert (retained / "index.html").read_text() == "old bundle"


@pytest.mark.parametrize("fail_replacement", [False, True])
def test_real_loopback_preview_swap_and_rollback_preserve_other_fixture_pids(tmp_path, fail_replacement):
    # Real process and HTTP behavior, using a tiny static test fixture, never
    # the user's Portal, Backend, accounts, proxies, providers or model server.
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    fixture = tmp_path / "preview.py"
    fixture.write_text('''import argparse, functools
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
p=argparse.ArgumentParser()
p.add_argument('--host');p.add_argument('--port',type=int);p.add_argument('--outDir')
a=p.parse_args()
if FAIL and Path(a.outDir).name.startswith('b') and a.port==PORT:raise SystemExit(2)
ThreadingHTTPServer((a.host,a.port),functools.partial(SimpleHTTPRequestHandler,directory=a.outDir)).serve_forever()
'''.replace('FAIL', repr(fail_replacement)).replace('PORT', str(port)))
    original = preview(tmp_path)
    def real(value):
        return ui.Preview([sys.executable,str(fixture),'--host','127.0.0.1','--port',str(port),
                           '--outDir',str(value.bundle)],tmp_path,dict(os.environ),
                          f'http://127.0.0.1:{port}',value.commit,value.configuration)
    old, new = real(original), real(preview(tmp_path,'b'*40))
    others = [subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],start_new_session=True)
              for _ in range(4)]
    children = list(others)
    value = None
    try:
        active = local_stack.start_preview(old,tmp_path/'original.log')
        children.append(active)
        ui.probe(old,active,others)
        value = ui.Controller(tmp_path,old,active,children,lambda *_:new,
                              local_stack.start_preview,local_stack.stop)
        result = submit(value)
        assert result['non_ui_pids']==[p.pid for p in others]
        assert all(p.poll() is None for p in others)
        if fail_replacement:
            assert result['status']=='failed' and result['rollback_healthy']
            assert value.preview.commit==old.commit
        else:
            assert result['status']=='ready' and value.preview.commit==new.commit
        assert value.child.pid!=active.pid and active.poll() is not None
        ui.probe(value.preview,value.child,others)
    finally:
        if value:value.close()
        local_stack.stop(children)
