# Changelog

## v0.51.0 — 2026-05-08

Observability layers actually populate; rendered apps inline.

Phase 8H shipped logs / metrics / traces collection that was
technically wired but sparse on the live cluster (`metrics=0`,
`logs=24`, `traces=4`) because the upstream MCP wrapper had no
Prom / Loki / Tempo URL configured. v0.51.0 adds a guaranteed
fallback path: when MCP returns `isError` / 0 results we shell out
to `kubectl port-forward` and hit the in-cluster service directly.
All three layers share one `contextlib.contextmanager` that owns
the subprocess lifecycle, so every PF dies on layer-end (no
orphaned port-forwards). Same architectural principle as v0.50.1's
state-inline: every layer writes nodes / edges straight to the
store; no sidecars.

- **NEW: `layers/_pf.py`** — shared `kubectl_port_forward()` ctx
  manager + tiny stdlib HTTP helpers. Single cleanup invariant for
  every observability layer.
- **CHG: `MetricsLayer.scan()`** — try MCP first; on 0 results /
  `isError`, kubectl-PF to `svc/prometheus-kube-prometheus-prometheus
  -n monitoring 9090` and scrape `/api/v1/label/__name__/values` +
  `/api/v1/metadata` + `/api/v1/targets`. `metrics_use_kubectl_pf`
  now defaults to **on**. Soft-degrades when kubectl missing / no
  current-context / PF can't bind. `metrics_top_n` default raised
  200 -> 1000.
- **CHG: `LogsLayer.scan()`** — discover labels first via Loki
  `/loki/api/v1/label/namespace/values` then iterate per-namespace
  LogQL. PF target: `svc/loki-gateway -n monitoring 80`. Per-ns cap
  via new `logs_per_namespace_limit` ctx (default 1000 lines).
- **CHG: `TracesLayer.scan()`** — 3-path service discovery
  (existing app ids -> Prom `traces_spanmetrics_calls_total` ->
  k8s `Service` names) augmented with a Tempo
  `/api/search/tag/service.name/values` lookup over the PF.
  Per-service trace fetch via `/api/search?tags=service.name=<svc>`.
  PF target: `svc/tempo-query-frontend -n monitoring 3200`.
- **CHG: `RenderedLayer.scan()`** — `cue` is now invoked inline.
  Discover apps under `applications/<env>/*.json`, stage each JSON
  via `cue import` into `cue/`, run `cue cmd -t instance=<env>
  -t app=<name> dump .`, parse the multi-doc YAML, emit
  `app_render:` + `rendered_resource:` nodes. Drop the
  `.kuberly/rendered_apps_<env>.json` sidecar entirely. Soft-
  degrades when cue absent / no `cue/` dir / `applications/`
  missing / cue runs fail.

Tool count unchanged at **50**. Layer count unchanged at **25**.

## v0.50.1 — 2026-05-08

Fold S3 state extraction inline — drop sidecar JSON.

Phase 8H landed state extraction as a standalone module
(`state_extract.py`) that wrote a `.kuberly/state_<env>.json` sidecar,
and `StateLayer` then re-parsed that JSON. That was a wasteful two-step
indirection, inconsistent with every other layer (which writes nodes /
edges directly to the LanceDB store via its `scan()`).

- **CHG: `StateLayer.scan()` is now self-contained.** boto3 S3 fetch +
  tfstate parsing live inside `layers/state.py`. Per-env soft-degrade on
  missing `shared-infra.json` / no AWS creds / `ClientError`; per-module
  soft-degrade on `NoSuchKey` / corrupt JSON. New node ids:
  `tf_state_module:<env>/<rel>` + `tf_state_resource:<env>/<rel>/<addr>`.
  New cross-layer edges: `module:<provider>/<name> -> tf_state_module`
  (`has_state`), `tf_state_module -> tf_state_resource` (`contains`),
  `tf_state_resource -> aws:<service>:<id>` (`tracks`, best-effort match
  against AwsLayer ids cached in ctx).
- **DEL: `kuberly_graph/state_extract.py`** — gone. No more sidecar
  writer.
- **DEL: `extract_state_sidecar` MCP tool** — operators just call
  `regenerate_layer state` (or `regenerate_all`). Tool count: 51 -> 50.
- **DEL: `auto_extract_state` ctx flag** in `regenerate_graph()` —
  extraction is intrinsic to the layer scan now.
- **DEL: `.kuberly/state_<env>.json`** sidecar — never written; never
  read.

Verified on Traigent dev (account 340334787933, eu-west-1):
`regenerate_layer state` -> **992 nodes / 1091 edges** (Phase 8H
baseline was 824 / 944; the extra count comes from the new
`tf_state_module` per-module nodes + `has_state` cross-layer edges).
`tools/list` = **50**, `list_layers` length = **25** (unchanged).
No `.kuberly/state_*.json` written. `graph_stats` total =
**7442 nodes / 5461 edges**.

## v0.50.0 — 2026-05-08

Phase 8H: TreeSitterLayer + S3 state extractor + Loki / Tempo response
unwrap fixes + opt-in kubectl-port-forward path for the metrics layer.

- **FEAT: `kuberly_graph.layers.treesitter.TreeSitterLayer`** — new AST
  layer over the consumer's `clouds/` tree using `tree_sitter_languages`
  (HCL / YAML / Dockerfile / JSON; CUE soft-degrades with regex when the
  bundled wheel lacks the grammar). Emits `hcl_resource:` /
  `hcl_data:` / `hcl_module_call:` / `hcl_variable:` / `hcl_output:` /
  `hcl_locals:` / `cue_definition:` / `cue_field:` / `yaml_manifest:` /
  `dockerfile_step:` / `dockerfile_base_image:` nodes plus `declares` /
  `uses_var` / `refs` edges. Caps: 5000 files per glob (configurable via
  `treesitter_max_files`), 1 MiB per file, walk depth 8. Wired into
  `LAYERS` after `code` (provides `module:` ids for the `declares`
  edges) and before `dependency`. Verified on Traigent IaC — **4372
  nodes / 3671 edges** from 137 HCL/YAML/Dockerfile files in ~50 s.
- **FEAT: `kuberly_graph.state_extract.extract_states_from_s3`** — pure
  `boto3.s3.get_object` reader. Reads `components/<env>/shared-infra.json`
  for the `${account}-${region}-${cluster}-tf-states` bucket name, walks
  every `clouds/<provider>/modules/<name>/terragrunt.hcl` to extract
  `key = "..."` (or fall back to the `<provider>/<name>/terraform.tfstate`
  convention), pulls each tfstate, and writes the resource side-car to
  `.kuberly/state_<env>.json`. Soft-degrades on missing `boto3`, missing
  AWS creds, missing bucket, or per-module `NoSuchKey`. Verified on
  Traigent IaC — **24 modules extracted, StateLayer follow-up emits
  824 nodes / 944 edges**.
- **FEAT: `extract_state_sidecar` MCP tool** — pulls every module's
  tfstate so `regenerate_layer state` has data to ingest. Auto-detects
  the env from `components/<env>/`. Also wired into `regenerate_graph`
  via `auto_extract_state=True` (default) so a one-shot
  `regenerate_all` no longer needs an explicit pull step.
- **FEAT: `find_resource_callers` / `module_io_summary` /
  `find_yaml_manifest_kind` MCP tools** — pure GraphStore queries over
  the new TreeSitter nodes/edges. `find_resource_callers` does a
  depth-bounded reverse BFS along `uses_var` / `refs` / `reads_output` /
  `declares`; `module_io_summary` counts declared HCL kinds per module;
  `find_yaml_manifest_kind` filters `yaml_manifest:*` nodes by Kind.
- **FIX: TracesLayer ignored Tempo's `[Tempo HTTP /api/search ...]\n<json>`
  text wrapper** — the ai-agent-tool MCP returns Tempo / Loki responses
  as a tagged plaintext block, not JSON. Added `_maybe_unwrap_text_payload`
  helper that strips the leading bracket-tag line and JSON-decodes the
  body. The Tempo `spanSet.spans` shape (with OTLP-style `attributes`
  list and root-trace service hoisting via `rootServiceName` /
  `rootTraceName`) is now ingested correctly. Verified — **80 spans /
  40 traces / 4 service nodes / 2 service-call edges** from a 15 m
  window on the Traigent dev cluster.
- **FIX: TracesLayer skipped TraceQL-with-no-service** — the production
  Tempo wrapper rejects `query={}` with "either trace_id or service is
  required". Discovery now goes app-IDs → `traces_spanmetrics_calls_total`
  → `k8s_resource(Service)` and queries per-service. Falls back to the
  legacy TraceQL form only when every discovery path is empty (so the
  operator sees the upstream error).
- **FIX: LogsLayer dropped `[Loki via logcli ...]\n<lines>` text payload**
  — added `_parse_logcli_text` to recover the timestamp / labels / line
  triple from each entry. Loki LogQL fallback chain is now: env-tag →
  per-namespace → `{job=~".+"}` → `{app=~".+"}`, with namespace seeds
  pulled from already-populated `k8s_resource:*` nodes. Verified —
  **24 log_template nodes** from 117 ingested lines on the Traigent
  dev cluster.
- **FEAT: MetricsLayer opt-in kubectl-port-forward fallback** — when the
  ai-agent-tool wrapper's Prom upstream is blank ("Prometheus MCP not
  available"), passing `metrics_use_kubectl_pf: true` tells the layer to
  spawn `kubectl port-forward -n monitoring svc/prometheus-kube-prometheus-prometheus
  0:9090`, parse the random local port from kubectl's stderr, and hit
  the in-cluster Prom directly via `urllib`. Cleanup is via a
  `contextlib.contextmanager`. Off by default to keep the no-shell
  invariant intact for routine runs.
- **DEP: pinned `tree_sitter==0.21.*` + added `tree_sitter_languages>=1.10.2`**
  to `pyproject.toml`. The wrapper wheel only loads against
  tree-sitter 0.21.x; newer 0.22+ breaks `Language()` init.
- **Layer count: 24 → 25.** **Tool count: 47 → 51.**

## v0.49.0 — 2026-05-08

Phase 8G: render-fix + AWS lazy-init audit + KubectlLayer (full-RBAC k8s scan).

- **FIX: dashboard 3D Graph tab rendered black canvas** — the
  `/api/v1/graph` payload uses `edges`, but `3d-force-graph` expects
  `links`. The client `renderGraph()` already mapped `edges → links` and
  filtered cross-window references, but the canvas was being initialised
  while the Graph tab host was still `display:none`, so
  `host.clientWidth/clientHeight` were both 0 and the WebGL renderer
  never resized itself afterwards. Fixed by booting with viewport
  fallback dimensions, then `resyncGraphSize()` on tab-switch +
  `ResizeObserver` so the canvas tracks the host once it's actually laid
  out (`dashboard/static/app.js`).
- **FIX: AwsLayer/CostLayer/IAMLayer eager `boto3` work at module
  import** — verified via 5-second silent-boot probe that no
  `Found credentials` / `botocore` / `sts.amazonaws.com` traffic is
  emitted before the first `regenerate_*` tool call. All AWS work is
  inside `scan(ctx)`; module-level imports are stdlib-only. The layer
  classes now construct cleanly without a `boto3` install, and
  soft-degrade with an explicit stderr WARN when invoked.
- **FEAT: `kuberly_graph.layers.kubectl.KubectlLayer`** — new layer that
  shells out to local `kubectl` (`subprocess.run`) using whatever creds
  the operator has on PATH. `kubectl api-resources -o wide --no-headers`
  enumerates every listable kind, then `kubectl get <Kind>.<group> -A
  -o json` per kind populates the graph. Mirrors `K8sLayer`'s metadata
  shape (labels, owner_references, container_images, pvc_claims,
  secret_refs, configmap_refs, node_name / node_class_ref / provider_id,
  spec passthrough for monitoring kinds) and emits CRDs as `crd:<name>`
  nodes with `defines_kind` edges to matching `k8s_resource:` ids. Same
  id namespace as K8sLayer with `source: "kubectl"` so `DependencyLayer`
  Just Works regardless of which scanner populated the node — kubectl
  upserts overwrite the bearer-token entries when both run. Soft-degrade
  chain: missing binary → empty result; failed `kubectl version --client`
  → empty result; failed `kubectl config current-context` → empty
  result; per-kind failures logged + skipped without aborting.
  `kubectl_path` / `kubectl_kubeconfig` / `kubectl_context` /
  `kubectl_per_kind_limit` (default 5000) /
  `kubectl_skip_kinds` (default `["events.k8s.io/Event", "v1/Event"]`)
  / `kubectl_timeout_seconds` are all available via `regenerate_layer`.
- **CHORE: `regenerate_layer` knobs** — `kubectl_path`,
  `kubectl_kubeconfig`, `kubectl_context`, `kubectl_per_kind_limit`,
  `kubectl_skip_kinds`, `kubectl_timeout_seconds` plumbed through
  `extra_ctx`.
- **Layer count: 23 → 24.** Tool count unchanged at **47**. KubectlLayer
  is wired into `_LAYER_PRECEDES` after `k8s` and before `dependency`.

## v0.48.0 — 2026-05-08

AwsLayer — direct boto3 scan of ~25 AWS services. Plug-in for accounts where
no tfstate sidecar is available (or where deployed-actual data is needed) so
the existing `network` / `iam` / `storage` / `dns` layers (which read from
tfstate) can be complemented by a parallel `aws:*` namespace populated
straight from AWS APIs.

- **FEAT: `kuberly_graph.layers.aws.AwsLayer`** — scrapes VPC / Subnet / SG /
  RouteTable / NAT / IGW / VPC Endpoint, EBS / EC2, EKS clusters +
  nodegroups, IAM roles + customer-managed policies + instance profiles,
  S3 (with best-effort encryption / versioning / public-block / policy
  introspection), RDS clusters + instances, ElastiCache, ECR, Lambda,
  ALB / NLB, CloudFront, Route53 hosted zones, ACM certificates, and
  CloudWatch log groups. Emits ~25 service-specific node types under the
  `aws:*` id namespace plus intra-namespace edges (`in_vpc`, `in_subnet`,
  `uses_sg`, `attached_to`, `member_of`, `executes_as`, `has_member`,
  `attaches`, etc.). Per-service cap defaults to 1000 items
  (`aws_per_service_limit`); `aws_services` ctx knob narrows to a subset.
  `boto3` stays an OPTIONAL dep — the layer wraps the import + the STS
  validation in `try/except` and soft-degrades to `(0, 0)` with an explicit
  stderr WARN when the SDK is missing or creds are missing/expired/invalid.
  Per-service `try/except ClientError` so one rejected describe call
  doesn't poison the rest of the run.
- **FEAT: `DependencyLayer` cross-namespace edges for `aws:*`** —
  `aws:iam_role` ↔ `k8s_resource(ServiceAccount)` (relation `bound_to`,
  matched against the SA's `eks.amazonaws.com/role-arn` annotation, full
  IRSA chain), `aws:ec2` → `k8s_resource(Node)` (`runs_as`, matched against
  the node's `spec.providerID`), `aws:ebs` → `k8s_resource(PersistentVolume)`
  (`backs`, matched against `volumeHandle`), `aws:ecr_repo` → `image`
  (`hosts`, matched on registry/repo path), `aws:eks` → `component:<env>/eks`
  (`provisions`, best-effort cluster-name match).
- **FEAT: 2 new MCP tools** — `aws_resource_count_by_service` (group `aws:*`
  by `type`) and `find_aws_resources_in_vpc` (BFS over VPC-relevant edges
  + direct `vpc_id` attribute match). Pure GraphStore queries.
- **CHORE: `regenerate_layer` knobs** — `aws_per_service_limit` and
  `aws_services` plumb through `extra_ctx`.
- Tool count: **45 → 47**. Layer count: **22 → 23**.

## v0.47.0 — 2026-05-08

Dashboard rewrite + persist_dir resolution fix.

- **FEAT: dashboard UI rewrite** — two-tab vanilla HTML/JS/CSS app inspired
  by the stage5 `graph.html` reference: Geist + JetBrains Mono fonts, dark
  slate background, blue accent, coloured "dots" per node category. The
  Dashboard tab shows a per-layer overlays strip, five KPI cards, and an
  AWS architecture-tile section (AWS-shaped resources grouped by service).
  The Graph tab renders the full graph in 3D via `3d-force-graph@1.73.0`
  with filter chips per category (IAC FILES / TG STATE / K8S / APPLICATIONS
  / CI/CD / CUE / DOCS / LIVE / AWS / DEPS / META), search highlight,
  group-by selector (category / layer / type), click-to-detail panel that
  pulls `/api/v1/nodes/<id>/neighbors` and renders a Mermaid neighbourhood
  diagram. CDN libs only — no Python deps added, no build pipeline.
- **FEAT: `/api/v1/graph` REST endpoint** — additive endpoint that returns
  the full nodes+edges payload (default 5000-node cap) the 3D viz needs,
  with a derived `category` field per node mapping each layer/type to one
  of 11 buckets (`iac_files`, `tg_state`, `k8s_resources`, `applications`,
  `ci_cd`, `cue`, `docs`, `live_observability`, `aws`, `dependency`,
  `meta`). MCP tool surface unchanged at 45.
- **FIX: persist_dir mismatch** — when the user ran `kuberly-graph serve
  --repo <path>` from a CWD other than `<path>`, `--persist-dir` (default
  `.kuberly`) resolved against the *server* CWD instead of `<path>`. The
  dashboard then read from an empty `<cwd>/.kuberly` while
  `regenerate_all` had written to `<repo>/.kuberly` — surfacing as
  "graph store appears empty" against an actual 1012-node graph.
  `cli._resolve_persist_dir` now anchors relative `--persist-dir` paths
  against `--repo`, and the resolved absolute path is logged on startup
  (`dashboard reading from: ...`). `dashboard/api.py` no longer re-resolves
  against request-time CWD.

## v0.46.0 — 2026-05-08

Cluster-driven Kubernetes API discovery + a meta-graph layer that turns the
`kuberly-graph` package into a graph-of-graphs. v0.45.1 captured 463 k8s
nodes from a hardcoded ~22-kind list; v0.46.0 enumerates every CRD the
cluster actually exposes and scans every served version of every kind —
producing 800+ k8s nodes against the same `grafana-dev.traigent.ai/mcp`
endpoint (cert-manager, Istio, Karpenter, external-secrets, Argo, Tekton,
AWS Gateway/EBS/EFS controllers, Grafana operator, etc.) plus one new
`crd:<name>` node per CRD wired to the resources it governs via
`defines_kind` edges.

- **FEAT: `kuberly_graph.client`** — new `discover_kinds()` /
  `discover_kinds_sync()` enumerate the live API surface by listing every
  `apiextensions.k8s.io/v1/CustomResourceDefinition` and parsing each CRD's
  YAML spec (`spec.group`, `spec.names.kind`, `spec.scope`,
  `spec.versions[*].name`) without pulling in PyYAML — a tolerant
  indentation walker handles both inline `- name: vX` and block-style
  `- additionalPrinterColumns:` version entries. New `parse_crd_spec_yaml()`
  helper exported for testing. `BUILTIN_K8S_KINDS` (~40 entries) covers the
  standard built-ins discovery doesn't surface — workloads, networking,
  storage, RBAC, autoscaling, scheduling, admission webhooks, API services,
  leases. Discovery soft-degrades on every error path: missing tool, missing
  CRD kind, RBAC forbidden, transport failure all fall back to
  `BUILTIN_K8S_KINDS` with a single stderr WARN.
- **FEAT: `kuberly_graph.layers.k8s.K8sLayer`** — calls `discover_kinds_sync`
  before scanning; emits one `crd:<name>` node per discovered CRD with
  `crd:<name> -> k8s_resource:<...>` `defines_kind` edges to every live
  resource of any of the CRD's served versions. Per-kind cap defaults to
  1000 (configurable via ctx `k8s_per_kind_limit`) to keep huge clusters
  tractable. Cluster-scoped resources now get `<ns>` = `cluster` in the
  node id (was empty string before, breaking some `<env>/<ns>` parsing
  paths on derived layers).
- **FEAT: `kuberly_graph.layers.meta.MetaLayer`** — runs LAST after
  `dependency`. Reads the freshly-populated `GraphStore` and the layer
  registry, then emits one `graph_layer:<name>` node per registered layer
  (carrying `name`, `layer_type`, `refresh_trigger`, `node_count`,
  `edge_count`, `last_refresh`, sorted unique `node_types`) plus
  `feeds_into` edges derived from `_LAYER_PRECEDES` and `summarized_by`
  edges from every other layer to `graph_layer:meta`. Pure introspection —
  no live MCP calls; soft-degrades to empty on missing `graph_store`.
- **FEAT: `kuberly_graph.tools.meta.meta_overview`** — new MCP tool
  returning the persisted `graph_layer` nodes + `feeds_into` /
  `summarized_by` edges + a topological run order over the static layer
  registry + summary counts. Pure GraphStore query — call after
  `regenerate_layer meta` (or `regenerate_all`) for the freshest view.
- **WIRING:** `LAYERS` now ends with `..., DependencyLayer(), MetaLayer()`.
  `_LAYER_PRECEDES["meta"]` lists every other registered layer so topo-sort
  runs MetaLayer last. `list_layers_summary` type_map gets `"meta": "meta"`.
  Tool count: 44 -> **45**. Layer count: 21 -> **22**.

Verified live against `https://grafana-dev.traigent.ai/mcp`:

```
regenerate_layer k8s:   805 nodes / 82 edges (was 463 / 0 in v0.45.1)
                        — 102 CRDs, 154 distinct (apiVersion, Kind), per-kind
                          cap inert at 1000
regenerate_layer meta:   22 graph_layer nodes / 88 feeds_into+summarized_by edges
meta_overview:           layer_count=22, topo_order ends ['...', 'dependency', 'meta']
```

## v0.45.1 — 2026-05-08

Fixes the `kuberly-graph` live-layer scanners against MCP servers that
return kubectl-style **plaintext tables** instead of JSON (notably the
ai-agent-tool wrapper around `manusa/kubernetes-mcp-server`). v0.45.0 sent
~30 successful HTTP 200 POSTs to the live MCP and parsed every response as
empty, leaving every live layer (k8s/argo/logs/metrics/traces) at 0 nodes.
After this release `regenerate_layer k8s` populates 400+ nodes against a
real cluster and all live layers either populate or log a clear soft-degrade
warning when the upstream they need is unavailable.

- **FIX: `mcp/kuberly-graph/src/kuberly_graph/client.py`** —
  - new `parse_kubectl_table()` reconstructs `{apiVersion, kind, metadata:
    {name, namespace, labels, annotations}, spec, status}` records from
    kubectl plaintext tables (single-row + multi-row, namespaced + cluster
    scoped, with embedded comma-separated `LABELS` cells preserved as a
    `dict[str,str]`),
  - `fetch_live_resources()` now surfaces `isError=true` as a per-(api,kind)
    `WARN` line on stderr instead of swallowing it as an empty list, and
    walks tool-name fallbacks (`resources_list` -> `pods_list` /
    `pods_list_in_namespace` / `namespaces_list`) when the primary tool is
    rejected as `unknown tool`,
  - `_normalize_call_result()` is the new spine — separates `is_error /
    error_text / payload / raw_text` so each call site decides whether to
    JSON-decode, plaintext-parse, or log-and-skip,
  - de-duplicates "missing CRD" / "RBAC forbidden" / "tool not found"
    warnings with a per-scan `_SeenWarn` so a 30-kind scan doesn't spam 30
    identical errors.

- **FIX: `mcp/kuberly-graph/src/kuberly_graph/layers/logs.py`** — sends both
  the v0.45.1 `logql` / `since` argument names (the ai-agent-tool wrapper)
  AND the legacy `query` / `start` names, so we match either flavour. Soft-
  degrades on transport errors instead of raising.
- **FIX: `mcp/kuberly-graph/src/kuberly_graph/layers/metrics.py`** — sends
  both `promql` and `query`. Soft-degrades on tool error rather than
  raising. Per-metric `mode=metadata` calls are still attempted but never
  raise when the wrapper rejects them.
- **FIX: `mcp/kuberly-graph/src/kuberly_graph/layers/traces.py`** — when
  the upstream rejects TraceQL `{query: ...}` with a "service is required"
  error, falls back to per-service `query_traces({service: <name>, since,
  limit})` over the existing `application` nodes already in the graph
  (caps at 50 services to bound runtime).
- **FIX: `mcp/kuberly-graph/src/kuberly_graph/tools/regenerate.py`** —
  `regenerate_layer` now uses `_resolve_endpoint`, matching
  `regenerate_graph`, so when an operator passes an explicit `mcp_url`
  whose URL also appears in `<repo_root>/.mcp.json`, we merge the `headers`
  (e.g. `Authorization: Bearer ${VAR}`) from the file. Without this,
  explicit-URL invocations hit `401 Unauthorized` because no bearer ever
  attached.

## v0.45.0 — 2026-05-08

Introduces a brand-new MCP service — **`kuberly-graph`** — under `mcp/kuberly-graph/`. It's a FastMCP-based Python package that builds a **44-tool, 21-layer knowledge graph** spanning IaC, live cluster, observability, security, supply chain, compliance, DNS, secrets, and cost. Backed by **LanceDB** (vector search + auto-embedding via `sentence-transformers/all-MiniLM-L6-v2`) and **rustworkx** (graph algorithms). Ships with a vanilla-JS web dashboard mounted on the same FastMCP HTTP transport. Distributed via `apm install` like every other shared service in this package — no consumer-side scripts required.

Resurrects the historical `kuberly-graph` MCP name that v0.12.0 of `scripts/sync_claude_config.py` used to **strip** from consumer `.mcp.json` files; the strip is removed and the name is now treated as a **canonical entry**, registered automatically across Claude Code / Cursor / OpenCode / VS Code.

- **NEW:** **`mcp/kuberly-graph/`** — FastMCP microservice package (`pyproject.toml`, `Dockerfile`, `README.md`, `src/kuberly_graph/`). Exposes 44 `@mcp.tool()` decorators; runs as `kuberly-graph serve --transport {stdio,streamable-http}`. CLI surface limited to `serve / call / version` — every other operation is an MCP tool. Single `FastMCP("kuberly-graph", version="0.1.0")` instance imported from `server.py`.

- **NEW:** **21-layer scanner pipeline** at `src/kuberly_graph/layers/`:
  - **Cold (on-disk):** `code` (terragrunt modules) · `components` (env JSON) · `applications` (app JSON) · `rendered` (CUE-rendered manifests) · `state` (tfstate sidecar JSON). Plus `cold` meta-alias.
  - **Live (via MCP client):** `k8s` · `argo` · `logs` (Loki templates via stdlib regex clustering) · `metrics` (Prom + scrape targets) · `traces` (Tempo services + operations + p50/p95/p99).
  - **Derived structural:** `network` (VPC/Subnet/SG/NACL/Route/IGW/NAT/VPCEndpoint) · `iam` (roles/policies/IRSA chain) · `image_build` (image refs + optional GHA/ECR enrichment) · `storage` (PV/PVC/StorageClass/EBS/EFS/S3) · `dns` (Route53 + ACM) · `secrets` (ExternalSecret/SecretStore chain) · `cost` (Cost Explorer monthly snapshots — auth-gated, soft-degrades) · `alert` (PrometheusRule + Loki rules) · `compliance` (R001-R007 hardcoded rules over state + k8s).
  - **Capstone:** `dependency` runs last; emits cross-layer edges only — Pod→Deployment/Node, Pod→ReplicaSet/StatefulSet/DaemonSet/Job, Pod→Node→NodeClaim→NodePool→EC2NodeClass (Karpenter), Pod→log_template/metric/service, rendered_resource→k8s_resource, application→argo_app, module→resource, Pod→PVC mount, Pod→Secret/ConfigMap consumption, Ingress→DNS record.

  Layer order resolved via stdlib `graphlib.TopologicalSorter` + `_LAYER_PRECEDES` map. Empty-store-tolerant — every layer returns `(0, 0)` with a logged note when its source data isn't populated.

- **NEW:** **44 MCP tools** at `src/kuberly_graph/tools/`:
  - **Query (4):** `query_nodes` · `get_neighbors` · `blast_radius` · `shortest_path` — implemented over `RxGraph` (rustworkx `PyDiGraph` adapter at `src/kuberly_graph/graph/rustworkx_graph.py`).
  - **Regenerate (4):** `regenerate_graph` · `regenerate_layer` · `list_layers` · `regenerate_all`. The last one is the one-shot full-refresh for operators after `aws sso login` + `kubectl` + ai-agent-tool MCP wiring; auto-discovers the live MCP URL from `<repo_root>/.mcp.json` (looks for `ai-agent-tool` HTTP entry, resolves `${VAR}` headers from env, drops missing-var headers without crash).
  - **Semantic (3):** `semantic_search_graph` · `find_similar_graph` · `graph_stats` — backed by LanceDB's `SentenceTransformerEmbeddings` registry.
  - **Analytics (6):** `find_log_anomalies` · `find_high_cardinality_metrics` · `find_metric_owners` · `find_slow_operations` · `find_error_hotspots` · `service_call_graph`.
  - **Fusion (6):** `service_one_pager` · `find_anomalies` · `cross_layer_search` · `service_mermaid` · `health_score` · `cross_layer_fuse` (capstone — extends `fuse-live` semantics across all layers; writes `<out_dir>/cross_drift_<env>.{md,json}`).
  - **Infra (6):** `find_open_security_groups` · `service_network_path` · `iam_role_assumers` · `irsa_chain` · `find_image_users` · `find_unbound_pvcs`.
  - **Phase 7D (10):** `find_dns_dangling_records` · `service_dns_chain` · `find_secret_consumers` · `find_unused_secrets` · `external_secret_chain` · `cost_summary` · `find_orphan_alerts` · `service_alert_summary` · `compliance_report` · `find_violations_for_resource`.
  - **Image build (2):** `find_image_scan_findings` · `commit_to_image_chain`.

  Tools register via `@mcp.tool()` decorators — FastMCP auto-derives JSON schemas from type hints + docstrings. No hand-rolled `_MCP_TOOLS` dicts.

- **NEW:** **Web dashboard** at `src/kuberly_graph/dashboard/` — vanilla HTML/JS/CSS (no build pipeline) mounted on FastMCP's `streamable-http` transport via `mcp.custom_route()`. Routes:
  - `GET /dashboard` — SPA shell.
  - `GET /dashboard/static/<file>` — path-traversal-safe static file server.
  - `GET /api/v1/{layers,stats,nodes,nodes/<id>,nodes/<id>/neighbors,nodes/<id>/blast,search,search/cross,anomalies,service/<name>,service/<name>/mermaid}` — 11 JSON endpoints wrapping existing tools.

  Mermaid via jsdelivr CDN. Empty-store renders a "populate then refresh" call-to-action. Stdio transport unaffected (dashboard only mounts on HTTP).

- **NEW:** **`src/kuberly_graph/client.py`** — MCP client helper (`fetch_live_resources`, `call_mcp_tool`, `call_tool`). Sync wrappers detect a running event loop (FastMCP HTTP transport) and dispatch the coroutine to a worker-thread loop via `concurrent.futures.ThreadPoolExecutor` — fixes `RuntimeError: asyncio.run() cannot be called from a running event loop` that previously broke every live layer when invoked through the MCP transport.

- **NEW:** **Auth-gated enrichment paths** in `ImageBuildLayer`:
  - **GHA** — stdlib `urllib.request` (no `requests` lib) against GitHub Actions REST API. Token from `github_token` ctx → `GITHUB_TOKEN` env → `KUBERLY_GITHUB_TOKEN`. Emits `commit:<repo>/<sha>` + `workflow_run:<repo>/<run-id>` nodes; edges `commit→workflow_run→image` (SHA-prefix substring match against image tag).
  - **ECR** — optional `boto3` (`try/except ImportError`). Enriches `ecr_repo:` nodes with `image_tag_mutability` / `scan_on_push` / `lifecycle_policy_text`; emits `image_scan_finding:<image>/<cve>` for HIGH/CRITICAL severities (top 10 per image).

  Both opt-in via `enable_gha_enrichment` / `enable_ecr_enrichment` ctx flags (off by default). Soft-degrade with logged warning when token / boto3 / creds missing — never crash. v1 structural extraction unchanged when flags off.

- **CHANGE: `scripts/sync_claude_config.py`** — removes the `out["mcpServers"].pop("kuberly-graph", None)` strip that v0.12.0 introduced; replaces it with first-class `kuberly-graph` registration. New `_mcp_server_graph_claude()` / `_mcp_server_graph_cursor()` factories; `_merge_mcp_file` refactored to a `{name: entry}` map. Both `kuberly-platform` and `kuberly-graph` are now written canonically across all four runtime config files (`.claude/settings.json`, `.mcp.json`, `.cursor/hooks.json`, `.cursor/mcp.json`).

- **NEW:** **K8sLayer default kinds extended** with: `apps/v1` ReplicaSet · DaemonSet · Job; `v1` Pod · Node · ConfigMap · PersistentVolume · PersistentVolumeClaim · StorageClass; `karpenter.sh/v1` NodeClaim · NodePool; `karpenter.k8s.aws/v1` EC2NodeClass; `argoproj.io/v1alpha1` Application; `monitoring.coreos.com/v1` PrometheusRule · ServiceMonitor; `external-secrets.io/v1beta1` ExternalSecret · SecretStore · ClusterSecretStore. Live nodes carry `labels`, `owner_references`, `node_name`, `node_class_ref`, `provider_id`, `annotations`, `container_images`, `pvc_claims`, `secret_refs`, `configmap_refs` so DependencyLayer wires structurally without re-querying MCP.

- **NEW:** **`pyproject.toml`** declares `dependencies = [mcp>=1.27.0, rustworkx>=0.16.0, lancedb>=0.13.0, sentence-transformers>=3.0.0, pyarrow>=17.0.0]`. `boto3` is **not** a hard dep — CostLayer + ECR enrichment import it inside `try/except ImportError`. `chromadb` and `networkx` are **explicitly NOT** in the package — replaced by LanceDB + rustworkx for unified Rust-backed perf.

- **DOCS: `mcp/kuberly-graph/README.md`** — package overview, install (`pip install -e .`), running stdio (Claude Code) vs HTTP (microservice / cluster), the 11 layers, the 44-tool count, and the **Quick refresh** recipe: `kuberly-graph call regenerate_all`.

## v0.44.0 — 2026-05-08

Builds on v0.43.0's dual-source `agent-k8s-ops` by pushing the same
"live cluster reads via the kuberly-ai-agent MCP" pattern down into
the cross-cutting playbook skills, so the runtime persona and the
human-facing skill catalog stay aligned.

- **CHANGE: `.apm/skills/eks-observability-stack/SKILL.md`** —
  new "Live cluster reads via the **`kuberly-ai-agent`** MCP" section
  enumerating the K8s tools shipped by the embedded
  `kubernetes-mcp-server` and **when each one beats the equivalent
  Prometheus / Loki call**:
  - `pods_list_in_namespace` / `pods_get` for "is it running right now"
  - `pods_log previous=true` for pre-restart container logs (the
    dying instance's last lines that often never reach Loki)
  - `pods_top` / `nodes_top` for instant CPU / mem (needs metrics-server)
  - `events_list` for `OOMKilled` / `BackOff` / `FailedScheduling`
  - `nodes_stats_summary` for **PSI metrics** (cgroup v2 pressure
    stalls — node-level confirmation of saturation)
  - `resources_list` / `resources_get` for Karpenter
    `NodeClaim` / `NodePool` and generic CRDs

  Plus the explicit fallback note: `kubectl` paths still apply when
  the MCP isn't wired into the runtime.

- **CHANGE: `.apm/skills/kubernetes-finops-workloads/SKILL.md`** —
  new "The three numbers that drive every right-sizing decision"
  section codifying the **declared / live / allocatable** trio:
  1. **Declared request** (cold graph or live `pods_get`)
  2. **Live usage** (`pods_top` + `nodes_top`, requires metrics-server)
  3. **Allocatable** (`resources_get kind=Node`)

  Headroom = `allocatable − max(sum_of_requests, live_usage)`.
  Two columns in the report: **request-based** (scheduler's view) +
  **live** (reality). Live snapshots stay triage-only; sizing
  decisions still drop to the 24h Prometheus path.

- **CHANGE: `.apm/cursor/commands/kub-obs-triage.md`** — adds a
  symptom→tool-call **decision tree** for the 9 most common incident
  shapes (CrashLoop, OOM, slow request, 5xx surge, "something is
  slow", capacity, Karpenter churn, scrape job dropped, deploy
  failed). Each row gives **first / second / third call** with
  exact tool names from the `kuberly-ai-agent` + `kuberly-platform`
  MCPs, and the kubectl fallback when no MCP is present.

## v0.43.0 — 2026-05-08

- **CHANGE:** **`agents/agent-k8s-ops.md`** — promotes the persona to **dual
  source** (cold k8s overlay graph **and** live cluster via the in-cluster
  `kuberly-ai-agent` MCP). Adds the live-cluster tool surface as a first-class
  source: `pods_list`, `pods_list_in_namespace`, `pods_get`, `pods_log`
  (incl. `previous=true`), `pods_top`, `events_list`, `nodes_top`,
  `nodes_log`, `nodes_stats_summary`, `resources_list`, `resources_get`,
  `namespaces_list`, `configuration_view`. Removes the prior "no direct
  kubectl" hard rule (the upstream `--read-only --disable-destructive`
  posture replaces it). Mirrors the consumer-side wiring note from
  `agent-sre.md`: extend `tools:` with `mcp__kuberly-ai-agent__*` per cluster.
- **NEW:** **Common questions → tool recipes** section in
  `agent-k8s-ops.md` covering the patterns that come up most:
  - **Karpenter capacity** — split nodes by NodeClaim / non-Karpenter EC2 /
    Fargate (label `eks.amazonaws.com/compute-type=fargate`). Per-NodePool
    counts via `karpenter.sh/v1` `NodeClaim` / `NodePool`.
  - **Pod-to-node placement** — group `pods_list` (or `query_k8s(kind=Pod)`)
    by `.spec.nodeName`.
  - **Resource accounting** — three numbers, three columns: declared
    requests (`.spec.containers[*].resources.requests`), live usage
    (`pods_top` / `nodes_top`), allocatable (`.status.allocatable`).
    Headroom = `allocatable − max(sum_of_requests, live_usage)`. Surfaces
    the over-/under-provisioning gap and routes to
    `kubernetes-finops-workloads`.
  - **Recent change** — `events_list` filtered by reason
    (`OOMKilled`, `BackOff`, `FailedScheduling`, `NodeNotReady`, `Killing`).
- **CHANGE:** Workload-graph template in `k8s-state.md` gains rows for live
  data (`Restart count — live now` via `pods_list`, `Last 30m events` via
  `events_list`, `Live CPU / mem (top)` via `pods_top`). Cold-graph rows
  are unchanged.
- **CHANGE:** Citation rule extended — every row cites either a graph
  node id / edge / overlay field (cold) **or** a tool call signature
  (`pods_list(namespace=…, labelSelector=…)`) for live reads.

## v0.42.2 — 2026-05-07

- **NEW:** **`mcp/ai-agent-tool/README.md`** — integration doc for the
  in-cluster **`ai-agent-tool`** MCP server (read-only Kubernetes / Loki /
  Prometheus / Tempo investigations). Documents the tools, prompts, and the
  Cursor / Claude Code wiring snippets. The MCP is **not** auto-declared in
  `apm.yml` because its URL is per-cluster.
- **CHANGE:** **`agents/agent-sre.md`** — promotes **`kuberly-ai-agent`** MCP
  (the new ai-agent-tool) to first-class for runtime cluster signal; the
  shell-command path (`aws`/`kubectl`/`logcli`) is now an explicit fallback,
  not the primary route. Removes the "kuberly-observability MCP roadmap"
  note since it's now shipping as `ai-agent-tool`.
- **BUMP:** apm.yml 0.33.0 → 0.42.2 (bringing version field in line with the
  released tag stream; consumers in `kuberly-stack` were already pinned at
  `v0.42.1`).

## v0.42.3 — 2026-05-08

One-line bug fix on top of v0.42.1's caveman EOF normalizer (skipping the
parallel v0.42.2 release tagged from `main`, which did not include the
v0.42.1 EOF work — branches diverged):

- **FIX: `post_apm_install.sh` `_eof_newline_fix` loop now also walks
  `.agents/skills/`** in addition to `.claude/skills/`, `.cursor/skills/`,
  `.github/skills/`, and `.opencode/skills/`. apm-cli writes runtime-
  agnostic skills to `.agents/skills/<name>/SKILL.md` (the canonical
  location for opencode-aware consumers since v0.42.0), so the v0.42.1
  normalizer silently skipped that path. Caveman SKILL.md still flapped
  on every consumer commit because pre-commit's `end-of-file-fixer`
  rewrote `.agents/skills/caveman*/SKILL.md` and the next `apm install`
  redeployed the unfixed upstream copy. Adding `.agents/skills` as the
  first entry in the `skill_root` for-loop makes the normalizer cover
  every runtime root apm-cli emits today.

This release does NOT include the ai-agent-tool MCP integration doc
shipped in `main`'s `v0.42.2` (commit fe6b38d). That work lives on
`origin/main` and will be re-merged into the opencode-support lineage
in a future release; consumers needing both the EOF fix and the ai-
agent-tool MCP plumbing should pin v0.42.3 here and vendor the
ai-agent-tool module directly (kuberly-stack pattern).

## v0.42.1 — 2026-05-07

Three follow-ups on top of v0.42.0's opencode work, in response to flap
patterns surfaced by the first kuberly-stack consumer bump:

- **FIX: `post_apm_install.sh` now normalizes EOF newlines on
  APM-deployed `**/skills/caveman*/SKILL.md` files**, not just on the 5
  apm-managed JSON config files (the v0.41.5 fix). The `caveman` package
  ships its `SKILL.md` files without a trailing newline; once a consumer
  added them to git, pre-commit's `end-of-file-fixer` rewrote them, the
  next `apm install` redeployed the unfixed upstream version, and they
  conflicted on the next commit (a silent rollback in pre-commit's stash
  logic). The new globbed loop walks all four runtime skill roots
  (`.claude/`, `.cursor/`, `.github/`, `.opencode/skills/caveman*/`) and
  appends a single `\n` only if missing — same idempotent shape as
  section 5. Consumers can now either commit the trailing-newline
  version (no flap) or keep the existing `.pre-commit-config.yaml`
  caveman excludes (also no flap); both paths work.
- **NEW: `agents-opencode/*.md` restore the `name:` frontmatter field.**
  v0.42.0 dropped `name:` because opencode derives the agent name from
  the filename — but tests confirm opencode tolerates `name:` cleanly
  and ignores it when redundant. Restoring it keeps every persona file
  byte-identical to its `agents/*.md` Claude Code counterpart on the
  `name:` and `description:` lines (only `tools:` vs `mode:` differs),
  which simplifies cross-runtime diff review.
- **DOCS: `agent-orchestrator` SKILL.md now describes runtime-specific
  invocation syntax.** A new table under "Persona roster" maps each of
  Claude Code, Cursor, and opencode to its dispatch ABI (Claude Code's
  `Agent({subagent_type: ...})` vs opencode's Task tool / `@<name>`
  mention) and documents that opencode subagents create child sessions
  the user can navigate via opencode's session shortcuts. The
  Distribution section also names `agents-opencode/` explicitly so
  consumers know there are two parallel source trees.
- **BUMP:** `apm.yml` 0.42.0 → 0.42.1.

Consumer migration: bump the apm.yml pin to `#v0.42.1` and run
`apm install --update`. The new EOF-normalization runs idempotently;
on consumers that already excluded `.opencode/skills/caveman*/` from
`end-of-file-fixer` the visible behavior is unchanged.

## v0.42.0 — 2026-05-07

First-class **opencode** support: the persona subagents now ship in a second
frontmatter dialect that opencode's loader accepts, and `sync_agents.sh`
materializes them at `.opencode/agents/` alongside the existing
`.claude/agents/` and `.cursor/agents/` outputs.

The Claude Code / Cursor frontmatter (`name:` + comma-separated `tools:`)
is rejected by opencode's schema (it expects `mode:` and a `permission:`
object). v0.42.0 ships a parallel source tree that produces opencode-native
frontmatter without changing the persona bodies, so the same orchestration
playbook works under all three runtimes.

- **NEW:** `agents-opencode/*.md` — opencode-native variant of every persona.
  Frontmatter reduced to `description:` + `mode: subagent`; bodies are byte-
  identical to `agents/*.md`. Wide-open permission stance (no `permission:`
  block) — consumers can tighten per-fork via opencode's `permission:` field
  if needed.
- **NEW:** `scripts/sync_agents.sh` extended to a third destination
  (`.opencode/agents/`) sourced from `agents-opencode/`. Idempotent;
  preserves the existing `.claude/` + `.cursor/` outputs unchanged.
- **DOCS:** `agent-orchestrator` SKILL is unchanged — the orchestration
  protocol applies identically under opencode (primary session fans out
  via opencode's Task tool, subagents discover one another by filename).
- **BUMP:** `apm.yml` 0.41.5 → 0.42.0.

Consumer migration (kuberly-stack and forks):

```yaml
# apm.yml
dependencies:
  apm:
    - git@github.com:kuberly/kuberly-skills.git#v0.42.0
```

Then `apm install`. If the consumer previously symlinked
`.opencode → .cursor` to share Claude Code agent files, **remove the
symlink** before installing so the new `.opencode/agents/` directory can
be populated with native frontmatter.

## v0.41.5 — 2026-05-06

Fix the two flap sources that forced consumers to commit with
`KUBERLY_SKIP_APM_SYNC=1`. After v0.41.5, routine commits and version
bumps round-trip cleanly through the consumer's `ensure-apm-skills`
pre-commit hook with no escape hatch.

- **FIX: `kuberly-platform` graph generator wrote trailing space when an
  environment had zero `configures` edges.** `GRAPH_REPORT.md`'s
  blast-radius section used `f"... — " + ", ".join(...)`; an empty
  list collapsed to `"... — "` (trailing space after the em-dash),
  which then tripped the consumer's `trailing-whitespace` pre-commit
  hook every install. Now emits `"...: 0 components"` (no trailing
  punctuation) when the list is empty.
- **FIX: `post_apm_install.sh` now normalizes EOF newlines on
  apm-managed config files** (`opencode.json`, `.mcp.json`,
  `.cursor/mcp.json`, `.cursor/hooks.json`, `.claude/settings.json`).
  apm-cli writes some of these without a trailing `\n`, which fired
  the consumer's `end-of-file-fixer` hook on every install. Idempotent:
  the new step only appends `\n` if the file is missing it.
- **VERIFIED:** kuberly-stack consumer now commits cleanly via
  `git commit` (no `KUBERLY_SKIP_APM_SYNC=1`); the `ensure-apm-skills`
  hook completes the version-bump round-trip without flap.
- **BUMP:** apm.yml 0.41.4 → 0.41.5.

## v0.41.4 — 2026-05-06

Source-side pre-commit hardening — catch trailing whitespace, EOL, and
broken JSON/YAML/shell **at the source** so consumers' `ensure-apm-skills`
hook never has to fix files this package shipped.

- **NEW:** `.pre-commit-config.yaml` at repo root. Hooks:
    - `trailing-whitespace`, `end-of-file-fixer`, `mixed-line-ending`
      (LF) — no more shipping files that the consumer's `pre-commit`
      auto-fixes after every `apm install`.
    - `check-yaml`, `check-json` — validates `apm.yml`,
      `apm.lock.yaml`, `.cursor/mcp.json` template, etc.
    - `check-added-large-files` (`--maxkb=500`) — catches accidental
      asset commits before they pollute consumer clones.
    - `shellcheck` over `scripts/*.sh` — surfaces scripting bugs in
      `post_apm_install.sh`, `sync_*.sh`, `ensure_*.sh` before they
      ship.
    - **Local hook:** `skill-frontmatter-required` — every `SKILL.md`
      must open with YAML frontmatter that has `name:` and
      `description:`. Catches the v0.41.3-class regression where the
      orchestrator's frontmatter description silently lost a persona.
- **VERIFIED:** existing source files all pass — no fmt churn in
  this release. The hooks are preventative for future commits.
- **CLARIFY (no code change):** `scripts/post_apm_install.sh` already
  ignores `generated_at`-only diffs in `apm.lock.yaml` and restores
  byte-identical pre-install snapshot when semantic content is
  unchanged (added v0.28.0). Consumers should NOT need
  `KUBERLY_SKIP_APM_SYNC=1` for routine commits or version bumps —
  the hook handles `apm-cli`'s timestamp churn cleanly. The env-var
  remains as an emergency escape, not a default.
- **BUMP:** apm.yml 0.41.3 → 0.41.4.

## v0.41.3 — 2026-05-06

Orchestrator persona-roster bugfix.

- **FIX: `agent-orchestrator` skill omitted `agent-k8s-ops` from its
  routing surface.** The persona has shipped since v0.22.0 (the
  v0.21.0 → v0.22.0 rename pass) but was never added to:
    - the skill's frontmatter `description` (the routing one-liner
      Claude Code reads to decide whether to load the skill)
    - the persona roster table — so the orchestrator could not
      decide between `agent-sre` ("what's the metric/log") and
      `agent-k8s-ops` ("what's running, how is it wired") on any
      live-cluster question.
  Net effect: pre-v0.41.3, the orchestrator either routed all
  cluster-state questions to `agent-sre` (wrong scope) or escalated
  to the user (wasted round-trip).
- **FIX: `terragrunt-plan-reviewer` was also missing from the persona
  roster table** (was in frontmatter, not in the table). Added a
  row describing its CI-comment plan-review scope.
- **CLARIFY: `pr-reviewer` rows differentiated as in-context pass vs.
  cold pass** (same persona, two distinct prompts run in parallel —
  the duplicate row was confusing reviewers).
- **EXTEND: read-only-personas mentions** (4 places) — added
  `agent-k8s-ops` to the implicit allowlist so it dispatches without
  user approval (matches its read-only frontmatter).
- **EXTEND: shared-prompts directory tree** — added `k8s-state.md`
  (agent-k8s-ops output) and `findings/plan-review.md`
  (terragrunt-plan-reviewer output).
- **EXTEND: cheap pre-flight rule** — added
  `agent-k8s-ops` reservation note so the orchestrator routes
  *"what's actually deployed in the cluster"* there instead of to
  `agent-sre` (which is for metrics/logs).
- **BUMP:** apm.yml 0.41.2 → 0.41.3.

## v0.41.2 — 2026-05-06

- **DROP:** the "stack intelligence / kuberly" eyebrow + `<h1>` from
  the hero panel — purely chrome, no signal.
- **REPLACE: hero KPIs.** Generic counters (AWS Resources / Findings /
  State age / Modules / Graph) replaced with concrete operator
  facts that answer specific questions:
    - **K8s version** — EKS cluster K8s version + node groups /
      fargate / addons
    - **Database** — engine (Aurora/MySQL/Postgres) + version + class
      (e.g. `aurora-postgresql 16.6 · db.serverless`)
    - **Cache** — ElastiCache node type + version + shard count
    - **Public exposure** — count of `0.0.0.0/0` ingress + publicly-
      accessible RDS, with a callout when zero (`no high findings`)
    - **Apps deployed** — total + per-env breakdown (`2 prod · 2 dev`)
- **NEW:** `_compute_hero_facts()` server-side helper composes the
  facts from existing schema-v3 essentials + findings + env data.
  Falls back gracefully when a category is empty.
- **BUMP:** apm.yml 0.41.1 → 0.41.2.

## v0.41.1 — 2026-05-06

- **MERGE:** the v0.41.0 stats bar and hero used to render as two
  separate cards stacked above each other. Now wrapped in a single
  `.hero-panel` card — stats rows on top, internal `border-bottom`
  separator, then the cluster name + KPI strip below. One visual unit.

## v0.41.0 — 2026-05-06

Dashboard redesign — toned down, less cartoonish, stats up top.

### Changes

- **Stats bar at TOP** of the dashboard (was footer in v0.39.1).
  Two slim mono-typography rows: `overlays` (OpenSpec / docs / state
  snapshots / doc-linked) and `graph nodes` (per-layer counts as
  colored pills). Uses a card surface + 1 px border, no gradient.
- **Refined hero** — eyebrow + cluster name + KPI strip now sits on a
  transparent background with a single bottom border-line separator.
  KPI numbers shrink **28 px → 20 px** and switch to monospace for
  consistency with the data-bar above. Removed the radial-gradient
  burst ("cartoon" effect).
- **Distributions section deleted** — user feedback: charts not
  useful. ECharts CDN dropped (smaller payload, faster cold load).
  The architecture diagram + per-tile drilldown + node spotlight are
  now the page's primary information density.
- **Footer removed** — its data lives at the top now; nothing
  redundant at the bottom.

Page order: Stats bar → Hero → Architecture (with click-to-list) →
Node spotlight.

- **BUMP:** apm.yml 0.40.0 → 0.41.0.

## v0.40.0 — 2026-05-06

Make MCP + skills aware of the new graph types (CUE / CI/CD /
Applications) so AI agents proactively query them.

### MCP manifest (`kuberly_mcp/manifest.py`)

- **`query_nodes`** description now lists every recognized node type:
  `environment`, `shared-infra`, `cloud_provider`, `module`,
  `component`, `application`, `resource`, `k8s_resource`, `doc`,
  `cue_schema`, `workflow`, `app_render`, `rendered_resource`. Plus
  the `source_layer` axis ∈ {static, state, k8s, docs, schema, ci_cd,
  rendered} so LLMs see the connection between node types and the
  graph-view layer pills.
- **`get_neighbors`** description gains the new edge relations
  (`references`, `renders`, `rendered_into`) with concrete answer-path
  examples ("which workflow deploys module X", "what does this app
  render into", "which SA assumes this IAM role").
- **`graph_index`** description now mentions all 7 layers and the new
  cross-layer bridges.

### Skills

- **`kuberly-stack-context`** — graph-layers section rewritten from
  "three layers" to **seven layers** with the new
  `cue_schema` / `workflow` / `app_render` / `rendered_resource`
  rows, common answer patterns, and the manual-only renderer block
  describing `scripts/render_apps.py` + `scripts/diff_apps.py`.
- **`agent-orchestrator`** — new "Graph layers covered (v0.36+)"
  paragraph after the tool-catalog section. Tells the orchestrator
  to answer app-deploy questions via `rendered_into` → `renders`
  walks, and to surface "manual run required" instead of concluding
  no deploy when the rendered layer is empty.
- **`/kub-graph-refresh`** — Step 3 now mentions the v0.34/v0.36/
  v0.38 layer pills (`IaC files / TG state / K8s / Docs / CUE /
  CI/CD / Applications`) and adds an optional block on populating
  the *Applications* (rendered) layer via the manual scripts.
- **`/kub-stack-context`** — Step 5 lists the three new graph types.
- **`/kub-repo-locate`** — classification step gains "CUE schema" and
  "CI/CD job" categories with the matching `query_nodes` queries.

- **BUMP:** apm.yml 0.39.1 → 0.40.0.

## v0.39.1 — 2026-05-06

- **MOVE:** the Stats & overlays section becomes the **dashboard
  footer**. New `<footer class="dash-footer">` rendered at the very
  bottom of the dashboard with two slim rows (`overlays` and
  `graph nodes by layer`).
- **DEDUP:** dropped the "newGraphCounts" chip row that duplicated
  CUE schemas / Workflows / Applications counts already shown in the
  per-layer legend.
- **STYLE:** footer rows are dense mono-typography pills (8 px
  padding, single-line) rather than full-width section chips. Section
  border-top separates the footer visually.
- **BUMP:** apm.yml 0.39.0 → 0.39.1.

## v0.39.0 — 2026-05-06

- **CHARTS:** migrate from Chart.js to **Apache ECharts** for the
  Distributions panel. ECharts gives the dashboard a far-more-polished
  default look — gradient fills (LinearGradient), smooth animations,
  rich hover tooltips with category dot + bold value, dark theme,
  inline value labels on bars. The 3 charts (Category share doughnut,
  IAM trust principals horizontal bar, Top resource types horizontal
  bar) all replaced. ResizeObserver re-fits each chart on grid reflow
  / window resize.
- **REORDER:** Distributions section moved to **top of dashboard**
  (right after the SaaS hero band, before Architecture). Hero → Charts
  → Architecture → Stats overlays → Spotlight.
- **STYLE:** chart cards get a subtle radial-gradient background + a
  hover lift (border glow + shadow + 1 px rise). Mount divs are 240 px
  tall. Chart.js canvas elements replaced with `div.chart-mount`.
- **RENAME:** the "Rendered" layer pill becomes **"Applications"**.
  Color changes from teal `#22a1c4` to **hot pink `#ff5e9c`** so the
  per-app cluster reads as distinct on the 3D graph. Spotlight chip
  reads "apps". Internal node types (`app_render`, `rendered_resource`,
  source_layer="rendered") unchanged — only labels/colors flipped.
- **FIX:** v0.39.0-pre had a broken `${...}` JS-template-literal in
  the new ECharts `formatter` that broke Python's string.Template.
  Escaped to `$${...}`.
- **BUMP:** apm.yml 0.38.1 → 0.39.0.

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
