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
digest and stale expected-digest mismatches, changed immutable producer bindings,
incompatible feature count or native numeric ABI, required model-option mismatches,
unsupported requested capabilities, same-name shadowing, interrupted Save, source
change/restart and captured dependencies. Those integrity failures must not start
replacement work. Differences in recorded feature recipes, declared units, cadence
and known feature ordering remain advisory; preserve authored order and values.
Native OLS tests cover invalid numeric and tuple-validity cases; those passes do
not certify public transport or UI.

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

`platform_owned.py` now prepares phased real API/SDK submissions against explicit
qualified private Native and Platform origins. `produce` checks the owned OLS
oracle, exact continuation, explicit Save and concurrent equal retries. Then stop
Native and restart Platform for `offline`; detach only the owned fixture's source
resources with retained backups while Native is stopped; restart Native before
`consume --remove-native-sources`. The launch receipts must prove changed service
PIDs. `save-selected` completes two fits, checks that neither was automatically
saved, then explicitly saves one through the ordinary Save API. ENG-3235 retires
`save_as` and the authored completion endpoint. These drivers are prepared; a
generated script is not a passing live run.
The intermediate Backend-owned `http_owned` Save entry point is retired; its
execution and Arrow helpers remain shared.

For the owned `produce` journey, `--drop-first-save-response` uses a real HTTP
transport, lets the actual Platform request finish successfully, then drops its
reply before the generated client receives a result. The next request reconciles
the same retained Save identity. This is explicit client-side fault injection,
not proof of interruption inside publication or cloud worker recovery. The copied
OLS artifact is also decoded read-only and its full-precision coefficients,
training count and baseline are checked against the independent fixture oracle;
this does not add a public fitted-parameter API.

`family_http.py` separately prepares HMM, ARIMA and GARCH replay/Save consumption
checks. Each family needs its own actual evidence. They do not establish missing
PCA, scaler, clustering or fitted-parameter output capabilities.

After a passed API `final-receipt.json`, `notebook.py` generates an explicit
scoring-only notebook. Use `--kernel-name` for a private kernel backed by the exact
qualified installed SDK environment. Running it admits one new scoring execution,
checks all rows and zero fit effects, and retains Python executable, SDK versions
and module paths. It never trains, saves or reconstructs a model. Do not overwrite
an existing notebook or attempt directory after an uncertain outcome.

None of these engineering drivers establish a campaign verdict, complete browser
journey, agent-mediated reuse or direct-S3 independence by themselves. The owned
native runtime boundary is qualified separately below.

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

## Controlling service ownership and required isolation evidence

The owner corrected the unmerged application boundary on 24 November: Platform
owns saved resources and accesses their S3 objects directly. Saved-model Save,
catalogue, metadata, search and script reads must not depend on Backend HTTP.
Backend owns native computation and artifacts still held by a server. A narrow
server-held export is distinct from ordinary cloud-backed reads.

Qualify the pending `RE04-cloud-resource-independence` variant with Backend HTTP
unavailable to Platform and measured outbound calls, not only a transport double.
Native execution must publish a versioned fit/producer descriptor with the exact
sealed-config digest, public producer snapshot and verified fit roster as part of
the completed generation. Platform reads those immutable resources, pins/copies
the chosen artifact and provenance, and owns intent/ready/catalogue state.

The separate `RE04-server-artifact-export` variant qualifies the exceptional
server-held transfer, unknown-response retry and subsequent server shutdown.
Neither case is passing merely because it is registered here. Source removal
is restricted to fixture-owned material and happens only after evidence/pins are
retained. Missing historical descriptors stay explicit.

The earlier `http_owned.py` receipts qualify an intermediate Backend-hosted
application implementation that has been withdrawn. Preserve their numerical,
identity and telemetry evidence; do not present them as final Platform ownership
acceptance. Update the transport adapter against the compatible Platform contract
before using this driver for final lifecycle qualification.

## Native provenance output through the actual API

`native_provenance.py` qualifies only the native output slice. Run it against an
explicit private task server/store, using a new evidence directory and the normal
signed test login. Choose `--dispatch inprocess` and `--dispatch subprocess` in
separate runs. It creates synthetic owned producer/continuation definitions,
checks every declared prediction/baseline/score, and verifies raw `model_fits.json`
bytes against the completion manifest, exact config, pinned script and native
artifact digest. Retained complete log streams must show one producer fit and
zero continuation fits; fitting must leave the native saved-name catalogue empty.

The 25 November private task receipts pass in both modes under
`.artifacts/model-lifecycle-native-20261125/owned-provenance-{inprocess,subprocess}-1/`.
The native source checkpoint is Backend `be5467e01b`. This is an engineering
qualification helper, not a campaign grade or Platform Save/AI/Portal acceptance.
Refer to the tracker and native validation receipt for the precise binary scope;
a later schema-3 compatibility guard received its own focused regression gates.

## Platform-owned executable journey

The intermediate Backend-hosted `http_owned.py` Save entry point is retired. Its
execution/Arrow helpers remain for native provenance checks; historical receipts
keep their original scope. New saves use the generated `epoch_sdk.models_api`
client against Platform. Native is used for computation and only the explicit
server-held export capability, never as a cloud application catalogue.

`platform_owned.py` takes explicit absolute fixture/evidence/token/root paths and
separate loopback Native/Platform origins. It never launches a service, changes
the shared stack or calls an LLM/vendor. Use a fresh evidence directory per
producer attempt and preserve failed directories. Numerical execution UUIDs and
Save request identities are written before submission; stop on unknown outcomes.

- `produce`: owned OLS fit/holdout, exact continuation, full rational oracle,
  retained one-fit/zero-refit audit, exact Platform Save and eight equal concurrent
  retries, copied-byte identity and zero Save numerical executions.
- `offline`: after the operator stops only the recorded private Native process
  and actually restarts private Platform, prove list/read/equal Save retry from
  independent copies with the Native socket closed. Preserve both service launch
  receipts and the changed Platform PID; the driver does not manufacture a restart.
- `detach`: while the recorded private Native is still stopped, rename its original
  model payloads and producer execution into retained evidence backups. The root
  must be the exact sibling `native-export-server` fixture and match its launch
  receipt. Original resources are preserved; the primary checkout is excluded.
- `consume`: after the operator restarts the qualified private Native, reuse the
  exact saved reference and manifest pin and compare every prediction/baseline/
  score. `--remove-native-sources` requires the prior explicit detach receipt; it never
  renames sources beneath a running Native server or infers a cold start. Record
  the actual changed Native PID after detaching and restarting.
- `save-selected`: a separate producer with two temporary fits. Two first Save
  requests select the same fit with different request IDs/names concurrently;
  both must return one saved model. A later fresh-ID/name Save returns the same
  identity and original name. The unselected fit stays temporary. The explicit
  `--save-second-fit` variant then saves the other fit and requires two distinct
  saved identities. Save creates no numerical execution. This private API
  scenario does not establish cloud, browser or agent acceptance.

`family_http.py` runs each actually implemented immutable-artifact family (HMM,
ARIMA, GARCH) separately. `families.py` supplies a deterministic owned 200-row
relation, a 160-row training block and a disjoint 40-row observed block. Each
family checks full Arrow pages, exact continuation/saved-copy replay, all-row input
identity, family invariants and one producer fit/zero consumer fits. ARIMA(0,1,0)
also has an independent closed-form drift/prediction oracle. This is not an
independent HMM/GARCH estimation oracle, forecast/parameter-table qualification,
AI/Portal acceptance or direct-S3 proof. PCA/scalers/clustering remain explicit
unsupported saved-artifact paths until native advertises and implements them.

No driver result is a campaign grade. The campaign ledger must link the actual
service/package/source receipts and keep the API/SDK, conversation, browser,
direct-S3 and family-capability verdicts separate.

`notebook.py` creates (and does not execute) a separate saved-model scoring
notebook from a passed API receipt. An explicit Run admits one new scoring-only
execution through the installed native SDK, reads the exact Platform model pins,
checks every owned prediction/baseline/score, and requires retained zero-fit
telemetry. It stops if its evidence directory already exists, preserving unknown
outcomes rather than resubmitting. The executed notebook and its receipt are a
separate channel verdict, not an AI/Portal/cloud acceptance claim.

`forecast_oracle.py` is a read-only engineering oracle for the next checkpoint
forecast cases. It requires an exact owned artifact digest, owner and original
producer before reading the versioned Native ARIMA/GARCH payload. It retains
full-precision fitted parameters, terminal histories, checkpoint matrices and
optimizer restart evidence. It does not estimate a model, reconstruct missing
state, call a service or add a public parameter-table interface.

The ARIMA oracle independently propagates the retained linear state and
covariance and computes normal prediction intervals. It currently accepts the
integrated state-space path; the scheduled fixture is ARIMA(0,1,0) with drift.
The GARCH oracle uses the normal GARCH(1,1) terminal squared residual and variance
and checks the conditional variance recurrence and its square-root volatility.
Both helpers bound horizons to128 for this qualification harness; that bound is
not the public operation's maximum. The frozen scenario uses1,2,5,21.

Synthetic byte/math controls and successful parsing of an authentic retained
checkpoint establish decoder compatibility only. A future-forecast verdict still
requires actual `arima_forecast_from_fit`/`garch_forecast_from_fit` outputs from the
same selected digest and cutoff, comparison of every scalar against the oracle,
and retained zero-fit/effect evidence. Observed40-row `*_infer` replay cannot
stand in for that gate. HMM remains separately qualified by its explicit causal
versus retrospective operation; no HMM forecast oracle is supplied here.

The v6 HMM execution bound is **two temporary consumers, one saved consumer and
one post-cleanup consumer**. Only the temporary stage runs the base/suffix causal
pair. Saved and post-cleanup stages each run the unchanged observed source once
and compare all40 input/probability rows with the original temporary base result.
They do not repeat the intervention or replace a missing baseline with a new run.

For those two HMM replay stages, the root-issued case must include three ordinary
`{path, sha256}` references: `hmm_reference_receipt`, `hmm_reference_answers` and
`hmm_reference_config`. The receipt must be the accepted temporary pair, with the
same producer/digest/fit and zero-fit counts. Its first contract witness must match
the original base config's owner, exact binding, causal mode and capability. The
answers must contain the complete unchanged40-row fixture. Missing, changed or
misbound references stop before credential or SDK access; preserve the reference
files before source cleanup. This is replay equivalence and causal filtering
evidence, not independent HMM estimation or a future state-forecast oracle.

ARIMA/GARCH stages remain separate empty-input checkpoint forecast executions.
Each uses all four horizons1/2/5/21 and compares every scalar with the independent
retained-state oracle. Their observed40-row `*_infer` source cannot satisfy this
future-forecast gate. Source-only preparation and synthetic codec/math controls
must remain explicitly unbound until real producer/saved identities, copied bytes,
runtime and operation admissions exist.

`family_consumers.py` wires the next actual cases, without producing, saving or
cleaning up a fit. Each invocation is one explicit temporary, saved or
post-cleanup stage. ARIMA/GARCH use empty-input checkpoint operations at1,2,5,21,
retain four declared SQL/Arrow rows, check all scalar values against the oracle,
and require zero-fit telemetry and the actual compiled checkpoint contracts.
The checkpoint launch omits an application period. Existing observed drivers
keep their previous explicit period. HMM instead runs two consumers of the same
fit with the same first five observations and different suffixes, verifies the
full probability rows and checks sealed direct/effective `ObservedCausal` mode.

The driver is inactive without an actual root-issued case admission and explicit
`--execute`. A case admission has kind
`ROOT_ADMITTED_MODEL_LIFECYCLE_CONSUMER_V1`, `ready=true` and exact source/origin,
storage-root, owner, producer/fit/digest/config/source/cutoff and selected saved
pins. It references the actual descriptor and primary runtime packet by absolute
path and SHA-256. The referenced runtime packet has kind
`ROOT_ADMITTED_PRIMARY_MODEL_LIFECYCLE_RUNTIME_V1`, `ready=true`, and matching
`native_url`, `platform_url`, `native_root`, `native_source`, `owner_user_id`.
Root must derive these records from the actual started/served primary stack;
source qualification or this schema description cannot act as readiness.

The descriptor's actual storage location is preserved. Shared storage also needs
an exact completed-generation witness; post-cleanup needs the actual root-admitted
supported cleanup witness. The oracle artifact bytes must match the same digest
and native manifest. The actual selected temporary or saved metadata is read
before a consumer is launched. The driver retains every execution UUID before
its write and does not resubmit unknown outcomes or an existing attempt.

Credentials come only from the root-pinned maintained primary
`EpochAI/tools/cognito_auth.py` `get_id_token()` callable. The driver keeps its
opaque result in memory, inspects no JWT/profile/groups/cache, and stops if the
provider returns None. Expected owner comes from the actual owned producer and
artifact joins. There is no token-file, login, private identity or registry
fallback. The local driver does not claim remote/deployed-IAM, natural AI,
Portal, source-cleanup execution or campaign acceptance.

`owned_primary.py` is the actual primary OLS producer/continuation/explicit
Save/saved-consumer entry point. It requires the same actual root readiness
packet, an explicit expected owner and `--execute`. It uses only the pinned
opaque provider, joins that expected owner to the actual new producer/artifact
and saved facts, and checks full owned numerical rows and retained1/0/0 fits.
It snapshots the existing bounded saved catalogue instead of requiring it to
be empty. It does not use private launch-PID files, stop/restart shared services,
detach source directories or claim source-cleanup/S3 independence. Those require
their separately admitted stages and witnesses.

The original private `platform_owned` and `family_http` entry points now also
require explicit expected-owner/root-readiness inputs and use that opaque
credential boundary. Their older private restart/detach assumptions remain
restricted; historical receipt paths are not permission to apply them to the
primary stack. Generated notebooks consume a newly passed admitted-primary API
receipt, recheck its fixture/resource pins and use the same opaque provider.
Existing executed notebooks and all older receipts remain unchanged.

The optional lost-response fixture deliberately observes one real successful
Save response and withholds it only from its fixture client. It first reconciles
that observed identity with an ordinary read before any known equal retry.
Genuinely unknown real outcomes stop all writes. This is not a shared proxy,
host failure injection, worker-recovery test or automatic request retry loop.

## Explicit DEV-cloud family consumers

`family_consumers` keeps its loopback default. DEV requires both `--mode dev-cloud`
and a hash-pinned root admission with `mode: "dev-cloud"`. Origins must be exactly
`https://api.dev.epoch.trade` and `https://services.dev.epoch.trade`: no alternate
hosts, ports, paths, redirects, localhost/cloud mixtures or server-export bridge.
This is an engineering harness, not a new product surface or a campaign verdict.

Root supplies the existing consumer admission fields plus:

- A `ROOT_ADMITTED_DEV_MODEL_LIFECYCLE_RUNTIME_V1` packet, `ready: true`, matching
  native/platform origins, full source commits, expected owner, `native_root: null`,
  and `runtime_receipts.native` / `.platform` ordinary-file references with hashes.
- `platform_source`, a canonical UUID `case_id`, `producer_definition_id` and
  `consumer_execution_ids`, and one `consumer_source_sha256` per reserved execution.
  Producer/owner/config/source/digest/cutoff joins remain mandatory. The generation
  witness must additionally be root-admitted and Completed, pin the producer
  definition/config and match the actual shared generation. No relabelled local fit.
- `acceptance_plan` referencing the existing v6 bytes, SHA-256
  `c99882765cc724c111809ad6cfa7cff109fc1da7d55d90986b17729db7068d28`,
  `harness_sources` exactly from `family_cloud.source_hashes()` (including the
  shared `tools/executions.py` metadata retention policy), and `request_plan`
  exactly from `family_cloud.stage_plan(family, stage)`. Reference shape is
  `{"path": "/absolute/ordinary/file", "sha256": "<64 lowercase hex>"}`.
- Existing selected artifact bytes and the unchanged maintained opaque credential
  provider. No token decoding, cache/profile inspection or credential fallback.
  Saved HMM stages also require the original temporary pair's finalized receipt,
  base answers and actual config, as in the loopback mode.

None of these are auto-generated live admissions. Root assigns and reviews actual
identities, complete bodies, runtime/readiness evidence and effect bounds first.
`--plan` checks references/source joins and prints caps without credentials or
requests. `--execute` alone activates the pinned stage and creates an exclusive
`.spent` fence beside its admission before credentials or requests. Keep that
fence and every interrupted/failed attempt; changing the output directory must
not resubmit an admission. Unknown outcomes stop writes, with no automatic retry.

| Consumer stage | Executions | Compile / definition / execute POSTs | Arrow read POSTs | GET maximum |
| --- | ---: | --- | ---: | ---: |
| HMM temporary base + changed suffix | 2 | 2 / 2 / 2 | 6 | 175 |
| HMM saved or post-cleanup | 1 | 1 / 1 / 1 | 3 | 90 |
| ARIMA/GARCH temporary, saved or post-cleanup checkpoint | 1 | 1 / 1 / 1 | 1 | 90 |

Per invocation GETs comprise health 1, inventory before/after 2, exact selection
before/after 2; per execution, at most 60 terminal polls, 20 log pages of 100,
and one each of as-run study, historical lifecycle, empty-fit roster, outputs
and bound response. These are ceilings, not read quotas. Every actual request
is retained before forwarding. Ordinary bounded replies retain unchanged
application bytes. Native execution-metadata GETs use the existing narrow
`artifact_source.signed_query` redaction **before any body retention**, including
nonterminal polls and the terminal-job copy. Malformed, error or partial execution
bodies are omitted; only status, sizes and complete/prefix hashes are retained.
Metadata labels the actual encoded wire-body hash separately from the retained
redacted-body hash. There is no generic JSON/string scanning or redaction of
model facts, source or Arrow payloads.
Only exact generated source/write bodies and actual bound table/page reads can
be POSTed. Normal API errors stop the attempt, including redirects. There are
zero Save, export, S3, provider/vendor, restart and cleanup calls.

The cloud mode checks completed job/definition identity, the exact as-run source,
all canonical log pages, a unique completed historical terminal, empty fit
roster, accepted response/version/request identity, complete Arrow rows and the
existing numerical oracles. HMM still uses two temporary consumers and one saved
replay; ARIMA/GARCH still use four empty-input scalar horizons 1/2/5/21 from the
same checkpoint, with no authored application period.

**Sealed contracts are a separate mandatory gate.** Public compile nodes and the
as-run study projection do not expose the actual sealed node contracts. A cloud
execution therefore writes `pending-receipt.json` with
`PENDING_ACTUAL_SEALED_CONFIG_CAPTURE`, never a passing `receipt.json`. It neither
uses a local Backend path nor automatically exports a tree/downloads S3 bytes.
Root must first qualify a supported capture's exact fixture, membership/size/path
bounds and secret/grant handling. The owner-authenticated full-tree export route
is a source lead for root's review, not an automatic capture fallback here.

Offline `--finalize /absolute/capture.json --finalize-sha256 HASH` requires a
hash-pinned `ROOT_ADMITTED_DEV_MODEL_LIFECYCLE_CONFIG_CAPTURE_V1` with `ready: true`,
the original admission/runtime-packet hashes and case ID, the exact pending
receipt reference and ordered `consumers`. Each consumer pins an actual config
reference and a separate root-admitted `capture_read_receipt`, joining owner,
execution, actual definition, Completed status, nonempty generation, config SHA
and source SHA. The same `retained_contract` checker then proves the sealed
owner/digest/origin/capability/horizon/causal stamps and zero fit-capable nodes.
Finalization writes a new `receipt.json`; the pending evidence remains unchanged.
It makes no credentials, network or numerical calls. Failed/missing captures
remain pending. HMM saved replay requires this finalized temporary evidence.

Cloud post-cleanup admission additionally requires actual witnessed removal of
both the source execution and temporary artifact, plus a hash-pinned supported
operation receipt. A generation 404 or `sources: null` is insufficient. There is
currently no qualified case-scoped artifact-removal operator; this remains a
genuine pending gate. The harness adds no owner-wide GC or manual S3 workaround.
Real cross-owner testing still requires a second ordinary root-supplied identity;
these positive owned consumers do not certify it.

### Remaining producer and family stage plans

`family_cloud.producer_request_plan(family, ROOT_RESERVED_EXECUTION_UUID,
ROOT_SELECTED_FIT_NAME)` is source-only: it emits `SOURCE_PLAN_NOT_ADMITTED`,
the exact source hash and three proposed write bodies (compile, create definition,
execute), with the actual returned definition ID substituted only after creation.
It cannot invoke a producer. Its 89-GET ceiling comprises health 1, inventory 2,
terminal polls 60, log pages 20, historical lifecycle 1, fit roster 1, exact fit 1,
outputs 1, bound response 1 and as-run study 1. Forty observed rows require three
Arrow read POSTs at page size 17. Actual descriptor/artifact/checkpoint capture,
Save/race checks and every live stage require separate root admission.

Keep the same 200 synthetic rows, first 160 training / next 40 observed, seed 42,
HMM two states, ARIMA(0,1,0) with constant and GARCH(1,1) normal. Per v6, HMM is
one producer plus four zero-fit consumers (temporary base/suffix, saved,
post-cleanup). ARIMA/GARCH each allow one producer plus four zero-fit consumers:
observed continuation, temporary checkpoint horizons, saved horizons, post-cleanup
horizons. The separate observed ARIMA/GARCH cloud continuation is still a planned
stage; this mode runs checkpoint consumers, not an extra observed execution.
Do not reuse the all-in-one local `family_http.journey` against DEV or add an
unplanned producer to obtain a convenient fit. API controls/preparation are not
future-output, product-chat, SPY or whole-family acceptance.
