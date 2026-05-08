"""Layer registry — order matters: cold sub-layers run first so live layers
can read freshly-stored cold ids when building cross-edges.
"""

from __future__ import annotations

from graphlib import TopologicalSorter

from .alert import AlertLayer
from .applications import ApplicationsLayer
from .argo import ArgoLayer
from .aws import AwsLayer
from .base import Layer
from .cold import ColdLayer
from .code import CodeLayer
from .components import ComponentsLayer
from .compliance import ComplianceLayer
from .cost import CostLayer
from .dependency import DependencyLayer
from .dns import DnsLayer
from .iam import IAMLayer
from .image_build import ImageBuildLayer
from .k8s import K8sLayer
from .kubectl import KubectlLayer
from .logs import LogsLayer
from .meta import MetaLayer
from .metrics import MetricsLayer
from .network import NetworkLayer
from .rendered import RenderedLayer
from .secrets import SecretsLayer
from .state import StateLayer
from .storage import StorageLayer
from .traces import TracesLayer


LAYERS: list[Layer] = [
    ColdLayer(),
    CodeLayer(),
    ComponentsLayer(),
    ApplicationsLayer(),
    RenderedLayer(),
    StateLayer(),
    K8sLayer(),
    # Phase 8G: KubectlLayer shells out to local `kubectl` with whatever
    # creds the operator already has (typically full IAM/admin), so kinds
    # the bearer-token MCP can't list (Secret, Role, RoleBinding, Lease,
    # custom resources) enter the graph. Same `k8s_resource:` id namespace
    # as K8sLayer — kubectl OVERWRITES the bearer-token version when both
    # populate. Soft-degrades when kubectl absent / no current-context.
    KubectlLayer(),
    ArgoLayer(),
    LogsLayer(),
    MetricsLayer(),
    TracesLayer(),
    # Phase 7B structural extractors — pure reads of state + k8s, no live calls.
    NetworkLayer(),
    IAMLayer(),
    ImageBuildLayer(),
    StorageLayer(),
    # Phase 7D structural extractors. Order matters only relative to
    # DependencyLayer (last) — the topo sort handles inter-7D ordering.
    DnsLayer(),
    SecretsLayer(),
    CostLayer(),
    AlertLayer(),
    ComplianceLayer(),
    # Phase 8F: AwsLayer scrapes AWS services live via boto3, emitting nodes
    # under `aws:*`. Empty-store tolerant; soft-degrades if boto3 / AWS creds
    # are missing.
    AwsLayer(),
    # DependencyLayer derives cross-layer edges from whatever is already in
    # the GraphStore — must run last among data layers.
    DependencyLayer(),
    # MetaLayer reads the populated store + the layer registry to emit
    # `graph_layer:<name>` nodes + feeds_into edges (graph-of-graphs). It
    # MUST run AFTER DependencyLayer so its node-counts reflect the final
    # store state.
    MetaLayer(),
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
    # Phase 7B structural extractors. They read tfstate + k8s data already in
    # the store, and must produce their nodes/edges before DependencyLayer.
    "network": {"state"},
    "iam": {"state", "k8s"},
    "image_build": {"k8s"},
    "storage": {"state", "k8s"},
    # Phase 7D structural extractors.
    "dns": {"state", "k8s"},
    "secrets": {"state", "k8s"},
    "cost": {"state"},
    "alert": {"k8s", "metrics"},
    "compliance": {"state", "k8s", "iam", "network"},
    # AwsLayer runs independently (no upstream layer feeds it — it scrapes
    # AWS directly) but must complete before DependencyLayer so its
    # `aws:*` nodes are visible for the cross-namespace wiring.
    "aws": set(),
    # KubectlLayer is independent of any other layer (shells directly to
    # `kubectl`), but should run AFTER K8sLayer so its richer view
    # OVERWRITES the bearer-token one when both are part of the run.
    "kubectl": {"k8s"},
    # DependencyLayer reads the populated store — make every other leaf
    # layer that's part of this run finish first.
    "dependency": {
        "code",
        "components",
        "applications",
        "rendered",
        "state",
        "k8s",
        "kubectl",
        "argo",
        "logs",
        "metrics",
        "traces",
        "network",
        "iam",
        "image_build",
        "storage",
        "dns",
        "secrets",
        "cost",
        "alert",
        "compliance",
        "aws",
    },
    # MetaLayer summarises the entire store — depends on every other layer
    # finishing first so its node_count / edge_count fields reflect reality.
    "meta": {
        "cold",
        "code",
        "components",
        "applications",
        "rendered",
        "state",
        "k8s",
        "kubectl",
        "argo",
        "logs",
        "metrics",
        "traces",
        "network",
        "iam",
        "image_build",
        "storage",
        "dns",
        "secrets",
        "cost",
        "alert",
        "compliance",
        "aws",
        "dependency",
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
