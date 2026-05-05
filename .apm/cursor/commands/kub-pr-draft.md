---
name: /kub-pr-draft
id: kub-pr-draft
category: Operations
description: Draft an infra PR title + body — problem, solution, testing, risks (fork-ready)
---

Draft a **pull request** for an **infra fork** change (Terragrunt / components / applications). The human will paste or attach a **diff summary**, **ticket link**, or bullet list of what changed.

**Inputs:** `MERGE_BASE` / target branch if known, **scope** (modules / paths), and **what is not in the diff** (e.g. “plan only in CI”, “manual smoke pending”).

**Steps**

1. Load **`git-pr-templates`** and **`infra-change-git-pr-workflow`** — match the fork’s expected sections (Problem / Solution / Testing / Risks; add **OpenSpec:** path only if this repo uses **`openspec/changes/<name>/`** and the change has a folder).
2. Infer **risk level** from touches: **`shared-infra.json`**, IAM, RDS, networking, auth → call out explicitly.
3. **Testing:** list what was run (`pre-commit`, local `tflint`, CI plan link placeholder) and what the **operator** must still run if anything is CI-only.
4. Keep the body **paste-ready** Markdown; no generic filler.

**Output**

- **Suggested PR title** (≤72 chars, imperative).
- **PR body** with headings the team’s template expects.
- **Optional:** one **Mermaid** diagram only if the user’s scope is a small dependency chain (≤8 nodes); otherwise skip.
