"""Shared cold-scan helpers — port of the legacy `KuberlyGraph` class.

Used by ColdLayer / CodeLayer / ComponentsLayer / ApplicationsLayer to walk
the on-disk Terragrunt monorepo. Behaviour is byte-for-byte identical with
the legacy script (same node ids, edge relations, drift output).
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path


def parse_hcl_dependencies(hcl_path: Path) -> list[str]:
    deps: list[str] = []
    try:
        text = hcl_path.read_text()
        for m in re.finditer(r'dependency\s+"([^"]+)"', text):
            deps.append(m.group(1))
    except Exception:
        pass
    return deps


def parse_hcl_component_refs(hcl_path: Path) -> list[str]:
    refs: list[str] = []
    try:
        text = hcl_path.read_text()
        for m in re.finditer(r"include\.root\.locals\.components\.(\w+)", text):
            refs.append(m.group(1))
        for m in re.finditer(r'components\["([^"]+)"\]', text):
            refs.append(m.group(1))
    except Exception:
        pass
    return list(set(refs))


def load_json_safe(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


class KuberlyGraph:
    """In-memory cold graph builder. Mirrors the legacy script's class."""

    def __init__(self, repo_root: str) -> None:
        self.repo = Path(repo_root)
        self.nodes: dict[str, dict] = {}
        self.edges: list[dict] = []

    def add_node(self, nid: str, **attrs) -> None:
        self.nodes[nid] = {**attrs, "id": nid}

    def add_edge(self, src: str, dst: str, **attrs) -> None:
        self.edges.append({"source": src, "target": dst, **attrs})

    # -- scanners -----------------------------------------------------------

    def scan_environments(self) -> None:
        comp_dir = self.repo / "components"
        if not comp_dir.exists():
            return
        for env_dir in sorted(comp_dir.iterdir()):
            if not env_dir.is_dir():
                continue
            env_name = env_dir.name
            self.add_node(f"env:{env_name}", type="environment", label=env_name)
            for jf in sorted(env_dir.glob("*.json")):
                comp_name = jf.stem
                nid = f"component:{env_name}/{comp_name}"
                data = load_json_safe(jf)
                is_shared = comp_name == "shared-infra"
                meta: dict = {}
                if is_shared and data:
                    si = data.get("shared-infra", {})
                    target = si.get("target", {})
                    meta = {
                        "account_id": target.get("account_id", ""),
                        "region": target.get("region", ""),
                        "cluster_name": target.get("cluster", {}).get("name", ""),
                        "env_label": si.get("env", ""),
                    }
                self.add_node(
                    nid,
                    type="shared-infra" if is_shared else "component",
                    label=comp_name,
                    environment=env_name,
                    **meta,
                )
                self.add_edge(f"env:{env_name}", nid, relation="contains")
                if is_shared:
                    for other in env_dir.glob("*.json"):
                        if other.stem != "shared-infra":
                            self.add_edge(
                                nid,
                                f"component:{env_name}/{other.stem}",
                                relation="configures",
                            )

    def scan_applications(self) -> None:
        app_dir = self.repo / "applications"
        if not app_dir.exists():
            return
        for env_dir in sorted(app_dir.iterdir()):
            if not env_dir.is_dir():
                continue
            env_name = env_dir.name
            env_nid = f"env:{env_name}"
            if env_nid not in self.nodes:
                self.add_node(env_nid, type="environment", label=env_name)
            for jf in sorted(env_dir.glob("*.json")):
                app_name = jf.stem
                nid = f"app:{env_name}/{app_name}"
                data = load_json_safe(jf)
                meta: dict = {}
                if data:
                    dep = data.get("deployment", {})
                    meta["port"] = dep.get("port")
                    meta["replicas"] = dep.get("replicas")
                    container = dep.get("container", {})
                    img = container.get("image", {})
                    meta["image"] = img.get("repository", "")
                    env_block = container.get("env", {})
                    meta["secret_count"] = len(env_block.get("secrets", []))
                    meta["env_var_count"] = len(env_block.get("env_vars", {}))
                self.add_node(
                    nid,
                    type="application",
                    label=app_name,
                    environment=env_name,
                    **meta,
                )
                self.add_edge(env_nid, nid, relation="deploys")

    def scan_modules(self) -> None:
        clouds_dir = self.repo / "clouds"
        if not clouds_dir.exists():
            return
        for provider_dir in sorted(clouds_dir.iterdir()):
            if not provider_dir.is_dir() or provider_dir.name.startswith("."):
                continue
            provider = provider_dir.name
            modules_dir = provider_dir / "modules"
            if not modules_dir.exists():
                continue
            provider_nid = f"cloud:{provider}"
            self.add_node(provider_nid, type="cloud_provider", label=provider)
            for mod_dir in sorted(modules_dir.iterdir()):
                if not mod_dir.is_dir() or mod_dir.name.startswith("."):
                    continue
                mod_name = mod_dir.name
                nid = f"module:{provider}/{mod_name}"
                meta: dict = {}
                kj = mod_dir / "kuberly.json"
                if kj.exists():
                    kdata = load_json_safe(kj)
                    if kdata:
                        meta["description"] = kdata.get("description", "")
                        meta["version"] = kdata.get("version", "")
                        meta["types"] = kdata.get("types", [])
                        meta["author"] = kdata.get("author", "")
                self.add_node(
                    nid,
                    type="module",
                    label=mod_name,
                    provider=provider,
                    path=str(mod_dir.relative_to(self.repo)),
                    **meta,
                )
                self.add_edge(provider_nid, nid, relation="provides")
                tg = mod_dir / "terragrunt.hcl"
                if tg.exists():
                    for dep in parse_hcl_dependencies(tg):
                        dep_nid = f"module:{provider}/{dep}"
                        self.add_edge(nid, dep_nid, relation="depends_on")
                    for comp_ref in parse_hcl_component_refs(tg):
                        self.add_edge(
                            nid,
                            f"component_type:{comp_ref}",
                            relation="reads_config",
                        )

    def scan_catalog(self) -> None:
        catalog_path = self.repo / "catalog" / "modules.json"
        if not catalog_path.exists():
            return
        data = load_json_safe(catalog_path)
        if not data:
            return
        for mod in data.get("modules", []):
            name = mod.get("name", "")
            for nid, node in self.nodes.items():
                if node.get("type") == "module" and node.get("label") == name:
                    node["resource_count"] = mod.get("resource_count", 0)
                    node["providers"] = mod.get("providers", [])
                    node["has_readme"] = mod.get("has_readme", False)
                    node["state_key"] = mod.get("state_key", "")
                    break

    def link_components_to_modules(self) -> None:
        module_names = {
            n["label"] for n in self.nodes.values() if n["type"] == "module"
        }
        for nid, node in list(self.nodes.items()):
            if node["type"] == "component":
                comp_name = node["label"]
                normalized = comp_name.replace("-", "_")
                if normalized in module_names:
                    for provider_mod_nid, pnode in self.nodes.items():
                        if (
                            pnode.get("type") == "module"
                            and pnode["label"] == normalized
                        ):
                            self.add_edge(
                                nid,
                                provider_mod_nid,
                                relation="configures_module",
                            )
                for mname in module_names:
                    if mname in normalized or normalized in mname:
                        if mname != normalized:
                            for provider_mod_nid, pnode in self.nodes.items():
                                if (
                                    pnode.get("type") == "module"
                                    and pnode["label"] == mname
                                ):
                                    self.add_edge(
                                        nid,
                                        provider_mod_nid,
                                        relation="configures_module",
                                    )

    # -- analytics ----------------------------------------------------------

    def cross_env_drift(self) -> dict:
        env_components = defaultdict(set)
        env_apps = defaultdict(set)
        for _nid, node in self.nodes.items():
            if node["type"] == "component":
                env_components[node["environment"]].add(node["label"])
            elif node["type"] == "application":
                env_apps[node["environment"]].add(node["label"])
        all_comps = (
            set().union(*env_components.values()) if env_components else set()
        )
        all_apps = set().union(*env_apps.values()) if env_apps else set()
        drift = {"components": {}, "applications": {}}
        for env in env_components:
            missing = all_comps - env_components[env]
            if missing:
                drift["components"][env] = sorted(missing)
        for env in env_apps:
            missing = all_apps - env_apps[env]
            if missing:
                drift["applications"][env] = sorted(missing)
        return drift

    def compute_stats(self) -> dict:
        in_deg: dict[str, int] = defaultdict(int)
        out_deg: dict[str, int] = defaultdict(int)
        for e in self.edges:
            out_deg[e["source"]] += 1
            in_deg[e["target"]] += 1
        all_nodes_deg = [
            (nid, in_deg.get(nid, 0), out_deg.get(nid, 0)) for nid in self.nodes
        ]
        critical = sorted(all_nodes_deg, key=lambda x: x[1], reverse=True)[:10]
        module_deps: dict[str, list[str]] = defaultdict(list)
        for e in self.edges:
            if e.get("relation") == "depends_on":
                module_deps[e["source"]].append(e["target"])

        def longest_chain(node, visited=None):
            if visited is None:
                visited = set()
            if node in visited:
                return [node + " (CYCLE)"]
            visited.add(node)
            best = [node]
            for dep in module_deps.get(node, []):
                chain = [node] + longest_chain(dep, visited.copy())
                if len(chain) > len(best):
                    best = chain
            return best

        chains: list[list[str]] = []
        for nid in self.nodes:
            if nid.startswith("module:") and module_deps.get(nid):
                chain = longest_chain(nid)
                if len(chain) > 1:
                    chains.append(chain)
        chains.sort(key=len, reverse=True)
        type_counts: dict[str, int] = defaultdict(int)
        for n in self.nodes.values():
            type_counts[n["type"]] += 1
        return {
            "node_count": len(self.nodes),
            "edge_count": len(self.edges),
            "type_counts": dict(type_counts),
            "critical_nodes": [
                (nid, ind, outd) for nid, ind, outd in critical
            ],
            "longest_chains": chains[:5],
        }

    # -- driver -------------------------------------------------------------

    def build(self) -> None:
        self.scan_environments()
        self.scan_applications()
        self.scan_modules()
        self.scan_catalog()
        self.link_components_to_modules()


# ---------------------------------------------------------------------------
# Rendered-manifest walker — used by RenderedLayer.
# ---------------------------------------------------------------------------


def walk_rendered_resources(payload):
    def _is_k8s_obj(o):
        return (
            isinstance(o, dict)
            and isinstance(o.get("kind"), str)
            and isinstance(o.get("apiVersion"), str)
        )

    def _emit(obj, app_id):
        meta = obj.get("metadata") or {}
        name = meta.get("name")
        ns = meta.get("namespace") or ""
        if not name:
            return
        yield (obj["apiVersion"], obj["kind"], ns, name, app_id)

    def _walk_app(app_name, node):
        if isinstance(node, list):
            for o in node:
                if _is_k8s_obj(o):
                    yield from _emit(o, app_name)
            return
        if isinstance(node, dict):
            for key in ("manifests", "resources", "objects"):
                inner = node.get(key)
                if isinstance(inner, list):
                    for o in inner:
                        if _is_k8s_obj(o):
                            yield from _emit(o, app_name)
            for v in node.values():
                if _is_k8s_obj(v):
                    yield from _emit(v, app_name)
                elif isinstance(v, list):
                    for o in v:
                        if _is_k8s_obj(o):
                            yield from _emit(o, app_name)

    if isinstance(payload, dict):
        apps = (
            payload.get("apps")
            if isinstance(payload.get("apps"), dict)
            else payload
        )
        for app_name, node in apps.items():
            yield from _walk_app(str(app_name), node)
    elif isinstance(payload, list):
        for o in payload:
            if isinstance(o, dict) and o.get("kind"):
                meta = o.get("metadata") or {}
                name = meta.get("name")
                if name:
                    yield (
                        o.get("apiVersion") or "",
                        o["kind"],
                        meta.get("namespace") or "",
                        name,
                        "_root",
                    )
