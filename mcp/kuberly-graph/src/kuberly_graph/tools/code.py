"""Phase 8H — TreeSitter + state extractor tools.

  * ``find_resource_callers`` — BFS from an HCL resource id along
    ``uses_var`` / ``refs`` edges to find every variable, output, or other
    resource that touches it.
  * ``module_io_summary`` — per-module counts of variables / outputs /
    resources / data / locals from TreeSitterLayer.
  * ``find_yaml_manifest_kind`` — lookup ``yaml_manifest:*`` nodes by Kind.
  * ``extract_state_sidecar`` — pull every module's tfstate from S3 into
    ``.kuberly/state_<env>.json`` so :class:`StateLayer` has data to ingest.
"""

from __future__ import annotations

from collections import defaultdict, deque
from pathlib import Path

from ..server import SERVER_CONFIG, mcp
from ..state_extract import detect_envs, extract_states_from_s3
from ..store import open_store


def _resolve_persist(persist_dir: str | None) -> str:
    return persist_dir or SERVER_CONFIG.get("persist_dir", ".kuberly")


def _resolve_repo(repo_root: str | None) -> str:
    return repo_root or SERVER_CONFIG.get("repo_root", ".")


def _open(persist_dir: str | None):
    return open_store(Path(_resolve_persist(persist_dir)).resolve())


# ---- TreeSitter queries -----------------------------------------------------


@mcp.tool()
def find_resource_callers(
    resource_id: str,
    persist_dir: str | None = None,
    max_depth: int = 4,
) -> dict:
    """All variables / resources / outputs that reference ``resource_id``.

    Walks ``uses_var`` and ``refs`` edges in **reverse** from the target up to
    ``max_depth`` hops. ``resource_id`` should be the full
    ``hcl_resource:<rel-path>/<type>/<name>`` form emitted by
    TreeSitterLayer.
    """
    if not resource_id:
        return {"error": "resource_id required"}
    store = _open(persist_dir)
    nodes_by_id = {n.get("id"): n for n in store.all_nodes() if n.get("id")}
    edges = store.all_edges()
    if resource_id not in nodes_by_id:
        return {
            "resource_id": resource_id,
            "found": False,
            "callers": [],
        }

    # Reverse adjacency for the relations we care about.
    rev: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for e in edges:
        rel = str(e.get("relation") or "")
        if rel not in {"uses_var", "refs", "reads_output", "declares"}:
            continue
        rev[str(e.get("target") or "")].append(
            (str(e.get("source") or ""), rel)
        )

    callers: list[dict] = []
    seen: set[str] = {resource_id}
    q: deque[tuple[str, int]] = deque([(resource_id, 0)])
    while q:
        cur, depth = q.popleft()
        if depth >= max(0, int(max_depth)):
            continue
        for src, rel in rev.get(cur, []):
            if src in seen:
                continue
            seen.add(src)
            n = nodes_by_id.get(src, {"id": src})
            callers.append(
                {
                    "id": src,
                    "type": n.get("type", ""),
                    "label": n.get("label", src),
                    "rel_path": n.get("rel_path", ""),
                    "via": rel,
                    "depth": depth + 1,
                }
            )
            q.append((src, depth + 1))

    return {
        "resource_id": resource_id,
        "found": True,
        "callers": callers,
        "depth_limit": int(max_depth),
    }


@mcp.tool()
def module_io_summary(
    module_id: str,
    persist_dir: str | None = None,
) -> dict:
    """Counts of variables / outputs / resources / data / locals declared
    inside ``module_id`` (e.g. ``module:aws/eks``).

    Pure GraphStore query — relies on TreeSitterLayer ``declares`` edges.
    Returns ``{module_id, found, counts, samples, total}``.
    """
    if not module_id:
        return {"error": "module_id required"}
    store = _open(persist_dir)
    nodes_by_id = {n.get("id"): n for n in store.all_nodes() if n.get("id")}
    if module_id not in nodes_by_id:
        return {"module_id": module_id, "found": False}

    counts: dict[str, int] = defaultdict(int)
    samples: dict[str, list[str]] = defaultdict(list)
    for e in store.all_edges():
        if str(e.get("relation") or "") != "declares":
            continue
        if str(e.get("source") or "") != module_id:
            continue
        tgt = str(e.get("target") or "")
        n = nodes_by_id.get(tgt) or {}
        ttype = str(n.get("type") or "")
        counts[ttype] += 1
        if len(samples[ttype]) < 8:
            samples[ttype].append(n.get("label", tgt))

    return {
        "module_id": module_id,
        "found": True,
        "counts": dict(counts),
        "samples": {k: v for k, v in samples.items()},
        "total": sum(counts.values()),
    }


@mcp.tool()
def find_yaml_manifest_kind(
    kind: str,
    persist_dir: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Every ``yaml_manifest:*`` node whose ``kind`` matches.

    Case-insensitive substring match — ``"deploy"`` matches ``Deployment``
    and ``DeploymentConfig``. Empty kind returns ``[]``.
    """
    if not kind:
        return []
    store = _open(persist_dir)
    needle = kind.lower()
    out: list[dict] = []
    for n in store.all_nodes():
        if n.get("type") != "yaml_manifest":
            continue
        if needle not in str(n.get("kind") or "").lower():
            continue
        out.append(
            {
                "id": n.get("id"),
                "kind": n.get("kind"),
                "api_version": n.get("api_version"),
                "name": n.get("name"),
                "rel_path": n.get("rel_path"),
                "doc_index": n.get("doc_index"),
            }
        )
        if len(out) >= max(1, int(limit)):
            break
    return out


# ---- State sidecar ----------------------------------------------------------


@mcp.tool()
def extract_state_sidecar(
    env: str | None = None,
    region: str = "eu-west-1",
    repo_root: str | None = None,
    persist_dir: str | None = None,
) -> dict:
    """Pull every module's Terraform/OpenTofu tfstate from S3 into
    ``.kuberly/state_<env>.json`` (the side-car StateLayer reads).

    When ``env`` is omitted, we pick the first directory under
    ``components/`` (single-env forks like Traigent dev).

    Soft-degrades when boto3 is missing or AWS creds are unset; returns
    ``{path, modules_extracted, modules_skipped, errors, bucket}``.
    """
    repo = _resolve_repo(repo_root)
    persist = _resolve_persist(persist_dir)
    target_env = env
    if not target_env:
        envs = detect_envs(repo)
        if not envs:
            return {
                "path": "",
                "modules_extracted": 0,
                "modules_skipped": 0,
                "errors": ["no components/<env>/ directory found"],
                "bucket": "",
            }
        target_env = envs[0]
    return extract_states_from_s3(
        repo_root=repo,
        env=target_env,
        region=region,
        persist_dir=persist,
    )
