"""Semantic-search tools — direct GraphStore queries."""

from __future__ import annotations

from pathlib import Path

from ..server import SERVER_CONFIG, mcp
from ..store import open_store


def _resolve_persist(persist_dir: str | None) -> str:
    return persist_dir or SERVER_CONFIG.get("persist_dir", ".kuberly")


@mcp.tool()
def semantic_search(
    query: str,
    layer: str | None = None,
    limit: int = 10,
    persist_dir: str | None = None,
) -> dict:
    """Vector similarity search over node embeddings (LanceDB). Returns
    top-N hits with similarity scores. Falls back to
    `{"error": "lancedb not installed"}` when the optional dep is missing.
    """
    store = open_store(Path(_resolve_persist(persist_dir)).resolve())
    hits = store.semantic_search(query=query, layer=layer, limit=int(limit))
    if hits and isinstance(hits[0], dict) and "error" in hits[0]:
        return {"error": hits[0]["error"]}
    return {"query": query, "layer": layer, "limit": int(limit), "hits": hits}


@mcp.tool()
def find_similar(
    node_id: str,
    limit: int = 10,
    persist_dir: str | None = None,
) -> dict:
    """Find nodes whose embeddings are nearest to a given node's. Returns
    `{"error": "lancedb not installed"}` when the optional dep is missing.
    """
    store = open_store(Path(_resolve_persist(persist_dir)).resolve())
    hits = store.find_similar(node_id=node_id, limit=int(limit))
    if hits and isinstance(hits[0], dict) and "error" in hits[0]:
        return {"error": hits[0]["error"]}
    return {"node_id": node_id, "limit": int(limit), "hits": hits}


@mcp.tool()
def graph_stats(persist_dir: str | None = None) -> dict:
    """Per-layer node/edge counts and last-refresh timestamps from the GraphStore."""
    store = open_store(Path(_resolve_persist(persist_dir)).resolve())
    return store.stats()
