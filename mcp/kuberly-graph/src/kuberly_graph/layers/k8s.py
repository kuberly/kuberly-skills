"""K8sLayer — live k8s resources via MCP `resources_list`."""

from __future__ import annotations

import asyncio

from .base import Layer

DEFAULT_K8S_KINDS: list[tuple[str, str]] = [
    ("apps/v1", "Deployment"),
    ("apps/v1", "StatefulSet"),
    ("v1", "Service"),
    ("networking.k8s.io/v1", "Ingress"),
    ("v1", "ServiceAccount"),
    ("v1", "ConfigMap"),
    ("v1", "Secret"),
]


class K8sLayer(Layer):
    name = "k8s"
    refresh_trigger = "on-event:k8s"

    def scan(self, ctx: dict) -> tuple[list[dict], list[dict]]:
        endpoint = ctx.get("mcp_endpoint")
        verbose = bool(ctx.get("verbose"))
        if not endpoint:
            if verbose:
                print("  [K8sLayer] skip — no mcp_endpoint in ctx")
            return [], []

        existing_rendered_ids: set[str] = set(
            ctx.get("_existing_rendered_ids", set())
        )

        from ..client import fetch_live_resources

        try:
            live = asyncio.run(fetch_live_resources(endpoint, DEFAULT_K8S_KINDS))
        except ConnectionError:
            raise
        except Exception as exc:
            raise ConnectionError(f"K8sLayer MCP call failed: {exc}") from exc

        nodes: list[dict] = []
        edges: list[dict] = []
        live_index: dict[tuple[str, str, str], str] = {}

        for (api_version, kind), resources in live.items():
            for r in resources:
                meta = r.get("metadata") or {}
                name = meta.get("name")
                if not name:
                    continue
                ns = meta.get("namespace") or ""
                rid = f"k8s_resource:{ns}/{kind}/{name}"
                nodes.append(
                    {
                        "id": rid,
                        "type": "k8s_resource",
                        "label": f"{kind}/{name}",
                        "apiVersion": api_version,
                        "kind": kind,
                        "namespace": ns,
                        "name": name,
                        "creation_timestamp": meta.get("creationTimestamp", ""),
                    }
                )
                live_index[(kind, ns, name)] = rid

        for rid_full in existing_rendered_ids:
            try:
                _, body = rid_full.split(":", 1)
                env_app, kind, name = body.rsplit("/", 2)
                _env, _app = env_app.split("/", 1)
            except ValueError:
                continue
            for (k_kind, _k_ns, k_name), live_id in live_index.items():
                if k_kind == kind and k_name == name:
                    edges.append(
                        {
                            "source": rid_full,
                            "target": live_id,
                            "relation": "live_match",
                        }
                    )

        if verbose:
            print(f"  [K8sLayer] emitted {len(nodes)} nodes / {len(edges)} edges")
        return nodes, edges
