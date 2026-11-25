"""Read-only audit of a disposable owned OLS artifact, never a recovery path.

This verifies native retained state against the exact-rational oracle. It does
not add an SDK/public parameter API or reconstruct missing fits from predictions.
Only the declared OLS v2 envelope/payload is accepted. No service calls occur.
"""
from __future__ import annotations

import hashlib
import json
import math
import struct
from dataclasses import dataclass
from pathlib import Path

from .oracle import expected, assert_close

ENVELOPE = b"EPOCH_MODEL_ARTIFACT_V1\n"
PAYLOAD = b"EPOCH_OLS_FIT_V1\n"
MAX_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class OlsState:
    digest: str
    owner: str
    producer_execution: str
    fit_intercept: bool
    training_count: int
    intercept: float
    slopes: tuple[float, ...]
    baseline: float


def decode_owned_ols(data: bytes, *, digest: str, owner: str, execution: str) -> OlsState:
    """Check the complete artifact pin before inspecting its allowlisted facts."""
    if not owner or not execution:
        raise ValueError("Explicit disposable fixture owner and producer are required")
    if len(data) > MAX_BYTES or "sha256:" + hashlib.sha256(data).hexdigest() != digest:
        raise ValueError("Artifact bytes differ from the selected digest or exceed the audit bound")
    offset = len(ENVELOPE)
    if not data.startswith(ENVELOPE) or len(data) < offset + 8:
        raise ValueError("Unsupported or truncated model envelope")
    size, = struct.unpack_from(">Q", data, offset)
    offset += 8
    if size == 0 or offset + size >= len(data):
        raise ValueError("Truncated model manifest or payload")
    # A private native manifest is opaque to the public SDK. This audit reads
    # only these source-owned facts; it never prints the private configuration.
    manifest = json.loads(data[offset:offset + size])
    if not isinstance(manifest, dict):
        raise ValueError("Invalid model manifest")
    solver = manifest.get("deterministic_solver")
    training = manifest.get("supervised_training")
    if (manifest.get("schema_version") != 2 or manifest.get("family") != "OLS"
            or manifest.get("implementation_id") != "epoch.eigen.ols"
            or manifest.get("implementation_schema") != 1
            or manifest.get("payload_format") != "epoch.eigen.ols.binary.v1"
            or not isinstance(solver, dict)
            or solver.get("solver_id") != "epoch.eigen.col_piv_householder_qr.v1"
            or not isinstance(training, dict)):
        raise ValueError("Unsupported native OLS contract")
    if manifest.get("owner_user_id") != owner or manifest.get("producer_execution_id") != execution:
        raise ValueError("Artifact owner or original producer differs from the selected fixture")
    payload = data[offset + size:]
    start = len(PAYLOAD)
    if not payload.startswith(PAYLOAD) or len(payload) < start + 40:
        raise ValueError("Unsupported or truncated OLS payload")
    intercept_flag, width, count, intercept, baseline = struct.unpack_from(">QQQdd", payload, start)
    if (intercept_flag not in (0, 1) or not 1 <= width <= 256
            or count < width + intercept_flag or len(payload) != start + 40 + width * 8):
        raise ValueError("OLS payload dimensions/count/trailing bytes are invalid")
    slopes = struct.unpack_from(">" + "d" * width, payload, start + 40)
    if (not all(math.isfinite(value) for value in (intercept, baseline, *slopes))
            or (not intercept_flag and intercept != 0)
            or training.get("selected_row_count") != count
            or isinstance(training.get("selected_row_count"), bool)):
        raise ValueError("OLS coefficients or retained training count are invalid")
    return OlsState(digest, owner, execution, bool(intercept_flag), count,
                    intercept, slopes, baseline)


def read_owned_ols(path: Path, *, digest: str, owner: str, execution: str) -> OlsState:
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("Artifact exceeds the bounded owned-fixture audit")
    with path.open("rb") as stream:
        data = stream.read(MAX_BYTES + 1)
    return decode_owned_ols(data, digest=digest, owner=owner, execution=execution)


def assert_fixture_state(state: OlsState, fixture: Path) -> None:
    reference = expected(fixture)
    if not state.fit_intercept or len(state.slopes) != 1 or state.training_count != reference["training_count"]:
        raise AssertionError("Native state differs from the declared one-predictor training recipe")
    assert_close(state.intercept, reference["intercept"])
    assert_close(state.slopes[0], reference["slope"])
    assert_close(state.baseline, reference["baseline"])
