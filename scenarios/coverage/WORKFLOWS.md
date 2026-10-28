# Workflow catalogue

[Programme and phases](README.md) · [Acceptance contract](ACCEPTANCE.md) ·
[Capability evidence](CAPABILITIES.md)

All cases start **not run**. P1/P2/P3 are proposed required phases. Each row is a
research journey, with specific evidence beyond the common acceptance contract.
Referenced earlier cases are foundations or comparison results, not automatic
passing credit. A later case may reuse a verified artifact only when its source,
window, owner, version and semantics still match.

## UN — Universe and fundamental selection

Personas: fundamental analyst, equity PM, systematic researcher. Foundation:
complete frozen universe → financial evidence → refinement → selection → strategy.
Every case keeps total, loaded, eligible, missing, rejected and selected counts.

| ID | Phase | Research journey | Distinct evidence and edge case |
| --- | --- | --- | --- |
| UN01 | P1 | Screen all covered US-listed common equities for a profitable-growth/value thesis; explain the candidates; add a balance-sheet restriction and compare the revised set | Complete population receipt; original clauses survive refinement; missing financials remain visible; empty selection is explained; no top-N search or index substitution |
| UN02 | P1 | Turn UN01's thesis into a historically rebalanced quality/value factor strategy and compare it with a declared benchmark | Historical population and information rules established independently of today's shortlist; ranks, tie rules, weights and turnover verified; current-history qualifications explicit; if required PIT evidence is unavailable the positive case remains blocked |
| UN03 | P2 | Find durable growers, then distinguish sustained growth from recovery off a weak base or an acquisition jump | Quarterly/annual/TTM distinctions; explicit endpoint CAGR versus provider growth; negative/zero bases; insufficient quarters; added conditions change selection for an explained economic reason |
| UN04 | P2 | Find apparently profitable companies; investigate cash conversion, working capital, reinvestment and leverage before refining quality | Reconcile income, balance sheet and cash flow; signed capex, cash definitions, stock compensation and denominator failures; owner earnings remains a provider-modelled measure |
| UN05 | P2 | Compare reported valuation with a dated current-capitalization/TTM-earnings construction; decide whether apparent cheapness survives | Compatible currency, share and time bases; nonpositive earnings; stale capitalization; issuer/listing duplication; distinguish provider ratio, aggregate earnings multiple and current-price EPS multiple |
| UN06 | P3 | Screen companies with concentrated product/geographic revenue; compare margin and growth resilience; test a more diversified cohort | Long segment relations, duplicate reports, changing segment labels, unequal fiscal calendars and source currency; country exposure comes from measured revenue rather than domicile alone |
| UN07 | P1 | Rank and rebalance a covered index using membership as it changed; compare with an explicitly fixed current roster | Historical union is only the load set; membership gates precede ranking/aggregation and remain in reports; exercise an addition and removal; unsupported history fails visibly |
| UN08 | P2 | Refine an existing current universe by capitalization, then apply a computed financial threshold and revisit the rejected cohort | Current-cap source is distinct from report-period and historical daily cap; strict boundary values, zero/nulls, frozen cohort identity and a changed user predicate |
| UN09 | P2 | Test whether the proposed factor actually separates future outcomes before building a more complex strategy | Quantile returns, cross-sectional IC and rolling IC information ratio; common eligible pairs, ties and minimum sample; native rank-correlation significance is approximate; forward-return labels are evaluation evidence, never available trading inputs |

## TH — Technical and hybrid selection

Personas: discretionary analyst, quant researcher, equity PM. Selection must
answer an investment question; adding indicators without a decision is not depth.

| ID | Phase | Research journey | Distinct evidence and edge case |
| --- | --- | --- | --- |
| TH01 | P1 | Screen a complete US-listed universe for trend/momentum with liquidity and volatility context; explain the leaders and refine | Valid bars, warmup, split-adjusted price basis, stale prices, ties and liquidity missingness; chosen signal families and ranked population reconciled |
| TH02 | P1 | Add technical confirmation to the fundamental selection in UN01; compare fundamental-only, technical-only and combined outcomes; develop the selected strategy | Same eligible population and dates; score scales/directions declared; incremental value measured; no accidental intersection caused by missing data; new logic has distinct study identity |
| TH03 | P2 | Determine whether strong index performance reflects broad participation or a few sectors; build a sector-aware rotation comparison | Breadth denominator, changing roster, equal/cap weighting and sector neutrality; concentration can contradict the headline index result |
| TH04 | P2 | Investigate a pullback versus structural deterioration using trend, momentum, volatility and volume; define an invalidation condition | Rule is measurable; correlated indicators do not count as independent evidence; whipsaws and low-volume periods; distinguish research levels from executable exits |
| TH05 | P3 | Use a daily eligibility/trend filter for intraday entries across the selected basket | Only completed daily information reaches each intraday decision; no forward fill across issuer/session boundaries; common cutoff and independent execution clocks |

## CO — Company, ETF and corporate-event research

Personas: company analyst, event analyst, family-office analyst. These are
different information structures from a general equity screen.

| ID | Phase | Research journey | Distinct evidence and edge case |
| --- | --- | --- | --- |
| CO01 | P1 | Compare two businesses' resilience; reconcile all three statements, margins, growth and cash generation; investigate the weakest part of the thesis | Sparse filings, fiscal periods, inconsistent cash definitions, currency and denominator validity; conclusions supported by exact source rows |
| CO02 | P1 | Examine earnings actuals versus expectations and subsequent price reaction; refine by surprise size and reporting session | Estimate/actual meaning, null estimates, release-time precision, after-close/weekend alignment, overlapping events and small cohorts; no same-bar hindsight |
| CO03 | P1 | Explain a stock's price path around dividends and splits; compare price return with dividend-inclusive research and executed account performance | Upstream split adjustment, ex-date cash, long credit/short debit, repeated event deduplication, benchmark basis and no double dividend addition |
| CO04 | P2 | Compare two ETFs' current holdings, overlap and concentration; assess whether the proposed switch actually diversifies exposure | Snapshot date, raw weights/units, cash/unknown holdings, issuer duplication; current holdings are not asserted to be a historical constituent tape |
| CO05 | P2 | Investigate an IPO cohort from calendar through prospectus and later performance; separate upcoming, priced and withdrawn events | Amendments versus unique issuers, incomplete calendar history, offering amount versus capitalization/proceeds, reused tickers and limited post-listing price history |
| CO06 | P3 | Compare domestic and international issuers or an ADR with its ordinary share using financial evidence | Financial coverage versus available market bars, reporting/quote currency, ADR/share basis, estimated filing dates and fiscal-calendar mismatch; unavailable conversions stay explicit |

## MA — Macro themes and asset exposure

Personas: macro PM, economist, corporate treasurer, commodity/sector analyst.
Use the [theme × asset allocation](README.md#macro-themes--assets-meaningful-combinations).
The same macro print can be a level, change, surprise or revision; each answers a
different question.

| ID | Phase | Research journey | Distinct evidence and edge case |
| --- | --- | --- | --- |
| MA01 | P1 | Compare equity, FX and a relevant commodity/index-futures response to inflation surprises; refine by prevailing inflation or policy conditions | Actual versus consensus units, event IDs, economic reference period versus release time, sample counts, source gaps and common windows |
| MA02 | P1 | Reconstruct what a macro analyst could know at a historical cutoff; compare first/retained vintage and latest-revised conclusions | Revision capture time versus observation date, covered vintage window, duplicate observation/revision rows, as-of join and release schedule versus actual observations |
| MA03 | P2 | Compare tightening, easing and hold cycles; assess FX and equity exposures conditional on the starting economy | Define cycles before measuring outcomes; policy rate observations are not bond trading; FX direction, country calendars and changing reaction functions |
| MA04 | P2 | Test how growth/employment conditions relate to cyclical versus defensive sectors and a growth-sensitive asset | Native seasonal adjustment, frequency and units; release lags; levels versus growth; GDP quarter versus monthly labour observations; correlation is not causality |
| MA05 | P2 | Trace inventory/supply or oil-price shocks through energy producers, airlines and a commodity-linked currency | Source-owned inventory/release identity; fuel input versus traded crude exposure; spot/continuous/per-contract differences; no implicit substitution for unavailable aviation-fuel data |
| MA06 | P3 | Test liquidity/credit conditions against equity and crypto risk, then investigate housing/household-demand sector sensitivity | Explicit economic hypothesis for each branch; monthly-to-daily availability; revised series, structural breaks, small crisis samples and no fabricated crypto funding cost |
| MA07 | P2 | Compare current provider economic/market forecasts with an analyst's own fitted forecast and historical outcomes where supported | Current snapshot is not historical consensus; forecast issue date, target period, units, horizon and forecast-origin availability preserved |
| MA08 | P3 | Research an externally documented shock, construct an auditable event chronology, compare past episodes and refine by starting macro conditions | Native economic calendar is not a conflict/news chronology; explicit web/user-file provenance; predeclared event selection, overlapping shocks and attribution uncertainty |

## FE — Feature engineering and explanatory models

Personas: quant researcher, risk analyst, economist. Feature engineering includes
availability and validation, not only a transformed numeric column.

| ID | Phase | Research journey | Distinct evidence and edge case |
| --- | --- | --- | --- |
| FE01 | P1 | Build a return/volatility/momentum feature panel; compare scaled features and a PCA representation before model fitting | Per-asset versus cross-sectional scaling, train-only/causal windows, ordered features, warmup intersection, constant inputs, missing rows, explained variance and component sign/rotation ambiguity |
| FE02 | P1 | Explain a stock/sector/FX exposure with named market and macro factors; compare full-period and rolling relationships | Aligned calendars, compatible return units, covariance/beta and regression direction, collinearity, residuals and changing exposure; descriptive fit is not out-of-sample prediction |
| FE03 | P2 | Investigate a plausible relative-value pair or basket using stationarity and cointegration before proposing a spread rule | ADF, Engle–Granger and Johansen each get a named subcase; argument order, hedge ratio, training window, unstable relationships and holdout deterioration |
| FE04 | P2 | Compare a state-space level/trend estimate with observed data; use change detection to investigate a suspected structural break | Kalman assumptions, causal update, CUSUM threshold/sensitivity and false alarms; distinguish noise smoothing, change detection and forecasting |
| FE05 | P3 | Rebuild the model feature panel after a missing source, shorter-history asset or revised definition appears | No silently changed feature order/universe; finite sample counts after warmup/drop; scaler variants and validation split integrity; failed model input stays a failure |

## RE — Regime detection and reusable fitted state

Personas: macro PM, model researcher, systematic PM. Regime IDs are fit-local;
economic naming requires evidence.

| ID | Phase | Research journey | Distinct evidence and edge case |
| --- | --- | --- | --- |
| RE01 | P1 | Fit a causal HMM on declared features; explain state probabilities and conditional market behaviour | Causal observed filtering, seed, training/prediction boundary, probability interpretation and finite-sample sufficiency; no permanent meaning assigned to state 0 |
| RE02 | P1 | Train and save an immutable HMM, reopen it in a later execution, infer and score genuinely held-out observations | Exact model digest, feature recipe/order/units and cutoff; owner access; no replay of training data; predictive density is not a probability and is comparable only on the same feature basis |
| RE03 | P1 | Compare causal HMM output with retrospective smoothing and attempt to use the latter as a strategy input | Preserve the compiler's methodological warning; an executable retrospective experiment cannot pass as causal evidence; a lag or renamed field does not remove hindsight |
| RE04 | P2 | Compare K-means market clusters and DBSCAN outliers for an anomaly/segmentation decision | Scaling, parameters, noise labels, finite rows, seed and refit instability; clustering does not automatically become a persistent economic-state forecast |
| RE05 | P2 | Add regime-dependent exposure to a baseline strategy and evaluate stability through refits | Supported economic state mapping or explicit neutral labels; uncertainty, decision delay and baseline comparison; threshold regimes cannot silently replace requested model regimes |
| RE06 | P3 | Combine release-aware macro features, asset features and optional PCA in regime models across markets | Mixed clocks and cadences, common versus per-key models, feature provenance, revised macro information and legitimate cross-refit comparison |
| RE07 | P2 | Forecast future regime probabilities from an earlier immutable HMM and compare horizons with the current filtered posterior | Transition-only state forecast, normalized probability tuples, fit-local state meaning and horizon counted in observations; origin-indexed output does not forecast feature values or create future timestamps |

## FC — Forecasting workflows

Personas: model researcher, economist, risk PM. Score the actual forecast target
at its correct horizon. Preserve fit diagnostics separately from forecast errors.

| ID | Phase | Research journey | Distinct evidence and edge case |
| --- | --- | --- | --- |
| FC01 | P1 | Train an ARIMA price-level model on an earlier window, forecast later observations and compare with a last-observation/random-walk baseline | Explicit level/return and differencing choice; origin versus target timestamps, interval coverage, MAE/RMSE and exact fit identity; no double shifting or same-sample validation |
| FC02 | P1 | Train GARCH on returns, forecast volatility and compare with declared realized-volatility and simple risk baselines | Return, variance and volatility units; horizon-compatible realization, annualization, loss measure, convergence and finite samples; a volatility forecast is not a price or automatic VaR forecast |
| FC03 | P2 | Compare 1/5/21-observation forecasts from an untouched checkpoint with forecasts conditioned on newly observed rows | From-fit versus observed-origin semantics; same immutable model, no fabricated future bars/dates, no decision-day assimilation in an untouched forecast |
| FC04 | P2 | Compare rolling and expanding fits and different permitted refit cadences on the same forecast problem | Step/horizon/minimum-sample constraints, incomplete final horizon, exact-horizon selection, origin-count weighting and outputs already placed on target rows |
| FC05 | P2 | Forecast an economic series and test whether it improves on naive/seasonal benchmarks at the requested publication horizon | Native monthly/quarterly grid, transformed versus level target, revision/vintage choice and later holdout; ARIMA support is not a promise of arbitrary multivariate/exogenous models |
| FC06 | P3 | Combine price forecasts, risk forecasts and a justified regime filter into a strategy; compare each addition to the baseline | Causal decision inputs, target horizons, size/execution clock, baseline ablation, costs and holdout discipline; predictive improvement need not improve trading performance |
| FC07 | P2 | Reuse ARIMA/GARCH artifacts across sessions, then test incompatible model options, changed feature provenance and future-trained inputs | Immutable reference and digest, hard compatibility failures versus methodological warnings, actual training cutoff and explicit unknown provenance |
| FC08 | P2 | Evaluate downside-risk exceedances using the supported rolling-GARCH risk outputs, then investigate failures during stress | Native VaR outputs use Gaussian multipliers even with another fit distribution; they are centered innovation-loss magnitudes, not mean-adjusted return forecasts; correct units, target rows, sample counts and exceedance definition |

## ST — Shapes of strategy development

Personas: systematic researcher, discretionary trader, equity PM. Specify entry,
exit, holding behaviour, sizing, capital, costs, time grid and benchmark before
interpreting a backtest.

| ID | Phase | Research journey | Distinct evidence and edge case |
| --- | --- | --- | --- |
| ST01 | P1 | Research a trend/breakout rule, build it and compare a costed daily strategy with buy-and-hold | Decision close versus next open/close, warmup, persistent held position, gaps and no fabricated fills; output trade count comes from executed trades |
| ST02 | P1 | Research a mean-reversion entry; add recovery, maximum-hold and protective exits; investigate losing trades | Exit ownership/precedence, flat/hold/flip behaviour, stop/target on one OHLC bar, gap-through levels and account reconciliation |
| ST03 | P1 | Rank a basket by relative momentum and rotate periodically; compare with a fixed basket | Cross-sectional selection versus single-asset signals; rebalance calendar, vacancies, ties, new/short-history assets, turnover and concentration |
| ST04 | P2 | Develop a long/short factor or evidence-backed pairs strategy from UN02/FE03 | Both legs' executable quantities, hedge versus capital weights, gross/net exposure, short dividend debit and absent borrow/margin funding; report the engine's actual cost scope |
| ST05 | P2 | Turn a tested earnings, macro or calendar effect into an event/seasonal strategy | Announcement availability, event overlap, holiday/month-end conventions, first eligible fill and no use of future event outcomes |
| ST06 | P2 | Compare market, limit, stop and stop-limit entries for the same thesis; investigate gaps, expiry and unfilled orders | Named subcases for each order type; captured levels, tick rounding, cancellation versus exit, partial entry protection and ordinary bar-path ambiguity |
| ST07 | P3 | Extend a baseline strategy with risk-per-trade, drawdown/exposure control and justified adaptive sizing | Position-size/risk-unit clocks, equity feedback, lot/multiplier rules; named Kelly/optimal-f/CPPI/TIPP variants where economically appropriate; no invented financing or guaranteed floor |

## PO — Allocation and portfolio risk

Personas: multi-asset PM, family-office analyst, risk analyst. The investor's
objective determines the allocator; method names alone do not define scenarios.

| ID | Phase | Research journey | Distinct evidence and edge case |
| --- | --- | --- | --- |
| PO01 | P1 | Assess a proposed equity/commodity/FX-related diversification; compare equal and inverse-volatility allocations and execute the selected rebalance rule | Exact asset exposure, return/FX basis, estimated versus realized weights, common sample, cash, turnover and marginal risk; no bond sleeve under current exclusions |
| PO02 | P2 | Allocate to a selected basket under concentration limits using risk parity/budgets and minimum-variance or diversification objectives | Named method subcases, covariance estimation, feasibility, singular inputs, zero volatility, missing members, constraint compliance and sum of weights |
| PO03 | P2 | Rework an allocation around downside or tail risk and compare it with the variance baseline | Minimum-CVaR and semivariance subcases, loss units, sample tail size, risk parameters and realized tradeoffs; distinguish forecast risk from realized drawdown |
| PO04 | P3 | Test whether hierarchical clustering improves diversification in a correlated universe | HRP/HERC subcases, distance/dependence/linkage settings, unstable clusters, near-duplicate assets, ordering effects and turnover |
| PO05 | P3 | Express explicit investor views in Black–Litterman and compare a max-Sharpe candidate with the base portfolio | Views/confidence, covariance/expected-return provenance, posterior outputs, constraints and sensitivity; optimizer output is not an execution book |
| PO06 | P2 | Analyze a supplied actual-holdings snapshot, explain concentration and factor risk, then assess a proposed change | Snapshot identity/as-of, positions/cash/currency reconciliation, unknown cost basis or missing history; illustrative portfolios do not satisfy an actual-holdings request |

## IN — Historical intraday and sessions

Personas: historical intraday trader, event trader, execution researcher. No live
quote or brokerage requirement is implied.

| ID | Phase | Research journey | Distinct evidence and edge case |
| --- | --- | --- | --- |
| IN01 | P1 | Research an opening-range breakout, define entry/protection, flatten by the session rule and inspect a historical backtest | Range must complete before decision; exchange calendar, bar granularity, end-of-day cutoff, next valid fill, overnight carry and one ordinary session-boundary counterexample |
| IN02 | P1 | Compare exact minute checkpoints after a macro release across an equity/index-futures and FX exposure; test a causal event-entry rule if requested | Timezone, release timestamp, missing exact bars, price denominator and no nearest-bar substitution; information available at the selected checkpoint |
| IN03 | P2 | Compare regular, pre-market, post-market and overnight behaviour through DST, holidays and shortened sessions | Named supported-session subcases; source-native minutes before aggregation; unsupported modified-close post-market case stays explicit; calendars are asset-specific |
| IN04 | P2 | Investigate time-of-day seasonality, overnight gaps and price/volume-profile levels, then evaluate a testable entry hypothesis | Correct session denominator, volume versus TPO meaning, POC/value-area inputs and initial-balance settings; profile observations are not order-book evidence |
| IN05 | P3 | Analyze market structure using swings, breaks, order blocks, liquidity and gaps; separate retrospective interpretation from an executable causal hypothesis | Source-owned confirmation/retrospective semantics, nulls, future mitigation and no magic fixed lag; a visually persuasive historical annotation is not a causal strategy |
| IN06 | P3 | Build an intraday basket using technical/fundamental eligibility and cross-sectional ranks, then evaluate costs and participation | TH05 plus multiple calendars, sparse bars, intraday rank population, execution volume availability, shared participation allowance and workload admission |

## XA — Futures, FX and crypto mechanics

Personas: commodity PM, currency analyst, cross-asset researcher. A ticker swap
is a new case here because the contract, clock or accounting changes.

| ID | Phase | Research journey | Distinct evidence and edge case |
| --- | --- | --- | --- |
| XA01 | P1 | Research a futures trend using a declared continuation, then inspect a historical strategy through a contract transition | Continuous versus dated contracts, adjustment basis, multiplier/tick, available history and ordinary automatic roll convention; account/fill evidence crosses the roll |
| XA02 | P1 | Compare an FX exposure and a relevant economic driver; develop a simple historical signal or hedge comparison | Base/quote direction, crosses, account conversion, holidays, absent exchange volume and no fabricated forward points, carry or financing |
| XA03 | P1 | Research a major spot-crypto trend/risk pattern and evaluate a simple strategy across weekends | Exact venue/pair, 24/7 timestamps, bar boundaries, coverage, quote currency and no implied perpetual-futures/funding contract |
| XA04 | P2 | Compare futures curve slope, carry and calendar spread; test a strategy with an explicit supported roll policy | Unadjusted real-leg prices, actual contracts/DTE, expiry, missing leg, term-structure units and daily paired executable quotes; no intraday explicit-roll promise |
| XA05 | P2 | Revisit a futures/commodity analysis through zero/negative prices or discontinuous history | Valid denominator for returns, difference versus ratio meaning, gap/roll diagnostics, duplicate rows and no silent deletion of an inconvenient episode |
| XA06 | P3 | Evaluate an account-currency futures price-crossing strategy with an explicit OHLC path and unit/pyramiding lifecycle | O-H-L-C versus O-L-H-C sensitivity, stop-limit gaps, allowed exits, risk unit and OriginalPrice recovery; reject unsupported target rebalancing/trailing/take-profit combinations |

## VA — Validation, tuning and robustness

Personas: model-risk reviewer, quant researcher, PM. A negative research finding
can pass; an unsupported positive claim cannot.

| ID | Phase | Research journey | Distinct evidence and edge case |
| --- | --- | --- | --- |
| VA01 | P1 | Independently reconcile a completed backtest, then compare benchmark and execution-cost assumptions | Fill/position/account chain, cash/dividends, FX/multipliers, gross versus net, configured versus realized slippage and common benchmark return basis |
| VA02 | P1 | Run Monte Carlo robustness on an identified completed backtest and interpret return/drawdown distributions | Exact parent execution, resampling method/seed/sample size, dependence assumptions and interval meaning; simulated uncertainty does not prove a market edge |
| VA03 | P1 | Declare tunable parameters, select a bounded search, choose on training/validation and evaluate an untouched holdout | Explicit objective/search space/budget, failed trials, trial count, parameter identity, no holdout reuse or retrospective winner selection; the best trial is not automatically the most robust |
| VA04 | P2 | Challenge an apparently good model/strategy across periods, cohorts, costs and economically justified parameter neighbourhoods | Predeclared comparisons, overlapping observations, regime/sample counts, stability, multiple-comparison exposure and contradictions in return/risk tradeoffs |
| VA05 | P3 | Scale a proven multi-asset/multi-model workflow and examine incomplete or failed executions under workload pressure | Measured latency, observed cost and data volume; truthful progress/partial results; no lost cohort, stale execution reuse or automatic scope reduction |

## BY — User data and saved evidence

Personas: analyst with existing studies, external-model researcher, risk analyst.
Backend contracts do not guarantee that every import path is exposed in the UI.

| ID | Phase | Research journey | Distinct evidence and edge case |
| --- | --- | --- | --- |
| BY01 | P1 | Reuse an owned saved dataset/model or completed execution to answer a follow-up and publish a new derived comparison | Exact source/version/owner; source-owned clocks and units; compute from real result rows, not a chart title; no silent source rerun or substitution after an unavailable read |
| BY02 | P2 | Import an explicitly provided external signal/feature table, reconcile it to native assets and evaluate it in a costed strategy | Import review, schema/types/timezone, row keys, duplicates, missing symbols, signal availability and immutable source identity; unsupported UI entry stays a channel gap |
| BY03 | P3 | Import a documented event/text corpus, derive dictionary-based classifications and compare event-conditioned asset outcomes | Text/dictionary provenance, missing/ambiguous categories, source chronology and sensitivity; native keyword/topic transforms are not an assumed pretrained sentiment or causal model |

## UX — Conversation, execution and presentation

Personas: all. Apply these as parts of domain journeys where possible. Retain
separate case IDs because their state and evidence contracts need explicit review.

| ID | Phase | Research journey | Distinct evidence and edge case |
| --- | --- | --- | --- |
| UX01 | P1 | Start with an ambiguous research need, settle material choices, refine it, then change one requirement after seeing evidence | Questions are necessary and batched; existing choices survive; no repeated approval for unchanged scope; Chat plan confirmation versus declared API behaviour; contradiction handled openly |
| UX02 | P1 | Request a response-only answer, then a dashboard/report and a presentation edit using the same completed evidence | Correct output channel, numeric traceability, no unrequested source rerun, stable result IDs, grouped chart/table identity, units, nulls and warning visibility |
| UX03 | P1 | Start from a real market-context or economic-calendar page, select an item, ask follow-up research and return to its evidence | Native selected context, event/asset identity, freshness, current snapshot versus historical data, successful publication and visible partial/unavailable source state |
| UX04 | P1 | Launch a study, reconnect after interruption, inspect a failed run and explicitly retry/cancel through supported controls | Logical slot and execution identity, idempotency, uncertain submission, retained failure evidence, truthful completion and cancellation ownership; require actual channel support |
| UX05 | P1 | Reopen owned artifacts and test a share/second-user boundary with dedicated test accounts | Dataset/model/execution ownership, immutable revisions, authorized read scope, revoked/invalid links and no secrets in published evidence; UI share creation requires the case's explicit authorization |
| UX06 | P1 | Request a broad universe and a demanding window, inspect measured admission and coverage, and handle an exceeded limit | Native screen ceiling and actual capacity, complete cohorts rather than previews, vendor pagination/entitlement failures, no silent clipping and truthful elapsed/cost reporting |
| UX07 | P2 | Save, reopen and reproduce a study/model/report after a later source or execution exists | Explicit latest-versus-pinned choice, model immutability, source revisions, execution history and preserved assumptions; a newer result must not overwrite the comparison identity |
| UX08 | P2 | Present nested cohort evidence in complementary tables, paths, distributions, heatmaps and summaries; revise layout | Numeric agreement, readable labels, correct denominators/panels, large/sparse results, percentage/currency formats and faithful explanation of uncertainty |
| UX09 | P1 | Ask for an excluded or unverified capability within a legitimate workflow and evaluate the response | Options, bond-focused trading and live prices preserve the stated exclusion; monitor placeholders, unsupported financing/model/roll features are identified; alternatives require the user's decision and do not mark the original need fulfilled |
