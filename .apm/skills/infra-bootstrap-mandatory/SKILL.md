---
name: infra-bootstrap-mandatory
description: >-
  MANDATORY at the start of every infra session: run apm install, identify the
  merge base, create a feature branch — before editing any file. Then PR back
  to that merge base with a quality description. Non-negotiable.
---

# Mandatory infra session bootstrap

This skill defines what **must** happen before any agent (or human-driven AI session) edits a file in an infra repo. Skipping any step makes the rest of the session unreviewable.

**Three rules. No exceptions.**

1. **`apm install` first.** Vendored skills + lock must match upstream before you trust any skill content.
2. **Feature branch before any edit.** No file edits on `dev`/`stage`/`prod`/any long-lived integration branch.
3. **PR back to `MERGE_BASE` with a real body.** No "done" without an open PR; no PRs without Problem / Solution / Testing / Risks.

## Step 1 — `apm install` (mandatory)

```bash
apm install                  # normal case: lock matches; nothing changes
# OR
apm install --update         # only when intentionally bumping a dep in apm.yml
```

If `apm install` modifies any file (`apm.lock.yaml`, vendored skill files under `.claude/`, `.cursor/`, `.github/skills/`), **stop**: that drift is a real change and must be committed before you start your task. The pre-commit hook **`ensure-apm-skills`** enforces this on every commit — see **`apm-skills-bootstrap`** for the hook configuration.

If `apm` CLI is missing, install it first ([APM Quick Start](https://microsoft.github.io/apm/getting-started/quick-start/)). Do not proceed with stale vendored skills — you'll be reading guidance that no longer matches upstream.

## Step 2 — feature branch (mandatory)

Identify `MERGE_BASE` and branch off **before** touching files. Two paths from **`infra-change-git-pr-workflow`**:

| Situation | Path |
|-----------|------|
| Need to discover the canonical integration branch (`dev`, `stage`, `prod`) | **A** |
| Already on a long-lived branch and the team's flow is "branch off **current**, PR back to **same**" | **B** |

```bash
# Path A
git fetch origin && git checkout <integration-branch> && git pull --ff-only
MERGE_BASE="<integration-branch>"

# Path B
git fetch origin
MERGE_BASE="$(git branch --show-current)"
[[ -n "$MERGE_BASE" ]] || { echo "no current branch"; exit 1; }
if git show-ref --verify --quiet "refs/remotes/origin/${MERGE_BASE}"; then
  git pull --ff-only origin "${MERGE_BASE}"
fi

# Common: branch off
git checkout -b "feat/${MERGE_BASE}-<slug>"
```

**Hard rule:** if you find yourself running `Edit` / `Write` / `git commit` while still on `MERGE_BASE`, **stop, `git stash`, branch, then re-apply**. Never leave the only copy of work committed on an integration branch when a PR is expected.

See **`infra-change-git-pr-workflow`** for full Path A/B mechanics, branch-name conventions, and OpenSpec gates.

## Step 3 — implement, verify, commit

- **`pre-commit`** — run via the hook; re-add and re-commit on auto-fixes (**`pre-commit-infra-mandatory`**).
- **`terragrunt run plan`** — agents stay plan-only unless a human explicitly approved apply (**`terragrunt-local-workflow`**).
- **OpenSpec** — required for changes under `clouds/`, `components/`, `applications/`, `cue/`, behavioral `*.hcl` (**`openspec-changelog-audit`**).

## Step 4 — PR back to `MERGE_BASE` (mandatory)

**Compare:** your feature branch. **Base:** `MERGE_BASE` exactly — not `main` or `master` if those weren't your start point.

```bash
gh pr create --base "${MERGE_BASE}" --head "$(git branch --show-current)" \
  --title "<imperative scoped title>" --body-file pr-body.md
```

**PR body — required sections** (paste-ready in **`git-pr-templates`** `references/infra-fork-pr.md`):

1. **Problem** — what's broken or missing.
2. **Solution** — files / modules / OpenSpec path.
3. **OpenSpec** — `OpenSpec: openspec/changes/...` (active or archive); omit only if explicitly out of scope.
4. **Testing** — `pre-commit` results, `terragrunt run plan` modules + one fenced excerpt.
5. **Risks** — blast radius, rollback, manual steps.
6. **Mermaid** — branch lifecycle diagram always; control-flow diagram when the change is non-trivial.

**Reporting "done" without an open PR — or with commits left on `MERGE_BASE` — is not allowed.**

## Pair with

- **`apm-skills-bootstrap`** — first-time `apm install` + hook installation; the hook detail behind Step 1.
- **`infra-change-git-pr-workflow`** — full branch/commit/PR mechanics for Steps 2–4.
- **`pre-commit-infra-mandatory`** — the hooks loop in Step 3.
- **`infra-self-review`** / **`agent-orchestrator`** — for non-trivial changes that warrant a parallel review pass before the PR.
