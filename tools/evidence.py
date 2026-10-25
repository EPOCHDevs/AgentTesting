"""Offline conversation rendering and conservative usage summaries.

Carries forward the EpochAI metric rule: cumulative snapshots must not count a
model response more than once. Raw counts have no implied dollar price.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import TypedDict

from langgraph_sdk.schema import ThreadState


class Usage(TypedDict):
    input_tokens: int
    output_tokens: int
    total_tokens: int
    agent: str | None
    model: str | None


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


def summarize(folder: Path) -> dict[str, object]:
    calls: dict[str, Usage] = {}
    event_ids: set[tuple[str, str]] = set()
    incomplete: set[str] = set()
    for path in sorted(folder.glob("streams/*/events.jsonl")):
        for line in path.read_text().splitlines():
            event = json.loads(line)
            event_id = event.get("id")
            if event_id:
                identity = (path.parent.name, event_id)
                if identity in event_ids:
                    continue
                event_ids.add(identity)
            # Other channels repeat model messages with different IDs. Only
            # the message channel owns streamed usage in the modern protocol.
            if not str(event.get("event", "")).startswith("messages"):
                continue
            data = event.get("data")
            if not isinstance(data, list) or not data or not isinstance(data[0], dict):
                continue
            message = data[0]
            usage, mid = message.get("usage_metadata"), message.get("id")
            if not usage or not mid:
                continue
            counts = [
                usage.get("input_tokens"),
                usage.get("output_tokens"),
                usage.get("total_tokens"),
            ]
            if any(type(value) is not int or value < 0 for value in counts):
                incomplete.add(mid)
                continue
            metadata = data[1] if len(data) > 1 and isinstance(data[1], dict) else {}
            response = message.get("response_metadata") or {}
            previous = calls.get(mid)
            if previous is None or counts[2] >= previous["total_tokens"]:
                calls[mid] = {
                    "input_tokens": counts[0],
                    "output_tokens": counts[1],
                    "total_tokens": counts[2],
                    "agent": message.get("name") or metadata.get("lc_agent_name"),
                    "model": response.get("model_name") or response.get("model"),
                }
                incomplete.discard(mid)
    runs = []
    for path in sorted(folder.glob("turns/*/run.json")):
        run = json.loads(path.read_text())
        elapsed = None
        if (
            run.get("status") in ("success", "error", "timeout", "interrupted")
            and run.get("updated_at")
            and run.get("created_at")
        ):
            elapsed = (
                datetime.fromisoformat(run["updated_at"])
                - datetime.fromisoformat(run["created_at"])
            ).total_seconds()
        runs.append(
            {
                "key": path.parent.name,
                "run_id": run["run_id"],
                "submitted_at": run.get("created_at"),
                "status": run.get("status"),
                "server_elapsed_seconds": elapsed,
            }
        )
    return {
        "observed_model_calls": len(calls),
        "calls": calls,
        "input_tokens": sum(int(x["input_tokens"]) for x in calls.values())
        if calls
        else None,
        "output_tokens": sum(int(x["output_tokens"]) for x in calls.values())
        if calls
        else None,
        "incomplete_usage_ids": sorted(incomplete),
        "runs": runs,
        "coverage": "captured streams only; missing or expired streams are not zero usage",
        "cost_usd": None,
        "timing": "Server elapsed is run lifetime, not model compute time. No per-agent duration is inferred.",
    }
