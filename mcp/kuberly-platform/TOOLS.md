# kuberly-platform MCP — tool catalog

A flat metadata table of every tool the `kuberly-platform` MCP server exposes,
plus a hint at typical token cost. The orchestrator reads this when planning
("what's the cheapest way to answer X?"). Personas declare a tools-list
subset in their frontmatter so they don't pay the schema-load cost for
tools they will not use.

## Discovery / topology (cheap)

| tool | answers | typical out | who uses |
|---|---|---|---|
| `query_nodes` | "list nodes by type/env/name-substring" | dozens of rows | every persona |
| `get_node` | "metadata for one node id" | one row | iac-developer, plan-reviewer |
| `get_neighbors` | "what does X depend on / what depends on X" | dozens of rows | scope-planner, troubleshooter |
| `blast_radius` | "what breaks if I change X" | <100 rows + summary | scope-planner, plan-reviewer |
| `shortest_path` | "how is A connected to B" | <20 rows | rarely; surface from Explore |
| `stats` | "graph counts + critical nodes" | <50 rows | rarely; debugging only |

## Cross-env / drift (medium)

| tool | answers | typical out | who uses |
|---|---|---|---|
| `drift` | "what's missing/extra between envs" | <100 rows | scope-planner (drift-fix) |
| `apps_for_env` | "applications deployed to env X" | <50 rows | iac-developer (new-application) |

## Module/component internals (medium)

| tool | answers | typical out | who uses |
|---|---|---|---|
| `module_resources` | "resources a module declares" | dozens of rows | plan-reviewer |
| `module_variables` | "variables a module accepts" | dozens of rows | iac-developer |
| `component_inputs` | "inputs a component sets" | dozens of rows | iac-developer |
| `find_inputs` | "components matching an input filter" | <50 rows | rarely |
| `list_overrides` | "where input X is overridden" | <50 rows | rarely |

## Orchestration / session

| tool | answers | typical out | who uses |
|---|---|---|---|
| `plan_persona_fanout` | "build a session plan for this task" | structured plan | orchestrator only |
| `session_init` | "create a new session dir" | small | orchestrator only |
| `session_read` | "read a file from the session" | file content | every persona |
| `session_write` | "write a file into the session" | confirmation | one-per-persona |
| `session_list` | "list files in the session" | dozens of rows | every persona |
| `session_status` | "render the live fanout dashboard" | structured | orchestrator only |
| `session_set_status` | "mark a persona / phase status" | confirmation | orchestrator only |

## How the orchestrator should consult this catalog

1. Customer asks something (e.g. "how is X wired to Y?").
2. Orchestrator picks the **single cheapest** tool that answers it
   (`shortest_path` for that example).
3. If unsure, fall back to `query_nodes` first (cheapest, always loaded).
4. Persona dispatch: only include tools the persona's prompt could plausibly
   need — see each persona's `tools:` frontmatter for the curated subset.

## Token-cost rule of thumb

- Each MCP tool's schema in the system prompt costs ~80–200 tokens.
- A persona that loads 21 tools pays ~3000 tokens *before any work*.
- A persona that loads 5 tools pays ~800 tokens.
- That is why each persona's `tools:` list should be the minimum it uses,
  not "everything we might possibly need." The orchestrator can spawn an
  `Explore` subagent for one-off queries that fall outside the persona's
  curated tool set.
