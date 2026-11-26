# Product acceptance: one process

**Review:** open the campaign's `reports/dashboard.html`. It shows scenarios,
agent folders, three assessment bars and related error reports. It is an offline
snapshot, regenerated after each ledger update. Reload the view to see updates.
`report` also refreshes it without starting services or model work.

**Definitions:** [catalog.json](catalog.json) is the single source for **50
journeys, 10 workstreams, 36 checks and 37 assignment slots**. Old results are not
imported. Detailed conditions appear under each dashboard row. Old scenario IDs
are provenance only; they do not grant passing coverage for untested variants.

Future-feature scenarios live in [the pending registry](../pending/index.json).
They are excluded from active campaigns and stay pending their linked issues.

## Cloud-only expectations

RS01's `cloud_only_expectations` flag covers PDF, Markdown and HTML study exports.
Run those download checks against a deployed cloud API and a cloud-published run
with saved outputs. A local Portal connected to that API is eligible. Against a
local Backend (`local`/`local-up`), verify that Export is disabled and exclude the
three download checks; continue checking the result table and chart locally.
Do not count those exclusions as export passes or local regressions. Export
failures must show an error, with no browser-print fallback. Existing campaigns
keep their frozen catalogues and evidence: record this owner-approved environment
boundary in the next plan/review rather than rewriting historical results.

## API Playground, cURL and Python notebooks

The **AP** family, owned by `journey-api`, covers the Playground and its copied
examples. Definitions and expected outcomes remain in `catalog.json`; this is
the execution protocol, not a second results ledger. Existing campaign snapshots
keep their frozen case sets. Start a new campaign to select AP01–AP08.

For each AP case, freeze the same intended request, context, output declarations
and fixture for **Playground**, **generated cURL**, and **generated Python in a
real notebook kernel**. Record the outcome for each surface separately; one
working client cannot pass all three. Freeze exact origins, Portal/API/SDK/native
revisions, Python/kernel/import identities and the authorized budget first.
Distinct intentional submissions normally have distinct operation IDs; compare
contract semantics and pinned values rather than IDs or model wording.

Run the copied example, rather than a hand-written equivalent. The notebook
must retain executed cell inputs, outputs, execution order, package versions and
the exact research/answer/version references. A code preview, import-only check
or unexecuted `.ipynb` does not pass. Record any setup, supported local-origin
binding or returned-ID placeholder substitution; repairing imports, API calls or
request semantics to get a green run is a finding against the copied example.
Splitting documented Ask/Run/Read blocks into cells is allowed. Separate read
cells may inspect the retained typed error, schema and all pinned result rows.

Retain redacted cURL request/response and event captures, the executed notebook,
browser interactions and screenshots, independent result calculations and native
admission/effect counts as appropriate. Keep keys/tokens out of source, notebook
outputs and shared reports. Re-executing a create cell or **Run All** can create
new paid work; it is not a read-only reconnect. Only a declared supported fixed
idempotency key may establish replay. Other criterion reviewers reuse the case
lead's evidence instead of submitting their own duplicate runs.

The public Playground is currently gated. `/dev/api-playground` and
`?failure=clarification` are labelled simulations: they can establish controlled
rendering/actions, never live API, discovery, notebook or native completion. The
one-shot migration also requires compatible Backend, Platform, SDK and AI
targets. Record missing readiness as blocked; do not enable the feature, replace
the default stack, or mark the migration accepted from this scenario addition.
Retain the observed phone-width shell clipping as a UI gap until verified fixed.
Discovery-disable controls remain future work under ENG-3188.

Creating a campaign is offline and does not run these cases:

```bash
.venv/bin/python -m tools.campaign init api-playground-local-20261123 --env local-up \
  --case AP01 --case AP02 --case AP03 --case AP04 \
  --case AP05 --case AP06 --case AP07 --case AP08
.venv/bin/python -m tools.campaign handoff api-playground-local-20261123 --agent journey-api --case AP01
```

Use a new campaign ID if that one exists. Readiness/account gates, a frozen plan,
explicit fixture/budget authority and independent review still apply.

## MCP workflow and response shapes

**MC01–MC14**, owned by `journey-mcp`, cover useful tasks, interaction/output
shapes and failure boundaries. Use a few small owned fixtures with existing
independent numerical oracles. RE/RB retain broad research-method and strategy
coverage; MCP acceptance must still check the actual returned values and claims.

Run the selected shape variants through three separate drivers:

| Driver | Evidence it establishes |
| --- | --- |
| Protocol harness | Actual MCP initialize/list/call behavior, argument/response schemas, forwarding and controlled boundary failures |
| Claude Code | A fresh installed client can choose the tools, obtain real human decisions and complete or explain the task |
| Codex | The equivalent fresh-client journey, with its own tool calls, decisions and result interpretation |

Keep separate campaigns per driver so a later protocol or host pass cannot hide
another driver's failure. Freeze `driver`, client and model versions/settings,
actual MCP endpoint, server/build identity, tool-manifest digest, fixture,
representative variants and budgets in each attempt's plan/evidence. Do not run
an exhaustive cross-product by default. Declare the required representative
variants before execution; an untested variant does not inherit a pass.

For real client acceptance, use short natural goals without supplying the tool
sequence or hidden oracle. Let genuine questions determine the next human reply.
Retain the raw question/choice and actual human answer; choosing a recommendation
on the user's behalf does not pass. Scripted protocol fixtures are labelled as
such and cannot establish this client behavior. Judge requested output and
side effects, rather than identical model wording or one prescribed call order.

Retain credential-redacted MCP calls/results, research/question/execution and
response/version references, complete table pages, observed human interactions,
bounded polling/retry/effect counts, timing and measured usage. Record unknown
costs and partial/truncated output explicitly. Use `deployed_journey` for actual
installed-client acceptance on a verified deployed target, with evidence of the
real client interaction. A CLI transcript is not a browser screenshot;
protocol-only attempts concretely exclude human UI checks and never pass them
from tool JSON. Genuine UI evidence and independent review remain required for
the corresponding claims.

Distinguish transport/JSON-RPC errors, MCP `isError`, asynchronous
`research.status`, native execution and required-output delivery. A successful
tool response can contain failed research. Usage-limit refusals must not trigger
retry loops or credential switching. Read/poll calls and embedded instructions
inside returned source data cannot grant authority to start new work.

At authoring, both installed `epoch-dev` registrations selected the hosted dev
server. Capture the actual endpoint again at readiness: a local client does not
prove a local service target, and a dev name does not prove the latest code is
deployed. New one-shot/mode-boundary acceptance requires the compatible service
release described in AP07. This catalogue addition deploys nothing and claims no
new live MCP acceptance.

The current `tools.agent_testing ask/answer` path talks to the conversation
service, bypassing MCP. It can support evidence collection where appropriate;
it must not be presented as the MCP driver. These are scenario definitions, not
an implemented MCP orchestration runner. Drive the declared real client/harness
and attach its evidence to the campaign ledger.

An offline example selecting a small first slice for each driver:

```bash
for driver in protocol claude codex; do
  .venv/bin/python -m tools.campaign init "mcp-dev-${driver}-20261124" --env dev \
    --case MC01 --case MC02 --case MC03 --case MC04 --case MC05 --case MC11
done
.venv/bin/python -m tools.campaign handoff mcp-dev-codex-20261124 --agent journey-mcp --case MC03
```

Use new campaign IDs when needed. The commands only define work: target/account
readiness, exact shape selection, actual session bindings and an authorized
call/time/cost budget precede any client invocation or paid research.

## The loop

1. Coordinator verifies the local stack and intended account/cleanup, then records
   the two readiness gates with evidence. Do not start scenarios before that.
2. Generate a handoff for each chosen agent/case. Bind its actual chat/session.
3. Case lead freezes the plan: exact target/design/fixture/budget and a target
   evidence mode or concrete exclusion for every check. It owns fixture writes.
4. Use the existing runner to ask, capture and report. Other criterion agents
   assess that same attempt; they do not relaunch it.
5. Agents record each check and any finding. A different actual reviewer records
   the verdict. Corrected work gets a new attempt; failed evidence stays visible.
6. Regenerate the dashboard/CSVs and review failures, blockers and handoffs.

## Commands

Run from `AgentTesting`.
All `tools.campaign` commands are offline. The existing runner retains its own
network/authorization rules; only its `ask` and `answer` initiate model runs.

```bash
.venv/bin/python -m tools.campaign init product-local-20261122 --env local-up
.venv/bin/python -m tools.campaign handoff product-local-20261122 --agent journey-results --case RS04
.venv/bin/python -m tools.campaign report product-local-20261122
```

`init` refuses to overwrite a campaign. Use a new campaign ID for a different
catalogue or environment; each campaign freezes the catalogue and its hash.
The generated handoff includes the claim, plan and report commands for that case.
Before any live work, the coordinator binds its session and records readiness:

```bash
.venv/bin/python -m tools.campaign claim product-local-20261122 --agent campaign-coordinator --thread ACTUAL_SESSION_ID
.venv/bin/python -m tools.campaign gate product-local-20261122 --agent campaign-coordinator --name target_ready --status ready --evidence TARGET_RECEIPT --note 'Exact local targets ready'
.venv/bin/python -m tools.campaign gate product-local-20261122 --agent campaign-coordinator --name account_ready --status ready --evidence CLEANUP_RECEIPT --note 'Verified intended account; cleanup complete'
```

For a check, retain the real runner report/capture, then record its observation:

```bash
.venv/bin/python -m tools.campaign record product-local-20261122 --agent ui-u12 --case RS04 --attempt ATTEMPT_ID --criterion U12 --status fail --level captured_browser --evidence EVIDENCE_FILE --note 'Observed behavior'
.venv/bin/python -m tools.campaign finding product-local-20261122 --agent ui-u12 --case RS04 --attempt ATTEMPT_ID --criterion U12 --key duplicate-reconnect --evidence EVIDENCE_FILE --note 'Expected X; observed Y'
```

Add `--issue ENG-1234` only for an actual linked issue. The tool does not create
issues or labels. Use `[case][criterion][agent_id]` in issue titles and preserve
reporter/session attribution. Match existing symptoms/contracts before filing a
duplicate. A shared key groups reports; it is not proof of a shared cause.

## Status and evidence

| Status | Meaning |
| --- | --- |
| not run / unassigned | No observation / no actual agent binding |
| running | Work or a declared check is in progress |
| blocked | A named prerequisite is missing |
| fail | An observed required check failed |
| awaiting review | Required checks pass; independent acceptance is pending |
| pass | All required checks plus current independent review pass |
| stale evidence | A referenced proof file changed or disappeared |
| not applicable | Explicit exclusion in the frozen plan, with a reason |

Research correctness, UI/UX and coordination are separate verdicts. No averaging.
Screenshots prove appearance, native receipts prove effects, and source CI proves
its tested scope. None automatically supplies the others. Unknown cost/effects
stay unknown. Vendor data/basis are authoritative; audit Epoch's use of them.

`agent_testing pull` preserves the previous and new collector views under
`.artifacts/<attempt>/collection-snapshots/`. Its returned `collection_snapshot`
manifest maps source names to immutable content objects. Use those object paths
when freezing review evidence; top-level thread, transcript, status and trace
files are current views and can change on another pull. Snapshotting cannot
recover files overwritten before this retention was introduced.

## Files and ownership

```text
scenarios/coverage/catalog.json       definitions only
campaigns/<campaign>/campaign.json   environment, selected cases, frozen hash
campaigns/<campaign>/catalog.json    immutable campaign snapshot
campaigns/<campaign>/agents/<id>/events.jsonl  that agent's append-only records
campaigns/<campaign>/reports/         generated dashboard, JSON, Markdown, CSVs
.artifacts/<attempt>/                existing runner's private captures/report
```

Each criterion has `agent_id`; case leads own the full attempt. One actual agent
may fill compatible slots. Independent review requires another actual session,
not a second label. Reassignment uses `claim --handoff 'reason'` and keeps old
bindings. Event logs are hash chained and briefly locked across concurrent writes.
Evidence files are referenced by path/hash; the dashboard never copies raw traces.

Keep credentials out of notes and published files. Controlled/scripted fixtures
must be labelled. Unsupported paths and historical limitations stay explicit.
The tracker does not deploy, mutate fixtures, cancel work or submit research.

## Natural conversation acceptance

Future human-facing acceptance uses short natural openings and replies, normally
one or two lines at a time. Let actual questions drive the next reply; keep
technical contracts, IDs and numeric oracles private unless the real human
supplied them. Do not insert arbitrary portfolio caps or other test thresholds
into the user's question; those require an established user mandate. Select
existing resources through ordinary product context where supported.
Earlier operator-directed diagnostics remain diagnostic evidence,
not natural-conversation acceptance. Real-user witnesses live separately under
`../user-witnesses/`; they do not alter a frozen campaign catalogue.
