"""Re-present the FAST/MSM retained relations. No provider loaders or financial recalculation.

Inputs are the native /inputs catalogue and as-run /study receipt, kept privately.
The original instructions and facts passed to the two llm_calls remain unchanged.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def quoted(value: str) -> str:
    # EpochScript literals retain backslashes: JSON escaping is not SQL/string escaping.
    for delimiter in ('"', "'", '"""', "'''"):
        if delimiter not in value and (len(delimiter) == 3 or "\n" not in value):
            return delimiter + value + delimiter
    raise ValueError("The authored literal needs an explicit delimiter")


def build(catalog: dict, original: str) -> str:
    names = ("history", "scorecard", "latest", "score_dates", "coverage", "weak")
    schemas = {name: "annual_schema" for name in ("history", "latest", "weak")}
    schemas.update(
        scorecard="score_schema", score_dates="date_schema", coverage="coverage_schema"
    )
    order = {name: "symbol, period_end" for name in ("history", "latest", "weak")}
    order.update(scorecard="position", score_dates="position", coverage="company")
    bindings, relations = [], []
    for name in names:
        prefix = f"analysis_{name}_"
        anchor = prefix + (
            "position" if name in ("scorecard", "score_dates") else "company"
        )
        matches = [
            table
            for table in catalog["tables"]
            if any(f["name"] == anchor for f in table["fields"])
        ]
        if len(matches) != 1:
            raise ValueError(
                f"Expected one full saved relation for {name}, got {len(matches)}"
            )
        table = matches[0]
        fields = [
            field for field in table["fields"] if field["name"].startswith(prefix)
        ]
        bindings.append(
            f"frozen_{name} = execution_table(table={table['reference']},fields=({','.join(f['reference'] for f in fields)},))"
        )
        projection = ", ".join(
            f"{f['name']} AS {f['name'][len(prefix) :]}" for f in fields
        )
        query = f"SELECT {projection} FROM frozen_{name} ORDER BY {order[name]}"
        relations.append(
            f"""{quoted(name + "_rows")}: {{"query":{quoted(query)},"output":{schemas[name]}}}"""
        )

    prelude = original[
        original.index("annual_text =") : original.index("analysis = sql_program")
    ]
    queries = [
        f"{quoted(name)}:{quoted(f'SELECT * FROM {name}_rows ORDER BY {order[name]}')}"
        for name in names
    ]
    outputs = [f"{quoted(name)}:{schemas[name]}" for name in names]
    reports = []
    # group, label, retained field, format, decimal places. Values remain numeric and nullable.
    layouts = [
        (
            "weak_sales",
            "weak",
            "Weak-growth years, weakest ties and 2020: sales and profits",
            1,
            3,
            [
                ("Sales", "Revenue (m)", "revenue_m", "DecimalFormat", 1),
                ("Sales", "Growth", "revenue_growth", "PercentFormat", 1),
                ("Sales", "Growth change (pp)", "deceleration_pp", "DecimalFormat", 2),
                (
                    "Profit",
                    "Operating income (m)",
                    "operating_income_m",
                    "DecimalFormat",
                    1,
                ),
                (
                    "Profit",
                    "Income change (m)",
                    "operating_income_change_m",
                    "DecimalFormat",
                    1,
                ),
                (
                    "Profit",
                    "Income growth",
                    "operating_income_growth",
                    "PercentFormat",
                    1,
                ),
                ("Margins", "Operating margin", "operating_margin", "PercentFormat", 1),
                (
                    "Margins",
                    "Margin change (pp)",
                    "margin_change_pp",
                    "DecimalFormat",
                    2,
                ),
            ],
        ),
        (
            "annual_sales",
            "history",
            "Annual sales and profitability",
            1,
            4,
            [
                ("Sales", "Revenue (m)", "revenue_m", "DecimalFormat", 1),
                ("Sales", "Growth", "revenue_growth", "PercentFormat", 1),
                (
                    "Profit",
                    "Operating income (m)",
                    "operating_income_m",
                    "DecimalFormat",
                    1,
                ),
                ("Margins", "Gross margin", "gross_margin", "PercentFormat", 1),
                ("Margins", "Operating margin", "operating_margin", "PercentFormat", 1),
                (
                    "Margins",
                    "Margin change (pp)",
                    "margin_change_pp",
                    "DecimalFormat",
                    2,
                ),
            ],
        ),
        (
            "cash_generation",
            "history",
            "Cash generation and conversion",
            2,
            2,
            [
                (
                    "Cash generation",
                    "Operating cash flow (m)",
                    "cfo_m",
                    "DecimalFormat",
                    1,
                ),
                (
                    "Cash generation",
                    "Capital spending (m)",
                    "capex_spend_m",
                    "DecimalFormat",
                    1,
                ),
                ("Cash generation", "Free cash flow (m)", "fcf_m", "DecimalFormat", 1),
                (
                    "Conversion",
                    "Free cash flow margin",
                    "fcf_margin",
                    "PercentFormat",
                    1,
                ),
                (
                    "Conversion",
                    "CFO / net income",
                    "cfo_net_income",
                    "DecimalFormat",
                    2,
                ),
                (
                    "Working capital",
                    "Cash contribution (m)",
                    "working_capital_cash_m",
                    "DecimalFormat",
                    1,
                ),
                (
                    "Working capital",
                    "CFO less contribution (m)",
                    "cfo_before_wc_m",
                    "DecimalFormat",
                    1,
                ),
            ],
        ),
        (
            "working_capital",
            "history",
            "Working-capital balances and sales ratios",
            2,
            3,
            [
                ("Balances", "Inventory (m)", "inventory_m", "DecimalFormat", 1),
                (
                    "Balances",
                    "Net receivables (m)",
                    "receivables_m",
                    "DecimalFormat",
                    1,
                ),
                ("Balances", "Payables (m)", "payables_m", "DecimalFormat", 1),
                (
                    "Share of annual sales",
                    "Inventory / sales",
                    "inventory_sales",
                    "PercentFormat",
                    1,
                ),
                (
                    "Share of annual sales",
                    "Receivables / sales",
                    "receivables_sales",
                    "PercentFormat",
                    1,
                ),
                (
                    "Share of annual sales",
                    "Payables / sales",
                    "payables_sales",
                    "PercentFormat",
                    1,
                ),
            ],
        ),
        (
            "financial_buffer",
            "history",
            "Debt, liquidity and financial buffer",
            2,
            4,
            [
                (
                    "Balances",
                    "Reported debt (m)",
                    "reported_debt_m",
                    "DecimalFormat",
                    1,
                ),
                ("Balances", "Cash (m)", "cash_m", "DecimalFormat", 1),
                ("Balances", "Net debt (m)", "net_debt_m", "DecimalFormat", 1),
                ("Coverage", "Current ratio", "current_ratio", "DecimalFormat", 2),
                (
                    "Coverage",
                    "Net debt / operating income",
                    "net_debt_operating_income",
                    "DecimalFormat",
                    2,
                ),
                (
                    "Coverage",
                    "Debt / operating cash flow",
                    "debt_cfo",
                    "DecimalFormat",
                    2,
                ),
            ],
        ),
    ]
    categories = {
        1: "1. Sales and operating-margin resilience",
        2: "2. Cash generation and financial flexibility",
        3: "3. Latest annual side-by-side scorecard",
        4: "4. Filing dates and coverage",
    }
    for view, source, title, category, position, metrics in layouts:
        groups = list(dict.fromkeys(m[0] for m in metrics))
        cells, styles = [], []
        for i, (group, label, field, fmt, dp) in enumerate(metrics):
            sql_group = group.replace("'", "''")
            sql_label = label.replace("'", "''")
            cells.append(
                f"('{sql_group}', '{sql_label}', {groups.index(group) + 1}::BIGINT, {i + 1}::BIGINT, {field})"
            )
            native_name = f'Group="{group}" · Metric="{label}"'
            styles.append(
                f"{{name:{quoted(native_name)},title:{quoted(label)},type:{fmt},dp:{dp}}}"
            )
        query = (
            "SELECT company, period_end, m.* FROM "
            + source
            + "_rows CROSS JOIN LATERAL (VALUES "
            + ", ".join(cells)
            + ') m("group",metric,group_order,metric_order,value) '
            + 'ORDER BY company, period_end, group_order, metric_order, m."group", metric'
        )
        queries.append(f"{quoted(view)}:{quoted(query)}")
        outputs.append(f"{quoted(view)}:cell_schema")
        reports.append(
            f"table(title={quoted(title)},category={quoted(categories[category])},order={position},"
            f"exhibit_id={quoted(view)},value_layout=Cells,table_schema={{columns:[{','.join(styles)}]}})"
            f"(key=(analysis.{view}.company,analysis.{view}.period_end),by=(analysis.{view}.group,analysis.{view}.metric),"
            f"by_sort=(analysis.{view}.group_order,analysis.{view}.metric_order),values=(analysis.{view}.value,))"
        )

    cell_schema = """cell_schema = relation_output(columns=[relation_field("company",Utf8),relation_field("period_end",TimestampNsUtc),relation_field("group",Utf8),relation_field("metric",Utf8),relation_field("group_order",Int64),relation_field("metric_order",Int64),relation_field("value",Float64)],row_key=("company","period_end","group","metric"),order_by=("company","period_end","group_order","metric_order","group","metric"))"""
    # Group the existing scorecard rows. Those percentages are already scaled, so keep Decimal.
    score_query = "SELECT CASE WHEN position<=6 THEN 'Profitability' WHEN position<=12 THEN 'Cash generation' WHEN position<=18 THEN 'Financial buffer' ELSE 'Working capital' END AS section, CASE WHEN position<=6 THEN 1 WHEN position<=12 THEN 2 WHEN position<=18 THEN 3 ELSE 4 END::BIGINT AS section_order, * FROM scorecard_rows ORDER BY position"
    queries.append(f'"grouped_scorecard":{quoted(score_query)}')
    outputs.append(
        '"grouped_scorecard":relation_output(columns=[relation_field("section",Utf8),relation_field("section_order",Int64),relation_field("position",Int64),relation_field("metric",Utf8),relation_field("unit",Utf8),relation_field("fastenal",Float64),relation_field("msc_industrial",Float64)],row_key=("position",),order_by=("position",))'
    )
    period_query = """WITH declared AS (
        SELECT company, 'Income' AS statement, UNNEST(STRING_SPLIT(period_ends, ', ')) AS fiscal_period FROM coverage_rows
        UNION ALL SELECT company, 'Cash flow', UNNEST(STRING_SPLIT(cash_period_ends, ', ')) FROM coverage_rows
        UNION ALL SELECT company, 'Balance sheet', UNNEST(STRING_SPLIT(balance_period_ends, ', ')) FROM coverage_rows
    ) SELECT company, strptime(fiscal_period, '%Y-%m-%d') AT TIME ZONE 'UTC' AS period_end,
      BOOL_OR(statement='Income') AS income, BOOL_OR(statement='Cash flow') AS cash_flow,
      BOOL_OR(statement='Balance sheet') AS balance_sheet
    FROM declared GROUP BY company, fiscal_period ORDER BY company, period_end"""
    queries.append(f'"coverage_periods":{quoted(period_query)}')
    outputs.append(
        '"coverage_periods":relation_output(columns=[relation_field("company",Utf8),relation_field("period_end",TimestampNsUtc),relation_field("income",Boolean),relation_field("cash_flow",Boolean),relation_field("balance_sheet",Boolean)],row_key=("company","period_end"),order_by=("company","period_end"))'
    )
    reports.append(
        'table(title="Latest annual resilience scorecard",category="3. Latest annual side-by-side scorecard",order=2,exhibit_id="scorecard",value_layout=Columns,table_schema={columns:[{name:"unit",title:"Unit and definition"},{name:"fastenal",title:"Fastenal",dp:2},{name:"msc_industrial",title:"MSC Industrial",dp:2}]})(key=(analysis.grouped_scorecard.section,analysis.grouped_scorecard.metric),key_sort=(analysis.grouped_scorecard.section_order,analysis.grouped_scorecard.position),values=(analysis.grouped_scorecard.unit,analysis.grouped_scorecard.fastenal,analysis.grouped_scorecard.msc_industrial))'
    )

    def columns(
        exhibit: str,
        title: str,
        source: str,
        category: int,
        position: int,
        fields: list[tuple[str, str, str | None]],
        key: tuple[str, ...],
    ):
        styles = [
            f"{{name:{quoted(field)},title:{quoted(label)}"
            + (f",type:{fmt}" if fmt else "")
            + "}"
            for field, label, fmt in fields
        ]
        reports.append(
            f"table(title={quoted(title)},category={quoted(categories[category])},order={position},exhibit_id={quoted(exhibit)},value_layout=Columns,table_schema={{columns:[{','.join(styles)}]}})(key=({','.join(f'analysis.{source}.{field}' for field in key)},),values=({','.join(f'analysis.{source}.{field}' for field, _, _ in fields)},))"
        )

    columns(
        "weak-basis",
        "Why these fiscal years are included",
        "weak",
        1,
        5,
        [
            ("fiscal_year", "Fiscal year", None),
            ("prior_period_end", "Previous period end", "DateFormat"),
            ("days_between_ends", "Days between ends", None),
            ("weak_growth", "Weak growth", None),
            ("weakest_growth", "Weakest growth tie", None),
            ("decelerating", "Decelerating", None),
        ],
        ("company", "period_end"),
    )
    columns(
        "filing-basis",
        "Filing dates and reported currencies",
        "history",
        4,
        1,
        [
            ("income_filed", "Income filed", "DateFormat"),
            ("currency", "Income currency", None),
            ("cash_filed", "Cash flow filed", "DateFormat"),
            ("cash_currency", "Cash currency", None),
            ("balance_filed", "Balance sheet filed", "DateFormat"),
            ("balance_currency", "Balance currency", None),
        ],
        ("company", "period_end"),
    )
    columns(
        "coverage-counts",
        "Observed annual coverage",
        "coverage",
        4,
        2,
        [
            ("income_years", "Income years", None),
            ("cash_years", "Cash years", None),
            ("balance_years", "Balance years", None),
            ("growth_pairs", "Growth pairs", None),
            ("weak_years", "Weak years", None),
            ("weakest_years", "Weakest ties", None),
            ("valid_fcf_years", "Free cash flow years", None),
        ],
        ("company",),
    )
    columns(
        "coverage-periods",
        "Retained fiscal periods by statement",
        "coverage_periods",
        4,
        3,
        [
            ("income", "Income statement", None),
            ("cash_flow", "Cash-flow statement", None),
            ("balance_sheet", "Balance sheet", None),
        ],
        ("company", "period_end"),
    )

    original_lines = original.splitlines()
    charts = [line for line in original_lines if line.startswith("line(")]
    score_dates = next(
        line
        for line in original_lines
        if line.startswith("table(") and 'exhibit_id="score-dates"' in line
    )
    company_styles = next(
        line for line in original_lines if line.startswith("company_styles =")
    )
    # Leave author-owned notes, facts and LLM instructions byte-for-byte unchanged.
    prose = original[original.index('markdown(title="Operating integration') :]
    result = (
        "\n\n".join(
            [
                "# ENG-2331: retained-result presentation; the source execution is supplied by the caller.",
                "\n".join(bindings),
                prelude,
                cell_schema,
                "analysis = sql_program(relations={"
                + ",\n".join(relations)
                + "},queries={"
                + ",\n".join(queries)
                + "},outputs={"
                + ",\n".join(outputs)
                + "})("
                + ",".join("frozen_" + name for name in names)
                + ")",
                company_styles,
                "\n".join(charts),
                "\n".join(reports),
                score_dates,
                prose,
            ]
        )
        + "\n"
    )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    original = json.loads(args.study.read_text())["study"]["source"]
    source = build(json.loads(args.catalog.read_text()), original)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        stream.write(source)
    print(
        json.dumps(
            {
                "path": str(args.output),
                "bytes": len(source.encode()),
                "sha256": hashlib.sha256(source.encode()).hexdigest(),
            }
        )
    )


if __name__ == "__main__":
    main()
