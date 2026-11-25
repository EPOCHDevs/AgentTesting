"""Independent exact-rational oracle for RE04's one-predictor OLS fixture.

This module performs no service calls. It is deliberately independent of numpy,
native model code, generated SDKs and the agent's reported coefficients.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class Row:
    coordinate: str
    feature: Fraction | None
    target: Fraction | None
    partition: str


def number(value: str | None) -> Fraction | None:
    if value is None or not value.strip():
        return None
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise ValueError(f"Not a decimal value: {value!r}") from error
    return Fraction(parsed) if parsed.is_finite() else None


def read_rows(path: Path) -> list[Row]:
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != ["coordinate", "feature", "target", "partition"]:
            raise ValueError("The fixture columns/order do not match RE04's declared recipe")
        rows = []
        for raw in reader:
            if raw["partition"] not in {"train", "holdout", "continuation"}:
                raise ValueError("Unknown fixture partition")
            rows.append(Row(raw["coordinate"], number(raw["feature"]),
                            number(raw["target"]), raw["partition"]))
        return rows


@dataclass(frozen=True)
class Fit:
    intercept: Fraction
    slope: Fraction
    baseline: Fraction
    training_count: int
    selected_rows_sha256: str

    def predict(self, feature: Fraction | None) -> Fraction | None:
        return None if feature is None else self.intercept + self.slope * feature


def fit(rows: Iterable[Row]) -> Fit:
    # Filtering is joint, including for the baseline. Do not use all finite Y
    # values if some rows had unusable predictors.
    selected = [(ordinal, row) for ordinal, row in enumerate(rows)
                if row.partition == "train" and row.feature is not None and row.target is not None]
    count = len(selected)
    if count < 2:
        raise ValueError("OLS requires at least two joint finite training rows")
    xs = [row.feature for _, row in selected]
    ys = [row.target for _, row in selected]
    sx, sy = sum(xs, Fraction()), sum(ys, Fraction())
    denominator = count * sum((x * x for x in xs), Fraction()) - sx * sx
    if not denominator:
        raise ValueError("OLS predictor is rank deficient")
    slope = (count * sum((x * y for x, y in zip(xs, ys)), Fraction()) - sx * sy) / denominator
    encoded = json.dumps([[ordinal, row.coordinate] for ordinal, row in selected],
                         ensure_ascii=False, separators=(",", ":")).encode()
    return Fit((sy - slope * sx) / count, slope, sy / count, count,
               hashlib.sha256(encoded).hexdigest())


def values(model: Fit, rows: Iterable[Row], partition: str) -> dict:
    selected = [row for row in rows if row.partition == partition]
    predictions = [model.predict(row.feature) for row in selected]
    paired = [(prediction, row.target) for prediction, row in zip(predictions, selected)
              if prediction is not None and row.target is not None]
    def mse(baseline: bool) -> Fraction | None:
        return (sum(((model.baseline if baseline else prediction) - actual) ** 2
                    for prediction, actual in paired) / len(paired)) if paired else None
    def wire(value: Fraction | None) -> float | None:
        return float(value) if value is not None else None
    return {"rows": [{"coordinate": row.coordinate, "prediction": wire(prediction),
                      "baseline": wire(model.baseline), "actual": wire(row.target)}
                     for row, prediction in zip(selected, predictions)],
            "scored_count": len(paired), "model_mse": wire(mse(False)), "baseline_mse": wire(mse(True))}


def expected(path: Path) -> dict:
    rows = read_rows(path)
    model = fit(rows)
    return {"schema_version": 1, "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "method": "one-predictor OLS with intercept; joint finite training pairs",
            "forecast_semantics": "fixed coefficients on supplied observed predictors; no recursive rollout",
            "intercept": float(model.intercept), "slope": float(model.slope),
            "baseline": float(model.baseline), "training_count": model.training_count,
            "selected_rows_sha256": model.selected_rows_sha256,
            "holdout": values(model, rows, "holdout"),
            "continuation": values(model, rows, "continuation")}


def assert_close(actual: float | None, reference: float | None, *, atol: float = 1e-12, rtol: float = 1e-10) -> None:
    if reference is None:
        if actual is not None:
            raise AssertionError(f"Expected null, got {actual!r}")
    elif actual is None or not math.isfinite(actual) or not math.isclose(actual, reference, abs_tol=atol, rel_tol=rtol):
        raise AssertionError(f"Expected {reference!r}, got {actual!r}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", type=Path)
    args = parser.parse_args()
    print(json.dumps(expected(args.fixture), indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
