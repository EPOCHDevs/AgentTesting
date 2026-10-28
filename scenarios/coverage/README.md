# Workflow coverage and release phases

Proposed acceptance programme · 27 October 2026 · Source review complete for the
listed surfaces; live workflow acceptance has not been run.

The catalogue contains **90 cases across 14 workflow families**: 37 release
prerequisites, 37 deeper workflows and 16 advanced compositions/stress cases.
The source inventory assigns 46 core macros and 37 package groups to these
families. These counts describe planned scope, not passing coverage.

Comprehensive means that every supported research journey has a home in the
checklist, its consequential compositions and failure modes are exercised, and
the result is independently checked. A catalogue entry, available function,
successful execution or convincing explanation is not passing coverage.

The unit of coverage is a person's decision and the workflow needed to support
it. Changing a ticker, persona or parameter creates a new case only when it
changes data semantics, analytical reasoning, execution behaviour or a material
failure mode. We do not enumerate every Cartesian product of features.

## Read and use this programme

1. [Capability evidence](CAPABILITIES.md): what current source declares, the
   important qualifications, and what still needs deployment verification.
2. [Workflow catalogue](WORKFLOWS.md): stable case IDs, phase, research journey,
   and the evidence that makes each case distinct.
3. [Acceptance checks](ACCEPTANCE.md): required checks, edge-case assignments,
   review rules and campaign records.
4. [Native inventory](capability-inventory.json): source metadata grouped by
   workflow family, including every core macro. This is an inventory, not a
   list of verified end-user capabilities.

The existing [role briefs](../README.md) remain useful openings. Select a case
and a suitable brief, let the researcher respond to the actual answers, and
record what was exercised. The review contract must not become a script pasted
into every conversation. No fixed turn count is required.

The repository's [audit scope](../../AGENTS.md) governs every case: vendor data
and its declared basis are authoritative; audit Epoch's use and calculation of
those inputs. Preserve original attempts and record reassessments as new reviews.

## What depth means

| Depth | Required evidence | Example |
| --- | --- | --- |
| D1: establish inputs | Exact universe, identities, windows, units, timestamps and coverage | A complete US-listed equity population and its eligibility accounting |
| D2: answer the research question | Correct measurements, appropriate comparisons and inspectable evidence | Explain quality, valuation and financial resilience across that population |
| D3: refine and compose | Preserve the original population and choices while adding a meaningful constraint | Add leverage and liquidity restrictions, then combine fundamentals with momentum |
| D4: act on the analysis | Explicit model/strategy specification, causal inputs, sizing and execution assumptions | Turn the selected cohort into a monthly factor portfolio and backtest it |
| D5: validate and reuse | Independent numerical checks, baselines, holdouts, robustness, saved identity and honest limits | Reuse a sealed model or completed backtest; test sensitivity without contaminating the holdout |

Not every request needs trading. An economist's forecast can reach D5 through
validated forecasts and reusable models. A corporate treasurer's research can
reach D5 through stable exposure estimates and evidence that changes a decision.
The depth target follows the research need.

## Phases

| Phase | Purpose | Completion rule |
| --- | --- | --- |
| P1: prerequisites for release — 37 cases | Exercise the important supported families end to end, including representative compositions, historical intraday and integrity failures | Every P1 case and its assigned acceptance checks has a reviewed passing result on the release candidate; no unresolved critical correctness, leakage, ownership or false-completion defect |
| P2: deeper workflows — 37 cases | Extend the proven foundations with richer financial questions, model lifecycle comparisons, execution choices and portfolio constraints | P1 remains valid; P2 cases pass with their stated dependencies and the expanded method/asset coverage recorded |
| P3: advanced composition and stress — 16 cases | Combine several demanding behaviours, larger workloads and uncommon market structures | P1/P2 regressions pass; P3 claims are limited to the combinations actually verified |

These are proposed release requirements, not a deployment authorization or a
statement that the product currently passes. A later-phase label is not
permission for an advertised feature to be incorrect. If a capability is in the
release promise, move its required case into P1 before the campaign starts.

P1 deliberately includes an all-US-equity journey, a historical membership
journey, fundamental + technical composition, model training/forecasting,
costed strategy execution, FX/futures/crypto representation, historical intraday,
tuning, Monte Carlo and saved-result follow-up. It is more than a smoke suite.
The full all-US request must be attempted as authored; a small index substitute
does not satisfy it. Admission, vendor coverage and runtime remain to be measured.

Within P1, establish source identity, population and analytical baselines first;
then validate feature/model boundaries and simple execution accounting. Use those
artifacts for the combined screening/strategy, intraday, Monte Carlo, tuning and
saved-result journeys. Apply interaction and evidence checks throughout. P2/P3
extend these verified foundations; they do not reset the earlier success criteria.

## Families and representative release journeys

| Family | Decision being tested | P1 anchors | Further depth |
| --- | --- | --- | --- |
| UN: universe and fundamental selection | Which companies qualify, why, and does the selection support a strategy? | UN01, UN02, UN07 | Growth durability, cash quality, valuations, segments, capitalization and factor efficacy |
| TH: technical and hybrid selection | Is an opportunity technical, fundamental, or supported by both? | TH01, TH02 | Breadth, sector effects, timing and multiple timeframes |
| CO: company, ETF and corporate events | What explains an issuer or fund's economics and event response? | CO01–CO03 | Holdings, IPO identities and international accounting bases |
| MA: macro and assets | Which economic conditions matter for the chosen exposure? | MA01, MA02 | Policy cycles, growth, supply, liquidity, forecasts and external event evidence |
| FE: features and explanatory models | Are measured drivers informative and correctly constructed? | FE01, FE02 | Cointegration, structural breaks, state-space estimates and difficult input panels |
| RE: regimes | Are states causal, interpretable and useful outside their fit? | RE01–RE03 | Clustering, refit mapping, state forecasts and macro/asset compositions |
| FC: forecasting | Does an explicitly defined forecast improve on an appropriate baseline? | FC01, FC02 | Checkpoints, multiple horizons, rolling fits, macro forecasts, model reuse and downside-risk validation |
| ST: strategy construction | Does the trading book implement the intended research rule? | ST01–ST03 | Long/short, event rules, priced orders and risk-sensitive sizing |
| PO: allocation and portfolio risk | Does the proposed allocation solve the investor's actual objective? | PO01 | Constraints, downside objectives, hierarchical methods, views and actual holdings |
| IN: historical intraday | Does the method respect when information and executable prices exist? | IN01, IN02 | Sessions, profiles, retrospective structure and intraday baskets |
| XA: asset-specific mechanics | Are results valid for the asset actually researched or traded? | XA01–XA03 | Futures curves, negative prices, roll details and path-dependent execution |
| VA: validation and robustness | Are performance and uncertainty claims supported? | VA01–VA03 | Stability, multiple comparisons and workload stress |
| BY: user and saved data | Can an analyst reuse their own evidence without changing its meaning? | BY01 | Imported signals, declared event chronologies and text-derived features |
| UX: conversation, execution and presentation | Can the user complete, inspect and continue the work reliably? | UX01–UX06, UX09 | Reproduction and richer output forms |

## Worked journey: all-US fundamentals → factor strategy

This is one coherent journey with consequential extensions, not a set of
unrelated prompts. UN01 and UN02 own it; TH02 extends it.

1. Establish what “all US equity” means: listing venues, security types,
   active/current versus historical population, cutoff and issuer/listing policy.
   Preserve the complete frozen population, counts and exclusions. Do not
   replace it with search results or a conveniently smaller index.
2. Screen for a stated economic thesis, such as profitable growth at a reasonable
   valuation. Inspect revenue, margins, cash conversion, leverage and valuation
   with compatible periods. Explain which findings support or weaken the thesis.
3. Refine from that evidence. For example, exclude fragile balance sheets or
   require sustained cash generation. Preserve earlier predicates, show which
   names enter/leave and why, and report missing or ineligible names separately.
4. Investigate an economically meaningful disagreement: cheap but deteriorating
   businesses, strong earnings with weak cash conversion, or improving operating
   performance with stretched valuation. The analyst's objective determines the
   next question.
5. Establish the historical selection rules before creating a factor strategy:
   information timing, eligible population at each date, score direction and
   normalization, ties, rebalance schedule, weights, costs and benchmark basis.
6. Backtest and explain holdings, turnover, exposures, returns, drawdowns and
   failures. A current survivor screen is not a historical investable universe.
   Filing-date alignment is not proof of immutable as-published fundamentals.
   Preserve those distinctions in the actual claim being tested.
7. Extend to TH02 only when useful: add momentum/trend or liquidity to the same
   thesis, and measure its incremental effect over the fundamental baseline.
8. Validate selected intermediate rows and trade/account reconciliation, then
   inspect sensitivity or a predeclared holdout. An unprofitable but correct
   result can pass. A profitable result with leakage cannot.

Growth durability (UN03), cash quality (UN04), valuation reconstruction (UN05),
segment concentration (UN06) and capitalization refinement (UN08) are separate
variants because they require different reasoning and data contracts. Simply
changing a sector or renaming the analyst is not sufficient.

## Worked journey: features → regime/model → forecast → decision

1. Specify the target, information cutoff, observation frequency and forecast
   horizon. Distinguish forecasting a price level, return, conditional variance,
   economic series or latent-state distribution.
2. Build economically motivated features: returns, realized volatility, momentum,
   liquidity or release-aware macro inputs. Preserve missingness and source
   clocks. FE01 checks scaling, warmup and optional PCA within the allowed history.
3. Use RE01/RE02 to estimate and reuse an HMM when the question is about regimes.
   Preserve feature order, units, recipe, cutoff and model identity. State numbers
   have no permanent economic meaning across refits.
4. Use FC01 for ARIMA price forecasts and FC02 for GARCH volatility forecasts.
   Compare the correct target with a declared baseline and an untouched later
   evaluation window. Price forecast accuracy and volatility accuracy are
   different claims.
5. FC03/FC04 distinguish untouched checkpoint forecasts, forecasts after observing
   new rows, and rolling/expanding refits. Their forecast timestamps and horizons
   are not interchangeable.
6. Only then combine models into a decision (RE05/FC06): conditional exposure,
   volatility sizing or a forecast-driven signal, with a baseline strategy and
   the actual execution delay. Demonstrate incremental evidence, not just a
   more complicated dashboard.

“Train/forecast” refers to the native model families in the evidence inventory.
An arbitrary estimator, external model or unverified exogenous-feature API is
not automatically supported because Epoch has model artifacts.

## Macro themes × assets: meaningful combinations

Every selected pairing needs a reason and observable data. These cells allocate
coverage; they do not claim that every requested series/window is populated.
Use exact served identities when instantiating a case. Cross-asset results may
share a study but each leg needs its own coverage and unit checks.

| Economic theme | Equity / ETF exposure | FX | Commodity / equity-index futures | Crypto | Case and reason |
| --- | --- | --- | --- | --- | --- |
| Inflation and surprises | Broad market and rate-sensitive sectors | USD major pair | Gold/oil or equity-index futures | — | MA01, IN02: discount-rate, currency and commodity channels; daily versus release-time response |
| Policy tightening/easing | Growth versus value or domestic/export exposures | Rate-differential pair | Equity-index future | Optional later extension | MA03: policy-cycle position and pre-existing inflation conditions |
| Growth and employment | Cyclical versus defensive sectors | Cyclical currency pair | Copper/oil | — | MA04: growth sensitivity with release-aware information |
| Energy supply/inventories | Energy producers versus airlines | Commodity-linked currency | Crude term structure | — | MA05, XA04: producer/input-cost/roll channels differ |
| Liquidity, money and credit conditions | Broad risk exposure | Funding currency | Equity-index future | Major spot pair | MA06: conditional co-movement; no unsupported causal claim or fabricated funding carry |
| Housing and household demand | Homebuilders/retail versus broad market | — | — | — | MA06: economically focused sector exposure rather than generic cross-asset permutations |
| Country divergence / trade | Exporter revenue regions and sector mix | Quote/base-country comparison | Relevant commodity where justified | — | MA03, UN06, XA02: country meaning, FX direction and heterogeneous reporting calendars |
| Economic forecasts and revisions | Relevant sector exposure | Currency comparison | Optional observed exposure | — | MA02, MA07, FC05: current provider forecast versus historical forecast evidence and fitted forecast |
| Externally established shocks | Broad/sector exposure | Relevant currency | Oil/gold | Optional later extension | MA08: chronology from explicit evidence, distinct from an economic-release calendar |

Bond-focused research/trading stays excluded. Policy rates, macro financial
conditions and yield observations used to explain an in-scope asset are not
automatically excluded; do not turn that into a bond-strategy assignment.

## Strategy shapes that must have a home

| Shape | Case(s) | Distinguishing contract |
| --- | --- | --- |
| Buy/hold or periodic allocation | PO01, VA01 | Executed account versus analytical benchmark; cash and dividend treatment |
| Fundamental cross-sectional factor | UN02, UN07 | Historical eligibility, available inputs, ranks, weights and rebalance |
| Technical/hybrid ranking and rotation | TH02, ST03 | Cross-sectional ranking versus per-asset signals; incremental filter effect |
| Time-series trend / breakout | ST01, IN01 | Signal timing, next eligible fill and position persistence |
| Mean reversion | ST02 | Recovery exit, maximum hold and protective-exit precedence |
| Long/short or relative value | ST04, FE03 | Both legs, hedge/weight semantics, gross/net exposure and omitted financing |
| Event / seasonal strategy | ST05, IN02 | Information time, calendar boundaries and event overlap |
| Model/regime-driven strategy | RE05, FC06 | Causal features, fitted-state semantics, model cutoff and baseline comparison |
| Allocation / risk-budget strategy | PO02–PO05 | Constraint feasibility, covariance/return estimation and realized weights |
| Limit, stop and stop-limit execution | ST06 | Submission versus activation, expiry/cancellation, gaps and partial fills |
| Risk-managed scaling / insurance | ST07 | Sizing clock, equity feedback, floors and exposure caps |
| Futures continuation and carry | XA01, XA04 | Real contracts, multipliers, two leg prices and roll convention |
| Intraday and multi-timeframe | IN01–IN06, TH05 | Session clocks, completed higher-timeframe information and bar-path uncertainty |
| Explicit futures price-path / pyramiding | XA06 | Supported path and unit lifecycle; distinct from ordinary bar execution |

## Personas and the original six briefs

Personas provide motivation and success criteria. They are not independent
coverage counts. Use the same family with different personas only when their
decision, constraints or evidence needs differ.

| Existing brief | Natural continuation | What would make the extension useful |
| --- | --- | --- |
| Industrial resilience | CO01 → UN04 → UN01/UN02 | Expand a two-company judgment into a defensible universe and portfolio thesis |
| A simpler equity ride | ST01/ST02 → VA01–VA03 → PO01 | Quantify costs, risk, robustness and the tradeoff against a simple alternative |
| The next inflation outlook | MA02 → FE01 → FC05 → RE06 | Separate revisions, fit evidence, uncertainty and conditional economic states |
| Euro revenue exposure | XA02 → FE02 → MA03 | Quote direction, revenue currency and the stability of an exposure relationship |
| Fuel and airline performance | MA05 → FE02 → XA04 | Separate commodity spot/futures/roll effects from airline operating exposure |
| Diversifying technology exposure | TH03 → PO01/PO02 → FE02 | Determine whether the new basket changes actual factor and country risk |

Add briefs for an event analyst, historical intraday trader, commodity PM, model
researcher and portfolio-risk analyst when instantiating those cases. Reuse the
case acceptance contract; do not manufacture different feature lists by persona.

## Coverage accounting

Track separate denominators for: workflow cases; depth stages; meaningful
asset/macro combinations; native method/option contracts; and common acceptance
checks. A family passing once does not prove every model, allocator or order
type in that family. The native inventory assigns ownership so a new source or
macro cannot disappear from planning, while method-level evidence must name the
exact methods and configurations actually used.

Before a release campaign, freeze scope, environments, revisions, cases,
fixtures, tolerances and review assignments. Preserve unknowns and failures in
the ledger. Extend this plan when the verified capability inventory changes;
do not infer deployment coverage from local metadata or a marketing guide.
