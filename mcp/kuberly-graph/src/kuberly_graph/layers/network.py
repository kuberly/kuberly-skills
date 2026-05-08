"""NetworkLayer — structural extractor over Terraform-state `resource:` nodes.

Runs AFTER `state` and BEFORE `dependency`. Pure read of nodes already in the
GraphStore — no external calls. Empty-store tolerant: returns ``([], [])`` if
no `resource:` nodes have been emitted yet.

Node types emitted (filtered on ``tf_type``):
  * ``vpc``                — ``vpc:<env>/<addr>``
  * ``subnet``             — ``subnet:<env>/<addr>``       (in_vpc → vpc)
  * ``security_group``     — ``security_group:<env>/<addr>`` (in_vpc → vpc)
  * ``nacl``               — ``nacl:<env>/<addr>``         (in_vpc → vpc)
  * ``route_table``        — ``route_table:<env>/<addr>``   (routes_for → subnet,
                                                             routes_via_nat,
                                                             routes_via_igw)
  * ``internet_gateway``   — ``internet_gateway:<env>/<addr>``
  * ``nat_gateway``        — ``nat_gateway:<env>/<addr>``    (lives_in → subnet)
  * ``vpc_endpoint``       — ``vpc_endpoint:<env>/<addr>``   (in_vpc → vpc)
  * ``eip``                — ``eip:<env>/<addr>``
  * ``cidr``               — synthetic ``cidr:<block>`` for SG ingress sources

`aws_security_group_rule` resources are *not* emitted as nodes — they're
encoded as edges between SGs (or SG↔CIDR).

Heuristic: TF state nodes don't include the parsed attributes here (StateLayer
keeps only address + tf_type + provider). NetworkLayer therefore re-reads the
raw state files alongside the resource nodes to recover ``vpc_id`` /
``cidr_block`` / etc. — same data path as StateLayer.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from .base import Layer


_NETWORK_TF_TYPES = {
    "aws_vpc",
    "aws_subnet",
    "aws_security_group",
    "aws_security_group_rule",
    "aws_network_acl",
    "aws_route_table",
    "aws_route_table_association",
    "aws_internet_gateway",
    "aws_nat_gateway",
    "aws_vpc_endpoint",
    "aws_eip",
}


def _safe_load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _iter_state_resources(repo_root: Path, persist_dir: Path):
    """Yield (env, addr, tf_type, attrs) tuples from every state_<env>.json."""
    if not persist_dir.exists():
        return
    for state_path in sorted(persist_dir.glob("state_*.json")):
        env = state_path.stem.replace("state_", "", 1)
        payload = _safe_load_state(state_path)
        modules = payload.get("modules") or {}
        if not isinstance(modules, dict):
            continue
        for _module_path, blob in modules.items():
            for res in (blob or {}).get("resources") or []:
                if not isinstance(res, dict):
                    continue
                addr = res.get("address") or ""
                tf_type = res.get("type") or ""
                if not addr or tf_type not in _NETWORK_TF_TYPES:
                    continue
                # State export uses different shapes depending on producer.
                attrs = (
                    res.get("values")
                    or res.get("attributes")
                    or res.get("instances", [{}])[0].get("attributes", {})
                    if isinstance(res.get("instances"), list)
                    else {}
                )
                if not isinstance(attrs, dict):
                    attrs = {}
                yield env, addr, tf_type, attrs


def _node_id(prefix: str, env: str, addr: str) -> str:
    return f"{prefix}:{env}/{addr}"


def _attr_id(attrs: dict) -> str:
    """Best-effort native AWS id for cross-reference within a state file."""
    return str(attrs.get("id") or "")


class NetworkLayer(Layer):
    name = "network"
    refresh_trigger = "manual"

    def scan(self, ctx: dict) -> tuple[list[dict], list[dict]]:
        verbose = bool(ctx.get("verbose"))
        repo_root = Path(ctx.get("repo_root", "."))
        persist_dir = Path(ctx.get("persist_dir", repo_root / ".kuberly"))

        # Empty-state tolerance: if state files don't exist, log and bail.
        rows = list(_iter_state_resources(repo_root, persist_dir))
        if not rows:
            if verbose:
                print("  [NetworkLayer] no Terraform state — emitting 0 nodes")
            return [], []

        nodes: list[dict] = []
        edges: list[dict] = []
        emitted_ids: set[str] = set()
        # Per-env native-id → node-id index so we can resolve same-state refs
        # like vpc_id / subnet_id / security_group_id without going through TF
        # addresses.
        index_by_env: dict[str, dict[str, dict[str, str]]] = defaultdict(
            lambda: defaultdict(dict)
        )

        def _emit_node(node: dict) -> None:
            if node["id"] in emitted_ids:
                return
            emitted_ids.add(node["id"])
            nodes.append(node)

        def _emit_edge(source: str, target: str, relation: str, **extra) -> None:
            if not source or not target:
                return
            edge = {"source": source, "target": target, "relation": relation}
            edge.update(extra)
            edges.append(edge)

        # ---- pass 1: nodes + per-env id index ---------------------------------
        rule_rows: list[tuple[str, str, dict]] = []
        rt_assoc_rows: list[tuple[str, str, dict]] = []
        for env, addr, tf_type, attrs in rows:
            native_id = _attr_id(attrs)
            if tf_type == "aws_vpc":
                nid = _node_id("vpc", env, addr)
                _emit_node(
                    {
                        "id": nid,
                        "type": "vpc",
                        "label": addr,
                        "env": env,
                        "address": addr,
                        "cidr_block": str(attrs.get("cidr_block") or ""),
                        "tags": attrs.get("tags") or {},
                    }
                )
                if native_id:
                    index_by_env[env]["vpc"][native_id] = nid
            elif tf_type == "aws_subnet":
                nid = _node_id("subnet", env, addr)
                _emit_node(
                    {
                        "id": nid,
                        "type": "subnet",
                        "label": addr,
                        "env": env,
                        "address": addr,
                        "cidr_block": str(attrs.get("cidr_block") or ""),
                        "az": str(attrs.get("availability_zone") or ""),
                        "vpc_id": str(attrs.get("vpc_id") or ""),
                        "tags": attrs.get("tags") or {},
                    }
                )
                if native_id:
                    index_by_env[env]["subnet"][native_id] = nid
            elif tf_type == "aws_security_group":
                nid = _node_id("security_group", env, addr)
                _emit_node(
                    {
                        "id": nid,
                        "type": "security_group",
                        "label": addr,
                        "env": env,
                        "address": addr,
                        "vpc_id": str(attrs.get("vpc_id") or ""),
                        "description": str(attrs.get("description") or ""),
                    }
                )
                if native_id:
                    index_by_env[env]["security_group"][native_id] = nid
            elif tf_type == "aws_network_acl":
                nid = _node_id("nacl", env, addr)
                _emit_node(
                    {
                        "id": nid,
                        "type": "nacl",
                        "label": addr,
                        "env": env,
                        "address": addr,
                        "vpc_id": str(attrs.get("vpc_id") or ""),
                    }
                )
                if native_id:
                    index_by_env[env]["nacl"][native_id] = nid
            elif tf_type == "aws_route_table":
                nid = _node_id("route_table", env, addr)
                routes = attrs.get("route") if isinstance(attrs.get("route"), list) else []
                _emit_node(
                    {
                        "id": nid,
                        "type": "route_table",
                        "label": addr,
                        "env": env,
                        "address": addr,
                        "vpc_id": str(attrs.get("vpc_id") or ""),
                        "route_count": len(routes),
                    }
                )
                if native_id:
                    index_by_env[env]["route_table"][native_id] = nid
            elif tf_type == "aws_internet_gateway":
                nid = _node_id("internet_gateway", env, addr)
                _emit_node(
                    {
                        "id": nid,
                        "type": "internet_gateway",
                        "label": addr,
                        "env": env,
                        "address": addr,
                        "vpc_id": str(attrs.get("vpc_id") or ""),
                    }
                )
                if native_id:
                    index_by_env[env]["internet_gateway"][native_id] = nid
            elif tf_type == "aws_nat_gateway":
                nid = _node_id("nat_gateway", env, addr)
                _emit_node(
                    {
                        "id": nid,
                        "type": "nat_gateway",
                        "label": addr,
                        "env": env,
                        "address": addr,
                        "subnet_id": str(attrs.get("subnet_id") or ""),
                    }
                )
                if native_id:
                    index_by_env[env]["nat_gateway"][native_id] = nid
            elif tf_type == "aws_vpc_endpoint":
                nid = _node_id("vpc_endpoint", env, addr)
                _emit_node(
                    {
                        "id": nid,
                        "type": "vpc_endpoint",
                        "label": addr,
                        "env": env,
                        "address": addr,
                        "service_name": str(attrs.get("service_name") or ""),
                        "vpc_id": str(attrs.get("vpc_id") or ""),
                    }
                )
                if native_id:
                    index_by_env[env]["vpc_endpoint"][native_id] = nid
            elif tf_type == "aws_eip":
                nid = _node_id("eip", env, addr)
                _emit_node(
                    {
                        "id": nid,
                        "type": "eip",
                        "label": addr,
                        "env": env,
                        "address": addr,
                    }
                )
                if native_id:
                    index_by_env[env]["eip"][native_id] = nid
            elif tf_type == "aws_security_group_rule":
                rule_rows.append((env, addr, attrs))
            elif tf_type == "aws_route_table_association":
                rt_assoc_rows.append((env, addr, attrs))

        # ---- pass 2: subnet/sg/nacl/etc. → vpc edges --------------------------
        def _resolve(env: str, kind: str, native_id: str) -> str:
            return index_by_env.get(env, {}).get(kind, {}).get(native_id, "")

        for n in nodes:
            t = n.get("type")
            env = n.get("env", "")
            vpc_native = n.get("vpc_id", "")
            if t in {"subnet", "security_group", "nacl", "route_table", "vpc_endpoint"}:
                if vpc_native:
                    target = _resolve(env, "vpc", vpc_native)
                    if target:
                        _emit_edge(n["id"], target, "in_vpc")
            if t == "nat_gateway":
                subnet_native = n.get("subnet_id", "")
                if subnet_native:
                    target = _resolve(env, "subnet", subnet_native)
                    if target:
                        _emit_edge(n["id"], target, "lives_in")

        # ---- pass 3: SG-rule edges -------------------------------------------
        for env, addr, attrs in rule_rows:
            sg_id = _resolve(env, "security_group", str(attrs.get("security_group_id") or ""))
            if not sg_id:
                continue
            direction = str(attrs.get("type") or "ingress")  # 'ingress' | 'egress'
            protocol = str(attrs.get("protocol") or "-1")
            from_port = attrs.get("from_port")
            to_port = attrs.get("to_port")
            port_range = f"{from_port}-{to_port}" if from_port is not None else ""
            relation = (
                "allows_ingress_from" if direction == "ingress" else "allows_egress_to"
            )
            ref_sg = str(attrs.get("source_security_group_id") or "")
            cidrs = attrs.get("cidr_blocks")
            ipv6_cidrs = attrs.get("ipv6_cidr_blocks")
            if ref_sg:
                target = _resolve(env, "security_group", ref_sg)
                if target:
                    _emit_edge(
                        sg_id,
                        target,
                        relation,
                        protocol=protocol,
                        port_range=port_range,
                        rule_address=addr,
                    )
            if isinstance(cidrs, list):
                for block in cidrs:
                    if not block:
                        continue
                    cidr_node = f"cidr:{block}"
                    _emit_node(
                        {
                            "id": cidr_node,
                            "type": "cidr",
                            "label": block,
                            "block": block,
                            "family": "ipv4",
                        }
                    )
                    cidr_relation = (
                        "allows_ingress_from_cidr"
                        if direction == "ingress"
                        else "allows_egress_to_cidr"
                    )
                    _emit_edge(
                        sg_id,
                        cidr_node,
                        cidr_relation,
                        protocol=protocol,
                        port_range=port_range,
                        rule_address=addr,
                    )
            if isinstance(ipv6_cidrs, list):
                for block in ipv6_cidrs:
                    if not block:
                        continue
                    cidr_node = f"cidr:{block}"
                    _emit_node(
                        {
                            "id": cidr_node,
                            "type": "cidr",
                            "label": block,
                            "block": block,
                            "family": "ipv6",
                        }
                    )
                    cidr_relation = (
                        "allows_ingress_from_cidr"
                        if direction == "ingress"
                        else "allows_egress_to_cidr"
                    )
                    _emit_edge(
                        sg_id,
                        cidr_node,
                        cidr_relation,
                        protocol=protocol,
                        port_range=port_range,
                        rule_address=addr,
                    )

        # ---- pass 4: route_table → subnet (associations) + IGW/NAT -----------
        for env, _addr, attrs in rt_assoc_rows:
            rt = _resolve(env, "route_table", str(attrs.get("route_table_id") or ""))
            subnet = _resolve(env, "subnet", str(attrs.get("subnet_id") or ""))
            if rt and subnet:
                _emit_edge(rt, subnet, "routes_for")

        # Walk the route table's inline routes (already counted in pass 1) for
        # NAT / IGW destinations.
        for env, addr, tf_type, attrs in rows:
            if tf_type != "aws_route_table":
                continue
            rt_id = _node_id("route_table", env, addr)
            for route in (attrs.get("route") or []) if isinstance(attrs.get("route"), list) else []:
                if not isinstance(route, dict):
                    continue
                nat_native = str(route.get("nat_gateway_id") or "")
                if nat_native:
                    target = _resolve(env, "nat_gateway", nat_native)
                    if target:
                        _emit_edge(rt_id, target, "routes_via_nat")
                gw_native = str(route.get("gateway_id") or "")
                if gw_native:
                    target = _resolve(env, "internet_gateway", gw_native)
                    if target:
                        _emit_edge(rt_id, target, "routes_via_igw")

        if verbose:
            print(f"  [NetworkLayer] emitted {len(nodes)} nodes / {len(edges)} edges")
        return nodes, edges
