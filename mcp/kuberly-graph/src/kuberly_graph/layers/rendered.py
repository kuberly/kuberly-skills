"""RenderedLayer — inline ``cue eval`` of per-app manifests.

v0.51.0: drop the ``.kuberly/rendered_apps_<env>.json`` sidecar. Mirrors
v0.50.1's state-inline architectural principle — every layer writes
nodes/edges directly to the LanceDB store; no two-step indirection.

For each ``applications/<env>/<app>.json`` we run::

    cue cmd -t instance=<env> -t app=<app-name> dump .

inside ``cue/`` (the kuberly-stack convention as encoded by
``cue/generate.sh``). Output is multi-doc YAML on stdout.

Soft-degrade rules (every one returns ``([], [])`` cleanly, never raises):

  * ``cue`` binary not on PATH.
  * ``cue/`` directory not found in ``repo_root``.
  * ``applications/<env>/`` missing.
  * ``cue cmd`` non-zero exit.
  * YAML doc unparseable — skip that doc, continue.
"""

from __future__ import annotations

import json as _json
import os
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

from .base import Layer
from ._util import walk_rendered_resources


_DOC_SEP_RE = re.compile(r"^---\s*$", re.MULTILINE)


def _have_cue() -> bool:
    return bool(shutil.which("cue"))


def _split_yaml_docs(text: str) -> list[str]:
    """Split a multi-doc YAML stream on ``^---$`` lines."""
    if not text:
        return []
    parts = _DOC_SEP_RE.split(text)
    return [p.strip() for p in parts if p.strip()]


def _yaml_safe_load_all(text: str) -> list[dict]:
    """Parse a multi-doc YAML stream. Tries PyYAML first; falls back to a
    minimal regex-based extractor (apiVersion/kind/metadata.name/namespace)
    so the layer still produces structure when PyYAML is unavailable.
    """
    try:
        import yaml  # type: ignore

        out: list[dict] = []
        for doc in yaml.safe_load_all(text):
            if isinstance(doc, dict):
                out.append(doc)
        return out
    except Exception:
        pass

    # Stdlib-only fallback: regex over each ``---``-separated doc.
    out: list[dict] = []
    api_re = re.compile(r"^apiVersion:\s*(\S+)\s*$", re.MULTILINE)
    kind_re = re.compile(r"^kind:\s*(\S+)\s*$", re.MULTILINE)
    name_re = re.compile(r"^\s{2,}name:\s*(\S+)\s*$", re.MULTILINE)
    ns_re = re.compile(r"^\s{2,}namespace:\s*(\S+)\s*$", re.MULTILINE)
    for doc in _split_yaml_docs(text):
        a = api_re.search(doc)
        k = kind_re.search(doc)
        # Take the first ``name:`` under metadata-ish indent. The real
        # metadata.name is typically the first hit because YAML preserves
        # order, and metadata is convention-first.
        n = name_re.search(doc)
        ns = ns_re.search(doc)
        if not (a and k and n):
            continue
        out.append(
            {
                "apiVersion": a.group(1).strip("\"'"),
                "kind": k.group(1).strip("\"'"),
                "metadata": {
                    "name": n.group(1).strip("\"'"),
                    "namespace": (
                        ns.group(1).strip("\"'") if ns else ""
                    ),
                },
            }
        )
    return out


def _run_cue_dump(
    cue_dir: Path,
    *,
    instance: str,
    app: str,
    timeout_s: float = 60.0,
) -> str:
    """Invoke ``cue cmd -t instance=<env> -t app=<name> dump .`` inside
    ``cue_dir``. Returns the stdout text on success; empty string on any
    failure (logged to stderr).
    """
    bin_path = shutil.which("cue")
    if not bin_path:
        return ""
    cmd = [
        bin_path,
        "cmd",
        "-t",
        f"instance={instance}",
        "-t",
        f"app={app}",
        "dump",
        ".",
    ]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cue_dir),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env=os.environ.copy(),
        )
    except FileNotFoundError:
        return ""
    except subprocess.TimeoutExpired:
        print(
            f"  [RenderedLayer] cue cmd dump timed out after {timeout_s}s "
            f"(env={instance}, app={app})",
            file=sys.stderr,
        )
        return ""
    except Exception as exc:
        print(
            f"  [RenderedLayer] cue cmd dump errored: {exc} "
            f"(env={instance}, app={app})",
            file=sys.stderr,
        )
        return ""
    if proc.returncode != 0:
        # cue prints its diagnostic to stderr.
        err = (proc.stderr or "")[:300]
        print(
            f"  [RenderedLayer] cue cmd dump non-zero exit {proc.returncode} "
            f"(env={instance}, app={app}): {err}",
            file=sys.stderr,
        )
        return ""
    return proc.stdout or ""


def _resolve_app_name(json_path: Path, fallback: str) -> str:
    """The kuberly convention: take ``name`` from the top-level JSON config
    if present; otherwise fall back to the file stem. ``generate.sh`` does
    this via grep — we do the same via JSON parse.

    For argo-app wrapped JSON (``{"argo-app": {"common": {...}}}``), the
    application tag the CUE ``dump`` task filters by lives at
    ``argo-app.common.app_slug`` — that's the value matching
    ``metadata.annotations.application``. Generic enough: walk the tree.
    """
    try:
        data = _json.loads(json_path.read_text())
    except Exception:
        return fallback
    if isinstance(data, dict):
        # Direct top-level name (main/* layout).
        name = data.get("name")
        if isinstance(name, str) and name:
            return name
        # argo-app layout — pick the deepest ``app_slug`` inside ``common``.
        for outer_v in data.values():
            if not isinstance(outer_v, dict):
                continue
            common = outer_v.get("common")
            if isinstance(common, dict):
                slug = common.get("app_slug")
                if isinstance(slug, str) and slug:
                    return slug
                nm = common.get("name") or common.get("app_name")
                if isinstance(nm, str) and nm:
                    return nm
    return fallback


def _is_dumpable(json_path: Path) -> bool:
    """Sniff whether the JSON is in the ``cue cmd dump``-compatible shape.

    The ``commands_tool.cue dump`` task expects the JSON to land under
    ``config:`` at the package root (one of the resource-collection top-
    level fields like ``deployment``, ``service``, etc.). The argo-app
    wrapped layout is meant for a different render path
    (ApplicationSet -> argo) and produces no resources via this dump.
    """
    try:
        data = _json.loads(json_path.read_text())
    except Exception:
        return False
    if not isinstance(data, dict):
        return False
    if "argo-app" in data:
        return False
    # Direct layout: at least one of the resource-collection keys lives
    # at the top.
    direct_keys = {
        "common",
        "deployment",
        "service",
        "configMap",
        "secrets",
        "rbac",
        "name",
    }
    return bool(set(data.keys()) & direct_keys)


def _import_json_to_cue(
    cue_dir: Path,
    *,
    json_path: Path,
    label: str,
) -> Path | None:
    """Mirror generate.sh's ``cue import -f -l 'config:' -p applications``
    step: stage the per-app JSON as a temp ``.cue`` file inside ``cue_dir``
    so the ``cmd dump`` task can read it. Returns the temp file path on
    success, or ``None`` on failure. Caller must delete the temp file.
    """
    bin_path = shutil.which("cue")
    if not bin_path:
        return None
    # CUE treats files prefixed with ``_`` as private and excludes them
    # from the package, so dump never sees the staged config. Use a
    # plain prefix instead.
    out_name = f"kg_render_{label}.cue"
    out_path = cue_dir / out_name
    cmd = [
        bin_path,
        "import",
        "-f",
        "-l",
        "config:",
        "-p",
        "applications",
        str(json_path),
        "-o",
        str(out_path),
    ]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cue_dir),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception as exc:
        print(
            f"  [RenderedLayer] cue import errored: {exc} (json={json_path})",
            file=sys.stderr,
        )
        return None
    if proc.returncode != 0:
        print(
            f"  [RenderedLayer] cue import non-zero exit {proc.returncode} "
            f"(json={json_path}): {(proc.stderr or '')[:300]}",
            file=sys.stderr,
        )
        try:
            if out_path.exists():
                out_path.unlink()
        except Exception:
            pass
        return None
    return out_path


class RenderedLayer(Layer):
    name = "rendered"
    refresh_trigger = "manual"

    def scan(self, ctx: dict) -> tuple[list[dict], list[dict]]:
        repo_root = Path(ctx.get("repo_root", "."))
        verbose = bool(ctx.get("verbose"))
        existing_app_ids: set[str] = set(ctx.get("_existing_app_ids", set()))

        cue_dir = repo_root / "cue"
        apps_dir = repo_root / "applications"

        if not cue_dir.exists() or not cue_dir.is_dir():
            if verbose:
                print(
                    f"  [RenderedLayer] skip — no cue/ directory under "
                    f"{repo_root}"
                )
            return [], []
        if not apps_dir.exists() or not apps_dir.is_dir():
            if verbose:
                print(
                    f"  [RenderedLayer] skip — no applications/ directory "
                    f"under {repo_root}"
                )
            return [], []
        if not _have_cue():
            if verbose:
                print(
                    "  [RenderedLayer] skip — cue binary not on PATH "
                    "(soft-degrade)"
                )
            return [], []

        envs = sorted(
            p.name
            for p in apps_dir.iterdir()
            if p.is_dir() and not p.name.startswith(".")
        )
        if verbose:
            print(
                f"  [RenderedLayer] scanning envs={envs} (cue_dir={cue_dir})"
            )

        nodes: list[dict] = []
        edges: list[dict] = []

        for env in envs:
            env_dir = apps_dir / env
            if not env_dir.is_dir():
                continue
            for json_path in sorted(env_dir.glob("*.json")):
                stem = json_path.stem
                app_name = _resolve_app_name(json_path, stem)

                # Soft-skip JSON whose top-level shape isn't compatible
                # with the ``cue cmd dump`` task (e.g. argo-app wrapper).
                if not _is_dumpable(json_path):
                    if verbose:
                        print(
                            f"  [RenderedLayer] env={env} app={app_name}: "
                            f"JSON shape isn't dumpable (argo-app wrapper "
                            f"or unknown) — skip"
                        )
                    continue

                # Stage the JSON inside cue/ so cue cmd can read it as a
                # ``config:`` field — same shape generate.sh uses.
                staged = _import_json_to_cue(
                    cue_dir, json_path=json_path, label=re.sub(r"\W+", "_", stem)
                )
                if staged is None:
                    if verbose:
                        print(
                            f"  [RenderedLayer] env={env} app={app_name}: "
                            f"cue import failed — skip"
                        )
                    continue

                try:
                    yaml_text = _run_cue_dump(
                        cue_dir, instance=env, app=app_name
                    )
                finally:
                    try:
                        staged.unlink()
                    except Exception:
                        pass

                if not yaml_text.strip():
                    if verbose:
                        print(
                            f"  [RenderedLayer] env={env} app={app_name}: "
                            f"cue cmd dump produced no output — skip"
                        )
                    continue

                docs = _yaml_safe_load_all(yaml_text)
                if verbose:
                    print(
                        f"  [RenderedLayer] env={env} app={app_name}: "
                        f"parsed {len(docs)} docs"
                    )
                if not docs:
                    continue

                # Walk via the existing helper for shape consistency.
                payload = {app_name: {"manifests": docs}}
                per_app: dict[str, list[tuple[str, str, str, str]]] = (
                    defaultdict(list)
                )
                for api_v, kind, ns, name, app_id in walk_rendered_resources(
                    payload
                ):
                    per_app[app_id].append((api_v, kind, ns, name))

                for app, items in per_app.items():
                    render_id = f"app_render:{env}/{app}"
                    nodes.append(
                        {
                            "id": render_id,
                            "type": "app_render",
                            "label": app,
                            "env": env,
                            "app": app,
                            "manifest_count": len(items),
                            "source": "cue-inline",
                        }
                    )
                    cold_app_id = f"app:{env}/{app}"
                    if cold_app_id in existing_app_ids:
                        edges.append(
                            {
                                "source": cold_app_id,
                                "target": render_id,
                                "relation": "renders",
                            }
                        )
                    for api_v, kind, ns, name in items:
                        rid = f"rendered_resource:{env}/{app}/{kind}/{name}"
                        nodes.append(
                            {
                                "id": rid,
                                "type": "rendered_resource",
                                "label": f"{kind}/{name}",
                                "apiVersion": api_v,
                                "kind": kind,
                                "namespace": ns,
                                "name": name,
                                "env": env,
                                "app": app,
                            }
                        )
                        edges.append(
                            {
                                "source": render_id,
                                "target": rid,
                                "relation": "renders",
                            }
                        )

        if verbose:
            print(
                f"  [RenderedLayer] emitted {len(nodes)} nodes / "
                f"{len(edges)} edges"
            )
        return nodes, edges
