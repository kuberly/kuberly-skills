"""ImageBuildLayer — extract container images from k8s workload nodes.

Runs AFTER `k8s`, BEFORE `dependency`. Pure read of nodes already in the
GraphStore — no external API calls. Empty-store tolerant.

Nodes:
  * ``image``     — ``image:<full-ref>``        (registry/repository@digest or :tag)
  * ``ecr_repo``  — ``ecr_repo:<registry>/<repository>``  (only when ECR)

Edges:
  * k8s_resource → image    (``runs_image``)
  * image        → ecr_repo (``from_repo``)

TODO (future / Phase 7C+):
  * ``_fetch_gha_runs(repo, sha)`` → emit ``commit:<repo>/<sha>`` and
    ``workflow_run:<repo>/<run-id>`` nodes by hitting the GitHub API. Gate
    behind ``ctx.get("image_build_gha_enrichment_enabled")`` so the test loop
    stays offline. Requires authenticated GitHub token; defer to a phase that
    has secret plumbing.
"""

from __future__ import annotations

import re

from .base import Layer


_IMAGE_KINDS = {"Deployment", "StatefulSet", "DaemonSet", "Job", "ReplicaSet", "Pod"}
_ECR_RE = re.compile(r"^[0-9]+\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com$")


def _parse_image_ref(ref: str) -> dict | None:
    """Parse ``[registry/]repository[:tag][@sha256:digest]`` into parts.

    Returns ``None`` for empty / clearly invalid refs.
    """
    if not ref or not isinstance(ref, str):
        return None
    full = ref.strip()
    digest = ""
    if "@" in full:
        full, digest = full.split("@", 1)
    tag = ""
    # A registry can include a port (host:port), so split tag carefully.
    repo_part = full
    last_slash = full.rfind("/")
    last_colon = full.rfind(":")
    if last_colon > last_slash:
        repo_part = full[:last_colon]
        tag = full[last_colon + 1 :]
    # Determine registry vs repository.
    parts = repo_part.split("/", 1)
    if len(parts) == 2 and ("." in parts[0] or ":" in parts[0] or parts[0] == "localhost"):
        registry = parts[0]
        repository = parts[1]
    else:
        # docker.io implicit registry
        registry = "docker.io"
        repository = repo_part if "/" in repo_part else f"library/{repo_part}"
    return {
        "registry": registry,
        "repository": repository,
        "tag": tag,
        "digest": digest,
        "full_ref": ref,
    }


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

        if not k8s_nodes:
            if verbose:
                print("  [ImageBuildLayer] no k8s nodes — emitting 0 nodes")
            return [], []

        nodes: list[dict] = []
        edges: list[dict] = []
        emitted: set[str] = set()

        def _emit_node(node: dict) -> None:
            if node["id"] in emitted:
                return
            emitted.add(node["id"])
            nodes.append(node)

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
                # Stable id — prefer digest when available; tag otherwise.
                if parsed["digest"]:
                    suffix = f"@{parsed['digest']}"
                elif parsed["tag"]:
                    suffix = f":{parsed['tag']}"
                else:
                    suffix = ""
                image_id = f"image:{parsed['registry']}/{parsed['repository']}{suffix}"
                _emit_node(
                    {
                        "id": image_id,
                        "type": "image",
                        "label": parsed["full_ref"],
                        "registry": parsed["registry"],
                        "repository": parsed["repository"],
                        "tag": parsed["tag"],
                        "digest": parsed["digest"],
                        "full_ref": parsed["full_ref"],
                    }
                )
                edges.append(
                    {
                        "source": n["id"],
                        "target": image_id,
                        "relation": "runs_image",
                    }
                )
                if _ECR_RE.match(parsed["registry"]):
                    repo_id = f"ecr_repo:{parsed['registry']}/{parsed['repository']}"
                    _emit_node(
                        {
                            "id": repo_id,
                            "type": "ecr_repo",
                            "label": f"{parsed['registry']}/{parsed['repository']}",
                            "registry": parsed["registry"],
                            "repository": parsed["repository"],
                        }
                    )
                    edges.append(
                        {
                            "source": image_id,
                            "target": repo_id,
                            "relation": "from_repo",
                        }
                    )

        if verbose:
            print(f"  [ImageBuildLayer] emitted {len(nodes)} nodes / {len(edges)} edges")
        return nodes, edges
