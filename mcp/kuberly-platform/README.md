# kuberly-platform MCP (stdio)

Python **Model Context Protocol** server and **`generate`** CLI for Terragrunt/OpenTofu monorepos (`blast_radius`, `drift`, persona-orchestration tools, etc.).

**Consumers:** after `apm install`, `scripts/sync_mcp.sh` copies this directory into the infra repo at `scripts/mcp/kuberly-platform/`. Configure **Cursor** (`.cursor/mcp.json`) and **Claude Code** (`.mcp.json`) to run:

`python3 scripts/mcp/kuberly-platform/kuberly_platform.py mcp --repo .`

Use `${workspaceFolder}` in Cursor for `--repo` when using absolute workspace roots.

Canonical source lives in **kuberly-skills**; do not fork the script inside customer repos — extend here and release a new tag.

## State-overlay graph (v0.17.0+)

The static graph (HCL + `components/<env>/*.json`) underreports: modules deployed by the platform without a JSON sidecar (e.g. `loki`, `grafana`, `alloy` once EKS exists) look like graph leaves and the actionability check returns `stop-no-instance`.

`state_graph.py` closes the gap by listing the cluster's Terragrunt state bucket and emitting a sanitized overlay file the consumer commits to its repo. **List-only by default** — only S3 object keys are fetched, never object bodies.

```bash
# schema 1 (default) — list-only, lists deployed modules + applications.
# Needs s3:ListBucket. Fast (~1s).
python3 scripts/mcp/kuberly-platform/state_graph.py generate \
    --env prod --output .claude/state_overlay_prod.json

# schema 2 (--resources) — adds per-module resource graph.
# Needs s3:ListBucket + s3:GetObject. Slower (~30s–2min per env).
python3 scripts/mcp/kuberly-platform/state_graph.py generate \
    --env prod --resources

# all envs in components/ in one go (per-account login still required):
python3 scripts/mcp/kuberly-platform/state_graph.py generate-all \
    --output-dir .claude --resources

# pass --profile <name> to pick a specific AWS CLI profile.
# pass --modules loki,grafana to subset (only with --resources).
```

### Schema 1 — list-only (default, v0.17.0+)

Cluster `env`/`name`/`region`/`account_id`/`state_bucket` (all already public in `shared-infra.json`) plus `deployed_modules[]` and `deployed_applications[]` listing module/app names + their state keys. Anything else is dropped at write time.

### Schema 2 — resource graph (v0.18.0+)

Schema 1 + per-module `resources[]`: each resource's `address`, `type`, `name`, `provider` (e.g. `hashicorp/helm`), `instance_count`, and `depends_on[]` (other resource addresses in the same state). Plus `output_names[]` per module.

**Attribute values are NEVER emitted** — the producer drops `instance.attributes`, `instance.private`, output `.value`, and provider config at extraction time. Resources of sensitive types (`aws_secretsmanager_secret`, `kubernetes_secret`, `helm_release`, `tls_private_key`, `random_password`, etc.) appear as nodes (so the graph reflects "this exists") but the kuberly-platform graph builder tags them with `redacted: true` so consumer UIs can render them with a redaction marker.

The `query_resources` MCP tool filters them: `query_resources(resource_type="helm_release")`, `query_resources(module="loki", environment="prod")`, `query_resources(name_contains="secret", include_redacted=False)`.

**Schema 2 requires** `s3:GetObject` on the state bucket in addition to `s3:ListBucket`. Commit the overlay file alongside the bump — the platform graph picks it up automatically on build.

## Live-cluster overlay (v0.19.0+)

`k8s_graph.py` does for the **runtime layer** what `state_graph.py` does for the infra layer: shells out to `kubectl get -o json`, extracts whitelisted fields per kind, emits a sanitized `.claude/k8s_overlay_<env>.json` file the consumer commits.

```bash
# be connected to the cluster:
aws eks update-kubeconfig --name prod --region eu-central-1

python3 scripts/mcp/kuberly-platform/k8s_graph.py generate \
    --env prod --output .claude/k8s_overlay_prod.json

# subset / opt-ins:
#   --namespaces monitoring,argocd       limit to listed ns
#   --include-pods                       include Pods (off by default — transient)
#   --context arn:aws:eks:...            override kubectl current-context
#   --dry-run                            print without writing
```

### Per-kind whitelist

Workloads (Deployment / StatefulSet / DaemonSet / Job / CronJob / Pod): name, namespace, labels, ownerRefs, replicas, serviceAccountName, container names, image refs, configMap/secret/PVC volume references **(names only)**, envFrom/env.valueFrom **(names only)**.

Service: selector, ports (number+protocol). Ingress: hosts, backend service refs. ConfigMap / Secret: **`data_keys` only — values NEVER read**. ServiceAccount: name + IRSA role ARN annotation. HPA: target, min/max replicas. NetworkPolicy: pod selector, policy types.

**Always dropped**: env values, `command`, `args`, `status`, all `data` / `stringData`, all unlisted annotations.

### IRSA bridge

ServiceAccounts with `eks.amazonaws.com/role-arn` annotation get an `irsa_bound` edge to the matching `resource:<env>/<m>/.../aws_iam_role.<n>` node from the state overlay. This means once both overlays are committed, the graph spans **EKS IAM role (Terraform) → ServiceAccount (k8s) → workload (k8s)** — useful for "what cluster workload uses this IAM role" queries.

### MCP tool: `query_k8s`

Filter `k8s_resource:` nodes by `environment` / `namespace` / `kind` / `name_contains` / `label_selector`. Set `include_redacted=False` to hide Secret/ConfigMap nodes when sharing graph dumps.

## Docs / knowledge overlay (v0.20.0+)

`docs_graph.py` indexes every doc/skill/agent/prompt/OpenSpec change in the repo into `.claude/docs_overlay.json`. Stdlib only, deterministic, runs offline by default.

```bash
# offline pass — file walk + frontmatter + headings + link/mention edges
python3 scripts/mcp/kuberly-platform/docs_graph.py generate

# also compute embeddings (incremental — only changed files re-embedded):
KUBERLY_DOCS_EMBED=openai OPENAI_API_KEY=sk-... \
    python3 scripts/mcp/kuberly-platform/docs_graph.py generate --embed

# limit to specific path prefixes:
python3 scripts/mcp/kuberly-platform/docs_graph.py generate --paths agents/,docs/

# full rescan (ignore prior overlay; useful after switching embed providers):
python3 scripts/mcp/kuberly-platform/docs_graph.py generate --full --embed
```

### Pre-commit auto-regen

Wire `scripts/regenerate_docs_overlay.sh` into the consumer's `.pre-commit-config.yaml`:

```yaml
- repo: local
  hooks:
    - id: regenerate-docs-overlay
      name: Refresh .claude/docs_overlay.json
      entry: bash apm_modules/kuberly/kuberly-skills/scripts/regenerate_docs_overlay.sh
      language: system
      pass_filenames: false
      files: '\.(md|json)$'
```

The hook is idempotent — when no doc changed, the only diff is the `generated_at` timestamp. Embeddings default OFF (no API key needed); set `KUBERLY_DOCS_EMBED=openai` in your shell to also embed changed files.

### What gets indexed

| Kind | Path pattern | Source of metadata |
|---|---|---|
| `skill` | `.apm/skills/*/SKILL.md` | YAML frontmatter (name, description) |
| `agent` | `agents/*.md` | YAML frontmatter (tools, description) |
| `doc` | `docs/*.md`, `*.md` (top-level), `mcp/*/README.md` | First H1 + leading paragraph |
| `openspec` | `openspec/changes/*/{proposal,tasks,design,CHANGELOG}.md` | Filename + headings |
| `reference` | `references/**/*.md` | Headings |
| `prompt` | `prompts/**/*.md` | Headings |

Edges:
- `links_to` — markdown links between docs
- `mentions` — backtick-wrapped mentions of known module/component/application names map to those graph nodes (so `query_nodes(node_type="doc")` and `get_neighbors("module:aws/loki")` connect)
- `uses_tool` — agents → `tool:<name>` (informational)

### MCP tools: `find_docs` + `graph_index`

`find_docs(query, kind=None, semantic=True, limit=20)` — keyword scoring against title+description+headings always; if embeddings present, also cosine similarity (combined 0.4 keyword + 0.6 semantic).

`graph_index()` — meta-tool. Returns a snapshot of every layer loaded, node counts by type, edge counts by relation, cross-layer bridges (IRSA, configures_module, depends_on, mentions), and overlay file freshness timestamps. Call this at session start to know what data you have.
