"""Regeneration tools — drive the LAYERS pipeline."""

from __future__ import annotations

import sys

from ..layers import leaf_layer_names
from ..orchestrator import (
    build_mcp_endpoint,
    list_layers_summary,
    regenerate_graph as _regenerate_graph_op,
    regenerate_layer_op,
)
from ..server import SERVER_CONFIG, mcp
from ..store._mcp_discovery import discover_live_mcp


# Layers that need a live-cluster MCP endpoint to produce real data.
_LIVE_LAYERS: set[str] = {"k8s", "argo", "logs", "metrics", "traces"}


def _resolve_repo(repo_root: str | None) -> str:
    return repo_root or SERVER_CONFIG.get("repo_root", ".")


def _resolve_persist(persist_dir: str | None) -> str:
    return persist_dir or SERVER_CONFIG.get("persist_dir", ".kuberly")


def _resolve_endpoint(
    mcp_url: str | None,
    mcp_stdio: str | None,
    repo_root: str,
) -> tuple[dict | None, str | None]:
    """Pick endpoint: explicit args win; else auto-discover from .mcp.json.

    Returns (endpoint, label) where label is a human-readable string for the
    skipped_layers note ("auto-discovered http://..." / "no live MCP found").
    """
    if mcp_url or mcp_stdio:
        endpoint = build_mcp_endpoint(mcp_url, mcp_stdio)
        return endpoint, ("explicit url" if mcp_url else "explicit stdio")
    discovered = discover_live_mcp(repo_root)
    if discovered:
        url = discovered.get("url") or "stdio"
        print(f"Auto-discovered MCP at {url}", file=sys.stderr)
        return discovered, f"auto-discovered {url}"
    return None, None


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

    When neither `mcp_url` nor `mcp_stdio` is provided, auto-discovers the
    live-cluster MCP endpoint from `<repo_root>/.mcp.json` (looking for an
    `ai-agent-tool` HTTP entry, resolving `${VAR}` headers from os.environ).
    If discovery returns nothing, live layers (k8s/argo/logs/metrics/traces)
    are skipped silently with a note in the result's `skipped_layers` field;
    cold/code/components/applications/rendered/state still run.
    """
    repo = _resolve_repo(repo_root)
    persist = _resolve_persist(persist_dir)
    endpoint, endpoint_label = _resolve_endpoint(mcp_url, mcp_stdio, repo)

    # Decide which layers to actually run vs skip when no MCP is available.
    requested = list(layers) if layers else None
    skipped: list[dict] = []
    if endpoint is None:
        target = requested if requested is not None else ["all"]
        # Expand "all" to the leaf list so we can filter.
        expanded: list[str] = []
        for name in target:
            if name == "all":
                expanded.extend(leaf_layer_names())
            else:
                expanded.append(name)
        kept: list[str] = []
        for name in expanded:
            if name in _LIVE_LAYERS:
                skipped.append(
                    {"layer": name, "reason": "no live MCP endpoint discovered"}
                )
            else:
                kept.append(name)
        run_layers: list[str] | None = kept if requested is not None else (
            kept if kept else None
        )
        # If user passed nothing explicit and we filtered out live layers,
        # ensure we don't end up passing [] (which resolve_layer_names treats
        # as "no layers"). Default to leaf cold-only set.
        if requested is None:
            run_layers = kept or None
    else:
        run_layers = requested

    result = _regenerate_graph_op(
        repo_root=repo,
        persist_dir=persist,
        layers=run_layers,
        verbose=verbose,
        mcp_endpoint=endpoint,
        logs_window=logs_window,
        logs_limit=logs_limit,
        metrics_top_n=metrics_top_n,
        traces_window=traces_window,
        traces_limit=traces_limit,
    )
    if skipped:
        result["skipped_layers"] = skipped
    if endpoint_label:
        result["mcp_endpoint"] = endpoint.get("url") if endpoint else None
        result["mcp_endpoint_source"] = endpoint_label
    else:
        result["mcp_endpoint"] = None
        result["mcp_endpoint_source"] = "none"
    return result


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
    aws_account_id: str | None = None,
    cost_lookback_months: int | None = None,
    compliance_required_tags: list[str] | None = None,
) -> dict:
    """Re-run one layer's scanner. Convenience wrapper around
    `regenerate_graph(layers=[layer])`.

    Phase 7D knobs (all optional):
      * ``aws_account_id`` / ``cost_lookback_months`` — CostLayer.
      * ``compliance_required_tags`` — ComplianceLayer R003 input.
    """
    endpoint = build_mcp_endpoint(mcp_url, mcp_stdio)
    extra: dict = {}
    if aws_account_id:
        extra["aws_account_id"] = aws_account_id
    if cost_lookback_months:
        extra["cost_lookback_months"] = int(cost_lookback_months)
    if compliance_required_tags:
        extra["compliance_required_tags"] = list(compliance_required_tags)
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
        extra_ctx=extra or None,
    )


@mcp.tool()
def list_layers(persist_dir: str | None = None) -> list[dict]:
    """Per-layer summary: name, type, refresh_trigger, last_refresh, node/edge counts."""
    return list_layers_summary(_resolve_persist(persist_dir))


@mcp.tool()
def regenerate_all(
    repo_root: str | None = None,
    persist_dir: str | None = None,
) -> dict:
    """Refresh every layer of the graph in one shot. No JSON args required.

    Auto-discovers the live-cluster MCP endpoint from `<repo_root>/.mcp.json`
    (preferring an `ai-agent-tool` HTTP entry, resolving `${VAR}` headers
    from os.environ). Use this after `aws sso login` + `kubectl` connection
    + ai-agent-tool MCP wiring, when you want a one-shot full refresh.

    If no live MCP is discoverable, cold/code/components/applications/
    rendered/state still run; live layers (k8s/argo/logs/metrics/traces) are
    skipped silently and reported under `skipped_layers`.

    Returns: {layers_run, node_count, edge_count, mcp_endpoint,
              mcp_endpoint_source, duration_ms, skipped_layers, ...}.
    """
    return regenerate_graph(
        layers=None,
        verbose=False,
        mcp_url=None,
        mcp_stdio=None,
        repo_root=repo_root,
        persist_dir=persist_dir,
    )
