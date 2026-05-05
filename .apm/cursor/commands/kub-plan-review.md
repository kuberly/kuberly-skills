---
name: /kub-plan-review
id: kub-plan-review
category: Operations
description: Structured Terragrunt/OpenTofu plan review — surprises, destroys, IAM, state alignment
---

Review a **Terragrunt plan** like a staff engineer: the user will paste an excerpt or point to CI logs.

**Inputs:** Ask once if missing: **module path** (`clouds/aws/modules/...`), **environment/cluster**, and the **plan excerpt** (JSON `-json` or text).

**Steps**

1. Load persona **`terragrunt-plan-reviewer`** (`.cursor/agents/` / `.claude/agents/`) and follow its **diff-only** discipline: no invented resources.
2. Classify changes: **create / update / delete / replace**. Flag **destroy**, **replace (forces new)**, security-group **ingress widen**, **IAM policy** broadening, **RDS / OpenSearch** major version jumps.
3. Cross-check **blast radius**: if **kuberly-platform MCP** is available, **`query_nodes`** / **`upstream`** / **`downstream`** for the module or shared-infra hinge mentioned in the plan.
4. Call out **state vs config** mismatches only when the excerpt supports it; otherwise label as “unknown from excerpt alone.”
5. Finish with **Verdict**: Ship / Ship with conditions / Block — each with **three concrete bullets** max.

**Output format**

- **Summary table:** resource address → action → risk (Low/Med/High).
- **Blockers:** fenced list or “None”.
- **Questions for operator:** max 3 bullets.
