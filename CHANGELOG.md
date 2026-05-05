# Changelog

## v0.32.2 — 2026-05-06

- **FIX:** MCP stdio failed when the host used a system **``python3``** without
  the PyPI **``mcp``** package (Cursor showed *install mcp>=1.10* then closed).
  **``scripts/ensure_mcp_venv.sh``** now creates **``.venv-mcp``** at the
  consumer repo root and **``pip install -r …/requirements-mcp.txt``**;
  **``post_apm_install.sh``** runs it before **``sync_claude_config.py``**.
  Cursor and Claude Code MCP entries use **``.venv-mcp/bin/python3``**;
  **``apm.yml``** MCP **``command``** matches for other APM targets.
- **GITIGNORE:** ignore repo-root **``.venv-mcp/** (consumer workspace).
- **BUMP:** apm.yml 0.32.1 → 0.32.2.

## v0.32.1 — 2026-05-06

- **FIX:** Cursor **hooks** — use supported event name **`beforeSubmitPrompt`**
  (replaces invalid **`UserPromptSubmit`** in `.cursor/hooks.json`).
- **FIX:** Cursor **MCP** / APM — `apm.yml` MCP args no longer use
  **`${CLAUDE_PLUGIN_ROOT}`** (Claude-only; Cursor left it literal and the
  server failed to start). Use repo-relative **`apm_modules/kuberly/kuberly-skills/...`**.
- **FIX:** **`orchestrator_route.py`** — echo **`hook_event_name`** from stdin
  (Cursor sends **`beforeSubmitPrompt`**); resolve **`.kuberly/graph.json`**
  via **`workspace_roots`** when present.
- **BUMP:** apm.yml 0.32.0 → 0.32.1.

## v0.32.0 — 2026-05-06

- **CHORE:** Version bump for APM consumer pins (no MCP behavior change vs v0.31.0).
- **BUMP:** apm.yml 0.31.0 → 0.32.0.

## v0.31.0 — 2026-05-06

- **CHANGE:** `kuberly_mcp/stdio_app.py` now drives stdio via **FastMCP**
  (`mcp.server.fastmcp.FastMCP`): `mcp.run(transport="stdio")`, optional
  `instructions`, and a **lifespan** that yields `AppRuntime` (graph +
  injected format/telemetry callables). Tool names, JSON Schemas, dispatch,
  rendering, and telemetry are unchanged (`manifest.py`, `dispatch.py`,
  `render_tool_result` / `_emit_telemetry` in `kuberly_platform.py`).
- **DETAIL:** `KuberlyFastMCP` overrides `list_tools` / `call_tool` so
  `tools/list` still comes verbatim from `mcp_tool_objects()` while the
  stack benefits from FastMCP’s stdio session wiring and initialization.
- **BUMP:** apm.yml 0.30.0 → 0.31.0.

## v0.30.0 — 2026-05-06

- **CHANGE:** `kuberly_platform.py mcp` now uses the official PyPI **`mcp`**
  Python SDK (`mcp.server.stdio` + low-level `Server`) instead of a hand-rolled
  JSON-RPC readline loop. Tool schemas and dispatch live under
  `mcp/kuberly-platform/kuberly_mcp/` (`manifest.py`, `dispatch.py`,
  `stdio_app.py`); `render_tool_result` / `_emit_telemetry` stay in
  `kuberly_platform.py` and are injected at startup (avoids `__main__`
  double-import when the script is run as a file).
- **NEW:** `requirements-mcp.txt` — pin range `mcp>=1.10,<2` for consumers.
- **BUMP:** apm.yml 0.29.0 → 0.30.0.

## v0.29.0 — 2026-05-06

- **NEW:** `.kuberly/graph.html` opens on an **operator dashboard** by
  default (KPIs, per-environment cards, cross-env drift, critical
  hubs, module/component/application tables, IRSA map, node spotlight
  with neighbor edges, and inline **shared-infra blast** Mermaid from
  existing `blast_*.mmd`). The full **Cytoscape** compound graph moves
  to a secondary **Graph** tab (lazy-init so the heavy layout runs only
  when needed). Reuses `_compute_dashboard_data` projections — no new
  repo scanners.
- **NEW:** `graph_html_template.py` holds the HTML template;
  `generate` runs `write_mermaid_dag` **before** `write_graph_html` so
  blast diagrams embed.
- **BUMP:** apm.yml 0.28.0 → 0.29.0.

## v0.28.0 — 2026-05-06

- **FIX:** generator non-determinism made the pre-commit
  `regenerate-docs-overlay` and graph-regen hooks flap indefinitely on
  consumer repos — every commit attempt rewrote `docs_overlay.json` /
  `blast_*.mmd` / `graph.json` with different bytes, so the hooks
  always reported "files were modified" and rolled back the commit.
  Three independent root causes:
  1. `parse_hcl_component_refs()` returned `list(set(refs))`. Set
     iteration order depends on `PYTHONHASHSEED`, so HCL
     `component_type:*` edges were inserted in different order across
     runs. Now `sorted(set(refs))`.
  2. `link_components_to_modules()` built `module_names` as a set
     comprehension and iterated it. Same hashseed problem; surfaced
     as `configures_module` edges in different order. Now
     `sorted({...})`.
  3. `docs_graph.build_overlay()` always wrote a fresh `generated_at`
     timestamp. Now compares the validated overlay (sans timestamp)
     against the previous on-disk overlay and preserves the previous
     timestamp when content is unchanged. Idempotent regen.
- **FIX:** mermaid emitters (`module_dag.mmd`, `env_*.mmd`,
  `blast_*.mmd`) now write a trailing newline. Without it,
  `pre-commit-hooks` `end-of-file-fixer` would auto-fix on every
  commit, contributing to the same flap loop.
- Verified: two consecutive `kuberly_platform.py generate` runs against
  stage5 (1308 nodes / 1770 edges) produce **byte-identical** outputs.
  Same for `docs_graph.py generate`.
- **BUMP:** apm.yml 0.27.0 → 0.28.0.

## v0.27.0 — 2026-05-05

- **FIX:** empty-canvas regression on graphs with state + k8s overlays.
  HCL `component_type:*` references, agent doc `tool:*` references,
  `k8s_namespace:*` targets, and state-overlay refs to suppressed
  sensitive resource types all emitted edges to non-existent target
  nodes. Cytoscape aborts on the first such edge ("Can not create edge
  eN with nonexistant target ...") and renders nothing — even though
  the header still shows the right node/edge counts.
  - Filter orphan edges inside `to_json()` (the single chokepoint
    feeding both `write_graph_json` and `write_graph_html`) via a new
    `_serializable_edges()` helper. In-memory `self.edges` is left
    intact so existing query semantics and tests that assert on those
    edges (e.g. `targets_namespace`, `uses_tool`) keep working — only
    the serialized projection is sanitized. On stage5 (1307 nodes /
    4157 edges pre-fix) this drops ~2.4k orphan edges from the output
    and the canvas renders cleanly.
  - Regression test: `test_to_json_strips_orphan_edges`.
- **BUMP:** apm.yml 0.26.0 → 0.27.0.

## v0.26.0 — 2026-05-05

- **BREAKING:** graph artifacts directory `kuberly/` → `.kuberly/` (dot-prefix
  convention, matches `.claude/`, `.cursor/`, `.github/`).
  - Generator default output: `kuberly` → `.kuberly`
  - MCP server overlay loader, SessionStart hook, agent / skill / cursor-rule
    references all updated.
  - Migration for existing v0.25.x consumers:
    ```
    git mv kuberly .kuberly
    python3 apm_modules/kuberly/kuberly-skills/mcp/kuberly-platform/kuberly_platform.py generate .
    ```
- **FIX:** compound parent styling — switched cytoscape selector from
  class-binding (`node.compound`) to pseudo-class (`node:parent`). The
  rounded translucent rect with `--ink-line` border now actually applies to
  compound containers (previously fell through to default fill `#999`).
- **BUMP:** apm.yml 0.25.0 → 0.26.0.

## v0.25.0 — 2026-05-05

- **BREAKING:** graph artifacts relocated from `.claude/` to `kuberly/`. Tool-neutral location so Cursor / Codex / VS Code / future tools share one source of truth.
  - Generator default output: `.claude` → `kuberly`
  - MCP server overlay loader: reads `kuberly/state_overlay_*.json`, `kuberly/k8s_overlay_*.json`, `kuberly/docs_overlay.json`
  - SessionStart hook regen target updated.
  - Migration for existing consumers (one-shot, after `apm install --update`):
    ```
    git mv .claude/graph.html .claude/graph.json .claude/GRAPH_REPORT.md kuberly/ 2>/dev/null
    git mv .claude/*.mmd kuberly/ 2>/dev/null
    git mv .claude/state_overlay_*.json .claude/k8s_overlay_*.json .claude/docs_overlay.json kuberly/ 2>/dev/null
    # then regenerate
    python3 apm_modules/kuberly/kuberly-skills/mcp/kuberly-platform/kuberly_platform.py generate . -o kuberly
    ```
- **FIX:** empty-canvas bug in graph.html — initial `runLayout("fcose")` was never called; all nodes stacked at (0,0). Now invoked after construction.
- **FIX:** compound parent nodes carry `classes: "compound"` so the `node.compound` style selector applies (rounded fill, ink-line border, label faint).
- **BUMP:** apm.yml 0.24.0 → 0.25.0.

## v0.24.0 — 2026-05-05

- **POLISH:** kuberly-graph viz now uses kuberly-web brand tokens.
  - Logo + wordmark in top bar (inline SVG mark from kuberly-web LogoMark).
  - Color tokens map to kuberly-web globals.css: `--bg #090b0d`,
    `--ink #ffffff`, `--blue #1677ff`, `--aws #ff9900`, etc.
  - Layer encoding semantically aligned: static=brand-blue,
    state=AWS-orange, k8s=amber, docs=ink-mute.
  - Geist + JetBrains Mono via Google Fonts CDN with system fallback
    (`font-display: swap`).
  - Subtle dot-grid canvas background (matches kuberly-web
    `.dot-grid-dim`).
  - Sidebar uses card pattern: `bg-card`, `ink-line` border,
    `radius-lg` 22px, modal lift shadow.
- **BUMP:** apm.yml 0.23.0 → 0.24.0.

## v0.23.0 — 2026-05-05

- **NEW:** cytoscape.js compound-node graph viz replaces vis.js force-graph
  in `.claude/graph.html`.
  - Color-coded by source layer (static / state / k8s / docs)
  - Collapsible compound nesting by env -> namespace
  - Layer toggles, fuzzy search, layout switcher (fcose / dagre /
    concentric)
  - Click-to-sidebar with node details + edges + blast-radius highlight
  - k8s layer OFF by default (cuts initial render from 864 to ~100
    nodes)

## v0.22.0 — 2026-05-05

- **BREAKING:** persona rename — `iac-developer` → `agent-infra-ops`,
  `infra-scope-planner` → `agent-planner`, `troubleshooter` → `agent-sre`,
  `app-cicd-engineer` → `agent-cicd`. Skill rename: `infra-orchestrator` →
  `agent-orchestrator`. Consumer repos must update any hardcoded
  `subagent_type` strings or persona references after `apm install`.
- **NEW:** `agent-k8s-ops` persona — read-only live-cluster Kubernetes
  operator (distinct from `agent-sre`). Reports on running workloads, helm
  releases, ServiceAccount-to-IAM-role wiring via the k8s overlay graph and
  IRSA bindings. Writes `k8s-state.md`. Added to the `incident` DAG's
  `diagnose` phase alongside `agent-sre` and `agent-planner`.
- **FIX:** graph indexer false-positive — modules deployed directly via
  `terragrunt apply` (with state in `state_overlay.deployed_modules` but
  no `components/<env>/<x>.json` invoker) are no longer reported as
  `stop-no-instance`. The actionability predicate in `quick_scope` and
  `plan_persona_fanout` now recognizes `source="state"` component nodes
  even when `link_components_to_modules` cannot label-match them to the
  module.
