"""ApplicationsLayer — application JSON files."""

from __future__ import annotations

from .base import Layer
from .code import _split_cold_subset


class ApplicationsLayer(Layer):
    name = "applications"
    refresh_trigger = "manual"

    def scan(self, ctx: dict) -> tuple[list[dict], list[dict]]:
        repo_root = ctx.get("repo_root", ".")

        def keep_node(n: dict) -> bool:
            return n.get("type") == "application"

        def keep_edge(e: dict, nodes: dict) -> bool:
            return e.get("relation") == "deploys"

        return _split_cold_subset(
            repo_root,
            ["scan_environments", "scan_applications"],
            keep_node,
            keep_edge,
            "applications",
        )
