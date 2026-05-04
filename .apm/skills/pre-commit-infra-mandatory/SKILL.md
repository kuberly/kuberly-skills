---
name: pre-commit-infra-mandatory
description: >-
  Require pre-commit for kuberly-stack infra changes: install tracked hooks, run checks before
  commit, and on failure re-stage auto-fixes and commit again until clean (no --no-verify).
---

# Pre-commit — mandatory for infra changes

Treat **pre-commit** as **non-optional** for commits that touch **IaC** (`.tf`, `.hcl`, Terragrunt, YAML/JSON under repo control, etc.). Hooks often **rewrite** files (`terraform_fmt`, `trailing-whitespace`, `end-of-file-fixer`); after a failed hook run, **re-stage** those fixes and **commit again**.

## One-time per clone (humans / fresh workspace)

From the **git root** of the infra repo:

```bash
./scripts/install-hooks.sh
./scripts/verify-hooks.sh
```

That sets **`core.hooksPath`** to **`.githooks`** so **`git commit`** runs **pre-commit** on staged files (see **`.githooks/README.md`**). If **`pre-commit`** is missing: **`pip install pre-commit`** (and **tflint** per **`.pre-commit-config.yaml`** comments).

## Agent workflow (Cursor / Claude / Codex)

1. **Before the first commit** on a change set, run pre-commit explicitly if you are unsure hooks are installed:

   ```bash
   pre-commit run --files <paths-you-edited>
   ```

   For broad edits or first-time setup on a machine, **`pre-commit run --all-files`** is acceptable but slower.

2. **`git add`** only what you intend, then **`git commit`**. If **hooks are installed**, commit may run pre-commit again — that is expected.

3. **If `git commit` fails** (exit non-zero) because pre-commit **modified** files or reported fixable issues:

   - Read the hook output (which files changed).
   - Run **`git status`**, then **`git add -u`** or **`git add .`** to include hook fixes (your org standard may prefer **`git add -u`** to avoid unrelated untracked files — if only hook-fixed tracked files changed, either is fine).
   - **`git commit`** again with the **same** or an amended message (use **`git commit --amend --no-edit`** only if you are still on the same unpublished commit and the team allows amend).

4. **Repeat step 3** until **`git commit`** succeeds. Cap at **reasonable** retries (e.g. **five**); if still failing, **stop** and fix remaining errors manually (often **tflint** or a hook that does not auto-fix). Do **not** loop blindly on the same error.

5. **Never** use **`git commit --no-verify`** for routine infra work. Reserve **`--no-verify`** only when the **user explicitly** orders it for a documented exception.

## What “failure” means

| Outcome | Action |
|--------|--------|
| Hook **rewrote** files (fmt, EOF, whitespace) | **`git add`** those paths → **`git commit`** again |
| Hook **reports** errors with **no** auto-fix (e.g. some lint rules) | Edit files → **`git add`** → **`git commit`** again |
| Same error after **multiple** rounds | Stop; summarize for the human |

## APM skills sync (kuberly-stack fork with `apm.yml` deps)

**`scripts/ensure_apm_skills.sh`** runs as a **pre-commit** hook when **`apm.yml`** lists **non-empty** `dependencies.apm`. It runs **`apm install`** and fails if **`apm.lock.yaml`** changed — **`git add apm.lock.yaml`** and **`git commit`** again (same loop as fmt). See **`apm-skills-bootstrap`**. **`KUBERLY_SKIP_APM_SYNC=1`** skips it.

### Hook order (avoid pre-commit ↔ APM “flapping”)

In **`.pre-commit-config.yaml`**, list **`ensure-apm-skills` before** **`trailing-whitespace`** and **`end-of-file-fixer`** (and the rest of **`pre-commit-hooks`**). If generic hooks run first, they fix deployed markdown (e.g. **Caveman** under **`.cursor/skills/`**), then **`apm install`** redeploys the package bytes and the next commit fails again on the same paths.

**`kuberly/skills`** keeps every **`.apm/skills/**/*.md`** ending with a final newline (**`scripts/validate-skills.sh`** enforces it) so **`end-of-file-fixer`** is a no-op on skills content after deploy. Third-party APM packages (e.g. **Caveman**) are outside this repo — hook order is what makes them stable in infra clones.

## CI vs local

**`.pre-commit-config.yaml`** notes that some heavy checks run in **CI** only; local hooks still enforce fmt/tflint/basic hygiene. Do not skip local pre-commit because “CI will catch it.”

## Customer forks

Same rules if the fork ships **`.pre-commit-config.yaml`** and **`./scripts/install-hooks.sh`**. If a fork **removed** hooks, document that exception in the fork’s **`AGENTS.md`** — this skill assumes the **kuberly-stack** layout.
