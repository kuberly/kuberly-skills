"""MetaLayer — graph-of-graphs.

Runs LAST, after every other layer. Reads the freshly-populated GraphStore
and the layer registry, then emits one ``graph_layer:<name>`` node per
registered ``Layer`` plus ``feeds_into`` edges derived from
``_LAYER_PRECEDES`` (so dependents/dependees in the layer DAG show up as
graph edges users can query).

Pure introspection — no live MCP calls, no filesystem reads. Soft-degrades
when ``ctx['graph_store']`` is missing (returns empty).
"""

from __future__ import annotations

from .base import Layer


_TYPE_MAP: dict[str, str] = {
    "cold": "capstone",
    "code": "cold",
    "components": "cold",
    "applications": "cold",
    "rendered": "cold",
    "state": "cold",
    "k8s": "live",
    "argo": "live",
    "logs": "live",
    "metrics": "live",
    "traces": "live",
    "network": "derived",
    "iam": "derived",
    "image_build": "derived",
    "storage": "derived",
    "dns": "derived",
    "secrets": "derived",
    "cost": "live",
    "alert": "derived",
    "compliance": "derived",
    "dependency": "capstone",
    "meta": "meta",
}


def _layer_type(name: str) -> str:
    return _TYPE_MAP.get(name, "unknown")


class MetaLayer(Layer):
    name = "meta"
    refresh_trigger = "manual"

    def scan(self, ctx: dict) -> tuple[list[dict], list[dict]]:
        verbose = bool(ctx.get("verbose"))
        store = ctx.get("graph_store")
        # Late import to avoid a circular import (layers package -> meta ->
        # layers package).
        from . import LAYERS, _LAYER_PRECEDES

        # Stats — defensively cope with stores that don't yet expose stats().
        stats: dict = {}
        if store is not None:
            try:
                stats = store.stats() or {}
            except Exception as exc:  # pragma: no cover — store-impl bug guard
                if verbose:
                    print(f"  [MetaLayer] stats() failed: {exc}")
                stats = {}
        per_layer = stats.get("per_layer", {}) or {}

        # Collect node-types per layer for richer metadata.
        types_per_layer: dict[str, set[str]] = {}
        if store is not None:
            try:
                all_nodes = store.all_nodes() or []
            except Exception:
                all_nodes = []
            for n in all_nodes:
                lname = n.get("layer") or ""
                ntype = n.get("type") or ""
                if not lname or not ntype:
                    continue
                types_per_layer.setdefault(lname, set()).add(ntype)

        nodes: list[dict] = []
        edges: list[dict] = []
        layer_ids: dict[str, str] = {}
        for layer in LAYERS:
            lname = layer.name
            info = per_layer.get(lname, {}) or {}
            node_count = int(info.get("nodes") or 0)
            edge_count = int(info.get("edges") or 0)
            last_refresh = info.get("last_refresh", "")
            ntypes = sorted(types_per_layer.get(lname, set()))
            lid = f"graph_layer:{lname}"
            layer_ids[lname] = lid
            nodes.append(
                {
                    "id": lid,
                    "type": "graph_layer",
                    "label": lname,
                    "name": lname,
                    "layer_type": _layer_type(lname),
                    "refresh_trigger": getattr(layer, "refresh_trigger", "manual"),
                    "node_count": node_count,
                    "edge_count": edge_count,
                    "last_refresh": last_refresh,
                    "node_types": ntypes,
                }
            )

        # `_LAYER_PRECEDES[name]` is the set of layers `name` reads from. So
        # every dep -> name is a `feeds_into` edge.
        for downstream, deps in _LAYER_PRECEDES.items():
            tgt = layer_ids.get(downstream)
            if not tgt:
                continue
            for upstream in deps:
                src = layer_ids.get(upstream)
                if not src:
                    continue
                edges.append(
                    {
                        "source": src,
                        "target": tgt,
                        "relation": "feeds_into",
                    }
                )

        # Every other layer summarises into meta.
        meta_id = layer_ids.get("meta")
        if meta_id:
            for lname, lid in layer_ids.items():
                if lname == "meta":
                    continue
                edges.append(
                    {
                        "source": lid,
                        "target": meta_id,
                        "relation": "summarized_by",
                    }
                )

        if verbose:
            print(f"  [MetaLayer] emitted {len(nodes)} nodes / {len(edges)} edges")
        return nodes, edges
