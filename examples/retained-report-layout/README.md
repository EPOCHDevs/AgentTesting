# Retained FAST/MSM report layout

ENG-2331 presents the saved industrial-resilience calculation with readable,
grouped tables and native sparse-line membership. This is a saved-input layout
rebuild, not another discovery run or a new vendor-data sample.

The builder selects the complete `analysis.history`, `latest`, `coverage`,
`weak`, `scorecard`, and `score_dates` relations from the native input catalogue.
It does not load vendors or redo financial calculations. It retains the original
three chart definitions, static notes, two `llm_call` fact selections and their
instructions. Prompt changes belong in a separate review.

## Layout

- Sales, profit and margin columns have separate grouped headers and short leaf
  labels. Weak-year eligibility sits in its own table.
- Cash generation, conversion and working-capital contributions share a grouped
  comparison. Working-capital balances and debt/liquidity have separate tables.
- The latest scorecard groups rows by profitability, cash generation, financial
  buffer and working capital. Its percent rows were already scaled by the source
  program and must remain decimal values with explicit units.
- Filing dates and currencies move to a dedicated section. Retained coverage
  date lists become typed per-period presence rows, keeping every declared date.
- ClientPortal supplies two-line ordinary-note previews with Show more and a
  full-note dialog. Structured briefings and executive summaries keep their own
  rendering contracts.

## Reproduce from owned native receipts

Read the original execution's as-run `/study` and `/inputs` catalogue. Keep the
receipts private; do not commit authentication, runtime environment or vendor
rows. Then generate a new source file (existing output files are never replaced):

```bash
python examples/retained-report-layout/build_report.py \
  --catalog /path/to/input-catalog.json \
  --study /path/to/source-study.json \
  --output /path/to/grouped-report.epochscript
```

Admit the source using the native agent definition endpoint with
`execution_input.source_execution_id` and the six exact catalogue table names.
Use `study_type: research` and `data.assets: []`. Run it through the integration
execution-request endpoint, with the same source execution and `input_tables`.
Supply a new operation handle and the original period. Do not use
`report_template`, which reruns the original study. Preserve failed admissions
and all prior execution identities.

After numerical completion, wait for `response_phase: outputs_recorded`. The
outer execution request may still say `phase: active`; that field alone is not
the text/output completion signal. Capture native schema, chart/table Arrow and
the six source/derived relations. `verify_report.py` checks these private
receipts, including exact missing values and fiscal dates:

```bash
python examples/retained-report-layout/verify_report.py /path/to/receipt-directory
```

The verifier requires PyArrow. Query replies use Arrow streams; dashboard data
uses Arrow files. Select a relation by an exact advertised field, not just a
prefix: `analysis_weak_` also prefixes the reshaped `analysis_weak_sales_` fields.

## Native acceptance, 31 October 2026

The sanitized `acceptance.json` records the exact saved-input run, source hash
and verification totals. All 1,703 original cells match, including nulls; all
648 grouped metric cells and 42 scorecard values match their source units.
Three charts preserve 12 real observations per company, filter union padding,
and keep the genuinely missing first growth observation. Coverage preserves
24 company/period rows. Three static notes and both original LLM instructions
and fact selections are unchanged.

Two real Terra 6.1 medium commentary calls completed successfully; subsequent
layout revisions reused their cached outputs. The original cached summary's
incorrect claim that both businesses grew in 2020 is not repeated in the new
output. An independent review checked both new texts against the retained
Arrow evidence. This is not acceptance of the broader ten-scenario campaign.

Backend source: `3c38231fcea666357938e9b7fd59e0f9242f3d44` (equivalent local
warm build tree `8279de3cce0b134a872912f6c77829f95caa6721`), official SDK
`3.3.0-rc.48`; unchanged AI source:
`6206e4d035a5fd2cbb6b3c33c75f601c3ebef751`.
