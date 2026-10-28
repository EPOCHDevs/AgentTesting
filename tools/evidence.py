"""Offline conversation rendering and conservative usage summaries.

Carries forward the EpochAI metric rule: cumulative snapshots must not count a
model response more than once. Raw counts have no implied dollar price.
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from html import escape
from pathlib import Path
from typing import TypedDict, cast
from urllib.parse import quote

from langgraph_sdk.schema import ThreadState


class Usage(TypedDict):
    input_tokens: int
    output_tokens: int
    total_tokens: int
    agent: str | None
    model: str | None
    first_observed_at: str | None
    last_observed_at: str | None
    usage_evidence: EvidenceRef


def text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(block["text"])
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


def render_transcript(state: ThreadState) -> str:
    values = state.get("values")
    messages = values.get("messages", []) if isinstance(values, dict) else []
    lines = [
        "# Conversation",
        "",
        "Saved server messages; tool details remain in thread.json.",
    ]
    for message in messages:
        role = message.get("type") or message.get("role")
        if role not in ("human", "user", "ai", "assistant"):
            continue
        body = text(message.get("content"))
        if body:
            lines.extend(
                [
                    "",
                    "## " + ("Researcher" if role in ("human", "user") else "Epoch"),
                    "",
                    body,
                ]
            )
    return "\n".join(lines) + "\n"


class EvidenceRef(TypedDict):
    file: str
    line: int
    channel: str
    observed_at: str | None


class ToolEvidence(TypedDict):
    name: str | None
    agent: str | None
    request_observed: bool
    result_observed: bool
    result_statuses: list[str]
    first_observed_at: str | None
    last_observed_at: str | None
    request_evidence: EvidenceRef | None
    result_evidence: EvidenceRef | None
    error_evidence: EvidenceRef | None


class AgentUsage(TypedDict):
    agent: str | None
    observed_model_messages: int
    observed_model_calls: int
    input_tokens: int | None
    output_tokens: int | None
    observed_tool_calls: int
    observed_tool_errors: int
    first_observed_at: str | None
    last_observed_at: str | None


class RunSummary(TypedDict):
    key: str | None
    run_id: str
    submitted_at: str | None
    status: str | None
    server_elapsed_seconds: float | None


class RunCoverage(TypedDict):
    source: str
    complete: bool
    captured_at: str | None


class TraceSpan(TypedDict):
    id: str
    parent_run_id: str | None
    trace_id: str | None
    name: str | None
    run_type: str | None
    agent: str | None
    model: str | None
    start_time: str | None
    end_time: str | None
    first_token_time: str | None
    elapsed_seconds: float | None
    recorded_cost_usd: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    cache_creation_tokens: int | None
    cache_read_tokens: int | None
    usage_complete: bool
    error_observed: bool
    error_preview: str | None
    evidence: str


class TraceSummary(TypedDict):
    source: object
    collected_at: str | None
    collection_status: str | None
    coverage: object
    spans: list[TraceSpan]
    cost_usd: str | None
    observed_root_cost_usd: str | None
    missing_root_cost_ids: list[str]
    incomplete_root_ids: list[str]
    missing_llm_cost_ids: list[str]
    incomplete_llm_usage_ids: list[str]
    cost_complete: bool
    cost_scope: str
    timing_scope: str


class Summary(TypedDict):
    observed_model_calls: int
    calls: dict[str, Usage]
    input_tokens: int | None
    output_tokens: int | None
    incomplete_usage_ids: list[str]
    runs: list[RunSummary]
    run_coverage: RunCoverage
    observed_model_messages: int
    agents: dict[str, AgentUsage]
    observed_tool_calls: int
    tool_calls: dict[str, ToolEvidence]
    tool_counts: dict[str, int]
    stream_errors: list[EvidenceRef]
    traces: TraceSummary | None
    coverage: str
    cost_usd: str | None
    timing: str


def _name(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _agent_identity(message: dict, metadata: dict) -> tuple[str | None, int]:
    """Prefer Epoch's recorded call role over descriptive message/agent names."""
    response = _mapping(message.get("response_metadata"))
    receipt = _mapping(response.get("epoch_model_call"))
    if role := _name(receipt.get("model_role")):
        return role, 3
    if role := _name(metadata.get("epoch_model_role")):
        return role, 2
    fallback = _name(message.get("name")) or _name(metadata.get("lc_agent_name"))
    return fallback, 1 if fallback else 0


def _timestamp(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError:
        return None
    return value if stamp.tzinfo is not None else None


def _edge(left: str | None, right: str | None, *, latest: bool = False) -> str | None:
    if left is None:
        return right
    if right is None:
        return left
    ordered = sorted((left, right), key=datetime.fromisoformat)
    return ordered[-1 if latest else 0]


def _snapshot_messages(value: object):
    """Read protocol message arrays, without walking tool arguments/content."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "messages" and isinstance(item, list):
                yield from (message for message in item if isinstance(message, dict))
            elif isinstance(item, dict):
                yield from _snapshot_messages(item)


def _tool_observation(
    tools: dict[str, ToolEvidence],
    tool_id: str,
    name: str | None,
    agent: str | None,
    evidence: EvidenceRef,
    *,
    agent_priorities: dict[str, int],
    agent_priority: int = 0,
    result_status: str | None = None,
    is_result: bool = False,
) -> None:
    row = tools.setdefault(
        tool_id,
        {
            "name": None,
            "agent": None,
            "request_observed": False,
            "result_observed": False,
            "result_statuses": [],
            "first_observed_at": None,
            "last_observed_at": None,
            "request_evidence": None,
            "result_evidence": None,
            "error_evidence": None,
        },
    )
    row["name"] = row["name"] or name
    if agent_priority > agent_priorities.get(tool_id, -1):
        row["agent"] = agent
        agent_priorities[tool_id] = agent_priority
    row["first_observed_at"] = _edge(row["first_observed_at"], evidence["observed_at"])
    row["last_observed_at"] = _edge(
        row["last_observed_at"], evidence["observed_at"], latest=True
    )
    if is_result:
        row["result_observed"] = True
        row["result_evidence"] = row["result_evidence"] or evidence
        if result_status and result_status not in row["result_statuses"]:
            row["result_statuses"].append(result_status)
        if result_status == "error":
            row["error_evidence"] = row["error_evidence"] or evidence
    else:
        row["request_observed"] = True
        row["request_evidence"] = row["request_evidence"] or evidence


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        return None
    return result if result.is_finite() and result >= 0 else None


def _count(value: object) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _error_preview(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    lines = value.splitlines()
    first = lines[0]
    suffix = (
        " [truncated; see raw evidence]" if len(first) > 240 or len(lines) > 1 else ""
    )
    return first[:240] + suffix


def _trace_summary(folder: Path) -> TraceSummary | None:
    """Preserve recorded spans; never sum parent and child inclusive costs."""
    path = folder / "traces/langsmith.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    if data.get("schema_version") != 1:
        raise ValueError("Unsupported saved LangSmith trace schema")
    runs = {run["id"]: run for run in data.get("runs", [])}
    roots = list(dict.fromkeys(data.get("root_run_ids", [])))
    spans: list[TraceSpan] = []
    known_cost = Decimal(0)
    missing_cost: list[str] = []
    incomplete_roots: list[str] = []
    for root_id in roots:
        root = runs.get(root_id)
        if root is None or root.get("parent_run_id") is not None:
            incomplete_roots.append(root_id)
            missing_cost.append(root_id)
            continue
        cost = _decimal(root.get("total_cost"))
        if cost is None:
            missing_cost.append(root_id)
        else:
            known_cost += cost
        if _timestamp(root.get("end_time")) is None:
            incomplete_roots.append(root_id)
    for run_id, run in sorted(runs.items()):
        start, end = _timestamp(run.get("start_time")), _timestamp(run.get("end_time"))
        elapsed = None
        if start and end:
            seconds = (
                datetime.fromisoformat(end) - datetime.fromisoformat(start)
            ).total_seconds()
            elapsed = seconds if seconds >= 0 else None
        extra = run.get("extra") or {}
        metadata = extra.get("metadata") if isinstance(extra, dict) else None
        metadata = metadata if isinstance(metadata, dict) else {}
        cost = _decimal(run.get("total_cost"))
        prompt_details = _mapping(run.get("prompt_token_details"))
        usage_complete = (
            _count(run.get("prompt_tokens")) is not None
            and _count(run.get("completion_tokens")) is not None
            and end is not None
            and run.get("error") is None
        )
        spans.append(
            {
                "id": run_id,
                "parent_run_id": _name(run.get("parent_run_id")),
                "trace_id": _name(run.get("trace_id")),
                "name": _name(run.get("name")),
                "run_type": _name(run.get("run_type")),
                "agent": _name(metadata.get("epoch_model_role"))
                or _name(metadata.get("lc_agent_name")),
                "model": _name(metadata.get("ls_model_name"))
                or _name(metadata.get("model")),
                "start_time": start,
                "end_time": end,
                "first_token_time": _timestamp(run.get("first_token_time")),
                "elapsed_seconds": elapsed,
                "recorded_cost_usd": str(cost) if cost is not None else None,
                "prompt_tokens": _count(run.get("prompt_tokens")),
                "completion_tokens": _count(run.get("completion_tokens")),
                "total_tokens": _count(run.get("total_tokens")),
                "cache_creation_tokens": _count(prompt_details.get("cache_creation")),
                "cache_read_tokens": _count(prompt_details.get("cache_read")),
                "usage_complete": usage_complete,
                "error_observed": run.get("error") is not None,
                "error_preview": _error_preview(run.get("error")),
                "evidence": f"traces/langsmith.json#runs/{run_id}",
            }
        )
    coverage = data.get("coverage") or {}
    current = _sources(folder)
    status = _name(
        _mapping(_mapping(current.get("sources")).get("langsmith")).get("status")
    )
    missing_llm_cost = [
        row["id"]
        for row in spans
        if row["run_type"] == "llm" and row["recorded_cost_usd"] is None
    ]
    incomplete_llm_usage = [
        row["id"]
        for row in spans
        if row["run_type"] == "llm" and not row["usage_complete"]
    ]
    complete_cost = (
        bool(roots)
        and coverage.get("complete") is True
        and not missing_cost
        and not incomplete_roots
        and not missing_llm_cost
        and not incomplete_llm_usage
    )
    return {
        "source": data.get("source"),
        "collected_at": _timestamp(data.get("collected_at")),
        "collection_status": status,
        "coverage": coverage,
        "spans": spans,
        "cost_usd": str(known_cost) if complete_cost else None,
        "observed_root_cost_usd": str(known_cost)
        if len(missing_cost) < len(roots)
        else None,
        "missing_root_cost_ids": missing_cost,
        "incomplete_root_ids": incomplete_roots,
        "missing_llm_cost_ids": missing_llm_cost,
        "incomplete_llm_usage_ids": incomplete_llm_usage,
        "cost_complete": complete_cost,
        "cost_scope": "LangSmith reported root total_cost only, excluding child totals; provider estimates, not billing receipts.",
        "timing_scope": "Recorded trace span elapsed time; overlapping span durations are not added or called model compute time.",
    }


def summarize(folder: Path) -> Summary:
    calls: dict[str, Usage] = {}
    model_agents: dict[str, str | None] = {}
    model_agent_priorities: dict[str, int] = {}
    snapshot_agents: dict[str, tuple[str | None, int]] = {}
    model_names: dict[str, str | None] = {}
    model_times: dict[str, tuple[str | None, str | None]] = {}
    usage_bounds: dict[str, list[int]] = {}
    tools: dict[str, ToolEvidence] = {}
    tool_agent_priorities: dict[str, int] = {}
    errors: list[EvidenceRef] = []
    event_ids: set[tuple[str, str]] = set()
    incomplete: set[str] = set()
    for path in sorted(folder.glob("streams/*/events.jsonl")):
        for line_number, line in enumerate(path.read_text().splitlines(), 1):
            event = json.loads(line)
            event_id = event.get("id")
            if event_id:
                identity = (path.parent.name, event_id)
                if identity in event_ids:
                    continue
                event_ids.add(identity)
            channel = str(event.get("event", ""))
            evidence: EvidenceRef = {
                "file": str(path.relative_to(folder)),
                "line": line_number,
                "channel": channel,
                "observed_at": _timestamp(event.get("observed_at")),
            }
            if channel.split("|", 1)[0] == "error":
                errors.append(evidence)
            data = event.get("data")
            streamed = channel.startswith("messages")
            metadata = {}
            if (
                streamed
                and isinstance(data, list)
                and data
                and isinstance(data[0], dict)
            ):
                messages = [data[0]]
                if len(data) > 1 and isinstance(data[1], dict):
                    metadata = data[1]
            elif channel.split("|", 1)[0] in ("values", "updates"):
                messages = list(_snapshot_messages(data))
            else:
                continue
            for message in messages:
                role = message.get("type") or message.get("role")
                if role in ("tool", "ToolMessage"):
                    if tool_id := _name(message.get("tool_call_id")):
                        _tool_observation(
                            tools,
                            tool_id,
                            _name(message.get("name")),
                            None,
                            evidence,
                            agent_priorities=tool_agent_priorities,
                            is_result=True,
                            result_status=_name(message.get("status")),
                        )
                    continue
                agent, agent_priority = _agent_identity(message, metadata)
                for tool in message.get("tool_calls") or []:
                    if isinstance(tool, dict) and (tool_id := _name(tool.get("id"))):
                        _tool_observation(
                            tools,
                            tool_id,
                            _name(tool.get("name")),
                            agent,
                            evidence,
                            agent_priorities=tool_agent_priorities,
                            agent_priority=agent_priority,
                        )
                # Only the messages channel owns model usage. Values/updates
                # may repeat the same response with a different provider ID.
                mid = _name(message.get("id"))
                usage = message.get("usage_metadata")
                if not streamed:
                    # End-of-call receipts can arrive on the persisted message.
                    # Run directories need not sort chronologically. Keep only
                    # identity candidates, then apply them to streamed calls.
                    receipt = _mapping(
                        _mapping(message.get("response_metadata")).get(
                            "epoch_model_call"
                        )
                    )
                    receipt_run = _name(receipt.get("run_id"))
                    identities = [mid] if mid else []
                    if receipt_run:
                        identities.append("lc_run--" + receipt_run)
                    for identity in identities:
                        if (
                            agent_priority
                            > snapshot_agents.get(identity, (None, -1))[1]
                        ):
                            snapshot_agents[identity] = (agent, agent_priority)
                if (
                    not streamed
                    or not mid
                    or not (usage or role in ("ai", "assistant", "AIMessageChunk"))
                ):
                    continue
                if agent_priority > model_agent_priorities.get(mid, -1):
                    model_agents[mid] = agent
                    model_agent_priorities[mid] = agent_priority
                response = message.get("response_metadata")
                response = response if isinstance(response, dict) else {}
                model_names[mid] = (
                    _name(response.get("model_name"))
                    or _name(response.get("model"))
                    or model_names.get(mid)
                )
                first, last = model_times.get(mid, (None, None))
                model_times[mid] = (
                    _edge(first, evidence["observed_at"]),
                    _edge(last, evidence["observed_at"], latest=True),
                )
                if not isinstance(usage, dict):
                    if mid not in calls:
                        incomplete.add(mid)
                    continue
                counts = [
                    usage.get(key)
                    for key in ("input_tokens", "output_tokens", "total_tokens")
                ]
                if any(type(value) is not int or value < 0 for value in counts):
                    incomplete.add(mid)
                    continue
                counts = cast(list[int], counts)
                bounds = usage_bounds.get(mid)
                if bounds is not None and not all(
                    current >= prior for current, prior in zip(counts, bounds)
                ):
                    if any(current > prior for current, prior in zip(counts, bounds)):
                        # Split/delta or contradictory usage is not a cumulative
                        # total. Do not invent a sum; wait for a complete snapshot.
                        calls.pop(mid, None)
                        incomplete.add(mid)
                    usage_bounds[mid] = [
                        max(current, prior) for current, prior in zip(counts, bounds)
                    ]
                    continue
                usage_bounds[mid] = counts
                calls[mid] = {
                    "input_tokens": counts[0],
                    "output_tokens": counts[1],
                    "total_tokens": counts[2],
                    "agent": model_agents[mid],
                    "model": model_names[mid],
                    "first_observed_at": model_times[mid][0],
                    "last_observed_at": model_times[mid][1],
                    "usage_evidence": evidence,
                }
                incomplete.discard(mid)
    # Persisted receipts enrich identity only: they cannot create model calls
    # or contribute usage, even if captured before the corresponding stream.
    for mid in model_agents:
        if mid in snapshot_agents:
            agent, priority = snapshot_agents[mid]
            if priority > model_agent_priorities[mid]:
                model_agents[mid] = agent
                model_agent_priorities[mid] = priority
    agents: dict[str, AgentUsage] = {}

    def agent_row(agent: str | None) -> AgentUsage:
        return agents.setdefault(
            agent or "(unknown)",
            {
                "agent": agent,
                "observed_model_messages": 0,
                "observed_model_calls": 0,
                "input_tokens": None,
                "output_tokens": None,
                "observed_tool_calls": 0,
                "observed_tool_errors": 0,
                "first_observed_at": None,
                "last_observed_at": None,
            },
        )

    for mid, agent in model_agents.items():
        row = agent_row(agent)
        row["observed_model_messages"] += 1
        first, last = model_times[mid]
        row["first_observed_at"] = _edge(row["first_observed_at"], first)
        row["last_observed_at"] = _edge(row["last_observed_at"], last, latest=True)
        if mid in calls:
            calls[mid].update(
                agent=agent,
                model=model_names[mid],
                first_observed_at=first,
                last_observed_at=last,
            )
            row["observed_model_calls"] += 1
            row["input_tokens"] = (row["input_tokens"] or 0) + calls[mid][
                "input_tokens"
            ]
            row["output_tokens"] = (row["output_tokens"] or 0) + calls[mid][
                "output_tokens"
            ]
    tool_counts: dict[str, int] = {}
    for tool in tools.values():
        row = agent_row(tool["agent"])
        row["observed_tool_calls"] += 1
        row["observed_tool_errors"] += int("error" in tool["result_statuses"])
        row["first_observed_at"] = _edge(
            row["first_observed_at"], tool["first_observed_at"]
        )
        row["last_observed_at"] = _edge(
            row["last_observed_at"], tool["last_observed_at"], latest=True
        )
        name = tool["name"] or "(unknown)"
        tool_counts[name] = tool_counts.get(name, 0) + 1
    local_runs = [
        json.loads(path.read_text()) | {"key": path.parent.name}
        for path in sorted(folder.glob("turns/*/run.json"))
    ]
    local_keys = {run["run_id"]: run["key"] for run in local_runs}
    runs_path = folder / "runs.json"
    run_coverage: RunCoverage
    if runs_path.exists():
        captured = json.loads(runs_path.read_text())
        records = captured["runs"]
        run_coverage = {
            "source": "runs.json",
            "complete": captured.get("complete") is True,
            "captured_at": _timestamp(captured.get("captured_at")),
        }
    else:
        records = local_runs
        run_coverage = {
            "source": "local turn receipts",
            "complete": False,
            "captured_at": None,
        }
    runs: list[RunSummary] = []
    for run in {record["run_id"]: record for record in records}.values():
        elapsed = None
        if run.get("status") in ("success", "error", "timeout", "interrupted"):
            start, end = (
                _timestamp(run.get("created_at")),
                _timestamp(run.get("updated_at")),
            )
            if start and end:
                seconds = (
                    datetime.fromisoformat(end) - datetime.fromisoformat(start)
                ).total_seconds()
                elapsed = seconds if seconds >= 0 else None
        runs.append(
            {
                "key": local_keys.get(run["run_id"]),
                "run_id": run["run_id"],
                "submitted_at": run.get("created_at"),
                "status": run.get("status"),
                "server_elapsed_seconds": elapsed,
            }
        )
    traces = _trace_summary(folder)
    return {
        "observed_model_calls": len(calls),
        "calls": calls,
        "input_tokens": sum(x["input_tokens"] for x in calls.values())
        if calls
        else None,
        "output_tokens": sum(x["output_tokens"] for x in calls.values())
        if calls
        else None,
        "incomplete_usage_ids": sorted(incomplete),
        "runs": runs,
        "run_coverage": run_coverage,
        "observed_model_messages": len(model_agents),
        "agents": agents,
        "observed_tool_calls": len(tools),
        "tool_calls": tools,
        "tool_counts": tool_counts,
        "stream_errors": errors,
        "traces": traces,
        "coverage": "captured streams only; missing or expired streams are not zero usage; observed_model_calls counts messages with usable cumulative usage",
        "cost_usd": traces["cost_usd"]
        if traces and traces["collection_status"] in (None, "available")
        else None,
        "timing": "Server elapsed is run lifetime, not model compute time. First/last observed timestamps are local capture times, not execution bounds. Trace span timing is separate; no per-agent duration is inferred from streams.",
    }


def _mapping(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _sources(folder: Path) -> dict:
    path = folder / "sources.json"
    return _mapping(json.loads(path.read_text())) if path.exists() else {}


def _cell(value: object) -> str:
    if value is None:
        return "unknown"
    return (
        escape(str(value))
        .replace("|", "&#124;")
        .replace("[", "&#91;")
        .replace("]", "&#93;")
        .replace("\n", " ")
        .replace("\r", " ")
    )


def _link(folder: Path, path: object, label: str) -> str:
    """Link only saved local evidence, never a URL or a path outside this attempt."""
    if not isinstance(path, str):
        return "unavailable"
    target = folder / path
    if not target.resolve().is_relative_to(folder.resolve()) or not target.is_file():
        return "unavailable"
    relative = target.relative_to(folder).as_posix()
    return f"[{_cell(label)}]({quote(relative, safe='/')})"


def render_report(folder: Path) -> str:
    """Summarize saved observations without publishing arguments, results or reasoning."""
    summary = summarize(folder)
    capture = _sources(folder)
    sources = _mapping(capture.get("sources"))
    attempt_path = folder / "attempt.json"
    environment = (
        json.loads(attempt_path.read_text()).get("environment", "dev")
        if attempt_path.exists()
        else "not recorded"
    )
    lines = [
        "# Conversation evidence",
        "",
        f"Environment: {_cell(environment)}.",
        "",
        f"Latest collection: {_cell(_timestamp(capture.get('collected_at')))}.",
        "",
        "Saved observations, not a quality score. Unknown counts and costs are not zero.",
        "",
        "| Source | Latest collection status | Evidence |",
        "|---|---|---|",
    ]
    for name, path in (
        ("thread", "thread.json"),
        ("stream", None),
        ("langsmith", "traces/langsmith.json"),
        ("backend", None),
    ):
        source = _mapping(sources.get(name))
        path = source.get("manifest") if name == "backend" else path
        lines.append(
            f"| {name} | {_cell(source.get('status'))} | {_link(folder, path, 'saved source')} |"
        )
    lines.extend(
        [
            "",
            "A saved file can predate the latest collection. Unavailable, partial or unrequested sources do not establish complete coverage.",
            "",
            "## Captured streams",
            "",
            summary["coverage"] + ".",
            "",
            "| Agent | Model messages | Calls with usage | Input / output tokens | Tool calls / errors | First / last observed |",
            "|---|---:|---:|---|---|---|",
        ]
    )
    for name, row in sorted(summary["agents"].items()):
        lines.append(
            f"| {_cell(name)} | {row['observed_model_messages']} | {row['observed_model_calls']} | "
            f"{_cell(row['input_tokens'])} / {_cell(row['output_tokens'])} | "
            f"{row['observed_tool_calls']} / {row['observed_tool_errors']} | "
            f"{_cell(row['first_observed_at'])} / {_cell(row['last_observed_at'])} |"
        )
    if not summary["agents"]:
        lines.append("| No observed messages | — | — | unknown | unknown | unknown |")
    lines.extend(
        [
            "",
            (
                f"Missing usable usage: {len(summary['incomplete_usage_ids'])} message(s). "
                f"Explicit stream error events: {len(summary['stream_errors'])}."
            ),
            "",
            "| Tool | Observed calls | Explicit error results | Evidence |",
            "|---|---:|---:|---|",
        ]
    )
    for name, count in sorted(summary["tool_counts"].items()):
        rows = [
            row
            for row in summary["tool_calls"].values()
            if (row["name"] or "(unknown)") == name
        ]
        failures = [
            row["error_evidence"] for row in rows if row["error_evidence"] is not None
        ]
        refs = (
            failures[:3]
            or [row["request_evidence"] or row["result_evidence"] for row in rows][:1]
        )
        links = dict.fromkeys(
            _link(folder, ref["file"], f"line {ref['line']}") for ref in refs if ref
        )
        lines.append(
            f"| {_cell(name)} | {count} | {len(failures)} | {', '.join(links)} |"
        )
    for ref in summary["stream_errors"]:
        label = f"line {ref['line']}"
        lines.append(
            f"\nStream error: {_link(folder, ref['file'], label)} at {_cell(ref['observed_at'])}."
        )
    lines.extend(
        [
            "",
            "## Server runs",
            "",
            (
                f"Source: {_cell(summary['run_coverage']['source'])}; captured {_cell(summary['run_coverage']['captured_at'])}; "
                f"list complete: {summary['run_coverage']['complete']}."
            ),
            "",
            "| Run | Status | Submitted | Server elapsed seconds |",
            "|---|---|---|---:|",
        ]
    )
    for row in summary["runs"]:
        lines.append(
            f"| {_cell(row['run_id'])} | {_cell(row['status'])} | {_cell(row['submitted_at'])} | {_cell(row['server_elapsed_seconds'])} |"
        )
    lines.extend(["", summary["timing"], "", "## LangSmith", ""])
    trace = summary["traces"]
    if trace is None:
        lines.append(
            "No saved LangSmith snapshot. Model cost and trace timing are unknown."
        )
    else:
        coverage = _mapping(trace["coverage"])
        cost_label = (
            "Saved snapshot cost"
            if trace["collection_status"] is None
            else "Current collection cost"
        )
        lines.extend(
            [
                f"Snapshot captured {_cell(trace['collected_at'])}; latest collection status: {_cell(trace['collection_status'])}.",
                "",
                (
                    f"Snapshot coverage complete: {coverage.get('complete') is True}. "
                    f"Cost accounting complete: {trace['cost_complete']}. "
                    f"{cost_label} (USD): {_cell(summary['cost_usd'])}. "
                    f"Saved known root cost (USD): {_cell(trace['observed_root_cost_usd'])}."
                ),
                "",
                trace["cost_scope"],
                "",
                trace["timing_scope"],
                "",
                "Query completion does not prove that all work was instrumented or ingested.",
                "",
                (
                    f"Coverage gaps: {_cell(', '.join(str(item) for item in coverage.get('incomplete_reasons', [])) or 'none reported')}. "
                    f"Missing root costs: {len(trace['missing_root_cost_ids'])}; unfinished/invalid roots: {len(trace['incomplete_root_ids'])}."
                    f" Unpriced LLM spans: {len(trace['missing_llm_cost_ids'])}; incomplete LLM usage spans: {len(trace['incomplete_llm_usage_ids'])}."
                ),
                "",
                "| Root span | Start / end | Seconds | Reported USD | Error observed |",
                "|---|---|---:|---:|---|",
            ]
        )
        for row in trace["spans"]:
            if row["parent_run_id"] is None:
                lines.append(
                    f"| {_cell(row['name'])} | {_cell(row['start_time'])} / {_cell(row['end_time'])} | "
                    f"{_cell(row['elapsed_seconds'])} | {_cell(row['recorded_cost_usd'])} | {row['error_observed']} |"
                )
        lines.extend(
            [
                "",
                (
                    "Trace-only model observations below are separate from stream counts; do not add them together. "
                    "Tokens sum known LLM span fields only. Duration and reported cost ranges describe individual spans, not totals; inclusive span costs are never added."
                ),
                "",
                "| Agent | LLM spans | Input / output tokens | Missing usage spans | Span seconds (min–max) | Reported USD (min–max; known/total spans) | Error spans |",
                "|---|---:|---|---:|---|---|---:|",
            ]
        )
        names = sorted(
            {
                row["agent"] or "(unknown)"
                for row in trace["spans"]
                if row["run_type"] == "llm"
            }
        )
        for name in names:
            rows = [
                row
                for row in trace["spans"]
                if row["run_type"] == "llm" and (row["agent"] or "(unknown)") == name
            ]
            known = [row for row in rows if row["usage_complete"]]
            inp = sum(row["prompt_tokens"] or 0 for row in known) if known else None
            out = sum(row["completion_tokens"] or 0 for row in known) if known else None
            seconds = [
                row["elapsed_seconds"]
                for row in rows
                if row["elapsed_seconds"] is not None
            ]
            elapsed = f"{min(seconds)}–{max(seconds)}" if seconds else "unknown"
            costs = [
                Decimal(row["recorded_cost_usd"])
                for row in rows
                if row["recorded_cost_usd"] is not None
            ]
            cost_range = (
                f"{min(costs)}–{max(costs)} ({len(costs)}/{len(rows)})"
                if costs
                else "unknown"
            )
            lines.append(
                f"| {_cell(name)} | {len(rows)} | {_cell(inp)} / {_cell(out)} | {len(rows) - len(known)} | {elapsed} | {cost_range} | {sum(row['error_observed'] for row in rows)} |"
            )
        lines.extend(
            ["", "| Trace agent / tool | Tool spans | Error spans |", "|---|---:|---:|"]
        )
        tool_groups: dict[tuple[str, str], list[TraceSpan]] = {}
        for row in trace["spans"]:
            if row["run_type"] == "tool":
                tool_groups.setdefault(
                    (row["agent"] or "(unknown)", row["name"] or "(unknown)"), []
                ).append(row)
        for (agent, name), rows in sorted(tool_groups.items()):
            lines.append(
                f"| {_cell(agent)} / {_cell(name)} | {len(rows)} | {sum(row['error_observed'] for row in rows)} |"
            )
        failures = [row for row in trace["spans"] if row["error_observed"]]
        if failures:
            lines.append(
                "\nExplicit error spans (bounded first line; full diagnostics stay in the raw snapshot):"
            )
            for row in failures:
                lines.append(
                    f"- {_cell(row['run_type'])} {_cell(row['name'])}: {_cell(row['error_preview'])} — {_link(folder, 'traces/langsmith.json', row['id'])}"
                )
    lines.extend(["", "## Backend executions", ""])
    backend = _mapping(sources.get("backend"))
    lines.extend(
        [
            f"Capture status: {_cell(backend.get('status'))}; captured {_cell(backend.get('captured_at'))}.",
            "",
            "| Execution | Execution / capture status | Native job ms | Record | Log events / pages (status) | Stream event counts (status) | Evidence |",
            "|---|---|---:|---|---|---|---|",
        ]
    )
    backend_errors: list[str] = []
    for row in backend.get("executions", []):
        record, logs, stream = (
            _mapping(row.get(key)) for key in ("record", "logs", "stream")
        )
        directory = str(row.get("directory", ""))
        record_link = _link(
            folder,
            directory + "/" + str(record.get("file", "")),
            str(record.get("status", "unknown")),
        )
        paths = [*logs.get("pages", [])[:1], stream.get("parsed"), stream.get("raw")]
        links = [
            _link(folder, directory + "/" + path, path)
            for path in paths
            if isinstance(path, str)
        ]
        counts = (
            ", ".join(
                f"{key}: {value}"
                for key, value in sorted(_mapping(stream.get("event_counts")).items())
            )
            or "unknown"
        )
        lines.append(
            f"| {_cell(row.get('execution_id'))} | {_cell(record.get('execution_status'))} / {_cell(row.get('status'))} | {_cell(stream.get('job_duration_ms'))} | {record_link} | "
            f"{_cell(logs.get('events'))} / {len(logs.get('pages', []))} ({_cell(logs.get('status'))}) | "
            f"{_cell(counts)} ({_cell(stream.get('status'))}) | {', '.join(links) or 'unavailable'} |"
        )
        if preview := _error_preview(record.get("error_reason")):
            backend_errors.append(
                f"- {_cell(row.get('execution_id'))}: {_cell(preview)} — {record_link}"
            )
    if backend_errors:
        lines.extend(
            ["", "Native Backend errors (bounded first line):", *backend_errors]
        )
    if not backend.get("executions"):
        lines.append(
            "\nNo execution capture in this collection; this does not imply that no execution occurred."
        )
    lines.extend(
        [
            "",
            (
                "Backend counts describe retained records and events, not complete runtime telemetry. "
                "Logs and persisted non-log stream events are separate; no Backend cost is inferred. "
                "Job duration is the recorded terminal job-event value, otherwise unknown."
            ),
            "",
            "Raw evidence: "
            + ", ".join(
                _link(folder, path, path)
                for path in (
                    "sources.json",
                    "metrics.json",
                    "thread.json",
                    "runs.json",
                    "traces/langsmith.json",
                )
            )
            + ".",
            "",
        ]
    )
    return "\n".join(lines)
