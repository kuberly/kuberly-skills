"""DnsLayer — extract Route53 zones / records + ACM certificates from
Terraform state, then wire them to k8s Ingress / LoadBalancer Services.

Runs AFTER ``state`` and ``k8s``, BEFORE ``dependency``. Pure structural read
of nodes already in the GraphStore + raw state files. No live AWS calls.
Empty-store tolerant: returns ``([], [])`` when no relevant resources exist.

Nodes:
  * ``dns_zone:<env>/<zone-name>``
  * ``dns_record:<env>/<zone>/<name>/<type>``
  * ``acm_cert:<env>/<arn-or-addr>``

Edges:
  * dns_record → dns_zone                  (``in_zone``)
  * dns_record → k8s_resource(Ingress|Svc) (``points_to``) — best-effort match
                                              of the alias hostname against
                                              ingress / Service LB hostnames.
  * acm_cert   → dns_zone                  (``validated_by``) — DNS validation.
  * k8s_resource(Ingress) → acm_cert       (``secured_by``) — from common
                                              annotations:
                                              ``alb.ingress.kubernetes.io/certificate-arn``
                                              and the ingress.tls hostnames.
"""

from __future__ import annotations

import json
from pathlib import Path

from .base import Layer


_DNS_TF_TYPES = {
    "aws_route53_zone",
    "aws_route53_record",
    "aws_acm_certificate",
}


def _safe_load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _iter_state_resources(persist_dir: Path):
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
                if not addr or tf_type not in _DNS_TF_TYPES:
                    continue
                attrs = res.get("values") or res.get("attributes") or {}
                if not isinstance(attrs, dict):
                    if isinstance(res.get("instances"), list) and res["instances"]:
                        attrs = res["instances"][0].get("attributes") or {}
                    else:
                        attrs = {}
                yield env, addr, tf_type, attrs


def _alias_hostname(attrs: dict) -> str:
    """Extract the alias / record value hostname for ``points_to`` matching."""
    alias = attrs.get("alias")
    if isinstance(alias, list) and alias:
        first = alias[0] if isinstance(alias[0], dict) else {}
        name = str(first.get("name") or "")
        if name:
            return name.rstrip(".")
    if isinstance(alias, dict):
        name = str(alias.get("name") or "")
        if name:
            return name.rstrip(".")
    records = attrs.get("records") or []
    if isinstance(records, list) and records:
        first = records[0]
        if isinstance(first, str):
            return first.rstrip(".")
    return ""


def _ingress_lb_hostnames(node: dict) -> list[str]:
    """Best-effort: pull LB hostnames off Ingress / Service nodes.

    K8sLayer doesn't materialize ``status`` today, so this returns whatever the
    annotations expose (``external-dns.alpha.kubernetes.io/hostname`` and the
    ALB controller's ``hostnames``). Empty list is fine — points_to just won't
    match for that ingress.
    """
    hostnames: list[str] = []
    anns = node.get("annotations") if isinstance(node.get("annotations"), dict) else {}
    for key in (
        "external-dns.alpha.kubernetes.io/hostname",
        "alb.ingress.kubernetes.io/hostname",
    ):
        v = anns.get(key)
        if isinstance(v, str):
            hostnames.extend(s.strip().rstrip(".") for s in v.split(",") if s.strip())
    return hostnames


def _ingress_cert_arns(node: dict) -> list[str]:
    anns = node.get("annotations") if isinstance(node.get("annotations"), dict) else {}
    raw = anns.get("alb.ingress.kubernetes.io/certificate-arn")
    if not isinstance(raw, str):
        return []
    return [s.strip() for s in raw.split(",") if s.strip()]


class DnsLayer(Layer):
    name = "dns"
    refresh_trigger = "manual"

    def scan(self, ctx: dict) -> tuple[list[dict], list[dict]]:
        verbose = bool(ctx.get("verbose"))
        repo_root = Path(ctx.get("repo_root", "."))
        persist_dir = Path(ctx.get("persist_dir", repo_root / ".kuberly"))
        store = ctx.get("graph_store")
        if store is None:
            from ..store import open_store

            store = open_store(persist_dir)

        rows = list(_iter_state_resources(persist_dir))

        nodes: list[dict] = []
        edges: list[dict] = []
        emitted: set[str] = set()

        def _emit_node(node: dict) -> None:
            if node["id"] in emitted:
                return
            emitted.add(node["id"])
            nodes.append(node)

        def _emit_edge(source: str, target: str, relation: str, **extra) -> None:
            if not source or not target:
                return
            edge = {"source": source, "target": target, "relation": relation}
            edge.update(extra)
            edges.append(edge)

        # zone native id (Z…) → node id; zone name → node id.
        zone_by_native: dict[tuple[str, str], str] = {}
        zone_by_name: dict[tuple[str, str], str] = {}
        cert_by_arn: dict[str, str] = {}

        # Pass 1: emit zone + cert nodes.
        for env, addr, tf_type, attrs in rows:
            if tf_type == "aws_route53_zone":
                zone_name = str(attrs.get("name") or "").rstrip(".")
                native = str(attrs.get("zone_id") or attrs.get("id") or "")
                nid = f"dns_zone:{env}/{zone_name or addr}"
                _emit_node(
                    {
                        "id": nid,
                        "type": "dns_zone",
                        "label": zone_name or addr,
                        "env": env,
                        "address": addr,
                        "zone_name": zone_name,
                        "private": bool(
                            (attrs.get("vpc") or attrs.get("vpcs"))
                            if isinstance(attrs.get("vpc"), list)
                            or isinstance(attrs.get("vpcs"), list)
                            else False
                        ),
                        "vpc_associations": (
                            attrs.get("vpc") if isinstance(attrs.get("vpc"), list) else []
                        ),
                        "native_id": native,
                    }
                )
                if native:
                    zone_by_native[(env, native)] = nid
                if zone_name:
                    zone_by_name[(env, zone_name)] = nid
            elif tf_type == "aws_acm_certificate":
                arn = str(attrs.get("arn") or "")
                domain = str(attrs.get("domain_name") or "")
                key = arn or addr
                nid = f"acm_cert:{env}/{key}"
                sans = attrs.get("subject_alternative_names")
                if not isinstance(sans, list):
                    sans = []
                _emit_node(
                    {
                        "id": nid,
                        "type": "acm_cert",
                        "label": domain or addr,
                        "env": env,
                        "address": addr,
                        "arn": arn,
                        "domain_name": domain,
                        "sans": [str(x) for x in sans if isinstance(x, str)],
                        "validation_method": str(
                            attrs.get("validation_method") or ""
                        ),
                    }
                )
                if arn:
                    cert_by_arn[arn] = nid

        # Pass 2: records → zone + record value hostnames index.
        record_hostnames: dict[str, list[tuple[str, str]]] = {}
        for env, addr, tf_type, attrs in rows:
            if tf_type != "aws_route53_record":
                continue
            zone_native = str(attrs.get("zone_id") or "")
            rec_name = str(attrs.get("name") or "").rstrip(".")
            rec_type = str(attrs.get("type") or "")
            ttl = int(attrs.get("ttl") or 0)
            rid = f"dns_record:{env}/{zone_native or 'zone'}/{rec_name}/{rec_type}"
            target_host = _alias_hostname(attrs)
            _emit_node(
                {
                    "id": rid,
                    "type": "dns_record",
                    "label": f"{rec_name} {rec_type}",
                    "env": env,
                    "address": addr,
                    "name": rec_name,
                    "record_type": rec_type,
                    "ttl": ttl,
                    "alias_target": target_host,
                    "zone_native_id": zone_native,
                }
            )
            zone_id = zone_by_native.get((env, zone_native), "")
            if zone_id:
                _emit_edge(rid, zone_id, "in_zone")
            if target_host:
                record_hostnames.setdefault(target_host.lower(), []).append((rid, env))

        # Pass 3: dns_record → ingress/service via alias hostname.
        try:
            k8s_nodes = store.all_nodes(layer="k8s")
        except Exception:
            k8s_nodes = []

        ingresses_by_host: dict[str, list[dict]] = {}
        services_lb: list[dict] = []
        ingresses: list[dict] = []
        for n in k8s_nodes:
            kind = n.get("kind")
            if kind == "Ingress":
                ingresses.append(n)
                for h in _ingress_lb_hostnames(n):
                    ingresses_by_host.setdefault(h.lower(), []).append(n)
            elif kind == "Service":
                services_lb.append(n)

        for host, rec_pairs in record_hostnames.items():
            matched = ingresses_by_host.get(host, [])
            for rid, _env in rec_pairs:
                for ing in matched:
                    _emit_edge(rid, ing["id"], "points_to", hostname=host)

        # Pass 4: acm_cert → dns_zone (validated_by) when DNS validation.
        for n in nodes:
            if n.get("type") != "acm_cert":
                continue
            if n.get("validation_method") != "DNS":
                continue
            domain = str(n.get("domain_name") or "")
            env = str(n.get("env") or "")
            if not domain:
                continue
            # Match the longest zone name suffix.
            best = ""
            for (zone_env, zone_name), zid in zone_by_name.items():
                if zone_env != env or not zone_name:
                    continue
                if domain == zone_name or domain.endswith("." + zone_name):
                    if len(zone_name) > len(best):
                        best = zone_name
            if best:
                target = zone_by_name.get((env, best))
                if target:
                    _emit_edge(n["id"], target, "validated_by")

        # Pass 5: k8s_resource(Ingress) → acm_cert via annotation.
        for ing in ingresses:
            for arn in _ingress_cert_arns(ing):
                cert_id = cert_by_arn.get(arn)
                if cert_id:
                    _emit_edge(ing["id"], cert_id, "secured_by", arn=arn)

        if verbose:
            print(
                f"  [DnsLayer] emitted {len(nodes)} nodes / {len(edges)} edges"
            )
        return nodes, edges
