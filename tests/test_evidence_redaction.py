from copy import deepcopy
from typing import cast

from langgraph_sdk.schema import Run

from tools.evidence_redaction import REDACTED, redact_run


def test_run_credentials_are_removed_only_from_owned_carriers():
    payload = {
        "messages": [{"content": "token issuance and authorization costs"}],
        "token": 15,
        "authorization": {"amount": 12},
        "config": {"configurable": {"authorization": "business evidence"}},
    }
    run = cast(Run, {
        "run_id": "run", "thread_id": "thread", "status": "success",
        "metadata": {"token": "classification", "source_id": "source"},
        "kwargs": {
            "input": payload,
            "command": {"resume": payload},
            "config": {"configurable": {
                "langgraph_auth_user": {
                    "identity": "owner", "display_name": "Analyst", "is_authenticated": True,
                    "authorization": "Bearer secret-user", "refresh_token": "secret-renewal",
                },
                "Authorization": "Bearer secret-header",
                "authorization": "Bearer secret-lowercase",
                "X-Refresh-Token": "secret-refresh-header",
                "x-refresh-token": "secret-refresh-lowercase",
                "thread_id": "thread", "run_id": "run", "model_choice": "selected",
            }},
            "context": {
                "bearer_token": "secret-context", "authorization": "secret-context-lowercase",
                "Authorization": "secret-context-header", "refresh_token": "secret-context-refresh",
                "model_choice": "selected", "research": payload,
            },
        },
    })
    original = deepcopy(run)
    expected = deepcopy(run)
    config = expected["kwargs"]["config"]["configurable"]
    config["langgraph_auth_user"]["authorization"] = REDACTED
    config["langgraph_auth_user"]["refresh_token"] = REDACTED
    for field in ("Authorization", "authorization", "X-Refresh-Token", "x-refresh-token"):
        config[field] = REDACTED
    for field in ("bearer_token", "authorization", "Authorization", "refresh_token"):
        expected["kwargs"]["context"][field] = REDACTED

    saved = redact_run(run)
    assert saved == expected
    assert run == original
    assert redact_run(saved) == saved
    assert saved is not run


def test_absent_and_null_auth_carriers_do_not_change_the_record():
    for run in (
        {"run_id": "run", "thread_id": "thread", "status": "success"},
        {"kwargs": {"config": None, "context": None}},
        {"kwargs": {"config": {"configurable": {
            "langgraph_auth_user": {"authorization": None}, "Authorization": None,
        }}, "context": {"authorization": None}}},
    ):
        assert redact_run(cast(Run, run)) == run
