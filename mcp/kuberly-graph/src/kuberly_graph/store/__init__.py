"""GraphStore — LanceDB-backed; memory fallback if lancedb is missing."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Protocol

from .memory import MemoryGraphStore

_LANCE_WARN_PRINTED = False


class GraphStore(Protocol):
    persist_dir: Path
    mode: str

    def upsert_nodes(self, nodes: list[dict]) -> None: ...
    def upsert_edges(self, edges: list[dict]) -> None: ...
    def replace_layer(self, layer: str, nodes: list[dict], edges: list[dict]) -> None: ...
    def all_nodes(self, layer: str | None = None) -> list[dict]: ...
    def all_edges(self, layer: str | None = None) -> list[dict]: ...
    def semantic_search(self, query: str, layer: str | None = None, limit: int = 10) -> list[dict]: ...
    def find_similar(self, node_id: str, limit: int = 10) -> list[dict]: ...
    def stats(self) -> dict: ...


def open_store(persist_dir: Path | str) -> "GraphStore":
    """Try LanceDB; fall back to MemoryGraphStore on import error."""
    global _LANCE_WARN_PRINTED
    pd = Path(persist_dir)
    pd.mkdir(parents=True, exist_ok=True)
    try:
        from .lance import LanceGraphStore
    except Exception as exc:
        if not _LANCE_WARN_PRINTED:
            print(
                f"WARNING: lancedb not installed ({exc}); semantic_search/find_similar disabled.",
                file=sys.stderr,
            )
            _LANCE_WARN_PRINTED = True
        return MemoryGraphStore(pd)
    try:
        return LanceGraphStore(pd)
    except Exception as exc:
        if not _LANCE_WARN_PRINTED:
            print(
                f"WARNING: lancedb available but failed to open ({exc}); falling back to memory store.",
                file=sys.stderr,
            )
            _LANCE_WARN_PRINTED = True
        return MemoryGraphStore(pd)


__all__ = ["GraphStore", "open_store", "MemoryGraphStore"]
