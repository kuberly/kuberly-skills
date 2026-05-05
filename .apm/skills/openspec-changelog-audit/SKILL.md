---
name: openspec-changelog-audit
description: >-
  Require CHANGELOG.md inside every OpenSpec change (active and archived) for audit trails and
  cross-repo aggregation; wire Cursor / Claude Code workflows to create, update, and PR-excerpt it.
---

# OpenSpec — mandatory `CHANGELOG.md` per change (audit + aggregation)

Use this skill when **every meaningful infra change** must leave an **auditable, human-readable trail** that your platform can **collect later** from many **customer forks** (same relative path in every repo).

IDEs do not enforce repo rules by themselves: **agents** implement this skill by **always** creating and maintaining **`CHANGELOG.md`** beside **`proposal.md`** in the OpenSpec change folder.

## Non-negotiable file

For **each** OpenSpec change directory:

- **`openspec/changes/<change-name>/CHANGELOG.md`**
- After archive: **`openspec/changes/archive/YYYY-MM-DD-<change-name>/CHANGELOG.md`** (the file **moves with** the folder — keep it updated through archive so history stays intact).

**Filename:** exactly **`CHANGELOG.md`** at the **root of the change folder** (not under `specs/`). Stable path enables org-wide jobs such as:

```text
openspec/changes/**/CHANGELOG.md
openspec/changes/archive/**/CHANGELOG.md
```

## Required sections (audit-friendly)

Keep Markdown **H2 headings** stable so downstream parsers stay boring:

| Section | Purpose |
|---------|---------|
| **`## Summary`** | **User- or operator-facing** one short paragraph: what changed and why it matters. |
| **`## Technical notes`** | Optional: modules, JSON paths, state keys, migration steps (internal detail). |
| **`## Risk & rollback`** | What can go wrong; how to revert or mitigate. |
| **`## Customer impact`** | Optional but recommended for forks: **who** is affected (all tenants / one cluster / opt-in). Use **`None`** if truly internal-only. |

Update **`CHANGELOG.md` on every material edit** to the change (not only at the end). Treat it as the **running** release note for that OpenSpec card.

## Agent workflow (Cursor + Claude Code)

1. **When creating a change** (`openspec new`, Cursor **opsx:** propose flow if your workspace has it, or hand-created `openspec/changes/<name>/`): add **`CHANGELOG.md`** immediately with **`## Summary`** stub (replace “TBD” before PR).
2. **While implementing**: append or revise **Technical notes** / **Risk** as decisions land.
3. **Before `git push` / PR**: **`## Summary`** must be **final-quality** (no TBD). Same bar as **`proposal.md`** abstract.
4. **When archiving** (move to **`openspec/changes/archive/...`**): re-read **`CHANGELOG.md`**; add a final line under **Summary** or **Technical notes** if archive fixed spec paths or dates.
5. **PR body**: paste a short **“Changelog (OpenSpec)”** block copied from **`CHANGELOG.md`** (in addition to **`OpenSpec:`** path — see **`infra-change-git-pr-workflow`**).

## Why this helps aggregation

Across **N customer repos**, an automated job (or ad-hoc script) can:

- Walk **`openspec/changes/archive/**/CHANGELOG.md`** on default branch (or all branches with PR labels).
- Emit CSV / JSON lines: **`repo`, `date`, `change-folder`, `summary-text`, `git-sha`** for a central **FinOps / compliance / release** datastore.

Using a **fixed layout** beats scraping free-form **`proposal.md`** alone.

## Governance

- **Secrets / ARNs / internal URLs** do not belong in **`CHANGELOG.md`** — use **`## Technical notes`** with **references** to secret **names** or runbooks only.
- If a change is **out of scope** for OpenSpec per **`AGENTS.md`**, no **`CHANGELOG.md`** is required in OpenSpec — document the exception in the **PR** instead.

## Related skills

- **`infra-change-git-pr-workflow`**, **`git-pr-templates`** — PR body + **`OpenSpec:`** line.
- **`kuberly-stack-context`** — OpenSpec scope and **`openspec/UPSTREAM_AND_FORKS.md`**.
- **`short-session-memory`** — keep huge logs out of **`CHANGELOG.md`**; link to tickets.

## Optional CI (customer-owned)

A lightweight check: fail if any **active** `openspec/changes/<name>/` (excluding `archive`) contains **`proposal.md`** but **no** **`CHANGELOG.md`**, or if **`## Summary`** still contains **`TBD`**. Implement in **customer** pipelines — not required in upstream **kuberly-stack** unless the org adopts it globally.

## Template

See **`references/CHANGELOG.template.md`**.
