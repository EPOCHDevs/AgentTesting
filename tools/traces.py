"""Optional, read-only LangSmith evidence collection for one research thread.

The SDK owns the Run schema. Snapshots retain its records, including nullable
provider-estimated costs; this module never rates tokens or adds overlapping
span costs. API query completion is not proof of tracing/ingestion completeness.

Reads use LangSmith's v2 run and trace queries; the v1 endpoints stop working on
2027-01-31. A v2 run reports its own usage only, so trace totals come from the
provider's trace aggregates. Every query is bounded by an explicit time window,
because v2 otherwise searches only the last 24 hours.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TypedDict
from urllib.parse import urlsplit
from uuid import uuid4

from tools.evidence_redaction import redact_trace_run

if TYPE_CHECKING:
    from langsmith import Client
    from langsmith.types import Run


@dataclass(frozen=True)
class TraceConfig:
    endpoint: str
    project: str
    api_key: str = field(repr=False)
    workspace_id: str | None = None

    def __post_init__(self) -> None:
        parts = urlsplit(self.endpoint)
        if (
            parts.scheme != "https"
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
        ):
            raise ValueError(
                "LANGSMITH_ENDPOINT must be an HTTPS URL without credentials or query strings."
            )
        if not self.project.strip() or not self.api_key.strip():
            raise ValueError(
                "Set explicit LANGSMITH_PROJECT and LANGSMITH_API_KEY values."
            )

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> TraceConfig:
        env = os.environ if environ is None else environ
        required = ("LANGSMITH_ENDPOINT", "LANGSMITH_PROJECT", "LANGSMITH_API_KEY")
        missing = [name for name in required if not env.get(name, "").strip()]
        if missing:
            raise ValueError("Trace collection requires " + ", ".join(missing) + ".")
        return cls(
            endpoint=env["LANGSMITH_ENDPOINT"],
            project=env["LANGSMITH_PROJECT"],
            api_key=env["LANGSMITH_API_KEY"],
            workspace_id=env.get("LANGSMITH_WORKSPACE_ID") or None,
        )


@dataclass(frozen=True)
class TraceWindow:
    """Run start times to search. v2 defaults to the last day when omitted."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("Trace window bounds must be timezone-aware.")
        if self.start >= self.end:
            raise ValueError("Trace window must start before it ends.")


class TraceSource(TypedDict):
    provider: Literal["langsmith"]
    endpoint: str
    project: str
    project_id: str | None
    workspace_id: str | None


class TraceQuery(TypedDict):
    trace_id: str
    complete: bool
    run_count: int
    error_type: str | None


class RejectedRun(TypedDict):
    run_id: str
    reason: str


class TraceTotals(TypedDict):
    total_tokens: int | None
    total_cost: str | None
    first_token_time: str | None


class TraceCoverage(TypedDict):
    complete: bool
    root_query_complete: bool
    root_query_error_type: str | None
    aggregate_query_complete: bool
    aggregate_query_error_type: str | None
    trace_queries: list[TraceQuery]
    rejected_runs: list[RejectedRun]
    incomplete_reasons: list[str]
    open_run_ids: list[str]
    expected_run_ids: list[str]
    unmatched_expected_run_ids: list[str]
    scope: str


class TraceSnapshot(TypedDict):
    schema_version: Literal[2]
    thread_id: str
    collected_at: str
    source: TraceSource
    window: dict[str, str]
    root_run_ids: list[str]
    runs: list[Run]
    trace_aggregates: dict[str, TraceTotals]
    coverage: TraceCoverage


# Source-owned fields requested from the API, not a client-side Run replica.
RUN_SELECTS = (
    "ID",
    "TRACE_ID",
    "PARENT_RUN_IDS",
    "PROJECT_ID",
    "THREAD_ID",
    "NAME",
    "RUN_TYPE",
    "START_TIME",
    "END_TIME",
    "LATENCY_SECONDS",
    "FIRST_TOKEN_TIME",
    "STATUS",
    "ERROR",
    "EXTRA",
    "METADATA",
    "INPUTS",
    "OUTPUTS",
    "EVENTS",
    "TAGS",
    "DOTTED_ORDER",
    "APP_PATH",
    "PROMPT_TOKENS",
    "COMPLETION_TOKENS",
    "TOTAL_TOKENS",
    "PROMPT_TOKEN_DETAILS",
    "COMPLETION_TOKEN_DETAILS",
    "PROMPT_COST",
    "COMPLETION_COST",
    "TOTAL_COST",
    "PROMPT_COST_DETAILS",
    "COMPLETION_COST_DETAILS",
)

# Trace-wide sums from the provider; v2 run costs no longer include children.
AGGREGATE_SELECTS = ("ID", "TRACE_ID", "TOTAL_TOKENS", "TOTAL_COST", "FIRST_TOKEN_TIME")


def parent_id(run: Run) -> str | None:
    """The immediate parent: the last ancestor in v2's root-first list."""
    ancestors = run.parent_run_ids or []
    return str(ancestors[-1]) if ancestors else None


def run_metadata(run: Run) -> Mapping[str, object]:
    if isinstance(run.metadata, Mapping):
        return run.metadata
    extra = run.extra if isinstance(run.extra, Mapping) else {}
    metadata = extra.get("metadata")
    return metadata if isinstance(metadata, Mapping) else {}


def _snapshot_json(snapshot: TraceSnapshot) -> str:
    # model_dump preserves the SDK's Decimal serialization and explicit nulls.
    payload = {
        **snapshot,
        "runs": [run.model_dump(mode="json") for run in snapshot["runs"]],
    }
    return json.dumps(payload, indent=2) + "\n"


def _save(folder: Path, snapshot: TraceSnapshot) -> None:
    target = folder / "traces"
    captures = target / "captures"
    captures.mkdir(parents=True, exist_ok=True)
    content = _snapshot_json(snapshot)
    # Preserve earlier observations, including partial failures, on every pull.
    (captures / f"{uuid4()}.json").write_text(content)
    with tempfile.NamedTemporaryFile(mode="w", dir=target, delete=False) as stream:
        stream.write(content)
        temporary = Path(stream.name)
    temporary.replace(target / "langsmith.json")


async def collect(
    folder: Path,
    thread_id: str,
    *,
    window: TraceWindow,
    config: TraceConfig | None = None,
    client: Client | None = None,
    expected_run_ids: Sequence[str] = (),
) -> TraceSnapshot:
    """Collect every accessible root and descendant for an exact thread/project.

    Only project, run and trace reads are used. expected_run_ids may contain
    local LangGraph run receipt IDs; roots match them by Run.id or explicit
    root metadata.run_id. A missing match is reported, never silently treated
    as a zero-cost turn.
    """
    if not thread_id.strip():
        raise ValueError("A nonempty thread ID is required.")
    config = config or TraceConfig.from_env()
    owns_client = client is None
    if client is None:
        try:
            from langsmith import Client
        except ImportError as error:
            raise RuntimeError(
                "Install langsmith==0.14.17 to enable trace collection."
            ) from error
        client = Client(
            api_url=config.endpoint,
            api_key=config.api_key,
            workspace_id=config.workspace_id,
            auto_batch_tracing=False,
            timeout_ms=30_000,
        )
    try:
        snapshot = await _collect(client, config, thread_id, window, expected_run_ids)
        snapshot["runs"] = [redact_trace_run(run) for run in snapshot["runs"]]
        _save(folder, snapshot)
        return snapshot
    finally:
        if owns_client:
            client.close()


async def _collect(
    client: Client,
    config: TraceConfig,
    thread_id: str,
    window: TraceWindow,
    expected_run_ids: Sequence[str],
) -> TraceSnapshot:
    coverage: TraceCoverage = {
        "complete": False,
        "root_query_complete": False,
        "root_query_error_type": None,
        "aggregate_query_complete": False,
        "aggregate_query_error_type": None,
        "trace_queries": [],
        "rejected_runs": [],
        "incomplete_reasons": [],
        "open_run_ids": [],
        "expected_run_ids": sorted(set(expected_run_ids)),
        "unmatched_expected_run_ids": [],
        "scope": "Query-exhausted provider snapshot within the recorded time window only. Sampling, ingestion delay, untraced work and unavailable historical runs cannot be ruled out. Costs are LangSmith estimates, not billing receipts. Span durations overlap and must not be summed as wall time.",
    }
    snapshot: TraceSnapshot = {
        "schema_version": 2,
        "thread_id": thread_id,
        "collected_at": datetime.now(UTC).isoformat(),
        "source": {
            "provider": "langsmith",
            "endpoint": config.endpoint,
            "project": config.project,
            "project_id": None,
            "workspace_id": config.workspace_id,
        },
        "window": {
            "min_start_time": window.start.isoformat(),
            "max_start_time": window.end.isoformat(),
        },
        "root_run_ids": [],
        "runs": [],
        "trace_aggregates": {},
        "coverage": coverage,
    }
    roots: dict[str, Run] = {}

    def reject(run: Run, reason: str) -> None:
        coverage["rejected_runs"].append({"run_id": str(run.id), "reason": reason})

    try:
        project_id = str((await client.aread_project(project_name=config.project)).id)
        snapshot["source"]["project_id"] = project_id
        # JSON quoting is for the LangSmith filter language, never shell text.
        root_filter = (
            'and(eq(metadata_key,"thread_id"),eq(metadata_value,'
            + json.dumps(thread_id)
            + "))"
        )
        async for run in client.runs.query(
            project_ids=[project_id],
            is_root=True,
            filter=root_filter,
            selects=list(RUN_SELECTS),
            min_start_time=window.start,
            max_start_time=window.end,
        ):
            if str(run.project_id) != project_id:
                reject(run, "project_mismatch")
            elif parent_id(run) is not None:
                reject(run, "root_has_parent")
            elif run_metadata(run).get("thread_id") != thread_id:
                reject(run, "thread_metadata_mismatch")
            else:
                roots[str(run.id)] = run
        coverage["root_query_complete"] = True
    except Exception as error:
        # Exception text can contain request headers/credentials; retain its type.
        coverage["root_query_error_type"] = type(error).__name__
        coverage["incomplete_reasons"].append("root_query_failed")

    trace_ids = sorted({str(run.trace_id) for run in roots.values()})
    if trace_ids:
        try:
            async for trace in client.traces.query(
                project_id=snapshot["source"]["project_id"],
                trace_ids=trace_ids,
                selects=list(AGGREGATE_SELECTS),
                min_start_time=window.start,
                max_start_time=window.end,
            ):
                totals = trace.trace_aggregates
                if totals is None:
                    continue
                snapshot["trace_aggregates"][str(trace.root_run.trace_id)] = {
                    "total_tokens": totals.total_tokens,
                    "total_cost": None
                    if totals.total_cost is None
                    else str(totals.total_cost),
                    "first_token_time": None
                    if totals.first_token_time is None
                    else str(totals.first_token_time),
                }
            coverage["aggregate_query_complete"] = True
        except Exception as error:
            coverage["aggregate_query_error_type"] = type(error).__name__
            coverage["incomplete_reasons"].append("aggregate_query_failed")
        for trace_id in trace_ids:
            if trace_id not in snapshot["trace_aggregates"]:
                coverage["incomplete_reasons"].append("missing_trace_aggregates:" + trace_id)

    accepted = dict(roots)
    for trace_id in trace_ids:
        trace_roots = {
            rid for rid, run in roots.items() if str(run.trace_id) == trace_id
        }
        if len(trace_roots) != 1:
            coverage["incomplete_reasons"].append("ambiguous_trace_roots:" + trace_id)
        query: TraceQuery = {
            "trace_id": trace_id,
            "complete": False,
            "run_count": 0,
            "error_type": None,
        }
        candidates: dict[str, Run] = {}
        try:
            async for run in client.runs.query(
                project_ids=[snapshot["source"]["project_id"]],
                trace_id=trace_id,
                selects=list(RUN_SELECTS),
                min_start_time=window.start,
                max_start_time=window.end,
            ):
                if str(run.project_id) != snapshot["source"]["project_id"]:
                    reject(run, "project_mismatch")
                elif str(run.trace_id) != trace_id:
                    reject(run, "trace_mismatch")
                elif parent_id(run) is None and str(run.id) not in roots:
                    reject(run, "unmatched_root")
                elif str(run.id) in roots and (
                    parent_id(run) is not None
                    or run_metadata(run).get("thread_id") != thread_id
                    or str(roots[str(run.id)].trace_id) != trace_id
                ):
                    reject(run, "root_identity_changed")
                else:
                    candidates[str(run.id)] = run
            query["complete"] = True
        except Exception as error:
            query["error_type"] = type(error).__name__
            coverage["incomplete_reasons"].append("trace_query_failed:" + trace_id)
        # Pagination order is not guaranteed. Resolve the ancestry after reading
        # all pages, and never accept an orphan, cycle or unrelated root.
        connected = set(trace_roots)
        remaining = dict(candidates)
        while remaining:
            ready = [
                rid
                for rid, run in remaining.items()
                if rid in connected or parent_id(run) in connected
            ]
            if not ready:
                break
            for rid in ready:
                accepted[rid] = remaining.pop(rid)
                connected.add(rid)
        for run in remaining.values():
            reject(run, "missing_or_untrusted_parent")
        if any(rid not in candidates for rid in connected if rid in roots):
            coverage["incomplete_reasons"].append("trace_missing_root:" + trace_id)
        query["run_count"] = len(connected)
        coverage["trace_queries"].append(query)

    snapshot["root_run_ids"] = sorted(roots)
    snapshot["runs"] = sorted(
        accepted.values(), key=lambda run: (run.start_time, str(run.id))
    )
    coverage["open_run_ids"] = sorted(
        str(run.id) for run in accepted.values() if run.end_time is None
    )
    identified = set(roots)
    identified.update(
        str(run_metadata(run)["run_id"])
        for run in roots.values()
        if run_metadata(run).get("run_id")
    )
    coverage["unmatched_expected_run_ids"] = sorted(set(expected_run_ids) - identified)
    if not roots:
        coverage["incomplete_reasons"].append("no_matching_roots")
    if coverage["rejected_runs"]:
        coverage["incomplete_reasons"].append("rejected_untrusted_runs")
    if coverage["open_run_ids"]:
        coverage["incomplete_reasons"].append("unfinished_spans")
    if coverage["unmatched_expected_run_ids"]:
        coverage["incomplete_reasons"].append("unmatched_expected_runs")
    coverage["complete"] = not coverage["incomplete_reasons"]
    return snapshot
