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


@pytest.mark.parametrize("operator_revision", [None, "", "  ", "operator-deployment-7"])
def test_configuration_keeps_all_services_local(tmp_path, monkeypatch, operator_revision):
    ai, ui, backend = [
        tmp_path / name for name in ("EpochAI", "EpochClientPortal", "EpochBackend")
    ]
    monkeypatch.setattr(local_stack, "PROJECT_ROOT", tmp_path)
    ai_commit = "a" * 40
    monkeypatch.setattr(
        local_stack, "verify_primary_checkouts",
        lambda: {"EpochAI": {"commit": ai_commit, "branch": "main", "path": str(ai)}},
    )
    for file in (
        backend / "build/bin/epoch_stratifyx_server",
        ai / ".venv/bin/langgraph",
    ):
        file.parent.mkdir(parents=True, exist_ok=True)
        file.touch()
        file.chmod(0o700)
    (backend / "build/bin/config.json").write_text(json.dumps({"plugins": [
        {"name": "epoch_stratifyx::server::MiPageRuntimeService", "config": {}}]}))
    (ui / "node_modules").mkdir(parents=True)
    (backend / "services/stratifyx").mkdir(parents=True)
    (backend / "services/stratifyx/.env.local").write_text(
        "EPOCH_ENVIRONMENT=prod\nEPOCH_SKIP_AUTH=1\nVENDOR_KEY=private\n"
    )
    (ai / ".env").write_text(
        "EPOCH_CAMPAIGN_SERVER=https://wrong.example\nMODEL_KEY=private\n"
        + (f'EPOCH_COMMENTARY_DEPLOYMENT_REVISION="{operator_revision}"\n'
           if operator_revision is not None else "")
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
    # An unconfigured local stack keeps Markets page work off (shared vendor quotas).
    assert be[3]["EPOCH_MI_PAGE_AUTO_REFRESH_ENABLED"] == "0" and be[3]["EPOCH_MI_RATES_PAGE"] == "0"
    runtime = json.loads((backend / "build/bin/config.json").read_text())
    assert runtime["plugins"][0]["config"]["calendar"] == {"enabled": False, "refresh_mode": "paused"}
    assert portal[3]["VITE_API_BASE_URL"] == stack.backend_url
    assert portal[3]["VITE_LANGGRAPH_API_URL"] == stack.ai_url
    generated = json.loads((state / "ai/langgraph.json").read_text())
    revision = (operator_revision or "").strip() or "local-" + ai_commit
    assert generated["env"]["EPOCH_COMMENTARY_DEPLOYMENT_REVISION"] == revision
    assert _agent[3]["EPOCH_COMMENTARY_DEPLOYMENT_REVISION"] == revision
    receipt = json.loads((state / "source-checkouts.json").read_text())
    assert receipt["EpochAI"]["commentary_deployment_revision"] == revision
    assert generated["env"]["EPOCH_CAMPAIGN_SERVER"] == stack.backend_url
    assert generated["graphs"]["epoch-ai"]["path"] == str(ai / "agent.py") + ":agent"
    assert generated["store"]["index"]["embed"] == str(ai / "embeddings.py") + ":embed"
    assert (state / "ai/langgraph.json").stat().st_mode & 0o777 == 0o600
    assert json.loads((ai / "langgraph.json").read_text()) == graph
    for _, command, _, env, _ in services:
        assert "EPOCH_TOKEN" not in env and "TEST_EPOCH_PASSWORD" not in env
        assert "private" not in " ".join(command)


def test_local_cache_does_not_authenticate_hosted_profiles(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("EPOCH_TOKEN", raising=False)
    monkeypatch.setattr(local_auth, "cached_token", lambda: "local-token")
    assert (
        Stack.load(Path("environments.toml"), "local").bearer() == "Bearer local-token"
    )
    for env in ("dev", "qa", "prod"):
        with pytest.raises(ValueError, match="Set EPOCH_TOKEN"):
            Stack.load(Path("environments.toml"), env).bearer()
    monkeypatch.setenv("EPOCH_TOKEN", "explicit")
    assert Stack.load(Path("environments.toml"), "local").bearer() == "Bearer explicit"


QA = Stack.load(Path("environments.toml"), "qa")


def published(**changes):
    config = {
        "backendUrl": QA.backend_url,
        "agentUrl": QA.ai_url,
        "assistantId": QA.assistant_id,
        "amplifyOutputs": {
            "auth": {
                "user_pool_id": "us-east-1_TestPool",
                "user_pool_client_id": "test-client",
            }
        },
    }
    return config | changes


def serve(monkeypatch, body):
    def get(url, **_):
        assert url == "https://app.qa.epoch.trade/epoch-runtime-config.json"
        request = agent_testing.httpx.Request("GET", url)
        if isinstance(body, str):
            return agent_testing.httpx.Response(200, text=body, request=request)
        return agent_testing.httpx.Response(200, json=body, request=request)

    monkeypatch.setattr(agent_testing.httpx, "get", get)


def test_hosted_login_uses_the_pool_its_portal_publishes(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("EPOCH_TOKEN", raising=False)
    serve(monkeypatch, published())
    values = tokens(9999999999)
    pools = []

    def client(pool, **_):
        pools.append(pool)
        user = SimpleNamespace(**{k: v for k, v in values.items() if k != "email"})
        user.authenticate = lambda **_: None
        return user

    monkeypatch.setattr(local_auth, "client", client)
    monkeypatch.setattr(local_auth.getpass, "getpass", lambda _: "private")
    local_auth.save_tokens(tokens(9999999998), local_auth.token_path())
    QA.login("test@example.com")
    assert pools == [local_auth.UserPool("us-east-1_TestPool", "test-client")]
    cache = tmp_path / ".config/epoch-agent-testing/app.qa.epoch.trade-login.json"
    assert QA.login_path() == cache
    assert json.loads(cache.read_text())["user_pool_id"] == "us-east-1_TestPool"
    # Each stack reads only its own login.
    assert QA.bearer() == "Bearer " + values["id_token"]
    local = Stack.load(Path("environments.toml"), "local")
    assert local.bearer() == "Bearer " + tokens(9999999998)["id_token"]
    with pytest.raises(ValueError, match="Set EPOCH_TOKEN"):
        Stack.load(Path("environments.toml"), "dev").bearer()


@pytest.mark.parametrize(
    "body, message",
    [
        ("<!doctype html>", "publishes no runtime config"),
        (published(backendUrl="https://api.other.example"), "different backendUrl"),
        (published(agentUrl="https://agent.other.example"), "different agentUrl"),
    ],
)
def test_login_refuses_an_unknown_or_mismatched_portal(
    tmp_path, monkeypatch, body, message
):
    monkeypatch.setenv("HOME", str(tmp_path))
    serve(monkeypatch, body)
    monkeypatch.setattr(local_auth, "client", lambda **_: pytest.fail("no sign-in"))
    with pytest.raises(ValueError, match=message):
        QA.login("test@example.com")
    assert not QA.login_path().exists()


def test_hosted_login_refreshes_with_the_pool_that_issued_it(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("EPOCH_TOKEN", raising=False)
    pool = local_auth.UserPool("us-east-1_TestPool", "test-client")
    expired = tokens(1) | {"user_pool_id": pool.pool_id, "client_id": pool.client_id}
    local_auth.save_tokens(expired, QA.login_path())
    fresh = tokens(9999999999)
    pools = []

    def client(pool, **_):
        pools.append(pool)
        user = SimpleNamespace(**{k: v for k, v in fresh.items() if k != "email"})
        user.renew_access_token = lambda: None
        return user

    monkeypatch.setattr(local_auth, "client", client)
    assert QA.bearer() == "Bearer " + fresh["id_token"]
    assert pools == [pool]
    assert json.loads(QA.login_path().read_text())["user_pool_id"] == pool.pool_id


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


def test_up_profile_is_local_with_its_own_agent_port(tmp_path, monkeypatch):
    up = Stack.load(Path("environments.toml"), "local-up")
    assert up.is_local and local_stack.local_ports(up) == [9000, 2124, 3000]
    assert local_stack.local_ports(Stack.load(Path("environments.toml"), "local")) == [
        9000,
        2024,
        3000,
    ]
    monkeypatch.delenv("EPOCH_TOKEN", raising=False)
    monkeypatch.setattr(local_auth, "cached_token", lambda: "local-token")
    assert up.bearer() == "Bearer local-token"
    assert up.login_path() == local_auth.token_path()


def test_up_container_env_reaches_the_host_and_mounts_named_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(local_stack, "PROJECT_ROOT", tmp_path)
    (tmp_path / "templates").mkdir()
    values = {
        "EPOCH_BACKEND_URL": "http://localhost:9000",
        "EPOCH_CAMPAIGN_SERVER": "http://localhost:9000",
        "EPOCH_AI_URL": "http://localhost:2124",
        "LANGGRAPH_API_URL": "http://localhost:2124",
        "POSTGRES_URI": "postgresql://localhost:5432/langgraph_checkpoints",
        "SCRIPT_TEMPLATES_DIR": str(tmp_path / "templates"),
        "MISSING_DIR": str(tmp_path / "absent"),
        "OUTSIDE_DIR": "/usr",
    }
    env, mounts = local_stack.up_container_env(values)
    assert env["EPOCH_BACKEND_URL"] == "http://host.docker.internal:9000"
    assert env["EPOCH_CAMPAIGN_SERVER"] == "http://host.docker.internal:9000"
    assert env["EPOCH_AI_URL"] == env["LANGGRAPH_API_URL"] == "http://localhost:8000"
    assert "POSTGRES_URI" not in env
    assert mounts == [str(tmp_path / "templates")]
    assert values["EPOCH_BACKEND_URL"] == "http://localhost:9000"  # input untouched
    override = local_stack.up_compose_override(mounts)
    assert '"host.docker.internal:host-gateway"' in override
    assert f'"{tmp_path}/templates:{tmp_path}/templates:ro"' in override


def test_up_env_file_is_literal_and_rejects_unquotable_values():
    text = local_stack.env_file_text({"B": "x$HOME\\y", "A": "1"})
    assert text == "A='1'\nB='x$HOME\\y'\n"
    for bad in ("it's", "two\nlines"):
        with pytest.raises(ValueError, match="BAD cannot be written"):
            local_stack.env_file_text({"BAD": bad})


def test_up_files_are_excluded_locally_once(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    names = (local_stack.UP_CONFIG, local_stack.UP_ENV, local_stack.UP_COMPOSE)
    local_stack.exclude_locally(tmp_path, names)
    local_stack.exclude_locally(tmp_path, names)
    lines = (tmp_path / ".git/info/exclude").read_text().splitlines()
    assert [line for line in lines if line in names] == list(names)
    for name in names:
        (tmp_path / name).write_text("generated")
    status = subprocess.run(
        ["git", "-C", str(tmp_path), "status", "--porcelain"], capture_output=True, text=True
    ).stdout
    assert status == ""
