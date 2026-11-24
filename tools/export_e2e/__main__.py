"""Export end-to-end suite: `python -m tools.export_e2e --env dev run [--only FIXTURE]`.

Commands:
  fixtures   The catalog and this environment's pinned runs (offline).
  inventory  Profile every run the signed-in owner can open (read only).
  prepare    Pin an existing run to each fixture, creating only missing script
             fixtures (research, no model call). Idempotent.
  run        prepare, then check each fixture's export in headless Chrome and
             record the attempt under .artifacts/<attempt>/.
  recheck    Re-judge a recorded attempt's observations offline; writes nothing.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import traceback
from collections.abc import Callable
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import get_args

from tools.agent_testing import ROOT, Environment, Stack

from . import checks, report, target
from .backend import Backend, State, create_fixture, create_paid_fixture, locked, now, owner_of
from .catalog import CATALOG, Catalog, Fixture, Profile, choose, load_catalog, matches

INVENTORY_MAX_AGE = 6 * 3600


def suite_dir(artifacts: Path) -> Path:
    return artifacts / "export-e2e"


def guarded(work: Callable[..., dict[str, object]], *args: object) -> dict[str, object]:
    """A server PDF observation, or the error that stopped it, without any URL it named."""
    from .server_pdf import withhold_urls

    try:
        return work(*args)
    except Exception as error:  # noqa: BLE001 — record it; the fixture's other checks stand.
        return {"error": withhold_urls(f"{type(error).__name__}: {str(error)[:300]}")}


class Resolver:
    """Pins a run to each fixture: the recorded one, a matching existing run, or a created one."""

    def __init__(
        self,
        catalog: Catalog,
        backend: Backend,
        state: State,
        inventory_path: Path,
        *,
        create: bool,
        refresh: bool,
        log: Callable[[str], None],
        allow_ai_cost: frozenset[str] = frozenset(),
    ):
        self.catalog, self.backend, self.state = catalog, backend, state
        self.inventory_path, self.create, self.refresh, self.log = inventory_path, create, refresh, log
        # Fixtures whose AI cost the owner approved for this invocation (Monte Carlo, tuning).
        self.allow_ai_cost = allow_ai_cost
        self._inventory: list[Profile] | None = None
        self.created: list[str] = []
        self.paid: list[str] = []
        self.resolved: dict[str, Profile] = {}
        self.usage_before: dict[str, object] | None = None

    def inventory(self) -> list[Profile]:
        if self._inventory is None:
            self._inventory = load_inventory(
                self.backend,
                self.state.environment,
                self.state.owner,
                self.inventory_path,
                refresh=self.refresh,
                log=self.log,
            )
        return self._inventory

    def resolve(self, fixture: Fixture, taken: set[str]) -> dict[str, object]:
        entry = self.state.fixtures.get(fixture.id)
        if entry and entry.get("run_id"):
            stale_script = (
                entry.get("origin") == "created"
                and fixture.script is not None
                and entry.get("spec_sha256") != fixture.script.spec_hash()
            )
            if not stale_script and (entry.get("origin") != "created" or entry.get("status") == "completed"):
                run = self.backend.profile(str(entry["run_id"]))
                if run is not None and run.status == "completed" and not (
                    entry.get("origin") == "reused" and fixture.match and matches(fixture.match, run)
                ):
                    return self._ready(fixture, run, str(entry["origin"]))
                if entry.get("origin") == "reused":
                    self.log(f"{fixture.id}: the pinned run {entry['run_id']} no longer qualifies")
                    self.state.fixtures.pop(fixture.id)
                    self.state.save()
        if fixture.match is not None:
            runs = self.inventory()
            chosen = choose(fixture, runs, exclude=taken) or choose(fixture, runs)
            if chosen is not None:
                self.state.fixtures[fixture.id] = {
                    "origin": "reused",
                    "run_id": chosen.run_id,
                    "study_id": chosen.study_id,
                    "pinned_at": now(),
                }
                self.state.save()
                return self._ready(fixture, chosen, "reused")
        if fixture.script is not None:
            if not self.create:
                return self._uncovered(fixture, "Not created yet; run without --no-create to create it.")
            if self.usage_before is None:
                self.usage_before = self.backend.usage()
            run_id = create_fixture(self.backend, self.state, fixture, log=self.log)
            self.created.append(run_id)
            run = self.backend.profile(run_id)
            assert run is not None
            return self._ready(fixture, run, "created")
        reason = f"No completed run on {self.state.environment} matches {json.dumps(fixture.match)}."
        if fixture.create_cost:
            reason += f" {fixture.create_cost}"
        recorded = self.state.fixtures.get(fixture.id) or {}
        if recorded.get("origin") == "created" and recorded.get("status") not in (None, "completed"):
            reason += f" The run the suite created for it, {recorded.get('run_id')}, {recorded.get('status')}."
        if fixture.derive is not None and self.create and fixture.id in self.allow_ai_cost:
            parent_id = None
            if fixture.derive.parent:
                parent = self.resolved.get(fixture.derive.parent)
                if parent is None:
                    parent = self.resolve(self.catalog.get(fixture.derive.parent), taken).get("run")  # type: ignore[assignment]
                parent_id = parent.run_id if parent else None
            run_id = create_paid_fixture(self.backend, self.state, fixture, parent_id, log=self.log)
            self.created.append(run_id)
            self.paid.append(fixture.id)
            run = self.backend.profile(run_id)
            assert run is not None
            return self._ready(fixture, run, "created")
        return self._uncovered(fixture, reason)

    def _ready(self, fixture: Fixture, run: Profile, origin: str) -> dict[str, object]:
        self.log(f"{fixture.id}: {origin} run {run.run_id}")
        return {"fixture": fixture, "status": "ready", "origin": origin, "run": run}

    def _uncovered(self, fixture: Fixture, reason: str) -> dict[str, object]:
        self.log(f"{fixture.id}: not covered. {reason}")
        return {"fixture": fixture, "status": "uncovered", "reason": reason}

    def resolve_all(self, fixtures: list[Fixture]) -> list[dict[str, object]]:
        # Runs the suite created belong to their script fixtures; discovery prefers others.
        taken = {
            str(entry["run_id"])
            for entry in self.state.fixtures.values()
            if entry.get("origin") == "created" and entry.get("run_id")
        }
        resolved = []
        for fixture in fixtures:
            item = self.resolve(fixture, taken)
            if item["status"] == "ready":
                taken.add(item["run"].run_id)  # type: ignore[union-attr]
                self.resolved[fixture.id] = item["run"]  # type: ignore[assignment]
            resolved.append(item)
        return resolved


def load_inventory(
    backend: Backend,
    environment: str,
    owner: str,
    path: Path,
    *,
    refresh: bool,
    log: Callable[[str], None],
) -> list[Profile]:
    """Every run's profile, cached for a few hours per environment and owner."""
    if path.exists() and not refresh:
        cached = json.loads(path.read_text())
        if cached.get("environment") == environment and cached.get("owner_sub") == owner and time.time() - cached.get("read_epoch", 0) < INVENTORY_MAX_AGE:
            return [Profile.from_json(p) for p in cached["profiles"]]
    log("Profiling the owner's runs (read only)…")
    profiles, errors = [], []
    for run_id in backend.execution_ids():
        try:
            run = backend.profile(run_id)
        except Exception as error:  # noqa: BLE001 — one unreadable run must not stop discovery.
            errors.append({"run_id": run_id, "error": f"{type(error).__name__}: {str(error)[:200]}"})
            continue
        if run is not None:
            profiles.append(run)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "environment": environment,
                "owner_sub": owner,
                "read_at": now(),
                "read_epoch": time.time(),
                "profiles": [p.to_json() for p in profiles],
                "errors": errors,
            },
            indent=1,
        )
    )
    log(f"Profiled {len(profiles)} runs ({len(errors)} unreadable) into {path}")
    return profiles


def git_commit() -> str:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True).stdout
        return commit + (" (uncommitted changes)" if dirty.strip() else "")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def fixture_record(item: dict[str, object]) -> dict[str, object]:
    fixture: Fixture = item["fixture"]  # type: ignore[assignment]
    run: Profile | None = item.get("run")  # type: ignore[assignment]
    record: dict[str, object] = {"id": fixture.id, "shape": fixture.shape, "notes": list(fixture.notes)}
    if run is not None:
        record.update(run_id=run.run_id, study_id=run.study_id, origin=item["origin"], execution_type=run.execution_type)
    if item["status"] == "uncovered":
        record.update(outcome="uncovered", reason=item["reason"])
    return record


def run_suite(args: argparse.Namespace, stack: Stack, catalog: Catalog) -> int:
    from . import server_pdf
    from .browser import Terminal  # Playwright is only needed for browser runs.

    ui_url = target.override_origin(args.ui_url, stack.environment) if args.ui_url else stack.ui_url
    overridden = ui_url != stack.ui_url.rstrip("/")
    # An overridden Terminal is named in the attempt, so its results aren't read as the environment's.
    where = f"{stack.environment}-ui-{re.sub(r'[^a-z0-9]+', '-', ui_url.split('://')[1].lower()).strip('-')}" if overridden else stack.environment
    attempt = args.attempt or f"export-e2e-{where}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    folder = args.artifacts / attempt
    if folder.exists():
        raise SystemExit(f"{folder} exists; attempts are never overwritten. Choose a new --attempt.")
    backend = Backend(stack, stack.bearer)
    owner = owner_of(stack)
    state_path = suite_dir(args.artifacts) / f"state-{stack.environment}.json"
    summary: dict[str, object] = {
        "attempt": attempt,
        "environment": stack.environment,
        "ui_url": ui_url,
        "ui_url_override_of": stack.ui_url if overridden else None,
        "backend_url": stack.backend_url,
        "started_at": now(),
        "suite_commit": git_commit(),
        "playwright": metadata.version("playwright"),
    }
    usage_before = backend.usage()
    with locked(state_path):
        state = State(state_path, stack.environment, owner)
        resolver = Resolver(
            catalog,
            backend,
            state,
            suite_dir(args.artifacts) / f"inventory-{stack.environment}.json",
            create=not args.no_create,
            refresh=args.refresh,
            log=print,
            allow_ai_cost=frozenset(args.allow_ai_cost or ()),
        )
        resolved = resolver.resolve_all(catalog.select(args.only))
    folder.mkdir(parents=True)
    (folder / "attempt.json").write_text(
        json.dumps(
            {
                "owner": args.owner,
                "scenario": "export-e2e",
                "kind": "export-e2e",
                "created_at": summary["started_at"],
                "environment": stack.environment,
                "services": target.identity(stack, ui_url),
                "fixtures": [fixture_record(item) for item in resolved],
            },
            indent=2,
        )
    )
    terminal = Terminal(stack, headed=args.headed, ready_timeout=args.ready_timeout, ui_url=ui_url)
    summary["chrome"] = terminal.version()
    exports = server_pdf.Exports(backend._request)  # The owner's bearer, as for every Backend read.
    posts: list[dict[str, object]] = []  # Each export the suite asked Platform for, for the cost record.
    probed = False  # The attempt-wide server PDF probes run once, on the first dashboard checked.
    records, observed = [], {}
    formats = [{"id": f.id, "menu_label": f.menu_label} for f in catalog.formats]
    offered: dict[str, set[bool]] = {f.id: set() for f in catalog.formats}
    try:
        for item in resolved:
            record = fixture_record(item)
            records.append(record)
            if item["status"] != "ready":
                continue
            fixture: Fixture = item["fixture"]  # type: ignore[assignment]
            run: Profile = item["run"]  # type: ignore[assignment]
            out = folder / "fixtures" / fixture.id
            record["evidence"] = str(out.relative_to(args.artifacts))
            print(f"{fixture.id}: checking the export of run {run.run_id}…", flush=True)
            out.mkdir(parents=True, exist_ok=True)
            (out / "profile.json").write_text(json.dumps(run.to_json(), indent=1))
            try:
                obs = terminal.observe(run.run_id, run.study_id, out, formats)
            except Exception as error:  # noqa: BLE001 — record and continue with the next fixture.
                record.update(outcome="error", error=f"{type(error).__name__}: {str(error)[:400]}")
                (out / "error.txt").write_text(traceback.format_exc())
                continue
            obs["fixture"] = fixture.id
            empty = run.status == "completed" and not run.categories
            print(f"{fixture.id}: asking Platform for its server PDF…", flush=True)
            obs["server_pdf"] = guarded(server_pdf.export_run, exports, run.run_id, out)
            posts.append({"fixture": fixture.id, "via": "api", "status": obs["server_pdf"].get("post", {}).get("status")})  # type: ignore[union-attr]
            if not empty and not probed and (obs.get("menu") or {}).get("found"):  # type: ignore[union-attr]
                probed = True
                cancel = guarded(server_pdf.cancel_probe, exports, run.run_id)
                obs["server_pdf_cancel"] = cancel
                posts.append({"fixture": fixture.id, "via": "api (cancel)", "status": cancel.get("post", {}).get("status")})  # type: ignore[union-attr]
                obs["server_pdf_other_owner"] = server_pdf.other_owner_probe(None, obs["server_pdf"].get("export_id"))  # type: ignore[arg-type]
                print(f"{fixture.id}: Export → PDF in the Terminal…", flush=True)
                ui = guarded(terminal.export_pdf, run.run_id, run.study_id, out)
                obs["ai_runs"].extend(ui.pop("ai_runs", []))  # type: ignore[union-attr]
                obs["server_pdf_ui"] = ui
                if ui.get("build") == "server":
                    posts.append({"fixture": fixture.id, "via": "Terminal", "status": ui.get("post_status")})
            (out / "observation.json").write_text(json.dumps(obs, indent=1))
            results = checks.evaluate(obs, fixture, catalog.formats, run)
            record["results"] = [r.to_json() for r in results]
            record["outcome"] = checks.outcome(results)
            record["pages"] = {k: v.get("pages") for k, v in (obs.get("pdf") or {}).items()}  # type: ignore[union-attr]
            (out / "result.json").write_text(json.dumps(record, indent=1))
            images = next((r.status for r in results if r.check == "chart-images"), "skip")
            observed[fixture.id] = (run, obs, images)
            for name, value in (obs.get("formats") or {}).items():  # type: ignore[union-attr]
                offered.setdefault(name, set()).add(bool(value.get("offered")))
            print(f"{fixture.id}: {record['outcome']}", flush=True)
    finally:
        terminal.close()
    summary["fixtures"] = records
    summary["chart_kinds"] = checks.chart_coverage(catalog.chart_kinds, observed)
    summary["formats"] = {
        f.menu_label: (
            "offered and checked" if f.implemented and True in offered.get(f.id, set())
            else "offered, no check yet" if True in offered.get(f.id, set())
            else "not offered by this build; skipped"
        )
        for f in catalog.formats
    }
    earlier = [
        f"{r['id']} ({r['run_id']})" for r in records if r.get("origin") == "created" and r["run_id"] not in resolver.created
    ]
    statement = (
        f"This attempt created {len(resolver.created)} research runs ({', '.join(resolver.created)}) from scripts "
        "without llm_call, for which Backend makes no model call."
        if resolver.created
        else "This attempt created no run."
    )
    if earlier:
        statement += f" Script fixtures created by an earlier prepare were reused: {', '.join(earlier)}."
    statement += (
        " The export checks only read: no assistant run is started (see no-assistant-runs). "
        "The owner's usage meters were read before and after; other activity on the account can also move them."
    )
    statement += server_pdf.cost_statement(posts)
    summary["server_pdf_requests"] = posts
    paid = {
        r["id"]: {
            k: state.fixtures[r["id"]].get(k)
            for k in ("run_id", "kind", "status", "run_requested_at", "usage_delta")
        }
        for r in records
        if state.fixtures.get(r["id"], {}).get("paid")
    }
    summary["cost"] = {
        "statement": statement,
        "usage_before": usage_before,
        "usage_after": backend.usage(),
        "paid_runs": paid,
    }
    summary["finished_at"] = now()
    (folder / "summary.json").write_text(json.dumps(summary, indent=1))
    (folder / "report.md").write_text(report.render(summary))
    backend.close()
    print(f"\nReport: {folder / 'report.md'}")
    for record in records:
        print(f"  {record['id']:<28} {record.get('outcome')}")
    return 1 if any(r.get("outcome") in ("fail", "error") for r in records) else 0


def prepare(args: argparse.Namespace, stack: Stack, catalog: Catalog) -> int:
    backend = Backend(stack, stack.bearer)
    state_path = suite_dir(args.artifacts) / f"state-{stack.environment}.json"
    with locked(state_path):
        state = State(state_path, stack.environment, owner_of(stack))
        resolver = Resolver(
            catalog,
            backend,
            state,
            suite_dir(args.artifacts) / f"inventory-{stack.environment}.json",
            create=not args.no_create,
            refresh=args.refresh,
            log=print,
            allow_ai_cost=frozenset(args.allow_ai_cost or ()),
        )
        resolved = resolver.resolve_all(catalog.select(args.only))
    print()
    for item in resolved:
        record = fixture_record(item)
        print(f"  {record['id']:<28} {record.get('origin') or record.get('outcome'):<10} {record.get('run_id') or record.get('reason')}")
    for name in resolver.paid:
        entry = state.fixtures[name]
        moved = {k: v for k, v in (entry.get("usage_delta") or {}).items() if v}
        print(f"\n{name}: run {entry['run_id']}; usage moved by {moved or 'nothing'}")
    if resolver.created:
        after = backend.usage()
        print("\nUsage meters before/after creating runs:")
        for label, usage in (("before", resolver.usage_before), ("after", after)):
            if usage is None:
                continue
            meters = ", ".join(f"{m['meter']} {m['used']}" for m in usage["meters"])  # type: ignore[index]
            print(f"  {label}: {meters}")
    backend.close()
    return 0


def inventory(args: argparse.Namespace, stack: Stack, catalog: Catalog) -> int:
    backend = Backend(stack, stack.bearer)
    profiles = load_inventory(
        backend,
        stack.environment,
        owner_of(stack),
        suite_dir(args.artifacts) / f"inventory-{stack.environment}.json",
        refresh=args.refresh,
        log=print,
    )
    kinds = sorted({k for p in profiles for k in p.chart_kinds})
    print(f"{len(profiles)} runs; chart kinds seen: {', '.join(kinds)}")
    for fixture in catalog.fixtures:
        if fixture.match is None:
            print(f"  {fixture.id:<28} script only")
            continue
        found = [p for p in profiles if not matches(fixture.match, p)]
        best = choose(fixture, profiles)
        print(f"  {fixture.id:<28} {len(found)} matching runs" + (f"; best {best.run_id}" if best else ""))
    backend.close()
    return 0


def fixtures(args: argparse.Namespace, catalog: Catalog) -> int:
    state_path = suite_dir(args.artifacts) / f"state-{args.env}.json"
    pinned = json.loads(state_path.read_text())["fixtures"] if state_path.exists() else {}
    for fixture in catalog.fixtures:
        entry = pinned.get(fixture.id, {})
        source = "script" if fixture.script else "reuse"
        print(f"{fixture.id:<28} {source:<7} {entry.get('origin', '-'):<8} {entry.get('run_id', '-')}  {fixture.shape}")
    return 0


def recheck(args: argparse.Namespace, catalog: Catalog) -> int:
    folder = args.artifacts / args.attempt
    failed = False
    for out in sorted((folder / "fixtures").glob("*/observation.json")):
        obs = json.loads(out.read_text())
        fixture = catalog.get(obs["fixture"])
        profile_path = out.with_name("profile.json")
        run = Profile.from_json(json.loads(profile_path.read_text())) if profile_path.exists() else None
        results = checks.evaluate(obs, fixture, catalog.formats, run)
        print(f"{fixture.id}: {checks.outcome(results)}")
        for result in results:
            if result.status != "pass":
                print(f"  {result.status:<5} {result.check}: {result.detail}")
        failed |= checks.outcome(results) == checks.FAIL
    return 1 if failed else 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m tools.export_e2e", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env", choices=get_args(Environment), default="dev")
    parser.add_argument("--config", type=Path, default=ROOT / "environments.toml")
    parser.add_argument("--artifacts", type=Path, default=ROOT / ".artifacts")
    parser.add_argument("--catalog", type=Path, default=CATALOG)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("fixtures")
    inv = sub.add_parser("inventory")
    inv.add_argument("--refresh", action="store_true", help="Re-read every run instead of the cached inventory")
    for name in ("prepare", "run"):
        command = sub.add_parser(name)
        command.add_argument("--only", action="append", help="A fixture id; repeatable")
        command.add_argument("--no-create", action="store_true", help="Reuse runs only; create nothing")
        command.add_argument("--refresh", action="store_true", help="Re-read every run when discovering")
        command.add_argument(
            "--allow-ai-cost",
            action="append",
            metavar="FIXTURE",
            help="Create this Monte Carlo or tuning fixture if no run exists; its report text "
            "spends the owner's study_ai allowance. Repeatable. Never re-creates a recorded run.",
        )
        if name == "run":
            command.add_argument("--attempt", help="Attempt folder name (default: export-e2e-<env>-<time>)")
            command.add_argument("--owner", default="export-e2e")
            command.add_argument("--headed", action="store_true")
            command.add_argument(
                "--ui-url",
                help="Drive this local Terminal origin (e.g. http://localhost:3002) instead of the "
                "environment's, with the environment's Backend, sign-in and fixtures",
            )
            command.add_argument("--ready-timeout", type=float, default=180, help="Seconds to wait for data-export-ready")
    check = sub.add_parser("recheck")
    check.add_argument("attempt")
    args = parser.parse_args()
    if getattr(args, "attempt", None) and not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,99}", args.attempt):
        parser.error("Use a simple attempt name, not a path.")
    catalog = load_catalog(args.catalog)
    if getattr(args, "ui_url", None):
        try:
            target.override_origin(args.ui_url, args.env)
        except ValueError as error:
            parser.error(str(error))
    for name in getattr(args, "allow_ai_cost", None) or ():
        if catalog.get(name).derive is None:
            parser.error(f"--allow-ai-cost names {name!r}, which the suite never creates at AI cost.")
    if args.command == "fixtures":
        sys.exit(fixtures(args, catalog))
    if args.command == "recheck":
        sys.exit(recheck(args, catalog))
    stack = Stack.load(args.config, args.env)
    handler = {"inventory": inventory, "prepare": prepare, "run": run_suite}[args.command]
    sys.exit(handler(args, stack, catalog))


if __name__ == "__main__":
    main()
