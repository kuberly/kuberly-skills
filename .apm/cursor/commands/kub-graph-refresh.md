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
3. Tell the operator how to open **`graph.html`** locally and what to expect after **v0.34.0+**: 3D **force-directed** graph (3d-force-graph + d3-force-3d); **Group by layer / env / module / resource_type / provider**; **Filters** panel; click **Filters > Reset** to recenter. Layer pills: `IaC files` / `TG / OpenTofu state` / `K8s resources` / `Docs` / `CUE` / `CI/CD` / `Applications` (the last three from v0.36+ / v0.38+).
4. If generation fails, read the error, suggest **`apm install`** / **`.venv-mcp`** / missing **`root.hcl`**, and do not claim the graph is fresh.

**Optional — populate the *Applications* (rendered) layer**

The per-app rendered manifests (CUE → k8s YAML stream) are not populated by the canonical generator — they need a separate manual run before regen:

```
python3 apm_modules/kuberly/kuberly-skills/scripts/render_apps.py        # writes .kuberly/rendered_apps_<env>.json
python3 apm_modules/kuberly/kuberly-skills/scripts/diff_apps.py          # writes .kuberly/app_drift_<env>.json (optional)
python3 apm_modules/kuberly/kuberly-skills/mcp/kuberly-platform/kuberly_platform.py generate . -o .kuberly
```

After that, `query_nodes(node_type="app_render")` / `query_nodes(node_type="rendered_resource")` return the rendered manifests, and the **Applications** layer pill (hot pink) appears on the graph view.

**Output**

- One-line **status** (success / blocked + reason).
- **Files touched** (list).
- **Operator tip:** default overview for ~280+ nodes; use search + sidebar on Graph tab.
