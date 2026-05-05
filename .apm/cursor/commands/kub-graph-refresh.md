---
name: /kub-graph-refresh
id: kub-graph-refresh
category: Operations
description: Regenerate .kuberly graph artifacts and point to graph.html (overview vs full)
---

Refresh **stack intelligence** under **`.kuberly/`** so the MCP and **`.kuberly/graph.html`** match the repo.

**Steps**

1. Prefer the repo’s canonical generator (from **`AGENTS.md`** / **`post_apm_install.sh`**):
   `python3 apm_modules/kuberly/kuberly-skills/mcp/kuberly-platform/kuberly_platform.py generate "$REPO_ROOT" -o .kuberly`
   Adjust only if this fork documents a different entrypoint. Capture stderr/stdout summary (node/edge counts if printed).
2. Confirm outputs exist: **`.kuberly/graph.html`**, **`.kuberly/graph.json`** (if not gitignored for this fork), **`blast_*.mmd`** / **`GRAPH_REPORT.md`** when the generator emits them.
3. Tell the operator how to open **`graph.html`** locally and what to expect after **v0.32.6+**: **Graph scope → Overview (module deps)** for large stacks (Terragrunt **module** + **`depends_on`** only); **Full graph** for deep dives; choice persists in **sessionStorage**.
4. If generation fails, read the error, suggest **`apm install`** / **`.venv-mcp`** / missing **`root.hcl`**, and do not claim the graph is fresh.

**Output**

- One-line **status** (success / blocked + reason).
- **Files touched** (list).
- **Operator tip:** default overview for ~280+ nodes; use search + sidebar on Graph tab.
