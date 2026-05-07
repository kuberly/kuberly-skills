"""Regeneration tools — drive the LAYERS pipeline."""

from __future__ import annotations

from ..orchestrator import (
    build_mcp_endpoint,
    list_layers_summary,
    regenerate_graph as _regenerate_graph_op,
    regenerate_layer_op,
)
from ..server import SERVER_CONFIG, mcp


def _resolve_repo(repo_root: str | None) -> str:
    return repo_root or SERVER_CONFIG.get("repo_root", ".")


def _resolve_persist(persist_dir: str | None) -> str:
    return persist_dir or SERVER_CONFIG.get("persist_dir", ".kuberly")


@mcp.tool()
def regenerate_graph(
    layers: list[str] | None = None,
    verbose: bool = False,
    mcp_url: str | None = None,
    mcp_stdio: str | None = None,
    logs_window: str | None = None,
    logs_limit: int | None = None,
    metrics_top_n: int | None = None,
    traces_window: str | None = None,
    traces_limit: int | None = None,
    repo_root: str | None = None,
    persist_dir: str | None = None,
) -> dict:
    """Re-run scanners across one or more layers (cold | code | components |
    applications | rendered | state | k8s | argo | logs | metrics | traces |
    all) and refresh the persistent GraphStore. Also writes
    .kuberly/graph.json for legacy consumers when the cold slice is included.
    """
    endpoint = build_mcp_endpoint(mcp_url, mcp_stdio)
    return _regenerate_graph_op(
        repo_root=_resolve_repo(repo_root),
        persist_dir=_resolve_persist(persist_dir),
        layers=layers,
        verbose=verbose,
        mcp_endpoint=endpoint,
        logs_window=logs_window,
        logs_limit=logs_limit,
        metrics_top_n=metrics_top_n,
        traces_window=traces_window,
        traces_limit=traces_limit,
    )


@mcp.tool()
def regenerate_layer(
    layer: str,
    mcp_url: str | None = None,
    mcp_stdio: str | None = None,
    logs_window: str | None = None,
    logs_limit: int | None = None,
    metrics_top_n: int | None = None,
    traces_window: str | None = None,
    traces_limit: int | None = None,
    repo_root: str | None = None,
    persist_dir: str | None = None,
) -> dict:
    """Re-run one layer's scanner. Convenience wrapper around
    `regenerate_graph(layers=[layer])`.
    """
    endpoint = build_mcp_endpoint(mcp_url, mcp_stdio)
    return regenerate_layer_op(
        layer=layer,
        repo_root=_resolve_repo(repo_root),
        persist_dir=_resolve_persist(persist_dir),
        mcp_endpoint=endpoint,
        logs_window=logs_window,
        logs_limit=logs_limit,
        metrics_top_n=metrics_top_n,
        traces_window=traces_window,
        traces_limit=traces_limit,
    )


@mcp.tool()
def list_layers(persist_dir: str | None = None) -> list[dict]:
    """Per-layer summary: name, type, refresh_trigger, last_refresh, node/edge counts."""
    return list_layers_summary(_resolve_persist(persist_dir))
