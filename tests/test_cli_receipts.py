"""The real CLI must not print authenticated configuration echoed by the server."""

import json
import sys

import pytest

from tools import agent_testing as app


@pytest.mark.parametrize("action", ["ask", "answer", "status", "attach"])
def test_cli_prints_status_and_questions_without_server_run_configuration(
    action, monkeypatch, capsys
):
    server_run = {
        "run_id": "run-1",
        "thread_id": "thread-1",
        "status": "interrupted",
        "created_at": "2026-10-26T13:05:09Z",
        "kwargs": {
            "config": {"configurable": {"langgraph_auth_user": {
                "authorization": "Bearer DO-NOT-PRINT"
            }}},
            "context": {"api_key": "DO-NOT-PRINT"},
        },
        "metadata": {"new_server_field": "DO-NOT-PRINT"},
    }
    pending = {"interrupt-1": {"question": "Which fiscal period?"}}

    async def fake_run(args):
        if action in ("ask", "answer"):
            return server_run
        return {
            "checked_at": "2026-10-26T13:05:10Z",
            "environment": "local",
            "thread_id": "thread-1",
            "thread_status": "needs_input",
            "pending": pending,
            "runs": [server_run],
        }

    argv = ["agent_testing", "--env", "local", action, "case-1"]
    if action == "ask":
        argv += ["--key", "opening", "Compare the two businesses"]
    elif action == "answer":
        argv += ["--key", "clarification", "--responses", "unused.json"]
    elif action == "attach":
        argv += ["thread-1"]
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(app, "run", fake_run)
    app.main()
    output = capsys.readouterr().out
    assert "DO-NOT-PRINT" not in output
    assert "authorization" not in output and "kwargs" not in output
    rendered = json.loads(output)
    receipt = rendered if action in ("ask", "answer") else rendered["runs"][0]
    assert receipt == {
        "run_id": "run-1", "thread_id": "thread-1", "status": "interrupted",
        "created_at": "2026-10-26T13:05:09Z",
    }
    if action in ("status", "attach"):
        assert rendered["pending"] == pending
    assert server_run["kwargs"]["context"]["api_key"] == "DO-NOT-PRINT"
