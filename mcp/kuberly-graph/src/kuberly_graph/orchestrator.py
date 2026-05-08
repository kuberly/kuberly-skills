"""Orchestration helpers shared by the tools.

Behaviour mirrors the legacy `regenerate_graph` / `regenerate_layer`
module-level functions in `scripts/kuberly_graph.py`.
"""

from __future__ import annotations

import datetime as _dt
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from .layers import (
    LAYERS,
    layer_by_name,
    resolve_layer_names,
    topo_sort_layers,
)
from .layers._util import KuberlyGraph
from .store import open_store


def build_mcp_endpoint(
    mcp_url: str | None = None, mcp_stdio: str | None = None
) -> dict | None:
    if mcp_url and mcp_stdio:
        raise ValueError("pass only one of mcp_url / mcp_stdio")
    if mcp_url:
        return {"url": mcp_url}
    if mcp_stdio:
        return {"stdio_cmd": mcp_stdio}
    return None


def regenerate_graph(
    repo_root: str = ".",
    persist_dir: str = ".kuberly",
    layers: list[str] | None = None,
    write_json: bool = True,
    verbose: bool = False,
    mcp_endpoint: dict | None = None,
    logs_window: str | None = None,
    logs_limit: int | None = None,
    metrics_top_n: int | None = None,
    traces_window: str | None = None,
    traces_limit: int | None = None,
    extra_ctx: dict | None = None,
) -> dict:
    start = _dt.datetime.now()
    repo = Path(repo_root).resolve()
    persist = Path(persist_dir).resolve()
    persist.mkdir(parents=True, exist_ok=True)

    store = open_store(persist)
    target_names = resolve_layer_names(layers)
    target_names = topo_sort_layers(target_names)

    comp_dir = repo / "components"
    envs_seed: list[str] = []
    if comp_dir.exists():
        envs_seed = sorted(
            p.name
            for p in comp_dir.iterdir()
            if p.is_dir() and not p.name.startswith(".")
        )

    ctx: dict[str, Any] = {
        "repo_root": str(repo),
        "persist_dir": str(persist),
        "verbose": verbose,
        "mcp_endpoint": mcp_endpoint,
        "envs": envs_seed,
        "logs_window": logs_window or "1h",
        "logs_limit": int(logs_limit) if logs_limit else 5000,
        "metrics_top_n": int(metrics_top_n) if metrics_top_n else 200,
        "traces_window": traces_window or "1h",
        "traces_limit": int(traces_limit) if traces_limit else 500,
        # Expose the live GraphStore so DependencyLayer (and any future
        # cross-layer scanner) can read the freshly-populated nodes/edges
        # without re-opening the store.
        "graph_store": store,
    }
    if extra_ctx:
        ctx.update(extra_ctx)
    per_layer: dict[str, dict] = {}
    cold_graph: KuberlyGraph | None = None

    for name in target_names:
        layer = layer_by_name(name)
        if layer is None:
            continue
        ctx["_existing_app_ids"] = {
            n["id"] for n in store.all_nodes() if n.get("type") == "application"
        }
        ctx["_existing_module_ids"] = {
            n["id"] for n in store.all_nodes() if n.get("type") == "module"
        }
        ctx["_existing_rendered_ids"] = {
            n["id"]
            for n in store.all_nodes()
            if n.get("type") == "rendered_resource"
        }
        nodes, edges = layer.scan(ctx)
        store.replace_layer(name, nodes, edges)
        per_layer[name] = {"nodes": len(nodes), "edges": len(edges)}
        if name == "cold":
            cold_graph = ctx.get("_cold_graph")
        if verbose:
            print(f"  layer={name} nodes={len(nodes)} edges={len(edges)}")

    if write_json:
        if cold_graph is not None:
            out_path = persist / "graph.json"
            out_path.write_text(
                json.dumps(
                    {
                        "nodes": list(cold_graph.nodes.values()),
                        "edges": cold_graph.edges,
                        "stats": cold_graph.compute_stats(),
                        "drift": cold_graph.cross_env_drift(),
                    },
                    indent=2,
                    default=str,
                )
            )
        elif {"code", "components", "applications"}.issubset(set(target_names)):
            synth = KuberlyGraph(str(repo))
            synth.build()
            out_path = persist / "graph.json"
            out_path.write_text(
                json.dumps(
                    {
                        "nodes": list(synth.nodes.values()),
                        "edges": synth.edges,
                        "stats": synth.compute_stats(),
                        "drift": synth.cross_env_drift(),
                    },
                    indent=2,
                    default=str,
                )
            )

    duration_ms = int((_dt.datetime.now() - start).total_seconds() * 1000)
    return {
        "layers_run": target_names,
        "node_count": len(store.all_nodes()),
        "edge_count": len(store.all_edges()),
        "per_layer": per_layer,
        "duration_ms": duration_ms,
        "mode": store.mode,
        "persist_dir": str(persist),
    }


def regenerate_layer_op(
    layer: str,
    repo_root: str = ".",
    persist_dir: str = ".kuberly",
    mcp_endpoint: dict | None = None,
    logs_window: str | None = None,
    logs_limit: int | None = None,
    metrics_top_n: int | None = None,
    traces_window: str | None = None,
    traces_limit: int | None = None,
    extra_ctx: dict | None = None,
) -> dict:
    return regenerate_graph(
        repo_root=repo_root,
        persist_dir=persist_dir,
        layers=[layer],
        mcp_endpoint=mcp_endpoint,
        logs_window=logs_window,
        logs_limit=logs_limit,
        metrics_top_n=metrics_top_n,
        traces_window=traces_window,
        traces_limit=traces_limit,
        extra_ctx=extra_ctx,
    )


def list_layers_summary(persist_dir: str = ".kuberly") -> list[dict]:
    store = open_store(Path(persist_dir).resolve())
    s = store.stats()
    per_layer = s.get("per_layer", {})
    type_map = {
        "cold": "meta",
        "code": "cold",
        "components": "cold",
        "applications": "cold",
        "rendered": "cold",
        "state": "cold",
        "k8s": "live",
        "argo": "live",
        "logs": "stub",
        "metrics": "stub",
        "traces": "stub",
        # Phase 7B — structural extractors over already-stored data.
        "network": "derived",
        "iam": "derived",
        "image_build": "derived",
        "storage": "derived",
        # Phase 7D — DNS/Secrets/Cost/Alert/Compliance.
        "dns": "derived",
        "secrets": "derived",
        "cost": "live",
        "alert": "derived",
        "compliance": "derived",
        "dependency": "meta",
        "meta": "meta",
    }
    out: list[dict] = []
    for layer in LAYERS:
        info = per_layer.get(layer.name, {})
        out.append(
            {
                "name": layer.name,
                "type": type_map.get(layer.name, "unknown"),
                "refresh_trigger": layer.refresh_trigger,
                "last_refresh": info.get("last_refresh"),
                "node_count": info.get("nodes", 0),
                "edge_count": info.get("edges", 0),
            }
        )
    return out
