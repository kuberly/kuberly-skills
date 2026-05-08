# Multi-agent sessions — `.agents/prompts/<session>/`

Filesystem-based shared memory for the orchestrator + persona subagents that run on top of a kuberly-stack repo.

## Quick start

```bash
# Create a new session
python3 scripts/init_agent_session.py init my-task \
    --task "Add observability stack to anton cluster" \
    --node component:anton/kuberly-eks-stack

# When done
python3 scripts/init_agent_session.py cleanup my-task

# List active sessions
python3 scripts/init_agent_session.py list
```

If you don't have a local copy of `init_agent_session.py`, run it from the apm cache:

```bash
python3 apm_modules/kuberly/kuberly-skills/scripts/init_agent_session.py init my-task
```

## Personas and the file each writes

| Persona | Defined in | Writes |
|---------|------------|--------|
| **agent-planner** | `.claude/agents/agent-planner.md` | `scope.md` |
| **agent-infra-ops** | `.claude/agents/agent-infra-ops.md` | repo files (no markdown write) |
| **agent-sre** | `.claude/agents/agent-sre.md` | `diagnosis.md` |
| **agent-cicd** | `.claude/agents/agent-cicd.md` | repo files in infra repo or customer app repo (no markdown write) |
| **pr-reviewer-in-context** | `.claude/agents/pr-reviewer-in-context.md` | `findings/in-context.md` |
| **pr-reviewer-cold** | `.claude/agents/pr-reviewer-cold.md` | `findings/cold.md` |
| **findings-reconciler** | `.claude/agents/findings-reconciler.md` | `findings/reconciled.md` |
| **orchestrator (you)** | `agent-orchestrator` skill | `context.md`, `decisions.md`, `tasks/<NN>-<slug>.md` |

**Read rule:** every persona reads every file in the session dir.
**Write rule:** every persona writes only its own assigned file (or, for `agent-infra-ops` / `agent-cicd`, repo files).
**Exception:** `pr-reviewer-cold` deliberately does **not** read `context.md`, `scope.md`, `decisions.md`, `plan.md`, or sibling findings — it must look at the diff cold.

## Standard files

```
<session>/
├── context.md           orchestrator-managed; goal, graph snapshot, constraints
├── scope.md             planner output
├── decisions.md         orchestrator's irreversible calls + reasons
├── plan.md              revise-infra-plan output (optional)
├── diagnosis.md         agent-sre output (optional)
├── findings/
│   ├── in-context.md
│   ├── cold.md
│   └── reconciled.md
└── tasks/
    ├── 01-<slug>.md     orchestrator-prepared prompts for agent-infra-ops
    └── 02-<slug>.md
```

## Why this exists

- **Subagents can't message each other directly** — they all return to the parent. The filesystem is the inter-agent message bus.
- **Parallel fan-out is cheap** — orchestrator launches multiple personas in one message; each writes its own file; reconciler merges. No locking needed since each persona has a unique write target.
- **Audit trail** — every session leaves a transcript the next session (or a reviewing human) can read.

## Distribution and sync

The persona definitions live in this repo at `agents/<name>.md`. APM ships them inside `apm_modules/kuberly/kuberly-skills/agents/` after `apm install`. The consumer repo runs `scripts/sync_agents.sh` (also shipped here) to copy them into `.claude/agents/<name>.md` where Claude Code reads subagent definitions.

The kuberly-stack `ensure-apm-skills` pre-commit hook calls `sync_agents.sh` automatically after `apm install`. Other forks should add the same call.

## Lifecycle

- `init_agent_session.py init <name>` at session start.
- Orchestrator writes `context.md` with the task and a graph snapshot.
- Orchestrator dispatches personas via `Agent({subagent_type: "<persona>", prompt: ...})`.
- Personas write their assigned files.
- Orchestrator reads outputs, writes `decisions.md`, dispatches the next round.
- `init_agent_session.py cleanup <name>` once the PR is open and the session is complete.

The `.agents/` directory is **gitignored**; sessions are ephemeral. The protocol is documented here and in the `agent-orchestrator` skill.

## MCP orchestration

`kuberly-platform` can create and manage this session layout directly:

```bash
kuberly-platform call orchestrate --args '{"goal":"backend is slow in dev","environment":"main"}'
kuberly-platform call orchestrate_status --args '{"session_id":"<session>"}'
kuberly-platform call collect_agent_results --args '{"session_id":"<session>"}'
```

The MCP does not spawn subagents by itself. It writes graph evidence, routing,
and per-agent task prompts into the session directory so the parent OpenCode or
Claude process can dispatch personas safely, including parallel phases.
