"""Remove credentials only from their transport-owned evidence carriers.

LangGraph returns server Run.kwargs beyond its SDK Run TypedDict. EpochAI's
auth handler owns kwargs.config.configurable.langgraph_auth_user; its runtime
auth reader also supports the explicit context and legacy-header carriers
below. LangChain promotes those legacy scalar headers to LangSmith metadata.
Inputs, outputs, messages and unrelated metadata are preserved, not scanned.
"""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING

from langgraph_sdk.schema import Run

if TYPE_CHECKING:
    from langsmith.schemas import Run as TraceRun

REDACTED = "[REDACTED]"
_LEGACY_HEADERS = ("authorization", "Authorization", "x-refresh-token", "X-Refresh-Token")
_CONTEXT_CREDENTIALS = ("bearer_token", "authorization", "Authorization", "refresh_token")


def _object(parent: object, key: str) -> dict[str, object] | None:
    if isinstance(parent, dict) and isinstance(parent.get(key), dict):
        return parent[key]
    return None


def _redact(owner: dict[str, object] | None, fields: tuple[str, ...]) -> None:
    if owner is not None:
        for field in fields:
            if field in owner and owner[field] is not None:
                owner[field] = REDACTED


def redact_run(run: Run) -> Run:
    """Copy an SDK run, retaining its envelope and payloads except auth values."""
    saved = deepcopy(run)
    kwargs = _object(saved, "kwargs")
    configurable = _object(_object(kwargs, "config"), "configurable")
    _redact(_object(configurable, "langgraph_auth_user"), ("authorization", "refresh_token"))
    _redact(configurable, _LEGACY_HEADERS)
    _redact(_object(kwargs, "context"), _CONTEXT_CREDENTIALS)
    return saved


def redact_trace_run(run: TraceRun) -> TraceRun:
    """Copy the trace SDK model; only legacy auth-header metadata is removed."""
    extra = deepcopy(run.extra)
    _redact(_object(extra, "metadata"), _LEGACY_HEADERS)
    return run.model_copy(update={"extra": extra})
