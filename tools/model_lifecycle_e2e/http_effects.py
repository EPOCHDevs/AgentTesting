"""Read-only fit-effect audit for the declared private local HTTP fixture.

Canonical fit phases are LogEvents in logs.jsonl. Do not sum internal progress,
events.jsonl and logs.jsonl. A missing/truncated/uncompleted stream is unknown.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import UUID


def fit_count(logs: list[dict], events: list[dict], execution: str, expected: int) -> dict:
    if not logs or not events:
        raise AssertionError("Missing retained telemetry cannot establish zero fits")
    terminals = [event for event in events if event.get("type") == "Lifecycle"
                 and event.get("operation_type") == "job" and event.get("operation_name") == execution
                 and event.get("status") in {"completed", "failed", "cancelled"}]
    if len(terminals) != 1 or terminals[0].get("status") != "completed":
        raise AssertionError("A single durable completed job terminal is required")
    phases = [event for event in logs if event.get("type") == "Log"
              and event.get("context", {}).get("model_operation") == "fit"]
    ids = [event.get("event_id") for event in phases]
    if any(not item for item in ids) or len(ids) != len(set(ids)):
        raise AssertionError("Fit phase event identities are missing or duplicated")
    started = [event for event in phases if event["context"].get("model_fit_phase") == "started"]
    completed = [event for event in phases if event["context"].get("model_fit_phase") == "completed"]
    if (len(phases) != expected * 2 or len(started) != expected or len(completed) != expected
            or [event["path"] for event in started] != [event["path"] for event in completed]):
        raise AssertionError("Retained fit effects differ from the selected owned journey")
    return {"started": len(started), "completed": len(completed), "canonical_stream": "logs.jsonl",
            "completed_job_terminal": True, "fit_events": phases}


def stable_lines(path: Path) -> tuple[list[dict], str]:
    before = path.stat()
    raw = path.read_bytes()
    after = path.stat()
    if (not raw or not raw.endswith(b"\n") or before.st_size != after.st_size
            or len(raw) != after.st_size or before.st_mtime_ns != after.st_mtime_ns):
        raise AssertionError("Retained telemetry is empty, changing or truncated")
    return [json.loads(line) for line in raw.splitlines() if line.strip()], hashlib.sha256(raw).hexdigest()


def audit(backend: Path, owner: str, receipt: Path) -> dict:
    if str(UUID(owner)) != owner:
        raise ValueError("Supply the exact disposable fixture account identity")
    journey = json.loads(receipt.read_text())
    results = {}
    for stage, field, count in (("fit", "producer_execution", 1),
                                 ("continue", "continuation_execution", 0),
                                 ("saved", "saved_consumer_execution", 0)):
        execution = journey[field]
        if str(UUID(execution)) != execution:
            raise ValueError("Use an exact accepted fixture execution UUID")
        root = backend / "research_studies" / owner / execution
        logs, log_sha = stable_lines(root / "logs.jsonl")
        events, event_sha = stable_lines(root / "events.jsonl")
        results[stage] = fit_count(logs, events, execution, count) | {
            "logs_sha256": log_sha, "events_sha256": event_sha, "execution_id": execution}
    return {"boundary": "retained canonical fit logs from the owned HTTP journey",
            "status": "passed", "executions": results,
            "scope": "One positive producer plus complete retained consumer streams; no inferred zero from missing telemetry"}
