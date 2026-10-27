import asyncio
import json
from argparse import Namespace
from dataclasses import replace
from uuid import uuid4

import httpx
import pytest
from langgraph_sdk.client import LangGraphClient

from tools import agent_testing as app
from tools.evidence import render_report, render_transcript, summarize

CONFIG = app.Stack(
    "https://ai.dev.example", "https://api.dev.example", "https://ui.dev.example"
)
STAMP = "2026-10-25T12:00:00+00:00"


class Server:
    """Protocol fake: no model or vendor calls, even if local tokens are present."""

    def __init__(self):
        self.posts = []
        self.runs = []
        self.state = {"values": {"messages": []}, "tasks": [], "next": []}
        self.lose_response = False
        self.reject_code = None

    def handle(self, request):
        path = request.url.path
        if request.method == "POST":
            data = json.loads(request.content)
            self.posts.append((path, data))
            if path == "/threads":
                return httpx.Response(
                    200, json={"thread_id": data["thread_id"], "status": "idle"}
                )
            if path.endswith("/runs"):
                if self.reject_code:
                    return httpx.Response(
                        self.reject_code, json={"detail": "request rejected"}
                    )
                row = {
                    "run_id": str(uuid4()),
                    "thread_id": path.split("/")[2],
                    "status": "pending",
                    "metadata": data["metadata"],
                    "created_at": STAMP,
                    "updated_at": STAMP,
                }
                self.runs.insert(0, row)
                if self.lose_response:
                    raise httpx.ReadError("accepted, then disconnected")
                return httpx.Response(200, json=row)
        if path.endswith("/state"):
            return httpx.Response(200, json=self.state)
        if path.endswith("/runs"):
            offset = int(request.url.params.get("offset", 0))
            limit = int(request.url.params.get("limit", 10))
            return httpx.Response(200, json=self.runs[offset : offset + limit])
        if "/runs/" in path:
            return httpx.Response(
                200,
                json=next(
                    r for r in self.runs if r["run_id"] == path.rsplit("/", 1)[1]
                ),
            )
        return httpx.Response(
            200, json={"thread_id": path.rsplit("/", 1)[1], "status": "idle"}
        )


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    import socket

    def blocked(*args, **kwargs):
        raise AssertionError("Real network is forbidden in offline tests")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)


@pytest.fixture
def fake_health(monkeypatch):
    async def check(*args):
        return {"checked_at": STAMP, "services": CONFIG.identity()}

    monkeypatch.setattr(app, "check_stack", check)


def exercise(folder, server, action, *, config=CONFIG):
    async def run():
        async with httpx.AsyncClient(
            base_url=config.ai_url,
            transport=httpx.MockTransport(server.handle),
            headers={"Authorization": "Bearer test-only"},
        ) as http:
            return await action(app.Conversation(folder, config, LangGraphClient(http)))

    return asyncio.run(run())


async def ask(c, key="first", message="Compare cash generation"):
    return await c.submit(
        key,
        message=message,
        responses=None,
        owner="analyst",
        scenario="financial-quality",
        bearer="Bearer test-only",
    )


class AuthRunServer(Server):
    """The server echoes auth config alongside the SDK's run envelope."""

    def handle(self, request):
        response = super().handle(request)
        if "/runs" not in request.url.path:
            return response
        value = response.json()
        for run in value if isinstance(value, list) else [value]:
            if "run_id" in run:
                run["kwargs"] = {
                    "config": {"configurable": {"langgraph_auth_user": {
                        "identity": "owner", "authorization": "Bearer secret-run-auth",
                    }}},
                    "input": {"token": 23, "authorization": "financial data"},
                }
        return httpx.Response(response.status_code, json=value)


def test_all_run_evidence_boundaries_redact_server_auth(tmp_path, fake_health):
    server = AuthRunServer()
    first = exercise(tmp_path, server, ask)
    def auth(row):
        return row["kwargs"]["config"]["configurable"]["langgraph_auth_user"]
    assert auth(first) == {"identity": "owner", "authorization": "[REDACTED]"}

    # A cached receipt and history created by an older collector are sanitized
    # when this owner next returns/archives them under its normal writer lock.
    old = json.loads(json.dumps(first))
    auth(old)["authorization"] = "Bearer secret-old-auth"
    receipt_path = tmp_path / "turns/first/run.json"
    app.save(receipt_path, old)
    assert exercise(tmp_path, server, ask) == first
    app.save(tmp_path / "runs.json", {
        "captured_at": STAMP, "complete": True, "error_type": None, "runs": [old],
    })
    status = exercise(tmp_path, server, lambda c: c.status())
    assert auth(status["runs"][0])["authorization"] == "[REDACTED]"
    history = exercise(tmp_path, server, lambda c: c.all_runs())
    assert auth(history["runs"][0])["authorization"] == "[REDACTED]"
    exercise(tmp_path, server, lambda c: c.pull())  # includes refreshed turns/*/run.json
    for path in [receipt_path, tmp_path / "status.json", tmp_path / "runs.json",
                 *tmp_path.glob("run-captures/*.json")]:
        value = json.loads(path.read_text())
        for row in value["runs"] if "runs" in value else [value]:
            assert auth(row) == {"identity": "owner", "authorization": "[REDACTED]"}
            assert row["kwargs"]["input"] == {"token": 23, "authorization": "financial data"}
        assert "secret-" not in path.read_text()
    assert len(server.runs) == 1


def test_lost_create_response_redacts_reconnected_run(tmp_path, fake_health):
    server = AuthRunServer()
    server.lose_response = True
    with pytest.raises(httpx.ReadError):
        exercise(tmp_path, server, ask)
    restored = exercise(tmp_path, server, ask)
    assert restored["kwargs"]["config"]["configurable"]["langgraph_auth_user"]["authorization"] == "[REDACTED]"
    assert "secret-run-auth" not in (tmp_path / "turns/first/run.json").read_text()
    assert len(server.runs) == 1


def test_repeated_cell_does_not_submit_and_followup_keeps_thread(tmp_path, fake_health):
    server = Server()
    first = exercise(tmp_path, server, ask)
    assert exercise(tmp_path, server, ask) == first
    second = exercise(
        tmp_path, server, lambda c: ask(c, "followup", "Explain the change")
    )
    assert first["thread_id"] == second["thread_id"]
    assert len(server.runs) == 2
    payload = next(data for path, data in server.posts if path.endswith("/runs"))
    assert payload["multitask_strategy"] == "reject"
    assert payload["stream_resumable"] is True
    assert payload["stream_subgraphs"] is True
    assert "test-only" not in json.dumps(payload)
    assert "Authorization" not in json.dumps(payload)
    assert "test-only" not in (tmp_path / "attempt.json").read_text()
    assert "test-only" not in (tmp_path / "turns/first/request.json").read_text()


def test_changed_prompt_under_existing_key_is_rejected(tmp_path, fake_health):
    server = Server()
    exercise(tmp_path, server, ask)
    with pytest.raises(ValueError, match="different request"):
        exercise(tmp_path, server, lambda c: ask(c, message="Another task"))
    assert len(server.runs) == 1


def test_lost_submit_response_is_recovered_without_another_run(tmp_path, fake_health):
    server = Server()
    server.lose_response = True
    with pytest.raises(httpx.ReadError):
        exercise(tmp_path, server, ask)
    server.lose_response = False
    with pytest.raises(RuntimeError, match="unconfirmed"):
        exercise(tmp_path, server, lambda c: ask(c, "next"))
    recovered = exercise(tmp_path, server, ask)
    assert recovered["run_id"] == server.runs[0]["run_id"]
    assert len(server.runs) == 1


def test_unconfirmed_submission_does_not_retry(tmp_path, fake_health):
    server = Server()
    exercise(tmp_path, server, ask)
    (tmp_path / "turns/first/run.json").unlink()
    server.runs.clear()
    with pytest.raises(RuntimeError, match="unconfirmed"):
        exercise(tmp_path, server, ask)
    assert not server.runs


@pytest.mark.parametrize("status", [400, 401, 403, 409, 422, 429])
def test_definitive_rejection_retains_evidence_but_does_not_block_new_turn(
    tmp_path, fake_health, status
):
    server = Server()
    server.reject_code = status
    with pytest.raises(httpx.HTTPStatusError):
        exercise(tmp_path, server, ask)
    assert (
        json.loads((tmp_path / "turns/first/rejected.json").read_text())["status_code"]
        == status
    )
    with pytest.raises(RuntimeError, match="server rejected"):
        exercise(tmp_path, server, ask)
    server.reject_code = None
    exercise(tmp_path, server, lambda c: ask(c, "corrected"))
    assert len(server.runs) == 1


@pytest.mark.parametrize("status", [408, 500, 502, 504])
def test_uncertain_http_failure_still_prevents_resubmit(tmp_path, fake_health, status):
    server = Server()
    server.reject_code = status
    with pytest.raises(httpx.HTTPStatusError):
        exercise(tmp_path, server, ask)
    assert not (tmp_path / "turns/first/rejected.json").exists()
    server.reject_code = None
    with pytest.raises(RuntimeError, match="unconfirmed"):
        exercise(tmp_path, server, lambda c: ask(c, "next"))
    assert not server.runs


def test_nested_interrupts_and_explicit_distinct_answers(tmp_path, fake_health):
    server = Server()
    exercise(tmp_path, server, ask)
    server.state["tasks"] = [
        {"interrupts": [{"id": "one", "value": {"question": "Period?"}}]},
        {
            "state": {
                "tasks": [
                    {"interrupts": [{"id": "two", "value": {"question": "Costs?"}}]}
                ]
            }
        },
    ]
    with pytest.raises(RuntimeError, match="pending"):
        exercise(tmp_path, server, lambda c: ask(c, "followup"))

    async def answer(c):
        return await c.submit(
            "reply",
            message=None,
            responses={"one": {"answer": "2020–2025"}, "two": {"answer": "5bps"}},
            owner="analyst",
            scenario=None,
            bearer="Bearer test-only",
        )

    exercise(tmp_path, server, answer)
    payload = [data for path, data in server.posts if path.endswith("/runs")][-1]
    assert payload["command"]["resume"] == {
        "one": {"answer": "2020–2025"},
        "two": {"answer": "5bps"},
    }
    status = exercise(tmp_path, server, lambda c: c.status())
    assert set(status["pending"]) == {
        "one",
        "two",
    }  # next=[] does not erase an interrupt


def test_rejects_stale_interrupt_answer(tmp_path, fake_health):
    server = Server()
    exercise(tmp_path, server, ask)

    async def answer(c):
        return await c.submit(
            "reply",
            message=None,
            responses={"old": {}},
            owner="analyst",
            scenario=None,
            bearer="test",
        )

    with pytest.raises(ValueError, match="pending"):
        exercise(tmp_path, server, answer)
    assert len(server.runs) == 1


def test_stack_change_is_visible(tmp_path, fake_health):
    server = Server()
    exercise(tmp_path, server, ask)
    record = json.loads((tmp_path / "attempt.json").read_text())
    record["services"]["ai_url"] = "https://different.example"
    app.save(tmp_path / "attempt.json", record)
    with pytest.raises(ValueError, match="different stack"):
        exercise(tmp_path, server, lambda c: c.status())
    with pytest.raises(ValueError, match="different stack"):
        exercise(tmp_path, server, ask)


def test_index_reports_pending_input_and_snapshot_time(tmp_path, fake_health):
    folder = tmp_path / "case"
    server = Server()
    exercise(folder, server, ask)
    server.state["interrupts"] = [{"id": "pending", "value": {"question": "Period?"}}]
    state = exercise(folder, server, lambda c: c.status())
    table = app.index(tmp_path)
    assert "needs_input" in table
    assert state["checked_at"] in table


def test_pull_refreshes_runs_and_writes_full_transcript(tmp_path, fake_health):
    server = Server()
    exercise(tmp_path, server, ask)
    server.runs[0].update(status="success", updated_at="2026-10-25T12:00:12+00:00")
    server.state["values"]["messages"] = [
        {"type": "human", "content": "Question"},
        {"type": "ai", "content": "A" * 10000},
    ]
    result = exercise(tmp_path, server, lambda c: c.pull())
    assert "A" * 10000 in (tmp_path / "conversation.md").read_text()
    assert result["metrics"]["runs"][0]["server_elapsed_seconds"] == 12
    assert result["metrics"]["observed_model_calls"] == 0
    assert result["metrics"]["input_tokens"] is None
    assert result["metrics"]["cost_usd"] is None


def test_per_attempt_records_and_no_automatic_review(tmp_path, fake_health):
    for name in ("a", "b"):
        exercise(tmp_path / name, Server(), ask)
    result = app.index(tmp_path)
    assert result.count("not reviewed") == 2
    assert (tmp_path / "a/attempt.json").read_text() != (
        tmp_path / "b/attempt.json"
    ).read_text()


def test_single_writer_lock(tmp_path):
    with app.writer(tmp_path):
        with pytest.raises(BlockingIOError):
            with app.writer(tmp_path):
                pytest.fail("second writer acquired the same attempt")


def test_attach_existing_ui_thread_is_read_only(tmp_path):
    server = Server()
    tid = str(uuid4())
    result = exercise(
        tmp_path,
        server,
        lambda c: c.attach(tid, owner="researcher", scenario="airline-fuel"),
    )
    assert result["thread_id"] == tid
    assert server.posts == []
    assert json.loads((tmp_path / "attempt.json").read_text())["attached"] is True
    with pytest.raises(ValueError, match="another thread"):
        exercise(
            tmp_path,
            server,
            lambda c: c.attach(str(uuid4()), owner="researcher", scenario=None),
        )


def test_pull_collects_every_run_for_attached_thread(tmp_path):
    server = Server()
    tid = str(uuid4())
    exercise(
        tmp_path, server, lambda c: c.attach(tid, owner="researcher", scenario=None)
    )
    server.runs = [
        {
            "run_id": str(uuid4()),
            "thread_id": tid,
            "status": "success",
            "created_at": STAMP,
            "updated_at": "2026-10-25T12:00:04+00:00",
            "metadata": {},
        }
        for _ in range(205)
    ]
    result = exercise(tmp_path, server, lambda c: c.pull())
    assert len(result["metrics"]["runs"]) == 205
    assert result["capture_status"] == "complete"
    assert (tmp_path / "report.md").exists()


def test_attached_unknown_submission_cannot_adopt_an_old_readable_key(
    tmp_path, fake_health
):
    server = Server()
    tid = str(uuid4())
    exercise(
        tmp_path, server, lambda c: c.attach(tid, owner="researcher", scenario=None)
    )
    server.runs = [
        {
            "run_id": str(uuid4()),
            "thread_id": tid,
            "status": "success",
            "metadata": {
                "agent_testing_key": "first",
                "agent_testing_submission_id": "old-submission",
            },
            "created_at": STAMP,
            "updated_at": STAMP,
        }
    ]
    normal = server.handle

    def uncertain(request):
        if request.method == "POST" and request.url.path.endswith("/runs"):
            raise httpx.ReadError("unknown submission outcome")
        return normal(request)

    server.handle = uncertain
    with pytest.raises(httpx.ReadError):
        exercise(tmp_path, server, ask)
    server.handle = normal
    with pytest.raises(RuntimeError, match="unconfirmed"):
        exercise(tmp_path, server, ask)
    assert not (tmp_path / "turns/first/run.json").exists()
    assert len(server.runs) == 1


def test_history_page_failure_preserves_current_partial_and_old_capture(tmp_path):
    server = Server()
    tid = str(uuid4())
    exercise(
        tmp_path, server, lambda c: c.attach(tid, owner="researcher", scenario=None)
    )
    server.runs = [
        {
            "run_id": str(uuid4()),
            "thread_id": tid,
            "status": "success",
            "metadata": {},
            "created_at": STAMP,
            "updated_at": STAMP,
        }
        for _ in range(101)
    ]
    original = {"captured_at": STAMP, "complete": True, "runs": []}
    app.save(tmp_path / "runs.json", original)
    normal = server.handle

    def failure(request):
        if (
            request.url.path.endswith("/runs")
            and request.url.params.get("offset") == "100"
        ):
            return httpx.Response(503, json={"detail": "later page unavailable"})
        return normal(request)

    server.handle = failure
    result = exercise(tmp_path, server, lambda c: c.pull())
    receipt = json.loads((tmp_path / "runs.json").read_text())
    assert len(receipt["runs"]) == 100
    assert receipt["complete"] is False
    assert receipt["error_type"] == "InternalServerError"
    assert result["capture_status"] == "partial"
    assert result["metrics"]["run_coverage"]["complete"] is False
    assert original in [
        json.loads(p.read_text()) for p in (tmp_path / "run-captures").glob("*.json")
    ]


def test_requested_missing_trace_config_is_explicit_partial(
    tmp_path, fake_health, monkeypatch
):
    for name in ("LANGSMITH_API_KEY", "LANGSMITH_PROJECT", "LANGSMITH_ENDPOINT"):
        monkeypatch.delenv(name, raising=False)
    server = Server()
    exercise(tmp_path, server, ask)
    result = exercise(tmp_path, server, lambda c: c.pull(traces=True))
    assert result["sources"]["thread"]["status"] == "available"
    assert result["sources"]["langsmith"]["status"] == "unavailable"
    assert "LANGSMITH_API_KEY" in result["sources"]["langsmith"]["reason"]
    assert result["capture_status"] == "partial"


def test_backend_collection_uses_only_accepted_state_ids(
    tmp_path, fake_health, monkeypatch
):
    from tools import executions

    server = Server()
    exercise(tmp_path, server, ask)
    server.state["values"].update(
        execution_ids=["accepted-job"],
        messages=[{"type": "ai", "content": "unrelated uuid: do-not-use"}],
    )
    captured = []

    async def collect(folder, url, bearer, ids):
        captured.extend(ids)
        return {"executions": [{"execution_id": i, "status": "complete"} for i in ids]}

    monkeypatch.setattr(executions, "collect", collect)
    result = exercise(
        tmp_path, server, lambda c: c.pull(executions=True, bearer="Bearer test-only")
    )
    assert captured == ["accepted-job"]
    assert result["sources"]["backend"]["status"] == "available"


def test_other_sources_are_attempted_when_graph_fetch_fails(
    tmp_path, fake_health, monkeypatch
):
    from tools import executions, traces

    server = Server()
    exercise(tmp_path, server, ask)
    handle = server.handle

    def failed(request):
        if request.method == "GET":
            return httpx.Response(503, json={"detail": "unavailable"})
        return handle(request)

    server.handle = failed
    monkeypatch.setenv("LANGSMITH_ENDPOINT", "https://trace.example")
    monkeypatch.setenv("LANGSMITH_PROJECT", "dev")
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-only")
    calls = []

    def trace_collect(*args, **kwargs):
        calls.append("traces")
        return {"runs": [], "coverage": {"complete": False}}

    async def backend_collect(*args, **kwargs):
        calls.append("backend")
        return {"executions": [{"execution_id": "known-id", "status": "partial"}]}

    monkeypatch.setattr(traces, "collect", trace_collect)
    monkeypatch.setattr(executions, "collect", backend_collect)
    result = exercise(
        tmp_path,
        server,
        lambda c: c.pull(
            traces=True, execution_ids=["known-id"], bearer="Bearer test-only"
        ),
    )
    assert calls == ["traces", "backend"]
    assert result["sources"]["thread"]["status"] == "unavailable"
    assert result["capture_status"] == "partial"


def test_review_preserves_history_and_never_changes_run_state(tmp_path, fake_health):
    server = Server()
    exercise(tmp_path / "case", server, ask)
    app.record_review(
        tmp_path / "case",
        reviewer="reviewer-a",
        outcome="partial",
        finding="Missing comparison",
        evidence=["conversation.md"],
    )
    app.record_review(
        tmp_path / "case",
        reviewer="reviewer-b",
        outcome="fail",
        finding="Wrong calculation",
        evidence=["thread.json"],
    )
    assert len(list((tmp_path / "case/reviews").glob("*.json"))) == 2
    assert json.loads((tmp_path / "case/review.json").read_text())["outcome"] == "fail"
    assert len(server.runs) == 1


def test_usage_deduplicates_cumulative_messages_and_excludes_other_channels(tmp_path):
    p = tmp_path / "streams/run/events.jsonl"
    p.parent.mkdir(parents=True)

    def event(kind, mid, total):
        return {
            "event": kind,
            "data": [
                {
                    "id": mid,
                    "name": "author",
                    "usage_metadata": {
                        "input_tokens": 10,
                        "output_tokens": total - 10,
                        "total_tokens": total,
                    },
                },
                {},
            ],
        }

    rows = [
        event("messages", "a", 11),
        event("messages", "a", 14),
        event("values", "provider-a", 14),
        event("messages", "a", 14),
    ]
    p.write_text("\n".join(map(json.dumps, rows)))
    result = summarize(tmp_path)
    assert result["observed_model_calls"] == 1
    assert result["input_tokens"] == 10
    assert result["output_tokens"] == 4


def test_config_does_not_accept_credentials_in_url(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(
        '[dev]\nai_url="https://name:secret@ai.example"\nbackend_url="https://b.example"\nui_url="https://u.example"'
    )
    with pytest.raises(ValueError, match="credentials"):
        app.Stack.load(p)


def test_profiles_default_to_dev_and_require_an_explicit_existing_profile(tmp_path):
    config = app.Stack.load(app.ROOT / "environments.toml")
    assert config.environment == "dev"
    assert config.backend_url == "https://api.dev.epoch.trade"
    custom = tmp_path / "custom.toml"
    custom.write_text(
        '[local]\nai_url="http://127.0.0.1:12048"\nbackend_url="http://127.0.0.1:19048"\nui_url="http://localhost:3001"'
    )
    local = app.Stack.load(custom, "local")
    assert local.backend_url == "http://127.0.0.1:19048"
    with pytest.raises(ValueError, match="No \\[dev\\] profile"):
        app.Stack.load(custom)
    with pytest.raises(ValueError, match="Choose local, dev or prod"):
        app.Stack.load(custom, "typo")


@pytest.mark.parametrize("environment", ["dev", "prod"])
def test_hosted_profiles_require_https(tmp_path, environment):
    config = tmp_path / "config.toml"
    config.write_text(
        f'[{environment}]\nai_url="http://host.example"\nbackend_url="https://b.example"\nui_url="https://u.example"'
    )
    with pytest.raises(ValueError, match="HTTPS"):
        app.Stack.load(config, environment)


@pytest.mark.parametrize("environment", ["local", "dev", "prod"])
def test_cli_routes_only_to_selected_profile(tmp_path, monkeypatch, environment):
    config_path = app.ROOT / "environments.toml"
    config = app.Stack.load(config_path, environment)
    requests = []
    server = Server()
    allowed = {
        httpx.URL(url).netloc
        for url in (config.ai_url, config.backend_url, config.ui_url)
    }

    def handle(request):
        requests.append(request)
        assert request.url.netloc in allowed
        if request.url.path == "/health/custom":
            assert request.url.netloc == httpx.URL(config.ai_url).netloc
            return httpx.Response(
                200,
                json={
                    "status": "healthy",
                    "build_study": {"ready": True, "backend_url": config.backend_url},
                },
            )
        if request.url.path == "/api/v1/health":
            assert request.url.netloc == httpx.URL(config.backend_url).netloc
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/api/v1/execution-extensions/commentary-policy":
            assert request.url.netloc == httpx.URL(config.ai_url).netloc
            assert request.headers["Authorization"] == "Bearer test-only"
            return httpx.Response(200, json={"model_policy": "execution_commentary.v1"})
        if str(request.url).rstrip("/") == config.ui_url.rstrip("/"):
            assert "authorization" not in request.headers
            return httpx.Response(200, text="UI")
        assert request.url.netloc == httpx.URL(config.ai_url).netloc
        assert request.headers["Authorization"] == "Bearer test-only"
        return server.handle(request)

    real = httpx.AsyncClient
    monkeypatch.setattr(
        app.httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handle), **kw),
    )
    monkeypatch.setenv("EPOCH_TOKEN", "test-only")
    args = Namespace(
        action="ask",
        env=environment,
        config=config_path,
        artifacts=tmp_path,
        attempt="case",
        key="opening",
        message="Research question",
        owner="researcher",
        scenario=None,
    )
    result = asyncio.run(app.run(args))
    assert result["metadata"]["agent_testing_environment"] == environment
    record = json.loads((tmp_path / "case/attempt.json").read_text())
    assert record["environment"] == environment
    assert record["services"] == config.identity()
    assert {request.url.netloc for request in requests} == allowed
    assert f"| {environment} |" in app.index(tmp_path)
    assert f"Environment: {environment}." in render_report(tmp_path / "case")


@pytest.mark.parametrize("action", ["status", "cached", "new_turn", "attach"])
def test_profile_switch_is_rejected_before_any_network_call(
    tmp_path, fake_health, monkeypatch, action
):
    server = Server()
    first = exercise(tmp_path, server, ask)

    def forbidden(request):
        raise AssertionError("Wrong-profile network request")

    async def no_readiness(*args):
        raise AssertionError("Wrong-profile readiness request")

    server.handle = forbidden
    monkeypatch.setattr(app, "check_stack", no_readiness)
    changed = replace(
        CONFIG, environment="prod"
    )  # even identical URLs must not silently relabel an attempt
    actions = {
        "status": lambda c: c.status(),
        "cached": ask,
        "new_turn": lambda c: ask(c, "next"),
        "attach": lambda c: c.attach(
            first["thread_id"], owner="researcher", scenario=None
        ),
    }
    with pytest.raises(ValueError, match="belongs to dev"):
        exercise(tmp_path, server, actions[action], config=changed)


def test_existing_dev_receipts_remain_readable(tmp_path, fake_health):
    server = Server()
    exercise(tmp_path, server, ask)
    path = tmp_path / "attempt.json"
    record = json.loads(path.read_text())
    record.pop("environment")
    app.save(path, record)
    assert exercise(tmp_path, server, lambda c: c.status())["environment"] == "dev"


def test_command_line_default_is_dev(monkeypatch, capsys):
    async def inspect(args):
        return {"environment": args.env}

    monkeypatch.setattr(app, "run", inspect)
    monkeypatch.setenv("EPOCH_ENV", "prod")
    monkeypatch.setattr("sys.argv", ["agent_testing", "check"])
    app.main()
    assert json.loads(capsys.readouterr().out)["environment"] == "dev"


def test_transcript_excludes_non_public_reasoning_blocks():
    result = render_transcript(
        {
            "values": {
                "messages": [
                    {
                        "type": "ai",
                        "content": [
                            {"type": "thinking", "thinking": "private"},
                            {"type": "text", "text": "Answer"},
                        ],
                    }
                ]
            }
        }
    )
    assert "Answer" in result
    assert "private" not in result


def test_resumable_stream_keeps_cursor_and_does_not_cancel_server(
    tmp_path, fake_health
):
    server = Server()
    exercise(tmp_path, server, ask)
    requests = []
    handle = server.handle

    def stream(request):
        if request.url.path.endswith("/stream"):
            requests.append(request)
            body = (
                "id: 7\nevent: messages\ndata: "
                + json.dumps(
                    [
                        {
                            "id": "model1",
                            "usage_metadata": {
                                "input_tokens": 30,
                                "output_tokens": 5,
                                "total_tokens": 35,
                            },
                        },
                        {},
                    ]
                )
                + "\n\n"
            )
            return httpx.Response(
                200, content=body, headers={"content-type": "text/event-stream"}
            )
        return handle(request)

    server.handle = stream
    exercise(tmp_path, server, lambda c: c.pull(follow=True))
    exercise(tmp_path, server, lambda c: c.pull(follow=True))
    assert requests[1].headers["last-event-id"] == "7"
    assert all(r.url.params.get("cancel_on_disconnect") != "true" for r in requests)
    assert summarize(tmp_path)["observed_model_calls"] == 1


@pytest.mark.parametrize(
    "ready,backend,status,commentary_status,passes",
    [
        (True, CONFIG.backend_url, "healthy", 200, True),
        (False, CONFIG.backend_url, "healthy", 200, False),
        (True, "https://wrong.example", "healthy", 200, False),
        (True, CONFIG.backend_url, "misconfigured", 200, False),
        (True, CONFIG.backend_url, "healthy", 503, False),
    ],
)
def test_readiness_checks_service_identity_and_ui_has_no_token(
    monkeypatch, ready, backend, status, commentary_status, passes
):
    captured = []

    def handle(request):
        captured.append(request)
        if request.url.path == "/api/v1/execution-extensions/commentary-policy":
            assert request.url.host == "ai.dev.example"
            assert request.headers["Authorization"] == "Bearer test-only"
            return httpx.Response(commentary_status, json={"model_policy": "execution_commentary.v1"})
        if request.url.host == "ai.dev.example":
            return httpx.Response(
                200,
                json={
                    "status": status,
                    "build_study": {"ready": ready, "backend_url": backend},
                },
            )
        if request.url.host == "api.dev.example":
            return httpx.Response(200, json={"status": "ok", "git_sha": "backend-sha"})
        return httpx.Response(200, text="<html>UI</html>")

    real = httpx.AsyncClient
    monkeypatch.setattr(
        app.httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handle), **kw),
    )
    if passes:
        result = asyncio.run(app.check_stack(CONFIG, "Bearer test-only"))
        assert result["backend"]["git_sha"] == "backend-sha"
        assert result["ui"]["revision"] is None
    else:
        with pytest.raises(RuntimeError):
            asyncio.run(app.check_stack(CONFIG, "Bearer test-only"))
    assert (
        "authorization"
        not in next(r for r in captured if r.url.host == "ui.dev.example").headers
    )
