"""StateLayer — Terraform/OpenTofu state extracts in `.kuberly/state_<env>.json`."""

from __future__ import annotations

import json
from pathlib import Path

from .base import Layer


class StateLayer(Layer):
    name = "state"
    refresh_trigger = "manual"

    def scan(self, ctx: dict) -> tuple[list[dict], list[dict]]:
        repo_root = Path(ctx.get("repo_root", "."))
        persist_dir = Path(ctx.get("persist_dir", repo_root / ".kuberly"))
        verbose = bool(ctx.get("verbose"))
        existing_module_ids: set[str] = set(ctx.get("_existing_module_ids", set()))

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
            state_path = persist_dir / f"state_{env}.json"
            if not state_path.exists():
                if verbose:
                    print(f"  [StateLayer] skip env={env}: {state_path.name} missing")
                continue
            try:
                payload = json.loads(state_path.read_text())
            except Exception as exc:
                if verbose:
                    print(f"  [StateLayer] skip env={env}: parse error {exc}")
                continue

            modules = payload.get("modules") or {}
            if not isinstance(modules, dict):
                continue

            for module_path, module_blob in modules.items():
                resources = (module_blob or {}).get("resources") or []
                module_id = None
                segs = str(module_path).strip("/").split("/")
                if len(segs) >= 4 and segs[0] == "clouds" and segs[2] == "modules":
                    candidate = f"module:{segs[1]}/{segs[3]}"
                    if not existing_module_ids or candidate in existing_module_ids:
                        module_id = candidate
                for res in resources:
                    if not isinstance(res, dict):
                        continue
                    addr = res.get("address") or ""
                    if not addr:
                        continue
                    rid = f"resource:{env}/{addr}"
                    nodes.append(
                        {
                            "id": rid,
                            "type": "resource",
                            "label": addr,
                            "env": env,
                            "address": addr,
                            "tf_type": res.get("type", ""),
                            "provider": res.get("provider", ""),
                            "mode": res.get("mode", ""),
                            "module_path": str(module_path),
                        }
                    )
                    if module_id:
                        edges.append(
                            {
                                "source": module_id,
                                "target": rid,
                                "relation": "state_owns",
                            }
                        )

        if verbose:
            print(f"  [StateLayer] emitted {len(nodes)} nodes / {len(edges)} edges")
        return nodes, edges
