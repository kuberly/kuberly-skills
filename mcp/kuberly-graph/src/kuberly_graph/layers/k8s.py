"""K8sLayer — live k8s resources via MCP `resources_list`."""

from __future__ import annotations

from .base import Layer

DEFAULT_K8S_KINDS: list[tuple[str, str]] = [
    ("apps/v1", "Deployment"),
    ("apps/v1", "StatefulSet"),
    ("apps/v1", "DaemonSet"),
    ("apps/v1", "ReplicaSet"),
    ("batch/v1", "Job"),
    ("v1", "Pod"),
    ("v1", "Node"),
    ("v1", "Service"),
    ("networking.k8s.io/v1", "Ingress"),
    ("v1", "ServiceAccount"),
    ("v1", "ConfigMap"),
    ("v1", "Secret"),
    # Karpenter — Pod→Node→NodeClaim→NodePool→EC2NodeClass chain.
    ("karpenter.sh/v1", "NodeClaim"),
    ("karpenter.sh/v1", "NodePool"),
    ("karpenter.k8s.aws/v1", "EC2NodeClass"),
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

        from ..client import fetch_live_resources_sync

        try:
            live = fetch_live_resources_sync(endpoint, DEFAULT_K8S_KINDS)
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
                # Preserve full metadata + spec on the node so DependencyLayer
                # can read ownerReferences / labels / spec.nodeName etc. without
                # hitting the live MCP again.
                spec = r.get("spec") if isinstance(r.get("spec"), dict) else {}
                labels = meta.get("labels") if isinstance(meta.get("labels"), dict) else {}
                owner_refs = meta.get("ownerReferences") if isinstance(meta.get("ownerReferences"), list) else []
                node_name = ""
                if kind == "Pod" and isinstance(spec, dict):
                    node_name = spec.get("nodeName") or ""
                node_class_ref = ""
                if kind == "NodePool" and isinstance(spec, dict):
                    tpl = spec.get("template") or {}
                    tpl_spec = (tpl.get("spec") or {}) if isinstance(tpl, dict) else {}
                    ncr = tpl_spec.get("nodeClassRef") or {}
                    if isinstance(ncr, dict):
                        node_class_ref = ncr.get("name") or ""
                provider_id = ""
                if kind == "Node" and isinstance(spec, dict):
                    provider_id = spec.get("providerID") or ""
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
                        "labels": labels,
                        "owner_references": owner_refs,
                        "node_name": node_name,
                        "node_class_ref": node_class_ref,
                        "provider_id": provider_id,
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
