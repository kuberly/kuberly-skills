---
name: git-pr-templates
description: >-
  PR body templates for the skills repo and for kuberly-stack infra forks; use with infra-change-git-pr-workflow.
---

# Git pull request templates

Use this skill when you need **paste-ready PR sections** that match what reviewers expect in this org.

## Where the canonical markdown lives

| Audience | Canonical path in **this** repo | Copy in this skill (for APM / Cursor) |
|----------|-----------------------------------|----------------------------------------|
| **Skills repo** PRs | **`.github/PULL_REQUEST_TEMPLATE/skills.md`** | **`references/skills-repo-pr.md`** (same text) |
| **Infra fork** PRs (kuberly-stack style) | **`.github/PULL_REQUEST_TEMPLATE/infra_fork.md`** | **`references/infra-fork-pr.md`** (same text) |

**GitHub** and **mirrors** still read **`.github/PULL_REQUEST_TEMPLATE/`** for native PR UI when that path exists on the host. The **`references/`** copies exist so **APM** can ship the same wording into **`.cursor/skills/git-pr-templates/references/`** on consumer clones.

## When to load this skill

- Opening or editing a **skills** repo pull request (checklist + risk tone).
- Opening a **customer infra fork** pull request where the team uses **`infra_fork.md`** sections (Problem / Solution / OpenSpec / Testing / Risks / Mermaid).

Pair with **`infra-change-git-pr-workflow`** for branch choice, push, and merge target — that skill covers both Path A (integration branch) and Path B (already on the merge target).
