"""Synthetic saved protocol evidence only; no model, server or vendor access."""

import json
from typing import cast

import pytest
from langgraph_sdk.schema import ThreadState

from tools.evidence import render_report, render_transcript, summarize

FIRST = "2026-10-25T12:00:00+00:00"
LAST = "2026-10-25T12:00:12+00:00"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    import socket

    def blocked(*args, **kwargs):
        raise AssertionError("Evidence tests must remain offline")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)


def save_events(folder, events, run="run"):
    path = folder / "streams" / run / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(event) for event in events) + "\n")


def message(mid, *, agent=None, usage=None, tools=None, role="ai"):
    return {
        "id": mid,
        "type": role,
        "name": agent,
        "usage_metadata": usage,
        "tool_calls": tools or [],
    }


def event(
    value, *, channel="messages", stamp: str | None = FIRST, eid=None, metadata=None
):
    return {
        "id": eid,
        "event": channel,
        "observed_at": stamp,
        "data": [value, metadata or {}] if channel.startswith("messages") else value,
    }


def usage(inp, out):
    return {"input_tokens": inp, "output_tokens": out, "total_tokens": inp + out}


def test_agents_keep_names_unknown_usage_and_real_observation_times(tmp_path):
    save_events(
        tmp_path,
        [
            event(message("a", agent="study_author", usage=usage(20, 2)), eid="one"),
            event(
                message("a", agent="study_author", usage=usage(20, 5)),
                stamp=LAST,
                eid="two",
            ),
            event(message("b"), metadata={"lc_agent_name": "discovery"}),
            event(message("c", usage=usage(3, 1)), stamp=None),
            event(
                message("a", agent="study_author", usage=usage(20, 5)),
                stamp=LAST,
                eid="two",
            ),
        ],
    )
    result = summarize(tmp_path)
    assert result["observed_model_calls"] == 2
    assert result["observed_model_messages"] == 3
    assert result["input_tokens"] == 23 and result["output_tokens"] == 6
    assert result["incomplete_usage_ids"] == ["b"]
    author = result["agents"]["study_author"]
    assert author["observed_model_calls"] == 1
    assert (author["input_tokens"], author["output_tokens"]) == (20, 5)
    assert (author["first_observed_at"], author["last_observed_at"]) == (FIRST, LAST)
    assert result["agents"]["discovery"]["input_tokens"] is None
    assert result["agents"]["(unknown)"]["first_observed_at"] is None
    assert result["cost_usd"] is None


def test_tools_deduplicate_snapshots_and_retain_error_pointer_without_private_content(
    tmp_path,
):
    call = {"id": "tool-1", "name": "build_study", "args": {"private": "do not copy"}}
    request = message("model-1", agent="supervisor", tools=[call])
    reply = {
        "type": "tool",
        "name": "build_study",
        "tool_call_id": "tool-1",
        "status": "error",
        "content": "private validator content",
    }
    save_events(
        tmp_path,
        [
            event(request),
            event(
                {"supervisor": {"messages": [request, reply]}},
                channel="updates|agent:uuid",
                stamp=LAST,
            ),
            event({"messages": [request, reply]}, channel="values", stamp=LAST),
            event(
                {"error": "private backend details"},
                channel="error",
                eid="error-1",
                stamp=LAST,
            ),
            event(
                {"error": "private backend details"},
                channel="error",
                eid="error-1",
                stamp=LAST,
            ),
        ],
    )
    result = summarize(tmp_path)
    assert result["observed_tool_calls"] == 1
    assert result["tool_counts"] == {"build_study": 1}
    tool = result["tool_calls"]["tool-1"]
    assert tool["request_observed"] and tool["result_observed"]
    assert tool["result_statuses"] == ["error"]
    assert tool["error_evidence"] is not None
    assert tool["error_evidence"]["file"] == "streams/run/events.jsonl"
    assert tool["error_evidence"]["line"] == 2
    assert result["agents"]["supervisor"]["observed_tool_errors"] == 1
    assert len(result["stream_errors"]) == 1
    assert "private" not in json.dumps(result)


def test_result_only_call_remains_unattributed_and_prose_is_not_an_error_classifier(
    tmp_path,
):
    reply = {
        "type": "tool",
        "name": "inspect",
        "tool_call_id": "only-result",
        "status": "success",
        "content": "The word error appears in quoted input.",
    }
    save_events(tmp_path, [event({"messages": [reply]}, channel="values")])
    row = summarize(tmp_path)["tool_calls"]["only-result"]
    assert row["agent"] is None and row["request_observed"] is False
    assert row["result_statuses"] == ["success"] and row["error_evidence"] is None


def test_alias_snapshots_do_not_add_model_usage_and_tool_payload_is_not_walked(
    tmp_path,
):
    nested = {
        "type": "tool",
        "tool_call_id": "real",
        "name": "read",
        "content": {"messages": [message("fake", usage=usage(1000, 1000))]},
    }
    save_events(
        tmp_path,
        [
            event(message("live", usage=usage(10, 1))),
            event(
                {"messages": [message("provider-alias", usage=usage(10, 1)), nested]},
                channel="values",
            ),
        ],
    )
    result = summarize(tmp_path)
    assert set(result["calls"]) == {"live"}
    assert result["input_tokens"] == 10
    assert set(result["tool_calls"]) == {"real"}


def test_incomparable_usage_is_unknown_until_a_full_cumulative_receipt(tmp_path):
    events = [
        event(message("m", usage=usage(20, 0))),
        event(message("m", usage=usage(0, 5))),
    ]
    save_events(tmp_path, events)
    result = summarize(tmp_path)
    assert result["calls"] == {} and result["input_tokens"] is None
    assert result["incomplete_usage_ids"] == ["m"]
    save_events(tmp_path, events + [event(message("m", usage=usage(20, 5)))])
    result = summarize(tmp_path)
    assert result["input_tokens"] == 20 and result["output_tokens"] == 5
    assert result["incomplete_usage_ids"] == []


def test_complete_run_capture_covers_attached_ui_thread_and_deduplicates(tmp_path):
    rows = [
        {
            "run_id": "ui-run",
            "created_at": FIRST,
            "updated_at": LAST,
            "status": "success",
        },
        {
            "run_id": "unfinished",
            "created_at": FIRST,
            "updated_at": LAST,
            "status": "running",
        },
    ]
    (tmp_path / "runs.json").write_text(
        json.dumps({"captured_at": LAST, "complete": True, "runs": rows + [rows[0]]})
    )
    result = summarize(tmp_path)
    assert len(result["runs"]) == 2
    by_id = {row["run_id"]: row for row in result["runs"]}
    assert by_id["ui-run"]["key"] is None
    assert by_id["ui-run"]["server_elapsed_seconds"] == 12
    assert by_id["unfinished"]["server_elapsed_seconds"] is None
    assert result["run_coverage"] == {
        "source": "runs.json",
        "complete": True,
        "captured_at": LAST,
    }


def trace_file(folder, roots, runs, *, complete=True):
    path = folder / "traces/langsmith.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "root_run_ids": roots,
                "runs": runs,
                "collected_at": LAST,
                "source": {"provider": "langsmith"},
                "coverage": {"complete": complete},
            }
        )
    )


def span(
    rid, *, parent=None, cost: str | None = "1.25", end: str | None = LAST, kind="chain"
):
    return {
        "id": rid,
        "trace_id": "trace-1",
        "parent_run_id": parent,
        "start_time": FIRST,
        "end_time": end,
        "first_token_time": FIRST,
        "total_cost": cost,
        "run_type": kind,
        "name": rid,
        "extra": {"metadata": {"lc_agent_name": "author"}},
        "prompt_tokens": 20,
        "completion_tokens": 5,
        "total_tokens": 25,
        "error": "validation failed\nPRIVATE" if rid == "failed-tool" else None,
    }


def test_recorded_trace_cost_counts_roots_once_not_parent_plus_child(tmp_path):
    trace_file(
        tmp_path,
        ["root", "root"],
        [
            span("root"),
            span("llm", parent="root", kind="llm"),
            span("failed-tool", parent="root", kind="tool", cost=None),
        ],
    )
    result = summarize(tmp_path)
    assert result["cost_usd"] == "1.25"
    assert result["traces"] is not None
    assert result["traces"]["observed_root_cost_usd"] == "1.25"
    assert len(result["traces"]["spans"]) == 3
    failure = next(
        row for row in result["traces"]["spans"] if row["id"] == "failed-tool"
    )
    assert failure["elapsed_seconds"] == 12 and failure["error_observed"] is True
    assert failure["evidence"] == "traces/langsmith.json#runs/failed-tool"
    assert "PRIVATE" not in json.dumps(result)
    assert "not billing receipts" in result["traces"]["cost_scope"]


@pytest.mark.parametrize(
    "cost,end,complete",
    [
        (None, LAST, True),
        ("1.25", None, True),
        ("1.25", LAST, False),
        ("NaN", LAST, True),
        ("-1", LAST, True),
    ],
)
def test_missing_or_incomplete_trace_cost_stays_unknown(tmp_path, cost, end, complete):
    trace_file(
        tmp_path, ["root"], [span("root", cost=cost, end=end)], complete=complete
    )
    assert summarize(tmp_path)["cost_usd"] is None


def test_zero_trace_cost_is_known_and_nonroot_selected_cost_is_not_a_total(tmp_path):
    trace_file(tmp_path, ["root"], [span("root", cost="0")])
    assert summarize(tmp_path)["cost_usd"] == "0"
    trace_file(
        tmp_path, ["root", "child"], [span("root"), span("child", parent="root")]
    )
    assert summarize(tmp_path)["cost_usd"] is None


def test_public_transcript_does_not_expose_reasoning_or_tool_payloads():
    rendered = render_transcript(
        cast(
            ThreadState,
            {
                "values": {
                    "messages": [
                        {
                            "type": "ai",
                            "content": [
                                {"type": "thinking", "thinking": "PRIVATE"},
                                {"type": "reasoning", "summary": "PRIVATE"},
                                {"type": "text", "text": "Public answer"},
                            ],
                        },
                        {"type": "tool", "content": "PRIVATE"},
                    ]
                }
            },
        )
    )
    assert "Public answer" in rendered and "PRIVATE" not in rendered


def test_report_exposes_counts_timing_cost_and_backend_evidence_without_payloads(
    tmp_path,
):
    save_events(
        tmp_path,
        [
            event(
                message(
                    "model",
                    agent="supervisor",
                    usage=usage(20, 5),
                    tools=[
                        {
                            "id": "call",
                            "name": "build_study",
                            "args": {"private": "PRIVATE"},
                        }
                    ],
                )
            )
        ],
    )
    trace_file(
        tmp_path,
        ["root"],
        [
            span("root"),
            span("model", parent="root", kind="llm"),
            span("failed-tool", parent="root", kind="tool"),
        ],
    )
    directory = tmp_path / "backend/capture-one/execution-one"
    directory.mkdir(parents=True)
    for name in ("execution.json", "logs-00000.json", "events.jsonl", "stream.sse"):
        (directory / name).write_text("PRIVATE")
    (tmp_path / "sources.json").write_text(
        json.dumps(
            {
                "collected_at": LAST,
                "sources": {
                    "thread": {"status": "unavailable", "error_type": "PRIVATE"},
                    "stream": {"status": "available"},
                    "langsmith": {"status": "available"},
                    "backend": {
                        "status": "partial",
                        "captured_at": LAST,
                        "executions": [
                            {
                                "execution_id": "execution-one",
                                "directory": "backend/capture-one/execution-one",
                                "record": {
                                    "status": "complete",
                                    "file": "execution.json",
                                    "execution_status": "failed",
                                },
                                "logs": {
                                    "status": "complete",
                                    "events": 2,
                                    "pages": ["logs-00000.json"],
                                },
                                "stream": {
                                    "status": "partial",
                                    "event_counts": {"lifecycle": 1, "output": 3},
                                    "parsed": "events.jsonl",
                                    "raw": "stream.sse",
                                },
                                "status": "partial",
                            }
                        ],
                    },
                },
            }
        )
    )
    report = render_report(tmp_path)
    assert "| supervisor | 1 | 1 | 20 / 5 | 1 / 0 |" in report
    assert "| author | 1 | 20 / 5 | 0 | 12.0–12.0 | 1.25–1.25 (1/1) | 0 |" in report
    assert "| author / failed-tool | 1 | 1 |" in report
    assert "Current collection cost (USD): 1.25" in report
    assert "| thread | unavailable |" in report
    assert "| execution-one | failed / partial |" in report
    assert "2 / 1 (complete)" in report
    assert "lifecycle: 1, output: 3 (partial)" in report
    assert "(backend/capture-one/execution-one/execution.json)" in report
    assert "(backend/capture-one/execution-one/logs-00000.json)" in report
    assert "PRIVATE" not in report
    assert "do not add them together" in report
    assert "not billing receipts" in report


@pytest.mark.parametrize("status", ["unavailable", "not_requested", "partial"])
def test_stale_trace_file_is_dated_and_not_presented_as_current_cost(tmp_path, status):
    trace_file(tmp_path, ["root"], [span("root")])
    current = "2026-10-26T13:00:00+00:00"
    (tmp_path / "sources.json").write_text(
        json.dumps(
            {"collected_at": current, "sources": {"langsmith": {"status": status}}}
        )
    )
    summary = summarize(tmp_path)
    assert summary["cost_usd"] is None
    assert summary["traces"] is not None
    assert summary["traces"]["observed_root_cost_usd"] == "1.25"
    report = render_report(tmp_path)
    assert f"Latest collection: {current}" in report
    assert f"Snapshot captured {LAST}; latest collection status: {status}" in report
    assert "Current collection cost (USD): unknown" in report
    assert "Saved known root cost (USD): 1.25" in report


def test_empty_report_keeps_unknowns_and_no_false_execution_or_usage_claims(tmp_path):
    report = render_report(tmp_path)
    assert "No observed messages" in report
    assert "No saved LangSmith snapshot" in report
    assert "does not imply that no execution occurred" in report
    assert "Model cost and trace timing are unknown" in report


def test_report_does_not_turn_source_labels_or_external_paths_into_links(tmp_path):
    save_events(
        tmp_path,
        [event(message("m", agent="[click](https://bad.example)", usage=usage(1, 1)))],
    )
    (tmp_path / "sources.json").write_text(
        json.dumps(
            {
                "sources": {
                    "backend": {
                        "status": "partial",
                        "manifest": "../private.json",
                        "executions": [],
                    }
                }
            }
        )
    )
    report = render_report(tmp_path)
    assert "[click](https://bad.example)" not in report
    assert "&#91;click&#93;" in report
    assert "../private.json" not in report


def test_legacy_snapshot_without_collection_receipt_does_not_claim_current_cost(
    tmp_path,
):
    trace_file(tmp_path, ["root"], [span("root")])
    report = render_report(tmp_path)
    assert f"Snapshot captured {LAST}" in report
    assert "Saved snapshot cost (USD): 1.25" in report
    assert "Current collection cost" not in report


def test_recorded_epoch_roles_override_fallback_names_even_on_later_chunks(tmp_path):
    call = {"id": "tool", "name": "build_study"}
    first = message("m", agent="fallback", usage=usage(10, 1), tools=[call])
    receipt = {
        **first,
        "response_metadata": {"epoch_model_call": {"model_role": "supervisor"}},
    }
    save_events(
        tmp_path,
        [
            event(first),
            event(first, metadata={"epoch_model_role": "stream-role"}),
            event(receipt, metadata={"epoch_model_role": "lower-precedence"}),
            event(first),
            event(
                message("a", agent="fallback", usage=usage(3, 1)),
                metadata={
                    "epoch_model_role": "author",
                    "lc_agent_name": "other-fallback",
                },
            ),
            event(message("unknown", usage=usage(1, 1))),
        ],
    )
    summary = summarize(tmp_path)
    assert set(summary["agents"]) == {"supervisor", "author", "(unknown)"}
    assert summary["calls"]["m"]["agent"] == "supervisor"
    assert summary["tool_calls"]["tool"]["agent"] == "supervisor"
    assert summary["agents"]["supervisor"]["input_tokens"] == 10
    assert summary["agents"]["author"]["input_tokens"] == 3
    assert summary["calls"]["unknown"]["agent"] is None


def test_trace_roles_and_agent_cost_ranges_come_from_recorded_fields_only(tmp_path):
    author = span("call-one", parent="root", kind="llm", cost="0.10")
    author["extra"] = {
        "metadata": {"epoch_model_role": "author", "lc_agent_name": "wrong"}
    }
    second = {**author, "id": "call-two", "total_cost": "0.25"}
    missing = {**author, "id": "call-three", "total_cost": None}
    supervisor = span("a-model-name", parent="root", kind="llm")
    supervisor["extra"] = {"metadata": {"epoch_model_role": "supervisor"}}
    unknown = span("supervisor", parent="root", kind="llm", cost=None)
    unknown["extra"] = {"metadata": {}}
    trace_file(
        tmp_path, ["root"], [span("root"), author, second, missing, supervisor, unknown]
    )
    summary = summarize(tmp_path)
    assert summary["traces"] is not None
    by_id = {row["id"]: row for row in summary["traces"]["spans"]}
    assert by_id["call-one"]["agent"] == "author"
    assert by_id["a-model-name"]["agent"] == "supervisor"
    assert by_id["supervisor"]["agent"] is None
    report = render_report(tmp_path)
    assert "| author | 3 | 60 / 15 | 0 | 12.0–12.0 | 0.10–0.25 (2/3) | 0 |" in report
    assert "inclusive span costs are never added" in report
    assert "0.35" not in report
    assert summary["cost_usd"] is None
    assert summary["traces"]["observed_root_cost_usd"] == "1.25"
    assert summary["traces"]["missing_llm_cost_ids"] == ["call-three", "supervisor"]
    assert summary["traces"]["cost_complete"] is False


def test_priced_roots_do_not_hide_unpriced_recovered_author_calls(tmp_path):
    root = span("root", cost="0.7687310")
    models = []
    for i in range(6):
        supervisor = span(
            f"supervisor-{i}",
            parent="root",
            kind="llm",
            cost="0.1596210" if i == 5 else "0.1",
        )
        supervisor["extra"] = {"metadata": {"epoch_model_role": "supervisor"}}
        models.append(supervisor)
    models.append(span("summary", parent="root", kind="llm", cost="0.10911"))
    for i in range(7):
        author = span(f"author-{i}", parent="root", kind="llm", cost=None)
        author["extra"] = {
            "metadata": {
                "epoch_model_role": "study_author",
                "ls_model_name": "openai.gpt-6-astra",
            }
        }
        author["prompt_token_details"] = {"cache_creation": 10, "cache_read": 90}
        if i == 0:
            author.update(
                error="StreamChunkTimeoutError",
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
            )
        models.append(author)
    trace_file(tmp_path, ["root"], [root, *models])
    summary = summarize(tmp_path)
    trace = summary["traces"]
    assert trace is not None
    assert summary["cost_usd"] is None
    assert trace["cost_complete"] is False
    assert trace["observed_root_cost_usd"] == "0.7687310"
    assert trace["missing_root_cost_ids"] == []
    assert trace["missing_llm_cost_ids"] == [f"author-{i}" for i in range(7)]
    assert trace["incomplete_llm_usage_ids"] == ["author-0"]
    author = next(row for row in trace["spans"] if row["id"] == "author-1")
    assert author["model"] == "openai.gpt-6-astra"
    assert author["cache_creation_tokens"] == 10
    assert author["cache_read_tokens"] == 90
    assert author["usage_complete"] is True
    report = render_report(tmp_path)
    assert "Cost accounting complete: False" in report
    assert "Saved known root cost (USD): 0.7687310" in report
    assert "Unpriced LLM spans: 7; incomplete LLM usage spans: 1" in report


@pytest.mark.parametrize(
    "cost,end,error,prompt",
    [
        (None, LAST, None, 20),
        ("NaN", LAST, None, 20),
        ("-1", LAST, None, 20),
        ("0.05", None, None, 20),
        ("0", LAST, "StreamChunkTimeoutError", 0),
        ("0.05", LAST, None, None),
    ],
)
def test_incomplete_llm_receipts_do_not_become_complete_root_costs(
    tmp_path, cost, end, error, prompt
):
    model = span("model", parent="root", kind="llm", cost=cost, end=end)
    model.update(error=error, prompt_tokens=prompt)
    trace_file(tmp_path, ["root"], [span("root"), model])
    summary = summarize(tmp_path)
    assert summary["cost_usd"] is None
    assert summary["traces"]["cost_complete"] is False


def test_genuine_zero_llm_cost_and_zero_cache_tokens_remain_known(tmp_path):
    model = span("model", parent="root", kind="llm", cost="0")
    model["prompt_token_details"] = {"cache_creation": 0, "cache_read": 0}
    trace_file(tmp_path, ["root"], [span("root", cost="0"), model])
    summary = summarize(tmp_path)
    assert summary["cost_usd"] == "0"
    trace = summary["traces"]
    assert trace is not None and trace["cost_complete"] is True
    row = next(row for row in trace["spans"] if row["id"] == "model")
    assert row["cache_creation_tokens"] == row["cache_read_tokens"] == 0


def test_report_native_job_timing_and_bounded_error_previews_are_source_owned(tmp_path):
    trace = span("failed-tool", parent="root", kind="tool")
    trace["error"] = "<unexpected> [input]" + "x" * 260 + "\nPRIVATE stack trace"
    trace_file(tmp_path, ["root"], [span("root"), trace])
    target = tmp_path / "backend/capture/execution"
    target.mkdir(parents=True)
    (target / "execution.json").write_text('{"error_reason":"full private diagnostic"}')
    (tmp_path / "sources.json").write_text(
        json.dumps(
            {
                "sources": {
                    "backend": {
                        "status": "partial",
                        "executions": [
                            {
                                "execution_id": "e",
                                "directory": "backend/capture/execution",
                                "record": {
                                    "status": "complete",
                                    "file": "execution.json",
                                    "execution_status": "failed",
                                    "error_reason": "<native> failure\nPRIVATE remainder",
                                },
                                "logs": {"status": "unavailable"},
                                "stream": {
                                    "status": "partial",
                                    "job_duration_ms": 125.5,
                                },
                                "status": "partial",
                            }
                        ],
                    }
                }
            }
        )
    )
    report = render_report(tmp_path)
    assert "| e | failed / partial | 125.5 |" in report
    assert "&lt;native&gt; failure" in report
    assert "&lt;unexpected&gt; &#91;input&#93;" in report
    assert "truncated; see raw evidence" in report
    assert "PRIVATE" not in report and "x" * 241 not in report
    assert "(backend/capture/execution/execution.json)" in report
    assert "[failed-tool](traces/langsmith.json)" in report
    summary = summarize(tmp_path)
    assert summary["traces"] is not None
    saved = next(
        row for row in summary["traces"]["spans"] if row["id"] == "failed-tool"
    )
    assert (
        saved["error_preview"]
        == trace["error"].splitlines()[0][:240] + " [truncated; see raw evidence]"
    )


@pytest.mark.parametrize("channel", ["values", "updates"])
@pytest.mark.parametrize("persisted_id", ["lc_run--recorded-run", "provider-final-id"])
def test_late_persisted_receipt_updates_only_observed_stream_identity(
    tmp_path, channel, persisted_id
):
    call = {"id": "tool", "name": "build_study"}
    streamed = message(
        "lc_run--recorded-run", agent="fallback", usage=usage(10, 1), tools=[call]
    )
    persisted = {
        **streamed,
        "id": persisted_id,
        "usage_metadata": usage(999, 999),
        "response_metadata": {
            "epoch_model_call": {"model_role": "supervisor", "run_id": "recorded-run"}
        },
    }
    unknown = message("never-streamed", usage=usage(999, 999))
    unknown["response_metadata"] = {
        "epoch_model_call": {"model_role": "not-observed", "run_id": "missing-run"}
    }
    payload = {"messages": [persisted, unknown]}
    if channel == "updates":
        payload = {"supervisor": payload}
    save_events(
        tmp_path,
        [
            event(streamed, metadata={"epoch_model_role": "earlier-stream-role"}),
            event(payload, channel=channel),
        ],
    )
    result = summarize(tmp_path)
    assert (
        result["observed_model_calls"] == 1 and result["observed_model_messages"] == 1
    )
    assert result["input_tokens"] == 10 and result["output_tokens"] == 1
    assert set(result["agents"]) == {"supervisor"}
    assert result["calls"]["lc_run--recorded-run"]["agent"] == "supervisor"
    assert result["tool_calls"]["tool"]["agent"] == "supervisor"


@pytest.mark.parametrize("persisted_id", ["lc_run--original", "provider-final-id"])
def test_receipt_from_continuation_scanned_first_still_enriches_original_stream(
    tmp_path, persisted_id
):
    original = message("lc_run--original", agent="fallback", usage=usage(10, 2))
    saved = {
        **original,
        "id": persisted_id,
        "usage_metadata": usage(500, 500),
        "response_metadata": {
            "epoch_model_call": {"model_role": "supervisor", "run_id": "original"}
        },
    }
    never_streamed = {
        **saved,
        "id": "absent",
        "response_metadata": {
            "epoch_model_call": {"model_role": "phantom", "run_id": "absent"}
        },
    }
    # A later continuation has the lexically smaller UUID directory.
    save_events(
        tmp_path,
        [event({"messages": [saved, never_streamed]}, channel="values")],
        run="a-continuation",
    )
    save_events(tmp_path, [event(original)], run="z-original")
    result = summarize(tmp_path)
    assert (
        result["observed_model_calls"] == 1 and result["observed_model_messages"] == 1
    )
    assert result["input_tokens"] == 10 and result["output_tokens"] == 2
    assert set(result["agents"]) == {"supervisor"}
    assert result["calls"]["lc_run--original"]["agent"] == "supervisor"
