# AgentTesting

A small workspace for testing research agents through realistic, multi-turn
conversations and reviewing the evidence behind their results.

## Structure

| Folder | Purpose |
|---|---|
| `scenarios/` | Personas, research questions and acceptance checks |
| `campaigns/` | Assignments, coverage and attempt records |
| `tools/` | Conversation and evidence helpers |
| `notebooks/` | Review views and examples |

## Workflow

Choose a scenario → have a conversation → collect evidence → review → record findings.

The researcher reacts to actual answers. A separate reviewer checks the result.
Each attempt owns its records; a shared index can summarize them without competing
writes. A completed conversation is not automatically a passing result.

CLI, API and direct UI interaction are possible ways to drive a conversation.
Notebooks are an optional way to run or review it.

This repository currently contains only the starting structure. Existing runners
and research collections will be reviewed before migration. Keep credentials and
raw session artifacts outside version control; publish reviewed examples only.
