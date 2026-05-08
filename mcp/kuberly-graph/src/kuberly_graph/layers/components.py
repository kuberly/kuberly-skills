"""ComponentsLayer — environments, components, shared-infra slice."""

from __future__ import annotations

from .base import Layer
from .code import _split_cold_subset


class ComponentsLayer(Layer):
    name = "components"
    refresh_trigger = "manual"

    def scan(self, ctx: dict) -> tuple[list[dict], list[dict]]:
        repo_root = ctx.get("repo_root", ".")

        def keep_node(n: dict) -> bool:
            return n.get("type") in {"environment", "component", "shared-infra"}

        def keep_edge(e: dict, nodes: dict) -> bool:
            src_type = nodes.get(e["source"], {}).get("type")
            allowed_src = {"environment", "component", "shared-infra"}
            return src_type in allowed_src

        return _split_cold_subset(
            repo_root,
            [
                "scan_environments",
                "scan_modules",
                "link_components_to_modules",
            ],
            keep_node,
            keep_edge,
            "components",
        )
