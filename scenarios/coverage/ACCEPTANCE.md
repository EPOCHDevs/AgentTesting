# Acceptance and evidence

[Programme](README.md) · [Cases](WORKFLOWS.md) · [Source evidence](CAPABILITIES.md)

This is the reviewer contract. Give the researcher the decision and context;
keep numerical answer keys and internal checks separate. Follow actual answers
instead of requiring a memorized sequence of prompts.

Follow the repository's [audit scope](../../AGENTS.md): the selected vendor's
data and declared basis are authoritative. Independent review checks Epoch's
source selection, bindings, units, transformations, calculations and delivered
results against those inputs. It does not re-audit vendor values through external
filings, invented accounting identities or inferred quarter/YTD conversions.
Vendor differences alone are not platform failures. Preserve original attempts;
a revised interpretation is a new review, not a replacement artifact or fresh run.

## What passes

A positive workflow passes only when the stated research need is met, required
results are correct and inspectable, material limitations are visible, and the
reviewer can connect conclusions to source and execution evidence. A no-edge,
uncertain or commercially unattractive result can pass. Profitability is not a
software correctness criterion.

An honest explanation of an unavailable source can pass a **boundary-behaviour
test** while leaving the requested positive workflow **blocked**. Record both.
Do not convert a failed prerequisite into a pass by renaming it a limitation,
testing only a smaller universe, or excluding the missing capability. An
accepted alternative gets its own scope and evidence; it does not erase the
original result. Only the owner can change release scope or existing exclusions.

## Common checks

Apply every relevant check to every case. An N/A needs a concrete reason. Home
cases ensure deliberate counterexamples are run; other cases are not exempt.

| Check | Acceptance condition | Primary deliberate cases |
| --- | --- | --- |
| G01: population | Frozen universe definition and full count match the request; preserve predicates on refinement; distinguish searched candidates, loaded assets and runtime eligibility | UN01, UN07, UN08, TH02, UX06 |
| G02: identity and sources | Exact assets/series/venues, source versions, currencies, units, adjustment and return basis are established; unresolved facts remain unresolved | CO03, CO06, MA01, XA01–XA03, BY02 |
| G03: time and availability | Observation, fiscal period, filing, release, capture, model-fit, decision and execution clocks are distinguished; joins use only information allowed by the claim | UN02, UN07, MA02, RE02/RE03, FC01–FC04, IN01/IN02 |
| G04: data integrity | Missing, stale, duplicate, conflicting, nonfinite and out-of-window values have explicit rules; before/after counts and withheld-output reasons reconcile | UN01, UN04–UN06, FE05, XA05, BY02 |
| G05: numerical correctness | Independently check selected source-to-result calculations and aggregate identities with declared tolerances; inspect actual values | CO01, FE01/FE02, FC01/FC02, PO01/PO02, VA01 |
| G06: model validity | Target, horizon, feature recipe/order, scaling, sample sizes, fit cutoff, seed, diagnostics and baseline are explicit; claimed validation is genuinely held out | FE01, UN09, RE01–RE07, FC01–FC08, VA03 |
| G07: execution correctness | Entry/exit/sizing/clocks/costs match the specification; fills, holdings and account reconcile; simulation assumptions are visible | ST01–ST07, IN01/IN06, XA01/XA04/XA06, VA01 |
| G08: interpretation | Claims follow evidence; sample counts, uncertainty, contradictions, source gaps and methodological warnings survive narrative and charts | CO02, MA01–MA08, RE03/RE05, FC01/FC02, VA02/VA04 |
| G09: interaction and continuity | Material choices are settled once, amendments preserve the rest, correct artifacts are reused, outputs match the requested channel and failures remain inspectable | UX01–UX04, UX07, BY01 |
| G10: ownership and publication | Only authorized sources/models/results are read; source identity and publication revision are retained; shares disclose only intended content | BY01/BY02, RE02, UX05 |
| G11: scale and recovery | Required population/window is preserved; actual admission/latency/cost and unknowns are reported; interrupted/partial/failed work is not labelled complete or duplicated | UN01, UX04/UX06, VA05 |
| G12: presentation | Displayed values match source rows; dates, signs, percentages, currencies and denominators are correct; grouped identities and nulls survive edits | UX02/UX08, CO03, MA01, PO01 |

## Required counterexamples

Choose fixtures because they expose a consequence. A ticker/date becomes a
fixture only after verifying the needed property in the selected environment.
Use an authorized controlled fixture where vendor observations cannot reliably
exercise an error. Controlled fixtures do not prove vendor coverage.

| Counterexample | Required behaviour | First phase / home |
| --- | --- | --- |
| Zero selected names; one eligible name; all inputs missing | Explicit counts and conclusion; no automatic relaxation or fabricated rank | P1 / UN01 |
| Mixed histories and insufficient warmup | Eligibility changes visible; no warmup leakage or hidden missing cohort | P1 / TH01, FE01, FC01/FC02 |
| Index addition and removal | Gate membership before cross-sectional operations; current roster remains different | P1 / UN07 |
| Nonpositive earnings, zero denominator, conflicting financial periods | Withhold/qualify derived values with the actual reason; retain source facts | P1 / CO01, UN01 |
| Duplicate corporate-action delivery | No duplicate cash/splits; conflicts remain visible | P1 / CO03, VA01 |
| Revised macro print | Latest and vintage preserve different information sets and clocks | P1 / MA02 |
| Insufficient finite model rows or constant feature | Correct diagnosis; no invented successful fit or interval | P1 / FE01, RE01, FC01/FC02 |
| Fit overlaps holdout or follows the as-of date | Contract warning/rejection preserved; no causal held-out claim | P1 / RE02/RE03, FC01 |
| Retrospective smoother used in execution | May run with a warning; causal performance claim fails | P1 / RE03 |
| Decision versus fill bar; missing next bar | Correct delay; no synthetic execution | P1 / ST01, IN01 |
| Gap through stop; stop and target both touched | Correct source-owned precedence/path and account effect | P1 / ST02 |
| Session boundary; release without exact checkpoint bar | Calendar-aware boundary; no silent nearest-bar substitution | P1 / IN01/IN02 |
| Crypto weekend; FX inversion; futures contract transition | Asset-specific clocks, units and roll handling | P1 / XA01–XA03 |
| Unavailable saved result; uncertain launch | Preserve identity/uncertainty; no substitute result or uncontrolled duplicate run | P1 / BY01, UX04 |
| Unauthorized or expired source/share | Intelligible access failure without unrelated disclosure | P1 / UX05 |
| Over-limit universe/window/workload; partial vendor response | Explicit limit/gap and population accounting; no silent clipping | P1 / UX06 |
| Excluded/unimplemented request in a valid conversation | Explicit boundary; original need is not marked fulfilled | P1 / UX09 |
| Tied ranks, duplicate issuer listings, changing fiscal calendars | Deterministic policy, valid comparison and no double issuer allocation | P2 / UN03/UN05/UN08 |
| Singular covariance or infeasible weights | Supported solution or explicit failure; no silent constraint removal | P2 / PO02/PO03 |
| Incompatible saved-model options or feature recipe | Hard failure versus methodological warning distinguished; no silent retraining | P2 / FC07 |
| Refitted regime labels permute | No permanent raw-label meaning; supported mapping or limitation | P2 / RE04/RE05 |
| DST/half-day/extended-session mismatch | Native calendars and explicit unsupported cases | P2 / IN03 |
| Missing volume/price; partial priced-order fill | Correct expiry/cancel/fallback/protection; liquidity not invented | P2 / ST06 |
| Zero/negative commodity price; missing futures leg | Valid measurement or limitation; no selective episode deletion | P2 / XA04/XA05 |
| Retrospective market structure used as a live signal | Preserve annotation semantics; a fixed lag does not prove causality | P3 / IN05 |
| Large mixed-calendar basket with multiple models | Correct population, alignment, execution and recovery under measured load | P3 / IN06, VA05 |

Critical checks are never postponed when an earlier workflow exercises the
behaviour. A P1 fixture crossing DST must meet the DST checks now.

## Independent numerical review

- **Screens:** account for every member of the frozen population. Independently
  recompute representative pass/fail/boundary names, exclusions and ties; verify
  count reconciliation and monotonicity when only narrowing.
- **Fundamentals:** trace reported metrics and constructed ratios to compatible
  dated source rows. Check currency/share basis, capex signs, TTM periods and
  denominator policy using the vendor's declared definitions. Different source
  cash scopes do not establish a reconciliation failure. Never tune expected
  values to match the answer or invent a conversion to repair vendor values.
- **Time joins:** inspect before/at/after disclosure, release, membership and
  session boundaries. Appending later data must not revise supposedly causal
  earlier output.
- **Models:** freeze training/validation inputs. Independently check selected
  transforms, forecast-target alignment, baseline errors, sample counts, units
  and aggregate loss. Exact parity needs matching estimator settings and numeric
  tolerances; otherwise use documented invariants and a separately validated
  reference. Do not invent universal acceptable forecast errors.
- **Portfolios:** check weights, signs, constraints and eligible identities;
  inspect executed holdings separately from target weights.
- **Backtests:** hand-reconcile selected fills through positions, cash, costs,
  dividend cash, FX and multipliers into equity. Include losing trades, gap/exit
  boundaries and relevant corporate-action/roll events. Match benchmark basis.
- **Robustness:** reproduce summaries from actual parent execution/trial/simulation
  rows. Retain failed trials, selection history and dependence assumptions.
  Holdout results must not select the winner.
- **Presentation:** compare exact rows with chart/table values and narrative.
  A field name, chart title or presence assertion is not a numerical oracle.

Declare checks, reference calculations and tolerances before the run. Use
exhaustive population/identity checks where practical and risk-based numerical
sampling where full recomputation would duplicate the product. Report the
sampling limitation explicitly.

## Campaign records

Use the existing runner/review format. A ledger in `campaigns/<campaign>/` links
case results to attempts and independent reviews. Raw credentials and session
artifacts remain in ignored `.artifacts/`; do not publish them as proof.

| Required field | Meaning |
| --- | --- |
| Case / phase / depth | Stable ID, frozen release scope and required D1–D5 stages |
| Persona / need | Decision, constraints and original opening ask |
| Configuration | Universe, source versions, windows, cutoff, cadence, model/strategy settings, execution assumptions and output channel |
| Environment / revisions | Actual service endpoints/versions, binary/deployment provenance, AI/UI and catalogue/correction versions |
| Prerequisites / fixtures | Source access, parent artifacts and independently verified fixture properties; label controlled inputs |
| Expected evidence | Source/intermediate/result rows, model identities, fills/account and other necessary artifacts |
| Oracle / tolerances | Independent calculation, invariants, sampled rows and comparison tolerance |
| Attempt / outcome | Not run, running, pass, fail, blocked or accepted exclusion; separate boundary-behaviour outcome |
| Actual coverage | Exact methods/options, asset mechanics, macro pairings, depth stages and G01–G12 checks exercised |
| Findings / review | Failure type/severity, owner, independent reviewer identity, evidence references and unresolved checks |
| Runtime / cost | Measured wall time, observed token/cost/provider use and explicit unknowns; run budget and product latency target when defined |

Set campaign budgets and product response-time expectations before declaring
those criteria passing. Without a target, report measured performance with that
criterion still unassessed.

## Release gate

- [ ] Freeze release scope and P1 cases, including the success meaning for the
  all-US historical factor journey.
- [ ] Instantiate fixtures, expected artifacts and independent oracles; establish
  exact service/binary/catalogue versions and source access.
- [ ] Run every P1 positive workflow and assigned counterexample. Missing
  channels/data are blockers, not evidence of deployed support.
- [ ] Obtain independent review; resolve material numerical errors, look-ahead
  claims, access failures, silent cohort changes and false-success states.
- [ ] Record representative method/configuration coverage as well as families.
  Do not call untested alternatives certified.
- [ ] Verify complete flows through the intended UI and API/CLI channels;
  UI-only interactions require UI evidence.
- [ ] Match evidence to the actual release candidate. Changed contracts, source
  data or affected components trigger targeted reruns.
- [ ] Report passing, failed, blocked and untested scope separately. Owner
  release authorization remains a later decision.

This documentation creates no live runs, issues or shares. Future campaigns
follow the relevant tool and environment authorization for those actions.
