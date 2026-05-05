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
# after `aws sso login` (any profile that can read the state bucket):
python3 scripts/mcp/kuberly-platform/state_graph.py generate \
    --env prod --output .claude/state_overlay_prod.json
# all envs in components/ in one go (per-account login still required):
python3 scripts/mcp/kuberly-platform/state_graph.py generate-all \
    --output-dir .claude
# pass --profile <name> to pick a specific AWS CLI profile.
```

Output is a strict-schema JSON: cluster `env`/`name`/`region`/`account_id`/`state_bucket` (all already public in `shared-infra.json`) plus `deployed_modules[]` and `deployed_applications[]` listing module/app names + their state keys. Anything else is dropped at write time. Commit one file per cluster — the platform graph picks them up automatically on build.
