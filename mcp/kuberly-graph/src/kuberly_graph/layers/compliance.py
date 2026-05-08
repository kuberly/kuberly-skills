"""ComplianceLayer — derived violation scan over state + k8s nodes.

Runs AFTER ``state``, ``k8s``, ``iam``, ``network``, BEFORE ``dependency``.
Pure derived layer — no live API calls. Empty-store tolerant.

Compliance rules (v1, hardcoded):
  * R001 — ``aws_s3_bucket`` without ``server_side_encryption_configuration``.
  * R002 — ``aws_ebs_volume`` with ``encrypted = false`` (or unset).
  * R003 — ``resource:`` missing required tags. Configurable via ctx flag
           ``compliance_required_tags`` (default ``["Owner", "Environment", "Cost-Center"]``).
  * R004 — ``aws_security_group`` with ingress 0.0.0.0/0 on a port other
           than 80/443.
  * R005 — ``aws_iam_role`` with ``assume_role_policy.statement[].principal = "*"``.
  * R006 — k8s Pod / Deployment / StatefulSet without resource limits.
  * R007 — k8s Service of type LoadBalancer with no ingress controller.

Nodes:
  * ``compliance_violation:<rule-id>/<resource-id>``

Edges:
  * compliance_violation → <resource>     (``violated_by``)
"""

from __future__ import annotations

import json
from pathlib import Path

from .base import Layer


_DEFAULT_REQUIRED_TAGS = ("Owner", "Environment", "Cost-Center")
_RULE_SEVERITY = {
    "R001": "HIGH",
    "R002": "HIGH",
    "R003": "MEDIUM",
    "R004": "HIGH",
    "R005": "HIGH",
    "R006": "MEDIUM",
    "R007": "LOW",
}
_RULE_DESC = {
    "R001": "S3 bucket without server-side encryption configuration",
    "R002": "EBS volume not encrypted",
    "R003": "AWS resource missing required tags",
    "R004": "Security group allows 0.0.0.0/0 on a non-web port",
    "R005": "IAM role assume policy uses wildcard principal",
    "R006": "Workload pod template lacks resource limits",
    "R007": "LoadBalancer Service exposed without ingress controller",
}
_RULE_RECO = {
    "R001": "Add aws_s3_bucket_server_side_encryption_configuration",
    "R002": "Set encrypted = true on the EBS volume",
    "R003": "Add the required tags to the resource block",
    "R004": "Restrict CIDR or use 80/443 only",
    "R005": "Replace `Principal: \"*\"` with a specific principal ARN",
    "R006": "Add resources.limits.cpu / memory to every container",
    "R007": "Wrap the service behind an ingress controller",
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
                if not addr:
                    continue
                attrs = res.get("values") or res.get("attributes") or {}
                if not isinstance(attrs, dict):
                    if isinstance(res.get("instances"), list) and res["instances"]:
                        attrs = res["instances"][0].get("attributes") or {}
                    else:
                        attrs = {}
                yield env, addr, tf_type, attrs


def _has_resource_limits(spec: dict) -> bool:
    """True iff every container in the pod template declares resources.limits."""
    if not isinstance(spec, dict):
        return False
    pod_spec = spec
    template = spec.get("template")
    if isinstance(template, dict) and isinstance(template.get("spec"), dict):
        pod_spec = template["spec"]
    containers = pod_spec.get("containers")
    if not isinstance(containers, list) or not containers:
        return False
    for c in containers:
        if not isinstance(c, dict):
            return False
        res = c.get("resources")
        if not isinstance(res, dict):
            return False
        limits = res.get("limits")
        if not isinstance(limits, dict) or not limits:
            return False
    return True


class ComplianceLayer(Layer):
    name = "compliance"
    refresh_trigger = "manual"

    def scan(self, ctx: dict) -> tuple[list[dict], list[dict]]:
        verbose = bool(ctx.get("verbose"))
        repo_root = Path(ctx.get("repo_root", "."))
        persist_dir = Path(ctx.get("persist_dir", repo_root / ".kuberly"))
        store = ctx.get("graph_store")
        if store is None:
            from ..store import open_store

            store = open_store(persist_dir)

        required_tags = tuple(
            ctx.get("compliance_required_tags") or _DEFAULT_REQUIRED_TAGS
        )

        violations: list[dict] = []
        edges: list[dict] = []

        def _add(rule_id: str, resource_node_id: str, **extra) -> None:
            vid = f"compliance_violation:{rule_id}/{resource_node_id}"
            base = {
                "id": vid,
                "type": "compliance_violation",
                "label": f"{rule_id}: {extra.get('summary', _RULE_DESC[rule_id])}",
                "rule_id": rule_id,
                "severity": _RULE_SEVERITY[rule_id],
                "description": _RULE_DESC[rule_id],
                "recommendation": _RULE_RECO[rule_id],
                "resource_id": resource_node_id,
            }
            base.update(extra)
            violations.append(base)
            edges.append(
                {"source": vid, "target": resource_node_id, "relation": "violated_by"}
            )

        # --- state-side rules (R001..R005) -----------------------------------
        rows = list(_iter_state_resources(persist_dir))
        for env, addr, tf_type, attrs in rows:
            resource_node_id = f"resource:{env}/{addr}"

            # R001 — S3 bucket missing encryption.
            if tf_type == "aws_s3_bucket":
                sse = attrs.get("server_side_encryption_configuration")
                if not sse:
                    _add(
                        "R001",
                        resource_node_id,
                        resource_type=tf_type,
                        env=env,
                        address=addr,
                    )

            # R002 — EBS unencrypted.
            if tf_type == "aws_ebs_volume":
                encrypted = attrs.get("encrypted")
                if encrypted is False or encrypted is None:
                    _add(
                        "R002",
                        resource_node_id,
                        resource_type=tf_type,
                        env=env,
                        address=addr,
                    )

            # R003 — missing required tags. Skip non-AWS / aux resources.
            if tf_type and tf_type.startswith("aws_") and "tags" in attrs:
                tags = attrs.get("tags")
                if isinstance(tags, dict):
                    missing = [t for t in required_tags if t not in tags]
                    if missing:
                        _add(
                            "R003",
                            resource_node_id,
                            resource_type=tf_type,
                            env=env,
                            address=addr,
                            missing_tags=missing,
                        )

            # R004 — wide-open SG ingress on a non-web port.
            if tf_type == "aws_security_group_rule":
                direction = str(attrs.get("type") or "")
                if direction != "ingress":
                    continue
                cidrs = attrs.get("cidr_blocks")
                if not isinstance(cidrs, list):
                    continue
                from_port = attrs.get("from_port")
                to_port = attrs.get("to_port")
                if "0.0.0.0/0" in cidrs and (
                    from_port not in (80, 443) or to_port not in (80, 443)
                ):
                    _add(
                        "R004",
                        resource_node_id,
                        resource_type=tf_type,
                        env=env,
                        address=addr,
                        from_port=from_port,
                        to_port=to_port,
                    )

            # R005 — IAM role wildcard principal.
            if tf_type == "aws_iam_role":
                arp_raw = attrs.get("assume_role_policy")
                doc = None
                if isinstance(arp_raw, str):
                    try:
                        doc = json.loads(arp_raw)
                    except Exception:
                        doc = None
                elif isinstance(arp_raw, dict):
                    doc = arp_raw
                if isinstance(doc, dict):
                    statements = doc.get("Statement") or []
                    if isinstance(statements, dict):
                        statements = [statements]
                    if isinstance(statements, list):
                        for stmt in statements:
                            if not isinstance(stmt, dict):
                                continue
                            principal = stmt.get("Principal")
                            if principal == "*":
                                _add(
                                    "R005",
                                    resource_node_id,
                                    resource_type=tf_type,
                                    env=env,
                                    address=addr,
                                )
                                break
                            if isinstance(principal, dict):
                                vals = []
                                for v in principal.values():
                                    if isinstance(v, list):
                                        vals.extend(v)
                                    else:
                                        vals.append(v)
                                if any(v == "*" for v in vals):
                                    _add(
                                        "R005",
                                        resource_node_id,
                                        resource_type=tf_type,
                                        env=env,
                                        address=addr,
                                    )
                                    break

        # --- k8s-side rules (R006, R007) -------------------------------------
        try:
            k8s_nodes = store.all_nodes(layer="k8s")
        except Exception:
            k8s_nodes = []

        for n in k8s_nodes:
            kind = n.get("kind")
            # R006 — pod template without resource limits. K8sLayer doesn't
            # persist full container specs by default; we use a best-effort
            # check on the spec field if present, otherwise mark as a soft
            # violation only when the node actually carries a `spec` dict.
            if kind in {"Deployment", "StatefulSet", "DaemonSet", "Pod"}:
                spec = n.get("spec") if isinstance(n.get("spec"), dict) else None
                if spec is not None and not _has_resource_limits(spec):
                    _add(
                        "R006",
                        n["id"],
                        resource_type=f"k8s/{kind}",
                        namespace=n.get("namespace", ""),
                        name=n.get("name", ""),
                    )

            # R007 — Service.type=LoadBalancer with no Ingress also exposed.
            if kind == "Service":
                spec = n.get("spec") if isinstance(n.get("spec"), dict) else {}
                svc_type = str(spec.get("type") or "")
                if svc_type == "LoadBalancer":
                    # cheap heuristic: ingress in same ns referencing this svc.
                    ns = str(n.get("namespace") or "")
                    name = str(n.get("name") or "")
                    has_ingress = False
                    for ing in k8s_nodes:
                        if ing.get("kind") != "Ingress":
                            continue
                        if str(ing.get("namespace") or "") != ns:
                            continue
                        ing_spec = ing.get("spec") if isinstance(ing.get("spec"), dict) else {}
                        rules = ing_spec.get("rules") or []
                        if not isinstance(rules, list):
                            continue
                        for r in rules:
                            http = r.get("http") if isinstance(r, dict) else None
                            paths = http.get("paths") if isinstance(http, dict) else None
                            if not isinstance(paths, list):
                                continue
                            for p in paths:
                                back = p.get("backend") if isinstance(p, dict) else {}
                                svc = (back or {}).get("service") if isinstance(back, dict) else None
                                if isinstance(svc, dict) and svc.get("name") == name:
                                    has_ingress = True
                                    break
                            if has_ingress:
                                break
                        if has_ingress:
                            break
                    if not has_ingress:
                        _add(
                            "R007",
                            n["id"],
                            resource_type=f"k8s/{kind}",
                            namespace=ns,
                            name=name,
                        )

        if verbose:
            print(
                f"  [ComplianceLayer] emitted {len(violations)} nodes / {len(edges)} edges"
            )
        return violations, edges
