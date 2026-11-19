"""Server-made PDFs through Platform's export routes, as the signed-in owner (no browser).

EpochPlatform docs/contracts/exports.md (ENG-3029): POST starts an export (202, `queued`), GET
reads its state, DELETE cancels a queued one. The Terminal reads the state every 2 s for at most
2 minutes, and so does the suite. A ready export carries a presigned `download_url`; it is fetched
without the bearer and never recorded: observations keep the URL's place as `REDACTED`, and an
HTTP error is recorded by its type only, because httpx puts the URL in its messages.

Each function returns a JSON-serializable observation; `checks` judges it.
"""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import httpx

from .browser import pdf_pages, pdf_text, withhold_urls  # noqa: F401 — the runner withholds URLs with it.

EXPORTS_PATH = "/api/v1/platform/exports"
POLL_INTERVAL_S = 2.0
POLL_TIMEOUT_S = 120.0
MAX_BYTES = 50 * 1024 * 1024
FINAL = ("ready", "failed", "cancelled")
REDACTED = "[signed URL withheld]"

Request = Callable[..., httpx.Response]


@dataclass(frozen=True)
class Answer:
    """One answer from an export route: its status, JSON body and Platform's envelope message."""

    status: int
    body: Mapping[str, object] | None = None
    message: str | None = None
    retry_after: str | None = None
    error: str | None = None

    @classmethod
    def of(cls, response: httpx.Response) -> Answer:
        try:
            body = response.json() if response.content else None
        except ValueError:
            body = None
        if not isinstance(body, Mapping):
            body = None
        message = body.get("message") if body is not None and response.status_code >= 400 else None
        return cls(
            response.status_code,
            body if response.status_code < 400 else None,
            message if isinstance(message, str) else None,
            response.headers.get("retry-after"),
        )

    def record(self) -> dict[str, object]:
        value: dict[str, object] = {"status": self.status}
        for key in ("message", "retry_after", "error"):
            if getattr(self, key):
                value[key] = getattr(self, key)
        return value


def public(body: Mapping[str, object] | None) -> dict[str, object] | None:
    """An export as evidence may keep it: the signed download address withheld."""
    if body is None:
        return None
    value = dict(body)
    if value.get("download_url"):
        value["download_url"] = REDACTED
    return value


class Exports:
    """Platform's export routes, through the Backend client's signed-in request (its bearer)."""

    def __init__(self, request: Request):
        self._request = request

    def _call(self, method: str, path: str, **kwargs: object) -> Answer:
        try:
            return Answer.of(self._request(method, path, **kwargs))
        except httpx.HTTPError as error:  # Its message can carry the URL: keep the type only.
            return Answer(0, error=type(error).__name__)

    def create(self, run_id: str, paper: str = "a4") -> Answer:
        return self._call("POST", EXPORTS_PATH, json={"execution_id": run_id, "format": "pdf", "paper": paper})

    def get(self, export_id: str) -> Answer:
        return self._call("GET", f"{EXPORTS_PATH}/{export_id}")

    def cancel(self, export_id: str) -> Answer:
        return self._call("DELETE", f"{EXPORTS_PATH}/{export_id}")


def poll(
    get: Callable[[str], Answer],
    started: Mapping[str, object],
    *,
    began: float,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    interval: float = POLL_INTERVAL_S,
    timeout: float = POLL_TIMEOUT_S,
) -> tuple[Mapping[str, object], dict[str, object]]:
    """Reads a queued export every `interval` s until it is final, gone (404) or `timeout` s have
    passed since `began` (the POST). Other errors are read again, as the Terminal does.

    Answers the last export read (signed URL included; the caller withholds it) and the record:
    each state with when it was first seen, the polls, the errors and how the wait ended.
    """
    export_id = str(started.get("export_id"))
    current = started
    transitions = [{"state": current.get("state"), "at_s": round(clock() - began, 1)}]
    errors: list[dict[str, object]] = []
    polls = 0
    outcome = "final"
    while current.get("state") not in FINAL:
        left = timeout - (clock() - began)
        if left <= 0:
            outcome = "timeout"
            break
        sleep(min(interval, left))
        answer = get(export_id)
        polls += 1
        at = round(clock() - began, 1)
        if answer.status == 200 and answer.body is not None and answer.body.get("state"):
            if answer.body.get("state") != current.get("state"):
                transitions.append({"state": answer.body.get("state"), "at_s": at})
            current = answer.body
            continue
        errors.append({**answer.record(), "at_s": at})
        if answer.status == 404:
            outcome = "gone"
            break
    record = {
        "transitions": transitions,
        "polls": polls,
        "errors": errors,
        "outcome": outcome,
        "elapsed_s": round(clock() - began, 1),
    }
    return current, record


def download(
    url: str, limit: int = MAX_BYTES, transport: httpx.BaseTransport | None = None
) -> tuple[dict[str, object], bytes | None]:
    """The file at a presigned address, from a fresh client with no Authorization header.

    Stops reading past `limit`. Answers what the checks need and the bytes (None when unread).
    """
    started = time.monotonic()
    info: dict[str, object] = {}
    data = bytearray()
    try:
        with httpx.Client(timeout=httpx.Timeout(30, read=120), follow_redirects=False, transport=transport) as client:
            with client.stream("GET", url) as response:
                info["status"] = response.status_code
                info["content_type"] = response.headers.get("content-type")
                info["content_disposition"] = response.headers.get("content-disposition")
                if response.status_code == 200:
                    for chunk in response.iter_bytes():
                        data += chunk
                        if len(data) > limit:
                            info["over_limit"] = True
                            break
    except httpx.HTTPError as error:
        info["error"] = type(error).__name__
    info["bytes"] = len(data)
    info["elapsed_s"] = round(time.monotonic() - started, 1)
    return info, (bytes(data) if info.get("status") == 200 and not info.get("error") else None)


def pdf_facts(data: bytes) -> dict[str, object]:
    return {"pdf_header": data[:5] == b"%PDF-", "pages": pdf_pages(data), "bytes": len(data)}


def export_run(
    exports: Exports,
    run_id: str,
    out: Path,
    *,
    paper: str = "a4",
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    fetch: Callable[[str], tuple[dict[str, object], bytes | None]] = download,
    text: Callable[[Path], str | None] = pdf_text,
) -> dict[str, object]:
    """One server PDF of a run: POST, poll, then download, check and keep the file as evidence."""
    began = clock()
    answer = exports.create(run_id, paper)
    observed: dict[str, object] = {
        "paper": paper,
        "post": {**answer.record(), "at_s": round(clock() - began, 1)},
    }
    if answer.status != 202 or answer.body is None:
        return observed
    observed["export_id"] = answer.body.get("export_id")
    final, record = poll(exports.get, answer.body, began=began, clock=clock, sleep=sleep)
    observed.update(record)
    observed["export"] = public(final)
    if record["outcome"] == "timeout":
        # As the Terminal does: past its deadline it tells the server it needn't finish.
        observed["cancelled_after_timeout"] = exports.cancel(str(answer.body.get("export_id"))).record()
    url = final.get("download_url")
    if final.get("state") == "ready" and isinstance(url, str) and url:
        info, data = fetch(url)
        if data is not None:
            info.update(pdf_facts(data))
            if not info.get("over_limit"):
                path = out / f"server-export-{paper}.pdf"
                path.write_bytes(data)
                info["path"] = path.name
                observed["text"] = text(path)
        observed["download"] = info
    return observed


def cancel_probe(exports: Exports, run_id: str, paper: str = "a4") -> dict[str, object]:
    """Starts an export and cancels it at once: no GET comes between, so it is still queued
    when DELETE arrives (Platform finalises a record only on a read). Then reads its state."""
    answer = exports.create(run_id, paper)
    observed: dict[str, object] = {"post": answer.record()}
    if answer.status != 202 or answer.body is None:
        return observed
    export_id = str(answer.body.get("export_id"))
    observed["export_id"] = export_id
    observed["state_before"] = answer.body.get("state")
    observed["delete"] = exports.cancel(export_id).record()
    after = exports.get(export_id)
    observed["after"] = {**after.record(), "state": (after.body or {}).get("state")}
    return observed


def other_owner_probe(other: Exports | None, export_id: str | None) -> dict[str, object]:
    """Another owner's read of this owner's export: Platform answers 404, as for an unknown ID.
    The suite signs in as one owner, so without a second identity nothing is tried."""
    if other is None:
        return {"second_identity": False}
    if not export_id:
        return {"second_identity": True, "export_id": None}
    return {"second_identity": True, "export_id": export_id, "get": other.get(export_id).record()}


def cost_statement(posts: Sequence[Mapping[str, object]]) -> str:
    """The Cost section's sentence on server PDFs: how many the suite asked for and what they cost."""
    if not posts:
        return ""
    answers = Counter(str(p.get("status") or "no answer") for p in posts)
    made = answers.get("202", 0)
    summary = ", ".join(f"{count}× {status}" for status, count in sorted(answers.items()))
    return (
        f" Server PDF: the suite asked Platform for {len(posts)} exports ({summary}); {made} were made. "
        "A server export prints a run's saved dashboard in a headless browser on Platform: it makes no "
        "model call and creates no run."
    )
