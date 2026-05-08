"""ColdLayer — meta-alias that runs the full cold IaC scan.

Produces the same 92/243 nodes/edges that `KuberlyGraph.build()` historically
emitted. Stashes the freshly-built graph in `ctx["_cold_graph"]` so the
orchestrator can reuse it for stats / drift / JSON export without re-scanning.
"""

from __future__ import annotations

from .base import Layer
from ._util import KuberlyGraph


class ColdLayer(Layer):
    name = "cold"
    refresh_trigger = "manual"

    def scan(self, ctx: dict) -> tuple[list[dict], list[dict]]:
        repo_root = ctx.get("repo_root", ".")
        g = KuberlyGraph(str(repo_root))
        g.build()
        nodes = [{**n, "layer": "cold"} for n in g.nodes.values()]
        edges = [{**e, "layer": "cold"} for e in g.edges]
        ctx["_cold_graph"] = g
        return nodes, edges
