"""ArgoLayer — Argo CD Application objects via MCP `resources_list`."""

from __future__ import annotations

import asyncio

from .base import Layer


class ArgoLayer(Layer):
    name = "argo"
    refresh_trigger = "on-event:k8s"

    def scan(self, ctx: dict) -> tuple[list[dict], list[dict]]:
        endpoint = ctx.get("mcp_endpoint")
        verbose = bool(ctx.get("verbose"))
        if not endpoint:
            if verbose:
                print("  [ArgoLayer] skip — no mcp_endpoint in ctx")
            return [], []

        existing_app_ids: set[str] = set(ctx.get("_existing_app_ids", set()))

        from ..client import fetch_live_resources

        argo_kinds = [("argoproj.io/v1alpha1", "Application")]
        try:
            live = asyncio.run(fetch_live_resources(endpoint, argo_kinds))
        except ConnectionError:
            raise
        except Exception as exc:
            raise ConnectionError(f"ArgoLayer MCP call failed: {exc}") from exc

        items = live.get(("argoproj.io/v1alpha1", "Application"), []) or []
        if not items and verbose:
            print(
                "  [ArgoLayer] no Application objects returned (CRD may not be exposed)"
            )

        nodes: list[dict] = []
        edges: list[dict] = []
        for r in items:
            meta = r.get("metadata") or {}
            name = meta.get("name")
            if not name:
                continue
            ns = meta.get("namespace") or ""
            spec = r.get("spec") or {}
            source = spec.get("source") or {}
            dest = spec.get("destination") or {}
            status = r.get("status") or {}
            sync = status.get("sync") or {}
            health = status.get("health") or {}

            argo_id = f"argo_app:{ns}/{name}"
            nodes.append(
                {
                    "id": argo_id,
                    "type": "argo_app",
                    "label": name,
                    "namespace": ns,
                    "name": name,
                    "sync_status": sync.get("status", ""),
                    "health_status": health.get("status", ""),
                    "repo_url": source.get("repoURL", ""),
                    "path": source.get("path", ""),
                    "target_revision": source.get("targetRevision", ""),
                    "dest_namespace": dest.get("namespace", ""),
                    "dest_server": dest.get("server", ""),
                }
            )
            for cold_app_id in existing_app_ids:
                try:
                    _, body = cold_app_id.split(":", 1)
                    _env, app_name = body.split("/", 1)
                except ValueError:
                    continue
                if app_name == name:
                    edges.append(
                        {
                            "source": argo_id,
                            "target": cold_app_id,
                            "relation": "tracks",
                        }
                    )

        if verbose:
            print(f"  [ArgoLayer] emitted {len(nodes)} nodes / {len(edges)} edges")
        return nodes, edges
