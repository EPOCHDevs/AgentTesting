"""Build the explicitly synthetic, source-free native RE04 fixture study.

No service calls or model runs. This is an owned integration fixture, not agent
instructions for a real market study. Native compiler/runtime qualification is
separate; it must pass before a live driver submits the generated source.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Literal

from .oracle import number

Stage = Literal["fit", "continue", "saved"]


def fixture_source(path: Path, *, stage: Stage, model_ref: str = "owned_fixture",
                   producer_execution: str | None = None, model_manifest_sha256: str | None = None) -> str:
    if stage not in ("fit", "continue", "saved"):
        raise ValueError("Unknown fixture stage")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,254}", model_ref):
        raise ValueError("Use the exact native local-fit or saved-model reference")
    if (stage == "continue") != (producer_execution is not None):
        raise ValueError("Only an execution-fit continuation requires its original producer")
    if (stage == 'saved') != (model_manifest_sha256 is not None):
        raise ValueError('Saved-resource consumption requires its exact Platform manifest pin')
    if model_manifest_sha256 is not None and not re.fullmatch(r'[a-f0-9]{64}', model_manifest_sha256):
        raise ValueError('Use the exact immutable saved manifest SHA-256')
    if producer_execution is not None and (not producer_execution or len(producer_execution) > 256
                                           or any(ord(char) < 32 for char in producer_execution)):
        raise ValueError("The original producer execution is invalid")
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != ["coordinate", "feature", "target", "partition"]:
            raise ValueError("The fixture columns/order differ from the declared recipe")
        rows = list(reader)
    if not rows or len(rows) > 1000:
        raise ValueError("Use a bounded owned fixture of 1..1000 rows")
    partitions: dict[str, list[date]] = {key: [] for key in ("train", "holdout", "continuation")}
    coordinates = []
    literals = []
    for ordinal, row in enumerate(rows):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T00:00:00Z", row["coordinate"]):
            raise ValueError("The first owned fixture requires canonical UTC midnight coordinates")
        coordinate = date.fromisoformat(row["coordinate"][:10])
        if row["partition"] not in partitions:
            raise ValueError("Unknown fixture partition")
        partitions[row["partition"]].append(coordinate)
        coordinates.append(coordinate)
        def sql_number(raw: str) -> str:
            return "NULL::DOUBLE" if number(raw) is None else f"CAST('{Decimal(raw)}' AS DOUBLE)"
        literals.append(f"({ordinal}, TIMESTAMPTZ '{coordinate.isoformat()}T00:00:00Z', "
                        f"{sql_number(row['feature'])}, {sql_number(row['target'])})")
    if len(set(coordinates)) != len(coordinates) or coordinates != sorted(coordinates):
        raise ValueError("This first fixture requires unique ascending dates; duplicate-row qualification is separate")
    if any(not values for values in partitions.values()):
        raise ValueError("All three declared fixture partitions are required")
    train, holdout, continuation = (partitions[key] for key in ("train", "holdout", "continuation"))
    if not max(train) < min(holdout) or not max(holdout) < min(continuation):
        raise ValueError("Training, holdout and continuation dates must be disjoint and chronological")
    query = ("SELECT row_id, coordinate, feature, target FROM (VALUES " + ", ".join(literals)
             + ") AS fixture(row_id, coordinate, feature, target) ORDER BY coordinate, row_id")
    source = '''# Explicitly synthetic owned integration fixture; no vendor observations.
raw = sql_program(
    queries={"rows": %s},
    outputs={"rows": relation_output(columns=[
        relation_field("row_id", Int64), relation_field("coordinate", TimestampNsUtc),
        relation_field("feature", Float64), relation_field("target", Float64)],
        row_key=("row_id",), order_by=("coordinate", "row_id"))})()
x = series(raw.rows.coordinate, raw.rows.feature)
y = series(raw.rows.coordinate, raw.rows.target)
''' % json.dumps(query)
    if stage == "fit":
        source += f'''train_x = clip_window(window=SourceWindow(start="{min(train)}", end="{max(train)}"))(x)
train_y = clip_window(window=SourceWindow(start="{min(train)}", end="{max(train)}"))(y)
fit = ols_fit(model_ref={json.dumps(model_ref)})(train_x, target=train_y, training_source_coordinate=index_of(train_y))
'''
    scored_dates = holdout if stage == "fit" else continuation
    source += f'''score_x = clip_window(window=SourceWindow(start="{min(scored_dates)}", end="{max(scored_dates)}"))(x)
score_y = clip_window(window=SourceWindow(start="{min(scored_dates)}", end="{max(scored_dates)}"))(y)
'''
    origin = f", fit_execution_id={json.dumps(producer_execution)}" if producer_execution else ""
    if model_manifest_sha256 is not None:
        origin = f', model_manifest_sha256={json.dumps(model_manifest_sha256)}'
    source += f"scored = ols_infer(model_ref={json.dumps(model_ref)}{origin})(score_x)\n"
    source += '''scored_rows = relation(columns=("coordinate", "prediction", "baseline", "actual"))(
    index_of(scored.prediction), scored.prediction, scored.baseline, score_y)
answer = sql_program(
    queries={
        "predictions": "SELECT coordinate, prediction, baseline, actual FROM scored_rows ORDER BY coordinate",
        "scores": "SELECT count(*) AS scored_count, avg(power(prediction - actual, 2)) AS model_mse, avg(power(baseline - actual, 2)) AS baseline_mse FROM scored_rows WHERE isfinite(prediction) AND isfinite(actual)"},
    outputs={
        "predictions": relation_output(columns=[relation_field("coordinate", TimestampNsUtc),
            relation_field("prediction", Float64), relation_field("baseline", Float64),
            relation_field("actual", Float64)], row_key=("coordinate",), order_by=("coordinate",)),
        "scores": relation_output(columns=[relation_field("scored_count", Int64),
            relation_field("model_mse", Float64), relation_field("baseline_mse", Float64)],
            cardinality=Scalar)})(scored_rows)
response_table(name="predictions", source=answer.predictions)
response_table(name="scores", source=answer.scores)
'''
    return source


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", type=Path)
    parser.add_argument("--stage", choices=("fit", "continue", "saved"), required=True)
    parser.add_argument("--model-ref", default="owned_fixture")
    parser.add_argument("--producer-execution")
    parser.add_argument('--model-manifest-sha256')
    args = parser.parse_args()
    print(fixture_source(args.fixture, stage=args.stage, model_ref=args.model_ref,
                         producer_execution=args.producer_execution, model_manifest_sha256=args.model_manifest_sha256))


if __name__ == "__main__":
    main()
