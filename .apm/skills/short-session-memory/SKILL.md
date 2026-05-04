---
name: short-session-memory
description: >-
  Use short (in-chat) memory for the active debugging or edit loop; persist durable facts to git,
  OpenSpec, or tickets — pair with Caveman for terse updates.
---

# Short session memory (debug / edit loop)

Use this skill when you are **iterating quickly** (debugging, multi-step refactors, long plan outputs) and need to stay **correct without bloating the thread**.

## What “short memory” is

- **Short memory** = the **current conversation** plus whatever you **re-read from disk** this turn (grep, file slices, `terragrunt plan` excerpts).
- It is **not** a substitute for **git**, **OpenSpec**, **`AGENTS.md`**, or org **skills** — those are **durable** context the next session (or teammate) will load.

## Keep in the thread (ephemeral)

- **Hypotheses** you are about to confirm or discard.
- **Scratch paths**: “trying `ecs_app` with `APPLICATION_NAME=foo` next”.
- **Truncated** command output (errors, one module diff) — not full multi-hundred-line dumps unless needed once.
- **Working set**: file paths you touched this session.

## Promote to durable (repo / tracker)

Do **not** rely on the model “remembering” across a new chat:

| Durable home | When |
|--------------|------|
| **Code + JSON** | The actual fix or config change |
| **OpenSpec** (`openspec/changes/…`) | Behavioral or cross-cutting infra intent |
| **PR description** | Problem, approach, risks, how you tested (**`git-pr-templates`**, **`infra-change-git-pr-workflow`**) |
| **Issue / comment** | Blockers, decisions that outlive the branch |

## Loop that works

1. **Observe** — smallest failing signal (error line, one resource address).
2. **Locate** — grep or read **one** module / JSON; use **`kuberly-stack-context`** and **`components-vs-applications`** so you open the right tree.
3. **Change** — minimal diff; run **`pre-commit-infra-mandatory`**.
4. **Summarize in-thread** — 3–6 bullets: what was wrong, what changed, what is still unknown.
5. If unknowns remain **after** two tight iterations — **write them down** (OpenSpec delta, ticket, or PR “open questions”).

## Token discipline

- Prefer **Caveman** (repo-packaged) for **short replies** when the user did not ask for a long essay.
- Prefer **links and paths** over pasting large unchanged blocks.

## Related skills

- **`kuberly-stack-context`** — repo map, plan-only, OpenSpec.
- **`components-vs-applications`** — `components/` vs `applications/` and RAG-oriented doc hints.
- **`pre-commit-infra-mandatory`** — hook loop after edits.
