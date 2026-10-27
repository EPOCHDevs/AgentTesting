import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from langsmith.schemas import Run

from tools.traces import TraceConfig, collect

CONFIG = TraceConfig("https://traces.example", "shared-dev", "test-key-never-save")
PROJECT = uuid4()
THREAD = str(uuid4())
START = datetime(2026, 10, 25, 12, tzinfo=UTC)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    import socket

    def blocked(*args, **kwargs):
        raise AssertionError("Real network is forbidden in offline tests")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)


def run(*, parent=None, trace=None, thread=THREAD, **fields):
    rid = fields.pop("id", uuid4())
    values = dict(
        id=rid,
        trace_id=trace or rid,
        parent_run_id=parent,
        session_id=PROJECT,
        name="research" if parent is None else "model",
        run_type="chain" if parent is None else "llm",
        start_time=START,
        end_time=START + timedelta(seconds=4),
        extra={"metadata": {"thread_id": thread}},
    )
    values.update(fields)
    return Run(**values)


class Client:
    def __init__(self, roots, traces):
        self.roots, self.traces, self.calls = roots, traces, []

    def read_project(self, **kwargs):
        self.calls.append(("project", kwargs))
        return SimpleNamespace(id=PROJECT)

    def list_runs(self, **kwargs):
        self.calls.append(("runs", kwargs))
        rows = self.roots if kwargs.get("is_root") else self.traces[kwargs["trace_id"]]
        for row in rows:
            if isinstance(row, Exception):
                raise row
            yield row


def test_full_tree_keeps_sdk_cost_time_errors_and_scopes_every_query(tmp_path):
    root = run(total_cost=Decimal("0.123456789"), total_tokens=36)
    child = run(
        parent=root.id,
        trace=root.trace_id,
        thread="nested-thread",
        total_cost=Decimal("0.0234"),
        prompt_cost=None,
        prompt_tokens=24,
        completion_tokens=12,
        total_tokens=36,
        first_token_time=START + timedelta(seconds=1),
        error="tool failed",
        inputs={"question": "compare"},
        outputs={"success": False},
    )
    client = Client([root], {str(root.trace_id): [child, root, child]})
    result = collect(
        tmp_path, THREAD, config=CONFIG, client=client, expected_run_ids=[str(root.id)]
    )
    assert result["coverage"]["complete"]
    assert len(result["runs"]) == 2
    assert client.calls[0] == ("project", {"project_name": "shared-dev"})
    calls = [args for name, args in client.calls if name == "runs"]
    assert all(args["project_id"] == str(PROJECT) for args in calls)
    assert calls[0]["is_root"] is True
    assert json.dumps(THREAD) in calls[0]["filter"]
    assert calls[1]["trace_id"] == str(root.trace_id)
    assert all("limit" not in args for args in calls)  # exhaust SDK cursor pagination
    saved = json.loads((tmp_path / "traces/langsmith.json").read_text())
    by_id = {row["id"]: row for row in saved["runs"]}
    assert by_id[str(root.id)]["total_cost"] == "0.123456789"
    assert by_id[str(child.id)]["prompt_cost"] is None
    assert by_id[str(child.id)]["error"] == "tool failed"
    assert by_id[str(child.id)]["first_token_time"]
    assert by_id[str(child.id)]["inputs"] == {"question": "compare"}
    assert by_id[str(child.id)]["outputs"] == {"success": False}
    assert CONFIG.api_key not in (tmp_path / "traces/langsmith.json").read_text()


def test_trace_redacts_only_promoted_auth_headers_and_preserves_sdk_payloads(tmp_path):
    payload = {"token": 12, "authorization": "financial authorization measure",
               "configurable": {"Authorization": "tool payload, not runtime configuration"}}
    root = run(
        extra={"metadata": {
            "thread_id": THREAD, "source_id": "source", "model_choice": "selected",
            "authorization": "Bearer secret-lowercase", "Authorization": "Bearer secret-header",
            "x-refresh-token": "secret-renewal-lowercase", "X-Refresh-Token": "secret-renewal-header",
        }},
        inputs=payload, outputs={"rows": [payload]}, total_tokens=17,
        total_cost=Decimal("0.000123456789"),
    )
    before = root.model_dump(mode="json")
    result = collect(tmp_path, THREAD, config=CONFIG,
                     client=Client([root], {str(root.trace_id): [root]}))
    saved = result["runs"][0].model_dump(mode="json")
    expected = root.model_dump(mode="json")
    for field in ("authorization", "Authorization", "x-refresh-token", "X-Refresh-Token"):
        expected["extra"]["metadata"][field] = "[REDACTED]"
    assert saved == expected
    assert root.model_dump(mode="json") == before
    for path in [tmp_path / "traces/langsmith.json", *tmp_path.glob("traces/captures/*.json")]:
        assert json.loads(path.read_text())["runs"] == [expected]
        assert "secret-" not in path.read_text()


def test_rejects_wrong_thread_project_trace_and_disconnected_spans(tmp_path):
    root = run()
    wrong_thread = run(thread="unrelated")
    wrong_project = run(session_id=uuid4())
    not_root = run(parent=uuid4())
    child = run(parent=root.id, trace=root.trace_id)
    wrong_trace = run(parent=root.id)
    orphan = run(parent=uuid4(), trace=root.trace_id)
    foreign_project_child = run(parent=root.id, trace=root.trace_id, session_id=uuid4())
    foreign_root = run(trace=root.trace_id)
    client = Client(
        [root, wrong_thread, wrong_project, not_root],
        {
            str(root.trace_id): [
                root,
                child,
                wrong_trace,
                orphan,
                foreign_project_child,
                foreign_root,
            ]
        },
    )
    result = collect(tmp_path, THREAD, config=CONFIG, client=client)
    assert {row.id for row in result["runs"]} == {root.id, child.id}
    assert not result["coverage"]["complete"]
    assert {row["reason"] for row in result["coverage"]["rejected_runs"]} == {
        "thread_metadata_mismatch",
        "project_mismatch",
        "root_has_parent",
        "trace_mismatch",
        "missing_or_untrusted_parent",
        "unmatched_root",
    }
    assert len(result["coverage"]["trace_queries"]) == 1


def test_parent_resolution_is_independent_of_page_order_and_rejects_cycles(tmp_path):
    root = run()
    parent = run(parent=root.id, trace=root.trace_id)
    child = run(parent=parent.id, trace=root.trace_id)
    cycle_a, cycle_b = uuid4(), uuid4()
    cycles = [
        run(id=cycle_a, parent=cycle_b, trace=root.trace_id),
        run(id=cycle_b, parent=cycle_a, trace=root.trace_id),
    ]
    result = collect(
        tmp_path,
        THREAD,
        config=CONFIG,
        client=Client([root], {str(root.trace_id): [child, *cycles, parent, root]}),
    )
    assert {row.id for row in result["runs"]} == {root.id, parent.id, child.id}
    assert len(result["coverage"]["rejected_runs"]) == 2


def test_partial_root_and_trace_pagination_preserves_observations(tmp_path):
    root = run()
    child = run(parent=root.id, trace=root.trace_id)
    client = Client(
        [root, RuntimeError("token=secret-detail")],
        {str(root.trace_id): [root, child, TimeoutError("secret-detail")]},
    )
    result = collect(tmp_path, THREAD, config=CONFIG, client=client)
    assert len(result["runs"]) == 2
    coverage = result["coverage"]
    assert not coverage["complete"]
    assert not coverage["root_query_complete"]
    assert coverage["root_query_error_type"] == "RuntimeError"
    assert coverage["trace_queries"][0]["error_type"] == "TimeoutError"
    assert "secret-detail" not in (tmp_path / "traces/langsmith.json").read_text()


def test_empty_unfinished_and_unmatched_runs_remain_explicit(tmp_path):
    empty = collect(tmp_path, THREAD, config=CONFIG, client=Client([], {}))
    assert empty["coverage"]["incomplete_reasons"] == ["no_matching_roots"]
    root = run(end_time=None, total_cost=None)
    result = collect(
        tmp_path,
        THREAD,
        config=CONFIG,
        client=Client([root], {str(root.trace_id): [root]}),
        expected_run_ids=["missing"],
    )
    assert result["runs"][0].total_cost is None
    assert result["coverage"]["open_run_ids"] == [str(root.id)]
    assert result["coverage"]["unmatched_expected_run_ids"] == ["missing"]
    assert not result["coverage"]["complete"]
    assert len(list((tmp_path / "traces/captures").glob("*.json"))) == 2


def test_expected_receipt_can_match_explicit_root_metadata(tmp_path):
    root = run(extra={"metadata": {"thread_id": THREAD, "run_id": "graph-run"}})
    result = collect(
        tmp_path,
        THREAD,
        config=CONFIG,
        client=Client([root], {str(root.trace_id): [root]}),
        expected_run_ids=["graph-run"],
    )
    assert result["coverage"]["complete"]
    assert result["root_run_ids"] == [str(root.id)]


def test_empty_trace_query_does_not_claim_complete_tree(tmp_path):
    root = run()
    result = collect(
        tmp_path, THREAD, config=CONFIG, client=Client([root], {str(root.trace_id): []})
    )
    assert not result["coverage"]["complete"]
    assert result["coverage"]["incomplete_reasons"] == [
        "trace_missing_root:" + str(root.trace_id)
    ]


def test_multiple_roots_for_same_trace_are_ambiguous_for_cost_totals(tmp_path):
    first = run(total_cost=Decimal("1.20"))
    second = run(trace=first.trace_id, total_cost=Decimal("1.20"))
    result = collect(
        tmp_path,
        THREAD,
        config=CONFIG,
        client=Client([first, second], {str(first.trace_id): [first, second]}),
    )
    assert not result["coverage"]["complete"]
    assert (
        "ambiguous_trace_roots:" + str(first.trace_id)
        in result["coverage"]["incomplete_reasons"]
    )


def test_project_lookup_failure_still_writes_safe_partial_receipt(tmp_path):
    class MissingProject(Client):
        def read_project(self, **kwargs):
            raise LookupError("secret-detail")

    result = collect(tmp_path, THREAD, config=CONFIG, client=MissingProject([], {}))
    assert result["source"]["project_id"] is None
    assert result["coverage"]["root_query_error_type"] == "LookupError"
    assert not result["coverage"]["complete"]
    assert (tmp_path / "traces/langsmith.json").exists()


def test_configuration_is_explicit_and_does_not_expose_key():
    with pytest.raises(ValueError, match="LANGSMITH_ENDPOINT"):
        TraceConfig.from_env({})
    with pytest.raises(ValueError, match="without credentials"):
        TraceConfig("https://user:secret@traces.example", "dev", "key")
    with pytest.raises(ValueError, match="without credentials"):
        TraceConfig("https://traces.example?key=secret", "dev", "key")
    assert "test-key-never-save" not in repr(CONFIG)
    parsed = TraceConfig.from_env(
        {
            "LANGSMITH_ENDPOINT": "https://traces.example",
            "LANGSMITH_PROJECT": "dev",
            "LANGSMITH_API_KEY": "key",
            "LANGSMITH_WORKSPACE_ID": str(PROJECT),
        }
    )
    assert parsed.workspace_id == str(PROJECT)
