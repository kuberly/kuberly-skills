"""ImageBuildLayer — extract container images from k8s workload nodes,
optionally enriched with GitHub Actions (GHA) and AWS ECR metadata.

Runs AFTER ``k8s``, BEFORE ``dependency``. v1 path is a pure read of nodes
already in the GraphStore — no external API calls. Empty-store tolerant.

Nodes (v1 — always):
  * ``image``     — ``image:<full-ref>``        (registry/repository@digest or :tag)
  * ``ecr_repo``  — ``ecr_repo:<registry>/<repository>``  (only when ECR)

Edges (v1 — always):
  * k8s_resource → image    (``runs_image``)
  * image        → ecr_repo (``from_repo``)

Phase 7E enrichment paths (auth-gated, off by default — explicit opt-in):

GHA (stdlib ``urllib.request`` — no `requests` lib dep):
  * ``commit:<repo>/<sha>``
  * ``workflow_run:<repo>/<run_id>``
  * Edges: ``commit → workflow_run`` (``triggered``);
           ``workflow_run → image`` (``built``) — best-effort SHA-substring match.

  ctx keys:
    * ``enable_gha_enrichment: bool = False``
    * ``github_repos: list[str] | None``  (auto-discover when None)
    * ``github_token: str | None``        (falls back to ``GITHUB_TOKEN`` /
       ``KUBERLY_GITHUB_TOKEN`` env)

ECR (boto3 OPTIONAL — wrapped in try/except; soft-degrades on ImportError):
  * Enriches existing ``ecr_repo:`` nodes with ``image_tag_mutability``,
    ``scan_on_push``, ``encryption_type``, ``created_at``, ``lifecycle_policy_text``.
  * ``image_scan_finding:<image-id>/<cve>`` for HIGH/CRITICAL vulnerabilities.
  * Edge: ``image → image_scan_finding`` (``has_finding``).

  ctx keys:
    * ``enable_ecr_enrichment: bool = False``
    * ``aws_account_id: str | None``
    * ``aws_region: str | None``  (falls back to AWS_REGION env / ``us-east-1``)

Soft-degrade contract: every enrichment path catches Exception, logs a warning
when ``ctx['verbose']``, and never crashes the layer. v1 nodes/edges are always
emitted; enrichment nodes only show up when their flags are on AND auth works.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request

from .base import Layer


_IMAGE_KINDS = {"Deployment", "StatefulSet", "DaemonSet", "Job", "ReplicaSet", "Pod"}
_ECR_RE = re.compile(r"^([0-9]+)\.dkr\.ecr\.([a-z0-9-]+)\.amazonaws\.com$")
_GHA_API = "https://api.github.com"


def _log(verbose: bool, msg: str) -> None:
    if verbose:
        print(f"  [ImageBuildLayer] {msg}", file=sys.stderr)


def _parse_image_ref(ref: str) -> dict | None:
    """Parse ``[registry/]repository[:tag][@sha256:digest]`` into parts."""
    if not ref or not isinstance(ref, str):
        return None
    full = ref.strip()
    digest = ""
    if "@" in full:
        full, digest = full.split("@", 1)
    tag = ""
    repo_part = full
    last_slash = full.rfind("/")
    last_colon = full.rfind(":")
    if last_colon > last_slash:
        repo_part = full[:last_colon]
        tag = full[last_colon + 1 :]
    parts = repo_part.split("/", 1)
    if len(parts) == 2 and ("." in parts[0] or ":" in parts[0] or parts[0] == "localhost"):
        registry = parts[0]
        repository = parts[1]
    else:
        registry = "docker.io"
        repository = repo_part if "/" in repo_part else f"library/{repo_part}"
    return {
        "registry": registry,
        "repository": repository,
        "tag": tag,
        "digest": digest,
        "full_ref": ref,
    }


# ---------------------------------------------------------------------------
# GHA enrichment (stdlib urllib only)
# ---------------------------------------------------------------------------


def _gha_request(url: str, token: str, timeout: float = 10.0) -> dict | list | None:
    """Tiny wrapper over ``urllib.request`` — returns parsed JSON or None on
    any error. Never raises.
    """
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "kuberly-graph-image-build-layer/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            if resp.status != 200:
                return None
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None
    except Exception:  # noqa: BLE001 — never propagate
        return None


def _auto_discover_repos(image_nodes: list[dict]) -> list[str]:
    """Best-effort: pull ``owner/repo`` candidates from ECR repository names.

    For ``traigent/backend`` style ECR repos, we treat the repo path itself
    as a candidate ``owner/repo``. Caller will skip any repo that 404s.
    """
    out: list[str] = []
    seen: set[str] = set()
    for n in image_nodes:
        repo = str(n.get("repository") or "")
        if "/" not in repo:
            continue
        parts = repo.split("/")
        if len(parts) < 2:
            continue
        candidate = f"{parts[0]}/{parts[1]}"
        if candidate not in seen:
            seen.add(candidate)
            out.append(candidate)
    return out


def _enrich_with_gha(
    ctx: dict,
    image_nodes: list[dict],
) -> tuple[list[dict], list[dict]]:
    verbose = bool(ctx.get("verbose"))
    if not ctx.get("enable_gha_enrichment"):
        return [], []
    token = (
        ctx.get("github_token")
        or os.environ.get("GITHUB_TOKEN")
        or os.environ.get("KUBERLY_GITHUB_TOKEN")
    )
    if not token:
        _log(verbose, "GHA enrichment requested but no GITHUB_TOKEN — skipping")
        return [], []

    repos = ctx.get("github_repos")
    if not repos:
        repos = _auto_discover_repos(image_nodes)
        _log(verbose, f"auto-discovered {len(repos)} GHA repo candidate(s)")
    if not repos:
        return [], []

    # Index image nodes by lowercase tag for fast head_sha matching.
    images_by_tag: dict[str, list[dict]] = {}
    for n in image_nodes:
        tag = str(n.get("tag") or "").lower()
        if tag:
            images_by_tag.setdefault(tag, []).append(n)

    nodes: list[dict] = []
    edges: list[dict] = []
    seen_node_ids: set[str] = set()

    def _emit(node: dict) -> None:
        nid = node["id"]
        if nid in seen_node_ids:
            return
        seen_node_ids.add(nid)
        nodes.append(node)

    for repo in repos:
        repo = str(repo).strip()
        if "/" not in repo:
            continue
        url = f"{_GHA_API}/repos/{repo}/actions/runs?per_page=20&status=completed"
        payload = _gha_request(url, token)
        if not isinstance(payload, dict):
            _log(verbose, f"GHA fetch failed or empty for {repo}")
            continue
        runs = payload.get("workflow_runs") or []
        if not isinstance(runs, list):
            continue
        for run in runs:
            if not isinstance(run, dict):
                continue
            run_id = run.get("id")
            head_sha = str(run.get("head_sha") or "")
            if not run_id or not head_sha:
                continue
            commit_id = f"commit:{repo}/{head_sha}"
            run_node_id = f"workflow_run:{repo}/{run_id}"

            head_commit = run.get("head_commit") if isinstance(run.get("head_commit"), dict) else {}
            commit_msg = str(head_commit.get("message") or "")
            if len(commit_msg) > 200:
                commit_msg = commit_msg[:197] + "..."
            author = head_commit.get("author") or {}
            author_name = str(author.get("name") or "") if isinstance(author, dict) else ""
            committed_at = str(head_commit.get("timestamp") or "")

            _emit(
                {
                    "id": commit_id,
                    "type": "commit",
                    "label": f"{repo}@{head_sha[:7]}",
                    "repo": repo,
                    "sha": head_sha,
                    "message_summary": commit_msg,
                    "author": author_name,
                    "committed_at": committed_at,
                }
            )
            _emit(
                {
                    "id": run_node_id,
                    "type": "workflow_run",
                    "label": f"{repo} run {run_id}",
                    "repo": repo,
                    "run_id": int(run_id) if isinstance(run_id, int) else run_id,
                    "name": str(run.get("name") or ""),
                    "conclusion": str(run.get("conclusion") or ""),
                    "status": str(run.get("status") or ""),
                    "head_sha": head_sha,
                    "head_branch": str(run.get("head_branch") or ""),
                    "created_at": str(run.get("created_at") or ""),
                    "run_url": str(run.get("html_url") or ""),
                }
            )
            edges.append(
                {
                    "source": commit_id,
                    "target": run_node_id,
                    "relation": "triggered",
                }
            )
            # Best-effort match: image tag containing ≥7 chars of head_sha.
            sha_lc = head_sha.lower()
            for prefix_len in (40, 12, 7):
                if len(sha_lc) < prefix_len:
                    continue
                needle = sha_lc[:prefix_len]
                matched_any = False
                for tag, candidate_imgs in images_by_tag.items():
                    if needle in tag:
                        for img in candidate_imgs:
                            edges.append(
                                {
                                    "source": run_node_id,
                                    "target": img["id"],
                                    "relation": "built",
                                }
                            )
                            matched_any = True
                if matched_any:
                    break

    _log(verbose, f"GHA enrichment emitted {len(nodes)} nodes / {len(edges)} edges")
    return nodes, edges


# ---------------------------------------------------------------------------
# ECR enrichment (boto3 optional)
# ---------------------------------------------------------------------------


def _enrich_with_ecr(
    ctx: dict,
    image_nodes: list[dict],
    ecr_repo_nodes: list[dict],
) -> tuple[list[dict], list[dict], list[dict]]:
    """Returns (extra_nodes, extra_edges, repo_metadata_patches).

    ``repo_metadata_patches`` is a list of dicts {"id": <ecr_repo:id>, ...new
    metadata kv...} the caller merges into the already-emitted ``ecr_repo``
    nodes before they hit the store.
    """
    verbose = bool(ctx.get("verbose"))
    if not ctx.get("enable_ecr_enrichment"):
        return [], [], []
    if not ecr_repo_nodes:
        _log(verbose, "ECR enrichment: no ecr_repo nodes — nothing to enrich")
        return [], [], []

    try:
        import boto3  # type: ignore
        from botocore.exceptions import (  # type: ignore
            BotoCoreError,
            ClientError,
            NoCredentialsError,
        )
    except Exception as exc:  # noqa: BLE001
        _log(verbose, f"boto3 unavailable ({exc}); skipping ECR enrichment")
        return [], [], []

    region = str(ctx.get("aws_region") or os.environ.get("AWS_REGION") or "us-east-1")

    try:
        session = boto3.Session()
        ecr = session.client("ecr", region_name=region)
    except Exception as exc:  # noqa: BLE001
        _log(verbose, f"ECR client init failed ({exc}); skipping ECR enrichment")
        return [], [], []

    # Group images by ecr_repo id for digest lookup later.
    images_by_repo: dict[str, list[dict]] = {}
    for img in image_nodes:
        registry = str(img.get("registry") or "")
        repository = str(img.get("repository") or "")
        if not _ECR_RE.match(registry) or not repository:
            continue
        repo_id = f"ecr_repo:{registry}/{repository}"
        images_by_repo.setdefault(repo_id, []).append(img)

    nodes: list[dict] = []
    edges: list[dict] = []
    patches: list[dict] = []
    seen_findings: set[str] = set()

    for repo_node in ecr_repo_nodes:
        repo_id = str(repo_node.get("id") or "")
        repository = str(repo_node.get("repository") or "")
        registry = str(repo_node.get("registry") or "")
        if not repository:
            continue

        # Account-id from the ECR registry hostname (NNNNNNNNNNNN.dkr.ecr...).
        m = _ECR_RE.match(registry)
        if not m:
            continue
        repo_account_id = m.group(1)
        target_account = ctx.get("aws_account_id") or repo_account_id

        # 1. describe_repositories (cross-account via registryId).
        patch: dict = {"id": repo_id}
        try:
            kwargs: dict = {"repositoryNames": [repository]}
            if target_account:
                kwargs["registryId"] = str(target_account)
            resp = ecr.describe_repositories(**kwargs)
            repos = resp.get("repositories") or []
            if repos:
                r = repos[0]
                patch["image_tag_mutability"] = str(r.get("imageTagMutability") or "")
                scan_cfg = r.get("imageScanningConfiguration") or {}
                patch["scan_on_push"] = bool(scan_cfg.get("scanOnPush") or False)
                enc = r.get("encryptionConfiguration") or {}
                patch["encryption_type"] = str(enc.get("encryptionType") or "")
                created = r.get("createdAt")
                patch["created_at"] = str(created) if created else ""
        except (NoCredentialsError, BotoCoreError, ClientError) as exc:
            _log(verbose, f"describe_repositories({repository}) failed: {exc}")
        except Exception as exc:  # noqa: BLE001
            _log(verbose, f"describe_repositories({repository}) unexpected: {exc}")

        # 2. lifecycle policy (404 is fine).
        try:
            kwargs2: dict = {"repositoryName": repository}
            if target_account:
                kwargs2["registryId"] = str(target_account)
            lp = ecr.get_lifecycle_policy(**kwargs2)
            text = str(lp.get("lifecyclePolicyText") or "")
            patch["lifecycle_policy_text"] = text[:500]
        except ClientError as exc:
            code = ""
            try:
                code = str(exc.response.get("Error", {}).get("Code", ""))
            except Exception:  # noqa: BLE001
                code = ""
            if code not in {"LifecyclePolicyNotFoundException", "RepositoryPolicyNotFoundException"}:
                _log(verbose, f"get_lifecycle_policy({repository}) failed: {exc}")
        except (NoCredentialsError, BotoCoreError) as exc:
            _log(verbose, f"get_lifecycle_policy({repository}) failed: {exc}")
        except Exception as exc:  # noqa: BLE001
            _log(verbose, f"get_lifecycle_policy({repository}) unexpected: {exc}")

        # If we collected any new metadata, queue the patch.
        if len(patch) > 1:
            patches.append(patch)

        # 3. Scan findings per image (HIGH/CRITICAL only, top 10 per image).
        for img in images_by_repo.get(repo_id, []):
            digest = str(img.get("digest") or "")
            tag = str(img.get("tag") or "")
            image_id_kwargs: dict = {}
            if digest:
                image_id_kwargs["imageDigest"] = digest
            elif tag:
                image_id_kwargs["imageTag"] = tag
            else:
                continue
            try:
                kwargs3: dict = {
                    "repositoryName": repository,
                    "imageId": image_id_kwargs,
                }
                if target_account:
                    kwargs3["registryId"] = str(target_account)
                resp = ecr.describe_image_scan_findings(**kwargs3)
            except (NoCredentialsError, BotoCoreError, ClientError) as exc:
                _log(verbose, f"describe_image_scan_findings({repository}) failed: {exc}")
                continue
            except Exception as exc:  # noqa: BLE001
                _log(verbose, f"describe_image_scan_findings({repository}) unexpected: {exc}")
                continue

            findings_obj = resp.get("imageScanFindings") or {}
            findings_list = findings_obj.get("findings") or []
            kept: list[dict] = []
            for f in findings_list:
                if not isinstance(f, dict):
                    continue
                sev = str(f.get("severity") or "").upper()
                if sev not in {"HIGH", "CRITICAL"}:
                    continue
                kept.append(f)
            kept.sort(
                key=lambda x: (
                    0 if str(x.get("severity") or "").upper() == "CRITICAL" else 1,
                    -float(x.get("cvssScore") or 0.0),
                )
            )
            for f in kept[:10]:
                cve = str(f.get("name") or "").strip() or "unknown"
                fid = f"image_scan_finding:{img['id'].removeprefix('image:')}/{cve}"
                if fid in seen_findings:
                    continue
                seen_findings.add(fid)
                attrs = f.get("attributes") or []
                pkg_name = ""
                pkg_version = ""
                fixed_version = ""
                if isinstance(attrs, list):
                    for a in attrs:
                        if not isinstance(a, dict):
                            continue
                        k = str(a.get("key") or "")
                        v = str(a.get("value") or "")
                        if k == "package_name":
                            pkg_name = v
                        elif k == "package_version":
                            pkg_version = v
                        elif k in {"fixed_version", "fixed_in_version"}:
                            fixed_version = v
                desc = str(f.get("description") or "")
                if len(desc) > 200:
                    desc = desc[:197] + "..."
                nodes.append(
                    {
                        "id": fid,
                        "type": "image_scan_finding",
                        "label": f"{cve} {sev}",
                        "severity": sev,
                        "cve": cve,
                        "cvss_score": float(f.get("cvssScore") or 0.0),
                        "package_name": pkg_name,
                        "package_version": pkg_version,
                        "fixed_version": fixed_version,
                        "description": desc,
                        "uri": str(f.get("uri") or ""),
                    }
                )
                edges.append(
                    {
                        "source": img["id"],
                        "target": fid,
                        "relation": "has_finding",
                    }
                )

    _log(
        verbose,
        f"ECR enrichment emitted {len(nodes)} finding nodes / "
        f"{len(edges)} edges / {len(patches)} repo patches",
    )
    return nodes, edges, patches


# ---------------------------------------------------------------------------
# Layer
# ---------------------------------------------------------------------------


class ImageBuildLayer(Layer):
    name = "image_build"
    refresh_trigger = "manual"

    def scan(self, ctx: dict) -> tuple[list[dict], list[dict]]:
        verbose = bool(ctx.get("verbose"))
        store = ctx.get("graph_store")
        if store is None:
            from ..store import open_store
            from pathlib import Path

            persist_dir = ctx.get("persist_dir") or str(
                Path(ctx.get("repo_root", ".")) / ".kuberly"
            )
            store = open_store(Path(persist_dir))

        try:
            k8s_nodes = store.all_nodes(layer="k8s")
        except Exception:
            k8s_nodes = []

        nodes: list[dict] = []
        edges: list[dict] = []
        emitted: set[str] = set()
        ecr_repo_nodes: list[dict] = []
        image_nodes_emitted: list[dict] = []

        def _emit_node(node: dict) -> None:
            if node["id"] in emitted:
                return
            emitted.add(node["id"])
            nodes.append(node)

        if not k8s_nodes:
            _log(verbose, "no k8s nodes — emitting 0 v1 nodes (enrichment may still run)")

        for n in k8s_nodes:
            if n.get("kind") not in _IMAGE_KINDS:
                continue
            images = n.get("container_images") or []
            if not isinstance(images, list):
                continue
            for ref in images:
                parsed = _parse_image_ref(str(ref))
                if not parsed:
                    continue
                if parsed["digest"]:
                    suffix = f"@{parsed['digest']}"
                elif parsed["tag"]:
                    suffix = f":{parsed['tag']}"
                else:
                    suffix = ""
                image_id = f"image:{parsed['registry']}/{parsed['repository']}{suffix}"
                if image_id not in emitted:
                    img_node = {
                        "id": image_id,
                        "type": "image",
                        "label": parsed["full_ref"],
                        "registry": parsed["registry"],
                        "repository": parsed["repository"],
                        "tag": parsed["tag"],
                        "digest": parsed["digest"],
                        "full_ref": parsed["full_ref"],
                    }
                    _emit_node(img_node)
                    image_nodes_emitted.append(img_node)
                edges.append(
                    {
                        "source": n["id"],
                        "target": image_id,
                        "relation": "runs_image",
                    }
                )
                if _ECR_RE.match(parsed["registry"]):
                    repo_id = f"ecr_repo:{parsed['registry']}/{parsed['repository']}"
                    if repo_id not in emitted:
                        ecr_node = {
                            "id": repo_id,
                            "type": "ecr_repo",
                            "label": f"{parsed['registry']}/{parsed['repository']}",
                            "registry": parsed["registry"],
                            "repository": parsed["repository"],
                        }
                        _emit_node(ecr_node)
                        ecr_repo_nodes.append(ecr_node)
                    edges.append(
                        {
                            "source": image_id,
                            "target": repo_id,
                            "relation": "from_repo",
                        }
                    )

        # ---- Phase 7E enrichment paths (auth-gated) -------------------------
        try:
            gha_nodes, gha_edges = _enrich_with_gha(ctx, image_nodes_emitted)
        except Exception as exc:  # noqa: BLE001 — never crash the layer
            _log(verbose, f"GHA enrichment crashed unexpectedly: {exc}")
            gha_nodes, gha_edges = [], []
        for gn in gha_nodes:
            _emit_node(gn)
        edges.extend(gha_edges)

        try:
            ecr_extra_nodes, ecr_extra_edges, ecr_patches = _enrich_with_ecr(
                ctx, image_nodes_emitted, ecr_repo_nodes
            )
        except Exception as exc:  # noqa: BLE001 — never crash the layer
            _log(verbose, f"ECR enrichment crashed unexpectedly: {exc}")
            ecr_extra_nodes, ecr_extra_edges, ecr_patches = [], [], []
        for en in ecr_extra_nodes:
            _emit_node(en)
        edges.extend(ecr_extra_edges)
        # Apply repo-metadata patches by mutating already-queued ecr_repo nodes.
        if ecr_patches:
            patch_by_id = {p["id"]: p for p in ecr_patches}
            for n in nodes:
                if n.get("type") != "ecr_repo":
                    continue
                p = patch_by_id.get(n["id"])
                if not p:
                    continue
                for k, v in p.items():
                    if k == "id":
                        continue
                    n[k] = v

        if verbose:
            print(f"  [ImageBuildLayer] emitted {len(nodes)} nodes / {len(edges)} edges")
        return nodes, edges
