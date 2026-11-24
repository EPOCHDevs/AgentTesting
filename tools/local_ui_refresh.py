"""Owned Portal-only replacement for an already capable local launcher.

No signals, injected code, mutable bundle aliases, service discovery or remote
operations. An older running launcher must be restarted once to enable this.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import fcntl
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import socket
import tempfile
import time
from typing import Callable, Protocol
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

import httpx


CAPABILITY = "ui-refresh-capability.json"
REQUEST = "ui-refresh-request.json"
RESULT = "ui-refresh-result.json"


class Child(Protocol):
    pid: int

    def poll(self) -> int | None: ...


def write_json(path: Path, value: dict) -> None:
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def process_start(pid: int) -> str:
    if pid <= 0 or Path(f"/proc/{pid}").stat().st_uid != os.getuid():
        raise ValueError("The launcher is not owned by this local account.")
    # comm can contain spaces and parentheses; fields after the last ')' start at3.
    return Path(f"/proc/{pid}/stat").read_text().rpartition(")")[2].split()[19]


@dataclass(frozen=True)
class Preview:
    command: list[str]
    cwd: Path
    env: dict[str, str]
    url: str
    commit: str
    configuration: str

    @property
    def bundle(self) -> Path:
        return Path(self.command[self.command.index("--outDir") + 1])

    def temporary(self) -> Preview:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        command = list(self.command)
        command[command.index("--port") + 1] = str(port)
        command[command.index("--host") + 1] = "127.0.0.1"
        return replace(self, command=command, url=f"http://127.0.0.1:{port}")


class _Modules(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths: list[str] = []

    def handle_starttag(self, tag, attrs):
        fields = dict(attrs)
        if tag == "script" and fields.get("type") == "module":
            path = fields.get("src", "")
            if not isinstance(path, str):
                raise ValueError("UI entry scripts require a local source path.")
            parsed = urlsplit(path)
            if (parsed.scheme or parsed.netloc or parsed.query or parsed.fragment
                    or not path.startswith("/assets/") or ".." in path.split("/")
                    or not path.endswith(".js")):
                raise ValueError("UI entry scripts must be local /assets/*.js files.")
            self.paths.append(path)


def _hash_http(http: httpx.Client, url: str, limit: int, content_types: set[str]) -> str:
    digest, size = hashlib.sha256(), 0
    with http.stream("GET", url) as response:
        if response.status_code != 200:
            raise ValueError(f"UI readiness returned HTTP {response.status_code}.")
        if response.headers.get("content-type", "").split(";", 1)[0].strip() not in content_types:
            raise ValueError("UI readiness returned an unexpected static-asset content type.")
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > limit:
                raise ValueError("UI readiness exceeded its static-asset byte bound.")
            digest.update(chunk)
    return digest.hexdigest()


def probe(preview: Preview, child: Child, others: list[Child], timeout: float = 15) -> dict:
    """Read only index and entry scripts, never execute JS or call a proxy route."""
    parsed = urlsplit(preview.url)
    if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost"):
        raise ValueError("Portal previews must stay on local loopback HTTP.")
    index = preview.bundle / "index.html"
    if index.stat().st_size > 1024 * 1024:
        raise ValueError("UI index exceeds its byte bound.")
    raw = index.read_bytes()
    modules = _Modules()
    modules.feed(raw.decode())
    if not modules.paths:
        raise ValueError("The UI bundle has no module entry script.")
    expected = {}
    for path in modules.paths:
        asset = preview.bundle / path.lstrip("/")
        if asset.stat().st_size > 32 * 1024 * 1024:
            raise ValueError("UI entry asset exceeds its byte bound.")
        expected[path] = hashlib.sha256(asset.read_bytes()).hexdigest()
    index_hash = hashlib.sha256(raw).hexdigest()
    deadline = time.monotonic() + timeout
    with httpx.Client(timeout=2, trust_env=False, follow_redirects=False) as http:
        while time.monotonic() < deadline:
            if child.poll() is not None or any(other.poll() is not None for other in others):
                raise ValueError("A supervised service exited during UI readiness.")
            try:
                if _hash_http(http, preview.url, 1024 * 1024, {"text/html"}) != index_hash:
                    raise ValueError("Served UI index differs from the qualified bundle.")
                for path, digest in expected.items():
                    url = urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
                    if _hash_http(http, url, 32 * 1024 * 1024,
                                  {"application/javascript", "text/javascript"}) != digest:
                        raise ValueError("Served UI entry asset differs from the qualified bundle.")
                return {"index_sha256": index_hash, "entry_assets": expected}
            except httpx.TransportError:
                time.sleep(0.1)
    raise ValueError("UI readiness timed out.")


class Controller:
    def __init__(self, state: Path, preview: Preview, child: Child, children: list[Child],
                 stage: Callable[[str, str], Preview], start: Callable[[Preview, Path], Child],
                 stop: Callable[[list[Child]], None], check=probe, guard: Callable[[], None] = lambda: None,
                 target_guard: Callable[[str], None] = lambda _: None):
        self.state, self.preview, self.children = state, preview, children
        self.child: Child | None = child
        self.stage, self.start, self.stop, self.check = stage, start, stop, check
        self.guard = guard
        self.target_guard = target_guard
        self.pid, self.started = os.getpid(), process_start(os.getpid())
        self.status = "ready"
        self.last_result: tuple[str, dict] | None = None
        self.publish()

    def publish(self) -> None:
        write_json(self.state / CAPABILITY, {
            "version": 1, "launcher_pid": self.pid, "launcher_start": self.started,
            "status": self.status, "ui_pid": self.child.pid if self.child else None,
            "ui_commit": self.preview.commit, "ui_configuration": self.preview.configuration,
            "bundle": str(self.preview.bundle), "url": self.preview.url,
        })

    def _stop(self, child: Child | None) -> None:
        if child is not None:
            self.stop([child])
            if child in self.children:
                self.children.remove(child)

    def _start(self, preview: Preview, name: str) -> Child:
        child = self.start(preview, self.state / name)
        self.children.append(child)
        return child

    def refresh(self, commit: str, configuration: str, request_id: str) -> dict:
        self.guard()
        self.target_guard(commit)
        if configuration != self.preview.configuration:
            raise ValueError("Portal-only refresh preserves current configuration; a configuration change needs a reviewed bootstrap.")
        if (commit, configuration) == (self.preview.commit, self.preview.configuration) and self.child:
            others = [p for p in self.children if p is not self.child]
            return {"status": "unchanged", "ui_pid": self.child.pid, "ui_commit": commit,
                    "ui_configuration": configuration, "bundle": str(self.preview.bundle),
                    "non_ui_pids": [p.pid for p in others],
                    "validation": self.check(self.preview, self.child, others)}
        others = [p for p in self.children if p is not self.child]
        original_pids = [p.pid for p in others]
        old = self.preview
        candidate = self.stage(commit, configuration)
        if ((candidate.commit, candidate.configuration) != (commit, configuration)
                or candidate.cwd != old.cwd or candidate.env != old.env or candidate.url != old.url):
            raise ValueError("The staged UI does not match the requested source/configuration or current service.")
        shadow = replacement = None
        try:
            temporary = candidate.temporary()
            shadow = self._start(temporary, f"ui-preview-{request_id}.log")
            self.check(temporary, shadow, others)
            self.guard()
            self.target_guard(commit)
            # The current UI is untouched until a separate preview is healthy.
            self._stop(self.child)
            self.child = None
            try:
                replacement = self._start(candidate, f"ui-refresh-{request_id}.log")
                validation = self.check(candidate, replacement, others)
                self.guard()
                self.target_guard(commit)
            except (ValueError, OSError, RuntimeError, httpx.HTTPError):
                self._stop(replacement)
                replacement = None
                rollback = None
                try:
                    self.guard()
                    rollback = self._start(old, f"ui-rollback-{request_id}.log")
                    self.check(old, rollback, others)
                    self.guard()
                    self.child = rollback
                except (ValueError, OSError, RuntimeError, httpx.HTTPError):
                    self._stop(rollback)
                    self.status = "degraded"
                return {"status": "failed", "stage": "replacement", "rollback_healthy": self.child is not None,
                        "non_ui_pids": original_pids}
            self.preview, self.child = candidate, replacement
            return {"status": "ready", "ui_pid": replacement.pid, "ui_commit": commit,
                    "ui_configuration": configuration, "bundle": str(candidate.bundle),
                    "non_ui_pids": original_pids, "validation": validation}
        finally:
            self._stop(shadow)

    def tick(self) -> None:
        if self.child is not None and self.child.poll() is not None:
            self._stop(self.child)
            self.child, self.status = None, "degraded"
            self.publish()
        request = self.state / REQUEST
        if not request.exists():
            return
        if request.stat().st_size > 16 * 1024:
            raise ValueError("Portal-refresh request exceeds its byte bound.")
        raw = request.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if self.last_result is not None and self.last_result[0] == digest:
            self.flush(raw, self.last_result[1])
            return
        request_id = None
        try:
            value = json.loads(raw)
            if set(value) != {"version", "request_id", "launcher_pid", "launcher_start", "commit", "configuration"}:
                raise ValueError("Unknown Portal-refresh request fields.")
            request_id = str(UUID(value["request_id"]))
            if (type(value["version"]) is not int or value["version"] != 1
                    or type(value["launcher_pid"]) is not int or value["launcher_pid"] != self.pid
                    or value["launcher_start"] != self.started
                    or not re.fullmatch(r"[0-9a-f]{40}", value["commit"])
                    or not re.fullmatch(r"[0-9a-f]{64}", value["configuration"])):
                raise ValueError("Portal-refresh target or launcher identity does not match.")
            self.status = "refreshing"
            self.publish()
            result = self.refresh(value["commit"], value["configuration"], request_id)
        except (ValueError, TypeError, KeyError, OSError, RuntimeError, httpx.HTTPError) as error:
            result = {"status": "failed", "stage": "staging", "detail": str(error)}
        result = {"request_id": request_id, **result}
        # Save the effect's result in memory before writing its acknowledgement.
        # A failed receipt write can be retried without repeating the UI swap.
        self.last_result = digest, result
        self.flush(raw, result)

    def flush(self, raw: bytes, result: dict) -> None:
        self.status = "ready" if self.child is not None else "degraded"
        self.publish()
        write_json(self.state / RESULT, result)
        # Do not remove a different request if a writer ignored serialization.
        request = self.state / REQUEST
        if request.exists() and request.read_bytes() == raw:
            request.unlink()

    def close(self) -> None:
        path = self.state / CAPABILITY
        try:
            value = json.loads(path.read_text())
            if (isinstance(value, dict) and value.get("launcher_pid") == self.pid
                    and value.get("launcher_start") == self.started):
                path.unlink()
        except (ValueError, OSError):
            pass  # Receipt cleanup must never prevent owned service shutdown.


def request_refresh(state: Path, commit: str, configuration: str, timeout: float = 300) -> dict:
    if not re.fullmatch(r"[0-9a-f]{40}", commit) or not re.fullmatch(r"[0-9a-f]{64}", configuration):
        raise ValueError("Supply the exact qualified UI commit and configuration SHA256.")
    capability = state / CAPABILITY
    if not capability.exists():
        raise ValueError("The running launcher has no Portal-refresh capability; one coordinated restart enables it.")
    with (state / "ui-refresh-request.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("A Portal refresh is already being observed; do not submit another.") from None
        cap = json.loads(capability.read_text())
        if (not isinstance(cap, dict) or type(cap.get("version")) is not int
                or cap.get("version") != 1 or type(cap.get("launcher_pid")) is not int
                or not isinstance(cap.get("launcher_start"), str)):
            raise ValueError("The Portal-refresh capability is invalid; no request was submitted.")
        if cap["launcher_start"] != process_start(cap["launcher_pid"]):
            raise ValueError("The Portal-refresh capability is stale; no request was submitted.")
        if cap.get("status") == "refreshing" or (state / REQUEST).exists():
            raise ValueError("A Portal refresh is still pending; reconcile its saved result before another.")
        request_id = str(uuid4())
        write_json(state / REQUEST, {"version": 1, "request_id": request_id,
                   "launcher_pid": cap["launcher_pid"], "launcher_start": cap["launcher_start"],
                   "commit": commit, "configuration": configuration})
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = state / RESULT
            if result.exists():
                value = json.loads(result.read_text())
                if value.get("request_id") == request_id:
                    return value
            time.sleep(0.1)
        raise ValueError(f"Portal refresh {request_id} is not yet reconciled; do not resubmit. Read {state / RESULT}.")
