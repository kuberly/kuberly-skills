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
