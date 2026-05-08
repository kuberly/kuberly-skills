"""Phase 8F AWS tools — pure GraphStore queries over `aws:*` nodes emitted
by AwsLayer. No live AWS calls.
"""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from pathlib import Path

from ..server import SERVER_CONFIG, mcp
from ..store import open_store


def _resolve_persist(persist_dir: str | None) -> str:
    return persist_dir or SERVER_CONFIG.get("persist_dir", ".kuberly")


def _open(persist_dir: str | None):
    return open_store(Path(_resolve_persist(persist_dir)).resolve())


@mcp.tool()
def aws_resource_count_by_service(
    persist_dir: str | None = None,
) -> dict:
    """Count `aws:*` nodes grouped by their `type` (one entry per AWS service).

    Returns ``{type_name: count, ...}`` for every node with ``layer="aws"``,
    plus a ``_total`` key. Pure GraphStore query — call after AwsLayer has
    populated the store via ``regenerate_layer aws``.
    """
    store = _open(persist_dir)
    counter: Counter = Counter()
    total = 0
    for n in store.all_nodes():
        if not isinstance(n, dict):
            continue
        if str(n.get("layer") or "") != "aws":
            continue
        ntype = str(n.get("type") or "unknown")
        counter[ntype] += 1
        total += 1
    out: dict = {k: counter[k] for k in sorted(counter)}
    out["_total"] = total
    return out


@mcp.tool()
def find_aws_resources_in_vpc(
    vpc_id: str,
    persist_dir: str | None = None,
) -> list[dict]:
    """Every ``aws:*`` node logically inside ``vpc_id``.

    Two-prong match:
      1. Direct attribute: ``node.vpc_id == vpc_id``.
      2. BFS over ``in_vpc`` / ``in_subnet`` / ``uses_subnet`` / ``uses_sg`` /
         ``lives_in`` edges from the VPC node — picks up subnets, SGs, NATs,
         IGWs, EC2s, LBs, EKS clusters, etc., even when they only point at
         a subnet rather than the VPC directly.

    Returns ``[{id, type, label, vpc_id, ...lite fields}, ...]``.
    """
    if not vpc_id:
        return []
    store = _open(persist_dir)
    nodes = store.all_nodes()
    edges = store.all_edges()
    nodes_by_id = {n.get("id"): n for n in nodes if isinstance(n, dict) and n.get("id")}
    aws_nodes = {
        nid: n for nid, n in nodes_by_id.items() if str(n.get("layer") or "") == "aws"
    }

    # Adjacency over VPC-relevant relations (undirected so we can walk in any
    # direction).
    rel_set = {"in_vpc", "in_subnet", "uses_subnet", "uses_sg", "lives_in", "attached_to", "member_of"}
    adj: dict[str, list[str]] = defaultdict(list)
    for e in edges:
        if not isinstance(e, dict):
            continue
        rel = str(e.get("relation") or "")
        if rel not in rel_set:
            continue
        s = str(e.get("source") or "")
        t = str(e.get("target") or "")
        if not s or not t:
            continue
        adj[s].append(t)
        adj[t].append(s)

    vpc_node_id = f"aws:vpc:{vpc_id}"
    visited: set[str] = set()
    if vpc_node_id in aws_nodes:
        # BFS from VPC node
        q: deque[str] = deque([vpc_node_id])
        while q:
            cur = q.popleft()
            if cur in visited:
                continue
            visited.add(cur)
            for nb in adj.get(cur, []):
                if nb in aws_nodes and nb not in visited:
                    q.append(nb)

    # Also pull anything whose attribute vpc_id matches.
    for nid, n in aws_nodes.items():
        if str(n.get("vpc_id") or "") == vpc_id:
            visited.add(nid)

    out: list[dict] = []
    for nid in sorted(visited):
        n = aws_nodes.get(nid)
        if not n:
            continue
        out.append(
            {
                "id": nid,
                "type": n.get("type", ""),
                "label": n.get("label", nid),
                "vpc_id": n.get("vpc_id", ""),
                "subnet_id": n.get("subnet_id", ""),
                "az": n.get("az", ""),
                "region": n.get("region", ""),
            }
        )
    return out
