"""CodeLayer — modules, cloud_provider, component_type catalog slice."""

from __future__ import annotations

from .base import Layer
from ._util import KuberlyGraph


def _split_cold_subset(
    repo_root: str,
    scanner_methods: list[str],
    keep_node_predicate,
    keep_edge_predicate,
    layer_name: str,
) -> tuple[list[dict], list[dict]]:
    g = KuberlyGraph(str(repo_root))
    for method in scanner_methods:
        getattr(g, method)()
    nodes = [
        {**n, "layer": layer_name}
        for n in g.nodes.values()
        if keep_node_predicate(n)
    ]
    edges = [
        {**e, "layer": layer_name}
        for e in g.edges
        if keep_edge_predicate(e, g.nodes)
    ]
    return nodes, edges


class CodeLayer(Layer):
    name = "code"
    refresh_trigger = "manual"

    def scan(self, ctx: dict) -> tuple[list[dict], list[dict]]:
        repo_root = ctx.get("repo_root", ".")

        def keep_node(n: dict) -> bool:
            return n.get("type") in {"module", "cloud_provider", "component_type"}

        def keep_edge(e: dict, nodes: dict) -> bool:
            src = nodes.get(e["source"], {}).get("type")
            tgt = nodes.get(e["target"], {}).get("type")
            allowed = {"module", "cloud_provider", "component_type"}
            return src in allowed or tgt in allowed

        return _split_cold_subset(
            repo_root,
            ["scan_modules", "scan_catalog"],
            keep_node,
            keep_edge,
            "code",
        )
