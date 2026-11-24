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


@pytest.mark.parametrize("setting", [
    "EPOCH_IDENTITY_GDS_URL=https://identity.example\n",
    "EPOCH_IDENTITY_GDS_TOKEN=independent-identity-credential\n",
])
def test_platform_rejects_partial_identity_configuration(tmp_path, monkeypatch, setting):
    (tmp_path / ".env.local").write_text(setting)
    main = tmp_path / "apps/platform/dist/main.js"
    main.parent.mkdir(parents=True)
    main.touch()
    monkeypatch.setattr(local_stack.subprocess, "run", lambda *_args, **_kwargs:
                        subprocess.CompletedProcess([], 0))
    with pytest.raises(ValueError, match="both identity GDS URL and service token"):
        local_stack.prepare_platform(tmp_path, "/bin/node", {}, {}, {}, tmp_path,
                                     "p" * 40, tmp_path, tmp_path / "reference")


@pytest.mark.parametrize("operator_revision", [None, "", "  ", "operator-deployment-7"])
@pytest.mark.parametrize("native_mi_plugin", [False, True])
@pytest.mark.parametrize("identity_configured", [False, True])
def test_configuration_keeps_all_services_local(tmp_path, monkeypatch, operator_revision, native_mi_plugin, identity_configured):
    ai, ui, backend, platform = [
        tmp_path / name for name in ("EpochAI", "EpochClientPortal", "EpochBackend", "EpochPlatform")
    ]
    monkeypatch.setattr(local_stack, "PROJECT_ROOT", tmp_path)
    ai_commit = "a" * 40
    backend_commit = "b" * 40
    monkeypatch.setattr(
        local_stack, "verify_primary_checkouts",
        lambda: {
            "EpochAI": {"commit": ai_commit, "branch": "main", "path": str(ai)},
            "EpochPlatform": {"commit": "p" * 40, "branch": "dev", "path": str(platform)},
            "EpochBackend": {"commit": backend_commit, "branch": "main", "path": str(backend)},
        },
    )
    (platform / "node_modules/typescript").mkdir(parents=True)
    (platform / ".env.local").write_text(
        "EPOCH_DISCOVERY_GDS_URL=https://gds.example\n"
        "EPOCH_DISCOVERY_SERVICE_TOKEN=discovery-credential\n"
        "EPOCH_DISCOVERY_NATIVE_URL=https://wrong.example\nAWS_PROFILE=must-not-inherit\n"
        + ("EPOCH_IDENTITY_GDS_URL=https://identity.example\n"
           "EPOCH_IDENTITY_GDS_TOKEN=independent-identity-credential\n" if identity_configured else "")
    )
    builds = []

    def build(command, cwd, env, **_):
        builds.append((command, cwd, env))
        (platform / "apps/platform/dist").mkdir(parents=True, exist_ok=True)
        (platform / "apps/platform/dist/main.js").touch()
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(local_stack.subprocess, "run", build)
    monkeypatch.setattr(local_stack, "prepare_reference_files", lambda *_: tmp_path / "native-reference")
    for file in (
        backend / "build/bin/epoch_stratifyx_server",
        ai / ".venv/bin/langgraph",
    ):
        file.parent.mkdir(parents=True, exist_ok=True)
        file.touch()
        file.chmod(0o700)
    (backend / "build/bin/config.json").write_text(json.dumps({"plugins": [
        {"name": "epoch_stratifyx::server::MiPageRuntimeService", "config": {}}
    ] if native_mi_plugin else []}))
    (ui / "node_modules").mkdir(parents=True)
    (backend / "services/stratifyx").mkdir(parents=True)
    (backend / "services/stratifyx/.env.local").write_text(
        "EPOCH_ENVIRONMENT=prod\nEPOCH_SKIP_AUTH=1\nVENDOR_KEY=private\nEPOCH_GIT_SHA=stale-cloud-sha\n"
        "EPOCH_TOKEN_ISSUER=http://localhost:2024\n"
    )
    (ai / ".env").write_text(
        "EPOCH_CAMPAIGN_SERVER=https://wrong.example\nMODEL_KEY=private\n"
        "EPOCH_FILES_WORKER_URL=https://services.dev.example\n"
        "LANGSMITH_LANGGRAPH_GIT_REF=dev\nLANGSMITH_LANGGRAPH_GIT_REF_SHA=stale-cloud-sha\n"
        + (f'EPOCH_COMMENTARY_DEPLOYMENT_REVISION="{operator_revision}"\n'
           if operator_revision is not None else "")
    )
    (ai / ".env.local").write_text(
        "EPOCH_OPENAI_PROVIDER=openai\nOPENAI_DIRECT_API_KEY=private-direct-fixture\n"
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
    monkeypatch.setenv("AWS_PROFILE", "operator")
    services = local_stack.prepare(config, stack, state)
    assert [service[0] for service in services] == ["Backend", "Platform", "FrontDoor", "AI", "UI"]
    be, files, door, _agent, portal = services
    # The front door answers the profile's API origin, as the load balancer answers api.<env>.
    assert be[1][-1] == be[3]["PORT"] == str(local_stack.BACKEND_PORT)
    assert be[4] == f"http://127.0.0.1:{local_stack.BACKEND_PORT}/api/v1/health"
    assert door[1][1:] == [str(local_stack.FRONT_DOOR), "9000", str(local_stack.BACKEND_PORT), str(local_stack.PLATFORM_PORT)]
    assert door[4] == stack.backend_url + "/api/v1/platform/health"
    # Platform serves Files and Space from the engine's personal content, with no AWS account.
    assert builds == [([files[1][0], "node_modules/typescript/bin/tsc", "-b"], platform, builds[0][2])]
    assert files[1][1:] == ["apps/platform/dist/main.js"] and files[2] == platform
    assert files[3]["EPOCH_FILES_LOCAL_ROOT"] == str(state / "backend/personal_content")
    assert files[3]["EPOCH_DEFINITIONS_LOCAL_ROOT"] == str(state / "backend/definitions")
    assert files[3]["EPOCH_REFERENCE_LOCAL_ROOT"] == str(tmp_path / "native-reference")
    assert files[3]["EPOCH_DISCOVERY_GDS_URL"] == "https://gds.example"
    assert files[3]["EPOCH_DISCOVERY_SERVICE_TOKEN"] == "discovery-credential"
    assert files[3]["EPOCH_DISCOVERY_NATIVE_URL"] == f"http://127.0.0.1:{local_stack.BACKEND_PORT}"
    assert "EPOCH_DISCOVERY_SERVICE_TOKEN" not in _agent[3]
    if identity_configured:
        assert files[3]["EPOCH_IDENTITY_GDS_URL"] == "https://identity.example"
        assert files[3]["EPOCH_IDENTITY_GDS_TOKEN"] == "independent-identity-credential"
    else:
        assert "EPOCH_IDENTITY_GDS_URL" not in files[3]
        assert "EPOCH_IDENTITY_GDS_TOKEN" not in files[3]
    for service in (be, _agent, portal):
        assert "EPOCH_IDENTITY_GDS_URL" not in service[3]
        assert "EPOCH_IDENTITY_GDS_TOKEN" not in service[3]
    assert files[3]["EPOCH_ENVIRONMENT"] == "local" and files[3]["EPOCH_PLATFORM_ROLE"] == "general"
    assert files[3]["PORT"] == str(local_stack.PLATFORM_PORT)
    assert files[3]["EPOCH_PLATFORM_REVISION"] == "p" * 40
    assert files[3]["COGNITO_CLIENT_IDS"].split(",") == [local_stack.CLIENT, local_stack.CLI_CLIENT]
    assert files[3]["EPOCH_TOKEN_ISSUER"] == "http://localhost:2024"
    assert files[3]["EPOCH_TOKEN_JWKS_URL"] == f"http://127.0.0.1:{local_stack.BACKEND_PORT}/api/v1/auth/jwks"
    assert "AWS_PROFILE" not in files[3] and files[3]["AWS_SHARED_CREDENTIALS_FILE"] == files[3]["AWS_CONFIG_FILE"] == "/dev/null"
    assert "VENDOR_KEY" not in files[3] and "MODEL_KEY" not in files[3]
    # The Files workers' credential is Platform's and the agent's, no longer Backend's.
    token = files[3][local_stack.WORKER_TOKEN]
    assert _agent[3][local_stack.WORKER_TOKEN] == token and local_stack.WORKER_TOKEN not in be[3]
    for name in ("EPOCH_DOCUMENT_BACKEND_URL", "EPOCH_FILES_WORKER_URL"):
        assert _agent[3][name] == stack.backend_url
    assert be[3]["EPOCH_ENVIRONMENT"] == "local"
    assert be[3]["EPOCH_SKIP_AUTH"] == "0"
    assert be[3]["EPOCH_GIT_SHA"] == backend_commit
    assert be[3]["EPOCH_AI_URL"] == stack.ai_url
    assert be[3]["EPOCH_PROJECT_DIR"] == str(state / "backend")
    # Backend exchanges ID tokens from the Portal's client and from `epoch login`'s.
    assert be[3]["COGNITO_CLIENT_IDS"].split(",") == [local_stack.CLIENT, local_stack.CLI_CLIENT]
    # An unconfigured local stack keeps Markets page work off (shared vendor quotas).
    assert be[3]["EPOCH_MI_PAGE_AUTO_REFRESH_ENABLED"] == "0" and be[3]["EPOCH_MI_RATES_PAGE"] == "0"
    runtime = json.loads((backend / "build/bin/config.json").read_text())
    if native_mi_plugin:
        assert runtime["plugins"][0]["config"]["calendar"] == {"enabled": False, "refresh_mode": "paused"}
    else:
        assert runtime == {"plugins": []}
    assert portal[3]["VITE_API_BASE_URL"] == stack.backend_url
    assert portal[3]["VITE_LANGGRAPH_API_URL"] == stack.ai_url
    generated = json.loads((state / "ai/langgraph.json").read_text())
    assert generated["auth"]["disable_studio_auth"] is True
    revision = (operator_revision or "").strip() or "local-" + ai_commit
    assert generated["env"]["EPOCH_COMMENTARY_DEPLOYMENT_REVISION"] == revision
    assert _agent[3]["EPOCH_COMMENTARY_DEPLOYMENT_REVISION"] == revision
    assert generated["env"]["LANGSMITH_LANGGRAPH_GIT_REF"] == "main"
    assert generated["env"]["LANGSMITH_LANGGRAPH_GIT_REF_SHA"] == ai_commit
    assert _agent[3]["LANGSMITH_LANGGRAPH_GIT_REF_SHA"] == ai_commit
    assert generated["env"]["EPOCH_OPENAI_PROVIDER"] == "openai"
    assert generated["env"]["OPENAI_DIRECT_API_KEY"] == "private-direct-fixture"
    assert _agent[3]["OPENAI_DIRECT_API_KEY"] == "private-direct-fixture"
    assert generated["env"]["MODEL_KEY"] == "private"
    assert "OPENAI_DIRECT_API_KEY" not in be[3]
    assert "OPENAI_DIRECT_API_KEY" not in portal[3]
    receipt = json.loads((state / "source-checkouts.json").read_text())
    assert receipt["EpochAI"]["commentary_deployment_revision"] == revision
    assert generated["env"]["EPOCH_CAMPAIGN_SERVER"] == stack.backend_url
    # `epoch login` finds its client from the agent; Backend doesn't need it.
    assert generated["env"]["EPOCH_SIGN_IN_CLIENT_ID"] == local_stack.CLI_CLIENT
    assert _agent[3]["EPOCH_SIGN_IN_CLIENT_ID"] == local_stack.CLI_CLIENT
    assert "EPOCH_SIGN_IN_CLIENT_ID" not in be[3]
    assert generated["graphs"]["epoch-ai"]["path"] == str(ai / "agent.py") + ":agent"
    assert generated["store"]["index"]["embed"] == str(ai / "embeddings.py") + ":embed"
    assert (state / "ai/langgraph.json").stat().st_mode & 0o777 == 0o600
    assert json.loads((ai / "langgraph.json").read_text()) == graph
    assert generated["env"]["EPOCH_FILES_WORKER_URL"] == stack.backend_url
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
    env, mounts, writable = local_stack.up_container_env(values)
    assert writable == []
    assert env["EPOCH_BACKEND_URL"] == "http://host.docker.internal:9000"
    assert env["EPOCH_CAMPAIGN_SERVER"] == "http://host.docker.internal:9000"
    assert env["EPOCH_AI_URL"] == env["LANGGRAPH_API_URL"] == "http://localhost:8000"
    assert "POSTGRES_URI" not in env
    assert mounts == [str(tmp_path / "templates")]
    assert values["EPOCH_BACKEND_URL"] == "http://localhost:9000"  # input untouched
    override = local_stack.up_compose_override(mounts)
    assert '"host.docker.internal:host-gateway"' in override
    assert f'"{tmp_path}/templates:{tmp_path}/templates:ro"' in override


def test_source_access_shares_one_kept_token_and_enables_web_with_exa(tmp_path):
    files, ai = local_stack.source_access(
        tmp_path, "http://localhost:9000", {}, {"EXA_API_KEY": "exa", "KEEP": "1"}
    )
    token = files[local_stack.WORKER_TOKEN]
    assert len(token) >= 32 and ai[local_stack.WORKER_TOKEN] == token
    assert (tmp_path / "document-worker-token").stat().st_mode & 0o077 == 0
    assert ai["EPOCH_DOCUMENT_BACKEND_URL"] == ai["EPOCH_FILES_WORKER_URL"] == "http://localhost:9000"
    assert ai["EPOCH_WEB_RESEARCH_ENABLED"] == "true" and ai["KEEP"] == "1"
    journal = Path(ai["EPOCH_WEB_RESEARCH_JOURNAL"])
    assert journal == (tmp_path / "ai/web-journal") and journal.stat().st_mode & 0o077 == 0
    again, _ = local_stack.source_access(tmp_path, "http://localhost:9000", {}, {})
    assert again[local_stack.WORKER_TOKEN] == token  # kept across restarts


def test_source_access_keeps_operator_settings_but_not_another_files(tmp_path):
    files, ai = local_stack.source_access(
        tmp_path,
        "http://localhost:9000",
        {local_stack.WORKER_TOKEN: "b" * 40},
        {"EXA_API_KEY": "exa", "EPOCH_WEB_RESEARCH_ENABLED": "false",
         "EPOCH_DOCUMENT_BACKEND_URL": "https://api.dev.example",
         "EPOCH_FILES_WORKER_URL": "https://services.dev.example"},
    )
    assert files[local_stack.WORKER_TOKEN] == ai[local_stack.WORKER_TOKEN] == "b" * 40
    assert ai["EPOCH_DOCUMENT_BACKEND_URL"] == ai["EPOCH_FILES_WORKER_URL"] == "http://localhost:9000"
    assert ai["EPOCH_WEB_RESEARCH_ENABLED"] == "false"
    assert "EPOCH_WEB_RESEARCH_JOURNAL" not in ai
    assert not (tmp_path / "document-worker-token").exists()
    _, no_exa = local_stack.source_access(tmp_path, "http://localhost:9000", {}, {})
    assert "EPOCH_WEB_RESEARCH_ENABLED" not in no_exa


def test_up_workers_keep_loopback_through_a_relay(tmp_path, monkeypatch):
    monkeypatch.setattr(local_stack, "PROJECT_ROOT", tmp_path)
    journal = tmp_path / "state/ai/web-journal"
    journal.mkdir(parents=True)
    values = {
        "EPOCH_BACKEND_URL": "http://localhost:9000",
        "EPOCH_CAMPAIGN_SERVER": "http://localhost:9000",
        "EPOCH_AI_URL": "http://localhost:2124",
        "LANGGRAPH_API_URL": "http://localhost:2124",
        "EPOCH_DOCUMENT_BACKEND_URL": "http://localhost:9000",
        "EPOCH_WEB_RESEARCH_JOURNAL": str(journal),
    }
    env, mounts, writable = local_stack.up_container_env(values)
    assert env["EPOCH_DOCUMENT_BACKEND_URL"] == "http://localhost:9000"
    assert mounts == [] and writable == [str(journal)]
    port = local_stack.loopback_port(env)
    assert port == 9000
    assert local_stack.loopback_port({"EPOCH_DOCUMENT_BACKEND_URL": "https://api.example"}) is None
    override = local_stack.up_compose_override(mounts, writable, "epoch-ai-up:abc", port)
    assert f'"{journal}:{journal}"' in override
    assert 'network_mode: "service:langgraph-api"' in override
    assert f'"{local_stack.RELAY}", "9000", "host.docker.internal"' in override
    assert "backend-loopback" not in local_stack.up_compose_override([], [], "x", None)


def test_loopback_relay_carries_both_directions():
    from tools import loopback_forward

    async def scenario():
        async def echo(reader, writer):
            writer.write(b"pong:" + await reader.read(4))
            await writer.drain()
            writer.close()

        # The host side listens on another loopback address with the same port.
        upstream = await asyncio.start_server(echo, "127.0.0.2", 0)
        port = upstream.sockets[0].getsockname()[1]
        async with upstream:
            relay = asyncio.create_task(loopback_forward.main(port, "127.0.0.2"))
            try:
                for _ in range(50):
                    try:
                        reader, writer = await asyncio.open_connection("127.0.0.1", port)
                        break
                    except OSError:
                        await asyncio.sleep(0.02)
                writer.write(b"ping")
                await writer.drain()
                assert await asyncio.wait_for(reader.read(), 5) == b"pong:ping"
                writer.close()
            finally:
                relay.cancel()

    asyncio.run(scenario())


def test_document_access_is_off_unless_configured():
    assert local_stack.document_access({}) == {}
    with pytest.raises(ValueError, match="aws_profile, account_id, bucket"):
        local_stack.document_access({"aws_profile": "dev"})
    env = local_stack.document_access(
        {"aws_profile": "dev", "account_id": "123456789012", "bucket": "epoch-dev-personal"}
    )
    assert env["EPOCH_DOCUMENT_PREPARATION_ENABLED"] == "true"
    assert env["EPOCH_DOCUMENT_BDA_PROFILE_ARN"] == (
        "arn:aws:bedrock:us-east-1:123456789012:data-automation-profile/us.data-automation-v1"
    )
    assert env["AWS_PROFILE"] == "dev" and env["EPOCH_DOCUMENT_AWS_BUCKET"] == "epoch-dev-personal"


def fake_aws(tmp_path, account, region):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)
    script = bin_dir / "aws"
    script.write_text(
        "#!/bin/sh\n"
        'case "$1" in\n'
        f'  sts) echo {account} ;;\n'
        f'  s3api) echo {region} ;;\n'
        "esac\n"
    )
    script.chmod(0o755)
    return {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path)}


def test_document_access_is_checked_before_launch(tmp_path):
    env = local_stack.document_access(
        {"aws_profile": "dev", "account_id": "123456789012", "bucket": "epoch-dev-personal"}
    )
    local_stack.check_document_access({}, {})  # off: nothing to check
    local_stack.check_document_access(env, fake_aws(tmp_path / "ok", "123456789012", "None"))
    with pytest.raises(ValueError, match="not the configured document account"):
        local_stack.check_document_access(env, fake_aws(tmp_path / "acct", "999999999999", "None"))
    with pytest.raises(ValueError, match="must be in us-east-1"):
        local_stack.check_document_access(env, fake_aws(tmp_path / "west", "123456789012", "us-west-2"))
    signed_out = fake_aws(tmp_path / "out", "x", "x")
    (Path(signed_out["PATH"].split(":")[0]) / "aws").write_text("#!/bin/sh\nexit 255\n")
    with pytest.raises(ValueError, match="aws sso login --profile dev"):
        local_stack.check_document_access(env, signed_out)


def test_document_reading_mounts_only_aws_config_and_sign_in_cache(tmp_path):
    assert local_stack.aws_mounts({}, {"HOME": str(tmp_path)}) == []
    mounts = local_stack.aws_mounts(
        {"EPOCH_DOCUMENT_PREPARATION_ENABLED": "true"}, {"HOME": str(tmp_path)}
    )
    assert mounts == [
        (str(tmp_path / ".aws/config"), "/root/.aws/config"),
        (str(tmp_path / ".aws/sso/cache"), "/root/.aws/sso/cache"),
    ]
    override = local_stack.up_compose_override([], [], "img", None, mounts)
    assert f'"{tmp_path}/.aws/sso/cache:/root/.aws/sso/cache:ro"' in override
    assert "credentials" not in override


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


def test_ui_build_runtime_serves_one_bundle_per_commit_through_the_agent_proxy(
    tmp_path, monkeypatch
):
    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / ".env.local").write_text("VITE_SHOWCASE_MODE=\n")
    stack = Stack.load(Path("environments.toml"), "local")
    state = local_stack.private_dir(tmp_path / "state")
    ui_env = {"PATH": "/bin", "VITE_LANGGRAPH_API_URL": stack.ai_url,
              "VITE_API_BASE_URL": stack.backend_url}
    builds = []

    def build(command, cwd, env, stdout, stderr, timeout):
        assert timeout == 180
        builds.append((command, env))
        out = Path(command[command.index("--outDir") + 1])
        out.mkdir(parents=True)
        (out / "index.html").write_text("<html></html>")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(local_stack.subprocess, "run", build)
    commit = "b" * 40
    name, command, cwd, env, ready = local_stack.prepare_ui_build(
        ui, "/bin/corepack", ui_env, stack, 3000, commit, state
    )
    assert name == "UI" and cwd == ui and ready == stack.ui_url
    [(build_command, build_env)] = builds
    assert build_command[:5] == ["/bin/corepack", "pnpm", "exec", "vite", "build"]
    # The bundle talks to the agent through its own origin's proxy (the agent's CORS list
    # does not include this port); the preview server proxies to the real agent.
    assert build_env["VITE_LANGGRAPH_API_URL"] == stack.ui_url.rstrip("/") + "/lg-api"
    assert env["VITE_LANGGRAPH_API_URL"] == stack.ai_url
    assert command[:5] == ["/bin/corepack", "pnpm", "exec", "vite", "preview"]
    bundle = Path(command[command.index("--outDir") + 1])
    assert bundle.name.startswith(commit[:12]) and (bundle / "index.html").is_file()
    assert command[-3:] == ["--port", "3000", "--strictPort"]

    # The same commit and configuration reuse the bundle.
    again = local_stack.prepare_ui_build(ui, "/bin/corepack", ui_env, stack, 3000, commit, state)
    assert again[1] == command and len(builds) == 1
    # A changed env file is a different bundle. A running UI or rollback may
    # still need the previous immutable generation, so building preserves it.
    (ui / ".env.local").write_text("VITE_SHOWCASE_MODE=1\n")
    changed = local_stack.prepare_ui_build(ui, "/bin/corepack", ui_env, stack, 3000, commit, state)
    assert len(builds) == 2 and changed[1] != command
    assert {path.name for path in (state / "ui-builds").iterdir()} == {
        bundle.name, Path(changed[1][changed[1].index("--outDir") + 1]).name
    }


def test_ui_build_failure_is_explicit(tmp_path, monkeypatch):
    stack = Stack.load(Path("environments.toml"), "local")
    state = local_stack.private_dir(tmp_path / "state")
    monkeypatch.setattr(
        local_stack.subprocess, "run",
        lambda command, **_: subprocess.CompletedProcess(command, 1),
    )
    with pytest.raises(ValueError, match="UI build failed"):
        local_stack.prepare_ui_build(
            tmp_path, "/bin/corepack", {"PATH": "/bin"}, stack, 3000, "c" * 40, state
        )
    assert not any((state / "ui-builds").iterdir())
