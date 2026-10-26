# Migration

The first cut moves the research conversation workflow into this repository.
It does not change the agents or run a paid acceptance campaign.

| Existing location in EpochAI | AgentTesting destination | Treatment |
|---|---|---|
| `tools/agent_cli/supervisor/cli.py` and `swarm/driver.sh` | `tools/agent_testing.py` | Persistent threads, replies, continuation and capture through the official LangGraph client |
| `swarm/epoch_remote.sh`, `supervisor/dump_thread_trace.py` | `attach`, `status` and `pull` | Explicit local/dev/prod profiles; existing UI threads, current checkpoint and paginated run history |
| `replay/tools/pull_swarm_traces.py` and duplicate batch pullers | `tools/traces.py` | Exact thread/project trace trees through the official LangSmith client |
| `supervisor/session_metrics.py`, trace audit scripts | `tools/evidence.py`, `report` | Deduplicated agent/tool usage, errors, native timestamps and reported cost; missing evidence stays unknown |
| Ad hoc execution-log and job-data pullers | `tools/executions.py` | Authenticated execution records, paginated logs and bounded historical SSE; no private storage access |
| Shared swarm run ledgers | One folder per attempt; `index` command | Independent writers, one read-only overview |
| Handwritten review rollups | `review` and local evidence links | Explicit reviewer findings with retained history; no automatic grade |
| Persona collections and historical results | Existing archives | Retain in place until selected, reviewed and suitable for public release |

Legacy EpochAI tools remain in place during adoption because its deterministic
release tests and historical campaign readers still import them. Removing them
now would break those callers. This repository is the entry point for new
research campaigns; a separate consumer migration must precede code removal.

Not carried over: automatic clarification choices, implicit production targeting,
private database reconstruction, guessed model prices, historical PASS claims,
or application source imports. Browser interaction remains available for actual
UI checks; old browser-login and screenshot automation is not copied.
Authentication travels in HTTP headers only;
the server's authenticated user supplies Backend access. Tokens are not placed
in traced run configuration. Inner-agent evidence comes from live subgraph
streams or saved LangSmith traces when available; pulling a checkpoint later
does not recreate lost inner events. Backend diagnostics use the deployed HTTP
contract; missing retained history remains visible. No benchmark corpus or old
private traces have been published.
