"""Append-only versions of the conversation collector's mutable evidence views."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from tools.evidence_redaction import redact_run, redact_trace_payload


def snapshot_collection(folder: Path) -> Path:
    """Freeze managed source views, not native binaries or arbitrary private files.

    Caller holds the attempt writer lock. Content-addressed objects are never
    overwritten; a corrupt existing object stops collection before refresh.
    """
    root = folder / "collection-snapshots"
    objects = root / "objects"
    objects.mkdir(parents=True, exist_ok=True)
    paths = [
        folder / name
        for name in (
            "attempt.json", "attachment.json", "thread.json", "status.json",
            "runs.json", "conversation.md", "sources.json", "metrics.json",
            "report.md", "traces/langsmith.json",
        )
    ]
    for pattern in ("turns/*/*.json", "run-captures/*.json", "streams/*/*"):
        paths.extend(folder.glob(pattern))
    files = {}
    for path in sorted(set(paths)):
        if not path.is_file() or path.is_symlink():
            continue
        data = path.read_bytes()
        # Old collector views can predate auth-envelope redaction. Never retain
        # those credentials merely to preserve byte identity; disclose the
        # sanitized projection in the manifest instead.
        projection = "original_bytes"
        if path.suffix == ".json":
            value = json.loads(data)
            sanitized = value
            if path.name == "run.json":
                sanitized = redact_run(value)
            elif isinstance(value, dict) and isinstance(value.get("runs"), list):
                redact = redact_trace_payload if path == folder / "traces/langsmith.json" else redact_run
                sanitized = {**value, "runs": [redact(row) for row in value["runs"]]}
            if sanitized != value:
                data = (json.dumps(sanitized, indent=2, default=str) + "\n").encode()
                projection = "canonical_auth_redacted"
        digest = hashlib.sha256(data).hexdigest()
        target = objects / digest
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise ValueError(f"Corrupt retained collection object: {digest}")
        else:
            with target.open("xb") as stream:
                stream.write(data)
        files[str(path.relative_to(folder))] = {
            "sha256": digest,
            "object": str(target.relative_to(folder)),
            "projection": projection,
        }
    manifest = root / f"{uuid4()}.json"
    with manifest.open("x") as stream:
        json.dump({
            "captured_at": datetime.now(UTC).isoformat(),
            "scope": "Versioned collector views; not complete native, billing or network evidence.",
            "files": files,
        }, stream, indent=2)
        stream.write("\n")
    return manifest
