---
name: /kub-repo-locate
id: kub-repo-locate
category: Operations
description: Find where to edit — Terragrunt roots, components, applications, secrets, shared-infra
---

You are helping an engineer **find the right files** in a **kuberly-stack** fork (or similar Terragrunt monorepo) before they edit anything.

**Inputs (ask once if missing):** what they want to change (one sentence), and **which environment / cluster** (e.g. `prod`, `stage`, cluster name).

**Steps**

1. Load **`kuberly-stack-context`** and **`components-vs-applications`** mentally — do not guess paths that contradict **`AGENTS.md`** / repo docs.
2. Classify the target:
   - **Terraform / Terragrunt module** → `clouds/<cloud>/modules/<name>/` and Terragrunt live dirs under `clouds/` (see `root.hcl`, `env.hcl`, `INSTANCE` / `CLUSTER_NAME` patterns).
   - **Cluster wiring** → `components/<cluster>/*.json` (especially **`shared-infra.json`** — high blast radius).
   - **Application / Argo / deploy manifest** → `applications/<env>/<app>.json` and **`application-types-and-deploy-paths`** if shape is unclear.
   - **Secrets references** → `components/.../secrets.json`, empty SM placeholders, **`application-env-and-secrets`**.
   - **CUE schema** → `cue/**/*.cue`. The graph carries one `cue_schema` node per file — `query_nodes(node_type="cue_schema")` to enumerate.
   - **CI/CD job** → `.github/workflows/*.yml`. The graph carries one `workflow` node per file with `references` edges to the modules/components it deploys — `query_nodes(node_type="workflow")` then `get_neighbors` to find which workflow deploys what.
3. If **kuberly-platform MCP** is available, call **`query_nodes`** / **`shortest_path`** for the named module or app string the user gave; paste a **short** graph slice (ids only, no walls of JSON). When the question is "what does this app actually deploy", check for `app_render:<env>/<app>` (rendered layer) — populated only after the manual `scripts/render_apps.py` has run, so absence ≠ no manifests.
4. List **exact relative paths** to open first (max **6** files), in **edit order**, with one line each on **why** that file.
5. Call out **one** “watch out” (OpenSpec requirement if `openspec/` is in play, IAM / `KUBERLY_ROLE`, or drift) only if grounded in repo layout or user input.

**Output**

- **Verdict line:** “You are editing: **module** / **component** / **application** (pick one).”
- **Open these files:** numbered list with paths.
- **Next command / skill:** e.g. run **`terragrunt-local-workflow`**, **`/kub-plan-review`** after plan, or **`infra-change-git-pr-workflow`** before PR.
