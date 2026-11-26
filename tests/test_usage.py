"""Usage API integration only: every HTTP request stays on MockTransport."""

import asyncio
import copy
import json
import socket

import httpx
import pytest

from tools import usage


def reading(assistant=10, study_ai=3):
    return {
        "status": "available",
        "week": {"starts_at": "2026-11-23T00:00:00Z", "resets_at": "2026-11-30T00:00:00Z"},
        "meters": [
            {"meter": name, "used": used, "limit": 100, "mode": "notify", "state": "ok", "blocked": False}
            for name, used in (("assistant", assistant), ("study_ai", study_ai))
        ],
        "breakdown": [],
        "measurement": {"unreported": 0, "unrated": 0},
        "pricing": {"rate_version": "exact-test-version", "provisional": False},
    }


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Usage tests must not access the network")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)


def read_api(monkeypatch, handle):
    client = httpx.AsyncClient
    monkeypatch.setattr(usage.httpx, "AsyncClient", lambda **kwargs: client(
        **kwargs, transport=httpx.MockTransport(handle)
    ))
    return asyncio.run(usage.read_usage("https://chosen-api.example", "Bearer private-test-token"))


def test_reads_only_selected_usage_endpoint_and_keeps_pricing(monkeypatch):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={**reading(), "authorization": "do-not-retain"})

    value = read_api(monkeypatch, handle)
    assert [(r.method, str(r.url)) for r in requests] == [
        ("GET", "https://chosen-api.example/api/v1/platform/usage")
    ]
    assert requests[0].headers["Authorization"] == "Bearer private-test-token"
    assert value["status"] == "available"
    assert value["pricing"] == reading()["pricing"]
    assert value["measurement"] == reading()["measurement"]
    assert "private-test-token" not in json.dumps(value)
    assert "do-not-retain" not in json.dumps(value)


@pytest.mark.parametrize("kind", ["503", "401", "404", "timeout", "invalid-json", "invalid-shape"])
def test_failed_usage_read_is_safe_unavailable_evidence(monkeypatch, kind):
    def handle(request):
        if kind == "timeout":
            raise httpx.ReadTimeout("private-test-token", request=request)
        if kind == "invalid-json":
            return httpx.Response(200, text="private-test-token")
        if kind == "invalid-shape":
            return httpx.Response(200, json={"detail": "private-test-token"})
        return httpx.Response(int(kind), text="private-test-token")

    value = read_api(monkeypatch, handle)
    assert value["status"] == "unavailable"
    assert "private-test-token" not in json.dumps(value)
    result = usage.summary(reading(), value, 20)
    assert result["delta"] is None and result["cost_usd"] is None
    assert result["budget_mode"] == "warn"


def test_credit_difference_is_not_a_dollar_estimate_or_scenario_attribution():
    result = usage.summary(reading(), reading(35, 7), 20)
    assert result["delta"] == {"assistant": 25, "study_ai": 4}
    assert result["unit"] == "credits"
    assert result["budget_usd"] == 20 and result["budget_status"] == "unavailable"
    assert result["cost_usd"] is None
    assert "other account activity" in result["scope"]
    assert "USD conversion" in " ".join(result["warnings"])


@pytest.mark.parametrize("change", ["week", "rate", "missing-rate", "decrease", "missing-counter", "duplicate", "boolean", "nan"])
def test_incomparable_usage_never_becomes_zero_or_negative_cost(change):
    before, after = reading(), reading(11, 4)
    if change == "week":
        after["week"]["starts_at"] = "2026-11-30T00:00:00Z"
    elif change == "rate":
        after["pricing"]["rate_version"] = "replacement"
    elif change == "missing-rate":
        after.pop("pricing")
    elif change == "decrease":
        after["meters"][0]["used"] = 1
    elif change == "missing-counter":
        after["meters"][0].pop("used")
    elif change == "duplicate":
        after["meters"][1] = copy.deepcopy(after["meters"][0])
    elif change == "boolean":
        after["meters"][0]["used"] = True
    else:
        after["meters"][0]["used"] = float("nan")
    result = usage.summary(before, after, 20)
    assert result["delta"] is None and result["cost_usd"] is None
    assert any("delta is unavailable" in warning for warning in result["warnings"])


@pytest.mark.parametrize("missing", [None, {"status": "unavailable"}])
def test_no_baseline_is_reported_as_unknown(missing):
    result = usage.summary(missing, reading(), 20)
    assert result["delta"] is None
    assert any("baseline" in warning for warning in result["warnings"])


def test_existing_allowance_and_incomplete_prices_warn_without_enforcement():
    latest = reading(30)
    latest["measurement"] = {"unreported": 1, "unrated": 2}
    latest["pricing"]["provisional"] = True
    latest["meters"][0]["state"] = "reached"
    latest["meters"][0]["blocked"] = True
    result = usage.summary(reading(), latest, 20)
    assert result["budget_mode"] == "warn"
    assert result["cost_usd"] is None
    assert any("incomplete/provisional" in warning for warning in result["warnings"])
    assert any("Weekly account assistant allowance: reached" in warning for warning in result["warnings"])
