# AgentTesting

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

Start with the [research situations](scenarios/README.md). The researcher reacts
to actual answers; features exercised are recorded afterward. A separate reviewer checks the result.
Plan campaign breadth and depth with the [workflow coverage programme](scenarios/coverage/README.md),
including release prerequisites, meaningful variants and evidence requirements.
Reviewers follow the [audit scope](AGENTS.md). Each attempt owns its records;
a shared index can summarize them without competing
writes. A completed conversation is not automatically a passing result.

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
| `EpochAI/` | `dev` |
| `EpochClientPortal/` | `dev` |

Before login or startup it checks those branches, rejects linked worktrees and
uncommitted tracked changes, and compares each checkout with its current remote
branch. Clean local commits ahead of that branch are allowed for testing before
push; behind, diverged or unverifiable checkouts are rejected. It prints the local
commits and records both local and remote revisions. It never switches or pulls
repositories automatically. Worktree/path overrides are not enabled.

Use the primary Backend's existing `build/bin/epoch_stratifyx_server`, the
primary EpochAI's `.venv`, and installed UI dependencies (Node 24+ with Corepack
and the UI's pinned pnpm). Source freshness does not certify a native executable's
build revision; rebuild it from that source through the normal Backend workflow.

```bash
.venv/bin/python -m tools.local_stack
```

`local-stack.toml` is optional and may set `node_bin`; see
`local-stack.example.toml`. Remove old worktree paths from an existing local
configuration. A missing primary build/dependency is an explicit error, never a
fallback to another checkout.

To populate the local Rates and Calendar pages, set `market_pages = true` after
building a Backend version that includes Calendar. The primary Backend's
`.env.local` must set `EPOCH_CATALOG_ROOT` to an absolute path containing a selected
native catalogue generation (`current/catalog_manifest.json`). Backend validates
its hashes and provenance at startup; the launcher does not manufacture a generation
from an unversioned asset file. This enables native source
capture, page publication and automatic refresh using the configured vendors.
It updates only the generated `build/bin/config.json` Calendar settings and
supplies the Rates settings to the process; source configuration stays unchanged.
Set `market_pages = false` to disable automatic page work. Omitting it leaves
the existing runtime settings alone. Page preparation does not start LLM work;
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
use this login automatically unless `EPOCH_TOKEN` is explicitly set. Dev/prod
still require their configured token variable.

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
.venv/bin/python -m tools.agent_testing ask industrial-01 --key opening --owner researcher-a \
  --scenario industrial-resilience "I'm comparing Fastenal and MSC Industrial for a long-term watchlist. Which business looks better able to withstand a slowdown in customer orders, and what makes you think so?"
.venv/bin/python -m tools.agent_testing pull industrial-01 --follow
.venv/bin/python -m tools.agent_testing status industrial-01
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
then use `answer industrial-01 --key clarification-1 --responses /path/to/answers.json`.
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

For `--traces`, configure `LANGSMITH_ENDPOINT`, `LANGSMITH_PROJECT` and
`LANGSMITH_API_KEY` in the environment; `LANGSMITH_WORKSPACE_ID` is optional.
Choose the selected stack's tracing project explicitly. A profile switch does
not guess or change LangSmith credentials. Each profile's `token_env` can name
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
