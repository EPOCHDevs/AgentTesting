# Migration

The first cut moves the research conversation workflow into this repository.
It does not change the agents or run a paid acceptance campaign.

| Existing location in EpochAI | AgentTesting destination | Treatment |
|---|---|---|
| `tools/agent_cli/supervisor/cli.py` and `swarm/driver.sh` | `tools/agent_testing.py` | Persistent threads, replies, continuation and capture through the official LangGraph client |
| `swarm/epoch_remote.sh`, `supervisor/dump_thread_trace.py` | `status` and `pull` commands | Explicit dev configuration; current checkpoint and interrupt IDs |
| `supervisor/session_metrics.py` | `tools/evidence.py` | Conservative deduplicated token counts; missing evidence remains unknown |
| Shared swarm run ledgers | One folder per attempt; `index` command | Independent writers, one read-only overview |
| Persona collections and historical results | Existing archives | Retain in place until selected, reviewed and suitable for public release |

Legacy EpochAI tools remain in place during adoption because its deterministic
release tests and historical campaign readers still import them. Removing them
now would break those callers. This repository is the entry point for new
research campaigns; a separate consumer migration must precede code removal.

Not carried over: automatic clarification choices, hardcoded production targets,
private database reconstruction, guessed model prices, historical PASS claims,
or application source imports. Authentication travels in HTTP headers only;
the server's authenticated user supplies Backend access. Tokens are not placed
in traced run configuration. Inner-agent evidence comes from live subgraph
streams when available; pulling a checkpoint later does not recreate that stream.
