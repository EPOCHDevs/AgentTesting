# AgentTesting

A small workspace for testing research agents through realistic, multi-turn
conversations and reviewing the evidence behind their results.

## Structure

| Folder | Purpose |
|---|---|
| `scenarios/` | Realistic role briefs, opening questions and known exclusions |
| `campaigns/` | Assignments, coverage and attempt records |
| `tools/` | Conversation and evidence helpers |
| `notebooks/` | Review views and examples |

## Workflow

Choose a scenario → have a conversation → collect evidence → review → record findings.

Start with the [research situations](scenarios/README.md). The researcher reacts
to actual answers; features exercised are recorded afterward. A separate reviewer checks the result.
Each attempt owns its records; a shared index can summarize them without competing
writes. A completed conversation is not automatically a passing result.

CLI, API and direct UI interaction are possible ways to drive a conversation.
Notebooks are an optional way to run or review it.

## Use the shared dev stack

Requires Python 3.12+ on Linux or macOS.

`dev.toml` binds the Backend, AI and UI. The runner checks AI readiness and its
reported Backend address before sending work. It records the reported server
versions for each submission. Connecting to dev does not deploy a product branch.

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
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
Choose the deployed agent's tracing project explicitly. Never commit the key.

The pull collects the thread and full run list, LangSmith trace trees, and Backend
records for the thread's accepted execution IDs. An explicit `--execution-id ID`
can add a known execution receipt. Each requested source is attempted separately.
Missing, expired or incomplete evidence is reported; a partial pull exits with
code 2 after saving what it obtained.

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
accessible shared-dev thread.
Keep credentials and raw session artifacts outside version control. Publish
reviewed examples only.
