"""Phase 7E image-build tools — pure GraphStore queries over the
``image_build`` layer including GHA / ECR enrichment nodes (when present).

Two new MCP tools:
  * ``find_image_scan_findings`` — list ``image_scan_finding`` nodes filtered
    by severity, sorted by severity then CVSS desc.
  * ``commit_to_image_chain`` — given a commit SHA, walk
    ``commit → workflow_run → image → k8s_resource``.

Both tools are empty-store tolerant: missing nodes yield ``[]`` / a chain
with null fields rather than crashing.
"""

from __future__ import annotations

from pathlib import Path

from ..server import SERVER_CONFIG, mcp
from ..store import open_store


def _resolve_persist(persist_dir: str | None) -> str:
    return persist_dir or SERVER_CONFIG.get("persist_dir", ".kuberly")


def _open(persist_dir: str | None):
    return open_store(Path(_resolve_persist(persist_dir)).resolve())


_SEV_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFORMATIONAL": 4}


@mcp.tool()
def find_image_scan_findings(
    severity: str = "HIGH",
    limit: int = 50,
    persist_dir: str | None = None,
) -> list[dict]:
    """List ``image_scan_finding`` nodes from the ECR enrichment path.

    Args:
        severity: Minimum severity to return — one of CRITICAL / HIGH /
            MEDIUM / LOW (case-insensitive). Anything at-or-above the
            threshold is included. Default ``HIGH``.
        limit: Cap on returned rows (default 50).
        persist_dir: GraphStore path. Defaults to the server's configured
            persist dir.

    Returns sorted by (severity rank ascending, cvss_score descending).
    Empty store / no findings → ``[]``.
    """
    store = _open(persist_dir)
    nodes = store.all_nodes()
    threshold = _SEV_ORDER.get(str(severity or "HIGH").upper(), 1)
    out: list[dict] = []
    for n in nodes:
        if n.get("type") != "image_scan_finding":
            continue
        sev = str(n.get("severity") or "").upper()
        rank = _SEV_ORDER.get(sev, 99)
        if rank > threshold:
            continue
        out.append(
            {
                "id": str(n.get("id") or ""),
                "severity": sev,
                "cve": n.get("cve", ""),
                "cvss_score": float(n.get("cvss_score") or 0.0),
                "package_name": n.get("package_name", ""),
                "package_version": n.get("package_version", ""),
                "fixed_version": n.get("fixed_version", ""),
                "description": n.get("description", ""),
                "uri": n.get("uri", ""),
            }
        )
    out.sort(
        key=lambda r: (
            _SEV_ORDER.get(str(r.get("severity") or "").upper(), 99),
            -float(r.get("cvss_score") or 0.0),
        )
    )
    if limit and limit > 0:
        out = out[: int(limit)]
    return out


@mcp.tool()
def commit_to_image_chain(
    commit_sha: str,
    persist_dir: str | None = None,
) -> dict:
    """Walk ``commit → workflow_run → image → k8s_resource`` for a given SHA.

    Args:
        commit_sha: Full or short (≥7 char) git SHA. Matched as a prefix
            against ``commit:`` node SHAs in the graph.
        persist_dir: GraphStore path.

    Returns:
        ``{"commit": <node|null>, "workflow_runs": [...],
           "images": [...], "k8s_resources": [...]}``. Missing fields stay
        ``null`` / empty rather than raising.
    """
    out: dict = {
        "commit": None,
        "workflow_runs": [],
        "images": [],
        "k8s_resources": [],
    }
    if not commit_sha or len(str(commit_sha).strip()) < 7:
        return out
    needle = str(commit_sha).strip().lower()

    store = _open(persist_dir)
    nodes = store.all_nodes()
    edges = store.all_edges()
    nodes_by_id = {str(n.get("id") or ""): n for n in nodes if n.get("id")}

    # Find the commit node by SHA prefix match.
    commit_node = None
    for n in nodes:
        if n.get("type") != "commit":
            continue
        sha = str(n.get("sha") or "").lower()
        if sha.startswith(needle) or needle.startswith(sha):
            commit_node = n
            break
    if commit_node is None:
        return out

    out["commit"] = {
        "id": str(commit_node.get("id") or ""),
        "repo": commit_node.get("repo", ""),
        "sha": commit_node.get("sha", ""),
        "message_summary": commit_node.get("message_summary", ""),
        "author": commit_node.get("author", ""),
        "committed_at": commit_node.get("committed_at", ""),
    }
    commit_id = str(commit_node.get("id") or "")

    # commit -> workflow_run (triggered)
    run_ids: list[str] = []
    for e in edges:
        if e.get("relation") != "triggered":
            continue
        if str(e.get("source") or "") != commit_id:
            continue
        rid = str(e.get("target") or "")
        if rid:
            run_ids.append(rid)
    for rid in run_ids:
        rn = nodes_by_id.get(rid)
        if not rn:
            continue
        out["workflow_runs"].append(
            {
                "id": rid,
                "repo": rn.get("repo", ""),
                "run_id": rn.get("run_id", ""),
                "name": rn.get("name", ""),
                "conclusion": rn.get("conclusion", ""),
                "head_branch": rn.get("head_branch", ""),
                "run_url": rn.get("run_url", ""),
            }
        )

    # workflow_run -> image (built)
    image_ids: list[str] = []
    seen_imgs: set[str] = set()
    for e in edges:
        if e.get("relation") != "built":
            continue
        if str(e.get("source") or "") not in run_ids:
            continue
        iid = str(e.get("target") or "")
        if iid and iid not in seen_imgs:
            seen_imgs.add(iid)
            image_ids.append(iid)
    for iid in image_ids:
        ino = nodes_by_id.get(iid)
        if not ino:
            continue
        out["images"].append(
            {
                "id": iid,
                "registry": ino.get("registry", ""),
                "repository": ino.get("repository", ""),
                "tag": ino.get("tag", ""),
                "digest": ino.get("digest", ""),
                "full_ref": ino.get("full_ref", ""),
            }
        )

    # k8s_resource -> image (runs_image) → reverse to find consumers.
    k8s_seen: set[str] = set()
    for e in edges:
        if e.get("relation") != "runs_image":
            continue
        if str(e.get("target") or "") not in image_ids:
            continue
        kid = str(e.get("source") or "")
        if not kid or kid in k8s_seen:
            continue
        k8s_seen.add(kid)
        kn = nodes_by_id.get(kid, {"id": kid})
        out["k8s_resources"].append(
            {
                "id": kid,
                "kind": kn.get("kind", ""),
                "namespace": kn.get("namespace", ""),
                "name": kn.get("name", ""),
            }
        )

    return out
