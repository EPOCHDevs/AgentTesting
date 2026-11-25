"""Audit complete numerical answers from the owned native engineering harness.

This proves only the explicitly retained native graph/file-store boundary.
Public SDK, conversational, browser and measured-effect gates are independent.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from .native_artifact import assert_fixture_state, read_owned_ols
from .oracle import assert_close, expected


def assert_tables(tables: dict, reference: dict) -> None:
    """Require the entire ordered output and the matching paired-sample scores."""
    if set(tables) != {"predictions", "scores"}:
        raise AssertionError("The complete declared answer tables are required")
    predictions = tables["predictions"]
    if predictions["columns"] != ["coordinate", "prediction", "baseline", "actual"]:
        raise AssertionError("The native prediction schema/order changed")
    if len(predictions["rows"]) != len(reference["rows"]):
        raise AssertionError("The complete native prediction rows are required")
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    for actual, row in zip(predictions["rows"], reference["rows"]):
        delta = datetime.fromisoformat(row["coordinate"].replace("Z", "+00:00")) - epoch
        coordinate = (delta.days * 86400 + delta.seconds) * 1_000_000_000
        if len(actual) != 4 or type(actual[0]) is not int or actual[0] != coordinate:
            raise AssertionError("Native prediction coordinates/order differ from the fixture")
        for value, field in zip(actual[1:], ("prediction", "baseline", "actual")):
            assert_close(value, row[field])
    scores = tables["scores"]
    if (scores["columns"] != ["scored_count", "model_mse", "baseline_mse"]
            or len(scores["rows"]) != 1 or len(scores["rows"][0]) != 3):
        raise AssertionError("The single complete paired-score answer is required")
    count, model_mse, baseline_mse = scores["rows"][0]
    if type(count) is not int or count != reference["scored_count"]:
        raise AssertionError("The native scoring sample count differs from the oracle")
    assert_close(model_mse, reference["model_mse"])
    assert_close(baseline_mse, reference["baseline_mse"])


def audit(root: Path, fixture: Path) -> dict:
    receipt = json.loads((root / "native-receipt.json").read_text())
    state = read_owned_ols(root / "fit.artifact.bin", digest=receipt["digest"],
                          owner=receipt["owner"], execution=receipt["producer_execution"])
    assert_fixture_state(state, fixture)
    oracle = expected(fixture)
    for stage, partition, fits in (("fit", "holdout", 1),
                                    ("continue", "continuation", 0),
                                    ("saved", "continuation", 0)):
        run = json.loads((root / f"{stage}.results.json").read_text())
        if (run["stage"] != stage or run["compiled_fit_nodes"] != fits
                or run.get("measured_fit_started") != fits
                or run.get("measured_fit_completed") != fits):
            raise AssertionError("The native receipt describes a different fixture graph")
        assert_tables(run["tables"], oracle[partition])
    return {"boundary": "native graph/file store", "numerical_audit": "passed",
            "digest": state.digest, "fixture_sha256": oracle["source_sha256"],
            "training_count": state.training_count,
            "holdout_rows": len(oracle["holdout"]["rows"]),
            "continuation_rows": len(oracle["continuation"]["rows"]),
            "fit_effects": {"fit": 1, "continue": 0, "saved_consumer": 0},
            "pending": ["HTTP/SDK effects", "conversation", "Portal UI"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    parser.add_argument("fixture", type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.evidence, args.fixture), indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
