"""Only controlled HTTP transports; no credentials or live conversations."""

from datetime import datetime, timezone, timedelta
import hashlib
import json
from uuid import uuid4

import httpx
import pytest

from tools.clear_local_threads import clear, verify_admission


def test_local_account_only_and_actual_empty_verification(tmp_path):
    owner = str(uuid4())
    threads = [str(uuid4()), str(uuid4())]
    requests = []

    def serve(request):
        requests.append((request.method, request.url.path))
        if request.url.path == "/threads/search":
            return httpx.Response(200, json=[{"thread_id": t, "metadata": {"owner": owner}} for t in threads])
        threads.remove(request.url.path.split("/")[-1])
        return httpx.Response(204)

    with httpx.Client(base_url="http://localhost:2124", transport=httpx.MockTransport(serve)) as client:
        result = clear(client, owner, tmp_path)
    assert result["state"] == "LOCAL_ACCOUNT_CONVERSATIONS_EMPTY"
    assert result["initial_count"] == result["deleted_204"] == 2
    assert result["remaining_count"] == 0
    assert len([r for r in requests if r[0] == "DELETE"]) == 2


def test_foreign_owner_stops_before_any_delete(tmp_path):
    requests = []

    def serve(request):
        requests.append(request.method)
        return httpx.Response(200, json=[{"thread_id": str(uuid4()), "metadata": {"owner": "foreign"}}])

    with httpx.Client(base_url="http://localhost:2124", transport=httpx.MockTransport(serve)) as client:
        with pytest.raises(ValueError, match="foreign"):
            clear(client, str(uuid4()), tmp_path)
    assert requests == ["POST"]
    assert not (tmp_path / "inventory.json").exists()


def test_lost_delete_acknowledgement_stops_without_retry(tmp_path):
    owner = str(uuid4())
    ids = [str(uuid4()), str(uuid4())]
    deletes = []

    def serve(request):
        if request.method == "POST":
            return httpx.Response(200, json=[{"thread_id": i, "metadata": {"owner": owner}} for i in ids])
        deletes.append(request.url.path)
        raise httpx.ReadError("Lost response after acceptance")

    with httpx.Client(base_url="http://localhost:2124", transport=httpx.MockTransport(serve)) as client:
        with pytest.raises(ValueError, match="unknown"):
            clear(client, owner, tmp_path)
    assert len(deletes) == 1
    assert json.loads((tmp_path / "unknown-write-outcome.json").read_text())["state"] == "UNKNOWN_OUTCOME_STOP_NO_RETRY"


def test_new_threads_are_reported_instead_of_deleted_in_a_loop(tmp_path):
    owner = str(uuid4())
    thread = str(uuid4())
    searches = 0

    def serve(request):
        nonlocal searches
        if request.method == "POST":
            searches += 1
            return httpx.Response(200, json=[] if searches == 1 else [{"thread_id": thread, "metadata": {"owner": owner}}])
        raise AssertionError("No deletion authorized for a thread outside inventory")

    with httpx.Client(base_url="http://localhost:2124", transport=httpx.MockTransport(serve)) as client:
        with pytest.raises(ValueError, match="remain"):
            clear(client, owner, tmp_path)
    assert json.loads((tmp_path / "result.json").read_text())["remaining_count"] == 1


def test_admission_checks_hash_local_target_freshness_and_actual_health_file(tmp_path):
    health = tmp_path / "health.json"
    health.write_text('{"actual":"ready"}')
    admission = tmp_path / "runtime.json"
    record = {"state": "LOCAL_PRIMARY_STACK_READY", "environment": "local-up", "observed_at": datetime.now(timezone.utc).isoformat(),
              "services": {"backend_url": "http://localhost:9000", "ai_url": "http://localhost:2124", "ui_url": "http://localhost:3000"},
              "health_evidence": {"path": str(health), "sha256": hashlib.sha256(health.read_bytes()).hexdigest()}}

    def write(value):
        admission.write_text(json.dumps(value))
        return hashlib.sha256(admission.read_bytes()).hexdigest()

    assert verify_admission(admission, write(record), "local-up")["state"] == "LOCAL_PRIMARY_STACK_READY"
    bad = {**record, "services": {**record["services"], "ai_url": "https://hosted.example"}}
    with pytest.raises(ValueError, match="local stack"):
        verify_admission(admission, write(bad), "local-up")
    bad = {**record, "observed_at": (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()}
    with pytest.raises(ValueError, match="fresh"):
        verify_admission(admission, write(bad), "local-up")
    correct = write(record)
    health.write_text('{"changed":true}')
    with pytest.raises(ValueError, match="readiness evidence changed"):
        verify_admission(admission, correct, "local-up")
