# Contributing

## Pull request templates

- **Skills-only changes:** use GitHub **`.github/PULL_REQUEST_TEMPLATE/skills.md`** (or paste that checklist in Bitbucket). In Cursor, load skill **`git-pr-templates`** (`references/skills-repo-pr.md`).
- **Customer infra fork** (mirrors kuberly-stack): use **`.github/PULL_REQUEST_TEMPLATE/infra_fork.md`** for Problem / Solution / OpenSpec / Testing / Risks / Mermaid — or skill **`git-pr-templates`** (`references/infra-fork-pr.md`).

## Pull request checklist (author)

- [ ] No secrets, tokens, private URLs, customer PII, or org-specific IAM ARNs in committed files.
- [ ] No hidden Unicode / homoglyph tricks; review diffs carefully.
- [ ] Executable or hook-like content is intentional, documented, and minimal.
- [ ] Customer-facing tone matches org policy (optional terse-style skills are opt-in).

## Checks

Run **`./scripts/validate-skills.sh`** before pushing. **Bitbucket Pipelines** runs the same script on each pipeline build.
