"""RenderedLayer — CUE-rendered manifests in `.kuberly/rendered_apps_<env>.json`."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from .base import Layer
from ._util import walk_rendered_resources


class RenderedLayer(Layer):
    name = "rendered"
    refresh_trigger = "manual"

    def scan(self, ctx: dict) -> tuple[list[dict], list[dict]]:
        repo_root = Path(ctx.get("repo_root", "."))
        persist_dir = Path(ctx.get("persist_dir", repo_root / ".kuberly"))
        verbose = bool(ctx.get("verbose"))
        existing_app_ids: set[str] = set(ctx.get("_existing_app_ids", set()))

        comp_dir = repo_root / "components"
        envs: list[str] = []
        if comp_dir.exists():
            envs = sorted(
                p.name
                for p in comp_dir.iterdir()
                if p.is_dir() and not p.name.startswith(".")
            )

        nodes: list[dict] = []
        edges: list[dict] = []

        for env in envs:
            rendered_path = persist_dir / f"rendered_apps_{env}.json"
            if not rendered_path.exists():
                if verbose:
                    print(
                        f"  [RenderedLayer] skip env={env}: {rendered_path.name} missing"
                    )
                continue
            try:
                payload = json.loads(rendered_path.read_text())
            except Exception as exc:
                if verbose:
                    print(f"  [RenderedLayer] skip env={env}: parse error {exc}")
                continue

            per_app: dict[str, list[tuple[str, str, str, str]]] = defaultdict(list)
            for api_v, kind, ns, name, app_id in walk_rendered_resources(payload):
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
            print(f"  [RenderedLayer] emitted {len(nodes)} nodes / {len(edges)} edges")
        return nodes, edges
