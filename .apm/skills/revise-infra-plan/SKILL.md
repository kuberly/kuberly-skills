---
name: revise-infra-plan
description: >-
  Interview-style plan revision for kuberly-stack infra work. Resolves the design tree branch by
  branch — target envs, clouds, OpenSpec change, IAM, shared-infra blast — explores the codebase or
  graph before asking, and keeps the plan document internally consistent. Sub-flow of
  agent-orchestrator; can also be invoked directly to harden a draft infra plan.
---

# Revise infra plan — interview workflow

Use this skill to **interview the user relentlessly** about every aspect of an infra plan until both of you reach a shared understanding. Walk down each branch of the design tree, resolving dependencies between decisions one-by-one.

This is a **sub-flow of `agent-orchestrator`**. It can also stand alone when a user hands you a half-baked infra plan and says "tighten this up before we touch any module".

## Before asking anything

If a question can be answered by **the kuberly-platform MCP** or by **reading existing code/docs**, do that first — do not ask. Specifically:

1. `mcp__kuberly-platform__query_nodes` for "what envs / components / modules exist".
2. `mcp__kuberly-platform__get_neighbors` for "what does X depend on".
3. `mcp__kuberly-platform__blast_radius` for "what breaks if I change X".
4. `mcp__kuberly-platform__drift` for "what's different between env A and env B".
5. `Read` on `AGENTS.md`, `INFRASTRUCTURE_CONFIGURATION_GUIDE.md`, `MODULE_CONVENTIONS.md`, `ARCHITECTURE.md`, the OpenSpec change folder, and the specific module / component JSON in scope.

If invoked from `agent-orchestrator`, delegate the lookup to an **Explore subagent** — do not run greps yourself.

## How to ask

Ask **one question at a time**, and for each question:

- **What caused it** — the inconsistency or open variable in the current plan or codebase.
- **Existing evidence** — file paths + line numbers, graph MCP excerpts, OpenSpec deltas, prior decisions.
- **Implications** — what each likely answer locks in or rules out downstream.
- **Recommended answer + why** — your best guess, citing the evidence.
- **Be concise** — short paragraphs, not essays.

Use the harness's question tool (Claude Code: `AskUserQuestion`; Cursor/Codex: `Question`).

## Infra-specific question dimensions

Walk these branches in roughly this order. Skip a branch if it's already settled in `context.md`.

1. **Target environments.** Which of `anton`, `dev`, `stage`, `prod`, `gcp-dev`, `azure-dev`? Single-env or rolling? If rolling, what's the order and gating between rings?
2. **Cloud provider.** AWS / GCP / Azure? Multi-cloud parity required?
3. **Module vs component vs application.** Is this a change in `clouds/<cloud>/modules/<m>/` (Terraform/HCL), `components/<cluster>/<comp>.json` (cluster wiring), or `applications/<env>/<app>.json` (CUE-rendered K8s app)? Mixed scopes must be split.
4. **Shared-infra blast.** Does the change touch `components/<cluster>/shared-infra.json`? If yes, the blast radius covers many components in that cluster — confirm the user understands.
5. **OpenSpec change.** Name (kebab-case)? New `/opsx:propose` or extend an existing `openspec/changes/<name>/`? Confirm the path before any implementation prompt is written.
6. **IAM / auth.** `KUBERLY_ROLE` from `components/<cluster>/shared-infra.json`. Confirm `aws sts get-caller-identity` will succeed before plan; flag if `assume-role` is needed and pre-checked.
7. **Verification scope.** Which clusters need `terragrunt run plan`? Which modules? Capture them so the Verify subagent has an exact list.
8. **Drift intent.** If the change is meant to **converge** envs, run `drift` first and record what is actually missing where; do not assume.
9. **Reversibility.** Plan-only is enforced by policy, but flag any change whose plan output would be hard to roll back manually (e.g. resource replacement, identity changes, KMS key rotation).
10. **Owner / review path.** Who approves the OpenSpec? Who merges? (Optional — skip if the user has standing answers.)

## Plan-document hygiene

When you edit the plan or `context.md`:

- **Record discoveries** only when they are **externally grounded facts** from code, graph MCP, or existing docs. Plan-internal inconsistencies are **not** discoveries — fix them inline.
- **Record decisions** only when you and the user explicitly decide architecture direction. Recommendations the user has not confirmed are not decisions.
- Preserve the most concrete, settled contract over vague earlier wording.
- If two settled sections conflict, **stop and ask** which is authoritative.
- Do not infer ownership / scope changes unless code, docs, or the user's answer clearly support them.
- Keep the Discovery and Decision log consistent with the body of the plan.
- Remove or rewrite stale text that could cause future misinterpretation.
- Keep the entire plan document consistent at each edit — partial updates that contradict each other are worse than no update.

## Exit criteria

Stop interviewing when:

- All ten dimensions above are either resolved or explicitly marked out-of-scope in `context.md`.
- No section of the plan contradicts another.
- Verification commands (which clusters, which modules) are concrete enough to paste into a Verify prompt.
- The OpenSpec change path is recorded.

Then return control to the orchestrator (or, if invoked directly, summarize the resolved plan and ask the user whether to proceed to implementation).

## Related

- **`agent-orchestrator`** — parent flow.
- **`infra-self-review`** — sibling flow that runs after implementation.
- **`kuberly-gitops-execution-model`** — branch ↔ many-clusters mental model that shapes "target environments" answers.
- **`detect-runtime-from-shared-infra`** — for the EKS / ECS / Lambda branch of the tree.
