"""A Portal's sign-in pool: published runtime config first, else the profile's own."""

import httpx
import pytest

from tools import local_auth
from tools.agent_testing import Stack

DEV = dict(
    ai_url="https://agent.example.test",
    backend_url="https://api.example.test",
    ui_url="https://app.example.test",
    environment="dev",
)
DECLARED = local_auth.UserPool("us-west-1_Declared1", "declaredclient")


def published(pool_id: str, client_id: str) -> httpx.Response:
    request = httpx.Request("GET", "https://app.example.test/epoch-runtime-config.json")
    return httpx.Response(
        200,
        request=request,
        json={
            "amplifyOutputs": {"auth": {"user_pool_id": pool_id, "user_pool_client_id": client_id}},
            "backendUrl": DEV["backend_url"],
            "agentUrl": DEV["ai_url"],
            "assistantId": "epoch-ai",
        },
    )


def missing(*_args: object, **_kwargs: object) -> httpx.Response:
    request = httpx.Request("GET", "https://app.example.test/epoch-runtime-config.json")
    return httpx.Response(404, request=request)


def test_a_dev_build_signs_in_with_the_pool_its_profile_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "get", missing)
    stack = Stack(**DEV, user_pool_id=DECLARED.pool_id, user_pool_client_id=DECLARED.client_id)
    assert stack.user_pool() == DECLARED


def test_without_published_config_or_a_named_pool_it_says_what_to_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "get", missing)
    with pytest.raises(ValueError, match="name it in the profile"):
        Stack(**DEV).user_pool()


def test_published_config_wins_and_must_match_a_named_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "get", lambda *a, **k: published("us-west-1_Published", "publishedclient"))
    assert Stack(**DEV).user_pool() == local_auth.UserPool("us-west-1_Published", "publishedclient")
    with pytest.raises(ValueError, match="different sign-in pool"):
        Stack(**DEV, user_pool_id=DECLARED.pool_id, user_pool_client_id=DECLARED.client_id).user_pool()
