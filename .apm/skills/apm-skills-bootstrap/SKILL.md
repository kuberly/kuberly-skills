---
name: apm-skills-bootstrap
description: >-
  After cloning a kuberly-stack fork: run apm install, use pre-commit ensure-apm-skills hook,
  and load github.com/kuberly/kuberly-skills for Cursor and Claude Code (.cursor/skills and .claude/skills).
---

# APM skills bootstrap (customer infra repo)

Customer developers should get **skills** and **agent config** from the public **`kuberly-skills`** repo (`github.com/kuberly/kuberly-skills`) via **Microsoft APM** — see **`apm.yml`** in your fork.

**Token cost:** upstream **`apm.yml`** pins **[Caveman](https://github.com/JuliusBrussee/caveman)** and **`git@github.com:kuberly/kuberly-skills.git#vX.Y.Z`** (SSH preferred for tag pinning; **`kuberly/kuberly-skills#vX.Y.Z`** also works) so **`apm install`** deploys **Caveman** plus **kuberly** skills into **`.cursor/skills/`** and **`.claude/skills/`** (APM copies to every agent layout that exists). Forks mirroring skills elsewhere (Bitbucket, GitLab, internal git host) replace the dependency line and re-run **`apm install`**; remove **Caveman** only if compliance forbids it.

## After every `git clone`

1. Install **APM** ([APM Quick Start](https://microsoft.github.io/apm/getting-started/quick-start/)).
2. Public **`github.com/kuberly/kuberly-skills`** needs no token; APM uses **`GITHUB_TOKEN`/`GH_TOKEN`** if present (rate-limit relief or private mirrors). For Bitbucket / GitLab / internal mirrors, configure normal git credentials so **`git ls-remote`** works.
3. From the **infra repo root**:

   ```bash
   apm install
   ```

4. Install git hooks so **pre-commit** (and **APM sync**) run on commit:

   ```bash
   ./scripts/install-hooks.sh
   ```

## What `ensure_apm_skills` does (kuberly-stack)

**`scripts/ensure_apm_skills.sh`** runs at **pre-commit** when **`apm.yml`** declares **non-empty** `dependencies.apm`. It **`mkdir -p .claude/skills`** so **Claude Code** gets the same org skills as Cursor, then runs **`apm install`**. It fails the commit if **`apm.lock.yaml`** changed so you **`git add apm.lock.yaml`** and commit again (same pattern as fmt hooks).

- Skip entirely: **`export KUBERLY_SKIP_APM_SYNC=1`** (or remove / empty **`apm:`** deps while bootstrapping).
- If **`apm`** CLI is missing, the hook **warns** and **does not** fail the commit (install APM when you are ready to consume skills).

**Pre-commit order:** in **`.pre-commit-config.yaml`**, declare **`ensure-apm-skills` before** **`end-of-file-fixer`** (and other **`pre-commit-hooks`** entries). Otherwise generic hooks “fix” deployed **Caveman** / skill markdown, then **`apm install`** runs and restores package bytes — commits flap. Upstream **kuberly-stack** ships that order.

## Claude Code (primary IDE for many teams)

- Read **`CLAUDE.md`** at the infra repo root, then **`AGENTS.md`** for full rules.
- After **`apm install`**, use skills from **`.claude/skills/<name>/`** the same way you would under **`.cursor/skills/`**.
- Install the **Caveman** plugin for shorter replies — **`docs/agent-packaging/CAVEMAN.md`** in **kuberly-stack**.

## Which skills to use for “normal” infra edits

| Goal | Skill |
|------|--------|
| Branch → PR → Mermaid (Path A integration branch, or Path B already-on-merge-target) | **`infra-change-git-pr-workflow`** |
| Pre-commit / autofix loop | **`pre-commit-infra-mandatory`** |
| Terragrunt / `CLUSTER_NAME` / `KUBERLY_ROLE` | **`terragrunt-local-workflow`**, **`kuberly-cli-customer`** |
| Repo map / OpenSpec | **`kuberly-stack-context`**, **`openspec-changelog-audit`** |
| `components/` vs `applications/` | **`components-vs-applications`**, **`kuberly-gitops-execution-model`** |
| Debug loop — thread vs git | **`short-session-memory`** |
| Env + Secrets Manager + app JSON | **`application-env-and-secrets`** |
| CloudTrail last hour, all regions | **`cloudtrail-last-hour-all-regions`** |
| VPC Flow Logs — src/dst grouping | **`vpc-flow-logs-source-destination-grouping`** |
| GitHub Actions → stack reusables (backend / app repo) | **`github-reusable-ci-kuberly-stack`** |
| K8s FinOps (Prometheus usage vs requests/limits) | **`kubernetes-finops-workloads`** |
| PR body markdown (skills vs infra fork) | **`git-pr-templates`** |

PR bodies: load **`git-pr-templates`** (same text as **`.github/PULL_REQUEST_TEMPLATE/`**, shipped under **`references/`** for APM). On **Bitbucket** (or any host without native `.github` PR templates) copy sections from **`references/`** or add a saved description template in repo settings.
