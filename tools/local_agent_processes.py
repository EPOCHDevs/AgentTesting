"""Read-only preflight for host Agent Servers sharing the local dataset broker.

A different public Agent Server port does not isolate its background workers.
Only inspect local EpochAI dev servers; never signal them or modify their state.
Another reported Backend URL is not proof of a different durable dataset queue.
"""

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx


@dataclass(frozen=True)
class LocalAgentProcess:
    pid: int
    port: int | None
    owner: str | None
    config: str | None


def _option(args: list[str], name: str) -> str | None:
    if name in args and args.index(name) + 1 < len(args):
        return args[args.index(name) + 1]
    return next(
        (value.split("=", 1)[1] for value in args if value.startswith(name + "=")),
        None,
    )


def local_agent_processes(
    ai: Path, proc: Path = Path("/proc")
) -> list[LocalAgentProcess]:
    if not proc.is_dir():
        raise ValueError(
            "Local Agent Server isolation requires a readable /proc inventory."
        )
    ai = ai.resolve()
    result = []
    for entry in proc.iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            if entry.stat().st_uid != os.getuid():
                continue
            args = [
                part.decode()
                for part in (entry / "cmdline").read_bytes().split(b"\0")
                if part
            ]
            command = next(
                (i for i, value in enumerate(args) if Path(value).name == "langgraph"),
                None,
            )
            module = next(
                (
                    i
                    for i, value in enumerate(args)
                    if value == "langgraph_cli" and i and args[i - 1] == "-m"
                ),
                None,
            )
            start = command if command is not None else module
            if start is None or args[start + 1 : start + 2] != ["dev"]:
                continue
            cwd = (entry / "cwd").resolve(strict=True)
            belongs = cwd == ai or any(
                value.startswith(str(ai / ".venv") + "/") for value in args[:2]
            )
            if not belongs:
                git_file = cwd / ".git"
                if git_file.is_file():
                    belongs = (
                        git_file.read_text()
                        .strip()
                        .startswith(f"gitdir: {ai / '.git' / 'worktrees'}/")
                    )
            if not belongs:
                continue

            raw_port = _option(args, "--port")
            port = (
                int(raw_port)
                if raw_port and raw_port.isdecimal() and 0 < int(raw_port) < 65536
                else None
            )
            owner = None
            try:
                for item in (entry / "environ").read_bytes().split(b"\0"):
                    if item.startswith(b"CODEX_THREAD_ID="):
                        owner = item.split(b"=", 1)[1].decode()
                        break
            except OSError:
                pass
            result.append(
                LocalAgentProcess(
                    int(entry.name), port, owner, _option(args, "--config")
                )
            )
        except (OSError, UnicodeError):
            # A process can exit between inventory reads. No state is adopted.
            continue
    return result


def _origin(value: str) -> tuple[str, int] | None:
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return None
        host = parsed.hostname.lower()
        if host in ("localhost", "127.0.0.1", "::1", "host.docker.internal"):
            host = "local-loopback"
        return host, parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return None


def reported_backend(port: int) -> str | None:
    try:
        response = httpx.get(f"http://127.0.0.1:{port}/health/custom", timeout=2)
        response.raise_for_status()
        body = response.json()
        build = body.get("build_study") if isinstance(body, dict) else None
        value = build.get("backend_url") if isinstance(build, dict) else None
        return value if isinstance(value, str) else None
    except (httpx.HTTPError, ValueError):
        return None


def verify_local_agent_isolation(
    ai: Path,
    backend_urls: tuple[str, ...],
    *,
    processes: list[LocalAgentProcess] | None = None,
    probe: Callable[[int], str | None] = reported_backend,
) -> None:
    targets = {_origin(value) for value in backend_urls}
    targets.discard(None)
    for process in local_agent_processes(ai) if processes is None else processes:
        backend = probe(process.port) if process.port is not None else None
        origin = _origin(backend) if backend else None
        if origin in targets:
            reason = f"shares the local Backend origin ({origin[0]}:{origin[1]})"
        elif origin is not None:
            reason = "reports another Backend URL, which does not prove a separate durable dataset queue"
        else:
            reason = "has unconfirmed Backend isolation"
        raise ValueError(
            f"Existing EpochAI dev server PID {process.pid}, API port {process.port}, "
            f"owner {process.owner or 'unknown'}, config {process.config or 'unknown'} {reason}. "
            "Different public API ports do not isolate background dataset workers. "
            "Retire the competing dev service through its owning task before starting the "
            "default primary stack; no isolation override is configured. The launcher has "
            "not stopped it or changed its state."
        )
