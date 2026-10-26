import argparse
import asyncio
import json
import socket
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import jwt
import pytest

from tools import agent_testing, local_auth, local_stack
from tools.agent_testing import Stack


def test_local_profiles_only():
    assert local_stack.local_ports(Stack.load(Path("environments.toml"), "local")) == [
        9000,
        2024,
        3000,
    ]
    with pytest.raises(ValueError, match="localhost"):
        local_stack.local_ports(Stack.load(Path("environments.toml"), "dev"))


def test_occupied_port_is_not_reused():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        with pytest.raises(ValueError, match="occupied"):
            local_stack.check_ports([sock.getsockname()[1]])


def test_configuration_keeps_all_services_local(tmp_path, monkeypatch):
    ai, ui, backend = [
        tmp_path / name for name in ("EpochAI", "EpochClientPortal", "EpochBackend")
    ]
    monkeypatch.setattr(local_stack, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(local_stack, "verify_primary_checkouts", lambda: {})
    for file in (
        backend / "build/bin/epoch_stratifyx_server",
        ai / ".venv/bin/langgraph",
    ):
        file.parent.mkdir(parents=True, exist_ok=True)
        file.touch()
        file.chmod(0o700)
    (ui / "node_modules").mkdir(parents=True)
    (backend / "services/stratifyx").mkdir(parents=True)
    (backend / "services/stratifyx/.env.local").write_text(
        "EPOCH_ENVIRONMENT=prod\nEPOCH_SKIP_AUTH=1\nVENDOR_KEY=private\n"
    )
    (ai / ".env").write_text(
        "EPOCH_CAMPAIGN_SERVER=https://wrong.example\nMODEL_KEY=private\n"
    )
    graph = {
        "dependencies": [".", "./packages/local"],
        "env": ".env",
        "graphs": {"epoch-ai": {"path": "agent.py:agent"}},
        "auth": {"path": "webapp.py:auth"},
        "http": {"app": "webapp.py:app"},
        "store": {"index": {"embed": "embeddings.py:embed"}},
    }
    (ai / "langgraph.json").write_text(json.dumps(graph))
    config = tmp_path / "local.toml"
    config.write_text("")
    monkeypatch.setenv("EPOCH_TOKEN", "must-not-inherit")
    monkeypatch.setenv("TEST_EPOCH_PASSWORD", "must-not-inherit")
    monkeypatch.setattr(local_stack.shutil, "which", lambda name, **_: "/bin/" + name)
    monkeypatch.setattr(
        local_stack.subprocess, "check_output", lambda *_, **__: "v24.1.0"
    )
    stack = Stack.load(Path("environments.toml"), "local")
    state = local_stack.private_dir(tmp_path / "state")
    services = local_stack.prepare(config, stack, state)
    assert len(services) == 3
    be, _agent, portal = services
    assert be[3]["EPOCH_ENVIRONMENT"] == "local"
    assert be[3]["EPOCH_SKIP_AUTH"] == "0"
    assert be[3]["EPOCH_AI_URL"] == stack.ai_url
    assert be[3]["EPOCH_PROJECT_DIR"] == str(state / "backend")
    assert portal[3]["VITE_API_BASE_URL"] == stack.backend_url
    assert portal[3]["VITE_LANGGRAPH_API_URL"] == stack.ai_url
    generated = json.loads((state / "ai/langgraph.json").read_text())
    assert generated["env"]["EPOCH_CAMPAIGN_SERVER"] == stack.backend_url
    assert generated["graphs"]["epoch-ai"]["path"] == str(ai / "agent.py") + ":agent"
    assert generated["store"]["index"]["embed"] == str(ai / "embeddings.py") + ":embed"
    assert (state / "ai/langgraph.json").stat().st_mode & 0o777 == 0o600
    assert json.loads((ai / "langgraph.json").read_text()) == graph
    for _, command, _, env, _ in services:
        assert "EPOCH_TOKEN" not in env and "TEST_EPOCH_PASSWORD" not in env
        assert "private" not in " ".join(command)


def test_local_cache_does_not_authenticate_hosted_profiles(monkeypatch):
    monkeypatch.delenv("EPOCH_TOKEN", raising=False)
    monkeypatch.setattr(local_auth, "cached_token", lambda: "local-token")
    assert (
        Stack.load(Path("environments.toml"), "local").bearer() == "Bearer local-token"
    )
    for env in ("dev", "prod"):
        with pytest.raises(ValueError, match="Set EPOCH_TOKEN"):
            Stack.load(Path("environments.toml"), env).bearer()
    monkeypatch.setenv("EPOCH_TOKEN", "explicit")
    assert Stack.load(Path("environments.toml"), "local").bearer() == "Bearer explicit"


def test_mismatched_attempt_is_rejected_before_auth_refresh(tmp_path, monkeypatch):
    folder = tmp_path / "existing"
    folder.mkdir()
    (folder / "attempt.json").write_text(json.dumps({"environment": "dev"}))
    monkeypatch.delenv("EPOCH_TOKEN", raising=False)

    def unexpected():
        pytest.fail("Cognito refresh must not precede the attempt identity check")

    monkeypatch.setattr(local_auth, "cached_token", unexpected)
    args = argparse.Namespace(
        action="status",
        config=Path("environments.toml"),
        env="local",
        artifacts=tmp_path,
        attempt="existing",
    )
    with pytest.raises(ValueError, match="belongs to dev"):
        asyncio.run(agent_testing.run(args))


def tokens(expiry):
    return {
        "email": "test@example.com",
        "username": "user",
        "id_token": jwt.encode({"exp": expiry}, "x" * 32),
        "access_token": "access",
        "refresh_token": "refresh",
    }


def test_private_cache_and_refresh(tmp_path, monkeypatch):
    path = tmp_path / "private/login.json"
    local_auth.save_tokens(tokens(1), path)
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    fresh = tokens(9999999999)
    user = SimpleNamespace(**{k: v for k, v in fresh.items() if k != "email"})
    user.renew_access_token = lambda: None
    monkeypatch.setattr(local_auth, "client", lambda **_: user)
    assert local_auth.cached_token(path, "test@example.com") == fresh["id_token"]
    assert json.loads(path.read_text()) == fresh
    with pytest.raises(ValueError, match="another account"):
        local_auth.cached_token(path, "another@example.com")


def test_login_does_not_persist_password_or_print_tokens(tmp_path, monkeypatch, capsys):
    path = tmp_path / "private/login.json"
    values = tokens(9999999999)
    user = SimpleNamespace(**{k: v for k, v in values.items() if k != "email"})
    user.authenticate = lambda **_: None
    monkeypatch.setattr(local_auth, "client", lambda **_: user)
    monkeypatch.setattr(local_auth.getpass, "getpass", lambda _: "sensitive-password")
    local_auth.login("test@example.com", path)
    assert "sensitive-password" not in path.read_text()
    output = capsys.readouterr().out
    assert "sensitive-password" not in output and values["id_token"] not in output


def test_auth_errors_do_not_echo_sdk_secrets(tmp_path, monkeypatch):
    def fail(**kwargs):
        raise RuntimeError("secret in SDK error")

    monkeypatch.setattr(local_auth, "client", fail)
    monkeypatch.setattr(local_auth.getpass, "getpass", lambda _: "private")
    with pytest.raises(ValueError, match="Epoch login failed") as err:
        local_auth.login("test@example.com", tmp_path / "login.json")
    assert "secret" not in str(err.value)


def test_cleanup_stops_only_owned_processes():
    owned = subprocess.Popen(["sleep", "30"], start_new_session=True)
    unrelated = subprocess.Popen(["sleep", "30"], start_new_session=True)
    try:
        local_stack.stop([owned])
        assert owned.poll() is not None
        assert unrelated.poll() is None
    finally:
        unrelated.terminate()
        unrelated.wait()


def test_ready_check_reports_early_exit():
    child = SimpleNamespace(poll=lambda: 1)
    with pytest.raises(ValueError, match="exited"):
        local_stack.wait_ready("http://localhost:1", [child], 1)


@pytest.mark.skipif(sys.platform != "linux", reason="Reads Linux process state")
def test_cleanup_kills_descendant_after_leader_exits(tmp_path):
    ready = tmp_path / "child.pid"
    grandchild = (
        "import os,signal,time,pathlib; signal.signal(signal.SIGTERM,signal.SIG_IGN); pathlib.Path(%r).write_text(str(os.getpid())); time.sleep(30)"
        % str(ready)
    )
    parent = (
        "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',%r]); time.sleep(30)"
        % grandchild
    )
    leader = subprocess.Popen([sys.executable, "-c", parent], start_new_session=True)
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert ready.exists()
        proc = Path("/proc") / ready.read_text() / "stat"
        local_stack.stop([leader], grace=0.2)
        assert leader.poll() is not None
        deadline = time.monotonic() + 2
        while (
            proc.exists()
            and proc.read_text().split()[2] != "Z"
            and time.monotonic() < deadline
        ):
            time.sleep(0.05)
        assert not proc.exists() or proc.read_text().split()[2] == "Z"
    finally:
        local_stack.stop([leader], grace=0)
