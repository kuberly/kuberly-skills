# kuberly-platform MCP (stdio)

Python **Model Context Protocol** server and **`generate`** CLI for Terragrunt/OpenTofu monorepos (`blast_radius`, `drift`, persona-orchestration tools, etc.).

**Consumers:** after `apm install`, `scripts/sync_mcp.sh` copies this directory into the infra repo at `scripts/mcp/kuberly-platform/`. Configure **Cursor** (`.cursor/mcp.json`) and **Claude Code** (`.mcp.json`) to run:

`python3 scripts/mcp/kuberly-platform/kuberly_platform.py mcp --repo .`

Use `${workspaceFolder}` in Cursor for `--repo` when using absolute workspace roots.

Canonical source lives in **kuberly-skills**; do not fork the script inside customer repos — extend here and release a new tag.
