import asyncio
import json
from pathlib import Path

import httpx
import pytest

from tools import executions


def frame(kind, data, event_id=""):
    return (
        f"event: {kind}\n"
        + (f"id: {event_id}\n" if event_id else "")
        + "data: "
        + json.dumps(data)
        + "\n\n"
    ).encode()


def replay():
    return b"".join(
        [
            frame("status", {"status": "completed"}),
            frame(
                "config",
                {
                    "event_id": "config-1",
                    "config_type": "execution",
                    "config": {"cash": 10},
                },
                "config-1",
            ),
            frame(
                "http",
                {"event_id": "http-1", "latency_ms": 12, "http_status": 200},
                "http-1",
            ),
            frame(
                "record",
                {
                    "event_id": "record-1",
                    "table": "trials",
                    "sequence": 1,
                    "row": {"score": 0.4},
                },
                "record-1",
            ),
            frame(
                "lifecycle",
                {
                    "event_id": "done",
                    "operation_type": "job",
                    "status": "completed",
                    "duration_ms": 80,
                },
                "done",
            ),
        ]
    )


class Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks, *, hang=False, failure=None):
        self.chunks, self.hang, self.failure = chunks, hang, failure

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk
        if self.failure:
            raise self.failure
        if self.hang:
            await asyncio.Event().wait()


class Backend:
    def __init__(self, *, status="completed", stream=None):
        self.status = status
        self.stream = replay() if stream is None else stream
        self.requests = []
        self.bad_pagination = False
        self.stream_status = 200
        self.record_status = 200

    def handle(self, request):
        assert request.method == "GET"
        assert request.headers["Authorization"] == "Bearer test-token"
        self.requests.append(request)
        if request.url.path.endswith("/logs"):
            offset = int(request.url.params["offset"])
            events = (
                [{"event_id": f"log-{offset}", "message": "worker output"}]
                if offset < 2
                else []
            )
            return httpx.Response(
                200,
                json={
                    "events": events,
                    "pagination": {
                        "offset": 0 if self.bad_pagination else offset,
                        "limit": 1,
                        "total": 2,
                        "has_more": offset == 0,
                    },
                },
            )
        if request.url.path.endswith("/stream"):
            assert dict(request.url.params) == {"include_historical": "true"}
            return httpx.Response(
                self.stream_status,
                headers={"content-type": "text/event-stream"},
                stream=self.stream
                if isinstance(self.stream, Chunks)
                else Chunks([self.stream]),
            )
        return httpx.Response(
            self.record_status,
            json={
                "id": "exact-run",
                "status": self.status,
                "error_reason": "worker failure" if self.status == "failed" else None,
                "artifact_source": {
                    "kind": "cloudfront",
                    "signed_query": "secret-cdn-query",
                },
            },
        )


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    import socket

    def blocked(*args, **kwargs):
        raise AssertionError("Real network is forbidden")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)


def collect(tmp_path, backend, ids=None):
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(backend.handle)
        ) as client:
            result = await executions.collect(
                tmp_path,
                "https://api.dev.example",
                "test-token",
                ids or ["exact-run"],
                client=client,
            )
            assert not client.is_closed
            return result

    return asyncio.run(run())


def test_complete_capture_keeps_records_http_config_and_all_log_pages(tmp_path):
    backend = Backend(stream=Chunks([bytes([byte]) for byte in replay()]))
    result = collect(tmp_path, backend, ["exact-run", "exact-run"])
    assert len(result["executions"]) == 1
    row = result["executions"][0]
    assert row["status"] == "complete"
    assert row["record"]["error_reason"] is None
    assert row["stream"]["job_duration_ms"] == 80
    assert row["logs"]["events"] == 2
    assert [
        r.url.params.get("offset")
        for r in backend.requests
        if r.url.path.endswith("/logs")
    ] == ["0", "1"]
    assert row["stream"]["event_counts"] == {
        "status": 1,
        "config": 1,
        "http": 1,
        "record": 1,
        "lifecycle": 1,
    }
    directory = tmp_path / row["directory"]
    assert (directory / "stream.sse").read_bytes() == replay()
    assert len((directory / "events.jsonl").read_text().splitlines()) == 5
    assert json.loads((tmp_path / result["manifest"]).read_text()) == result
    saved = "".join(
        p.read_text() for p in (tmp_path / "backend").rglob("*") if p.is_file()
    )
    assert "test-token" not in saved and "secret-cdn-query" not in saved


def test_running_job_gets_log_snapshot_without_opening_live_stream(tmp_path):
    backend = Backend(status="running")
    row = collect(tmp_path, backend)["executions"][0]
    assert row["status"] == "partial"
    assert row["logs"]["snapshot_only"] is True
    assert row["stream"]["status"] == "skipped"
    assert row["stream"]["job_duration_ms"] is None
    assert not any(r.url.path.endswith("/stream") for r in backend.requests)


def test_log_pagination_mismatch_does_not_hide_other_evidence(tmp_path):
    backend = Backend()
    backend.bad_pagination = True
    row = collect(tmp_path, backend)["executions"][0]
    assert row["logs"]["status"] == "partial"
    assert row["logs"]["reason"] == "invalid log pagination"
    assert row["stream"]["status"] == "complete"


@pytest.mark.parametrize(
    "body,reason",
    [
        (frame("status", {"status": "completed"}), "no terminal job"),
        (replay() + b"data: unfinished", "inside a frame"),
        (frame("record", {"event_id": "wrong"}, "actual"), "differs from payload"),
    ],
)
def test_incomplete_replay_is_never_claimed_complete(tmp_path, body, reason):
    row = collect(tmp_path, Backend(stream=body))["executions"][0]
    assert row["status"] == "partial"
    assert reason in row["stream"]["reason"]


def test_stream_time_limit_keeps_partial_bytes_and_closes(tmp_path, monkeypatch):
    monkeypatch.setattr(executions, "STREAM_SECONDS", 0.01)
    body = frame("status", {"status": "completed"})
    row = collect(tmp_path, Backend(stream=Chunks([body], hang=True)))["executions"][0]
    assert row["stream"]["status"] == "partial"
    assert row["stream"]["reason"] == "TimeoutError"
    assert (tmp_path / row["directory"] / "stream.sse").read_bytes() == body


def test_stream_byte_limit_and_log_page_limit_are_explicit(tmp_path, monkeypatch):
    monkeypatch.setattr(executions, "MAX_STREAM_BYTES", 25)
    monkeypatch.setattr(executions, "MAX_LOG_PAGES", 1)
    row = collect(tmp_path, Backend())["executions"][0]
    assert row["logs"]["reason"] == "log page limit reached"
    assert row["stream"]["reason"] == "stream byte limit reached"
    assert (tmp_path / row["directory"] / "stream.sse").stat().st_size == 25


def test_status_failure_makes_no_additional_requests(tmp_path):
    backend = Backend()
    backend.record_status = 403
    row = collect(tmp_path, backend)["executions"][0]
    assert row["record"]["status"] == "unavailable"
    assert row["error"] == "HTTP 403"
    assert len(backend.requests) == 1


def test_missing_stream_does_not_erase_failure_record_or_logs(tmp_path):
    backend = Backend(status="failed")
    backend.stream_status = 404
    row = collect(tmp_path, backend)["executions"][0]
    assert row["stream"]["status"] == "unavailable"
    assert row["stream"]["reason"] == "HTTP 404"
    assert row["logs"]["status"] == "complete"
    assert (
        json.loads((tmp_path / row["directory"] / "execution.json").read_text())[
            "error_reason"
        ]
        == "worker failure"
    )


def test_new_capture_does_not_overwrite_old_evidence(tmp_path):
    first = collect(tmp_path, Backend())
    second = collect(tmp_path, Backend(status="running"))
    assert first["manifest"] != second["manifest"]
    assert (tmp_path / first["manifest"]).is_file()


def test_relative_attempt_folder_keeps_resolvable_manifest_paths(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = collect(Path("relative-attempt"), Backend())
    assert (tmp_path / "relative-attempt" / result["manifest"]).is_file()
    assert result["executions"][0]["status"] == "complete"


def test_crlf_comments_and_multiline_data_parser(tmp_path):
    body = (
        b": keepalive\r\n\r\nevent: lifecycle\r\nid: done\r\n"
        b'data: {"event_id":"done",\r\n'
        b'data: "operation_type":"job","status":"completed"}\r\n\r\n'
    )
    row = collect(tmp_path, Backend(stream=Chunks([body[:20], body[20:]])))[
        "executions"
    ][0]
    assert row["stream"]["status"] == "complete"


def test_malformed_record_saves_only_safe_error_metadata(tmp_path):
    backend = Backend()
    backend.handle = lambda request: httpx.Response(200, content=b"{incomplete")
    row = collect(tmp_path, backend)["executions"][0]
    assert row["error"] == "JSONDecodeError"
    receipt = json.loads((tmp_path / row["directory"] / "execution.json").read_text())
    assert receipt == {
        "capture_error": {
            "error_type": "JSONDecodeError",
            "http_status": 200,
            "received_bytes": len(b"{incomplete"),
            "body_omitted": True,
        }
    }


def test_execution_redacts_only_artifact_signing_query_not_business_fields(tmp_path):
    backend = Backend(status="running")
    normal = backend.handle
    evidence = {"token": 23, "authorization": "purchase authorization amount",
                "signed_query": "business field", "net_cash": None}

    def handle(request):
        response = normal(request)
        if request.url.path.endswith("/exact-run"):
            value = response.json()
            value["parameters"] = evidence
            return httpx.Response(200, json=value)
        return response

    backend.handle = handle
    result = collect(tmp_path, backend)["executions"][0]
    saved = json.loads((tmp_path / result["directory"] / "execution.json").read_text())
    assert saved["artifact_source"]["signed_query"] == "[REDACTED]"
    assert saved["parameters"] == evidence
    assert saved["id"] == "exact-run"


def test_log_read_failure_preserves_received_bytes_without_exception_credentials(
    tmp_path,
):
    backend = Backend()
    normal = backend.handle
    partial = b'{"events":[{"event_id":"log-0","message":"partial worker detail"}'

    def handle(request):
        if request.url.path.endswith("/logs"):
            return httpx.Response(
                200,
                stream=Chunks(
                    [partial[:12], partial[12:]],
                    failure=httpx.ReadError("secret-cdn-query"),
                ),
            )
        return normal(request)

    backend.handle = handle
    row = collect(tmp_path, backend)["executions"][0]
    assert row["logs"]["status"] == "partial"
    assert row["logs"]["reason"] == "ReadError"
    assert row["logs"]["partial_page"] == {
        "file": "logs-00000.json",
        "received_bytes": len(partial),
    }
    assert (tmp_path / row["directory"] / "logs-00000.json").read_bytes() == partial
    assert row["stream"]["status"] == "complete"
    assert all(
        b"secret-cdn-query" not in path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    )


@pytest.mark.parametrize(
    "failure", ["invalid_json", "oversized", "http_error", "read_error"]
)
def test_execution_failure_bodies_never_persist_signing_credentials(
    tmp_path, monkeypatch, failure
):
    secret = "test-signing-secret"
    value = {
        "id": "exact-run",
        "status": "completed",
        "artifact_source": {"signed_query": secret},
        "padding": "x" * 200,
    }
    payload = json.dumps(value).encode()
    if failure == "invalid_json":
        payload = payload[:-1]
    if failure == "oversized":
        monkeypatch.setattr(
            executions, "MAX_RESPONSE_BYTES", payload.index(b"padding") + 10
        )
    error = httpx.ReadError(secret) if failure == "read_error" else None
    backend = Backend()
    backend.handle = lambda request: httpx.Response(
        500 if failure == "http_error" else 200,
        stream=Chunks([payload[:40], payload[40:]], failure=error),
    )
    row = collect(tmp_path, backend)["executions"][0]
    assert row["record"]["status"] == "unavailable"
    receipt = json.loads((tmp_path / row["directory"] / "execution.json").read_text())
    assert receipt["capture_error"]["body_omitted"] is True
    assert receipt["capture_error"]["received_bytes"] > 0
    assert all(
        secret.encode() not in path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    )


def test_native_error_and_terminal_job_duration_are_preserved_exactly(tmp_path):
    body = frame(
        "lifecycle",
        {
            "event_id": "failed",
            "operation_type": "job",
            "status": "failed",
            "duration_ms": 12.5,
        },
        "failed",
    )
    row = collect(tmp_path, Backend(status="failed", stream=body))["executions"][0]
    assert row["record"]["error_reason"] == "worker failure"
    assert row["stream"]["job_duration_ms"] == 12.5


@pytest.mark.parametrize("duration", [None, True, -1, "80", float("nan"), float("inf")])
def test_missing_or_invalid_job_duration_is_not_inferred(tmp_path, duration):
    terminal = {"event_id": "done", "operation_type": "job", "status": "completed"}
    if duration is not None:
        terminal["duration_ms"] = duration
    # A valid duration on a stage or nonterminal job event must not be substituted.
    body = frame(
        "lifecycle",
        {
            "event_id": "stage",
            "operation_type": "stage",
            "status": "completed",
            "duration_ms": 900,
        },
        "stage",
    )
    body += frame(
        "lifecycle",
        {
            "event_id": "start",
            "operation_type": "job",
            "status": "started",
            "duration_ms": 500,
        },
        "start",
    )
    body += frame("lifecycle", terminal, "done")
    backend = Backend(stream=body)
    normal = backend.handle

    def handle(request):
        response = normal(request)
        if request.url.path.endswith("/exact-run"):
            record = response.json()
            record.pop("error_reason", None)
            return httpx.Response(200, json=record)
        return response

    backend.handle = handle
    row = collect(tmp_path, backend)["executions"][0]
    assert row["status"] == "complete"
    assert row["record"]["error_reason"] is None
    assert row["stream"]["job_duration_ms"] is None
