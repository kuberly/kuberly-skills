"""Layer registry — order matters: cold sub-layers run first so live layers
can read freshly-stored cold ids when building cross-edges.
"""

from __future__ import annotations

from graphlib import TopologicalSorter

from .applications import ApplicationsLayer
from .argo import ArgoLayer
from .base import Layer
from .cold import ColdLayer
from .code import CodeLayer
from .components import ComponentsLayer
from .dependency import DependencyLayer
from .k8s import K8sLayer
from .logs import LogsLayer
from .metrics import MetricsLayer
from .rendered import RenderedLayer
from .state import StateLayer
from .traces import TracesLayer


LAYERS: list[Layer] = [
    ColdLayer(),
    CodeLayer(),
    ComponentsLayer(),
    ApplicationsLayer(),
    RenderedLayer(),
    StateLayer(),
    K8sLayer(),
    ArgoLayer(),
    LogsLayer(),
    MetricsLayer(),
    TracesLayer(),
    # DependencyLayer derives cross-layer edges from whatever is already in
    # the GraphStore — must run last.
    DependencyLayer(),
]

META_LAYERS: set[str] = {"cold"}


def layer_by_name(name: str) -> Layer | None:
    for layer in LAYERS:
        if layer.name == name:
            return layer
    return None


def leaf_layer_names() -> list[str]:
    return [layer.name for layer in LAYERS if layer.name not in META_LAYERS]


def resolve_layer_names(layers: list[str] | None) -> list[str]:
    """Match the legacy `_resolve_layer_names` semantics."""
    if not layers or list(layers) == ["all"]:
        return leaf_layer_names()
    valid = {layer.name for layer in LAYERS} | {"all"}
    bad = [name for name in layers if name not in valid]
    if bad:
        raise ValueError(f"unknown layer(s): {bad} (valid: {sorted(valid)})")
    out: list[str] = []
    seen: set[str] = set()
    for name in layers:
        if name == "all":
            for leaf in leaf_layer_names():
                if leaf not in seen:
                    seen.add(leaf)
                    out.append(leaf)
        elif name not in seen:
            seen.add(name)
            out.append(name)
    return out


# Layer ordering DAG — cold sub-layers must run before any layer that
# depends on existing cold ids. Express it via stdlib graphlib so future
# additions plug in cleanly.
_LAYER_PRECEDES: dict[str, set[str]] = {
    "rendered": {"applications", "components"},
    "state": {"code"},
    "k8s": {"rendered"},
    "argo": {"applications"},
    "logs": {"applications"},
    "metrics": {"applications", "code"},
    "traces": {"applications", "code"},
    # DependencyLayer reads the populated store — make every other leaf
    # layer that's part of this run finish first.
    "dependency": {
        "code",
        "components",
        "applications",
        "rendered",
        "state",
        "k8s",
        "argo",
        "logs",
        "metrics",
        "traces",
    },
}


def topo_sort_layers(names: list[str]) -> list[str]:
    """Return `names` ordered so dependencies precede dependents."""
    relevant = set(names)
    ts: TopologicalSorter = TopologicalSorter()
    for name in names:
        deps = _LAYER_PRECEDES.get(name, set()) & relevant
        ts.add(name, *deps)
    return list(ts.static_order())


__all__ = [
    "Layer",
    "LAYERS",
    "META_LAYERS",
    "layer_by_name",
    "leaf_layer_names",
    "resolve_layer_names",
    "topo_sort_layers",
]
