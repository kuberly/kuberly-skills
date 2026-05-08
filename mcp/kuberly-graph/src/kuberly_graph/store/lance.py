"""LanceDB-backed GraphStore.

Auto-embeds node embedding text via the `sentence-transformers/all-MiniLM-L6-v2`
model wired through LanceDB's embedding registry. Two tables:
  - `nodes`  (id, type, layer, label, metadata, embedding_text + auto vector)
  - `edges`  (id, source, target, relation, layer, metadata)

The in-memory mirror (self._mem_nodes / self._mem_edges) is hydrated on
open so freshly-spawned tool processes can answer query_nodes /
get_neighbors / blast_radius without a re-scan.
"""

from __future__ import annotations

import datetime as _dt
import json
import sys
from pathlib import Path

import lancedb
import pyarrow as pa


# Lazy-init the embedding function so the model only downloads once per
# process. The registry returns a singleton anyway but we avoid re-computing
# the schema dim if not strictly needed.
_EMBED = None


def _embed_fn():
    global _EMBED
    if _EMBED is None:
        from lancedb.embeddings import get_registry

        registry = get_registry()
        _EMBED = registry.get("sentence-transformers").create(
            name="all-MiniLM-L6-v2",
        )
    return _EMBED


def _embedding_text(node: dict) -> str:
    """Pick the text the embedding model encodes for this node.

    Preferred path: the orchestrator pre-renders ``node["_embedding_doc"]``
    via ``Layer.to_document()`` — a layer-aware sentence-shaped summary.
    Fallback (for layers that haven't customised their template, or for
    legacy nodes ingested through MemoryGraphStore tests): the generic
    "<type> <label> {extras-json}" shape we used pre-v0.59.
    """
    doc = node.get("_embedding_doc")
    if isinstance(doc, str) and doc.strip():
        return doc[:512]
    base_keys = {"id", "type", "label", "_embedding_doc"}
    extras = {k: v for k, v in node.items() if k not in base_keys}
    text = (
        f"{node.get('type', '')} {node.get('label', node.get('id', ''))} "
        f"{json.dumps(extras, sort_keys=True, default=str)}"
    )
    return text[:512]


def _stringify_meta(node: dict) -> str:
    """JSON-encode arbitrary node metadata so LanceDB sees a single string column."""
    out: dict = {}
    for k, v in node.items():
        if k == "_embedding_doc":
            # Internal field — used only by _embedding_text to drive the
            # vector embedding. Not part of the node's user-visible metadata.
            continue
        if v is None:
            continue
        if isinstance(v, (str, int, float, bool)):
            out[k] = v
        elif isinstance(v, (list, dict, tuple)):
            try:
                out[k] = v if isinstance(v, (list, dict)) else list(v)
            except Exception:
                out[k] = str(v)
        else:
            out[k] = str(v)
    try:
        return json.dumps(out, default=str)
    except Exception:
        return "{}"


def _parse_meta(blob: str) -> dict:
    if not blob:
        return {}
    try:
        d = json.loads(blob)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


class LanceGraphStore:
    mode = "lance"

    def __init__(self, persist_dir: Path) -> None:
        self.persist_dir = Path(persist_dir)
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        lance_dir = self.persist_dir / "lance"
        lance_dir.mkdir(parents=True, exist_ok=True)
        # v0.51.1: keep meta sidecar inside the lance store dir so the only
        # filesystem footprint outside <persist_dir> is the LanceDB store
        # itself. Top-level <persist_dir> stays empty after a regenerate.
        self._lance_dir = lance_dir
        self._db = lancedb.connect(str(lance_dir))

        # Try to bring the embedding model up. If the registry import or the
        # model download fails we want this whole class to fail open so the
        # caller falls back to MemoryGraphStore.
        embed = _embed_fn()
        self._embed = embed
        self._dims = embed.ndims()

        self._nodes_schema = pa.schema(
            [
                pa.field("id", pa.string()),
                pa.field("type", pa.string()),
                pa.field("layer", pa.string()),
                pa.field("label", pa.string()),
                pa.field("metadata", pa.string()),
                pa.field("embedding_text", pa.string()),
                pa.field("vector", pa.list_(pa.float32(), self._dims)),
            ]
        )
        self._edges_schema = pa.schema(
            [
                pa.field("id", pa.string()),
                pa.field("source", pa.string()),
                pa.field("target", pa.string()),
                pa.field("relation", pa.string()),
                pa.field("layer", pa.string()),
                pa.field("metadata", pa.string()),
            ]
        )

        self._nodes = self._open_or_create("nodes", self._nodes_schema)
        self._edges = self._open_or_create("edges", self._edges_schema)

        # Phase 8M — scalar indices on lookup-hot columns. Idempotent
        # (replace=True). Soft-degrade if the running LanceDB version
        # doesn't expose the API we expect; never raise.
        self._scalar_index_status: dict[str, str] = {}
        self._ensure_scalar_indices()

        self._last_refresh: dict[str, str] = {}
        meta_file = self._lance_dir / "lance_meta.json"
        # Migrate any legacy v0.51.0-or-earlier sidecar at the top level
        # into the lance subdir so we don't break a freshly-upgraded store.
        legacy_meta = self.persist_dir / "lance_meta.json"
        if legacy_meta.exists() and not meta_file.exists():
            try:
                meta_file.write_text(legacy_meta.read_text())
                legacy_meta.unlink()
            except Exception:
                pass
        if meta_file.exists():
            try:
                self._last_refresh = json.loads(meta_file.read_text()).get(
                    "last_refresh", {}
                )
            except Exception:
                self._last_refresh = {}

        # In-memory mirror — hydrate.
        self._mem_nodes: dict[str, dict] = {}
        self._mem_edges: list[dict] = []
        self._hydrate()

    # -- helpers ------------------------------------------------------------

    def _open_or_create(self, name: str, schema: pa.Schema):
        try:
            return self._db.open_table(name)
        except Exception:
            return self._db.create_table(name, schema=schema)

    def _ensure_scalar_indices(self) -> None:
        """Create scalar indices on lookup-hot columns (idempotent).

        Soft-degrade: if the LanceDB version doesn't expose
        ``create_scalar_index`` or rejects the args (empty table, unsupported
        index_type, etc.), record the reason and continue. Never raise — the
        store must remain usable even without indices.
        """
        plan: list[tuple[str, str, str]] = [
            # (table_attr, column, index_type)
            ("_nodes", "id", "BTREE"),
            ("_nodes", "type", "BITMAP"),
            ("_nodes", "layer", "BITMAP"),
            ("_edges", "source", "BTREE"),
            ("_edges", "target", "BTREE"),
        ]
        for table_attr, column, index_type in plan:
            tbl = getattr(self, table_attr, None)
            if tbl is None:
                continue
            key = f"{table_attr}.{column}"
            if not hasattr(tbl, "create_scalar_index"):
                self._scalar_index_status[key] = "unsupported (no create_scalar_index)"
                continue
            try:
                tbl.create_scalar_index(
                    column, index_type=index_type, replace=True
                )
                self._scalar_index_status[key] = f"ok ({index_type})"
            except Exception as exc:
                # Common case: empty table → "no rows" / version mismatch.
                self._scalar_index_status[key] = f"skipped: {exc}"

    def _persist_meta(self) -> None:
        try:
            (self._lance_dir / "lance_meta.json").write_text(
                json.dumps({"last_refresh": self._last_refresh}, indent=2)
            )
        except Exception:
            pass

    def _arrow_rows(self, table) -> list[dict]:
        try:
            t = table.to_arrow()
        except Exception:
            return []
        cols = {name: t.column(name).to_pylist() for name in t.column_names}
        rows: list[dict] = []
        n = t.num_rows
        for i in range(n):
            rows.append({name: cols[name][i] for name in cols})
        return rows

    def _hydrate(self) -> None:
        try:
            for row in self._arrow_rows(self._nodes):
                meta = _parse_meta(row.get("metadata") or "")
                nid = row.get("id") or meta.get("id")
                if not nid:
                    continue
                meta["id"] = nid
                meta["type"] = row.get("type") or meta.get("type")
                meta["layer"] = row.get("layer") or meta.get("layer")
                meta["label"] = row.get("label") or meta.get("label")
                self._mem_nodes[nid] = meta
        except Exception as exc:
            print(f"WARNING: lance hydrate nodes failed: {exc}", file=sys.stderr)
        try:
            for row in self._arrow_rows(self._edges):
                meta = _parse_meta(row.get("metadata") or "")
                edge = {
                    "source": row.get("source"),
                    "target": row.get("target"),
                    "relation": row.get("relation"),
                    "layer": row.get("layer"),
                    **meta,
                }
                self._mem_edges.append(edge)
        except Exception as exc:
            print(f"WARNING: lance hydrate edges failed: {exc}", file=sys.stderr)

    def _embed_texts(self, texts: list[str]) -> list[list[float]]:
        # Most embedding-fn implementations expose `compute_source_embeddings`.
        if hasattr(self._embed, "compute_source_embeddings"):
            return self._embed.compute_source_embeddings(texts)
        return self._embed.generate_embeddings(texts)

    # -- mutation -----------------------------------------------------------

    def upsert_nodes(self, nodes: list[dict]) -> None:
        if not nodes:
            return
        rows: list[dict] = []
        seen: set[str] = set()
        for n in nodes:
            nid = n.get("id")
            if not nid or nid in seen:
                continue
            seen.add(nid)
            n.setdefault("layer", "cold")
            self._mem_nodes[nid] = n
            rows.append(
                {
                    "id": nid,
                    "type": str(n.get("type") or ""),
                    "layer": str(n.get("layer") or "cold"),
                    "label": str(n.get("label") or nid),
                    "metadata": _stringify_meta(n),
                    "embedding_text": _embedding_text(n),
                }
            )
        if not rows:
            return
        try:
            vecs = self._embed_texts([r["embedding_text"] for r in rows])
            for r, v in zip(rows, vecs):
                r["vector"] = list(v)
            # Lance has no native upsert keyed by string — emulate.
            ids = [r["id"] for r in rows]
            try:
                self._nodes.delete(f"id IN ({','.join(repr(i) for i in ids)})")
            except Exception:
                pass
            self._nodes.add(rows)
        except Exception as exc:
            print(f"WARNING: LanceGraphStore.upsert_nodes error: {exc}", file=sys.stderr)

    def upsert_edges(self, edges: list[dict]) -> None:
        if not edges:
            return
        rows: list[dict] = []
        seen_ids: set[str] = set()
        for e in edges:
            e.setdefault("layer", "cold")
            self._mem_edges.append(e)
            eid = (
                f"{e.get('source','')}->{e.get('target','')}|"
                f"{e.get('relation','')}|{e.get('layer','cold')}"
            )
            if eid in seen_ids:
                continue
            seen_ids.add(eid)
            rows.append(
                {
                    "id": eid,
                    "source": str(e.get("source") or ""),
                    "target": str(e.get("target") or ""),
                    "relation": str(e.get("relation") or ""),
                    "layer": str(e.get("layer") or "cold"),
                    "metadata": _stringify_meta(e),
                }
            )
        if not rows:
            return
        try:
            ids = [r["id"] for r in rows]
            try:
                self._edges.delete(f"id IN ({','.join(repr(i) for i in ids)})")
            except Exception:
                pass
            self._edges.add(rows)
        except Exception as exc:
            print(f"WARNING: LanceGraphStore.upsert_edges error: {exc}", file=sys.stderr)

    def replace_layer(self, layer: str, nodes: list[dict], edges: list[dict]) -> None:
        # Drop in-memory.
        self._mem_nodes = {
            nid: n for nid, n in self._mem_nodes.items() if n.get("layer") != layer
        }
        self._mem_edges = [e for e in self._mem_edges if e.get("layer") != layer]
        # Drop persisted.
        try:
            self._nodes.delete(f"layer = '{layer}'")
        except Exception:
            pass
        try:
            self._edges.delete(f"layer = '{layer}'")
        except Exception:
            pass
        for n in nodes:
            n["layer"] = layer
        for e in edges:
            e["layer"] = layer
        self.upsert_nodes(nodes)
        self.upsert_edges(edges)
        self._last_refresh[layer] = _dt.datetime.now(
            _dt.timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._persist_meta()
        # Refresh scalar indices after a layer replacement so newly-added
        # rows are covered. Cheap when the index already exists (idempotent).
        self._ensure_scalar_indices()

    # -- queries ------------------------------------------------------------

    def all_nodes(self, layer: str | None = None) -> list[dict]:
        if layer is None:
            return list(self._mem_nodes.values())
        return [n for n in self._mem_nodes.values() if n.get("layer") == layer]

    def all_edges(self, layer: str | None = None) -> list[dict]:
        if layer is None:
            return list(self._mem_edges)
        return [e for e in self._mem_edges if e.get("layer") == layer]

    def _search_to_rows(self, search) -> list[dict]:
        try:
            t = search.to_arrow()
        except Exception as exc:
            print(f"WARNING: lance search error: {exc}", file=sys.stderr)
            return []
        cols = {name: t.column(name).to_pylist() for name in t.column_names}
        rows: list[dict] = []
        for i in range(t.num_rows):
            rows.append({name: cols[name][i] for name in cols})
        return rows

    def semantic_search(
        self, query: str, layer: str | None = None, limit: int = 10
    ) -> list[dict]:
        try:
            qvec = self._embed_texts([query])[0]
            search = self._nodes.search(list(qvec))
            if layer:
                search = search.where(f"layer = '{layer}'")
            search = search.limit(int(limit))
        except Exception as exc:
            print(f"WARNING: lance semantic_search error: {exc}", file=sys.stderr)
            return []
        out: list[dict] = []
        for row in self._search_to_rows(search):
            meta = _parse_meta(row.get("metadata") or "")
            meta["id"] = row.get("id")
            dist = row.get("_distance")
            score = (1.0 - float(dist)) if dist is not None else None
            out.append(
                {
                    "id": row.get("id"),
                    "score": score,
                    "distance": float(dist) if dist is not None else None,
                    "node": meta,
                }
            )
        return out

    def find_similar(self, node_id: str, limit: int = 10) -> list[dict]:
        try:
            rows = self._search_to_rows(
                self._nodes.search().where(f"id = '{node_id}'").limit(1)
            )
        except Exception as exc:
            print(f"WARNING: lance find_similar lookup error: {exc}", file=sys.stderr)
            return []
        if not rows:
            return []
        text = rows[0].get("embedding_text") or ""
        if not text:
            return []
        try:
            qvec = self._embed_texts([text])[0]
            search = self._nodes.search(list(qvec)).limit(int(limit) + 1)
        except Exception as exc:
            print(f"WARNING: lance find_similar query error: {exc}", file=sys.stderr)
            return []
        out: list[dict] = []
        for row in self._search_to_rows(search):
            if row.get("id") == node_id:
                continue
            meta = _parse_meta(row.get("metadata") or "")
            meta["id"] = row.get("id")
            dist = row.get("_distance")
            score = (1.0 - float(dist)) if dist is not None else None
            out.append(
                {
                    "id": row.get("id"),
                    "score": score,
                    "distance": float(dist) if dist is not None else None,
                    "node": meta,
                }
            )
            if len(out) >= int(limit):
                break
        return out

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
            "scalar_indices": dict(self._scalar_index_status),
        }
