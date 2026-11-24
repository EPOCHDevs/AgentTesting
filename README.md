# AgentTesting

**Current round:** [Fresh local scenarios](campaigns/product-local-20261123-round3/reports/dashboard.html) — [status table](campaigns/product-local-20261123-round3/reports/status.md). Previous rounds remain historical.


A small workspace for testing research agents through realistic, multi-turn
conversations and reviewing the evidence behind their results.

## Structure

| Folder | Purpose |
|---|---|
| `scenarios/` | Role briefs, opening questions, phased workflow coverage and known exclusions |
| `campaigns/` | Assignments, coverage and attempt records |
| `tools/` | Conversation and evidence helpers |
| `notebooks/` | Review views and examples |

## Workflow

Choose a scenario → have a conversation → collect evidence → review → record findings.

Use the [single campaign guide](scenarios/coverage/README.md) for the whole-product
process, agent handoffs and dashboard. [catalog.json](scenarios/coverage/catalog.json)
owns scenario definitions and criteria; campaign event logs own observations.
The existing conversation runner below still collects evidence. A completed run
is not automatically a passing journey.

CLI, API and direct UI interaction are possible ways to drive a conversation.
Notebooks are an optional way to run or review it.

## Choose a stack

Requires Python 3.12+ on Linux or macOS.

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
```

`environments.toml` contains `local`, `dev` and `prod` profiles. **Dev is the
default.** Select another explicitly with `--env` before the command:

```bash
.venv/bin/python -m tools.agent_testing --env local check
.venv/bin/python -m tools.agent_testing check                 # dev
.venv/bin/python -m tools.agent_testing --env prod status prod-review-01
```

Local defaults are Backend `localhost:9000`, AI `localhost:2024`, and UI
`localhost:3000`. To use different ports, copy the file to the ignored
`environments.local.toml`, edit that profile, and pass
`--config environments.local.toml --env local`. Selecting a profile does not
start or restart its services. HTTP is allowed for local; hosted profiles require HTTPS.
The former `dev.toml` is now `environments.toml`; existing custom `[dev]` files
still work when passed with `--config`.

Each attempt records its environment and exact service addresses. Use a new
attempt name when comparing environments; reconnecting under a different
profile is rejected before network access. Existing dev attempts remain readable.
The global index shows all recorded environments, and offline reports identify
the environment stored with the attempt.

## Start locally

The launcher uses the primary repositories beside AgentTesting:

| Directory | Branch |
|---|---|
| `EpochBackend/` | `main` |
| `EpochAI/` | `main` |
| `EpochClientPortal/` | `main` |
| `EpochPlatform/` | `dev` |

Before login or startup it checks those branches, rejects linked worktrees and
uncommitted tracked changes, and compares each checkout with its current remote
branch. Clean local commits ahead of that branch are allowed for testing before
push; behind, diverged or unverifiable checkouts are rejected. It prints the local
commits and records both local and remote revisions. It never switches or pulls
repositories automatically. Worktree/path overrides are not enabled.

Use the primary Backend's existing `build/bin/epoch_stratifyx_server`, the
primary EpochAI's `.venv`, and installed UI and Platform dependencies (Node 24+
with Corepack and the UI's pinned pnpm; `pnpm install` in EpochPlatform). Source
freshness does not certify a native executable's build revision; rebuild it from
that source through the normal Backend workflow. The launcher compiles Platform's
TypeScript in place (`tsc -b`, incremental; Git ignores the output).

Files and Space run on Epoch Platform, as in dev, QA and production. A front door
on the profile's Backend port (9000) applies the deployed load balancer's path
rules: `/api/v1/files*`, `/api/v1/space*`, the Files workers' `/internal/...`
routes and `/api/v1/platform/*` go to Platform (port 9020); everything else goes
to Backend (port 9010). The Portal, the agent and AgentTesting keep one API origin.
Platform runs its general service in local mode (`EPOCH_FILES_LOCAL_ROOT`): no AWS
account, with its objects and records in Backend's `personal_content` directory
(`.artifacts/local-stack/backend/personal_content`), where the engine's local job
store reads saved datasets, so a dataset saved locally is a local study input.
The front door also sends engine reference and discovery routes to Platform. Reference files come
from the primary engine's `epoch_reference_files` exporter, not another environment. Build that
target once alongside the server; the launcher exports a fresh immutable generation before startup
and Platform verifies its files against the exported manifest. New engine references require a stack
restart. The launcher still does not compile C++ or install dependencies.
Discovery optionally uses `EPOCH_DISCOVERY_GDS_URL` and `EPOCH_DISCOVERY_SERVICE_TOKEN`
from the primary `EpochPlatform/.env.local`. Supply both to use the shared GDS catalogue;
the launcher sends observation previews to the local engine and keeps selections on local disk.
Other settings in that file are not inherited. Keep the file private and untracked.
Asset metadata, logo selection and image routes also go to Platform. Configure
`EPOCH_IDENTITY_GDS_URL` and `EPOCH_IDENTITY_GDS_TOKEN` together in the same private
file to use GDS identity delivery. This credential is independent of discovery;
it stays on Platform and is never passed to Backend, the agent or the browser.
The launcher does not retrieve credentials or substitute external image URLs.
Studio connections use the same Cognito Authorization header as ordinary local agent requests;
the generated configuration requires custom authentication for Studio too.
Files uploaded through Backend's former local Files are not carried over. The
agent's Files workers use the front door with the shared worker token, which goes
to Platform, not Backend.

```bash
.venv/bin/python -m tools.local_stack
```

The agent server runs under `langgraph dev` on port 2024 by default (`local`
profile). That server shares one Python process, so concurrent runs wait on each
other. For parallel runs, start the licensed server instead:

```bash
.venv/bin/python -m tools.local_stack --ai-runtime up
```

This uses the `local-up` profile: the same Backend (9000) and Portal (3000) with
the agent on **2124** under `langgraph up` (Postgres, Redis queue, background
workers, custom auth). It needs Docker and a `LANGSMITH_API_KEY` with LangSmith
Deployment access plus the matching `LANGSMITH_ENDPOINT`; no Enterprise licence
key is used. The launcher builds `epoch-ai-up:<EpochAI commit>` from EpochAI's
own `langgraph.json` once per commit (log: `.artifacts/local-stack/ai-build.log`),
writes `langgraph-up.local.json`, `.env.up.local` (mode 600) and
`docker-compose.up.local.yml` in EpochAI's directory, keeps them out of `git status`
through `.git/info/exclude`, points the container at the host's Backend through
`host.docker.internal`, and mounts host directories named by the agent env
read-only at the same paths. Ctrl+C stops the services and runs
`docker compose down`; Postgres data stays in its volume. Use
`--env local-up` with `tools.agent_testing` for this stack.

`local-stack.toml` is optional and may set `node_bin`; see
`local-stack.example.toml`. Remove old worktree paths from an existing local
configuration. A missing primary build/dependency is an explicit error, never a
fallback to another checkout.

For launchers started with the supervised Portal-refresh capability, a later
qualified Portal code change can replace only the UI process:

```bash
.venv/bin/python -m tools.local_stack --refresh-ui \
  --ui-commit <qualified-full-commit> --ui-configuration <current-configuration-sha256>
```

Read the current configuration hash and active bundle from
`.artifacts/local-stack/ui-refresh-capability.json`. The primary Portal must be
clean, on its expected branch, and at that exact qualified commit. The owner
stages a separate immutable bundle and temporary loopback preview, checks the
served index and module asset hashes, revalidates the clean exact source before
publishing a build and immediately before stopping the old UI, then replaces
only its UI child. A build-time source drift leaves an unpublished partial, so
an explicit restored-source retry rebuilds it. A bind or
readiness failure rolls back to the retained bundle; Backend, AI, Platform and
the front door are not restarted. A failed acknowledgement write cannot repeat
the swap. If a command times out, reconcile the saved request/result instead of
submitting another.

An older already-running launcher needs **one coordinated restart** to enable
this capability; changing its Python files does not enable it in the existing
process. This first slice preserves the current proxy/configuration and pinned
preview dependency inputs. Changes to those inputs need a reviewed bootstrap.
Building never prunes an active or rollback bundle. The command sends no model,
provider, study or account operations and does not reload a person's browser.

An explicitly reviewed rollback may select previously qualified artifacts while
the primary checkouts remain at their current qualified revisions:

```bash
.venv/bin/python -m tools.local_stack --ai-runtime up --ui-runtime build \
  --retained-cut /absolute/path/manifest.json \
  --retained-cut-admission /absolute/path/review.json \
  --retained-cut-admission-sha <review-file-sha256>
```

This is a separate operator-selected mode, never an automatic fallback. The
normal clean, current integration-branch checks still run. The manifest pins
workspace revisions separately from the runtime revisions, the existing AI
image's full immutable ID, the original managed UI bundle and every file hash,
configuration/dependency inputs, primary Native binaries/reference, stack
identity and the original runtime qualification receipt. The independent
passing review binds the exact manifest hash. The files contain hashes and
paths, not environment credentials. Missing or changed artifacts/configuration
fail before service preparation; this mode never builds, pulls, resets source
or relabels an old artifact as new source. Readiness verifies the selected AI
source/reference and the served UI hashes. Its health receipt explicitly records
the retained runtime and current workspace separately.

Portal refresh is disabled in retained mode, and UI failure retains the original
all-service shutdown behavior. Leaving this mode requires a separately reviewed
normal bootstrap. A failed startup preflight never runs Compose cleanup against
an already-running stack; cleanup belongs only to a launcher that started AI.

Local Markets pages (Equities, Rates, Calendar, FX, Commodities and Crypto) are
**off by default**. The local stack uses the production vendor accounts, and
automatic page refresh can spend the shared Barchart hourly quota that
production pages and local research runs also need. To populate them, set
`market_pages = true` after building a Backend version that includes them. The primary Backend's
`.env.local` must set `EPOCH_CATALOG_ROOT` to an absolute path containing a selected
native catalogue generation (`current/catalog_manifest.json`). Backend validates
its hashes and provenance at startup; the launcher does not manufacture a generation
from an unversioned asset file. This enables native source
capture, page publication and automatic refresh using the configured vendors.
It updates only the generated `build/bin/config.json` Calendar settings and
supplies the Rates, FX, Commodities and Crypto settings to the process, with the
deployed environments' Equities source batch size (128); source configuration
stays unchanged.
Omitting `market_pages` (or setting it to `false`) disables automatic page work. Page preparation does not start LLM work;
opening a Rates context can run its declared text model, and previously accepted
context tasks can resume after a restart. Calendar V1 event rows remain read-only.

The launcher asks for your Epoch email/password on first use, starts Backend →
AI → UI, and checks authenticated readiness. Open `http://localhost:3000` and
sign in with the same account. The CLI and browser maintain separate sessions.
The local commentary deployment is identified by the verified AI commit, or by
`EPOCH_COMMENTARY_DEPLOYMENT_REVISION` in the AI environment when explicitly set.
It is recorded in `source-checkouts.json`; rotate an explicit revision when a
remote model deployment changes without a source change. Readiness checks the
commentary policy without making a model call.
Ctrl+C stops only the services started by this command. An occupied port or a
failed service stops startup; the launcher never substitutes a hosted service.

The password is not saved. Refreshable local CLI tokens are stored with private
permissions in `~/.config/epoch-agent-testing/local-login.json`; use `--login` to
change the account, or delete that file to forget it. `--env local` test commands
use this login automatically unless `EPOCH_TOKEN` is explicitly set.

A hosted stack whose Portal publishes `epoch-runtime-config.json` (QA today) has
its own login:

```bash
.venv/bin/python -m tools.agent_testing --env qa login
```

It reads the sign-in pool from that Portal and refuses when the Portal names a
different Backend, agent or assistant than `environments.toml`. Each login is
cached per Portal host, so one stack's login never authenticates another. A stack
without a published runtime config (dev and prod for now) needs its configured
token variable.

Service logs, AI state and Backend study records stay under
`.artifacts/local-stack/`. The generated AI configuration can contain service
keys and is private; do not publish this directory. Product source configuration
files are left unchanged. Backend listener addresses still follow its existing
`config.json`. The launcher does not build, install dependencies, submit research,
or deploy anything. To change ports, pass `--environments environments.local.toml`
and use that same file with the conversation CLI.

## Run a conversation

The runner checks AI readiness and its
reported Backend address before sending work. It records the reported server
versions for each submission. Selecting a stack does not deploy a product branch.

```bash
# Supply EPOCH_TOKEN through your existing Epoch login; do not commit it.
.venv/bin/python -m tools.agent_testing check
.venv/bin/python -m tools.agent_testing ask conversation-01 --key opening --owner journey-chat \
  --scenario CV01 "I'm comparing Fastenal and MSC Industrial for a long-term watchlist. Which business looks better able to withstand a slowdown in customer orders, and what makes you think so?"
.venv/bin/python -m tools.agent_testing pull conversation-01 --follow
.venv/bin/python -m tools.agent_testing status conversation-01
```

Read the returned answer before choosing a follow-up. Another `ask` with a new
`--key` continues the same thread. Repeating the same key and input reconnects;
changing its input is rejected. An uncertain submission is never silently retried.
A definite server rejection is recorded separately; correct its cause and submit
under a new key. Failed attempts remain available for review.

The CLI prints run identities, status and pending questions. Server-returned run
configuration stays out of terminal output because it can contain authentication
data. Raw diagnostic artifacts remain private under `.artifacts/`.

For a clarification, `status` shows the current interrupt IDs and payloads. Write
a local JSON file mapping the selected IDs to their explicit response payloads,
then use `answer conversation-01 --key clarification-1 --responses /path/to/answers.json`.
No option is selected automatically.

`pull` saves the server conversation; `pull --follow` also captures a resumable
live stream. Run it promptly: expired server streams cannot be reconstructed
from a later checkpoint. Stopping this local observer does not cancel the run.
Evidence lives in ignored `.artifacts/<attempt>/`. `metrics.json` distinguishes
observed token counts and server elapsed time from unknown model time/cost.

## Inspect deployed evidence

A conversation started in the UI can be attached without sending a message:

```bash
.venv/bin/python -m tools.agent_testing attach review-01 THREAD_ID --owner reviewer-a
.venv/bin/python -m tools.agent_testing pull review-01 --traces --executions
.venv/bin/python -m tools.agent_testing report review-01
```

For `--traces`, configure `LANGSMITH_ENDPOINT` and `LANGSMITH_API_KEY` in the
environment; `LANGSMITH_WORKSPACE_ID` is optional. A profile that names its
`langsmith_project` (local and qa) reads that project; set `LANGSMITH_PROJECT`
for the others. A `LANGSMITH_PROJECT` that contradicts the profile is refused.
A profile switch does not guess or change LangSmith credentials. Each profile's `token_env` can name
its own Epoch token variable when credentials differ. Never commit the keys.

The pull collects the thread and full run list, LangSmith trace trees, and Backend
records for the thread's accepted execution IDs. An explicit `--execution-id ID`
can add a known execution receipt. Each requested source is attempted separately.
Missing, expired or incomplete evidence is reported; a partial pull exits with
code 2 after saving what it obtained.

Run evidence replaces credentials in the server-owned authenticated-user,
runtime-context and legacy configurable-header fields with `[REDACTED]`.
LangSmith captures likewise redact legacy auth headers promoted into trace
metadata. Message/tool payloads, financial fields, identities and usage remain
unchanged. Backend execution records separately redact `artifact_source.signed_query`.
This is targeted transport redaction, not a scan of arbitrary text. Older files
are not rewritten in bulk; an already-running collector keeps its loaded code.

`report.md` shows agent usage, observed errors, span timing and reported cost, plus
Backend status, logs, configuration/HTTP/record events and native job timing where
those were retained. Raw records stay beside the report for debugging. Historical
streams are not a guarantee of every live event. LangSmith cost is a reported
estimate, not an invoice or Backend infrastructure bill; unknown cost stays unknown.

`report` and `index` work offline. Assign one owner per attempt and use distinct
attempt names for parallel research. Record a separate review explicitly:

```bash
.venv/bin/python -m tools.agent_testing review review-01 --reviewer reviewer-b \
  --outcome partial --finding "The comparison is incomplete." --evidence conversation.md
.venv/bin/python -m tools.agent_testing index > .artifacts/index.md
```

Reviews retain their history and never change the execution's status. Keep
numerical answer keys separate from the researcher brief. See the
[migration map](MIGRATION.md) for retained legacy code.

Only `ask` and `answer` initiate agent runs. Validation includes offline protocol
tests and read-only retrieval of existing hosted Backend executions. No new live
research acceptance is claimed; complete agent-trace validation needs an
accessible thread in the selected environment.
Keep credentials and raw session artifacts outside version control. Publish
reviewed examples only.

## Check study exports

`tools.export_e2e` exports one study run per shape the Terminal can export and
checks the result in headless Chrome. The fixtures are listed in
[`campaigns/export-e2e/catalog.json`](campaigns/export-e2e/catalog.json).

```bash
.venv/bin/python -m tools.export_e2e --env dev run                      # every fixture
.venv/bin/python -m tools.export_e2e --env dev run --only wide-table
.venv/bin/python -m tools.export_e2e --env dev prepare                  # pin or create runs only
.venv/bin/python -m tools.export_e2e --env dev inventory --refresh      # which runs fit which shape
.venv/bin/python -m tools.export_e2e recheck ATTEMPT                    # re-judge saved evidence offline
.venv/bin/python -m tools.export_e2e --env dev run --ui-url http://localhost:3002
```

`--ui-url` drives a Terminal on this machine, for example a Vite dev server of an
unmerged branch built against the dev API, with dev's Backend, sign-in and
fixtures. Only a loopback origin is accepted, because the browser is signed in
there. The attempt is named after the override and records it, so its results
aren't read as dev's.

It uses the installed Google Chrome (`channel="chrome"`); it never downloads a
browser. Sign in first with `tools.agent_testing --env dev login`. The browser
gets the cached ID and access tokens in localStorage, under the keys Amplify
reads, but never the refresh token.

For each fixture the suite opens the study page's Export menu, then the print
route, and waits for `data-export-ready`. It checks the rendered page against the
export JSON the page embeds: sections and blocks, chart images, the cover, the
file name, the 200-row limit, the usage-limit notice, and table cells at A4 and
Letter widths. It then makes A4 and Letter PDFs. Each format is a check in
`catalog.json`. A format is skipped while the Export menu doesn't offer it, and
fails when the menu offers it but the suite has no check for it yet.

It also asks Platform for each fixture's PDF made on the server
(`POST /api/v1/platform/exports`, A4), as the signed-in owner, through the API.
It reads the export every 2 s for up to 2 minutes, downloads a ready file
without the bearer, and keeps it as `server-export-a4.pdf` (`pdf-server`). It
then compares the file's section headings and table titles, in order, with the
browser-print PDF (`pdf-server-text`). A run without a dashboard must be
refused (422) or fail cleanly as `no_dashboard`. Once per attempt, on the first
dashboard checked, it cancels a queued export (`pdf-server-cancel`) and clicks
Export → PDF in the Terminal (`pdf-server-ui`). That check expects "Preparing
your PDF…" and then a download, or the print layout in a new tab when the server
refuses. A 404 (route not deployed), a 503 (unavailable) or the hourly limit
(429) is a skip, not a failure. The suite has one identity, so another owner's
read is skipped (`pdf-server-other-owner`). Signed download addresses are never
recorded. Platform allows 20 exports an hour per owner, and a full run asks for
about 15.

The suite reuses the owner's existing runs where it can. A shape that no
existing run has comes from a script in `campaigns/export-e2e/scripts`. Those
scripts are research-only, source-free and without `llm_call`, so Backend makes
no model call for them. The report text of backtests, Monte Carlo and tuning
runs is charged to the owner's AI allowance. The suite never creates a backtest,
and creates a Monte Carlo or tuning run only when the owner approves the cost:
`prepare --allow-ai-cost monte-carlo` (or `tuning`) makes one, and reads the
usage meters before the request and a minute after the run settles.
Pinned and created run ids are kept per environment in the git-ignored
`.artifacts/export-e2e/state-<env>.json`, so a rerun reuses them and never
makes duplicates.

Each attempt writes to `.artifacts/<attempt>/`: `report.md`, `summary.json`, and
per fixture the PDFs, a picture of page 1, the export data without its images,
and the raw observation. The command exits 1 when a check fails.
