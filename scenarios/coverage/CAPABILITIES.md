# Capability evidence and remaining verification

[Programme](README.md) · [Cases](WORKFLOWS.md) ·
[Native source inventory](capability-inventory.json)

Reviewed on 27 October 2026 from the current local canonical repositories.
Source declarations, executable contract evidence, populated vendor data,
deployed availability and end-to-end acceptance are separate facts.

| Repository | Inspected branch and commit |
| --- | --- |
| EpochBackend | `main` · `afe645191293f3345ce6bd9d19dec338460abee6` |
| EpochAI | `dev` · `9c8342b7b399b3d28ee096a769018f2539ddbf81` |
| EpochClientPortal | `dev` · `0c56b9bc5f3ccb8a2c7d4753198abda72c96249f` |
| AgentTesting | `dev` base · `0c8732edc267f9e523e34b7433c47743f1f611fe` |

The revisions above define the evidence snapshot. The review used tracked
source files; local artifacts and running services were not acceptance evidence.
Source links pin the inspected revisions so the review remains reproducible.

## Source facts that affect scenario design

| Surface | Current evidence and acceptance consequence | Source |
| --- | --- | --- |
| Universe screening | Native full-cohort screening and frozen universe references; native ceiling is **20,000**. Attempt the real all-US population and verify actual admission. Do not inherit the retired orchestrator template's 4,000 limit or treat a search preview as the cohort. | [Ceiling](https://github.com/EPOCHDevs/EpochBackend/blob/afe645191293f3345ce6bd9d19dec338460abee6/packages/epoch-datasdk/include/epoch_data_sdk/model/asset/asset_screen.hpp), [screen](https://github.com/EPOCHDevs/EpochBackend/blob/afe645191293f3345ce6bd9d19dec338460abee6/packages/epoch-datasdk/src/model/asset/asset_screen.cpp), [active AI tool](https://github.com/EPOCHDevs/EpochAI/blob/9c8342b7b399b3d28ee096a769018f2539ddbf81/agent_src/agents/supervisor/data_tools.py) |
| Capacity | Current global permissions remove coarse EOD/intraday count/lookback caps; deterministic workload admission and vendor constraints remain. The screen ceiling does not promise every universe/window fits or meets latency expectations. Verify deployed account policy. | [Permissions](https://github.com/EPOCHDevs/EpochBackend/blob/afe645191293f3345ce6bd9d19dec338460abee6/services/stratifyx/src/auth/permissions/global_study_permissions.h) |
| Historical membership | Covered historical index union plus per-row membership gate; current snapshot is distinct. Test additions/removals. Index support does not establish complete historical all-US membership or issuer lineage. | [Compiler gate](https://github.com/EPOCHDevs/EpochBackend/blob/afe645191293f3345ce6bd9d19dec338460abee6/packages/epoch-script/src/transforms/compiler/passes/asset_screen_validator_pass.cpp), screen above |
| Capitalization | Native current-cap screening/refinement exists; historical daily cap and sparse statement-period cap are distinct. The retired template's claim that current-cap screening is unavailable is not authoritative. | [Source service](https://github.com/EPOCHDevs/EpochBackend/blob/afe645191293f3345ce6bd9d19dec338460abee6/services/stratifyx/src/controller/services/discovery_source_service.cpp), [screen service](https://github.com/EPOCHDevs/EpochBackend/blob/afe645191293f3345ce6bd9d19dec338460abee6/services/stratifyx/src/controller/services/asset_screening_service.cpp) |
| Market bars | Native descriptions cover US stocks/ETFs, futures, FX and crypto at supported daily/intraday resolutions. US/global financial statements are a different surface. Do not infer worldwide tradeable bars or universal history from the old product guide. | [Source metadata](https://github.com/EPOCHDevs/EpochBackend/blob/afe645191293f3345ce6bd9d19dec338460abee6/packages/epoch-script/src/transforms/packages/market_data/metadata/market_data_source.json) |
| Fundamentals | Statements, growth, ratios/metrics, EV, owner earnings, daily capitalization, employee history and revenue segments provide multiple distinct research paths. Test fiscal periods, currencies, shares, raw versus derived values and missing denominators. | [Statements](https://github.com/EPOCHDevs/EpochBackend/blob/afe645191293f3345ce6bd9d19dec338460abee6/packages/epoch-script/src/transforms/packages/financial_statements/skills/detailed.md), [metrics](https://github.com/EPOCHDevs/EpochBackend/blob/afe645191293f3345ce6bd9d19dec338460abee6/packages/epoch-script/src/transforms/packages/financial_metrics/skills/detailed.md) |
| Fundamental timing | Filing/acceptance dates do not certify exact intraday availability or immutable historical values; some dates have estimated/date-only fallbacks. A strict as-published strategy remains blocked without required evidence. Descriptive or qualified retrospective analysis is a different claim. | Statements and metrics above |
| Corporate events / ETFs | Dividends, splits, earnings, current market-wide calendars, IPO calendar/prospectus and current ETF information/holdings. Current schedules/holdings are not historical tapes; amendments are not unique IPOs. | [Actions](https://github.com/EPOCHDevs/EpochBackend/blob/afe645191293f3345ce6bd9d19dec338460abee6/packages/epoch-script/src/transforms/packages/corporate_actions/skills/detailed.md), metrics above |
| Macro | Macrobond latest data, vintage/revision tape and schedules; Trading Economics series/calendars, current economic/market forecasts, market observations and ratings. Test reference versus release/capture clocks and native units; no generic conflict chronology. | [Macro contract](https://github.com/EPOCHDevs/EpochBackend/blob/afe645191293f3345ce6bd9d19dec338460abee6/packages/epoch-script/src/transforms/packages/macroeconomics/skills/detailed.md) |
| Features / explanation | Native relational/SQL, temporal/cross-sectional operations, statistics/technicals, scaling, PCA, regression/factor analysis, cointegration, Kalman and CUSUM. Correct grids/keys and availability are part of feature engineering. | [Package inventory](capability-inventory.json) |
| Models | ARIMA/GARCH/HMM reusable immutable fit/consumer operations coexist with rolling forecasts and clustering/regime methods. Test fit-only, later consumer, exact horizons, provenance and genuine holdout. This does not establish arbitrary estimator support. | [Forecasts](https://github.com/EPOCHDevs/EpochBackend/blob/afe645191293f3345ce6bd9d19dec338460abee6/packages/epoch-script/src/transforms/packages/time_series_forecasting/skills/detailed.md), [regimes](https://github.com/EPOCHDevs/EpochBackend/blob/afe645191293f3345ce6bd9d19dec338460abee6/packages/epoch-script/src/transforms/packages/regime_detection/skills/detailed.md) |
| Hindsight | Causal HMM differs from retrospective smoothing; retrospective execution can warn while running. State labels are fit-local. Several market-structure annotations are retrospective. Successful execution cannot certify causality. | Regimes above; [SMC](https://github.com/EPOCHDevs/EpochBackend/blob/afe645191293f3345ce6bd9d19dec338460abee6/packages/epoch-script/src/transforms/packages/smc/skills/detailed.md) |
| Execution | Market/limit/stop/stop-limit, next-bar timing and constrained current-open/PriceCrossing contracts; exits, sizing and benchmarks. PriceCrossing models an explicit OHLC path with narrower allowed policies. | [Execution contract](https://github.com/EPOCHDevs/EpochBackend/blob/afe645191293f3345ce6bd9d19dec338460abee6/packages/epoch-script/src/transforms/packages/signal_execution/skills/detailed.md) |
| Accounting / costs | Commission/slippage, account-currency totals and corporate-action handling; no ordinary borrow fees, margin interest or idle-cash interest. Reconcile actual fills/accounts; no duplicate dividend return or assumed all-in financing. | Execution contract above |
| Allocation | Equal/inverse-volatility, risk parity/budgets, variance/diversification/Sharpe, downside/tail, hierarchical and Black–Litterman methods. Name method/option subcases and verify constraints plus executed holdings. | [Allocation contract](https://github.com/EPOCHDevs/EpochBackend/blob/afe645191293f3345ce6bd9d19dec338460abee6/packages/epoch-script/src/transforms/packages/portfolio_allocation/skills/detailed.md) |
| Intraday / futures | Sessions/clocks/gaps, profiles, historical minute aggregation, term structure, ordinary proxy rolls and explicit daily paired rolls. Test calendars, availability and bar-path uncertainty; explicit paired roll is not intraday rolling. | [Sessions](https://github.com/EPOCHDevs/EpochBackend/blob/afe645191293f3345ce6bd9d19dec338460abee6/packages/epoch-script/src/transforms/packages/intraday_sessions/skills/detailed.md), [futures](https://github.com/EPOCHDevs/EpochBackend/blob/afe645191293f3345ce6bd9d19dec338460abee6/packages/epoch-script/src/transforms/packages/futures_continuation/skills/detailed.md), execution above |
| Agent lifecycle | Active supervisor uses native search/screen, build, run, tune, Monte Carlo and cited qualitative web research. Saved numerical findings use completed executions; Monte Carlo has a parent backtest. Use active tools, not retired prompts. | [Mounted tools](https://github.com/EPOCHDevs/EpochAI/blob/9c8342b7b399b3d28ee096a769018f2539ddbf81/agent_src/agents/supervisor/agent.py), [workflow](https://github.com/EPOCHDevs/EpochAI/blob/9c8342b7b399b3d28ee096a769018f2539ddbf81/agent_src/agents/supervisor/templates/2_workflow.txt) |
| Owned artifacts | Backend Files/import/dataset APIs, portfolio snapshots, references, archives and context publication/edit/share APIs exist. Verify actual tool/UI exposure, ownership and revisions before claiming a complete user journey. | [Files](https://github.com/EPOCHDevs/EpochBackend/blob/afe645191293f3345ce6bd9d19dec338460abee6/services/stratifyx/src/controller/http/files.h), [snapshots](https://github.com/EPOCHDevs/EpochBackend/blob/afe645191293f3345ce6bd9d19dec338460abee6/services/stratifyx/src/controller/http/portfolio_snapshots.h), [contexts](https://github.com/EPOCHDevs/EpochBackend/blob/afe645191293f3345ce6bd9d19dec338460abee6/services/stratifyx/src/controller/http/contexts.h) |
| Visible product | Markets/context, economic calendar, data explorer, research/studies and execution-history surfaces need UI evidence as well as API results. | [Routes](https://github.com/EPOCHDevs/EpochClientPortal/blob/0c56b9bc5f3ccb8a2c7d4753198abda72c96249f/src/constants/routes.ts), [calendar](https://github.com/EPOCHDevs/EpochClientPortal/blob/0c56b9bc5f3ccb8a2c7d4753198abda72c96249f/src/modules/calendar/CalendarPage/index.tsx), [markets](https://github.com/EPOCHDevs/EpochClientPortal/blob/0c56b9bc5f3ccb8a2c7d4753198abda72c96249f/src/modules/markets/MarketsPage/index.tsx) |
| Monitoring | Current page explicitly reads stub data and the create action says unavailable. Treat it as a boundary test, not an operational workflow. | [Implementation](https://github.com/EPOCHDevs/EpochClientPortal/blob/0c56b9bc5f3ccb8a2c7d4753198abda72c96249f/src/modules/monitoring/MonitoringPage/index.tsx) |

## Existing exclusions and explicit boundaries

The owner-specified [exclusions](../EXCLUSIONS.md) remain options-dependent work,
bond-focused work and live-price/streaming workflows. Historical intraday is in
scope. Policy-rate or macro-yield context is not automatically bond-focused work.
Do not add an exclusion merely because a run fails.

Source limitations are acceptance facts, not new owner exclusions: financing
and borrow/funding schedules, exact observed intrabar order flow, unverified
model types, unsupported roll/policy compositions and operational monitoring
must not be inferred from related functions or UI placeholders.

“Macros” has two relevant meanings here. MA/IN cover economic themes; the native
inventory covers actual EpochScript macros. Every macro has a family and case
assignment. Helpers are tested through useful workflows; a persona for each
indicator adds little value.

## Remaining verification

1. Match the release candidate's actual native binary, AI/UI version, generated
   SDK/reference registry and vendor catalogue with the intended source.
2. Establish exact identities, history, entitlement and timing quality for each
   fixture, especially all-US historical eligibility, immutable fundamentals,
   international prices, macro vintages and historical intraday.
3. Reconcile the metadata inventory with the deployed public registry. Files
   include implementation helpers and presentation definitions; presence alone
   does not prove a name is exposed or supported end to end.
4. Verify user-facing paths for Files, model reuse, portfolio snapshots,
   cancellation and sharing; distinguish API and UI channel evidence.
5. Run and independently review the cases. No live acceptance, numerical parity,
   runtime benchmark or production verification is claimed by this source review.

Memory search and the old product guide supplied leads. Planning facts above
were checked against current canonical sources. Marketing claims and retired
agent instructions were not promoted into acceptance proof.
