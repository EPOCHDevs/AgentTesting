"""Offline campaign ledger and reports; never starts services or agent runs."""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import csv
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import tempfile
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "scenarios/coverage/catalog.json"
LANES = ("research", "ui_ux", "coordination")
LEVELS = ("design_review", "contract", "captured_browser", "native_integration", "deployed_journey")
STATUSES = ("not_run", "running", "blocked", "fail", "pass")


def stamp():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def file_hash(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def identifier(value):
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,79}", value):
        raise ValueError(f"Invalid identifier: {value!r}")
    return value


def text(value, name):
    if not isinstance(value, str) or not value.strip() or "REPLACE" in value or value == "ACTUAL_SESSION_ID":
        raise ValueError(f"Provide an actual {name}.")
    return value.strip()


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def locked(folder, name=".ledger.lock"):
    with (folder / name).open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def evidence(paths):
    result = []
    for value in paths:
        path = Path(value).resolve(strict=True)
        if not path.is_file():
            raise ValueError("Evidence must name an existing file.")
        result.append({"path": str(path), "sha256": file_hash(path)})
    return result


def validate_catalog(data):
    if data["schema_version"] != 1:
        raise ValueError("Unsupported catalogue version.")
    for name in ("agents", "families", "cases", "criteria"):
        ids = [row["id"] for row in data[name]]
        if not ids or len(ids) != len(set(ids)):
            raise ValueError(f"Duplicate or empty {name} IDs.")
    agents = {r["id"] for r in data["agents"]}
    families = {r["id"] for r in data["families"]}
    for case in data["cases"]:
        if case["agent_id"] not in agents or case["family"] not in families or case["phase"] not in ("P1", "P2", "P3"):
            raise ValueError("Invalid case assignment.")
    for criterion in data["criteria"]:
        if criterion["lane"] not in LANES or (criterion["agent_id"] and criterion["agent_id"] not in agents):
            raise ValueError("Invalid criterion assignment.")
    return data


class Campaign:
    def __init__(self, folder):
        self.folder = Path(folder)
        if self.folder.is_symlink():
            raise ValueError("Campaign directories must not be symlinks.")
        self.meta = json.loads((self.folder / "campaign.json").read_text())
        if file_hash(self.folder / "catalog.json") != self.meta["catalog_sha256"]:
            raise ValueError("Frozen campaign catalogue changed; create a new campaign.")
        self.catalog = validate_catalog(json.loads((self.folder / "catalog.json").read_text()))
        self.agents = {r["id"]: r for r in self.catalog["agents"]}
        self.cases = {r["id"]: r for r in self.catalog["cases"] if r["id"] in self.meta["case_ids"]}
        self.criteria = {r["id"]: r for r in self.catalog["criteria"]}

    @classmethod
    def create(cls, root, name, environment, *, catalog=CATALOG, case_ids=None):
        identifier(name)
        raw = Path(catalog).read_bytes()
        data = validate_catalog(json.loads(raw))
        available = {r["id"] for r in data["cases"]}
        selected = list(case_ids) if case_ids else [r["id"] for r in data["cases"]]
        if not selected or len(selected) != len(set(selected)) or not set(selected) <= available:
            raise ValueError("Select unique catalogue case IDs.")
        folder = Path(root) / name
        folder.mkdir(parents=True, exist_ok=False)
        (folder / "catalog.json").write_bytes(raw)
        meta = {"schema_version": 1, "id": name, "environment": environment,
                "created_at": stamp(), "catalog_sha256": hashlib.sha256(raw).hexdigest(),
                "case_ids": selected, "gates": {"target_ready": "pending", "account_ready": "pending"}}
        atomic(folder / "campaign.json", json.dumps(meta, indent=2) + "\n")
        return cls(folder)

    def events(self):
        events = []
        for path in sorted((self.folder / "agents").glob("*/events.jsonl")):
            if path.is_symlink() or path.parent.is_symlink():
                raise ValueError("Agent logs must not be symlinks.")
            previous = None
            for line in path.read_text().splitlines():
                row = json.loads(line)
                expected = row["hash"]
                body = {k: v for k, v in row.items() if k != "hash"}
                if digest(body) != expected or row["previous_hash"] != previous or row["agent_id"] != path.parent.name:
                    raise ValueError(f"Ledger integrity failure: {path}")
                previous = expected
                events.append(row)
        events.sort(key=lambda r: r["index"])
        if [r["index"] for r in events] != list(range(1, len(events) + 1)):
            raise ValueError("Missing or duplicate ledger events.")
        return events

    def binding(self, events, agent):
        return next((r for r in reversed(events) if r["agent_id"] == agent and r["kind"] == "claim"), None)

    def gates(self, events):
        gates = dict(self.meta["gates"])
        for row in events:
            if row["kind"] == "gate":
                gates[row["data"]["name"]] = row["data"]["status"]
        return gates

    def plan_for(self, events, attempt):
        return next((r for r in events if r["kind"] == "plan" and r["data"]["attempt_id"] == attempt), None)

    def owner(self, case_id, criterion_id):
        return self.criteria[criterion_id]["agent_id"] or self.cases[case_id]["agent_id"]

    def append(self, agent, kind, data):
        if agent not in self.agents:
            raise ValueError("Unknown agent_id.")
        with locked(self.folder):
            events = self.events()
            binding = self.binding(events, agent)
            if kind != "claim" and binding is None:
                raise ValueError("Claim this assignment with its actual agent chat/session first.")
            self.validate(events, agent, kind, data, binding)
            previous = next((r["hash"] for r in reversed(events) if r["agent_id"] == agent), None)
            row = {"index": len(events) + 1, "id": str(uuid4()), "at": stamp(),
                   "agent_id": agent, "agent_thread_id": data["thread_id"] if kind == "claim" else binding["data"]["thread_id"],
                   "assignment_revision": (binding["assignment_revision"] + 1 if binding else 1) if kind == "claim" else binding["assignment_revision"],
                   "kind": kind, "data": data, "previous_hash": previous}
            row["hash"] = digest(row)
            folder = self.folder / "agents" / agent
            if folder.is_symlink():
                raise ValueError("Agent directory must not be a symlink.")
            folder.mkdir(parents=True, exist_ok=True)
            with (folder / "events.jsonl").open("a") as stream:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            return row

    def validate(self, events, agent, kind, data, binding):
        if kind == "claim":
            text(data["thread_id"], "agent chat/session identity")
            if binding and binding["data"]["thread_id"] != data["thread_id"]:
                text(data.get("handoff"), "handoff reason for reassignment")
            return
        if kind == "gate":
            if agent != "campaign-coordinator" or data["name"] not in self.meta["gates"] or data["status"] not in ("pending", "ready", "blocked"):
                raise ValueError("Only the coordinator can update a known readiness gate.")
            text(data["note"], "gate explanation")
            if data["status"] == "ready" and not data["evidence"]:
                raise ValueError("A ready gate requires evidence.")
            return
        case_id, attempt = data["case_id"], identifier(data["attempt_id"])
        if case_id not in self.cases:
            raise ValueError("Case is outside the frozen campaign.")
        plan = self.plan_for(events, attempt)
        if kind == "plan":
            if agent != self.cases[case_id]["agent_id"] or plan:
                raise ValueError("Only the case lead can freeze a new, unique attempt plan.")
            config = data["plan"]
            for key in ("scope", "design_reference", "fixture"):
                text(config.get(key), key)
            if not config.get("revisions") or not isinstance(config["revisions"], dict):
                raise ValueError("Name the exact target revisions.")
            for revision in config["revisions"].values():
                text(revision, "revision")
            budget = config.get("budget_usd")
            if isinstance(budget, bool) or not isinstance(budget, (float, int)) or not math.isfinite(budget) or budget < 0:
                raise ValueError("Declare a nonnegative budget; 0 for a no-paid-work fixture.")
            checks = config.get("checks", {})
            if set(checks) != set(self.criteria):
                raise ValueError("Declare every criterion: a target evidence mode or a concrete exclusion reason.")
            for criterion, value in checks.items():
                if set(value) == {"exclude"}:
                    text(value["exclude"], "exclusion reason")
                elif set(value) == {"target"} and value["target"] in LEVELS:
                    if criterion.startswith("U") and value["target"] not in ("captured_browser", "deployed_journey"):
                        raise ValueError("UI criteria require browser evidence.")
                else:
                    raise ValueError("Each check needs exactly one target or exclusion.")
            if all("exclude" in v for v in checks.values()):
                raise ValueError("A plan must require at least one check.")
            return
        if not plan or plan["data"]["case_id"] != case_id:
            raise ValueError("Freeze this attempt's case plan first.")
        if kind in ("record", "finding"):
            criterion = data["criterion_id"]
            if criterion not in self.criteria or agent != self.owner(case_id, criterion):
                raise ValueError("Only the responsible criterion agent can report this check.")
            if "exclude" in plan["data"]["plan"]["checks"][criterion]:
                raise ValueError("Check excluded by the frozen plan; use a new attempt to expand scope.")
            text(data["note"], "observation")
        if kind == "record":
            if data["status"] not in STATUSES:
                raise ValueError("Invalid check status.")
            prior = next((r for r in reversed(events) if r["kind"] == "record" and r["data"]["attempt_id"] == attempt and r["data"]["criterion_id"] == data["criterion_id"]), None)
            if prior and prior["data"]["status"] == "fail" and data["status"] != "fail":
                raise ValueError("Keep the failed attempt; freeze a new attempt for the correction or reassessment.")
            if data["status"] in ("running", "pass") and set(self.gates(events).values()) != {"ready"}:
                raise ValueError("Readiness/account gates are not ready; report blockers without starting work.")
            if data["status"] in ("pass", "fail"):
                if not data["evidence"]:
                    raise ValueError("Pass/fail requires retained evidence.")
                if data["evidence_level"] != plan["data"]["plan"]["checks"][data["criterion_id"]]["target"]:
                    raise ValueError("Actual evidence mode must match the frozen target; no silent substitution.")
        elif kind == "finding":
            identifier(data["correlation_key"])
            if data["state"] not in ("open", "resolved") or not data["evidence"]:
                raise ValueError("A finding needs a known state and evidence.")
        elif kind == "review":
            if agent != "campaign-review" or data["decision"] not in ("pass", "fail", "blocked"):
                raise ValueError("Only the independent reviewer records a verdict.")
            authored = {r["agent_thread_id"] for r in events if r["kind"] in ("plan", "record", "finding") and r["data"]["attempt_id"] == attempt}
            if binding["data"]["thread_id"] in authored:
                raise ValueError("Independent review requires a different actual agent/person.")
            text(data["note"], "review explanation")
            if not data["evidence"]:
                raise ValueError("Review requires evidence.")
            current = self.attempt(events, plan)
            if data["decision"] == "pass" and current["check_status"] != "pass":
                raise ValueError("Every required check needs current passing evidence before review can pass.")
            if data["decision"] == "pass" and current["open_blockers"]:
                raise ValueError("Resolve this attempt's blocking findings before passing review.")
            data["basis"] = current["basis"]
        else:
            raise ValueError("Unknown event kind.")

    def attempt(self, events, plan, cache=None):
        cache = {} if cache is None else cache

        def current(refs):
            for ref in refs:
                key = (ref["path"], ref["sha256"])
                if key not in cache:
                    try:
                        cache[key] = file_hash(ref["path"]) == ref["sha256"]
                    except OSError:
                        cache[key] = False
                if not cache[key]:
                    return False
            return True

        config = plan["data"]; attempt = config["attempt_id"]
        records = {r["data"]["criterion_id"]: r for r in events if r["kind"] == "record" and r["data"]["attempt_id"] == attempt}
        checks = []
        for criterion_id, requirement in config["plan"]["checks"].items():
            record = records.get(criterion_id)
            state = "not_applicable" if "exclude" in requirement else record["data"]["status"] if record else "not_run"
            if record and record["data"]["evidence"] and not current(record["data"]["evidence"]):
                state = "stale_evidence"
            checks.append({"id": criterion_id, "lane": self.criteria[criterion_id]["lane"],
                           "agent_id": self.owner(config["case_id"], criterion_id), "status": state,
                           "note": requirement.get("exclude") or (record["data"]["note"] if record else ""),
                           "evidence": record["data"]["evidence"] if record else []})
        basis = digest({"plan": plan["hash"], "records": sorted(r["hash"] for r in records.values())})
        verdict = next((r for r in reversed(events) if r["kind"] == "review" and r["data"]["attempt_id"] == attempt), None)
        findings = {}
        for row in events:
            if row["kind"] == "finding" and row["data"]["attempt_id"] == attempt:
                findings[(row["agent_id"], row["data"]["criterion_id"], row["data"]["correlation_key"])] = row
        blockers = [r["id"] for r in findings.values() if r["data"]["state"] == "open" and r["data"].get("blocking", True)]
        basis = digest({"assessment_basis": basis, "findings": sorted(r["hash"] for r in findings.values())})
        checks_status = rollup([r["status"] for r in checks])
        outcome = "awaiting_review" if checks_status == "pass" else checks_status
        if blockers and outcome != "fail":
            outcome = "blocked"
        if verdict and verdict["data"]["basis"] == basis and current(verdict["data"]["evidence"]):
            outcome = verdict["data"]["decision"] if verdict["data"]["decision"] != "pass" or (checks_status == "pass" and not blockers) else outcome
        return {"attempt_id": attempt, "case_id": config["case_id"], "at": plan["at"],
                "status": outcome, "check_status": checks_status, "basis": basis, "checks": checks,
                "lanes": {lane: rollup([r["status"] for r in checks if r["lane"] == lane]) for lane in LANES},
                "scope": config["plan"]["scope"], "revisions": config["plan"]["revisions"], "open_blockers": blockers}

    def snapshot(self):
        with locked(self.folder):
            events = self.events()
        cache = {}
        attempts = [self.attempt(events, r, cache) for r in events if r["kind"] == "plan"]
        cases = []
        for case in self.cases.values():
            history = [r for r in attempts if r["case_id"] == case["id"]]
            latest = history[-1] if history else None
            cases.append({**case, "status": latest["status"] if latest else "not_run",
                          "lanes": latest["lanes"] if latest else dict.fromkeys(LANES, "not_run"),
                          "attempt_id": latest["attempt_id"] if latest else None,
                          "history": history, "checks": latest["checks"] if latest else [],
                          "prior_failed_attempts": sum(r["status"] == "fail" for r in history[:-1])})
        findings = {}
        for event in events:
            if event["kind"] != "finding":
                continue
            d = event["data"]
            key = (event["agent_id"], d["attempt_id"], d["criterion_id"], d["correlation_key"])
            original = findings.get(key, {})
            findings[key] = {**d, "agent_id": event["agent_id"],
                             "agent_thread_id": original.get("agent_thread_id", event["agent_thread_id"]),
                             "finding_id": original.get("finding_id", event["id"]),
                             "event_id": event["id"], "reported_at": original.get("reported_at", event["at"]), "updated_at": event["at"]}
        groups = {}
        for row in findings.values():
            key = row["issue_id"] or row["correlation_key"]
            groups.setdefault(key, []).append(row)
        agents = []
        for agent in self.agents.values():
            binding = self.binding(events, agent["id"])
            owned_checks = [r for c in cases for r in c["checks"] if r["agent_id"] == agent["id"]]
            agent_events = [r for r in events if r["agent_id"] == agent["id"]]
            agents.append({**agent, "status": "unassigned" if not binding else rollup([r["status"] for r in owned_checks]) if owned_checks else "assigned",
                           "agent_thread_id": binding["data"]["thread_id"] if binding else None,
                           "assignment_revision": binding["assignment_revision"] if binding else None,
                           "last_update": agent_events[-1]["at"] if agent_events else None,
                           "folder": str(self.folder / "agents" / agent["id"]),
                           "checks": dict(Counter(r["status"] for r in owned_checks))})
        return {"schema_version": 1, "campaign": self.meta, "generated_at": stamp(),
                "gates": self.gates(events), "counts": dict(Counter(c["status"] for c in cases)),
                "gate_details": {name: next(({**r["data"], "at": r["at"], "agent_id": r["agent_id"]} for r in reversed(events) if r["kind"] == "gate" and r["data"]["name"] == name), None) for name in self.meta["gates"]},
                "lanes": {lane: dict(Counter(c["lanes"][lane] for c in cases)) for lane in LANES},
                "cases": cases, "agents": agents, "findings": list(findings.values()),
                "correlations": [{"key": key, "reports": len(values), "open": sum(r["state"] == "open" for r in values),
                                  "cases": sorted({r["case_id"] for r in values}), "agents": sorted({r["agent_id"] for r in values})} for key, values in groups.items()],
                "catalog": self.catalog}

    def report(self):
        folder = self.folder / "reports"
        folder.mkdir(exist_ok=True)
        with locked(folder, ".report.lock"):
            data = self.snapshot()
            atomic(folder / "summary.json", json.dumps(data, indent=2, ensure_ascii=False) + "\n")
            csv_file(folder / "scenario-status.csv", data["cases"], ["id", "family", "phase", "agent_id", "status", "attempt_id", "prior_failed_attempts"], lanes=True)
            csv_file(folder / "agent-status.csv", data["agents"], ["id", "section", "status", "agent_thread_id", "assignment_revision", "last_update", "folder"])
            csv_file(folder / "findings.csv", data["findings"], ["finding_id", "case_id", "criterion_id", "attempt_id", "agent_id", "agent_thread_id", "correlation_key", "issue_id", "state", "note"])
            template = (Path(__file__).with_name("campaign_dashboard.html")).read_text()
            payload = json.dumps(data, ensure_ascii=True).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
            atomic(folder / "dashboard.html", template.replace("/*CAMPAIGN_DATA*/null", payload))
            lines = [f"# {self.meta['id']}", "", f"Environment: {self.meta['environment']} · Updated: {data['generated_at']}", "",
                     " · ".join(f"{key}: {value}" for key, value in data["counts"].items()), "",
                     "[Dashboard](dashboard.html) · [Scenario CSV](scenario-status.csv) · [Agent CSV](agent-status.csv) · [Findings CSV](findings.csv)", "",
                     "| Case | Agent | Research | UI/UX | Coordination | Overall |", "| --- | --- | --- | --- | --- | --- |"]
            lines += ["| " + " | ".join([r["id"], r["agent_id"], *[r["lanes"][lane] for lane in LANES], r["status"]]) + " |" for r in data["cases"]]
            atomic(folder / "status.md", "\n".join(lines) + "\n")
        return {"dashboard": str(folder / "dashboard.html"), "counts": data["counts"], "gates": data["gates"]}


def rollup(states):
    required = [s for s in states if s != "not_applicable"]
    if not required:
        return "not_applicable"
    for state in ("fail", "stale_evidence", "blocked", "running"):
        if state in required:
            return state
    if all(s == "pass" for s in required):
        return "pass"
    return "running" if "pass" in required else "not_run"


def csv_file(path, rows, fields, lanes=False):
    import io
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fields + (list(LANES) if lanes else []))
    writer.writeheader()
    for row in rows:
        values = {key: row.get(key) for key in fields}
        if lanes:
            values.update(row["lanes"])
        # Keep user-entered notes/IDs literal in spreadsheet consumers.
        writer.writerow({k: "'" + str(v) if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r") else v for k, v in values.items()})
    atomic(path, buffer.getvalue())


def plan_template(campaign, case_id):
    return {"scope": campaign.cases[case_id]["journey"], "design_reference": "REPLACE with accepted design/flow reference or reason no UI is in scope",
            "fixture": "REPLACE with owned fixture and mutation scope", "revisions": {"target": "REPLACE with exact target identities"},
            "budget_usd": 0,
            "checks": {r["id"]: {"target": "captured_browser" if r["lane"] == "ui_ux" else "native_integration"} for r in campaign.catalog["criteria"]}}


def handoff(campaign, agent_id, case_id):
    if agent_id not in campaign.agents or case_id not in campaign.cases:
        raise ValueError("Choose a campaign agent and case.")
    lead = campaign.cases[case_id]["agent_id"]
    checks = [r["id"] for r in campaign.catalog["criteria"] if r["agent_id"] == agent_id or (not r["agent_id"] and agent_id == lead)]
    base = shlex.quote(str(ROOT / ".venv/bin/python")) + " -m tools.campaign --root " + shlex.quote(str(campaign.folder.parent))
    name = shlex.quote(campaign.meta["id"])
    lines = [f"agent_id: {agent_id} | case: {case_id} | case lead: {lead}",
             f"Goal: {campaign.cases[case_id]['journey']}", f"Checks: {', '.join(checks) or 'campaign oversight only'}.",
             "Read scenarios/coverage/README.md. Wait for coordinator readiness/account gates.",
             f"Bind your actual session: {base} claim {name} --agent {agent_id} --thread ACTUAL_SESSION_ID",
             "Use only your own agents/<agent_id>/ folder. Coordinate fixture writes with the case lead; do not launch duplicate work."]
    if agent_id == lead:
        plan_path = shlex.quote(str(campaign.folder / "agents" / agent_id / f"{case_id.lower()}-plan.json"))
        lines += [f"Prepare scope: {base} template {name} --case {case_id} > {plan_path}",
                  "Fill exact revisions/design/fixture/budget. For inapplicable checks use {\"exclude\":\"concrete reason\"}; never remove checks.",
                  f"Freeze: {base} plan {name} --agent {agent_id} --case {case_id} --attempt UNIQUE_ATTEMPT --input {plan_path}"]
        if campaign.cases[case_id]["family"] == "MC":
            lines += ["Drive the declared MCP protocol harness, Claude Code or Codex client against the frozen endpoint; retain actual MCP calls/results and human decisions.",
                      "Freeze driver/client/model/tool-manifest identities and request/time/cost limits. Use separate campaigns for separate drivers.",
                      "tools.agent_testing ask/answer bypasses MCP and cannot prove this journey. No MCP orchestration runner is implied by this handoff."]
        else:
            lines += [f"Run/capture with existing tools.agent_testing --env {campaign.meta['environment']} and --owner {agent_id} --scenario {case_id}; only ask/answer start model work."]
    if agent_id == "campaign-review":
        lines += ["Review the actual frozen plan, required cells, evidence and open findings. Use a different actual session from every author.",
                  f"Verdict: {base} review {name} --agent campaign-review --case {case_id} --attempt LEAD_ATTEMPT --decision OBSERVED_DECISION --evidence REVIEW_FILE --note 'review conclusion'",
                  "Choose pass, fail or blocked; all required checks and blocking findings must permit a pass."]
    elif agent_id == "campaign-coordinator":
        lines += ["Bind actual agents; own readiness/account gates, exclusive fixtures, budget and handoffs. Do not infer readiness from a process starting.",
                  f"Gate: {base} gate {name} --agent campaign-coordinator --name GATE_NAME --status OBSERVED_STATUS --evidence RECEIPT_FILE --note 'actual readiness result'"]
    elif checks:
        lines += [f"Report: {base} record {name} --agent {agent_id} --case {case_id} --attempt LEAD_ATTEMPT --criterion CHECK_ID --status OBSERVED_STATUS --level TARGET_MODE --evidence RETAINED_FILE --note 'specific observation'",
                  "Choose not_run, running, blocked, fail or pass from the actual evidence; never infer a pass from a process exit.",
                  "For errors use finding with the same case/attempt/criterion, --key shared-symptom, --evidence and --note; add --issue only for an existing/authorized issue."]
    else:
        lines += ["Provide the accepted design/flow reference and usability observations to the case lead; the lead pins them in the plan. Do not claim criterion cells owned by another assignment."]
    lines += [
              "Unknown write outcome: stop writes; retain the original identity. Preserve failed attempts. No credentials/raw tool payloads in notes.",
              "Request an actual independent campaign-review agent; a different label on your own session is not independent.",
              f"Refresh review: {base} report {name}"]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT / "campaigns")
    sub = parser.add_subparsers(dest="action", required=True)
    for name in ("init", "claim", "gate", "template", "plan", "record", "finding", "review", "handoff", "report", "status"):
        cmd = sub.add_parser(name)
        cmd.add_argument("campaign", type=identifier)
        if name == "init":
            cmd.add_argument("--env", required=True, choices=("local", "local-up", "dev", "qa", "prod"))
            cmd.add_argument("--case", action="append")
        if name in ("claim", "gate", "plan", "record", "finding", "review", "handoff"):
            cmd.add_argument("--agent", required=True)
        if name == "claim":
            cmd.add_argument("--thread", required=True)
            cmd.add_argument("--handoff")
        if name in ("template", "plan", "record", "finding", "review", "handoff"):
            cmd.add_argument("--case", required=True)
        if name in ("plan", "record", "finding", "review"):
            cmd.add_argument("--attempt", required=True, type=identifier)
        if name == "plan":
            cmd.add_argument("--input", type=Path, required=True)
        if name in ("gate", "record", "finding", "review"):
            cmd.add_argument("--note", required=True)
            cmd.add_argument("--evidence", action="append", default=[])
        if name == "gate":
            cmd.add_argument("--name", required=True)
            cmd.add_argument("--status", required=True, choices=("pending", "ready", "blocked"))
        if name in ("record", "finding"):
            cmd.add_argument("--criterion", required=True)
        if name == "record":
            cmd.add_argument("--status", required=True, choices=STATUSES)
            cmd.add_argument("--level", choices=LEVELS)
        if name == "finding":
            cmd.add_argument("--key", required=True, type=identifier)
            cmd.add_argument("--state", default="open", choices=("open", "resolved"))
            cmd.add_argument("--issue")
            cmd.add_argument("--nonblocking", action="store_true", help="Explicitly classify an observation as nonblocking")
        if name == "review":
            cmd.add_argument("--decision", required=True, choices=("pass", "fail", "blocked"))
    args = parser.parse_args()
    try:
        if args.action == "init":
            campaign = Campaign.create(args.root, args.campaign, args.env, case_ids=args.case)
            result = campaign.report()
        else:
            campaign = Campaign(args.root / args.campaign)
            if args.action == "template":
                result = plan_template(campaign, args.case)
            elif args.action == "handoff":
                result = handoff(campaign, args.agent, args.case)
            elif args.action == "report":
                result = campaign.report()
            elif args.action == "status":
                snapshot = campaign.snapshot()
                result = {key: snapshot[key] for key in ("generated_at", "gates", "counts", "lanes", "correlations")}
            else:
                data = {}
                if args.action == "claim":
                    data = {"thread_id": args.thread, "handoff": args.handoff}
                elif args.action == "gate":
                    data = {"name": args.name, "status": args.status}
                else:
                    data = {"case_id": args.case, "attempt_id": args.attempt}
                    if args.action == "plan":
                        data["plan"] = json.loads(args.input.read_text())
                    elif args.action == "record":
                        data.update(criterion_id=args.criterion, status=args.status, evidence_level=args.level)
                    elif args.action == "finding":
                        data.update(criterion_id=args.criterion, correlation_key=args.key, state=args.state, issue_id=args.issue, blocking=not args.nonblocking)
                    elif args.action == "review":
                        data["decision"] = args.decision
                if args.action in ("gate", "record", "finding", "review"):
                    data.update(note=args.note, evidence=evidence(args.evidence))
                event = campaign.append(args.agent, args.action, data)
                result = {"event_id": event["id"], "agent_id": event["agent_id"], "kind": event["kind"], "at": event["at"]}
                result.update(campaign.report())
        print(result if isinstance(result, str) else json.dumps(result, indent=2))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(1, f"Campaign error: {exc}\n")


if __name__ == "__main__":
    main()
