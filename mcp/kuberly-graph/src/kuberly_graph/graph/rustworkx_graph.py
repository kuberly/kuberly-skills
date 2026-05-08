"""Wrapper around rustworkx.PyDiGraph with id <-> idx maps.

The store keeps payload dicts canonically; this graph layer is built
from those dicts on demand for traversal queries (BFS, shortest path,
blast radius).

v0.53.0 — ``RxGraph.cached_from_store`` memoises a single RxGraph per
``(persist_dir, cache_epoch)`` so repeated ``shortest_path`` /
``blast_radius`` / ``trace_data_flow`` calls don't rebuild the rustworkx
graph from scratch. The cache is invalidated whenever
``regenerate_graph`` / ``regenerate_layer`` bumps the global cache epoch.
"""

from __future__ import annotations

from collections import deque
from typing import Iterable

import rustworkx as rx


class RxGraph:
    """Directed graph wrapper with string id keys.

    Internal rustworkx integer indices stay private; everything exposed to
    the tools is keyed by the original string id.
    """

    def __init__(self) -> None:
        self.g: rx.PyDiGraph = rx.PyDiGraph(check_cycle=False, multigraph=True)
        self._id_to_idx: dict[str, int] = {}
        self._idx_to_id: dict[int, str] = {}
        self._edge_payloads: list[dict] = []

    # -- mutation -----------------------------------------------------------

    def add_node(self, node: dict) -> int:
        nid = node["id"]
        if nid in self._id_to_idx:
            # Update payload in place.
            idx = self._id_to_idx[nid]
            self.g[idx] = node
            return idx
        idx = self.g.add_node(node)
        self._id_to_idx[nid] = idx
        self._idx_to_id[idx] = nid
        return idx

    def add_edge(self, source_id: str, target_id: str, payload: dict) -> None:
        # Auto-create stub nodes when an endpoint is missing — matches the
        # legacy behaviour where `component_type:` targets are dangling.
        if source_id not in self._id_to_idx:
            self.add_node({"id": source_id, "type": "unknown", "label": source_id})
        if target_id not in self._id_to_idx:
            self.add_node({"id": target_id, "type": "unknown", "label": target_id})
        self.g.add_edge(
            self._id_to_idx[source_id], self._id_to_idx[target_id], payload
        )
        self._edge_payloads.append(payload)

    # -- factory ------------------------------------------------------------

    @classmethod
    def from_store(cls, nodes: Iterable[dict], edges: Iterable[dict]) -> "RxGraph":
        g = cls()
        for n in nodes:
            if "id" in n:
                g.add_node(n)
        for e in edges:
            src = e.get("source")
            tgt = e.get("target")
            if not src or not tgt:
                continue
            g.add_edge(src, tgt, dict(e))
        return g

    # Process-local cache for `cached_from_store`. Key: (persist_dir, epoch).
    _CACHE: dict[tuple[str, int], "RxGraph"] = {}

    @classmethod
    def cached_from_store(cls, store, persist_dir: str = "") -> "RxGraph":
        """Return a process-cached RxGraph keyed by (persist_dir, cache_epoch).

        The graph is rebuilt only when the cache epoch advances (via a
        ``regenerate_*`` call) so consecutive ``shortest_path`` / BFS-style
        queries within the same epoch share a single rustworkx graph. The
        cache holds at most one entry per persist_dir to bound memory.
        """
        from ..cache import cache_epoch

        epoch = cache_epoch()
        key = (str(persist_dir), epoch)
        cached = cls._CACHE.get(key)
        if cached is not None:
            return cached
        # Drop stale entries for the same persist_dir, different epoch.
        cls._CACHE = {
            k: v for k, v in cls._CACHE.items() if k[0] != str(persist_dir)
        }
        nodes = store.all_nodes()
        edges = store.all_edges()
        graph = cls.from_store(nodes, edges)
        cls._CACHE[key] = graph
        return graph

    # -- introspection ------------------------------------------------------

    def has_node(self, node_id: str) -> bool:
        return node_id in self._id_to_idx

    def get_node(self, node_id: str) -> dict | None:
        idx = self._id_to_idx.get(node_id)
        if idx is None:
            return None
        return self.g[idx]

    def all_nodes(self, layer: str | None = None) -> list[dict]:
        out: list[dict] = []
        for idx in self.g.node_indices():
            payload = self.g[idx]
            if layer is None or payload.get("layer") == layer:
                out.append(payload)
        return out

    def all_edges(self) -> list[dict]:
        out: list[dict] = []
        for src_idx, tgt_idx, data in self.g.weighted_edge_list():
            payload = dict(data) if isinstance(data, dict) else {}
            payload.setdefault("source", self._idx_to_id.get(src_idx))
            payload.setdefault("target", self._idx_to_id.get(tgt_idx))
            out.append(payload)
        return out

    # -- queries ------------------------------------------------------------

    def neighbors(self, node_id: str, direction: str = "both") -> list[dict]:
        idx = self._id_to_idx.get(node_id)
        if idx is None:
            return []
        out: list[dict] = []
        if direction in ("downstream", "out", "both"):
            for tgt in self.g.successor_indices(idx):
                out.append(self.g[tgt])
        if direction in ("upstream", "in", "both"):
            for src in self.g.predecessor_indices(idx):
                out.append(self.g[src])
        return out

    def incoming_edges(self, node_id: str) -> list[dict]:
        idx = self._id_to_idx.get(node_id)
        if idx is None:
            return []
        out: list[dict] = []
        for src_idx, _tgt_idx, data in self.g.in_edges(idx):
            payload = dict(data) if isinstance(data, dict) else {}
            payload.setdefault("source", self._idx_to_id.get(src_idx))
            payload.setdefault("target", node_id)
            out.append(payload)
        return out

    def outgoing_edges(self, node_id: str) -> list[dict]:
        idx = self._id_to_idx.get(node_id)
        if idx is None:
            return []
        out: list[dict] = []
        for _src_idx, tgt_idx, data in self.g.out_edges(idx):
            payload = dict(data) if isinstance(data, dict) else {}
            payload.setdefault("source", node_id)
            payload.setdefault("target", self._idx_to_id.get(tgt_idx))
            out.append(payload)
        return out

    def bfs(self, node_id: str, direction: str = "both", max_depth: int = 20) -> dict[str, int]:
        """Return a mapping {visited_id: depth}, excluding the start node."""
        idx = self._id_to_idx.get(node_id)
        if idx is None:
            return {}

        visited: dict[int, int] = {idx: 0}
        queue: deque[tuple[int, int]] = deque([(idx, 0)])
        while queue:
            cur, d = queue.popleft()
            if d >= max_depth:
                continue
            neighbors: list[int] = []
            if direction in ("downstream", "out", "both"):
                neighbors.extend(self.g.successor_indices(cur))
            if direction in ("upstream", "in", "both"):
                neighbors.extend(self.g.predecessor_indices(cur))
            for nb in neighbors:
                if nb in visited:
                    continue
                visited[nb] = d + 1
                queue.append((nb, d + 1))

        visited.pop(idx, None)
        return {self._idx_to_id[i]: depth for i, depth in visited.items()}

    def shortest_path(self, src: str, dst: str) -> list[str]:
        """Undirected BFS shortest path (matches legacy semantics)."""
        if src not in self._id_to_idx or dst not in self._id_to_idx:
            return []
        if src == dst:
            return [src]

        src_idx = self._id_to_idx[src]
        dst_idx = self._id_to_idx[dst]
        # Build undirected adjacency for BFS — reuse rustworkx neighbors.
        parent: dict[int, int] = {src_idx: -1}
        queue: deque[int] = deque([src_idx])
        while queue:
            cur = queue.popleft()
            if cur == dst_idx:
                # Reconstruct.
                path_idx: list[int] = []
                node = cur
                while node != -1:
                    path_idx.append(node)
                    node = parent[node]
                path_idx.reverse()
                return [self._idx_to_id[i] for i in path_idx]
            for nb in list(self.g.successor_indices(cur)) + list(
                self.g.predecessor_indices(cur)
            ):
                if nb not in parent:
                    parent[nb] = cur
                    queue.append(nb)
        return []

    def blast_radius(
        self,
        node_id: str,
        direction: str = "both",
        max_depth: int = 20,
    ) -> dict:
        """Same shape as legacy KuberlyGraph.blast_radius output."""
        if node_id not in self._id_to_idx:
            return {"error": f"No node matching '{node_id}'"}
        result: dict = {
            "node": node_id,
            "node_info": self.get_node(node_id) or {},
        }
        if direction in ("downstream", "both"):
            ds = self.bfs(node_id, direction="downstream", max_depth=max_depth)
            result["downstream"] = {
                nid: {"depth": d, **(self.get_node(nid) or {})}
                for nid, d in sorted(ds.items(), key=lambda kv: kv[1])
            }
            result["downstream_count"] = len(ds)
        if direction in ("upstream", "both"):
            us = self.bfs(node_id, direction="upstream", max_depth=max_depth)
            result["upstream"] = {
                nid: {"depth": d, **(self.get_node(nid) or {})}
                for nid, d in sorted(us.items(), key=lambda kv: kv[1])
            }
            result["upstream_count"] = len(us)
        return result
