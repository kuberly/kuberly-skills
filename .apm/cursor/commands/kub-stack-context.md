---
name: /kub-stack-context
id: kub-stack-context
category: Operations
description: Lock in CLUSTER_NAME, two-step IAM, and Terragrunt roots before any plan or apply
---

You are helping an operator work safely in this **kuberly-stack** fork. Do **not** improvise AWS identity or paths.

**Goal:** Produce a short, copy-pasteable **context block** the operator can keep in the chat (or ticket) for this session.

**Steps**

1. Read **`AGENTS.md`** (repo root) and honor **OpenTofu (`tofu`)** + **Terragrunt** layout under **`clouds/aws/modules/`** unless the repo explicitly uses another path.
2. Confirm **`root.hcl`** exists and how **`CLUSTER_NAME`** is threaded (env / `include`). State the resolved cluster label you infer (or ask once if ambiguous).
3. Point to **`components/<CLUSTER_NAME>/shared-infra.json`** (or the fork’s equivalent) for **`KUBERLY_ROLE`** / org controller role. Remind: **assume org role first**, then **`KUBERLY_ROLE`** for Terragrunt `--iam-assume-role` (two-step); never bake long-lived keys.
4. List **one** representative **`terragrunt run plan`** working-dir example under **`clouds/aws/modules/<module>/`**, with placeholders only for module name — use the same flags the repo documents (`--non-interactive`, `--iam-assume-role`, etc.).
5. If **kuberly-platform MCP** is available, optionally call **`query_nodes`** for `type:environment` or `shared-infra` to echo **account / region / cluster_name** from the graph — treat as advisory vs live AWS.
6. Link mentally to **`QUICK_REFERENCE.md`** and **`MODULE_CONVENTIONS.md`** when the question touches module boundaries or naming.

**Output format**

- **Cluster / role:** bullet lines (CLUSTER_NAME, where `KUBERLY_ROLE` is read from, assume-role order).
- **Plan entrypoint:** one fenced shell example with `CLUSTER_NAME=…` and `--working-dir`.
- **Do-not:** one line (e.g. no `COMPONENT_DIR` double-prefix if `AGENTS.md` forbids it).
- **Next slash command:** suggest `/kub-graph-refresh` if they need stack intelligence, or `/kub-plan-review` once they have a plan excerpt.
