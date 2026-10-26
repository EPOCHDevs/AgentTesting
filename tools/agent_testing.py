"""Conversation driver for an explicitly selected local, dev or prod stack.

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
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, NotRequired, TypedDict, cast, get_args
from urllib.parse import quote, urlsplit
from uuid import uuid4

import httpx
from langgraph_sdk.client import LangGraphClient
from langgraph_sdk.schema import Command, Run, ThreadState

from tools.evidence import render_transcript, summarize

ROOT = Path(__file__).resolve().parents[1]
Environment = Literal["local", "dev", "prod"]


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
    attached: NotRequired[bool]
    environment: NotRequired[Environment]


class Status(TypedDict):
    checked_at: str
    thread_id: str
    thread_status: str
    pending: dict[str, object]
    runs: list[Run]
    environment: Environment


class RunHistory(TypedDict):
    captured_at: str
    complete: bool
    error_type: str | None
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
class Stack:
    ai_url: str
    backend_url: str
    ui_url: str
    assistant_id: str = "epoch-ai"
    token_env: str = "EPOCH_TOKEN"
    environment: Environment = "dev"

    def identity(self) -> ServiceIdentity:
        # Preserve the established endpoint receipt shape. Profile selection is
        # recorded separately so existing dev attempts remain readable.
        return {
            "ai_url": self.ai_url,
            "backend_url": self.backend_url,
            "ui_url": self.ui_url,
            "assistant_id": self.assistant_id,
            "token_env": self.token_env,
        }

    @classmethod
    def load(cls, path: Path, environment: Environment = "dev") -> Stack:
        if environment not in get_args(Environment):
            raise ValueError("Choose local, dev or prod.")
        profiles = tomllib.loads(path.read_text())
        if environment not in profiles:
            raise ValueError(
                f"No [{environment}] profile in {path}; no other profile will be substituted."
            )
        config = cls(**profiles[environment], environment=environment)
        schemes = ("http", "https") if environment == "local" else ("https",)
        for url in (config.ai_url, config.backend_url, config.ui_url):
            parts = urlsplit(url)
            if (
                parts.scheme not in schemes
                or not parts.hostname
                or parts.username
                or parts.password
                or parts.query
                or parts.fragment
            ):
                raise ValueError(
                    f"Configure explicit {'HTTP(S)' if environment == 'local' else 'HTTPS'} {environment} service URLs without credentials or query strings."
                )
        return config

    def bearer(self) -> str:
        value = os.environ.get(self.token_env)
        if (
            not value
            and self.environment == "local"
            and self.token_env == "EPOCH_TOKEN"
        ):
            from tools.local_auth import cached_token

            value = cached_token()
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


async def check_stack(config: Stack, bearer: str) -> dict[str, object]:
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
        raise RuntimeError(
            f"{config.environment} AI is not ready; inspect its /health/custom response."
        )
    if build.get("backend_url", "").rstrip("/") != config.backend_url.rstrip("/"):
        raise RuntimeError(
            f"{config.environment} AI reports a different Backend from AgentTesting's configured Backend."
        )
    if backend_health.get("status") not in ("ok", "healthy"):
        raise RuntimeError(f"{config.environment} Backend is not healthy.")
    return {
        "checked_at": now(),
        "environment": config.environment,
        "services": config.identity(),
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


def read_attempt(folder: Path, config: Stack) -> Attempt:
    record = cast(Attempt, json.loads((folder / "attempt.json").read_text()))
    if record.get("environment", "dev") != config.environment:
        raise ValueError(
            f"This attempt belongs to {record.get('environment', 'dev')}; select that environment or use a new attempt name."
        )
    if record["services"] != config.identity():
        raise ValueError(
            "This attempt belongs to a different stack. Keep its original configuration."
        )
    return record


class Conversation:
    def __init__(self, folder: Path, config: Stack, client: LangGraphClient):
        self.folder, self.config, self.client = folder, config, client

    def attempt(self) -> Attempt:
        return read_attempt(self.folder, self.config)

    async def attach(
        self, thread_id: str, *, owner: str, scenario: str | None
    ) -> Status:
        """Adopt an existing authorized UI/API thread without sending a message."""
        with writer(self.folder):
            exists = (self.folder / "attempt.json").exists()
            if exists:
                if self.attempt()["thread_id"] != thread_id:
                    raise ValueError("This attempt already belongs to another thread.")
            thread = await self.client.threads.get(thread_id)
            if thread["thread_id"] != thread_id:
                raise RuntimeError("The server returned a different thread identity.")
            if not exists:
                save(
                    self.folder / "attempt.json",
                    {
                        "thread_id": thread_id,
                        "owner": owner,
                        "scenario": scenario,
                        "created_at": now(),
                        "services": self.config.identity(),
                        "environment": self.config.environment,
                        "attached": True,
                    },
                )
                save(
                    self.folder / "attachment.json",
                    {
                        "configured_stack": self.config.identity(),
                        "environment": self.config.environment,
                        "attached_at": now(),
                        "thread_created_at": thread.get("created_at"),
                        "scope": "Configured endpoints at attachment, not a health check or proof of the historical deployment.",
                    },
                )
            return await self.status()

    async def all_runs(self) -> RunHistory:
        thread_id = self.attempt()["thread_id"]
        found: dict[str, Run] = {}
        offset = 0
        receipt: RunHistory = {
            "captured_at": now(),
            "complete": False,
            "error_type": None,
            "runs": [],
        }
        try:
            while True:
                page = await self.client.runs.list(thread_id, limit=100, offset=offset)
                new = [run for run in page if run["run_id"] not in found]
                if page and not new:
                    raise RuntimeError("Run pagination did not advance.")
                found.update((run["run_id"], run) for run in new)
                if len(page) < 100:
                    receipt["complete"] = True
                    break
                offset += len(page)
        except asyncio.CancelledError:
            receipt["error_type"] = "CancelledError"
            raise
        except Exception as error:
            receipt["error_type"] = type(error).__name__
        finally:
            receipt["runs"] = list(found.values())
            previous = self.folder / "runs.json"
            if previous.exists():
                save(
                    self.folder / "run-captures" / f"{uuid4()}.json",
                    json.loads(previous.read_text()),
                )
            save(self.folder / "run-captures" / f"{uuid4()}.json", receipt)
            save(previous, receipt)
        return receipt

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
            "environment": self.config.environment,
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
            if (self.folder / "attempt.json").exists():
                self.attempt()  # Reject a profile change before any readiness/API call.
            if request_file.exists():
                self.attempt()  # Cached receipts still belong to one exact stack.
                saved_request = json.loads(request_file.read_text())
                if {
                    key: saved_request[key] for key in ("message", "responses")
                } != request:
                    raise ValueError(
                        "This turn key already names a different request. Choose a new key."
                    )
                if run_file.exists():
                    return cast(Run, json.loads(run_file.read_text()))
                if (target / "rejected.json").exists():
                    raise RuntimeError(
                        "The server rejected this submission. Inspect rejected.json and use a new key after correcting the cause."
                    )
                submission_id = saved_request.get("submission_id")
                if not submission_id:
                    raise RuntimeError(
                        "Legacy unconfirmed submission has no unique receipt; manual verification is required."
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
                        and (r.get("metadata") or {}).get("agent_testing_submission_id")
                        == submission_id
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
                        "services": self.config.identity(),
                        "environment": self.config.environment,
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
            submission_id = str(uuid4())
            save(request_file, {**request, "submission_id": submission_id})
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
                    metadata={
                        "agent_testing_key": key,
                        "agent_testing_submission_id": submission_id,
                        "agent_testing_environment": self.config.environment,
                    },
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

    async def pull(
        self,
        *,
        follow: bool = False,
        traces: bool = False,
        executions: bool = False,
        execution_ids: list[str] | None = None,
        bearer: str | None = None,
    ) -> dict[str, object]:
        """Collect independent deployed evidence sources; report every missing part."""
        with writer(self.folder):
            attempt = self.attempt()
            sources: dict[str, object] = {
                "thread": {"status": "unavailable"},
                "stream": {"status": "not_requested"},
                "langsmith": {"status": "not_requested"},
                "backend": {"status": "not_requested"},
            }
            status, state, runs = None, None, []
            if follow:
                try:
                    status = await self.status()
                    recent = status["runs"]
                    if not recent:
                        raise ValueError("No run is available to follow.")
                    run = recent[0]
                    target = self.folder / "streams" / run["run_id"]
                    target.mkdir(parents=True, exist_ok=True)
                    cursor = target / "cursor.json"
                    last_id = (
                        json.loads(cursor.read_text()) if cursor.exists() else None
                    )
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
                    sources["stream"] = {"status": "available", "run_id": run["run_id"]}
                except Exception as error:
                    sources["stream"] = {
                        "status": "unavailable",
                        "error_type": type(error).__name__,
                    }
            try:
                status = await self.status()
                state = await self.client.threads.get_state(
                    attempt["thread_id"], subgraphs=True
                )
                save(self.folder / "thread.json", state)
                (self.folder / "conversation.md").write_text(render_transcript(state))
                history = await self.all_runs()
                runs = history["runs"]
                for path in self.folder.glob("turns/*/run.json"):
                    accepted = json.loads(path.read_text())
                    refreshed = await self.client.runs.get(
                        attempt["thread_id"], accepted["run_id"]
                    )
                    save(path, refreshed)
                sources["thread"] = {
                    "status": "available" if history["complete"] else "partial",
                    "run_count": len(runs),
                    "run_history_complete": history["complete"],
                    "error_type": history["error_type"],
                }
            except Exception as error:
                sources["thread"] = {
                    "status": "unavailable",
                    "error_type": type(error).__name__,
                }

            if traces:
                from tools.traces import TraceConfig
                from tools.traces import collect as collect_traces

                try:
                    trace_config = TraceConfig.from_env()
                except ValueError as error:
                    sources["langsmith"] = {
                        "status": "unavailable",
                        "reason": str(error),
                    }
                else:
                    try:
                        snapshot = await asyncio.to_thread(
                            collect_traces,
                            self.folder,
                            attempt["thread_id"],
                            config=trace_config,
                            expected_run_ids=[run["run_id"] for run in runs],
                        )
                        sources["langsmith"] = {
                            "status": "available"
                            if snapshot["coverage"]["complete"]
                            else "partial",
                            "run_count": len(snapshot["runs"]),
                            "coverage": snapshot["coverage"],
                            "path": "traces/langsmith.json",
                        }
                    except Exception as error:
                        sources["langsmith"] = {
                            "status": "unavailable",
                            "error_type": type(error).__name__,
                        }
            if executions or execution_ids:
                from tools.executions import collect as collect_executions

                values = state.get("values") if state else None
                supplied = (
                    values.get("execution_ids") if isinstance(values, dict) else None
                )
                ids = list(execution_ids or [])
                ids_available = isinstance(supplied, list) and all(
                    isinstance(value, str) for value in supplied
                )
                if ids_available:
                    ids.extend(cast(list[str], supplied))
                ids = list(dict.fromkeys(ids))
                if ids:
                    try:
                        if not bearer:
                            raise ValueError(
                                "Backend evidence requires the current Epoch identity."
                            )
                        manifest = await collect_executions(
                            self.folder, self.config.backend_url, bearer, ids
                        )
                        complete = all(
                            row["status"] == "complete"
                            for row in manifest["executions"]
                        )
                        sources["backend"] = {
                            **manifest,
                            "status": "available" if complete else "partial",
                            "identity_source": "state.values.execution_ids and explicit receipt IDs",
                        }
                    except Exception as error:
                        sources["backend"] = {
                            "status": "unavailable",
                            "error_type": type(error).__name__,
                        }
                elif ids_available:
                    sources["backend"] = {
                        "status": "not_applicable",
                        "reason": "The server has no accepted executions on this thread.",
                    }
                else:
                    sources["backend"] = {
                        "status": "unavailable",
                        "reason": "No current accepted execution_ids list; provide exact IDs from execution receipts.",
                    }
            complete = all(
                isinstance(source, dict)
                and source["status"] not in ("unavailable", "partial")
                for source in sources.values()
            )
            save(
                self.folder / "sources.json",
                {"collected_at": now(), "sources": sources},
            )
            summary = summarize(self.folder)
            save(self.folder / "metrics.json", summary)
            from tools.evidence import render_report

            (self.folder / "report.md").write_text(render_report(self.folder))
            return {
                "environment": self.config.environment,
                "status": status,
                "capture_status": "complete" if complete else "partial",
                "metrics": summary,
                "sources": sources,
                "evidence": str(self.folder),
            }


def index(root: Path) -> str:
    lines = [
        "| Attempt | Environment | Owner | Scenario | Last observed state | Observed at | Review | Evidence |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for path in sorted(root.glob("*/attempt.json")):
        attempt = json.loads(path.read_text())
        status_path, review_path = (
            path.with_name("status.json"),
            path.with_name("review.json"),
        )
        status = json.loads(status_path.read_text()) if status_path.exists() else None
        state = (
            ("needs_input" if status["pending"] else status["thread_status"])
            if status
            else "not observed"
        )
        observed_at = status["checked_at"] if status else "—"
        review = (
            json.loads(review_path.read_text())["outcome"]
            if review_path.exists()
            else "not reviewed"
        )
        values = [
            path.parent.name,
            attempt.get("environment", "dev"),
            attempt["owner"],
            attempt.get("scenario") or "—",
            state,
            observed_at,
            review,
        ]
        lines.append(
            "| "
            + " | ".join(str(x).replace("|", "\\|").replace("\n", " ") for x in values)
            + " | "
            + (
                f"[report]({quote(path.parent.name)}/report.md)"
                if path.with_name("report.md").exists()
                else "—"
            )
            + " |"
        )
    return "\n".join(lines)


def record_review(
    folder: Path, *, reviewer: str, outcome: str, finding: str, evidence: list[str]
) -> dict[str, object]:
    """Record an explicit review, independently of execution completion."""
    if (
        outcome not in ("pass", "partial", "fail", "unsupported")
        or not reviewer.strip()
        or not finding.strip()
    ):
        raise ValueError("Provide a reviewer, outcome and finding.")
    if not (folder / "attempt.json").exists():
        raise ValueError("Review an existing attempt.")
    with writer(folder):
        review = {
            "reviewer": reviewer,
            "outcome": outcome,
            "finding": finding,
            "evidence": evidence,
            "reviewed_at": now(),
        }
        save(folder / "reviews" / f"{uuid4()}.json", review)
        save(folder / "review.json", review)
        return review


async def run(args: argparse.Namespace) -> object:
    if args.action == "index":
        return index(args.artifacts)
    if args.action == "report":
        from tools.evidence import render_report

        folder = args.artifacts / args.attempt
        report = render_report(folder)
        (folder / "report.md").write_text(report)
        return report
    if args.action == "review":
        return record_review(
            args.artifacts / args.attempt,
            reviewer=args.reviewer,
            outcome=args.outcome,
            finding=args.finding,
            evidence=args.evidence or [],
        )
    config = Stack.load(args.config, args.env)
    if args.action != "check":
        folder = args.artifacts / args.attempt
        if (folder / "attempt.json").exists():
            read_attempt(folder, config)  # Before even a cached-login refresh.
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
        if args.action == "attach":
            return await conversation.attach(
                args.thread_id, owner=args.owner, scenario=args.scenario
            )
        return await conversation.pull(
            follow=args.follow,
            traces=args.traces,
            executions=args.executions,
            execution_ids=args.execution_id,
            bearer=bearer,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env",
        choices=get_args(Environment),
        default="dev",
        help="Network target (default: dev); offline commands read saved attempts",
    )
    parser.add_argument("--config", type=Path, default=ROOT / "environments.toml")
    parser.add_argument("--artifacts", type=Path, default=ROOT / ".artifacts")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("check")
    sub.add_parser("index")
    for name in ("ask", "answer", "status", "pull", "attach", "report", "review"):
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
            command.add_argument(
                "--traces",
                action="store_true",
                help="Collect the LangSmith trace tree, timing, cost and errors",
            )
            command.add_argument(
                "--executions",
                action="store_true",
                help="Collect Backend evidence for accepted execution IDs in thread state",
            )
            command.add_argument(
                "--execution-id",
                action="append",
                help="Additional exact Backend execution ID, repeatable",
            )
        if name == "attach":
            command.add_argument("thread_id")
            command.add_argument("--owner", default="unassigned")
            command.add_argument("--scenario")
        if name == "review":
            command.add_argument("--reviewer", required=True)
            command.add_argument(
                "--outcome",
                choices=("pass", "partial", "fail", "unsupported"),
                required=True,
            )
            command.add_argument("--finding", required=True)
            command.add_argument(
                "--evidence",
                action="append",
                help="Reviewed artifact path or issue link; repeatable",
            )
    args = parser.parse_args()
    if hasattr(args, "attempt") and not re.fullmatch(
        r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,99}", args.attempt
    ):
        parser.error("Use a simple attempt name, not a path.")
    result = asyncio.run(run(args))
    if args.action == "pull" and isinstance(result, dict):
        details = result["metrics"]
        result = {
            "environment": result["environment"],
            "capture_status": result["capture_status"],
            "evidence": result["evidence"],
            "report": str(args.artifacts / args.attempt / "report.md"),
            "sources": {
                name: {
                    key: value
                    for key, value in source.items()
                    if key in ("status", "reason", "error_type", "manifest", "path")
                }
                for name, source in result["sources"].items()
            },
            "observed_stream_model_calls": details["observed_model_calls"],
            "langsmith_reported_cost_usd": details["cost_usd"],
        }
    print(
        result if isinstance(result, str) else json.dumps(result, indent=2, default=str)
    )
    if (
        args.action == "pull"
        and isinstance(result, dict)
        and result.get("capture_status") == "partial"
    ):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
