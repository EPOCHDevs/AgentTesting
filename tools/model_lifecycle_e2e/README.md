# RE04 model lifecycle integration

Tracking: [ENG-3218](https://linear.app/epoch-inc/issue/ENG-3218),
[ENG-3217](https://linear.app/epoch-inc/issue/ENG-3217) and
[ENG-2755](https://linear.app/epoch-inc/issue/ENG-2755).

This directory begins the controlled fixture/oracle for **fit → holdout evaluation
→ exact continuation → explicit Save → Space discovery → fresh-chat reuse**.
It is not a completed integration runner or a passing campaign. The repository's
`scenarios/coverage/README.md` and campaign ledger own acceptance and verdicts.
Do not change frozen campaign plans or overwrite original attempt evidence.
Named variants are linked from RE04 in the single catalogue and registered as
[RE04L](../../scenarios/pending/model-lifecycle.json) until the compatible target
and a new campaign are qualified. There is no new passing verdict.

## Owned fixture and numerical oracle

`fixtures/owned-observed-predictor.csv` is a deliberately small, synthetic owned relation.
The rows supply one precomputed observed predictor and a contemporaneous target.
The coordinates are fixture dates; they do not claim an exchange session calendar,
market prices, corporate-action adjustments or a real SPY vendor observation.
No scaling, recursive forecasting or future target is part of this recipe.

- Training: 24 rows in January 2022.
- Holdout: 8 supplied rows in January 2024, with no fitting on their targets.
- Continuation: 6 supplied rows in January 2025, using the original coefficients.
- OLS includes an intercept and uses exactly the joint finite X/Y training rows.
- The baseline is the full-precision mean of Y on those same eligible rows.
- Evaluation pairs each finite prediction with its actual target; both models use
  exactly that paired scoring sample. Invalid X produces a missing prediction.
- Preserve row order and duplicate coordinates. Coordinate membership and complete
  input bytes have separate hashes; neither hash substitutes for the other.

The standard-library oracle uses exact rational arithmetic on the decimal CSV,
independently of the native numerical library and the agent's output. Run it:

```bash
python3 -m tools.model_lifecycle_e2e.oracle \
  tools/model_lifecycle_e2e/fixtures/owned-observed-predictor.csv
python3 -m unittest tests.test_model_lifecycle_oracle -v
```

Use numerical tolerance `atol=1e-12`, `rtol=1e-10` for native floating results.
Resource identities, artifact bytes/digests and row coordinates remain exact.
All pages/rows must be compared; a headline MSE alone cannot establish correctness.

## Required live receipt

The future live driver must retain, in the selected campaign attempt:

1. Actual repository/package/service identities, explicit origins and disposable
   fixture owner/account. Do not replace the default local stack with a task tree.
2. Owned input bytes/hash, exact authored and compiled source, selected training
   rows and complete numerical results against this independent oracle.
3. Producer execution, local fit name, original digest and retained baseline.
   Fitting alone must leave the saved catalogue unchanged.
4. Continuation's exact selected origin/digest, predictions and measured effects:
   one scoring execution and zero additional fits. Missing telemetry is unknown.
5. Explicit Save's exact selected fit and immutable saved identity: zero numerical
   executions/refits. Equal retries/concurrent requests preserve that identity.
6. Actual Space browse/reload/folder and fresh-chat tool traces. Opening/selecting
   has zero AI/fit/execution effects; explicit reuse consumes the saved digest.
7. Independent retained-evidence review, with separate native/API-SDK,
   conversational and Portal UI verdicts.

Also qualify multiple-fit selection, wrong-owner/missing/corrupt references,
option/feature incompatibility, same-name shadowing, interrupted Save, source
change/restart and captured dependencies. Native OLS tests cover invalid numeric
and tuple-validity cases; those passes do not certify public transport or UI.

After this deterministic slice passes, run a new pinned-data SPY journey with the
original natural follow-up. Preserve the original ENG-3217 attempt. Its historical
SQL aggregate did not declare reusable coefficients: never recover them from
rounded prose or silently refit the old run. HMM, ARIMA/GARCH, clustering, PCA and
scalers need their own supported numerical/lifecycle qualification; an OLS pass
cannot certify them.

## Native retained-state audit

`native_artifact.py` can inspect a disposable owned OLS artifact selected by its
exact API-reported digest, fixture owner and original producer execution. It
checks the complete byte hash, known envelope/payload version, original identity,
coefficient dimensions, finite binary64 values and joint training count. Its
`assert_fixture_state` compares those retained coefficients and baseline with
the independent exact-rational oracle.

This is **native artifact evidence**, not a public fitted-parameter API or a
substitute for SDK, conversational or browser acceptance. The unit fixture is a
synthetic codec example; only an actual produced artifact establishes native
interoperability. Read only the declared disposable fixture path. Do not inspect
unrelated users' artifacts, reconstruct missing fits, or print private sealed
configurations. Full SDK result pages and measured effects remain separate
required evidence.

```bash
python3 -m unittest tests.test_model_lifecycle_oracle \
  tests.test_model_lifecycle_native_artifact -v
```

## Concrete owned study source

`source.py` emits the synthetic relation as source-free SQL, promotes its
explicit UTC timestamps into native Series, fits only the disjoint training
window and declares complete `predictions` and `scores` answer tables. The
continuation and saved-model stages contain no fit operation. The first fixture
requires unique ascending UTC-midnight coordinates; duplicate-row cases remain
separately qualified rather than silently altered.

```bash
python3 -m tools.model_lifecycle_e2e.source \
  tools/model_lifecycle_e2e/fixtures/owned-observed-predictor.csv --stage fit
python3 -m tools.model_lifecycle_e2e.source \
  tools/model_lifecycle_e2e/fixtures/owned-observed-predictor.csv \
  --stage continue --producer-execution ORIGINAL_EXECUTION
```

The `native_compile_probe.cpp` engineering harness compiles the fit/holdout
source with an explicitly configured **current Backend core** and checks for one
OLS producer, one consumer and a valid sealed plan. Its metadata directories
must resolve to that build's matching package/compiler/DataSDK bundle. The
24 November local probe passed with 14 nodes, one fit and one consumer. This is
**compiler evidence only**: the probe does not execute the graph, contact a
service, save a model or pass a numerical/API/SDK/UI acceptance gate. It is not a
new product CLI or a replacement for Backend's managed build tools.

Public SDK submissions remain to be implemented/qualified against the completed
service cut. Do not infer a public service pass from generated scripts or use
placeholder producer IDs as actual public model references. The owned native
runtime boundary is qualified separately below.

## Owned native graph execution

`native_owned_runtime.cpp` is an engineering harness against the current Backend
compiler, ordinary graph orchestrator and durable file model store. It rejects
every external source request. Supply three generated sources: fit with local
name `owned_fixture`, continuation from `owned-fit-run`, and saved consumption
from `owned_saved`. The evidence root must be a new absolute directory; the
harness leaves evidence in place and never adopts or deletes an existing tree.
Run it in a managed memory scope, with the matching metadata bundle and an exact
Backend build base plus dirty-source witness. It is not a product command.

The 24 November run retained real fitted bytes, all eight holdout rows, all six
continuation rows and complete score answers. The independent audit passed for
coefficients, baseline and every row. Synchronous native event capture measured
one completed fit in the producer and zero fit events in continuation/saved
consumption. The saved store was reopened before fresh consumption and retained
the original exact digest. The existing feature-provenance-unknown warning is
preserved for the source-free fixture; this evidence does not certify arbitrary
SQL predictors as equivalent or point-in-time available.

```bash
python3 -m tools.model_lifecycle_e2e.native_results \
  /absolute/path/to/owned-native-evidence \
  tools/model_lifecycle_e2e/fixtures/owned-observed-predictor.csv
```

This qualifies only the owned native graph/file-store/event boundary. The
post-completion HTTP Save receipt/provenance, generated clients, Platform/Space,
conversation and Portal still require separate actual integration evidence.
No campaign grade, remote deployment or SPY-market acceptance follows from it.
