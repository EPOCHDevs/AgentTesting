"""Check the layout replay against captured native Arrow and schema receipts (read-only)."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from pyarrow import ipc


def rows(path: Path, *, stream: bool = False):
    return (
        (ipc.open_stream(path) if stream else ipc.open_file(path))
        .read_all()
        .to_pylist()
    )


def same(actual, expected):
    if expected is None:
        assert actual is None, (actual, expected)
    elif isinstance(expected, float):
        assert actual is not None and math.isclose(
            actual, expected, rel_tol=1e-12, abs_tol=1e-12
        ), (actual, expected)
    else:
        assert actual == expected, (actual, expected)


def verify(root: Path):
    original = {}
    equality = {}
    for name in ("history", "latest", "coverage", "weak", "scorecard", "score_dates"):
        before = rows(root / f"source-{name}.arrow", stream=True)
        after = rows(root / f"final-{name}.arrow", stream=True)
        prefix = f"analysis_{name}_"
        fields = [key for key in before[0] if key.startswith(prefix)]
        assert len(before) == len(after)
        for left, right in zip(before, after, strict=True):
            for field in fields:
                same(right[field], left[field])
        original[name] = [
            {key[len(prefix) :]: row[key] for key in fields} for row in before
        ]
        equality[name] = {
            "rows": len(before),
            "fields": len(fields),
            "cells": len(before) * len(fields),
        }

    dashboard = json.loads((root / "final-dashboard.json").read_text())
    chart_fields = {
        "sales-growth": "revenue_growth",
        "operating-margin": "operating_margin",
        "fcf-margin": "fcf_margin",
    }
    chart_checks = []
    for i, chart in enumerate(dashboard["charts"]):
        data = rows(root / f"final-charts-{i}.arrow")
        for series in chart["chart_def"]["series"]:
            expected = [
                row for row in original["history"] if row["company"] == series["name"]
            ]
            mask = series["row_presence_column"]
            present = [row for row in data if row[mask]]
            assert len(present) == len(expected) == 12
            # Each company has a real first row even when growth is missing.
            for row, source in zip(present, expected, strict=True):
                same(row["index"], source["period_end"].timestamp() * 1000)
                value = source[chart_fields[chart["id"]]]
                same(row[series["id"]], None if value is None else value * 100)
            assert all(row[series["id"]] is None for row in data if not row[mask])
            chart_checks.append(
                {
                    "chart": chart["id"],
                    "company": series["name"],
                    "observations": len(present),
                    "real_missing": sum(row[series["id"]] is None for row in present),
                    "union_padding": len(data) - len(present),
                }
            )

    fields_by_label = {
        "Revenue (m)": ("revenue_m", False),
        "Growth": ("revenue_growth", True),
        "Growth change (pp)": ("deceleration_pp", False),
        "Operating income (m)": ("operating_income_m", False),
        "Income change (m)": ("operating_income_change_m", False),
        "Income growth": ("operating_income_growth", True),
        "Operating margin": ("operating_margin", True),
        "Gross margin": ("gross_margin", True),
        "Margin change (pp)": ("margin_change_pp", False),
        "Operating cash flow (m)": ("cfo_m", False),
        "Capital spending (m)": ("capex_spend_m", False),
        "Free cash flow (m)": ("fcf_m", False),
        "Free cash flow margin": ("fcf_margin", True),
        "CFO / net income": ("cfo_net_income", False),
        "Cash contribution (m)": ("working_capital_cash_m", False),
        "CFO less contribution (m)": ("cfo_before_wc_m", False),
        "Inventory (m)": ("inventory_m", False),
        "Net receivables (m)": ("receivables_m", False),
        "Payables (m)": ("payables_m", False),
        "Inventory / sales": ("inventory_sales", True),
        "Receivables / sales": ("receivables_sales", True),
        "Payables / sales": ("payables_sales", True),
        "Reported debt (m)": ("reported_debt_m", False),
        "Cash (m)": ("cash_m", False),
        "Net debt (m)": ("net_debt_m", False),
        "Current ratio": ("current_ratio", False),
        "Net debt / operating income": ("net_debt_operating_income", False),
        "Debt / operating cash flow": ("debt_cfo", False),
    }
    grouped = []
    for i, table in enumerate(dashboard["tables"]):
        if not table.get("column_coordinate_domain"):
            continue
        data = rows(root / f"final-tables-{i}.arrow")
        source_rows = original["weak" if table["id"] == "weak_sales" else "history"]
        lookup = {(row["company"], row["period_end"]): row for row in source_rows}
        binding = table["row_coordinate_binding"]
        assert len(data) == len(lookup)
        checked = 0
        for row in data:
            source = lookup[tuple(row[field] for field in binding["level_fields"])]
            for column in table["columns"]:
                if column["id"] == "key":
                    continue
                label = column["name"]
                field, percent = fields_by_label[label]
                assert column["type"] == ("PercentValue" if percent else "DecimalValue")
                value = source[field]
                same(
                    row[column["id"]],
                    None if value is None else value * (100 if percent else 1),
                )
                checked += 1
        grouped.append(
            {"table": table["id"], "rows": len(data), "checked_cells": checked}
        )
    assert len(grouped) == 5

    score_index, _score = next(
        (i, t) for i, t in enumerate(dashboard["tables"]) if t["id"] == "scorecard"
    )
    scores = rows(root / f"final-tables-{score_index}.arrow")
    assert len(scores) == 21
    for row, source in zip(scores, original["scorecard"], strict=True):
        same(row["c0"], source["unit"])
        same(row["c1"], source["fastenal"])
        same(row["c2"], source["msc_industrial"])

    period_index, period_table = next(
        (i, t)
        for i, t in enumerate(dashboard["tables"])
        if t["id"] == "coverage-periods"
    )
    periods = rows(root / f"final-tables-{period_index}.arrow")
    company_field, date_field = period_table["row_coordinate_binding"]["level_fields"]
    for source in original["coverage"]:
        company_rows = [
            row for row in periods if row[company_field] == source["company"]
        ]
        for column, field in [
            ("c0", "period_ends"),
            ("c1", "cash_period_ends"),
            ("c2", "balance_period_ends"),
        ]:
            dates = {
                row[date_field].strftime("%Y-%m-%d")
                for row in company_rows
                if row[column]
            }
            assert dates == set(source[field].split(", "))

    old_dashboard = json.loads((root / "source-dashboard.json").read_text())
    old_notes = {
        note["id"]: note["markdown"] for note in old_dashboard["content"]["markdown"]
    }
    new_notes = {
        note["id"]: note["markdown"] for note in dashboard["content"]["markdown"]
    }
    for note_id in ["cash-method", "sales-method", "operating-model"]:
        assert old_notes[note_id] == new_notes[note_id]
    original_source = json.loads((root / "source-study.json").read_text())["study"][
        "source"
    ]
    new_source = json.loads((root / "final-study.json").read_text())["study"]["source"]
    tail = 'markdown(title="Operating integration'
    assert (
        original_source[original_source.index(tail) :].strip()
        == new_source[new_source.index(tail) :].strip()
    )
    record = json.loads((root / "final-execution-record.json").read_text())
    assert (
        record["execution_status"] == "Completed"
        and record["response_phase"] == "outputs_recorded"
    )
    outputs = record["extension_outputs"]
    assert len(outputs["provenance"]["inputs"]) == 6
    assert len(outputs["provenance"]["commentary"]) == 2
    return {
        "passed": True,
        "retained_relations": equality,
        "charts": chart_checks,
        "grouped_tables": grouped,
        "scorecard_cells": 42,
        "coverage_period_rows": len(periods),
        "static_notes_unchanged": 3,
        "llm_instructions_and_facts_unchanged": True,
        "source_execution_id": outputs["provenance"]["source_execution_id"],
        "execution_id": record["execution_id"],
        "native_input_snapshot_sha256": outputs["provenance"]["input_snapshot_sha256"],
        "source_sha256": hashlib.sha256(new_source.encode()).hexdigest(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("receipts", type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.receipts), indent=2))


if __name__ == "__main__":
    main()
