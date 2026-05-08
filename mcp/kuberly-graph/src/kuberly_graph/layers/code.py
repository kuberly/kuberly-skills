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

    def to_document(self, node: dict) -> str:
        """Render an OpenTofu module / component / cloud-provider node."""
        ntype = node.get("type", "")
        label = node.get("label") or node.get("id", "")
        if ntype == "module":
            cloud = node.get("cloud") or node.get("provider") or ""
            inputs = node.get("inputs") or node.get("input_names") or []
            outputs = node.get("outputs") or node.get("output_names") or []
            depends = node.get("dependencies") or []
            parts = [f"OpenTofu module {label}"]
            if cloud:
                parts.append(f"in cloud {cloud}.")
            if inputs:
                parts.append(f"Inputs: {', '.join(str(x) for x in inputs[:8])}.")
            if outputs:
                parts.append(f"Outputs: {', '.join(str(x) for x in outputs[:8])}.")
            if depends:
                parts.append(f"Depends on: {', '.join(str(x) for x in depends[:6])}.")
            return " ".join(parts)[:512]
        if ntype == "component":
            cluster = node.get("cluster") or node.get("env") or ""
            return f"Component {label} for cluster {cluster}."[:512]
        if ntype == "cloud_provider":
            return f"Cloud provider {label} (root of the IaC tree)."[:512]
        if ntype == "component_type":
            return f"Component type {label}."[:512]
        return super().to_document(node)

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
