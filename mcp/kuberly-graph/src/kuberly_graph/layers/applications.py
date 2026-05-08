"""ApplicationsLayer — application JSON files."""

from __future__ import annotations

from .base import Layer
from .code import _split_cold_subset


class ApplicationsLayer(Layer):
    name = "applications"
    refresh_trigger = "manual"

    def to_document(self, node: dict) -> str:
        """Render an application node as a deployment-shape sentence.

        Pulls in env, image, replica counts, and key resource specs so
        semantic_search can answer "applications using bedrock" or
        "frontend apps in dev".
        """
        ntype = node.get("type", "")
        if ntype != "application":
            return super().to_document(node)
        name = node.get("label") or node.get("id", "")
        env = node.get("environment") or ""
        image = node.get("image") or ""
        secrets = node.get("secret_count", 0)
        env_vars = node.get("env_var_count", 0)
        parts = [f"Application {name}"]
        if env:
            parts.append(f"deployed to env {env}.")
        if image:
            parts.append(f"Container image {image}.")
        if env_vars:
            parts.append(f"{env_vars} env variables.")
        if secrets:
            parts.append(f"{secrets} secret references.")
        return " ".join(parts)[:512]

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
