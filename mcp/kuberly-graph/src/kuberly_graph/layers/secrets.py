"""SecretsLayer — extract AWS Secrets Manager / SSM nodes from Terraform state
and wire them to the existing k8s ExternalSecret / SecretStore graph.

Runs AFTER ``state`` and ``k8s``, BEFORE ``dependency``. Pure structural
extractor; no live AWS / k8s calls. Empty-store tolerant.

Pod → Secret / Pod → ConfigMap consumption edges live in DependencyLayer (it
needs to read pod template specs which K8sLayer captures via
``container_images`` etc; SecretsLayer leaves that to the dependency walk).

Nodes:
  * ``aws_secret:<env>/<name>``   — from ``aws_secretsmanager_secret``
  * ``ssm_param:<env>/<name>``    — from ``aws_ssm_parameter``

Edges:
  * k8s_resource(ExternalSecret) → k8s_resource(Secret) (``creates``)
  * k8s_resource(ExternalSecret) → k8s_resource(SecretStore|ClusterSecretStore)
                                                   (``uses_store``)
  * k8s_resource(SecretStore|ClusterSecretStore) → aws_secret / ssm_param
                                                   (``pulls_from``)
"""

from __future__ import annotations

import json
from pathlib import Path

from .base import Layer


_SECRETS_TF_TYPES = {
    "aws_secretsmanager_secret",
    "aws_ssm_parameter",
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
                if not addr or tf_type not in _SECRETS_TF_TYPES:
                    continue
                attrs = res.get("values") or res.get("attributes") or {}
                if not isinstance(attrs, dict):
                    if isinstance(res.get("instances"), list) and res["instances"]:
                        attrs = res["instances"][0].get("attributes") or {}
                    else:
                        attrs = {}
                yield env, addr, tf_type, attrs


def _es_secret_target_name(es_node: dict) -> str:
    """Return the k8s Secret name produced by an ExternalSecret CRD.

    K8sLayer doesn't dump CRD spec by default; we look at the labels &
    annotations dict copy alongside ``name`` (default: ExternalSecret name
    when ``spec.target.name`` isn't surfaced).
    """
    target_name = ""
    spec = es_node.get("spec")
    if isinstance(spec, dict):
        target = spec.get("target")
        if isinstance(target, dict):
            target_name = str(target.get("name") or "")
    if not target_name:
        target_name = str(es_node.get("name") or "")
    return target_name


def _es_store_ref(es_node: dict) -> tuple[str, str]:
    """Return (kind, name) of the SecretStore referenced by the ExternalSecret.

    Empty tuple when not derivable from the persisted node.
    """
    spec = es_node.get("spec")
    if isinstance(spec, dict):
        ref = spec.get("secretStoreRef")
        if isinstance(ref, dict):
            return str(ref.get("kind") or "SecretStore"), str(ref.get("name") or "")
    return "", ""


def _store_provider_secrets(store_node: dict) -> list[str]:
    """Extract the AWS Secrets Manager / SSM keys referenced by a SecretStore's
    provider config. Defensive — returns ``[]`` if unparsable."""
    spec = store_node.get("spec")
    out: list[str] = []
    if not isinstance(spec, dict):
        return out
    provider = spec.get("provider")
    if not isinstance(provider, dict):
        return out
    aws = provider.get("aws")
    if isinstance(aws, dict):
        # Service is ``SecretsManager`` or ``ParameterStore``; we don't filter
        # — both end up as best-effort name matches downstream.
        # The actual keys live in ExternalSecret.spec.data[].remoteRef.key, not
        # in the SecretStore. We just record the service kind here.
        return out
    return out


class SecretsLayer(Layer):
    name = "secrets"
    refresh_trigger = "manual"

    def scan(self, ctx: dict) -> tuple[list[dict], list[dict]]:
        verbose = bool(ctx.get("verbose"))
        repo_root = Path(ctx.get("repo_root", "."))
        persist_dir = Path(ctx.get("persist_dir", repo_root / ".kuberly"))
        store = ctx.get("graph_store")
        if store is None:
            from ..store import open_store

            store = open_store(persist_dir)

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

        # ---- state-side nodes ------------------------------------------------
        aws_secret_by_name: dict[tuple[str, str], str] = {}
        ssm_param_by_name: dict[tuple[str, str], str] = {}
        for env, addr, tf_type, attrs in _iter_state_resources(persist_dir):
            if tf_type == "aws_secretsmanager_secret":
                name = str(attrs.get("name") or addr)
                nid = f"aws_secret:{env}/{name}"
                _emit_node(
                    {
                        "id": nid,
                        "type": "aws_secret",
                        "label": name,
                        "env": env,
                        "address": addr,
                        "name": name,
                        "arn": str(attrs.get("arn") or ""),
                        "rotation_enabled": bool(attrs.get("rotation_enabled")),
                        "kms_key_id": str(attrs.get("kms_key_id") or ""),
                    }
                )
                aws_secret_by_name[(env, name)] = nid
            elif tf_type == "aws_ssm_parameter":
                name = str(attrs.get("name") or addr)
                nid = f"ssm_param:{env}/{name}"
                _emit_node(
                    {
                        "id": nid,
                        "type": "ssm_param",
                        "label": name,
                        "env": env,
                        "address": addr,
                        "name": name,
                        "param_type": str(attrs.get("type") or ""),
                        "tier": str(attrs.get("tier") or ""),
                    }
                )
                ssm_param_by_name[(env, name)] = nid

        # ---- k8s side: ExternalSecret → Secret + → SecretStore ---------------
        try:
            k8s_nodes = store.all_nodes(layer="k8s")
        except Exception:
            k8s_nodes = []

        secrets_by_id: dict[tuple[str, str], dict] = {}
        stores_by_id: dict[tuple[str, str, str], dict] = {}
        external_secrets: list[dict] = []
        for n in k8s_nodes:
            kind = n.get("kind")
            ns = str(n.get("namespace") or "")
            name = str(n.get("name") or "")
            if not name:
                continue
            if kind == "Secret":
                secrets_by_id[(ns, name)] = n
            elif kind in {"SecretStore", "ClusterSecretStore"}:
                stores_by_id[(kind, ns, name)] = n
            elif kind == "ExternalSecret":
                external_secrets.append(n)

        for es in external_secrets:
            ns = str(es.get("namespace") or "")
            target_name = _es_secret_target_name(es)
            if target_name:
                secret_node = secrets_by_id.get((ns, target_name))
                if secret_node is not None:
                    _emit_edge(es["id"], secret_node["id"], "creates")
            store_kind, store_name = _es_store_ref(es)
            if store_name:
                store_node = stores_by_id.get((store_kind or "SecretStore", ns, store_name))
                if store_node is None and store_kind == "ClusterSecretStore":
                    store_node = stores_by_id.get(("ClusterSecretStore", "", store_name))
                if store_node is not None:
                    _emit_edge(es["id"], store_node["id"], "uses_store")
            # ExternalSecret.spec.data[].remoteRef.key tells us which
            # AWS-side keys this ES pulls from. K8sLayer only persists
            # the well-known fields, so this is best-effort.
            spec = es.get("spec") if isinstance(es.get("spec"), dict) else {}
            data = spec.get("data") if isinstance(spec.get("data"), list) else []
            for entry in data or []:
                if not isinstance(entry, dict):
                    continue
                ref = entry.get("remoteRef")
                if not isinstance(ref, dict):
                    continue
                key = str(ref.get("key") or "")
                if not key:
                    continue
                # Best-effort env match using the ES node's env (if surfaced)
                # or fall back to all envs.
                env_hint = str(es.get("env") or "")
                # Match against aws_secret / ssm_param by name regardless of env.
                aws_target = ""
                ssm_target = ""
                for (e, n_name), nid in aws_secret_by_name.items():
                    if n_name == key and (not env_hint or e == env_hint):
                        aws_target = nid
                        break
                if not aws_target:
                    for (e, n_name), nid in aws_secret_by_name.items():
                        if n_name == key:
                            aws_target = nid
                            break
                for (e, n_name), nid in ssm_param_by_name.items():
                    if n_name == key and (not env_hint or e == env_hint):
                        ssm_target = nid
                        break
                if not ssm_target:
                    for (e, n_name), nid in ssm_param_by_name.items():
                        if n_name == key:
                            ssm_target = nid
                            break
                store_node = None
                if store_name:
                    store_node = stores_by_id.get(
                        (store_kind or "SecretStore", ns, store_name)
                    ) or stores_by_id.get(("ClusterSecretStore", "", store_name))
                if store_node is not None and aws_target:
                    _emit_edge(store_node["id"], aws_target, "pulls_from", key=key)
                if store_node is not None and ssm_target:
                    _emit_edge(store_node["id"], ssm_target, "pulls_from", key=key)

        if verbose:
            print(
                f"  [SecretsLayer] emitted {len(nodes)} nodes / {len(edges)} edges"
            )
        return nodes, edges
