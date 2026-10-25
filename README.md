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

`index` prints the global table without network access. Assign one owner per
attempt and use different attempt names for parallel research. A reviewer may
add `review.json` inside an attempt with `outcome`, `finding` and `evidence` fields;
the runner never grades its own result. Keep numerical answer keys separate from
the researcher brief. See [migration scope](MIGRATION.md) for retained legacy code.

Only `ask` and `answer` initiate agent runs. Current implementation validation is
offline plus hosted health checks; no live research acceptance is claimed.
Keep credentials and raw session artifacts outside version control. Publish
reviewed examples only.
