"""Route MCP tool names to `KuberlyPlatform` methods — no I/O, no formatting."""

from __future__ import annotations

from typing import Any


def dispatch_tool(graph: Any, name: str, args: dict[str, Any]) -> Any:
    """Return a plain Python result for `render_tool_result`."""
    if name == "query_nodes":
        return graph.query_nodes(
            node_type=args.get("node_type"),
            environment=args.get("environment"),
            name_contains=args.get("name_contains"),
        )
    if name == "query_resources":
        return graph.query_resources(
            environment=args.get("environment"),
            module=args.get("module"),
            resource_type=args.get("resource_type"),
            name_contains=args.get("name_contains"),
            include_redacted=args.get("include_redacted", True),
        )
    if name == "find_docs":
        return graph.find_docs(
            query=args.get("query", ""),
            kind=args.get("kind"),
            semantic=args.get("semantic", True),
            limit=args.get("limit", 20),
        )
    if name == "graph_index":
        return graph.graph_index()
    if name == "query_k8s":
        return graph.query_k8s(
            environment=args.get("environment"),
            namespace=args.get("namespace"),
            kind=args.get("kind"),
            name_contains=args.get("name_contains"),
            label_selector=args.get("label_selector"),
            include_redacted=args.get("include_redacted", True),
        )
    if name == "get_node":
        return graph.get_neighbors(args["node"])
    if name == "get_neighbors":
        return graph.get_neighbors(args["node"])
    if name == "blast_radius":
        return graph.blast_radius(
            args["node"],
            direction=args.get("direction", "both"),
            max_depth=args.get("max_depth", 20),
        )
    if name == "shortest_path":
        return graph.shortest_path(args["source"], args["target"])
    if name == "drift":
        return graph.cross_env_drift()
    if name == "stats":
        return graph.compute_stats()
    if name == "plan_persona_fanout":
        return graph.plan_persona_fanout(
            task=args["task"],
            named_modules=args.get("named_modules"),
            target_envs=args.get("target_envs"),
            current_branch=args.get("current_branch"),
            session_name=args.get("session_name"),
            task_kind=args.get("task_kind"),
            with_review=bool(args.get("with_review", False)),
        )
    if name == "quick_scope":
        return graph.quick_scope(
            task=args["task"],
            named_modules=args.get("named_modules"),
            target_envs=args.get("target_envs"),
        )
    if name == "session_init":
        return graph.session_init(
            name=args["name"],
            task=args.get("task"),
            modules=args.get("modules"),
            current_branch=args.get("current_branch"),
        )
    if name == "session_read":
        return graph.session_read(args["name"], args["file"])
    if name == "session_write":
        return graph.session_write(args["name"], args["file"], args["content"])
    if name == "session_list":
        return graph.session_list(args["name"])
    if name == "session_status":
        return graph.session_status(args["name"])
    if name == "session_set_status":
        return graph.session_set_status(
            name=args["name"],
            target=args["target"],
            status=args["status"],
            kind=args.get("kind"),
        )
    raise ValueError(f"Unknown tool: {name}")
