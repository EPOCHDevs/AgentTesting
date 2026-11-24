"""Explicit account-scoped local conversation cleanup; no model or run submission."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

import httpx


def save(path: Path, value: object) -> None:
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")


def verify_admission(path: Path, expected_hash: str, environment: str) -> dict:
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_hash:
        raise ValueError("Runtime admission hash differs.")
    receipt = json.loads(raw)
    if receipt.get("state") != "LOCAL_PRIMARY_STACK_READY" or receipt.get("environment") != environment:
        raise ValueError("A completed matching local runtime admission is required.")
    at = datetime.fromisoformat(receipt["observed_at"])
    age = (datetime.now(timezone.utc) - at).total_seconds()
    if not 0 <= age <= 900:
        raise ValueError("Runtime admission is not fresh.")
    ports = {"backend_url": 9000, "ai_url": 2124 if environment == "local-up" else 2024, "ui_url": 3000}
    for key, port in ports.items():
        url = urlsplit(receipt["services"][key])
        if (url.scheme != "http" or url.hostname not in ("localhost", "127.0.0.1") or url.port != port
                or url.username or url.password or url.query or url.fragment or url.path not in ("", "/")):
            raise ValueError("Cleanup is restricted to the admitted primary local stack.")
    health = receipt["health_evidence"]
    if hashlib.sha256(Path(health["path"]).read_bytes()).hexdigest() != health["sha256"]:
        raise ValueError("Retained actual readiness evidence changed.")
    return receipt


def owned_threads(http: httpx.Client, owner: str) -> list[str]:
    found = []
    offset = 0
    while True:
        response = http.post("/threads/search", json={
            "limit": 100, "offset": offset, "sort_by": "created_at", "sort_order": "asc",
            "select": ["thread_id", "metadata", "status"],
        })
        if response.status_code != 200:
            raise ValueError(f"Owned conversation listing failed (HTTP {response.status_code}); no identity fallback.")
        rows = response.json()
        if not isinstance(rows, list) or len(rows) > 100:
            raise ValueError("Unexpected conversation-list shape.")
        for row in rows:
            metadata = row.get("metadata")
            if not isinstance(metadata, dict) or metadata.get("owner") != owner:
                raise ValueError("Listing returned a foreign or unverified conversation; no deletes permitted.")
            identifier = row["thread_id"]
            if not isinstance(identifier, str) or str(UUID(identifier)) != identifier or identifier in found:
                raise ValueError("Unexpected or duplicate conversation identity.")
            found.append(identifier)
        if len(rows) < 100:
            return found
        offset += len(rows)


def clear(http: httpx.Client, owner: str, folder: Path) -> dict:
    inventory = owned_threads(http, owner)
    save(folder / "inventory.json", {"thread_ids": inventory, "count": len(inventory),
                                   "owner_subject_sha256": hashlib.sha256(owner.encode()).hexdigest()})
    results = []
    for index, identifier in enumerate(inventory, 1):
        before = {"index": index, "thread_id": identifier, "method": "DELETE", "scope": "verified local account"}
        save(folder / f"{index:05}-before-delete.json", before)
        try:
            response = http.delete("/threads/" + identifier)
        except httpx.HTTPError:
            save(folder / "unknown-write-outcome.json", {**before, "state": "UNKNOWN_OUTCOME_STOP_NO_RETRY"})
            raise ValueError("Conversation deletion outcome is unknown; stopped without retry.") from None
        result = {**before, "http_status": response.status_code}
        save(folder / f"{index:05}-delete-result.json", result)
        if response.status_code not in (204, 404):
            raise ValueError(f"Conversation deletion refused or unrecognized (HTTP {response.status_code}); stopped without retry.")
        results.append(result)
    remaining = owned_threads(http, owner)
    result = {"state": "LOCAL_ACCOUNT_CONVERSATIONS_EMPTY" if not remaining else "NEW_OR_REMAINING_THREADS_NO_AUTOMATIC_DELETE_LOOP",
              "initial_count": len(inventory), "deleted_204": sum(r["http_status"] == 204 for r in results),
              "already_absent_404": sum(r["http_status"] == 404 for r in results),
              "remaining_count": len(remaining), "remaining_thread_ids": remaining,
              "model_or_run_submissions": 0, "tokens_retained": False}
    save(folder / "result.json", result)
    if remaining:
        raise ValueError("Conversations remain or appeared after inventory; no automatic repeat deletion.")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", required=True, choices=("local", "local-up"))
    parser.add_argument("--expected-email", required=True)
    parser.add_argument("--runtime-admission", required=True, type=Path)
    parser.add_argument("--runtime-sha256", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--execute", required=True, action="store_true", help="Execute the explicitly requested local account cleanup")
    args = parser.parse_args()
    try:
        receipt = verify_admission(args.runtime_admission, args.runtime_sha256, args.environment)
        args.output.mkdir(parents=True, mode=0o700, exist_ok=False)
        save(args.output / "intent.json", {"environment": args.environment, "expected_email": args.expected_email,
             "runtime_admission_sha256": args.runtime_sha256, "services": receipt["services"],
             "authorized_scope": "All conversations visible to this verified local account; no hosted/Codex threads"})
        # Maintained normal login/refresh only. Never read/decode claims or select another identity.
        from tools import local_auth
        tokens = local_auth.cached_tokens(email=args.expected_email)
        if tokens.get("user_pool_id", local_auth.POOL) != local_auth.POOL:
            raise ValueError("The local login names another user pool.")
        user = local_auth.client(**{key: tokens[key] for key in ("username", "id_token", "access_token", "refresh_token")})
        try:
            profile = user.client.get_user(AccessToken=tokens["access_token"])
            attributes = {row["Name"]: row["Value"] for row in profile["UserAttributes"]}
            email = attributes["email"]
            owner = attributes["sub"]
            if email.strip().casefold() != args.expected_email.strip().casefold() or str(UUID(owner)) != owner:
                raise ValueError
        except Exception:
            raise ValueError("Normal provider account verification failed; no deletion or identity fallback.") from None
        save(args.output / "verified-account.json", {"email": email, "owner_subject_sha256": hashlib.sha256(owner.encode()).hexdigest(),
             "verification": "Cognito GetUser with the normal cached/refreshed access token; no JWT inspection"})
        with httpx.Client(base_url=receipt["services"]["ai_url"], headers={"Authorization": "Bearer " + tokens["id_token"]},
                          timeout=httpx.Timeout(30, read=120), trust_env=False, follow_redirects=False) as http:
            result = clear(http, owner, args.output)
        print(json.dumps(result, indent=2))
    except (ValueError, OSError, KeyError, TypeError, httpx.HTTPError) as error:
        parser.exit(1, f"Local cleanup stopped: {error}\n")


if __name__ == "__main__":
    main()
