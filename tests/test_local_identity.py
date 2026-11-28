"""The normal launcher selects Platform's real, separate identity authority."""

from pathlib import Path
from dataclasses import replace
import subprocess

import httpx
import pytest

from tools import local_stack
from tools.agent_testing import Stack


def inputs():
    backend = {
        "EPOCH_TOKEN_ISSUER": "http://localhost:2024",
        "EPOCH_TOKEN_SIGNING_KEYS": "existing-native-key-material",
        "EPOCH_TOKEN_SERVICE_TOKEN": "existing-service-credential",
    }
    return backend, {"EPOCH_TOKEN_SERVICE_TOKEN": backend["EPOCH_TOKEN_SERVICE_TOKEN"]}


@pytest.mark.parametrize("runtime,host", [("dev", "127.0.0.1"), ("up", "host.docker.internal")])
def test_identity_is_a_separate_real_role_with_only_existing_server_authority(tmp_path, runtime, host):
    backend, agent = inputs()
    backend.update(EPOCH_API_KEYS_TABLE="must-not-use-cloud", EPOCH_SIGN_IN_TABLE="must-not-use-cloud",
                   EPOCH_TOKEN_SIGNING_KEYS_PARAMETER="must-not-read-SSM")
    base = {"PATH": "/bin", "AWS_PROFILE": "must-not-use-cloud", "MODEL_KEY": "must-not-inherit",
            "EPOCH_FILES_LOCAL_ROOT": "/must-not-inherit", "EPOCH_DOCUMENT_WORKER_TOKEN": "must-not-inherit"}
    original = dict(backend)
    service = local_stack.prepare_platform_identity(tmp_path, "/bin/node", base, backend, agent, "p" * 40, runtime)
    name, command, cwd, env, health = service
    assert name == "PlatformIdentity" and command == ["/bin/node", "apps/platform/dist/main.js"] and cwd == tmp_path
    assert env["EPOCH_PLATFORM_ROLE"] == "identity" and env["EPOCH_PLATFORM_REVISION"] == "p" * 40
    assert env["PORT"] == "9021" and env["EPOCH_PLATFORM_SERVICE_HOST"] == host
    assert env["EPOCH_TOKEN_ISSUER"] == backend["EPOCH_TOKEN_ISSUER"]
    assert env["EPOCH_TOKEN_SIGNING_KEYS"] == backend["EPOCH_TOKEN_SIGNING_KEYS"]
    assert env["EPOCH_TOKEN_SERVICE_TOKEN"] == agent["EPOCH_TOKEN_SERVICE_TOKEN"]
    assert env["AWS_CONFIG_FILE"] == env["AWS_SHARED_CREDENTIALS_FILE"] == "/dev/null"
    for key in ["AWS_PROFILE", "MODEL_KEY", "EPOCH_FILES_LOCAL_ROOT", "EPOCH_DOCUMENT_WORKER_TOKEN",
                "EPOCH_API_KEYS_TABLE", "EPOCH_SIGN_IN_TABLE", "EPOCH_TOKEN_SIGNING_KEYS_PARAMETER"]:
        assert key not in env
    assert health == "http://127.0.0.1:9021/health" and backend == original


@pytest.mark.parametrize("missing", ["EPOCH_TOKEN_ISSUER", "EPOCH_TOKEN_SIGNING_KEYS", "EPOCH_TOKEN_SERVICE_TOKEN"])
def test_missing_identity_inputs_stop_before_any_process_or_cloud_fallback(tmp_path, missing):
    backend, agent = inputs()
    backend.pop(missing)
    with pytest.raises(ValueError, match="existing token issuer, signing keys and service credential"):
        local_stack.prepare_platform_identity(tmp_path, "/node", {"AWS_PROFILE": "irrelevant"}, backend, agent, "p", "dev")


@pytest.mark.parametrize("agent", [{}, {"EPOCH_TOKEN_SERVICE_TOKEN": "foreign-sensitive-credential"},
    {"EPOCH_TOKEN_SERVICE_TOKEN": "existing-service-credential", "EPOCH_TOKEN_ISSUER": "https://foreign.example"}])
def test_mismatched_consumer_authority_is_not_replaced_by_a_new_key_or_credential(tmp_path, agent, capsys):
    backend, _ = inputs()
    with pytest.raises(ValueError) as failure:
        local_stack.prepare_platform_identity(tmp_path, "/node", {}, backend, agent, "p", "dev")
    assert "foreign-sensitive-credential" not in str(failure.value)
    assert "existing-native-key-material" not in str(failure.value)
    assert not "".join(capsys.readouterr())


def test_general_and_browser_do_not_inherit_identity_authority(tmp_path, monkeypatch):
    main = tmp_path / "apps/platform/dist/main.js"
    main.parent.mkdir(parents=True);main.touch()
    monkeypatch.setattr(local_stack.subprocess, "run", lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0))
    base = {key: "private-fixture" for key in [*local_stack.IDENTITY_ONLY_SETTINGS, local_stack.TOKEN_SERVICE_CREDENTIAL]}
    general = local_stack.prepare_platform(tmp_path, "/node", base, {}, {}, tmp_path, "p", tmp_path, tmp_path)[3]
    browser = local_stack.portal_environment(base, Stack.load(Path("environments.toml"), "local"))
    for key in base:
        assert key not in general and key not in browser


def test_up_maps_both_platform_public_and_internal_origins_to_the_real_host():
    values = {"EPOCH_BACKEND_URL": "http://localhost:9000", "EPOCH_CAMPAIGN_SERVER": "http://localhost:9000",
              "EPOCH_PLATFORM_SERVER": "http://localhost:9000", "EPOCH_PLATFORM_SERVICE_URL": "http://127.0.0.1:9021",
              "EPOCH_AI_URL": "http://localhost:2124", "LANGGRAPH_API_URL": "http://localhost:2124"}
    mapped, _, _ = local_stack.up_container_env(values)
    assert mapped["EPOCH_PLATFORM_SERVER"] == "http://host.docker.internal:9000"
    assert mapped["EPOCH_PLATFORM_SERVICE_URL"] == "http://host.docker.internal:9021"
    assert values["EPOCH_PLATFORM_SERVICE_URL"] == "http://127.0.0.1:9021"


def test_identity_port_is_owned_and_cannot_collide_with_the_profile():
    stack = Stack.load(Path("environments.toml"), "local")
    assert local_stack.service_ports(stack) == [9000, 2024, 3000, 9010, 9020, 9021]
    stack = replace(stack, ui_url="http://localhost:9021")
    with pytest.raises(ValueError, match="reserved"):
        local_stack.service_ports(stack)


KEY = {"kty": "EC", "crv": "P-256", "kid": "current", "use": "sig", "alg": "ES256", "x": "x", "y": "y"}


def readiness(tmp_path, monkeypatch, *, role="identity", identity_keys=None, native_keys=None, jwks_status=200):
    backend, agent = inputs()
    service = local_stack.prepare_platform_identity(tmp_path, "/node", {}, backend, agent, "p" * 40, "dev")
    calls = []
    def reply(request):
        calls.append((str(request.url), dict(request.headers)))
        assert "authorization" not in request.headers
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok", "service": "epoch-platform", "role": role, "revision": "p" * 40})
        if request.url.port == 9021:
            assert request.url.host == "localhost"  # public hostname, no forged Host override
            return httpx.Response(jwks_status, json={"keys": identity_keys if identity_keys is not None else [KEY]})
        assert request.url.host == "127.0.0.1" and request.url.port == 9010
        return httpx.Response(200, json={"keys": native_keys if native_keys is not None else [KEY]})
    client = httpx.Client
    monkeypatch.setattr(local_stack.httpx, "Client", lambda **kwargs: client(transport=httpx.MockTransport(reply), **kwargs))
    return service, calls


def test_identity_readiness_requires_real_role_and_same_public_keys_not_json_order(tmp_path, monkeypatch):
    previous = {**KEY, "kid": "previous"}
    service, calls = readiness(tmp_path, monkeypatch, identity_keys=[KEY, previous], native_keys=[previous, KEY])
    result = local_stack.verify_platform_identity(service)
    assert result["role"] == "identity" and result["native_jwks_match"] and result["key_count"] == 2
    assert len(calls) == 3 and all(headers["host"] == url.split("/")[2] for url, headers in calls)


@pytest.mark.parametrize("options,diagnostic", [
    ({"role": "general"}, "different role or source"),
    ({"jwks_status": 503}, "signing readiness is unavailable"),
    ({"native_keys": [{**KEY, "x": "different"}]}, "public signing keys differ"),
    ({"identity_keys": []}, "no valid public signing keys"),
    ({"identity_keys": [KEY, KEY]}, "duplicate public signing keys"),
    ({"identity_keys": [{**KEY, "private_key_pem": "must-not-be-published"}]}, "invalid or duplicate"),
])
def test_identity_readiness_fails_closed_on_missing_or_mismatched_authority(tmp_path, monkeypatch, options, diagnostic):
    service, _ = readiness(tmp_path, monkeypatch, **options)
    with pytest.raises(ValueError, match=diagnostic):
        local_stack.verify_platform_identity(service)
