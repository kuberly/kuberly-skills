# Changelog

## v0.38.1 — 2026-05-06

- **FIX:** `scripts/render_apps.py` was running `cue cmd dump` directly,
  but the consumer's CUE module needs a **two-step workflow** (mirroring
  `cue/generate.sh`): first `cue import -f -l 'config:' -p applications
  <json> -o config_gen_<file>.cue`, then `cue cmd -t instance=<ns> -t
  app=<name> dump .`, then cleanup the generated config file. Without
  the import step, `config: _` in `app.cue` is never bound and the dump
  emits an empty stream. After the fix, the stage5 prod stack renders
  17 manifests across 3 apps (Deployment / Service / ServiceAccount /
  ExternalSecret / VirtualService / AuthorizationPolicy).
- **FIX:** the `app` tag passed to `cue cmd dump` must be the JSON's
  internal `name` field, not the file stem. New `_extract_app_meta()`
  pulls `name` and `namespace` from the JSON (handles both
  `<top>.name` and `<top>.common.name` shapes).
- **NEW:** PyYAML used when available — full YAML parsing instead of
  the regex fallback. Pulls per-Deployment replicas, container ports,
  serviceAccountName from the spec.template tree.
- **BUMP:** apm.yml 0.38.0 → 0.38.1.

## v0.38.0 — 2026-05-06

Per-app rendered manifests now appear as graph nodes.

- **NEW: `scan_rendered_app_nodes()`** auto-loads
  `.kuberly/rendered_apps_<env>.json` (output of the manual
  `scripts/render_apps.py`) and synthesizes:
    - `app_render:<env>/<app>` umbrella node per app
    - `rendered:<env>/<app>/<Kind>/<name>` leaf per rendered manifest
    - Edges: `env → app_render` (contains), `app_render → rendered`
      (renders), `app:<env>/<app> → app_render` (rendered_into)
- **NEW: `rendered` layer** — color teal `#22a1c4`. Topbar pill,
  spotlight chip, dashboard layer-legend pill all wired. New
  `_load_rendered_apps_raw()` keeps per-resource detail for the
  scanner without bloating the dashboard payload.
- **MANUAL COMMAND** to populate the rendered nodes (no auto-run, no
  pre-commit hook):
  ```
  python3 apm_modules/kuberly/kuberly-skills/scripts/render_apps.py \\
    && python3 apm_modules/kuberly/kuberly-skills/mcp/kuberly-platform/kuberly_platform.py generate . -o .kuberly
  ```
- **BUMP:** apm.yml 0.37.0 → 0.38.0.

## v0.37.0 — 2026-05-06

Dashboard restored to a real SaaS layout + Graph view gets dedicated
toggles for the new graph types.

### Dashboard

- **NEW: SaaS hero band** — gradient surface, eyebrow + cluster name
  title, KPI tiles (AWS resources / Findings / State age / Modules /
  Graph) replacing the chip row. Uses the kuberly-web blue/orange
  radial gradient palette.
- **RESTORED: AWS architecture diagram** — layered service tiles
  (Edge / Compute / Data / Network / Identity / Secrets / Registries /
  Ops / k8s) with iconify AWS icons, counts, sample address per tile.
- **NEW: click-to-list drilldown** — clicking any architecture tile
  opens an inline detail panel with **every resource** of that
  service (address, module, env, key essentials), an `open in 3D
  graph →` button, and a close affordance. Click the same tile again
  (or ×) to collapse.
- **RESTORED: Distributions chart row** — Chart.js doughnut for
  category share, bar for IAM trust by principal kind, horizontal
  bar for top resource types.
- Stats & overlays + Node spotlight unchanged from v0.36.0.

### New layer pills in the Graph topbar

- **CUE** — purple `#a266ff`, schema files (`schema:cue/...`)
- **CI/CD** — green `#5fd098`, workflows
  (`workflow:.github/workflows/...`)
- Both layers default ON. Toggle pills filter the 3D graph.
- New `_node_source_layer` returns `"schema"` / `"ci_cd"` for the
  matching nodes; cluster force gives them their own attractor in 3D
  space so the lobes spread cleanly.
- Spotlight layer-filter row gains matching CUE / CI/CD chips so you
  can drill straight into either set.

### Misc

- **BUMP:** apm.yml 0.36.1 → 0.37.0.

## v0.36.1 — 2026-05-06

- **FIX:** v0.36.0 left the `root.innerHTML` template literal unclosed
  (the `</section>` and trailing `` `; `` were dropped during the
  dashboard cut). Result: `Uncaught SyntaxError: Unexpected token
  'class'` at the first JS line after the orphaned HTML, the dashboard
  rendered as a single empty box. Restored the closer.
- **TAGLINE:** "Terragrunt intelligence — drift, blast radius, and live
  overlays in one surface." → **"stack intelligence — IaC, state, live
  cluster, secrets, CI/CD, schemas — one navigable graph."** The page
  is much more than Terragrunt now.
- **BUMP:** apm.yml 0.36.0 → 0.36.1.

## v0.36.0 — 2026-05-06

Radical dashboard simplification + CUE schemas / GitHub workflows now
appear as nodes in the 3D Graph view.

### Dashboard cut

The dashboard collapses to **three sections only**:

1. **Hero** — node / edge / env / module counts.
2. **Stats & overlays** — OpenSpec, docs overlay timestamp, state
   snapshots, doc-linked module ratio, plus *new* counts: CUE
   schemas, GitHub workflows, secret references, rendered manifests,
   app-drift items. Layer pills (`IaC files`, `TG / OpenTofu state`,
   `K8s resources`, `Docs`) carry the per-layer node counts.
3. **Node spotlight** — promoted to top, with usability lift:
   - layer-filter chip row (all / IaC / TG state / K8s / Docs)
   - free-text search now matches id, label, type **and** layer
   - per-row layer dot + type/layer subline
   - **history breadcrumb** (last ≤8 nodes you walked)
   - **"open in 3D graph →"** button that flips to the Graph tab and
     centers the camera on the node

Removed (the data still loads — just no longer rendered as dashboard
sections; the data drives Graph view nodes / edges instead):

- KPI cards (security findings, state age, app health, ...)
- Infrastructure essentials (chart row + AWS architecture diagram)
- Category cards (Compute / Data / Identity / Networking / Secrets /
  Registries / Queues / Kubernetes)
- Security findings tier list
- Module age — last applied heatmap
- IAM identity & access section
- Apps → IAM → Secrets section
- Network reachability — security groups
- Secrets — references and Secrets Manager
- Application manifests — rendered from CUE
- CUE schemas list
- CI/CD — workflows by module
- Coverage & overlays (replaced by Stats & overlays)
- Terraform state overlay tile
- Environments grid
- Most depended-on nodes
- Cross-environment drift columns
- Longest Terragrunt dependency chains
- Shared-infra blast radius (Mermaid)
- IRSA — ServiceAccount → IAM role table
- Modules / Components / Applications tables

### Style alignment with kuberly-web

- KPI accent stripes (`kpi-warn` / `kpi-ok` / `kpi-blue`) removed —
  out of style with `kuberly.io`.
- The colored category-card top stripes are gone with the cards.
- New spotlight uses the same blue / mono / black palette as
  kuberly-web's `globals.css`.

### New graph-view node types

`scan_cue_schema_nodes()` and `scan_workflow_nodes()` synthesize:

- **`cue_schema`** nodes — one per `cue/**/*.cue` file (id
  `schema:cue/<file>`), with `package` + `field_count` attrs.
- **`workflow`** nodes — one per `.github/workflows/*.yml` (id
  `workflow:.github/workflows/<file>`), with `triggers` attr.
- **`references`** edges from each workflow to the
  `module:aws/<m>` and `component:<env>/<m>` it mentions, so the 3D
  graph answers "which CI/CD job deploys this module" by following
  inbound edges from a module node.

For the stage5 prod stack this surfaces 5 CUE schemas + 5 workflows
(adding 10 nodes and 4 references edges).

- **BUMP:** apm.yml 0.35.0 → 0.36.0.

## v0.35.0 — 2026-05-06

Customer-focused dashboard rebuild + new graphs. The headline KPIs and
sections move from "graph metadata" (modules / components / drift) to
"infrastructure that operators care about" (security findings, state
age, app health, IAM trust, secret references, network reachability,
CUE schemas, CI/CD origin, rendered manifests).

### Headline KPIs (replaces Modules / Components / Top Hub)

- **Security findings** — count + severity (high · medium · low). Built
  from schema-v3 essentials: 0.0.0.0/0 SG ingress, unencrypted EBS/EFS,
  publicly_accessible RDS, IAM cross-account trust, federated trust,
  CW log groups with no retention.
- **AWS resources** — count of actually-deployed resources × types ×
  envs.
- **State age** — youngest snapshot age + oldest module's age, so
  operators see at a glance "we applied 28m ago, oldest module is 4d".
- **App health** — running k8s Deployments + StatefulSets with
  replicas vs ready ratio.
- **Applications** — deployed app sidecars (kept).
- **Cross-env drift** — same as before, retitled.

### New dashboard sections

- **Security findings** — three tiers (high / medium / low) with
  rule + detail + module/env, expandable. High auto-opens.
- **Module age — last applied** — heatmap card per module, color-coded
  by snapshot age (fresh < 1d / warm < 1w / cold < 1mo / frozen ≥ 1mo).
- **Apps → IAM → Secrets** — one card per ServiceAccount with IRSA
  binding, showing the workloads using it, the bound IAM role, and
  attached/inline policy counts.
- **Network reachability — security groups** — per-SG ingress and
  egress sources. SGs with `0.0.0.0/0` get a red stripe.
- **Secrets — references and Secrets Manager** — every
  `aws_secretsmanager_secret` cross-referenced with which
  `components/<env>/*.json` files mention its name; orphan refs that
  don't map to a known SM resource flagged separately.
- **Application manifests — rendered from CUE** — auto-loads
  `.kuberly/rendered_apps_<env>.json` (from manual `render_apps.py`)
  + `.kuberly/app_drift_<env>.json` (from manual `diff_apps.py`).
  Empty state shows click-to-copy commands to populate.
- **CUE schemas** — `cue/**/*.cue` files with their top-level field
  declarations + types. Best-effort regex parser, no `cue` binary
  required.
- **CI/CD — workflows by module** — every `.github/workflows/*.yml`
  with the `clouds/aws/modules/...` and `components/<env>/<m>.json`
  references it carries, plus its triggers.

### New standalone scripts (manual run only)

- **`scripts/render_apps.py`** — for each `applications/<env>/<app>.json`,
  invokes `cue cmd dump -t instance=<env> -t app=<n>` against the
  consumer's `cue/` module, parses the YAML manifest stream, writes a
  summary to `.kuberly/rendered_apps_<env>.json`. **Explicitly NOT
  invoked by `kuberly_platform.py`, NOT in pre-commit.** Run with:
      `python3 apm_modules/kuberly/kuberly-skills/scripts/render_apps.py`
- **`scripts/diff_apps.py`** — diffs the rendered manifests against the
  live cluster overlay (`k8s_overlay_<env>.json`), writes
  `.kuberly/app_drift_<env>.json` with declared / running / matched /
  missing / extra. Also manual-only.
- The dashboard auto-picks up both files on the next graph regen.

### Click-to-copy

- Generic `.kbd-copy` button class — any `data-copy="..."` button on
  the dashboard copies on click with a "copied ✓" tick.

- **BUMP:** apm.yml 0.34.6 → 0.35.0.

## v0.34.6 — 2026-05-06

- **REPLACE:** the abstract Mermaid "Networking → Compute → Data" flow
  is replaced with a real **AWS-style layered architecture diagram**.
  Each architectural band (Edge / Compute / Data & Storage / Networking
  / Identity & Access / Secrets / Registries / Observability /
  Kubernetes) holds service tiles with the proper **AWS service icon**
  (iconify CDN — `logos:aws-eks`, `logos:aws-rds`, `logos:aws-iam`,
  `logos:aws-vpc`, `logos:aws-s3`, etc.), service label, count, and a
  sample resource address.
- **NEW:** `_compute_architecture` Python helper buckets every
  `resource_node` into a `(layer, service_label, icon)` triple via the
  `_ARCH_RULES` table — covers ~40 AWS resource types plus Helm /
  Kubernetes provider resources.
- **NEW:** clicking a tile switches to the **Graph** view filtered to
  that resource_type — the architecture overview becomes a launchpad
  into the 3D explorer. Wired via `window.__kuberlyFilterByResourceType`.
- **CDN:** adds `iconify-icon@2.1.0` (~3KB) for on-demand SVG icon
  loading.
- **BUMP:** apm.yml 0.34.5 → 0.34.6.

## v0.34.5 — 2026-05-06

- **FIX:** the click-to-copy command on the empty IAM-trust chart was
  missing the **`--resources`** flag, so users running it produced a
  schema-1 overlay (module list only, no per-resource attributes, no
  `essentials`) and the chart stayed empty. Updated to:
  `state_graph.py generate --env prod --resources --output ...
  && kuberly_platform.py generate . -o .kuberly` — schema 2/3 + dashboard
  rebuild in one shot.
- **BUMP:** apm.yml 0.34.4 → 0.34.5.

## v0.34.4 — 2026-05-06

- **RENAME:** dashboard chart "IAM trust principals" → **"IAM role
  trust — by principal kind"** for clearer intent.
- **NEW:** when the IAM trust chart is empty (state overlay still on
  schema v2, no `principals` extracted), the placeholder now shows a
  **click-to-copy** button with the full `state_graph.py generate`
  command. Click → command goes to clipboard with a "copied ✓"
  acknowledgement; on `file://` (where the Clipboard API may be
  blocked), the button text is select-all'd so the operator can ⌘C
  manually.
- **BUMP:** apm.yml 0.34.3 → 0.34.4.

## v0.34.3 — 2026-05-06

Layer-pill rename + recolor, filter-panel UX fixes.

### Graph layer pills (topbar)

- **RENAME:** layer pills now read **"IaC files"** (was *static*),
  **"TG / OpenTofu state"** (was *state*), **"K8s resources"** (was
  *k8s*), **"Docs"** (was *docs*). Tooltips on each pill explain what
  the layer means.
- **RECOLOR:** k8s layer dot/legend goes from **amber `#d89614`** to
  **dark red `#e44d4d`** (new CSS var `--k8s-red`) — amber was too
  close to the `state` orange and made the two layers indistinguishable
  in screenshots. Sidebar `.chip.layer-k8s` and the dashboard layer
  legend pick up the new color.
- **NEW:** `LAYER_LABELS` map + new `.ll-pill` styling for the
  Dashboard's *Coverage & overlays* layer legend — each layer gets a
  colored dot and a tooltip describing what it actually represents.

### Filter-panel UX

- **FIX:** filter panel now anchors to the actual bottom edge of the
  topbar (measured via `getBoundingClientRect`) instead of the
  hardcoded 56 px. Topbar is `flex-wrap: wrap`; with the v0.34.2
  controls (group-by select + filters toggle + reset) it wraps to a
  second row on narrow viewports — the old hardcoded offset hid the
  wrapped controls under the panel and left no way to close it.
- **NEW:** dedicated **×** close button in the panel header.
- **NEW:** **Esc** closes the filter panel first; pressing Esc again
  (or once when no panel is open) clears search/blast/sidebar like
  before.
- **NEW:** clicking outside the panel/toggle closes it.
- Window-resize re-pins the panel to the new topbar edge while open.
- **BUMP:** apm.yml 0.34.2 → 0.34.3.

## v0.34.2 — 2026-05-06

Filtering / grouping / IAM detail. The Graph view gets explorer-style
controls; the Dashboard gets a dedicated IAM section.

### Graph view — filters + group-by

- **NEW:** **Group by** selector — `source_layer` (default), `environment`,
  `node type`, `resource type`, `module`, `provider`. Drives both node
  color (palette mapped from a stable hash) and the cluster-force
  attractor positions, so swapping axes reflows the layout.
- **NEW:** **Filters panel** (toggle button in the Graph topbar) with
  multi-select chip lists for **Environment**, **Module**, **Resource
  type**, **Node type**. Empty filter set = pass-through; populated set
  = whitelist. Counts shown next to each chip so big buckets are
  obvious.
- **NEW:** **Reset** button clears all filters + search + blast and
  recenters the camera.
- **REFACTOR:** Cluster offsets are now recomputed every time data or
  group-by changes — `recomputeClusterOffsets()` distributes group
  centroids on a circle (`R = 280..360` based on N), with z-jitter so
  groups don't collapse into a flat plane. Skipped when `N > 12`
  (would just produce a uniform spread).
- `applyDataAndRefresh()` now reheats the d3 simulation so nodes
  migrate to their new cluster positions on filter / group-by change.

### Dashboard — IAM identity & access section

- **NEW:** Top-level **"IAM identity & access"** section between
  *Infrastructure essentials* and *Coverage*. Roles grouped by source
  module, each module a collapsible card listing every role with name,
  env, attached + inline policy counts, and trust-principal pills
  (color-coded by kind: service / aws / federated). OIDC providers and
  IRSA bindings (k8s ServiceAccount → AWS role) shown below.
- **NEW:** `_compute_iam_view` aggregates roles + attachments + OIDC +
  IRSA into one payload — works whether or not schema-v3 essentials
  are loaded. Without essentials, every role still appears with its
  address / module / env; principals/policies show "regen state for
  trust principals" hint instead of being silently empty.
- **NEW:** "IAM trust principals" chart shows a helpful placeholder
  when the principal_kinds totals are empty (state overlay still on
  schema v2) — directs the operator to **`state_graph.py generate`**.

- **BUMP:** apm.yml 0.34.1 → 0.34.2.

## v0.34.1 — 2026-05-06

Visual polish on the v0.34.0 3D graph — the dim, scattered cluster
becomes a bright, clustered, firing neural network.

- **VISUAL:** node radius scaling **`nodeRelSize`** 3.4 → **7**;
  per-node weighted value 2 → 2 + degree*0.5 (cap 16). Big high-degree
  hubs read clearly even from a wide camera.
- **VISUAL:** **`nodeOpacity`** 0.92 → **1.0**; **`linkOpacity`**
  0.55 → **0.75**; **`linkWidth`** 0.6 → **1.4**. Bolder lines, more
  contrast against the dark background.
- **VISUAL:** **`linkDirectionalParticles(2)`** + per-link spike color
  picked from a vibrant 8-color neon palette via stable endpoint hash.
  Different "neural pathways" glow in different colors.
- **VISUAL:** global firing pulse (220 ms tick) modulates particle
  width on a sine wave (1.2 → 3.4 px) and rotates the spike palette
  every 4 ticks so the whole network reads as continuously firing.
- **LAYOUT:** custom **`d3Force("cluster")`** pulls each `source_layer`
  toward its own attractor in 3D space — `static`, `state`, `k8s`,
  `docs` separate into their own lobes instead of one fuzzy ball.
  Charge raised -220 → -380, link distance 60 → 38 so each lobe
  packs densely.
- **CAMERA:** initial fly to `z=520` after 400 ms so the cluster fills
  the viewport instead of looking like a tiny dot.
- **BUMP:** apm.yml 0.34.0 → 0.34.1.

## v0.34.0 — 2026-05-06

Major UX overhaul of `.kuberly/graph.html` — both the **Graph view** and
the **Dashboard** are rewritten to surface infrastructure essentials
operators (and customers) actually want to read.

### Graph view — 3D force-directed (3d-force-graph)

- **REPLACE:** Cytoscape (2D, concentric layout) is replaced with
  **3d-force-graph** (three.js + d3-force-3d). The stack now reads as a
  floating spherical/galactic structure with edges *inside* the volume
  instead of stacked rings on a flat plane.
- d3 force tuning: `charge.strength = -220`, `link.distance = 60` so
  clusters spread out into a low-density gas instead of crushing
  together. Drag interaction enabled. Node label tooltips on hover.
- Layer toggles (static / state / k8s / docs) and the overview / full
  view-mode dropdown rebuild **`graphData`** instead of toggling
  cytoscape classes — no stale layout state.
- Search re-colors matched nodes via the `nodeColor` callback; non-hits
  dim. Enter pans+camera-flies to the first match.
- Sidebar with attrs / incoming / outgoing / **blast radius** is
  preserved; blast does a BFS over upstream/downstream and recolors
  via the same `nodeColor`/`linkColor` callbacks. ESC clears.
- Window-resize listener calls **`Graph3D.width(...).height(...)`**
  on viewport change (DevTools open / close, etc.).

### Schema v3 — whitelisted attribute extraction

- **NEW:** `state_graph.py` schema_version bumped 2 → 3. Schema 2
  remains accepted; schema 3 adds an OPTIONAL `essentials` field per
  resource — a tightly whitelisted projection of `instance.attributes`
  (sizes, instance classes, versions, IAM trust principals, EBS GB,
  EFS modes, SG rule CIDRs, etc.).
- **`_ESSENTIALS_WHITELIST`** is the security boundary. Per resource
  type, only the listed keys are projected through; everything else is
  dropped before the field is built. Supports ~30 AWS types
  (EKS / RDS / Aurora / ElastiCache / EBS / EFS / S3 / IAM / VPC /
  NAT / SG + rules / Lambda / KMS / ECR / SQS / CloudWatch / EventBridge
  / CloudFront).
- **`_extract_iam_principals`** parses `assume_role_policy` and emits
  only the principals list (`service:eks.amazonaws.com`,
  `aws:arn:...:role/x`, `federated:arn:...:oidc-provider/y`). Actions,
  Conditions, and the raw policy body NEVER pass through.
- **`_sanitize_essential`** caps strings at 512 chars, lists at 50,
  shallow dicts at 16 keys. Hand-edited overlays attempting to smuggle
  giant blobs are truncated through `_validate_essentials_field`.
- Sensitive resource types (`kubernetes_secret`, `random_password`, TLS,
  etc.) bypass the harvester entirely — even if a type is whitelisted,
  if it's in **`_SENSITIVE_RESOURCE_TYPES`** the essentials block is
  not built.

### Dashboard — category cards + charts + flow

- **NEW:** Eight category cards (Compute / Data / Identity / Networking
  / Secrets+KMS / Registries / Queues+Logs / Kubernetes) with
  color-coded top stripes, headline counts, kind chips, and an
  expandable drill-down body listing each resource with its
  whitelisted essentials (e.g. `db.t4g.medium · postgres 16.3`,
  `100 GB · gp3`, `service:eks.amazonaws.com · aws:arn:...`,
  `0.0.0.0/0 (ingress 443)` flagged red).
- **NEW:** Three **Chart.js** charts above the cards — doughnut
  (category share of resources), bar (IAM principal-kind distribution),
  horizontal bar (top resource types).
- **NEW:** Mermaid **flow diagram** "Networking → Compute → Data" with
  Identity / Secrets / Registries fanning into Compute, counts pulled
  live from `categories` so an empty bucket still renders a `0` node.
- IAM principals get color-coded pills by kind (service / aws /
  federated). 0.0.0.0/0 SG rules emit red finding pills both at the
  card level and the row level.

### Misc

- **NEW:** SVG favicon (data URI of the kuberly LogoMark) — the tab
  no longer shows the generic globe placeholder.
- **REMOVE:** Cytoscape and the v0.33.x concentric layout pipeline
  (`concentricLayoutOpts`, `sanitizePositions`, etc.) are gone.

### Tests

- 8 new tests in `StateGraphResourceExtractTests` cover the schema-v3
  extractor (EKS cluster, IAM trust principals, SG rule CIDRs,
  random-attr redaction, sensitive-type skip, schema-3 validator,
  oversized-blob cap).
- `test_graph_html_dashboard_categories` asserts the category cards
  + Chart.js + favicon + flow diagram all land in the rendered HTML.
- `test_graph_html_has_3d_force_graph` asserts cytoscape is gone and
  `ForceGraph3D` + `d3Force` tuning are present.
- Total: 135 tests pass (was 127).

- **BUMP:** apm.yml 0.33.2 → 0.34.0.

## v0.33.2 — 2026-05-06

- **FIX:** **`graph.html`** — empty Graph canvas, second pass. The v0.33.1 hotfix
  removed the 3D float wrapper but the canvas was still empty. Diagnostic from
  the live page (`cy.zoom() = 2.16e-15`, `extent.w = 3.3e17`) showed at least
  one node was being placed at ~10¹⁷ pixels, collapsing **`cy.fit`** zoom to
  near-zero so every other node rendered sub-pixel.

  Three changes close the loop:
  1. **Defer the layout** — drop **`layout: concentricLayoutOpts`** from the
     **`cytoscape({...})`** constructor. Building the graph runs the layout on
     all 1308 nodes including the 800+ k8s nodes that are about to be hidden,
     and that first **`fit:true`** locks in the broken zoom before
     **`applyLayerVisibility("k8s", false)`** runs. **`runLayoutImpl()`** now
     handles the only layout pass, after visibility is applied.
  2. **Hard-cap radius math** — add **`boundingBox: { x1:0, y1:0, w:4000,
     h:4000 }`** to the concentric options and clamp the **`concentric`**
     callback to **`Math.min(degree, 100)`** with a **`Number.isFinite`** guard
     so a pathological degree value can't compound through the radius
     accumulation.
  3. **Fit and layout only on visible elements** — **`runLayoutImpl`** uses
     **`eles: cy.elements(":visible")`**, and every **`cy.fit()`** site
     (constructor, **`setView`** rAF, **`viewSel`** change, window resize)
     now passes **`cy.elements(":visible")`** so a frozen-in extreme position
     on a hidden k8s node can't influence the viewport.

  Plus a post-layout **`sanitizePositions()`** that recenters any node whose
  coordinates exceed **`SAFE_COORD = 1e5`** or are non-finite — last line of
  defense against future regressions in the layout math.

- **BUMP:** apm.yml 0.33.1 → 0.33.2.

## v0.33.1 — 2026-05-06

- **FIX:** **`graph.html`** — empty Graph canvas regression. The v0.33.0 3D
  "neural float" wrapper (**`#cy-3d-stage`** + **`#cy-3d-float`** with
  **`perspective: 1680px`**, **`transform-style: preserve-3d`**, and the
  **`kuberlyNeuralFloat`** keyframe rotating up to **rotateX 12°** /
  **rotateY 18°** / **translateZ 36px**) rendered the cytoscape canvas onto a
  transformed plane while **`cy.fit()`** computed in untransformed pixels —
  nodes ended up outside the visible perspective frustum and the canvas
  appeared blank. Removed the rotation/perspective stack, kept the structural
  wrappers, and dropped **`transform: translateZ(0)`** + **`backface-visibility:
  hidden`** on **`#cy`**. Layout badge now reads **`concentric`**.
- **FIX:** **`graph.html`** — added a debounced **`window.resize`** listener that
  calls **`cy.resize()`** + **`cy.fit()`** while the Graph view is active, so
  opening / closing DevTools (or any viewport change) re-fits the canvas
  instead of leaving stale layout positions off-screen.
- **TEST:** assert **`kuberlyNeuralFloat`** and **`perspective: 1680px`** stay
  out of the rendered HTML — regression guard.
- **BUMP:** apm.yml 0.33.0 → 0.33.1.

## v0.33.0 — 2026-05-06

- **CHANGE:** **`graph.html`** — graph tab uses **concentric** layout only (built-in
  Cytoscape layout). Removed **fcose** / **dagre** CDN extensions. Overview vs full
  still filters elements; both views use concentric with tuned **spacingFactor** /
  **padding** for dense stacks.
- **FEATURE:** **3D “neural float”** — the Cytoscape canvas sits in **`#cy-3d-stage`**
  with CSS **perspective** and a slow **`kuberlyNeuralFloat`** keyframe (gentle
  **rotateX** / **rotateY** / **translateZ** / **translateY**). Respects
  **`prefers-reduced-motion`**. Layout badge shows **concentric · 3D**.
- **CHANGE:** Removed OpenSpec-oriented slash commands **`opsx-apply`**, **`opsx-archive`**, **`opsx-explore`**, **`opsx-propose`** from the default **`.apm/cursor/commands/`** pack (they confused customer forks). OpenSpec workflow remains in **skills** (`openspec-changelog-audit`, orchestrator OpenSpec gate, etc.).
- **NEW:** Customer day-to-day slash commands — **`/kub-repo-locate`**, **`/kub-pr-draft`**, **`/kub-apply-checklist`**, **`/kub-obs-triage`** (plus existing **`/kub-stack-context`**, **`/kub-plan-review`**, **`/kub-graph-refresh`**).
- **DOCS:** **`agent-orchestrator`**, **`openspec-changelog-audit`**, **`revise-infra-plan`**, **`README`**, **`apm-skills-bootstrap`** — dropped **`/opsx:*`** references; point to CLI / org OpenSpec paths instead.
- **FIX:** **`sync_agent_commands.sh`** — delete **`*.md`** in **`.cursor/commands/`** and **`.claude/commands/`** that are no longer shipped under **`.apm/cursor/commands/`** (so removed prompts do not linger after **`apm install`**).
- **BUMP:** apm.yml 0.32.8 → 0.33.0.

## v0.32.8 — 2026-05-06

- **FIX:** **`.apm/cursor/commands/kub-graph-refresh.md`** — drop Markdown hard-break
  trailing spaces so consumer **pre-commit** `trailing-whitespace` does not rewrite
  synced **`.cursor/commands/`** / **`.claude/commands/`** on every commit.
- **BUMP:** apm.yml 0.32.7 → 0.32.8.

## v0.32.7 — 2026-05-06

- **CHANGE:** Slash **commands** (OpenSpec **`/opsx-*`** and operator **`/kub-*`**) now
  live only under **`.apm/cursor/commands/`** in this package. **`post_apm_install.sh`**
  runs **`scripts/sync_agent_commands.sh`**, which copies them into the consumer’s
  **`.cursor/commands/`** and **`.claude/commands/`** (same markdown for Cursor and
  Claude Code). Forks should not maintain duplicate command sources outside APM.
- **BUMP:** apm.yml 0.32.6 → 0.32.7.

## v0.32.6 — 2026-05-06

- **FIX:** Cursor **`.cursor/hooks.json`** — **`beforeSubmitPrompt`** entries must be
  **flat** ``{ "command": "...", "timeout": … }`` (not a nested ``hooks`` array);
  Cursor validates ``beforeSubmitPrompt[0].command`` as a string. Matcher cleanup
  in **`sync_claude_config.py`** now recognizes both flat and nested kuberly-owned
  hooks.
- **FEATURE:** **`graph.html`** — **Overview** mode: Terragrunt **module** nodes and
  **depends_on** edges only (default when ≥280 leaf nodes; persisted in
  **sessionStorage**). **Full graph** still available from the scope dropdown;
  overview uses **dagre** (LR) for a readable “constellation” layout. UI listeners
  wire once so view switching rebuilds Cytoscape without duplicate handlers.
- **BUMP:** apm.yml 0.32.5 → 0.32.6.

## v0.32.5 — 2026-05-06

- **FIX:** **`ensure_apm_skills.sh`** snapshots **`apm.lock.yaml`** via a temp
  file (**`KUBERLY_LOCK_BEFORE_PATH`**) instead of a shell variable (avoids
  stripping trailing newlines). **`post_apm_install.sh`** restores the snapshot
  with **`cp`** when only non-semantic bytes differ after **`apm install`**,
  so pre-commit stops failing on **`generated_at`**-only churn.
- **BUMP:** apm.yml 0.32.4 → 0.32.5.

## v0.32.4 — 2026-05-06

- **FIX:** **`post_apm_install.sh`** — lockfile drift check ignores **`generated_at`**
  so `apm install` does not force a second commit when only that timestamp
  changes. Graph **`generate`** is skipped when **`PRE_COMMIT=1`** (unless
  **`KUBERLY_GRAPH_ON_HOOK=1`**) so hooks do not rewrite **`.kuberly/*.mmd`**
  on every commit; set **`KUBERLY_SKIP_GRAPH_ON_HOOK=1`** to skip generation
  outside pre-commit too.
- **FEATURE:** **`graph.html`** dashboard — **Terraform state overlay** section:
  per-env snapshot time, component counts, static∩state vs state-only, resource
  node counts, and top Terraform resource types from the merged graph.
- **BUMP:** apm.yml 0.32.3 → 0.32.4.

## v0.32.3 — 2026-05-06

- **FIX:** `.kuberly/graph.html` **Graph** tab — for large stacks (≥500 leaf
  nodes), **strip compound parents** and run **cose** instead of **fcose** so
  layouts are not collapsed into unusable white boxes / diagonal lines.
  **fcose** uses **draft** quality when there are many nodes; added **cose**
  as an explicit layout option.
- **FIX:** **Dashboard** shared-infra **Mermaid** blast — cap diagram size,
  sanitize labels, higher **`maxTextSize`**, collapsed blast **`<details>`**
  by default, and safer **`mermaid.run`** error handling.
- **BUMP:** apm.yml 0.32.2 → 0.32.3.

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
