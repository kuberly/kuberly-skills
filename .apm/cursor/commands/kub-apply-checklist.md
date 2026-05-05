---
name: /kub-apply-checklist
id: kub-apply-checklist
category: Operations
description: Human pre-apply gate — identity, workspace, lock, blast, rollback (no apply in-agent)
---

Build a **checklist** for the **human operator** immediately before **`terragrunt apply`** / **`tofu apply`** (agents and CI still follow **plan-only** policy unless the org explicitly allows otherwise).

**Inputs:** module or stack path, environment, and whether **CI plan** is green or **local plan** only.

**Steps**

1. **Identity & workspace:** remind to confirm **`aws sts get-caller-identity`** (or org equivalent), correct **`AWS_PROFILE`**, **`CLUSTER_NAME`**, **`KUBERLY_ROLE`** / two-step assume per **`terragrunt-local-workflow`** / **`AGENTS.md`**.
2. **Lock & state:** state bucket / Dynamo lock table correct for this env? No stray lock from a crashed run?
3. **Plan parity:** same **SHA** / branch as the approved plan? If CI plan vs local plan differ, **stop** and reconcile.
4. **Blast radius:** if **kuberly-platform MCP** is available, **`blast_radius`** or **`query_nodes`** on the hub module; otherwise point to **`.kuberly/graph.html`** and **`.kuberly/GRAPH_REPORT.md`** for a quick read.
5. **Rollback:** one paragraph: what to revert (git / TF state / feature flag) if apply succeeds but app is broken.

**Output**

- **Checklist** with `[ ]` items grouped: Auth · Plan · Lock · Blast · Rollback.
- **Ship / hold:** one-word recommendation with **one sentence** rationale.
- Do **not** run apply, destroy, or init in the agent session — checklist only.
