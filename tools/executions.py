"""Read Backend execution evidence through authenticated HTTP endpoints only.

Contract checked against Backend 5bf74186: /logs contains only LogEvent;
/stream replays non-log persisted events through the first job terminal.
Progress history retains 25-percent milestones, not every live update.
"""

from __future__ import annotations

import asyncio
import codecs
import hashlib
import json
import math
import tempfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx

TERMINAL = {"completed", "failed", "cancelled"}
STATUSES = TERMINAL | {"pending", "queued", "running"}
PAGE_SIZE = 1000
MAX_LOG_PAGES = 1000
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_STREAM_BYTES = 64 * 1024 * 1024
EXECUTION_SECONDS = 60
STREAM_SECONDS = 20


def _save(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n")


class CaptureError(Exception):
    pass


async def _json(client, url, headers, path, *, params=None):
    body = bytearray()
    status_code = None
    execution_record = path.name == "execution.json"
    try:
        async with client.stream(
            "GET",
            url,
            headers=headers,
            params=params,
            timeout=20,
            follow_redirects=False,
        ) as response:
            status_code = response.status_code
            async for chunk in response.aiter_bytes():
                remaining = MAX_RESPONSE_BYTES - len(body)
                body.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    raise CaptureError("response byte limit reached")
        if not 200 <= status_code < 300:
            raise CaptureError(f"HTTP {status_code}")
        value = json.loads(body)
        if execution_record:
            if (
                not isinstance(value, dict)
                or not isinstance(value.get("id"), str)
                or not isinstance(value.get("status"), str)
                or value["status"] not in STATUSES
            ):
                raise CaptureError("execution response identity or status is invalid")
            source = value.get("artifact_source")
            if source is not None and not isinstance(source, dict):
                raise CaptureError("execution artifact source is invalid")
            if isinstance(source, dict) and "signed_query" in source:
                source["signed_query"] = "[REDACTED]"
    except (
        CaptureError,
        httpx.HTTPError,
        TimeoutError,
        ValueError,
        asyncio.CancelledError,
    ) as error:
        if execution_record:
            # An incomplete/error record may contain minted CDN credentials.
            # Only parsed successful records can be redacted safely; never save
            # their raw failure body, including partial/oversized JSON prefixes.
            _save(
                path,
                {
                    "capture_error": {
                        "error_type": type(error).__name__,
                        "http_status": status_code,
                        "received_bytes": len(body),
                        "body_omitted": True,
                    }
                },
            )
        else:
            path.write_bytes(body)
        raise
    if execution_record:
        _save(path, value)
    else:
        path.write_bytes(body)
    return value


class _Events:
    """Parse the Backend's SSE framing while retaining the untouched response."""

    def __init__(self, output):
        self.output = output
        self.decoder = codecs.getincrementaldecoder("utf-8")()
        self.buffer = ""
        self.kind, self.event_id, self.data = "message", "", []
        self.counts = Counter()
        self.terminal = False
        self.job_duration_ms = None

    def feed(self, chunk, *, final=False):
        self.buffer += self.decoder.decode(chunk, final=final)
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            line = line.removesuffix("\r")
            if not line:
                if self.data:
                    payload = json.loads("\n".join(self.data))
                    if not isinstance(payload, dict):
                        raise CaptureError("SSE payload is not an object")
                    if self.kind != "status" and self.event_id != payload.get(
                        "event_id"
                    ):
                        raise CaptureError("SSE id differs from payload event_id")
                    self.counts[self.kind] += 1
                    self.output.write(
                        json.dumps(
                            {"event": self.kind, "id": self.event_id, "data": payload}
                        )
                        + "\n"
                    )
                    if (
                        self.kind == "lifecycle"
                        and payload.get("operation_type") == "job"
                        and isinstance(payload.get("status"), str)
                        and payload["status"] in TERMINAL
                    ):
                        duration = payload.get("duration_ms")
                        if (
                            not self.terminal
                            and isinstance(duration, (int, float))
                            and not isinstance(duration, bool)
                            and duration >= 0
                            and (isinstance(duration, int) or math.isfinite(duration))
                        ):
                            self.job_duration_ms = duration
                        self.terminal = True
                self.kind, self.event_id, self.data = "message", "", []
            elif not line.startswith(":"):
                key, _, value = line.partition(":")
                value = value.removeprefix(" ")
                if key == "event":
                    self.kind = value
                elif key == "id":
                    self.event_id = value
                elif key == "data":
                    self.data.append(value)
        if final and (
            self.buffer or self.data or self.event_id or self.kind != "message"
        ):
            raise CaptureError("SSE ended inside a frame")


async def _logs(client, url, headers, directory, result):
    offset, expected_total = 0, None
    result.update(status="partial", pages=[], events=0)
    for number in range(MAX_LOG_PAGES):
        path = directory / f"logs-{number:05d}.json"
        result["pages"].append(path.name)
        try:
            page = await _json(
                client,
                url + "/logs",
                headers,
                path,
                params={"limit": PAGE_SIZE, "offset": offset},
            )
        except (
            CaptureError,
            httpx.HTTPError,
            TimeoutError,
            ValueError,
            asyncio.CancelledError,
        ):
            result["partial_page"] = {
                "file": path.name,
                "received_bytes": path.stat().st_size,
            }
            raise
        pagination = page.get("pagination", {}) if isinstance(page, dict) else {}
        events = page.get("events") if isinstance(page, dict) else None
        if (
            not isinstance(events, list)
            or not isinstance(pagination, dict)
            or any(
                type(pagination.get(k)) is not int or pagination[k] < 0
                for k in ("total", "limit", "offset")
            )
            or type(pagination.get("has_more")) is not bool
            or pagination["offset"] != offset
        ):
            raise CaptureError("invalid log pagination")
        if expected_total is not None and expected_total != pagination["total"]:
            raise CaptureError("log total changed during pagination")
        expected_total = pagination["total"]
        offset += len(events)
        result.update(events=offset, total=expected_total)
        if not pagination["has_more"]:
            if offset != expected_total:
                raise CaptureError("log count differs from pagination total")
            result["status"] = "complete"
            return
        if not events or offset >= expected_total:
            raise CaptureError("log pagination made no progress")
    raise CaptureError("log page limit reached")


async def _stream(client, url, headers, directory, result):
    result.update(status="partial", raw="stream.sse", parsed="events.jsonl", bytes=0)
    with (
        (directory / "stream.sse").open("wb") as raw,
        (directory / "events.jsonl").open("w") as parsed,
    ):
        events = _Events(parsed)
        try:
            async with asyncio.timeout(STREAM_SECONDS):
                async with client.stream(
                    "GET",
                    url + "/stream",
                    headers=headers,
                    params={"include_historical": "true"},
                    timeout=15,
                    follow_redirects=False,
                ) as response:
                    if response.status_code != 200:
                        raise CaptureError(f"HTTP {response.status_code}")
                    if (
                        response.headers.get("content-type", "").split(";")[0]
                        != "text/event-stream"
                    ):
                        raise CaptureError("unexpected SSE content type")
                    async for chunk in response.aiter_bytes():
                        remaining = MAX_STREAM_BYTES - result["bytes"]
                        raw.write(chunk[:remaining])
                        result["bytes"] += min(len(chunk), remaining)
                        if len(chunk) > remaining:
                            raise CaptureError("stream byte limit reached")
                        events.feed(chunk)
                    events.feed(b"", final=True)
            # The first status frame can already be terminal: only EOF ends replay.
            if not events.terminal:
                raise CaptureError("served history has no terminal job lifecycle event")
            result["status"] = "complete"
        finally:
            result.update(
                event_counts=dict(events.counts),
                terminal_lifecycle_seen=events.terminal,
                job_duration_ms=events.job_duration_ms,
            )


async def _phase(result, action):
    try:
        await action
    except (CaptureError, httpx.HTTPError, TimeoutError, ValueError) as error:
        has_bytes = (
            result.get("bytes")
            or result.get("events")
            or result.get("partial_page", {}).get("received_bytes")
        )
        result["status"] = "partial" if has_bytes else "unavailable"
        result["reason"] = (
            str(error) if isinstance(error, CaptureError) else type(error).__name__
        )


async def collect(
    folder, backend_url, bearer, execution_ids: list[str], *, client=None
):
    """Save a bounded capture and return its manifest; never launch or cancel jobs.

    ``folder`` is the conversation folder. IDs must come from exact caller-owned
    state; this module does not infer identities from arbitrary text. A supplied
    AsyncClient remains open. All paths in each execution entry are relative to
    that entry's directory, which is relative to ``folder``.
    """
    parsed = urlsplit(backend_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError(
            "backend_url must be an HTTP origin without credentials or query"
        )
    if not isinstance(bearer, str) or not bearer.strip():
        raise ValueError("Backend bearer token is required")
    if any(
        not isinstance(value, str) or not value.strip() or value != value.strip()
        for value in execution_ids
    ):
        raise ValueError("execution IDs must be exact nonblank strings")
    folder = Path(folder).resolve()
    root = folder / "backend"
    root.mkdir(parents=True, exist_ok=True)
    capture = Path(tempfile.mkdtemp(prefix="capture-", dir=root))
    manifest = {
        "captured_at": datetime.now(UTC).isoformat(),
        "backend_url": backend_url,
        "manifest": str((capture / "manifest.json").relative_to(folder)),
        "scope": {
            "logs": "LogEvent only; all reported pages",
            "stream": "Persisted non-log history through first job terminal; progress at 25-percent milestones",
            "limits": "No server history count or completeness guarantee; failures may lose diagnostics after staging retention",
            "cost": "No billing or model-token cost endpoint is read",
        },
        "limits": {
            "execution_seconds": EXECUTION_SECONDS,
            "stream_seconds": STREAM_SECONDS,
            "response_bytes": MAX_RESPONSE_BYTES,
            "stream_bytes": MAX_STREAM_BYTES,
            "log_pages": MAX_LOG_PAGES,
            "log_page_size": PAGE_SIZE,
        },
        "executions": [],
    }
    headers = {
        "Authorization": bearer if bearer.startswith("Bearer ") else "Bearer " + bearer
    }
    owned = client is None
    client = client or httpx.AsyncClient()
    try:
        for execution_id in dict.fromkeys(execution_ids):
            directory = capture / hashlib.sha256(execution_id.encode()).hexdigest()
            directory.mkdir()
            row = {
                "execution_id": execution_id,
                "directory": str(directory.relative_to(folder)),
                "record": {
                    "status": "unavailable",
                    "file": "execution.json",
                    "error_reason": None,
                },
                "logs": {"status": "skipped"},
                "stream": {"status": "skipped", "job_duration_ms": None},
            }
            manifest["executions"].append(row)
            url = (
                backend_url.rstrip("/")
                + "/api/v1/studies/executions/"
                + quote(execution_id, safe="")
            )
            try:
                async with asyncio.timeout(EXECUTION_SECONDS):
                    record = await _json(
                        client, url, headers, directory / "execution.json"
                    )
                    if (
                        not isinstance(record, dict)
                        or record.get("id") != execution_id
                        or not isinstance(record.get("status"), str)
                        or record["status"] not in STATUSES
                    ):
                        raise CaptureError(
                            "execution response identity or status mismatch"
                        )
                    # This response can mint CDN credentials; do not retain those.
                    source = record.get("artifact_source")
                    if isinstance(source, dict) and "signed_query" in source:
                        row["record"]["redacted_fields"] = [
                            "artifact_source.signed_query"
                        ]
                    row["record"].update(
                        status="complete",
                        execution_status=record["status"],
                        error_reason=record.get("error_reason")
                        if isinstance(record.get("error_reason"), str)
                        else None,
                    )
                    await _phase(
                        row["logs"], _logs(client, url, headers, directory, row["logs"])
                    )
                    if record["status"] in TERMINAL:
                        await _phase(
                            row["stream"],
                            _stream(client, url, headers, directory, row["stream"]),
                        )
                    else:
                        row["logs"]["snapshot_only"] = True
                        row["stream"]["reason"] = (
                            "execution is not terminal; live stream skipped"
                        )
            except (CaptureError, httpx.HTTPError, TimeoutError, ValueError) as error:
                row["error"] = (
                    str(error)
                    if isinstance(error, CaptureError)
                    else type(error).__name__
                )
            row["status"] = (
                "complete"
                if all(
                    row[key]["status"] == "complete"
                    for key in ("record", "logs", "stream")
                )
                else "partial"
            )
            _save(capture / "manifest.json", manifest)
    finally:
        if owned:
            await client.aclose()
    _save(capture / "manifest.json", manifest)
    return manifest
