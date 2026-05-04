---
name: infra-self-review
description: >-
  Post-change review loop for kuberly-stack infra: runs pre-commit, tflint, tofu fmt, and
  terragrunt run plan via a Verify subagent, then launches in-context and out-of-context Review
  subagents in parallel, fixes valid findings, and repeats until clean. Sub-flow of
  infra-orchestrator; can also be invoked directly after a manual edit.
---

# Infra self-review loop

Run this after every implementation pass — your own or a subagent's — so the change is verified and reviewed before push. This is the infra equivalent of an app `self-review`: the **build/test step is `terragrunt run plan`**, not unit tests.

This is a **sub-flow of `infra-orchestrator`**. Can also be invoked standalone after a manual edit.

## Hard rules

- **Plan-only.** The Verify subagent runs `terragrunt run plan`, `validate`, `fmt`, lint, `pre-commit`. **Never** `apply` or `destroy`.
- **No recursive subagents.** Every subagent prompt MUST include: *"You may not spawn subagents yourself."*
- **You** assess findings, not the reviewers. Review subagents return findings; the orchestrator decides validity.

## Step 1 — Verify (single subagent, runs first)

Delegate to a general subagent with this scope:

1. From the repo root: `pre-commit run --all-files`. If hooks rewrite files, re-stage and re-commit per `pre-commit-infra-mandatory` (no `--no-verify`).
2. For each affected module the orchestrator listed:
   ```bash
   export CLUSTER_NAME=<cluster>
   export KUBERLY_ROLE=$(jq -r .kuberly_role components/$CLUSTER_NAME/shared-infra.json)
   aws sts get-caller-identity
   terragrunt run plan \
     --working-dir './clouds/<cloud>/modules/<module>/' \
     --iam-assume-role "$KUBERLY_ROLE"
   ```
   For GCP / Azure, follow `clouds/<provider>/README.md`.
3. Capture for each module: a 5–15 line summary, risks (resource replacements, identity / KMS / IAM changes, count of additions / changes / destroys), and a **fenced plan excerpt** for the PR comment.
4. If `aws sts get-caller-identity` or the AssumeRole pre-check fails, **stop** and report the error — do not fake a plan output.

The Verify subagent returns a single Markdown block per module. The orchestrator stitches these into `context.md` under a `## Verification` section.

## Step 2 — Two reviews in parallel

Delegate **both** in one message so they run concurrently.

### In-context Review

Inputs:
- The shared `context.md` (goal, decisions, target envs, IAM, shared-infra impact).
- The OpenSpec change folder path (`openspec/changes/<name>/` or its archive).
- The full diff of the change.
- The Verify subagent's plan summaries.

Checks:
- **OpenSpec deltas.** Do `proposal.md`, `tasks.md`, and any spec deltas under `openspec/changes/<name>/specs/` actually match the diff?
- **Drift.** If multiple envs are in scope, does the change land consistently? Use `mcp__kuberly-graph__drift` to verify.
- **Shared-infra blast.** If `components/<cluster>/shared-infra.json` was edited, does the change account for every dependent component (run `blast_radius`)?
- **Plan correctness.** Do the plan summaries match intent? Any unexpected resource replacements, identity changes, or destroys?
- **Plan-only adherence.** No `apply` / `destroy` was attempted.
- **Pre-commit cleanliness.** All hooks pass; no autofixes left unstaged.

Output: findings ordered by severity (Critical / Major / Minor / Nit), each with file:line, the issue, and a recommended fix. Findings only — no auto-fixing.

### Out-of-context Review

Inputs: **only** the diff and the Verify plan excerpt. No `context.md`, no OpenSpec, no graph data.

Checks: pure HCL / JSON / CUE correctness — typos, dangling references, wrong types, broken interpolation, lifecycle pitfalls (`prevent_destroy`, `create_before_destroy`), suspicious provider / module versions.

Output: same severity-ordered findings format.

## Step 3 — Triage findings (orchestrator only)

For each finding:

- **Valid + actionable** → record in `.agents/prompts/<session>/review-findings.md` with status `open`.
- **Caused by missing context** (only out-of-context reviewer would have raised it) → discard, don't record.
- **Repeated from a prior pass** → discard, mark the original as `still-open` if not yet fixed.
- **Invalid** → discard with a one-line reason.

Show the user the open findings and ask: address now, defer, or discard. **Do not auto-fix.**

## Step 4 — Fix loop

For each accepted finding:

1. Write a fix prompt under `.agents/prompts/<session>/fix-<n>.md` referencing `context.md` and the specific finding.
2. Request user approval on the prompt.
3. Delegate to an Implement subagent. Run independent fixes in parallel.
4. After all fixes return, update `context.md` so reviewers don't re-flag the same issues, mark the findings `fixed`, and **restart from Step 1**.

Repeat until both reviewers return zero valid findings.

## Step 5 — Final multi-pass review

If the change involved multiple implementation passes, run **one more** in-context + out-of-context review pair targeting the **cumulative diff** since the orchestrator started. Findings here block the close-out.

## Final response to the user

Once reviewers are clean, report:

- **Key fixes made** — bulleted, link to file paths + lines.
- **Key decisions made** — what was chosen and why; cite `context.md` lines.
- **Verification commands run** — `pre-commit`, the exact `terragrunt run plan` invocations, and a fenced plan excerpt per module (for the PR body).
- **Final review verdict** — both reviewers clean, count of findings discarded with reasons.
- **Residual risks / optional follow-ups** — anything noted but deferred (out-of-scope drift, optional refactors, OpenSpec items still `proposed` not `applied`).
- **Branch + PR hand-off (mandatory).** Confirm the change lives on a feature branch, then hand off to `infra-change-git-pr-workflow` (Path A for integration-branch base; Path B if the session started from a long-lived dev branch) for the OpenSpec archive + push + PR steps. Reporting "done" without an open PR — or with commits on an integration branch — is not allowed.

## Related

- **`infra-orchestrator`** — parent flow.
- **`revise-infra-plan`** — sibling flow that runs before implementation.
- **`pre-commit-infra-mandatory`** — Verify subagent uses this for the hooks loop.
- **`terragrunt-local-workflow`** — exact `CLUSTER_NAME` / `KUBERLY_ROLE` invocation.
- **`infra-change-git-pr-workflow`** — close-out: branch / commit / PR with plan excerpt.
- **`openspec-changelog-audit`** — verify each archived change has a `CHANGELOG.md`.
