## Summary

<!-- What changed? Link skills / docs / application or component JSON. -->

## Risk checklist (author)

- [ ] No secrets, tokens, private URLs, customer PII, or org-specific IAM ARNs in committed files.
- [ ] No hidden Unicode / homoglyph tricks (paste from trusted sources only; reviewers may run `apm audit --file` on suspicious diffs if available).
- [ ] Executable or hook-like content is intentional, documented, and scoped (skills are **instructions**, not arbitrary code execution in most hosts — still treat as supply chain).
- [ ] Customer-facing tone is appropriate (see org guidance on optional “compression” / humor skills).

## Testing

- [ ] `./scripts/validate-skills.sh` passes locally.
- [ ] Bitbucket Pipelines (or GitHub Actions, if mirrored) green for this branch.
