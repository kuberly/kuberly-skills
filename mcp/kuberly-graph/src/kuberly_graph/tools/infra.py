"""Phase 7B infra tools — pure GraphStore queries over Network / IAM /
ImageBuild / Storage layers. No live MCP calls."""

from __future__ import annotations

from collections import defaultdict, deque
from pathlib import Path

from ..server import SERVER_CONFIG, mcp
from ..store import open_store


def _resolve_persist(persist_dir: str | None) -> str:
    return persist_dir or SERVER_CONFIG.get("persist_dir", ".kuberly")


def _open(persist_dir: str | None):
    return open_store(Path(_resolve_persist(persist_dir)).resolve())


# ---- Network ----------------------------------------------------------------


@mcp.tool()
def find_open_security_groups(
    env: str | None = None,
    persist_dir: str | None = None,
) -> list[dict]:
    """Security groups with at least one ingress rule allowing 0.0.0.0/0 or ::/0.

    Pure GraphStore query — relies on NetworkLayer having emitted
    ``allows_ingress_from_cidr`` edges from `security_group:*` to `cidr:*` nodes.
    """
    store = _open(persist_dir)
    nodes = store.all_nodes()
    edges = store.all_edges()
    nodes_by_id = {n.get("id"): n for n in nodes if n.get("id")}
    open_blocks = {"0.0.0.0/0", "::/0"}
    open_sg_ids: set[str] = set()
    triggering_rules: dict[str, list[dict]] = defaultdict(list)
    for e in edges:
        if e.get("relation") != "allows_ingress_from_cidr":
            continue
        target = str(e.get("target") or "")
        if not target.startswith("cidr:"):
            continue
        block = target.split(":", 1)[1]
        if block not in open_blocks:
            continue
        src = str(e.get("source") or "")
        if not src.startswith("security_group:"):
            continue
        open_sg_ids.add(src)
        triggering_rules[src].append(
            {
                "cidr": block,
                "protocol": e.get("protocol", ""),
                "port_range": e.get("port_range", ""),
                "rule_address": e.get("rule_address", ""),
            }
        )
    out: list[dict] = []
    for sg_id in sorted(open_sg_ids):
        n = nodes_by_id.get(sg_id) or {"id": sg_id}
        if env and n.get("env") != env:
            continue
        out.append(
            {
                "id": sg_id,
                "env": n.get("env", ""),
                "address": n.get("address", ""),
                "description": n.get("description", ""),
                "vpc_id": n.get("vpc_id", ""),
                "open_rules": triggering_rules.get(sg_id, []),
            }
        )
    return out


@mcp.tool()
def service_network_path(
    source: str,
    target: str,
    persist_dir: str | None = None,
) -> dict:
    """Best-effort network path between two `k8s_resource` ids.

    Walks pod → node → (subnet via Node provider id heuristic) → vpc, and
    surfaces every security_group attached to either endpoint. Pure structural
    BFS over the in-memory graph; returns ``error`` if either id is missing.
    """
    store = _open(persist_dir)
    nodes_by_id = {n.get("id"): n for n in store.all_nodes() if n.get("id")}
    edges = store.all_edges()
    if source not in nodes_by_id:
        return {"error": f"source not found: {source}"}
    if target not in nodes_by_id:
        return {"error": f"target not found: {target}"}

    adj: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for e in edges:
        s = str(e.get("source") or "")
        t = str(e.get("target") or "")
        rel = str(e.get("relation") or "")
        adj[s].append((t, rel))
        adj[t].append((s, rel))

    def _bfs(start: str, end: str) -> list[str]:
        prev: dict[str, str | None] = {start: None}
        q: deque[str] = deque([start])
        while q:
            cur = q.popleft()
            if cur == end:
                path: list[str] = []
                node: str | None = cur
                while node is not None:
                    path.append(node)
                    node = prev[node]
                path.reverse()
                return path
            for nb, _rel in adj.get(cur, []):
                if nb not in prev:
                    prev[nb] = cur
                    q.append(nb)
        return []

    path = _bfs(source, target)
    nodes_on_path = [nodes_by_id.get(p, {"id": p}) for p in path]
    # Surface SGs touching pods on the path (best-effort — k8s does not store
    # SG-level wiring; we pick out subnet/vpc nodes instead).
    related: list[dict] = []
    seen_related: set[str] = set()
    for nid in path:
        for nb, rel in adj.get(nid, []):
            if nb in seen_related or nb in path:
                continue
            nb_node = nodes_by_id.get(nb)
            if nb_node is None:
                continue
            if nb_node.get("type") in {"subnet", "vpc", "security_group"}:
                seen_related.add(nb)
                related.append({**nb_node, "via": rel})
    return {
        "source": source,
        "target": target,
        "path_length": max(0, len(path) - 1),
        "path": nodes_on_path,
        "related_network_nodes": related,
    }


# ---- IAM --------------------------------------------------------------------


@mcp.tool()
def iam_role_assumers(
    role_arn: str,
    persist_dir: str | None = None,
) -> list[dict]:
    """All principals (k8s ServiceAccounts via IRSA, AWS services, other roles)
    that can assume the given role.
    """
    if not role_arn:
        return []
    store = _open(persist_dir)
    nodes_by_id = {n.get("id"): n for n in store.all_nodes() if n.get("id")}
    edges = store.all_edges()
    target_id = ""
    if role_arn.startswith("iam_role:"):
        target_id = role_arn
    else:
        # Match by node id suffix or by stored arn attribute.
        for nid, n in nodes_by_id.items():
            if n.get("type") != "iam_role":
                continue
            if nid == f"iam_role:{role_arn}" or n.get("arn") == role_arn:
                target_id = nid
                break
    if not target_id:
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for e in edges:
        rel = str(e.get("relation") or "")
        # incoming irsa_bound (SA → role) and incoming can_assume (other role → role)
        if rel in {"irsa_bound", "can_assume"} and str(e.get("target") or "") == target_id:
            src = str(e.get("source") or "")
            if src in seen:
                continue
            seen.add(src)
            src_node = nodes_by_id.get(src, {"id": src})
            out.append(
                {
                    "principal_id": src,
                    "principal_type": src_node.get("type", "unknown"),
                    "principal_label": src_node.get("label", src),
                    "relation": rel,
                }
            )
        # outgoing assumed_by (role → service)
        if rel == "assumed_by" and str(e.get("source") or "") == target_id:
            tgt = str(e.get("target") or "")
            if tgt in seen:
                continue
            seen.add(tgt)
            tgt_node = nodes_by_id.get(tgt, {"id": tgt})
            out.append(
                {
                    "principal_id": tgt,
                    "principal_type": tgt_node.get("type", "aws_service"),
                    "principal_label": tgt_node.get("label", tgt),
                    "relation": rel,
                }
            )
    return out


@mcp.tool()
def irsa_chain(
    service_account: str,
    namespace: str,
    persist_dir: str | None = None,
) -> dict:
    """Service account → IAM role → policies → action_summary chain.

    Returns ``found=False`` when the SA is not annotated for IRSA or no
    matching iam_role node exists in the graph.
    """
    store = _open(persist_dir)
    nodes_by_id = {n.get("id"): n for n in store.all_nodes() if n.get("id")}
    edges = store.all_edges()
    sa_id = f"k8s_resource:{namespace}/ServiceAccount/{service_account}"
    sa_node = nodes_by_id.get(sa_id)
    if sa_node is None:
        return {
            "service_account": service_account,
            "namespace": namespace,
            "found": False,
            "reason": "ServiceAccount not in graph",
        }
    role_id = ""
    for e in edges:
        if e.get("relation") != "irsa_bound":
            continue
        if str(e.get("source") or "") == sa_id:
            role_id = str(e.get("target") or "")
            break
    if not role_id:
        return {
            "service_account": service_account,
            "namespace": namespace,
            "found": False,
            "reason": "no irsa_bound edge",
        }
    role_node = nodes_by_id.get(role_id, {"id": role_id})
    policies: list[dict] = []
    action_verbs: set[str] = set()
    for e in edges:
        if e.get("relation") not in {"attaches", "inlines"}:
            continue
        if str(e.get("source") or "") != role_id:
            continue
        pol_id = str(e.get("target") or "")
        pol_node = nodes_by_id.get(pol_id, {"id": pol_id})
        verbs = pol_node.get("action_verbs") or []
        if isinstance(verbs, list):
            for v in verbs:
                if isinstance(v, str):
                    action_verbs.add(v)
        policies.append(
            {
                "id": pol_id,
                "type": pol_node.get("type", ""),
                "name": pol_node.get("name", ""),
                "action_verbs": verbs,
                "via": e.get("relation"),
            }
        )
    return {
        "service_account": service_account,
        "namespace": namespace,
        "found": True,
        "service_account_node": sa_node,
        "role": role_node,
        "policies": policies,
        "action_summary": sorted(action_verbs),
    }


# ---- Image ------------------------------------------------------------------


@mcp.tool()
def find_image_users(
    image_substring: str,
    persist_dir: str | None = None,
) -> list[dict]:
    """All `k8s_resource` nodes pulling images whose ref contains the substring.

    Useful for "where is image X running?" queries. Empty substring returns ``[]``.
    """
    if not image_substring:
        return []
    store = _open(persist_dir)
    nodes = store.all_nodes()
    edges = store.all_edges()
    nodes_by_id = {n.get("id"): n for n in nodes if n.get("id")}
    sub = image_substring.lower()
    matching_image_ids: set[str] = set()
    for n in nodes:
        if n.get("type") != "image":
            continue
        ref = str(n.get("full_ref") or n.get("label") or n.get("id") or "")
        if sub in ref.lower():
            matching_image_ids.add(n["id"])
    if not matching_image_ids:
        return []
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for e in edges:
        if e.get("relation") != "runs_image":
            continue
        if str(e.get("target") or "") not in matching_image_ids:
            continue
        src = str(e.get("source") or "")
        tgt = str(e.get("target") or "")
        if (src, tgt) in seen:
            continue
        seen.add((src, tgt))
        src_node = nodes_by_id.get(src, {"id": src})
        img_node = nodes_by_id.get(tgt, {"id": tgt})
        out.append(
            {
                "k8s_resource_id": src,
                "kind": src_node.get("kind", ""),
                "namespace": src_node.get("namespace", ""),
                "name": src_node.get("name", ""),
                "image_id": tgt,
                "image_ref": img_node.get("full_ref", ""),
            }
        )
    return out


# ---- Storage ----------------------------------------------------------------


@mcp.tool()
def find_unbound_pvcs(
    env: str | None = None,
    persist_dir: str | None = None,
) -> list[dict]:
    """PVCs without an outgoing ``bound_to`` edge.

    Pure GraphStore query. The optional ``env`` arg currently has no effect on
    PVCs (PVCs are k8s-cluster-scoped) but is accepted for API symmetry with
    the other Phase 7B tools.
    """
    store = _open(persist_dir)
    nodes = store.all_nodes()
    edges = store.all_edges()
    bound_sources: set[str] = set()
    for e in edges:
        if e.get("relation") != "bound_to":
            continue
        bound_sources.add(str(e.get("source") or ""))
    out: list[dict] = []
    for n in nodes:
        if n.get("kind") != "PersistentVolumeClaim":
            continue
        nid = n.get("id") or ""
        if nid in bound_sources:
            continue
        if env and n.get("env") and n.get("env") != env:
            continue
        out.append(
            {
                "id": nid,
                "namespace": n.get("namespace", ""),
                "name": n.get("name", ""),
                "storage_class": (
                    (n.get("pvc_binding") or {}).get("storage_class_name", "")
                    if isinstance(n.get("pvc_binding"), dict)
                    else ""
                ),
            }
        )
    return out
