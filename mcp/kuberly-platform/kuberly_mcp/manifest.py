"""MCP tool list — single source of truth for `tools/list` JSON schemas."""

from __future__ import annotations

from typing import Any

import mcp.types as types  # type: ignore[import-untyped]

_FORMAT_PROP: dict[str, Any] = {
    "type": "string",
    "enum": ["compact", "json", "card"],
    "default": "compact",
    "description": (
        "Output format. 'compact' (default, v0.13.4+) — structured plain text "
        "optimized for sub-agent token cost. 'json' — raw JSON dump. 'card' — "
        "rich Markdown for human display."
    ),
}

# Raw dicts mirror the pre-SDK `TOOLS` list (including `defer_loading` hints for docs).
_RAW_TOOLS: list[dict[str, Any]] = [
    {
        "name": "query_nodes",
        "description": (
            "Filter graph nodes by type, environment, and/or name substring. "
            "Recognized node types (v0.40.0):\n"
            "  - environment, shared-infra, cloud_provider — cluster spine\n"
            "  - module, component, application — IaC + app catalog\n"
            "  - resource — Terraform-state vertex (schema 2/3, see query_resources)\n"
            "  - k8s_resource — live cluster vertex (see query_k8s)\n"
            "  - doc — README / runbook / ADR / OpenSpec entry\n"
            "  - cue_schema — `cue/**/*.cue` schema file (v0.36.0+)\n"
            "  - workflow — `.github/workflows/*.yml` CI/CD job (v0.36.0+)\n"
            "  - app_render — per-application umbrella for CUE-rendered manifests "
            "(v0.38.0+; populated by `scripts/render_apps.py` → "
            "`.kuberly/rendered_apps_<env>.json`)\n"
            "  - rendered_resource — leaf rendered k8s manifest under an app_render "
            "(Deployment, Service, ExternalSecret, VirtualService, ...)\n"
            "Each node carries `source_layer` ∈ {static, state, k8s, docs, schema, "
            "ci_cd, rendered}; that's the same axis as the layer-toggle pills in the "
            "graph view."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "node_type": {"type": "string", "description": "Node type filter"},
                "environment": {"type": "string", "description": "Environment name filter"},
                "name_contains": {
                    "type": "string",
                    "description": "Substring to match in node name/id",
                },
                "format": _FORMAT_PROP,
            },
        },
    },
    {
        "name": "query_resources",
        "defer_loading": True,
        "description": (
            "Filter `resource:` nodes synthesized from the schema 2 state overlay "
            "(e.g. helm_release, aws_iam_role, kubernetes_namespace). Resource "
            "attribute VALUES are never in the graph — sensitive types (secrets, "
            "passwords, TLS keys) are tagged `redacted: true` so the existence is "
            "visible but the payload was suppressed at producer time."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "environment": {"type": "string", "description": "env filter, e.g. 'prod'"},
                "module": {"type": "string", "description": "module filter, e.g. 'loki'"},
                "resource_type": {
                    "type": "string",
                    "description": "Terraform resource type filter, e.g. 'helm_release', 'aws_iam_role'",
                },
                "name_contains": {
                    "type": "string",
                    "description": "Substring match against resource address / id",
                },
                "include_redacted": {
                    "type": "boolean",
                    "default": True,
                    "description": "Include resources of sensitive types (existence only, never values)",
                },
                "format": _FORMAT_PROP,
            },
        },
    },
    {
        "name": "find_docs",
        "defer_loading": True,
        "description": (
            "Search the docs overlay (skills, agents, READMEs, OpenSpec changes, prompts). "
            "Always does keyword scoring against title/description/headings. If embeddings "
            "are present (KUBERLY_DOCS_EMBED was set when the overlay was generated), also "
            "computes semantic cosine similarity and combines the two scores 0.4 keyword + "
            "0.6 semantic. Use to answer 'where is the skill that explains X' / 'what skill "
            "mentions module Y'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Free-text query (tokenized + embedded)"},
                "kind": {
                    "type": "string",
                    "description": "Filter: skill / agent / doc / openspec / reference / prompt",
                },
                "semantic": {
                    "type": "boolean",
                    "default": True,
                    "description": "Use embedding similarity if available",
                },
                "limit": {"type": "integer", "default": 20, "description": "Max results"},
                "format": _FORMAT_PROP,
            },
        },
    },
    {
        "name": "graph_index",
        "defer_loading": True,
        "description": (
            "Meta-tool. Returns a summary of every graph layer that's loaded "
            "(static, state, k8s, docs, schema, ci_cd, rendered), node counts "
            "by type, edge counts by relation, cross-layer bridges that fired "
            "(IRSA `irsa_bound`, `configures_module`, `depends_on`, `mentions`, "
            "and v0.36+ `references` / v0.38+ `renders` / `rendered_into`), and "
            "overlay file freshness timestamps. Use at the start of a session "
            "to know what data you have."
        ),
        "inputSchema": {"type": "object", "properties": {"format": _FORMAT_PROP}},
    },
    {
        "name": "query_k8s",
        "defer_loading": True,
        "description": (
            "Filter `k8s_resource:` nodes synthesized from the live-cluster overlay "
            "(`.kuberly/k8s_overlay_*.json`, produced by `k8s_graph.py`). Knows Deployments, "
            "StatefulSets, Services, Ingresses, ConfigMaps, Secrets, ServiceAccounts, HPAs, "
            "NetworkPolicies. Secret/ConfigMap nodes carry `redacted: true` and `data_keys[]` "
            "only — values are NEVER in the graph. ServiceAccounts with IRSA annotations are "
            "bridged (edge `irsa_bound`) to the matching `resource:*/aws_iam_role.<n>` node from "
            "the state overlay."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "environment": {"type": "string", "description": "env filter, e.g. 'prod'"},
                "namespace": {"type": "string", "description": "k8s namespace filter, e.g. 'monitoring'"},
                "kind": {"type": "string", "description": "k8s kind filter, e.g. 'Deployment', 'Service'"},
                "name_contains": {"type": "string", "description": "Substring match against resource name"},
                "label_selector": {
                    "type": "object",
                    "description": "{key: value} pairs that ALL must match the resource's labels",
                },
                "include_redacted": {
                    "type": "boolean",
                    "default": True,
                    "description": "Include Secret / ConfigMap nodes (existence only — `data_keys` shown, never values)",
                },
                "format": _FORMAT_PROP,
            },
        },
    },
    {
        "name": "get_node",
        "description": "Get full details for a specific node by id or name.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "node": {"type": "string", "description": "Node id or name"},
                "format": _FORMAT_PROP,
            },
            "required": ["node"],
        },
    },
    {
        "name": "get_neighbors",
        "description": (
            "Get immediate incoming and outgoing neighbors of a node, with the "
            "edge `relation` for each. Common relations: `depends_on`, `contains`, "
            "`configures`, `configures_module`, `mentions`, `irsa_bound`, "
            "`reads_configmap`, `reads_secret`, `uses_sa`, `selects`, `provides`, "
            "`references` (v0.36+ workflow→module/component), `renders` / "
            "`rendered_into` (v0.38+ app_render → rendered_resource and "
            "application → app_render). Useful answer paths: 'which workflow "
            "deploys module X' (inbound `references`), 'what does this app render "
            "into' (outbound `renders`), 'which SA assumes this IAM role' (inbound "
            "`irsa_bound`)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "node": {"type": "string", "description": "Node id or name"},
                "format": _FORMAT_PROP,
            },
            "required": ["node"],
        },
    },
    {
        "name": "blast_radius",
        "description": (
            "Compute the blast radius of a node: what it affects downstream (if changed) "
            "and what affects it upstream. Useful for impact analysis."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "node": {"type": "string", "description": "Node id or name (e.g. 'eks', 'module:aws/vpc')"},
                "direction": {
                    "type": "string",
                    "enum": ["upstream", "downstream", "both"],
                    "default": "both",
                },
                "max_depth": {"type": "integer", "default": 20},
                "format": _FORMAT_PROP,
            },
            "required": ["node"],
        },
    },
    {
        "name": "shortest_path",
        "defer_loading": True,
        "description": "Find the shortest path between two nodes in the graph.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "Source node id or name"},
                "target": {"type": "string", "description": "Target node id or name"},
                "format": _FORMAT_PROP,
            },
            "required": ["source", "target"],
        },
    },
    {
        "name": "drift",
        "defer_loading": True,
        "description": (
            "Show cross-environment drift: components and applications that exist in some "
            "environments but not others."
        ),
        "inputSchema": {"type": "object", "properties": {"format": _FORMAT_PROP}},
    },
    {
        "name": "stats",
        "defer_loading": True,
        "description": (
            "Get graph statistics: node/edge counts, critical nodes (most depended upon), "
            "and longest dependency chains."
        ),
        "inputSchema": {"type": "object", "properties": {"format": _FORMAT_PROP}},
    },
    {
        "name": "plan_persona_fanout",
        "description": (
            "Orchestration plan for a kuberly-stack infra task. Classifies task_kind, computes "
            "blast-radius/drift scope, runs branch + OpenSpec + personas-synced gates, returns "
            "a persona DAG (with per-phase parallel/needs_approval flags) and a ready-to-paste "
            "context.md body. Call this first in agent-orchestrator mode; then use session_init "
            "to materialize a session dir."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Free-form task description from the user."},
                "named_modules": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional: module names hinted by the user (e.g. ['loki']).",
                },
                "target_envs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional: target environments. Drift slice is computed only when set.",
                },
                "current_branch": {
                    "type": "string",
                    "description": "Result of `git rev-parse --abbrev-ref HEAD` — enables the branch gate.",
                },
                "session_name": {
                    "type": "string",
                    "description": "Optional override for the session slug; defaults to slugified task.",
                },
                "task_kind": {
                    "type": "string",
                    "enum": [
                        "resource-bump",
                        "incident",
                        "new-application",
                        "new-database",
                        "new-module",
                        "drift-fix",
                        "cicd",
                        "cleanup",
                        "plan-review",
                        "unknown",
                        "stop-target-absent",
                        "stop-no-instance",
                    ],
                    "description": "Override task_kind inference.",
                },
                "with_review": {
                    "type": "boolean",
                    "default": False,
                    "description": "Append a final `review` phase running the merged `pr-reviewer`.",
                },
                "format": _FORMAT_PROP,
            },
            "required": ["task"],
        },
    },
    {
        "name": "quick_scope",
        "description": (
            "Server-side scope.md generation. v0.15.0+: replaces the `agent-planner` agent for "
            "typical 'bump X', 'add Y', 'increase Z' tasks."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Free-form task description from the user."},
                "named_modules": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Module names hinted by the user (e.g. ['loki']).",
                },
                "target_envs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional: target environments. Drift slice computed only when set.",
                },
                "format": _FORMAT_PROP,
            },
            "required": ["task"],
        },
    },
    {
        "name": "session_init",
        "description": (
            "Create .agents/prompts/<slug>/ with context.md (seeded from plan_persona_fanout), "
            "findings/, tasks/, and status.json (fanout dashboard)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Session name; will be slugified."},
                "task": {"type": "string", "description": "One-line task description for context.md."},
                "modules": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional: module names to prefill into the graph snapshot.",
                },
                "current_branch": {
                    "type": "string",
                    "description": "Optional: current branch — recorded in context.md if it triggers the branch gate.",
                },
                "format": _FORMAT_PROP,
            },
            "required": ["name"],
        },
    },
    {
        "name": "session_read",
        "description": (
            "Read a file from a session dir under .agents/prompts/<slug>/. Path-validated — "
            "refuses reads outside the session dir."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Session name."},
                "file": {
                    "type": "string",
                    "description": "Relative path within the session dir (e.g. 'scope.md', 'findings/cold.md').",
                },
                "format": _FORMAT_PROP,
            },
            "required": ["name", "file"],
        },
    },
    {
        "name": "session_write",
        "description": (
            "Write content to a file inside a session dir. Path-validated. Use this for "
            "context.md, decisions.md, tasks/<NN>-<slug>.md."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Session name."},
                "file": {"type": "string", "description": "Relative path within the session dir."},
                "content": {"type": "string", "description": "Full file content."},
                "format": _FORMAT_PROP,
            },
            "required": ["name", "file", "content"],
        },
    },
    {
        "name": "session_list",
        "description": "List all files in a session dir with their sizes and mtimes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Session name."},
                "format": _FORMAT_PROP,
            },
            "required": ["name"],
        },
    },
    {
        "name": "session_status",
        "description": (
            "Live fanout dashboard for a session: phase progression with per-persona status "
            "badges (queued/running/done/blocked), persona timing, and file listing."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Session name."},
                "format": _FORMAT_PROP,
            },
            "required": ["name"],
        },
    },
    {
        "name": "session_set_status",
        "defer_loading": True,
        "description": (
            "Mutate status.json: mark a persona or phase as queued/running/done/blocked/skipped. "
            "Auto-detects whether `target` is a persona or phase id; phase status auto-rolls-up "
            "from its personas."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Session name."},
                "target": {
                    "type": "string",
                    "description": "Persona name (e.g. 'agent-infra-ops') or phase id (e.g. 'implement').",
                },
                "status": {
                    "type": "string",
                    "enum": ["queued", "running", "done", "blocked", "skipped"],
                },
                "kind": {
                    "type": "string",
                    "enum": ["persona", "phase"],
                    "description": "Optional override; auto-detected from `target`.",
                },
                "format": _FORMAT_PROP,
            },
            "required": ["name", "target", "status"],
        },
    },
]


def mcp_tool_objects() -> list[types.Tool]:
    """Return strict `mcp.types.Tool` entries (strips extension keys like `defer_loading`)."""
    out: list[types.Tool] = []
    for spec in _RAW_TOOLS:
        out.append(
            types.Tool(
                name=spec["name"],
                description=spec.get("description") or "",
                inputSchema=spec["inputSchema"],
            )
        )
    return out
