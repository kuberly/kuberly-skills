"""Meta-graph tools — graph-of-graphs introspection."""

from __future__ import annotations

from pathlib import Path

from ..layers import LAYERS, _LAYER_PRECEDES, topo_sort_layers
from ..server import SERVER_CONFIG, mcp
from ..store import open_store


def _resolve_persist(persist_dir: str | None) -> str:
    return persist_dir or SERVER_CONFIG.get("persist_dir", ".kuberly")


@mcp.tool()
def meta_overview(persist_dir: str | None = None) -> dict:
    """Return the graph-of-graphs summary: ``graph_layer`` nodes (one per
    registered layer), ``feeds_into`` edges from the layer-precedence DAG,
    a topological run order, and totals.

    Pure GraphStore query — call this AFTER ``regenerate_layer meta`` (or
    ``regenerate_all``) so the persisted ``graph_layer`` nodes are fresh.
    Soft-degrades to the in-memory layer-registry view when the meta layer
    has never been refreshed.
    """
    store = open_store(Path(_resolve_persist(persist_dir)).resolve())
    layer_nodes = [
        n
        for n in store.all_nodes(layer="meta")
        if n.get("type") == "graph_layer"
    ]
    layer_edges = [
        e
        for e in store.all_edges(layer="meta")
        if e.get("relation") in ("feeds_into", "summarized_by")
    ]

    # Always-available view, even when the meta layer is stale: derive the
    # topological order from the static layer registry.
    registered = [layer.name for layer in LAYERS]
    try:
        order = topo_sort_layers(registered)
    except Exception:
        order = registered

    # Build a quick {layer_name: feeds_into-targets} map from the persisted
    # edges (dependency view), or fall back to the static registry.
    persisted_feeds: dict[str, list[str]] = {}
    for e in layer_edges:
        if e.get("relation") != "feeds_into":
            continue
        src = e.get("source", "")
        tgt = e.get("target", "")
        if src.startswith("graph_layer:") and tgt.startswith("graph_layer:"):
            persisted_feeds.setdefault(src.split(":", 1)[1], []).append(
                tgt.split(":", 1)[1]
            )

    static_feeds: dict[str, list[str]] = {}
    for downstream, deps in _LAYER_PRECEDES.items():
        for upstream in deps:
            static_feeds.setdefault(upstream, []).append(downstream)

    return {
        "layer_count": len(layer_nodes) or len(registered),
        "edge_count": len(layer_edges),
        "registered_layers": registered,
        "topo_order": order,
        "graph_layers": layer_nodes,
        "feeds_into_edges": layer_edges,
        "static_feeds_into": static_feeds,
        "persisted_feeds_into": persisted_feeds,
        "persist_dir": _resolve_persist(persist_dir),
    }
