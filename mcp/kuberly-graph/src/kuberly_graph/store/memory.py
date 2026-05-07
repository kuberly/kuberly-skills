"""In-memory fallback store. Same external API as LanceGraphStore but
semantic_search / find_similar return `{"error": "lancedb not installed"}`.
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path


def _embedding_text(node: dict) -> str:
    base_keys = {"id", "type", "label"}
    extras = {k: v for k, v in node.items() if k not in base_keys}
    text = (
        f"{node.get('type', '')} {node.get('label', node.get('id', ''))} "
        f"{json.dumps(extras, sort_keys=True, default=str)}"
    )
    return text[:512]


class MemoryGraphStore:
    mode = "memory"

    def __init__(self, persist_dir: Path) -> None:
        self.persist_dir = Path(persist_dir)
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self._mem_nodes: dict[str, dict] = {}
        self._mem_edges: list[dict] = []
        self._last_refresh: dict[str, str] = {}

        # Hydrate from any existing JSON sidecar so tools that open a fresh
        # process can answer queries without re-running a layer.
        self._load_sidecar()

    # -- persistence sidecar ------------------------------------------------

    def _sidecar_path(self) -> Path:
        return self.persist_dir / "graph_store.json"

    def _load_sidecar(self) -> None:
        path = self._sidecar_path()
        if not path.exists():
            return
        try:
            blob = json.loads(path.read_text())
        except Exception:
            return
        for n in blob.get("nodes", []) or []:
            if isinstance(n, dict) and n.get("id"):
                self._mem_nodes[n["id"]] = n
        for e in blob.get("edges", []) or []:
            if isinstance(e, dict):
                self._mem_edges.append(e)
        self._last_refresh = blob.get("last_refresh", {}) or {}

    def _persist_sidecar(self) -> None:
        try:
            self._sidecar_path().write_text(
                json.dumps(
                    {
                        "nodes": list(self._mem_nodes.values()),
                        "edges": self._mem_edges,
                        "last_refresh": self._last_refresh,
                    },
                    indent=2,
                    default=str,
                )
            )
        except Exception:
            pass

    # -- mutation -----------------------------------------------------------

    def upsert_nodes(self, nodes: list[dict]) -> None:
        if not nodes:
            return
        for n in nodes:
            n.setdefault("layer", "cold")
            self._mem_nodes[n["id"]] = n
        self._persist_sidecar()

    def upsert_edges(self, edges: list[dict]) -> None:
        if not edges:
            return
        seen_ids: set[str] = set()
        for e in edges:
            e.setdefault("layer", "cold")
            eid = (
                f"{e.get('source','')}->{e.get('target','')}|"
                f"{e.get('relation','')}|{e.get('layer','cold')}"
            )
            if eid in seen_ids:
                continue
            seen_ids.add(eid)
            self._mem_edges.append(e)
        self._persist_sidecar()

    def replace_layer(self, layer: str, nodes: list[dict], edges: list[dict]) -> None:
        self._mem_nodes = {
            nid: n for nid, n in self._mem_nodes.items() if n.get("layer") != layer
        }
        self._mem_edges = [e for e in self._mem_edges if e.get("layer") != layer]
        for n in nodes:
            n["layer"] = layer
        for e in edges:
            e["layer"] = layer
        self.upsert_nodes(nodes)
        self.upsert_edges(edges)
        self._last_refresh[layer] = _dt.datetime.now(
            _dt.timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._persist_sidecar()

    # -- queries ------------------------------------------------------------

    def all_nodes(self, layer: str | None = None) -> list[dict]:
        if layer is None:
            return list(self._mem_nodes.values())
        return [n for n in self._mem_nodes.values() if n.get("layer") == layer]

    def all_edges(self, layer: str | None = None) -> list[dict]:
        if layer is None:
            return list(self._mem_edges)
        return [e for e in self._mem_edges if e.get("layer") == layer]

    def semantic_search(
        self, query: str, layer: str | None = None, limit: int = 10
    ) -> list[dict]:
        return [{"error": "lancedb not installed"}]

    def find_similar(self, node_id: str, limit: int = 10) -> list[dict]:
        return [{"error": "lancedb not installed"}]

    def stats(self) -> dict:
        per_layer: dict[str, dict] = {}
        for n in self._mem_nodes.values():
            layer = n.get("layer", "cold")
            per_layer.setdefault(layer, {"nodes": 0, "edges": 0})
            per_layer[layer]["nodes"] += 1
        for e in self._mem_edges:
            layer = e.get("layer", "cold")
            per_layer.setdefault(layer, {"nodes": 0, "edges": 0})
            per_layer[layer]["edges"] += 1
        for layer, ts in self._last_refresh.items():
            per_layer.setdefault(layer, {"nodes": 0, "edges": 0})
            per_layer[layer]["last_refresh"] = ts
        return {
            "mode": self.mode,
            "persist_dir": str(self.persist_dir),
            "total_nodes": len(self._mem_nodes),
            "total_edges": len(self._mem_edges),
            "per_layer": per_layer,
        }
