"""Backend reads for run profiles, and idempotent creation of script fixtures.

Creation is limited to research runs whose script makes no model call: no
`execution` (trading) overrides are ever sent, and `catalog.check_creatable`
refuses `llm_call`. Backtests, Monte Carlo and tuning runs are only reused,
because Backend has EpochAI write their report text on the owner's allowance.
"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx
import jwt

from tools.agent_testing import Stack

from .catalog import (
    ALL,
    Fixture,
    Profile,
    arrow_row_count,
    check_creatable,
    detailed_tables,
    profile,
    result_categories,
)

TERMINAL = ("completed", "failed", "cancelled")


def now() -> str:
    return datetime.now(UTC).isoformat()


class Backend:
    """The signed-in owner's Backend API. Never logs or stores the bearer token."""

    def __init__(self, stack: Stack, bearer: Callable[[], str]):
        self.stack = stack
        self._bearer = bearer
        self.client = httpx.Client(base_url=stack.backend_url, timeout=httpx.Timeout(60, read=120))

    def close(self) -> None:
        self.client.close()

    def _request(self, method: str, path: str, **kwargs: object) -> httpx.Response:
        headers = {"Authorization": self._bearer()}
        return self.client.request(method, path, headers=headers, **kwargs)  # type: ignore[arg-type]

    def get_json(self, path: str, **params: str) -> object:
        response = self._request("GET", path, params=params or None)
        response.raise_for_status()
        return response.json()

    # --- reads -----------------------------------------------------------------

    def execution_ids(self) -> list[str]:
        ids = self.get_json("/api/v1/studies/executions", limit="500")
        if not isinstance(ids, list):
            raise RuntimeError("The executions list was not a list of ids.")
        return [str(item) for item in ids]

    def execution(self, run_id: str) -> dict[str, object] | None:
        response = self._request("GET", f"/api/v1/studies/executions/{run_id}")
        if response.status_code in (403, 404):
            return None
        response.raise_for_status()
        return response.json()

    def snapshot(self, run_id: str) -> dict[str, object]:
        return self.get_json(f"/api/v1/studies/executions/{run_id}/study")  # type: ignore[return-value]

    def metadata(self, run_id: str) -> dict[str, dict[str, int | None]]:
        value = self.get_json(f"/api/v1/studies/executions/{run_id}/perf/metadata")
        assert isinstance(value, dict)
        return value.get("tearsheet_metadata") or {}

    def schema(self, run_id: str, category: str) -> dict[str, object]:
        return self.get_json(  # type: ignore[return-value]
            f"/api/v1/studies/executions/{run_id}/dashboard/schema", category=category
        )

    def row_count(self, run_id: str, data_id: str, category: str) -> int:
        response = self._request(
            "GET",
            f"/api/v1/studies/executions/{run_id}/dashboard/data/{data_id}",
            params={"category": category},
        )
        response.raise_for_status()
        return arrow_row_count(response.content)

    def profile(self, run_id: str, *, rows: bool = True) -> Profile | None:
        """A run's export shape. Reads what the study page reads; starts no work."""
        execution = self.execution(run_id)
        if execution is None:
            return None
        name = None
        if execution.get("status") != "completed":
            return profile(execution)
        try:
            name = str(self.snapshot(run_id).get("study", {}).get("name") or "")  # type: ignore[union-attr]
        except httpx.HTTPError:
            name = None
        metadata = self.metadata(run_id)
        categories = result_categories(metadata)
        if not categories:  # No dashboard: the Terminal reads no schema either.
            return profile(execution, metadata, {}, name=name)
        schemas: dict[str, dict[str, object]] = {ALL: self.schema(run_id, ALL)}
        for category in categories:
            if category not in schemas:
                schemas[category] = self.schema(run_id, category)
        counts: dict[tuple[str, str], int] = {}
        if rows:
            for category, schema in schemas.items():
                for table in detailed_tables(schema):
                    key = (category, str(table["data_id"]))
                    if key not in counts:
                        counts[key] = self.row_count(run_id, key[1], category)
        return profile(execution, metadata, schemas, counts, name=name)

    def usage(self) -> dict[str, object]:
        """The owner's weekly usage meters (read only), for a before/after cost record."""
        value = self.get_json("/api/v1/platform/usage")
        assert isinstance(value, dict)
        return {
            "read_at": now(),
            "week": value.get("week"),
            "meters": [
                {k: meter.get(k) for k in ("meter", "used", "limit", "mode", "state", "blocked")}
                for meter in value.get("meters") or []
            ],
            "breakdown": value.get("breakdown"),
            "measurement": value.get("measurement"),
        }

    # --- creation ------------------------------------------------------------------

    def validate(
        self, source: str, start: str, end: str, assets: list[str], study_type: str | None = "research"
    ) -> dict[str, object]:
        body: dict[str, object] = {"source": source, "study_from": start, "study_to": end, "study_assets": assets}
        if study_type:
            body["study_type"] = study_type
        response = self._request("POST", "/api/v1/script-validator/validate", json=body)
        response.raise_for_status()
        return response.json()

    def create_definition(self, name: str, description: str, source: str, assets: list[str]) -> str:
        response = self._request(
            "POST",
            "/api/v1/studies/definitions",
            json={"name": name, "description": description, "source": source, "data": {"assets": assets}},
        )
        if response.status_code != 201:
            raise RuntimeError(
                f"Backend refused the study definition (HTTP {response.status_code}): {response.text[:500]}"
            )
        return str(response.json()["id"])

    def start_execution(self, definition_id: str, execution_id: str, start: str, end: str) -> None:
        """Starts a research run with a client-chosen id; a repeat of the same id is a 409."""
        response = self._request(
            "POST",
            "/api/v1/studies/executions",
            json={"id": definition_id, "execution_id": execution_id, "period": {"start": start, "end": end}},
        )
        if response.status_code == 409:
            return  # Already accepted under this id; never a second run.
        if response.status_code != 201:
            raise RuntimeError(
                f"Backend refused the run (HTTP {response.status_code}): {response.text[:500]}"
            )
        accepted = str(response.json().get("id") or execution_id)
        if accepted != execution_id:
            raise RuntimeError("Backend accepted the run under a different id than requested.")

    def start_monte_carlo(self, parent_id: str, execution_id: str, options: Mapping[str, object]) -> None:
        """A Monte Carlo run derived from a completed backtest; a repeat of the id is a 409."""
        response = self._request(
            "POST",
            f"/api/v1/studies/executions/{parent_id}/monte-carlo",
            json={**options, "execution_id": execution_id},
        )
        if response.status_code == 409:
            return
        if response.status_code != 201:
            raise RuntimeError(f"Backend refused the Monte Carlo run (HTTP {response.status_code}): {response.text[:500]}")
        if str(response.json().get("id") or execution_id) != execution_id:
            raise RuntimeError("Backend accepted the Monte Carlo run under a different id than requested.")

    def parameter_search(self, definition_id: str, body: Mapping[str, object]) -> dict[str, object]:
        """Starts (or with dry_run, only sizes) a parameter search; a repeat of the id is a 409."""
        response = self._request(
            "POST", f"/api/v1/studies/definitions/{definition_id}/parameter-search", json=dict(body)
        )
        if response.status_code == 409:
            return {"id": body.get("execution_id"), "repeat": True}
        if response.status_code not in (200, 201):
            raise RuntimeError(f"Backend refused the parameter search (HTTP {response.status_code}): {response.text[:500]}")
        return response.json()

    def wait(self, run_id: str, timeout: float = 900, interval: float = 5) -> dict[str, object]:
        deadline = time.monotonic() + timeout
        while True:
            execution = self.execution(run_id)
            if execution and execution.get("status") in TERMINAL:
                return execution
            if time.monotonic() > deadline:
                raise TimeoutError(f"Run {run_id} did not finish within {timeout:.0f}s.")
            time.sleep(interval)


def owner_of(stack: Stack) -> str:
    """The signed-in owner's opaque Cognito subject, so state never crosses accounts."""
    token = stack.bearer().removeprefix("Bearer ")
    return str(jwt.decode(token, options={"verify_signature": False})["sub"])


class State:
    """Git-ignored, per-environment record of runs the suite reused or created.

    Created runs are recorded before and after each request, so an interrupted
    `prepare` resumes the same definition and execution id instead of making new ones.
    """

    def __init__(self, path: Path, environment: str, owner: str):
        self.path, self.environment, self.owner = path, environment, owner
        self.data: dict[str, object] = {"environment": environment, "owner_sub": owner, "fixtures": {}}
        if path.exists():
            data = json.loads(path.read_text())
            if data.get("environment") != environment or data.get("owner_sub") != owner:
                raise ValueError(
                    f"{path} belongs to another environment or account; move it aside to start over."
                )
            self.data = data

    @property
    def fixtures(self) -> dict[str, dict[str, object]]:
        return self.data["fixtures"]  # type: ignore[return-value]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", dir=self.path.parent, delete=False) as stream:
            json.dump(self.data, stream, indent=2)
            temporary = Path(stream.name)
        os.chmod(temporary, 0o600)
        temporary.replace(self.path)


@contextmanager
def locked(path: Path) -> Iterator[None]:
    """One writer per state file; a second `prepare`/`run` waits instead of racing."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def create_fixture(
    backend: Backend, state: State, fixture: Fixture, log: Callable[[str], None] = print
) -> str:
    """Creates (or resumes) a script fixture's run and returns its id. Never duplicates."""
    script = fixture.script
    assert script is not None
    source = script.source()
    check_creatable(source)
    spec = script.spec_hash()
    entry = state.fixtures.get(fixture.id) or {}
    if entry.get("origin") != "created" or entry.get("spec_sha256") != spec:
        entry = {"origin": "created", "spec_sha256": spec, "script": str(script.path.name)}
    state.fixtures[fixture.id] = entry
    if not entry.get("definition_id"):
        result = backend.validate(source, script.start, script.end, list(script.assets))
        if result.get("status") != "ok":
            raise RuntimeError(f"{fixture.id}: Backend rejected the script: {result.get('error_message')}")
        log(f"{fixture.id}: creating the study definition")
        entry["definition_id"] = backend.create_definition(
            script.name, script.description, source, list(script.assets)
        )
        entry["definition_created_at"] = now()
        state.save()
    if not entry.get("run_id"):
        entry["run_id"] = str(uuid.uuid4())
        entry["run_requested_at"] = now()
        state.save()  # Before the request: a retry reuses this id (409), never a new run.
    run_id = str(entry["run_id"])
    if backend.execution(run_id) is None:
        log(f"{fixture.id}: starting research run {run_id}")
        backend.start_execution(str(entry["definition_id"]), run_id, script.start, script.end)
    execution = backend.wait(run_id)
    entry["status"] = execution.get("status")
    entry["execution_type"] = execution.get("execution_type")
    entry["study_id"] = execution.get("definition_id")
    entry["settled_at"] = now()
    state.save()
    if execution.get("execution_type") != "research":
        raise RuntimeError(f"{fixture.id}: the created run is {execution.get('execution_type')}, not research.")
    if execution.get("status") != "completed":
        raise RuntimeError(f"{fixture.id}: the created run {run_id} {execution.get('status')}.")
    return run_id


def usage_delta(before: Mapping[str, object], after: Mapping[str, object]) -> dict[str, float]:
    """How much each meter and breakdown kind moved between two usage reads."""
    def read(usage: Mapping[str, object]) -> dict[str, float]:
        values = {f"meter:{m['meter']}": float(m.get("used") or 0) for m in usage.get("meters") or []}  # type: ignore[union-attr]
        values |= {f"kind:{k['kind']}": float(k.get("used") or 0) for k in usage.get("breakdown") or []}  # type: ignore[union-attr]
        return values

    first, second = read(before), read(after)
    return {key: round(second.get(key, 0.0) - first.get(key, 0.0), 6) for key in sorted(set(first) | set(second))}


def create_paid_fixture(
    backend: Backend,
    state: State,
    fixture: Fixture,
    parent_run_id: str | None,
    log: Callable[[str], None] = print,
    settle: float = 60,
) -> str:
    """Creates (or resumes) a Monte Carlo or tuning run, recording the owner's meters around it.

    Called only when the owner has approved this fixture's AI cost. Like `create_fixture`, the
    client-chosen run id is recorded before the request, so it never makes a second run.
    """
    derive = fixture.derive
    assert derive is not None
    spec = derive.spec_hash()
    entry = state.fixtures.get(fixture.id) or {}
    if entry.get("origin") != "created" or entry.get("spec_sha256") != spec:
        earlier = list(entry.pop("superseded", [])) + ([entry] if entry.get("run_id") else [])
        entry = {"origin": "created", "paid": True, "kind": derive.kind, "spec_sha256": spec}
        if earlier:  # Keep the record of an earlier attempt; never drop evidence.
            entry["superseded"] = earlier
    state.fixtures[fixture.id] = entry
    if "usage_before" not in entry:
        entry["usage_before"] = backend.usage()
        state.save()
    if derive.kind == "parameter_search":
        script = derive.script
        assert script is not None
        if not entry.get("definition_id"):
            assets = list(script.assets)
            if derive.parent:  # The parent backtest's concrete as-run asset ids.
                if not parent_run_id:
                    raise RuntimeError(f"{fixture.id}: the {derive.parent} fixture has no run to take assets from.")
                study = backend.snapshot(parent_run_id).get("study") or {}
                assets = list((study.get("data") or {}).get("assets") or [])  # type: ignore[union-attr]
                entry["assets_from_run"] = parent_run_id
            result = backend.validate(script.source(), script.start, script.end, assets, study_type=None)
            if result.get("status") != "ok":
                raise RuntimeError(f"{fixture.id}: Backend rejected the script: {result.get('error_message')}")
            log(f"{fixture.id}: creating the tuning parent definition")
            entry["assets"] = assets
            entry["definition_id"] = backend.create_definition(
                script.name, script.description, script.source(), assets
            )
            state.save()
        request = {"period": {"start": script.start, "end": script.end}, "search": dict(derive.search)}
        if "dry_run" not in entry:
            entry["dry_run"] = backend.parameter_search(str(entry["definition_id"]), {**request, "dry_run": True})
            state.save()
        limit = int((derive.search.get("budget") or {}).get("max_trials", 0))  # type: ignore[union-attr]
        trials = int(entry["dry_run"].get("trial_count") or 0)  # type: ignore[union-attr]
        if limit and trials > limit:
            raise RuntimeError(f"{fixture.id}: the search would run {trials} trials, more than {limit}.")
    else:
        if not parent_run_id and not entry.get("parent_run_id"):
            raise RuntimeError(f"{fixture.id}: the {derive.parent} fixture has no run to derive from.")
        entry.setdefault("parent_run_id", parent_run_id)
    if not entry.get("run_id"):
        entry["run_id"] = str(uuid.uuid4())
        entry["run_requested_at"] = now()
        state.save()
    run_id = str(entry["run_id"])
    if backend.execution(run_id) is None:
        log(f"{fixture.id}: starting {derive.kind} run {run_id}")
        if derive.kind == "parameter_search":
            backend.parameter_search(str(entry["definition_id"]), {**request, "execution_id": run_id})
        else:
            backend.start_monte_carlo(str(entry["parent_run_id"]), run_id, derive.options)
    execution = backend.wait(run_id, timeout=1800)
    entry["status"] = execution.get("status")
    entry["execution_type"] = execution.get("execution_type")
    entry["study_id"] = execution.get("definition_id")
    entry["settled_at"] = now()
    if "usage_after" not in entry:
        entry["usage_after"] = backend.usage()
        time.sleep(settle)  # Usage can be reported a little after the run settles.
        entry["usage_settled"] = backend.usage()
        entry["usage_delta"] = usage_delta(entry["usage_before"], entry["usage_settled"])  # type: ignore[arg-type]
    state.save()
    if execution.get("status") != "completed":
        raise RuntimeError(f"{fixture.id}: the created run {run_id} {execution.get('status')}.")
    return run_id
