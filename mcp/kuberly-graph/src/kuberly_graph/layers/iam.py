"""IAMLayer — structural extractor over Terraform-state IAM resources plus
k8s ServiceAccount IRSA annotations.

Runs AFTER `state` and `k8s`, BEFORE `dependency`. Empty-store tolerant.

Nodes:
  * ``iam_role``               — ``iam_role:<arn-or-addr>``
  * ``iam_policy``             — ``iam_policy:<arn-or-addr>``
  * ``iam_instance_profile``   — ``iam_instance_profile:<addr>``
  * ``iam_principal``          — ``iam_principal:user/<name>``
  * ``aws_service``            — ``service:<aws_service>``  (synthetic; trust-policy targets)

Edges:
  * iam_role → iam_policy        (``attaches``)            — from ``aws_iam_role_policy_attachment``
  * iam_role → iam_role          (``can_assume``)          — when role A's trust policy lists role B
  * iam_role → service           (``assumed_by``)          — for service principals (ec2/lambda/...)
  * iam_role → iam_inline_policy (``inlines``)             — from ``aws_iam_role_policy``
  * k8s SA   → iam_role          (``irsa_bound``)          — from SA annotation
                                                              ``eks.amazonaws.com/role-arn``

Defensive: trust-policy / policy_document JSON parsing is best-effort. Action
verbs collected on iam_policy nodes when the document is small enough to scan.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from .base import Layer


_IAM_TF_TYPES = {
    "aws_iam_role",
    "aws_iam_policy",
    "aws_iam_role_policy",
    "aws_iam_role_policy_attachment",
    "aws_iam_instance_profile",
    "aws_iam_user",
}

_MAX_POLICY_SCAN_BYTES = 32 * 1024


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
                if not addr or tf_type not in _IAM_TF_TYPES:
                    continue
                attrs = res.get("values") or res.get("attributes") or {}
                if not isinstance(attrs, dict):
                    if isinstance(res.get("instances"), list) and res["instances"]:
                        attrs = res["instances"][0].get("attributes") or {}
                    else:
                        attrs = {}
                yield env, addr, tf_type, attrs


def _maybe_load_doc(value) -> dict | list | None:
    """Trust / inline / managed policy documents are sometimes JSON strings,
    sometimes dicts. Be defensive — never crash."""
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        if len(value) > _MAX_POLICY_SCAN_BYTES:
            return None
        try:
            return json.loads(value)
        except Exception:
            return None
    return None


def _trust_policy_targets(doc) -> tuple[list[str], list[str]]:
    """Return (service_principals, role_arns) from a trust policy doc."""
    services: set[str] = set()
    roles: set[str] = set()
    if not isinstance(doc, dict):
        return [], []
    statements = doc.get("Statement") or []
    if isinstance(statements, dict):
        statements = [statements]
    if not isinstance(statements, list):
        return [], []
    for stmt in statements:
        if not isinstance(stmt, dict):
            continue
        principal = stmt.get("Principal")
        if not isinstance(principal, dict):
            continue
        for key in ("Service", "AWS", "Federated"):
            v = principal.get(key)
            if v is None:
                continue
            items = v if isinstance(v, list) else [v]
            for item in items:
                if not isinstance(item, str):
                    continue
                if key == "Service":
                    services.add(item)
                elif key == "AWS" and "role/" in item:
                    roles.add(item)
    return sorted(services), sorted(roles)


def _policy_action_verbs(doc) -> list[str]:
    """Extract distinct Action verbs from a policy document for cheap summarization."""
    if not isinstance(doc, dict):
        return []
    statements = doc.get("Statement") or []
    if isinstance(statements, dict):
        statements = [statements]
    if not isinstance(statements, list):
        return []
    verbs: set[str] = set()
    for stmt in statements:
        if not isinstance(stmt, dict):
            continue
        actions = stmt.get("Action")
        if actions is None:
            continue
        items = actions if isinstance(actions, list) else [actions]
        for a in items:
            if isinstance(a, str):
                verbs.add(a)
    return sorted(verbs)[:40]


class IAMLayer(Layer):
    name = "iam"
    refresh_trigger = "manual"

    def scan(self, ctx: dict) -> tuple[list[dict], list[dict]]:
        verbose = bool(ctx.get("verbose"))
        repo_root = Path(ctx.get("repo_root", "."))
        persist_dir = Path(ctx.get("persist_dir", repo_root / ".kuberly"))
        store = ctx.get("graph_store")

        nodes: list[dict] = []
        edges: list[dict] = []
        emitted: set[str] = set()

        # arn → node id, addr → node id, name → node id (best-effort lookup
        # paths so attachments resolve regardless of which key the rule uses).
        role_by_arn: dict[str, str] = {}
        role_by_addr: dict[str, str] = {}
        role_by_name: dict[str, str] = {}
        policy_by_arn: dict[str, str] = {}
        policy_by_addr: dict[str, str] = {}
        policy_by_name: dict[str, str] = {}

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

        rows = list(_iter_state_resources(persist_dir))

        # --- pass 1: emit nodes + indices --------------------------------------
        attachments: list[dict] = []
        inline_policies: list[tuple[str, str, dict]] = []
        for env, addr, tf_type, attrs in rows:
            if tf_type == "aws_iam_role":
                arn = str(attrs.get("arn") or "")
                role_name = str(attrs.get("name") or "")
                key = arn or addr
                nid = f"iam_role:{key}"
                trust_doc = _maybe_load_doc(attrs.get("assume_role_policy"))
                services, role_assumers = _trust_policy_targets(trust_doc)
                _emit_node(
                    {
                        "id": nid,
                        "type": "iam_role",
                        "label": role_name or addr,
                        "env": env,
                        "address": addr,
                        "arn": arn,
                        "name": role_name,
                        "max_session_duration": int(
                            attrs.get("max_session_duration") or 0
                        ),
                        "trust_policy_services": services,
                        "trust_policy_roles": role_assumers,
                    }
                )
                if arn:
                    role_by_arn[arn] = nid
                role_by_addr[addr] = nid
                if role_name:
                    role_by_name[role_name] = nid
                # Service-principal synthetic nodes + edges
                for svc in services:
                    svc_id = f"service:{svc}"
                    _emit_node(
                        {
                            "id": svc_id,
                            "type": "aws_service",
                            "label": svc,
                            "service": svc,
                        }
                    )
                    _emit_edge(nid, svc_id, "assumed_by")
            elif tf_type == "aws_iam_policy":
                arn = str(attrs.get("arn") or "")
                policy_name = str(attrs.get("name") or "")
                key = arn or addr
                nid = f"iam_policy:{key}"
                doc = _maybe_load_doc(attrs.get("policy"))
                verbs = _policy_action_verbs(doc) if doc else []
                _emit_node(
                    {
                        "id": nid,
                        "type": "iam_policy",
                        "label": policy_name or addr,
                        "env": env,
                        "address": addr,
                        "arn": arn,
                        "name": policy_name,
                        "customer_managed": True,
                        "action_verbs": verbs,
                    }
                )
                if arn:
                    policy_by_arn[arn] = nid
                policy_by_addr[addr] = nid
                if policy_name:
                    policy_by_name[policy_name] = nid
            elif tf_type == "aws_iam_instance_profile":
                nid = f"iam_instance_profile:{addr}"
                _emit_node(
                    {
                        "id": nid,
                        "type": "iam_instance_profile",
                        "label": addr,
                        "env": env,
                        "address": addr,
                        "role_name": str(attrs.get("role") or ""),
                    }
                )
            elif tf_type == "aws_iam_user":
                user_name = str(attrs.get("name") or addr)
                nid = f"iam_principal:user/{user_name}"
                _emit_node(
                    {
                        "id": nid,
                        "type": "iam_principal",
                        "label": user_name,
                        "env": env,
                        "address": addr,
                        "principal_kind": "user",
                    }
                )
            elif tf_type == "aws_iam_role_policy_attachment":
                attachments.append({"env": env, "addr": addr, "attrs": attrs})
            elif tf_type == "aws_iam_role_policy":
                inline_policies.append((env, addr, attrs))

        # --- pass 2: attachments + can_assume ----------------------------------
        def _resolve_role(ref: str) -> str:
            if not ref:
                return ""
            return (
                role_by_arn.get(ref)
                or role_by_name.get(ref)
                or role_by_addr.get(ref)
                or ""
            )

        def _resolve_policy(ref: str) -> str:
            if not ref:
                return ""
            return (
                policy_by_arn.get(ref)
                or policy_by_name.get(ref)
                or policy_by_addr.get(ref)
                or ""
            )

        for att in attachments:
            attrs = att["attrs"]
            role_ref = str(attrs.get("role") or "")
            policy_ref = str(attrs.get("policy_arn") or "")
            role_id = _resolve_role(role_ref)
            policy_id = _resolve_policy(policy_ref)
            if role_id and policy_id:
                _emit_edge(role_id, policy_id, "attaches")

        # role-to-role can_assume from collected trust_policy_roles.
        for n in list(nodes):
            if n.get("type") != "iam_role":
                continue
            for ref in n.get("trust_policy_roles") or []:
                target_id = _resolve_role(ref)
                if target_id and target_id != n["id"]:
                    _emit_edge(n["id"], target_id, "can_assume")

        # inline role policies — emit a synthetic iam_inline_policy node.
        for env, addr, attrs in inline_policies:
            role_ref = str(attrs.get("role") or "")
            role_id = _resolve_role(role_ref)
            if not role_id:
                continue
            inline_id = f"iam_inline_policy:{addr}"
            doc = _maybe_load_doc(attrs.get("policy"))
            verbs = _policy_action_verbs(doc) if doc else []
            _emit_node(
                {
                    "id": inline_id,
                    "type": "iam_inline_policy",
                    "label": addr,
                    "env": env,
                    "address": addr,
                    "name": str(attrs.get("name") or ""),
                    "action_verbs": verbs,
                }
            )
            _emit_edge(role_id, inline_id, "inlines")

        # --- pass 3: IRSA — k8s ServiceAccount → iam_role ---------------------
        # Read from the live store so we can match annotations to the iam_role
        # nodes we just emitted (which aren't yet visible to ctx-store mirror
        # because replace_layer happens after scan returns).
        if store is not None:
            try:
                k8s_nodes = store.all_nodes(layer="k8s")
            except Exception:
                k8s_nodes = []
        else:
            k8s_nodes = []
        for n in k8s_nodes:
            if n.get("kind") != "ServiceAccount":
                continue
            anns = n.get("annotations") if isinstance(n.get("annotations"), dict) else {}
            arn = str(anns.get("eks.amazonaws.com/role-arn") or "")
            if not arn:
                continue
            target = role_by_arn.get(arn)
            # Best-effort fallback: match on role name embedded in the ARN.
            if not target and "role/" in arn:
                role_name = arn.rsplit("role/", 1)[-1]
                target = role_by_name.get(role_name)
            if target:
                _emit_edge(n["id"], target, "irsa_bound")

        if verbose:
            print(f"  [IAMLayer] emitted {len(nodes)} nodes / {len(edges)} edges")
        return nodes, edges
