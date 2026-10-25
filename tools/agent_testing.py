"""Conversation driver for a dedicated development stack.

Adapted from EpochAI's supervisor CLI: persistent threads, explicit interrupt
replies and live capture. Product implementation imports and database access
are deliberately kept out of this standalone client.
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import os
import re
import tempfile
import tomllib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict, cast
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from langgraph_sdk.client import LangGraphClient
from langgraph_sdk.schema import Command, Run, ThreadState

from tools.evidence import render_transcript, summarize

ROOT = Path(__file__).resolve().parents[1]


class ServiceIdentity(TypedDict):
    ai_url: str
    backend_url: str
    ui_url: str
    assistant_id: str
    token_env: str


class Attempt(TypedDict):
    thread_id: str
    owner: str
    scenario: str | None
    created_at: str
    services: ServiceIdentity


class Status(TypedDict):
    checked_at: str
    thread_id: str
    thread_status: str
    pending: dict[str, object]
    runs: list[Run]


def now() -> str:
    return datetime.now(UTC).isoformat()


def save(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
        json.dump(value, stream, indent=2, default=str)
        temporary = Path(stream.name)
    temporary.replace(path)


@contextmanager
def writer(folder: Path) -> Iterator[None]:
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / ".lock").open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


@dataclass(frozen=True)
class DevStack:
    ai_url: str
    backend_url: str
    ui_url: str
    assistant_id: str = "epoch-ai"
    token_env: str = "EPOCH_TOKEN"

    @classmethod
    def load(cls, path: Path) -> DevStack:
        config = cls(**tomllib.loads(path.read_text())["dev"])
        for url in (config.ai_url, config.backend_url, config.ui_url):
            parts = urlsplit(url)
            if (
                parts.scheme != "https"
                or not parts.hostname
                or parts.username
                or parts.password
                or parts.query
                or parts.fragment
            ):
                raise ValueError(
                    "Configure explicit HTTPS dev service URLs without credentials or query strings."
                )
        return config

    def bearer(self) -> str:
        value = os.environ.get(self.token_env)
        if not value:
            raise ValueError(
                f"Set {self.token_env} to your Epoch identity token; credentials are never stored in this repo."
            )
        return "Bearer " + value


def pending(state: ThreadState) -> dict[str, object]:
    """Current checkpoint only; repeated interrupt IDs in later rounds are valid."""
    found: dict[str, object] = {}

    def walk(node: object) -> None:
        if not isinstance(node, dict):
            return
        for item in node.get("interrupts", []):
            if isinstance(item, dict) and item.get("id"):
                found[item["id"]] = item.get("value")
        for task in node.get("tasks", []):
            walk(task)
            if isinstance(task, dict):
                walk(task.get("state"))

    walk(state)
    return found


async def check_stack(config: DevStack, bearer: str) -> dict[str, object]:
    """Readiness reads only. HTTP 200 alone does not establish AI readiness."""
    async with httpx.AsyncClient(timeout=30) as client:
        ai = await client.get(
            config.ai_url.rstrip("/") + "/health/custom",
            headers={"Authorization": bearer},
        )
        backend = await client.get(
            config.backend_url.rstrip("/") + "/api/v1/health",
            headers={"Authorization": bearer},
        )
        # UI redirects to its login page before authentication. This proves
        # reachability, not a logged-in browser session or rendering parity.
        ui = await client.get(config.ui_url, follow_redirects=True)
    ai.raise_for_status()
    backend.raise_for_status()
    ui.raise_for_status()
    ai_health, backend_health = ai.json(), backend.json()
    build = ai_health.get("build_study", {})
    if ai_health.get("status") != "healthy" or build.get("ready") is not True:
        raise RuntimeError("Dev AI is not ready; inspect its /health/custom response.")
    if build.get("backend_url", "").rstrip("/") != config.backend_url.rstrip("/"):
        raise RuntimeError(
            "Dev AI reports a different Backend from AgentTesting's configured Backend."
        )
    if backend_health.get("status") not in ("ok", "healthy"):
        raise RuntimeError("Dev Backend is not healthy.")
    return {
        "checked_at": now(),
        "services": asdict(config),
        "ai": {
            "deployment": ai_health.get("deployment"),
            "reference_version": build.get("reference_version"),
        },
        "backend": {"git_sha": backend_health.get("git_sha")},
        "ui": {
            "status": ui.status_code,
            "url": str(ui.url),
            "etag": ui.headers.get("etag"),
            "revision": None,
        },
    }


class Conversation:
    def __init__(self, folder: Path, config: DevStack, client: LangGraphClient):
        self.folder, self.config, self.client = folder, config, client

    def attempt(self) -> Attempt:
        record = cast(Attempt, json.loads((self.folder / "attempt.json").read_text()))
        if record["services"] != asdict(self.config):
            raise ValueError(
                "This attempt belongs to a different stack. Keep its original configuration."
            )
        return record

    async def status(self) -> Status:
        attempt = self.attempt()
        thread, state, runs = await asyncio.gather(
            self.client.threads.get(attempt["thread_id"]),
            self.client.threads.get_state(attempt["thread_id"], subgraphs=True),
            self.client.runs.list(attempt["thread_id"], limit=20),
        )
        result: Status = {
            "checked_at": now(),
            "thread_id": attempt["thread_id"],
            "thread_status": thread["status"],
            "pending": pending(state),
            "runs": runs,
        }
        save(self.folder / "status.json", result)
        return result

    async def submit(
        self,
        key: str,
        *,
        message: str | None,
        responses: dict[str, object] | None,
        owner: str,
        scenario: str | None,
        bearer: str,
    ) -> Run:
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}", key):
            raise ValueError(
                "Use a short alphanumeric turn key, with hyphens or underscores."
            )
        if (message is None) == (responses is None):
            raise ValueError("Provide a message or explicit interrupt responses.")
        if message is not None and not message.strip():
            raise ValueError("The message must not be empty.")
        with writer(self.folder):
            target = self.folder / "turns" / key
            request = {"message": message, "responses": responses}
            request_file, run_file = target / "request.json", target / "run.json"
            if request_file.exists():
                if json.loads(request_file.read_text()) != request:
                    raise ValueError(
                        "This turn key already names a different request. Choose a new key."
                    )
                if run_file.exists():
                    return cast(Run, json.loads(run_file.read_text()))
                if (target / "rejected.json").exists():
                    raise RuntimeError(
                        "The server rejected this submission. Inspect rejected.json and use a new key after correcting the cause."
                    )
                # A lost HTTP response does not authorize another paid run.
                thread_id = self.attempt()["thread_id"]
                offset = 0
                while True:
                    runs = await self.client.runs.list(
                        thread_id, limit=100, offset=offset
                    )
                    matches = [
                        r
                        for r in runs
                        if (r.get("metadata") or {}).get("agent_testing_key") == key
                    ]
                    if matches:
                        if len(matches) != 1:
                            raise RuntimeError(
                                "Multiple matching runs need manual inspection."
                            )
                        save(run_file, matches[0])
                        return matches[0]
                    if len(runs) < 100:
                        break
                    offset += len(runs)
                raise RuntimeError(
                    "Submission outcome is unconfirmed. Inspect this thread; it will not be retried automatically."
                )

            unresolved = [
                p
                for p in self.folder.glob("turns/*/request.json")
                if not p.with_name("run.json").exists()
                and not p.with_name("rejected.json").exists()
            ]
            if unresolved:
                raise RuntimeError(
                    "Resolve the previous unconfirmed submission with its original turn key before continuing."
                )

            receipt = await check_stack(self.config, bearer)
            if not (self.folder / "attempt.json").exists():
                if responses is not None:
                    raise ValueError(
                        "Start a conversation before answering an interrupt."
                    )
                save(
                    self.folder / "attempt.json",
                    {
                        "thread_id": str(uuid4()),
                        "owner": owner,
                        "scenario": scenario,
                        "created_at": now(),
                        "services": asdict(self.config),
                    },
                )
            attempt = self.attempt()
            # A preallocated ID survives a lost create response and reconnects
            # to the same thread. It is not a claim that model runs are idempotent.
            await self.client.threads.create(
                thread_id=attempt["thread_id"],
                if_exists="do_nothing",
                metadata={"title": scenario or self.folder.name},
            )
            state = await self.client.threads.get_state(
                attempt["thread_id"], subgraphs=True
            )
            interrupts = pending(state)
            if responses is not None:
                if not responses or not set(responses).issubset(interrupts):
                    raise ValueError(
                        "Answer only currently pending interrupt IDs shown by status."
                    )
            elif interrupts:
                raise RuntimeError(
                    "Answer the pending questions before sending another user turn."
                )
            save(target / "stack.json", receipt)
            save(request_file, request)
            command: Command | None = (
                {"resume": responses} if responses is not None else None
            )
            try:
                result = await self.client.runs.create(
                    attempt["thread_id"],
                    self.config.assistant_id,
                    input={"messages": [{"role": "user", "content": message}]}
                    if message is not None
                    else None,
                    command=command,
                    metadata={"agent_testing_key": key},
                    multitask_strategy="reject",
                    stream_mode=["values", "messages-tuple", "custom", "updates"],
                    stream_subgraphs=True,
                    stream_resumable=True,
                )
            except httpx.HTTPStatusError as error:
                # Admission/validation rejects are definitive. A gateway timeout
                # or server/transport failure can still hide an accepted run.
                if error.response.status_code in (
                    400,
                    401,
                    403,
                    404,
                    405,
                    409,
                    413,
                    415,
                    422,
                    429,
                ):
                    save(
                        target / "rejected.json",
                        {
                            "status_code": error.response.status_code,
                            "recorded_at": now(),
                            "detail": error.response.text,
                        },
                    )
                raise
            save(run_file, result)
            return result

    async def pull(self, *, follow: bool = False) -> dict[str, object]:
        """Capture persisted state and optionally the latest resumable run stream."""
        with writer(self.folder):
            attempt = self.attempt()
            status = await self.status()
            if follow:
                runs = cast(list[Run], status["runs"])
                if not runs:
                    raise RuntimeError("No run is available to follow.")
                run = runs[0]
                target = self.folder / "streams" / run["run_id"]
                target.mkdir(parents=True, exist_ok=True)
                cursor = target / "cursor.json"
                last_id = json.loads(cursor.read_text()) if cursor.exists() else None
                async for event in self.client.runs.join_stream(
                    attempt["thread_id"],
                    run["run_id"],
                    cancel_on_disconnect=False,
                    last_event_id=last_id,
                ):
                    with (target / "events.jsonl").open("a") as stream:
                        stream.write(
                            json.dumps(
                                {
                                    "observed_at": now(),
                                    "id": event.id,
                                    "event": event.event,
                                    "data": event.data,
                                },
                                default=str,
                            )
                            + "\n"
                        )
                    if event.id:
                        save(cursor, event.id)
                    print(event.event, flush=True)
                status = await self.status()
            state = await self.client.threads.get_state(
                attempt["thread_id"], subgraphs=True
            )
            save(self.folder / "thread.json", state)
            for path in self.folder.glob("turns/*/run.json"):
                accepted = json.loads(path.read_text())
                refreshed = await self.client.runs.get(
                    attempt["thread_id"], accepted["run_id"]
                )
                save(path, refreshed)
            (self.folder / "conversation.md").write_text(render_transcript(state))
            summary = summarize(self.folder)
            save(self.folder / "metrics.json", summary)
            return {"status": status, "metrics": summary, "evidence": str(self.folder)}


def index(root: Path) -> str:
    lines = [
        "| Attempt | Owner | Scenario | Thread status | Review |",
        "|---|---|---|---|---|",
    ]
    for path in sorted(root.glob("*/attempt.json")):
        attempt = json.loads(path.read_text())
        status_path, review_path = (
            path.with_name("status.json"),
            path.with_name("review.json"),
        )
        state = (
            json.loads(status_path.read_text())["thread_status"]
            if status_path.exists()
            else "not observed"
        )
        review = (
            json.loads(review_path.read_text())["outcome"]
            if review_path.exists()
            else "not reviewed"
        )
        values = [
            path.parent.name,
            attempt["owner"],
            attempt.get("scenario") or "—",
            state,
            review,
        ]
        lines.append(
            "| "
            + " | ".join(str(x).replace("|", "\\|").replace("\n", " ") for x in values)
            + " |"
        )
    return "\n".join(lines)


async def run(args: argparse.Namespace) -> object:
    if args.action == "index":
        return index(args.artifacts)
    config = DevStack.load(args.config)
    bearer = config.bearer()
    if args.action == "check":
        return await check_stack(config, bearer)
    async with httpx.AsyncClient(
        base_url=config.ai_url,
        headers={"Authorization": bearer},
        timeout=httpx.Timeout(30, read=180),
    ) as http:
        conversation = Conversation(
            args.artifacts / args.attempt, config, LangGraphClient(http)
        )
        if args.action in ("ask", "answer"):
            responses = (
                json.loads(args.responses.read_text())
                if args.action == "answer"
                else None
            )
            if responses is not None and not isinstance(responses, dict):
                raise ValueError(
                    "The response file must map current interrupt IDs to their answer payloads."
                )
            return await conversation.submit(
                args.key,
                message=getattr(args, "message", None),
                responses=responses,
                owner=args.owner,
                scenario=args.scenario,
                bearer=bearer,
            )
        if args.action == "status":
            return await conversation.status()
        return await conversation.pull(follow=args.follow)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "dev.toml")
    parser.add_argument("--artifacts", type=Path, default=ROOT / ".artifacts")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("check")
    sub.add_parser("index")
    for name in ("ask", "answer", "status", "pull"):
        command = sub.add_parser(name)
        command.add_argument("attempt", help="A unique research attempt directory name")
        if name in ("ask", "answer"):
            command.add_argument(
                "--key",
                required=True,
                help="Stable key for this submission; repeating it never resubmits",
            )
            command.add_argument("--owner", default="unassigned")
            command.add_argument("--scenario")
        if name == "ask":
            command.add_argument("message")
        if name == "answer":
            command.add_argument("--responses", type=Path, required=True)
        if name == "pull":
            command.add_argument("--follow", action="store_true")
    args = parser.parse_args()
    if hasattr(args, "attempt") and not re.fullmatch(
        r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,99}", args.attempt
    ):
        parser.error("Use a simple attempt name, not a path.")
    result = asyncio.run(run(args))
    print(
        result if isinstance(result, str) else json.dumps(result, indent=2, default=str)
    )


if __name__ == "__main__":
    main()
