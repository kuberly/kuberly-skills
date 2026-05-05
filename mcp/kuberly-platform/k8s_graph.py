#!/usr/bin/env python3
"""k8s_graph — derive a sanitized "what's actually running in the cluster"
overlay from a live Kubernetes cluster.

Pairs with state_graph.py: that builds the **infrastructure** layer
(modules + per-resource Terraform graph) from S3 state buckets. This
script builds the **application** layer (workloads + services + IRSA
bridges) from the live cluster via `kubectl get -o json`.

The output overlay is committed to the infra repo at
`.claude/k8s_overlay_<env>.json` and consumed by KuberlyPlatform on
graph build to synthesize `k8s:<env>/<ns>/<kind>/<name>` nodes plus
edges (Service→workload via label selector, workload→ServiceAccount,
ServiceAccount→IAM role via IRSA annotation, ownerRefs, etc.).

Security model — same as state_graph.py: explicit per-kind whitelist
of fields kept. The leak surface is what we KEEP, not what we drop.
ConfigMap and Secret `data` is never read — only the key names.
Container `env` values, `command`, `args`, `status`, and all
unlisted annotations are dropped at extraction time.

Usage:

    # be connected to the cluster first:
    aws eks update-kubeconfig --name prod --region eu-central-1
    # ...or whatever auth path your stack uses

    python3 k8s_graph.py generate --env prod \
        --output .claude/k8s_overlay_prod.json

    # extra coverage (slower, default OFF):
    python3 k8s_graph.py generate --env prod --include-pods

    # filter:
    python3 k8s_graph.py generate --env prod --namespaces monitoring,argocd

Stdlib only. Shells out to `kubectl` — caller is responsible for the
right context being set.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# ----- safety: schema for the overlay file ----------------------------

SCHEMA_VERSION = 1

_RE_NS = re.compile(r"^[a-z0-9.-]{1,63}$")
_RE_K8S_NAME = re.compile(r"^[a-zA-Z0-9.\-_]{1,253}$")
_RE_LABEL_KEY = re.compile(r"^[a-zA-Z0-9./_-]{1,253}$")
_RE_LABEL_VAL = re.compile(r"^[a-zA-Z0-9._-]{0,63}$")  # K8s allows empty
_RE_KIND = re.compile(r"^[A-Z][a-zA-Z0-9]{0,62}$")
_RE_API = re.compile(r"^[a-zA-Z0-9./-]{1,128}$")
_RE_IMAGE = re.compile(r"^[a-zA-Z0-9._:/@\-]{1,512}$")
_RE_ARN = re.compile(r"^arn:aws[a-z0-9-]*:[a-z0-9-]+:[a-z0-9-]*:[0-9]{12}:[a-zA-Z0-9._:/+=,@\-]+$")
_RE_CONTEXT = re.compile(r"^[a-zA-Z0-9._:/@\-]{1,256}$")
_RE_ENV_NAME = re.compile(r"^[a-z0-9_-]+$")
_RE_CLUSTER_STR = re.compile(r"^[a-zA-Z0-9._-]+$")

_MAX_STR = 256

# Annotations we explicitly keep — anything else is dropped.
# These are non-sensitive structural markers that are useful for
# the graph: IRSA bridge, prometheus discovery, helm origin tracing.
_KEEP_ANNOTATIONS = frozenset({
    "eks.amazonaws.com/role-arn",
    "iam.gke.io/gcp-service-account",  # GKE Workload Identity equivalent
    "azure.workload.identity/client-id",  # AKS equivalent
    "prometheus.io/scrape",
    "prometheus.io/port",
    "prometheus.io/path",
    "prometheus.io/scheme",
    "argocd.argoproj.io/sync-wave",
    "argocd.argoproj.io/hook",
    "meta.helm.sh/release-name",
    "meta.helm.sh/release-namespace",
})

# Resource kinds whose `data` payload we never even parse — we just
# emit the data KEY NAMES (so "this secret has key `password`" is in
# the graph but the value never leaves the cluster).
_DATA_KINDS = frozenset({"ConfigMap", "Secret"})

# Kinds we treat as workloads (template.spec extraction path).
_WORKLOAD_KINDS = frozenset({
    "Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob",
    "ReplicaSet", "Pod",
})


# ----- string sanitizer ----------------------------------------------

def _sanitize_str(value: object, pattern: re.Pattern, field: str,
                  max_len: int = _MAX_STR) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field}: expected string, got {type(value).__name__}")
    if len(value) > max_len:
        raise ValueError(f"{field}: too long ({len(value)} > {max_len})")
    if not pattern.match(value):
        raise ValueError(f"{field}: failed safety regex {pattern.pattern!r}")
    return value


def _safe_str(value: object, pattern: re.Pattern, max_len: int = _MAX_STR) -> str:
    """Best-effort: return value if safe, else "". For nested fields where
    it's better to drop the field than fail the whole resource."""
    if not isinstance(value, str) or len(value) > max_len or not pattern.match(value):
        return ""
    return value


def _safe_dict_strings(d: object, key_pat: re.Pattern, val_pat: re.Pattern,
                       max_pairs: int = 64) -> dict:
    """Filter a {str: str} dict — only keys/values matching their regex pass."""
    if not isinstance(d, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in list(d.items())[:max_pairs]:
        if not isinstance(k, str) or not isinstance(v, str):
            continue
        if len(k) > 253 or len(v) > 253:
            continue
        if not key_pat.match(k):
            continue
        if v and not val_pat.match(v):
            continue
        out[k] = v
    return out


# ----- per-kind extractors --------------------------------------------

def _meta(obj: dict) -> dict:
    m = obj.get("metadata", {}) or {}
    return {
        "name": _safe_str(m.get("name"), _RE_K8S_NAME),
        "namespace": _safe_str(m.get("namespace"), _RE_NS),
    }


def _labels(obj: dict) -> dict:
    m = obj.get("metadata", {}) or {}
    return _safe_dict_strings(m.get("labels"), _RE_LABEL_KEY, _RE_LABEL_VAL)


def _filtered_annotations(obj: dict) -> dict:
    m = obj.get("metadata", {}) or {}
    raw = m.get("annotations") or {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in raw.items():
        if k not in _KEEP_ANNOTATIONS:
            continue
        if not isinstance(v, str) or len(v) > 256:
            continue
        # Only ARNs allowed for the role-arn annotation; other allowlisted
        # annotations get a generic length+printable check.
        if k == "eks.amazonaws.com/role-arn":
            if not _RE_ARN.match(v):
                continue
        out[k] = v
    return out


def _owner_refs(obj: dict) -> list[dict]:
    m = obj.get("metadata", {}) or {}
    raw = m.get("ownerReferences") or []
    out: list[dict] = []
    if not isinstance(raw, list):
        return out
    for r in raw[:8]:
        if not isinstance(r, dict):
            continue
        kind = _safe_str(r.get("kind"), _RE_KIND)
        name = _safe_str(r.get("name"), _RE_K8S_NAME)
        if kind and name:
            out.append({"kind": kind, "name": name})
    return out


def _extract_pod_template_spec(template: dict) -> dict:
    """From a `pod.spec` (or workload.spec.template.spec), pull only safe
    structural fields: SA, container names+images, volume references."""
    if not isinstance(template, dict):
        return {}
    sa = _safe_str(template.get("serviceAccountName") or template.get("serviceAccount", ""),
                   _RE_K8S_NAME)
    images: list[str] = []
    containers: list[str] = []
    config_refs: set[str] = set()
    secret_refs: set[str] = set()
    pvc_refs: set[str] = set()

    for c_list_key in ("containers", "initContainers"):
        for c in (template.get(c_list_key) or []):
            if not isinstance(c, dict):
                continue
            cname = _safe_str(c.get("name"), _RE_K8S_NAME)
            if cname:
                containers.append(cname)
            img = _safe_str(c.get("image"), _RE_IMAGE, max_len=512)
            if img:
                images.append(img)
            # envFrom — names only. NEVER `env[*].value` or `valueFrom`.
            for ef in (c.get("envFrom") or []):
                if not isinstance(ef, dict):
                    continue
                cm = ef.get("configMapRef", {}) or {}
                sec = ef.get("secretRef", {}) or {}
                cmn = _safe_str(cm.get("name"), _RE_K8S_NAME)
                sn = _safe_str(sec.get("name"), _RE_K8S_NAME)
                if cmn:
                    config_refs.add(cmn)
                if sn:
                    secret_refs.add(sn)
            # env[].valueFrom — names only.
            for e in (c.get("env") or []):
                if not isinstance(e, dict):
                    continue
                vf = e.get("valueFrom") or {}
                cm = (vf.get("configMapKeyRef") or {}).get("name")
                sec = (vf.get("secretKeyRef") or {}).get("name")
                if cm:
                    cn = _safe_str(cm, _RE_K8S_NAME)
                    if cn:
                        config_refs.add(cn)
                if sec:
                    sn = _safe_str(sec, _RE_K8S_NAME)
                    if sn:
                        secret_refs.add(sn)

    # Volumes — name + source kind + referenced object name. NEVER content.
    for v in (template.get("volumes") or []):
        if not isinstance(v, dict):
            continue
        cm = (v.get("configMap") or {}).get("name")
        if cm:
            cn = _safe_str(cm, _RE_K8S_NAME)
            if cn:
                config_refs.add(cn)
        sec = (v.get("secret") or {}).get("secretName")
        if sec:
            sn = _safe_str(sec, _RE_K8S_NAME)
            if sn:
                secret_refs.add(sn)
        pvc = (v.get("persistentVolumeClaim") or {}).get("claimName")
        if pvc:
            pn = _safe_str(pvc, _RE_K8S_NAME)
            if pn:
                pvc_refs.add(pn)

    return {
        "service_account": sa,
        "containers": containers,
        "images": images,
        "config_refs": sorted(config_refs),
        "secret_refs": sorted(secret_refs),
        "pvc_refs": sorted(pvc_refs),
    }


def _extract_workload(obj: dict) -> dict:
    kind = obj.get("kind", "")
    spec = obj.get("spec", {}) or {}
    # Pod has its own spec; workloads wrap it in spec.template.spec.
    if kind == "Pod":
        template = spec
        replicas = 1
    elif kind == "CronJob":
        template = ((spec.get("jobTemplate") or {}).get("spec") or {}).get("template", {}).get("spec") or {}
        replicas = 1
    else:
        template = (spec.get("template") or {}).get("spec") or {}
        r = spec.get("replicas")
        replicas = r if isinstance(r, int) and 0 <= r <= 100000 else None

    out = _extract_pod_template_spec(template)
    if replicas is not None:
        out["replicas"] = replicas
    return out


def _extract_service(obj: dict) -> dict:
    spec = obj.get("spec", {}) or {}
    selector = _safe_dict_strings(spec.get("selector"), _RE_LABEL_KEY, _RE_LABEL_VAL)
    ports: list[dict] = []
    for p in (spec.get("ports") or [])[:32]:
        if not isinstance(p, dict):
            continue
        port = p.get("port")
        proto = p.get("protocol", "TCP")
        if not isinstance(port, int) or not (0 < port < 65536):
            continue
        if proto not in ("TCP", "UDP", "SCTP"):
            continue
        ports.append({"port": port, "protocol": proto})
    svc_type = _safe_str(spec.get("type", ""), re.compile(r"^[A-Z][a-zA-Z]{0,32}$"))
    return {
        "selector": selector,
        "ports": ports,
        "service_type": svc_type,
    }


def _extract_ingress(obj: dict) -> dict:
    spec = obj.get("spec", {}) or {}
    hosts: set[str] = set()
    backends: list[dict] = []
    for rule in (spec.get("rules") or [])[:16]:
        if not isinstance(rule, dict):
            continue
        host = _safe_str(rule.get("host", ""), re.compile(r"^[a-zA-Z0-9.-]{1,253}$"))
        if host:
            hosts.add(host)
        http = rule.get("http") or {}
        for path in (http.get("paths") or [])[:32]:
            if not isinstance(path, dict):
                continue
            be = (path.get("backend") or {}).get("service") or {}
            n = _safe_str(be.get("name", ""), _RE_K8S_NAME)
            port = (be.get("port") or {}).get("number")
            if n:
                backends.append({
                    "service": n,
                    "port": port if isinstance(port, int) and 0 < port < 65536 else None,
                })
    return {"hosts": sorted(hosts), "backends": backends}


def _extract_configmap(obj: dict) -> dict:
    # Data keys ONLY, never values.
    data = obj.get("data") or {}
    binary = obj.get("binaryData") or {}
    keys: list[str] = []
    for k in list(data.keys())[:128]:
        if isinstance(k, str) and len(k) <= 253 and _RE_LABEL_KEY.match(k):
            keys.append(k)
    for k in list(binary.keys())[:128]:
        if isinstance(k, str) and len(k) <= 253 and _RE_LABEL_KEY.match(k):
            keys.append(k)
    return {"data_keys": sorted(set(keys))}


def _extract_secret(obj: dict) -> dict:
    # type + data KEY NAMES only. Never values, never `data`, never `stringData`.
    s_type = _safe_str(obj.get("type", "Opaque"),
                       re.compile(r"^[A-Za-z0-9./_-]{1,128}$"))
    keys: list[str] = []
    for k in list((obj.get("data") or {}).keys())[:128]:
        if isinstance(k, str) and len(k) <= 253 and _RE_LABEL_KEY.match(k):
            keys.append(k)
    return {
        "secret_type": s_type,
        "data_keys": sorted(set(keys)),
    }


def _extract_serviceaccount(obj: dict) -> dict:
    out = {}
    annos = _filtered_annotations(obj)
    if annos:
        out["annotations"] = annos
    # Lift the IRSA role ARN to a top-level field for easy bridging.
    arn = annos.get("eks.amazonaws.com/role-arn")
    if arn:
        out["irsa_role_arn"] = arn
    return out


def _extract_hpa(obj: dict) -> dict:
    spec = obj.get("spec", {}) or {}
    target = spec.get("scaleTargetRef") or {}
    return {
        "min_replicas": spec.get("minReplicas") if isinstance(spec.get("minReplicas"), int) else None,
        "max_replicas": spec.get("maxReplicas") if isinstance(spec.get("maxReplicas"), int) else None,
        "target_kind": _safe_str(target.get("kind", ""), _RE_KIND),
        "target_name": _safe_str(target.get("name", ""), _RE_K8S_NAME),
    }


def _extract_networkpolicy(obj: dict) -> dict:
    spec = obj.get("spec", {}) or {}
    pod_selector = _safe_dict_strings((spec.get("podSelector") or {}).get("matchLabels"),
                                       _RE_LABEL_KEY, _RE_LABEL_VAL)
    policy_types: list[str] = []
    for t in (spec.get("policyTypes") or [])[:4]:
        if t in ("Ingress", "Egress"):
            policy_types.append(t)
    return {"pod_selector": pod_selector, "policy_types": policy_types}


# Per-kind dispatch table.
_KIND_EXTRACTORS = {
    "Deployment": _extract_workload,
    "StatefulSet": _extract_workload,
    "DaemonSet": _extract_workload,
    "Job": _extract_workload,
    "CronJob": _extract_workload,
    "ReplicaSet": _extract_workload,
    "Pod": _extract_workload,
    "Service": _extract_service,
    "Ingress": _extract_ingress,
    "ConfigMap": _extract_configmap,
    "Secret": _extract_secret,
    "ServiceAccount": _extract_serviceaccount,
    "HorizontalPodAutoscaler": _extract_hpa,
    "NetworkPolicy": _extract_networkpolicy,
}


def _extract_resource(obj: dict) -> dict | None:
    """Return whitelisted fields for one K8s resource, or None to skip."""
    if not isinstance(obj, dict):
        return None
    kind = obj.get("kind", "")
    api_version = obj.get("apiVersion", "")
    if not _RE_KIND.match(kind) or not _RE_API.match(api_version):
        return None
    meta = _meta(obj)
    if not meta["name"]:
        return None  # cluster-scoped resources without ns are still ok (ns="")
    base = {
        "kind": kind,
        "apiVersion": api_version,
        "namespace": meta["namespace"],
        "name": meta["name"],
        "labels": _labels(obj),
        "owner_refs": _owner_refs(obj),
    }
    annos = _filtered_annotations(obj)
    if annos:
        base["annotations"] = annos
    extractor = _KIND_EXTRACTORS.get(kind)
    if extractor:
        kind_specific = extractor(obj) or {}
        # Don't let an extractor smuggle in a non-whitelisted field via dict
        # spread — copy through a known set of safe keys only.
        for k, v in kind_specific.items():
            if k in _ALLOWED_KIND_FIELDS.get(kind, set()):
                base[k] = v
    return base


_ALLOWED_KIND_FIELDS = {
    # Workloads
    "Deployment":  {"replicas", "service_account", "containers", "images",
                    "config_refs", "secret_refs", "pvc_refs"},
    "StatefulSet": {"replicas", "service_account", "containers", "images",
                    "config_refs", "secret_refs", "pvc_refs"},
    "DaemonSet":   {"service_account", "containers", "images",
                    "config_refs", "secret_refs", "pvc_refs"},
    "Job":         {"service_account", "containers", "images",
                    "config_refs", "secret_refs", "pvc_refs"},
    "CronJob":     {"service_account", "containers", "images",
                    "config_refs", "secret_refs", "pvc_refs"},
    "ReplicaSet": {"replicas", "service_account", "containers", "images",
                    "config_refs", "secret_refs", "pvc_refs"},
    "Pod":         {"service_account", "containers", "images",
                    "config_refs", "secret_refs", "pvc_refs"},
    # Network
    "Service":   {"selector", "ports", "service_type"},
    "Ingress":   {"hosts", "backends"},
    # Config / secret payloads (key names only)
    "ConfigMap": {"data_keys"},
    "Secret":    {"secret_type", "data_keys"},
    # Identity
    "ServiceAccount": {"annotations", "irsa_role_arn"},
    # Autoscaling
    "HorizontalPodAutoscaler": {"min_replicas", "max_replicas",
                                 "target_kind", "target_name"},
    # Network policy
    "NetworkPolicy": {"pod_selector", "policy_types"},
}


# ----- kubectl shellout -----------------------------------------------

def _kubectl_get(kinds_csv: str, namespaces: list[str] | None,
                 context: str | None) -> dict:
    """Run kubectl get -o json. Returns parsed JSON. Reads from current
    kubeconfig context unless --context is passed."""
    if not shutil.which("kubectl"):
        raise RuntimeError(
            "kubectl not found on PATH. Install kubectl and ensure your "
            "kubeconfig points at the target cluster."
        )
    cmd = ["kubectl", "get", kinds_csv, "-o", "json", "--ignore-not-found"]
    if namespaces:
        # If exactly one namespace, use -n. Otherwise, fetch all-namespaces and
        # filter Python-side.
        if len(namespaces) == 1:
            cmd.extend(["-n", namespaces[0]])
        else:
            cmd.append("--all-namespaces")
    else:
        cmd.append("--all-namespaces")
    if context:
        cmd.extend(["--context", context])
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except OSError as e:
        raise RuntimeError(f"kubectl failed to launch: {e}") from e
    if res.returncode != 0:
        tail = (res.stderr or "").splitlines()
        last = tail[-1] if tail else ""
        raise RuntimeError(f"kubectl get {kinds_csv} failed (exit {res.returncode}): {last[:200]}")
    try:
        return json.loads(res.stdout) if res.stdout.strip() else {"items": []}
    except json.JSONDecodeError as e:
        raise RuntimeError(f"kubectl returned non-JSON: {e.msg}") from e


def _kubectl_current_context() -> str:
    try:
        res = subprocess.run(["kubectl", "config", "current-context"],
                             capture_output=True, text=True, check=False)
        if res.returncode == 0:
            return res.stdout.strip()
    except OSError:
        pass
    return ""


# ----- shared-infra discovery (reuse pattern from state_graph) --------

def _find_shared_infra_files(repo_root: Path) -> dict[str, Path]:
    out: dict[str, Path] = {}
    comp = repo_root / "components"
    if not comp.is_dir():
        return out
    for env_dir in sorted(comp.iterdir()):
        if not env_dir.is_dir():
            continue
        si = env_dir / "shared-infra.json"
        if si.is_file():
            out[env_dir.name] = si
    return out


def _read_cluster_meta(shared_infra_path: Path, env_name: str) -> dict:
    with shared_infra_path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    si = data.get("shared-infra", {})
    target = si.get("target", {})
    cluster = target.get("cluster", {})
    name = str(cluster.get("name", "")).strip()
    region = str(target.get("region", "")).strip()
    if not name:
        raise ValueError(f"{shared_infra_path}: missing target.cluster.name")
    return {"env": env_name, "name": name, "region": region}


# ----- top-level build ------------------------------------------------

# Default kinds — covers ~95% of what's useful for the graph.
# Pods are off by default (transient + noisy); use --include-pods.
_DEFAULT_KINDS = [
    "deployments.apps",
    "statefulsets.apps",
    "daemonsets.apps",
    "jobs.batch",
    "cronjobs.batch",
    "services",
    "ingresses.networking.k8s.io",
    "configmaps",
    "secrets",
    "serviceaccounts",
    "horizontalpodautoscalers.autoscaling",
    "networkpolicies.networking.k8s.io",
]


def build_overlay(repo_root: Path, env: str,
                  include_pods: bool = False,
                  namespaces: list[str] | None = None,
                  context: str | None = None) -> dict:
    si_files = _find_shared_infra_files(repo_root)
    if env not in si_files:
        avail = ", ".join(sorted(si_files)) or "<none>"
        raise SystemExit(f"no components/{env}/shared-infra.json. Available envs: {avail}")
    cluster = _read_cluster_meta(si_files[env], env)
    ctx = context or _kubectl_current_context()

    kinds = list(_DEFAULT_KINDS)
    if include_pods:
        kinds.insert(0, "pods")
    kinds_csv = ",".join(kinds)

    raw = _kubectl_get(kinds_csv, namespaces, context)
    items = raw.get("items") or []

    resources: list[dict] = []
    seen_ns: set[str] = set()
    for obj in items:
        # If we fetched --all-namespaces but a filter list was requested,
        # filter in Python (kubectl can't combine --all-namespaces with -n).
        if namespaces:
            ns = (obj.get("metadata") or {}).get("namespace", "")
            if ns and ns not in namespaces:
                continue
        extracted = _extract_resource(obj)
        if extracted is None:
            continue
        if extracted["namespace"]:
            seen_ns.add(extracted["namespace"])
        resources.append(extracted)

    overlay = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generator": "kuberly-skills/k8s_graph.py",
        "cluster": {
            "env": _sanitize_str(cluster["env"], _RE_ENV_NAME, "cluster.env"),
            "name": _sanitize_str(cluster["name"], _RE_CLUSTER_STR, "cluster.name"),
            "context": _safe_str(ctx, _RE_CONTEXT) if ctx else "",
        },
        "namespaces": sorted(seen_ns),
        "resources": sorted(resources, key=lambda r: (r["namespace"], r["kind"], r["name"])),
    }
    return _validate_overlay(overlay)


# ----- final output validator (post-extraction safety net) ------------

def _validate_overlay(doc: dict) -> dict:
    if doc.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"unexpected schema_version: {doc.get('schema_version')!r}")
    cluster = doc.get("cluster") or {}
    safe_cluster = {
        "env": _sanitize_str(cluster.get("env"), _RE_ENV_NAME, "cluster.env"),
        "name": _sanitize_str(cluster.get("name"), _RE_CLUSTER_STR, "cluster.name"),
        "context": _safe_str(cluster.get("context", ""), _RE_CONTEXT) or "",
    }
    namespaces_in = doc.get("namespaces") or []
    if not isinstance(namespaces_in, list):
        raise ValueError("namespaces: expected list")
    safe_namespaces = []
    for n in namespaces_in:
        if isinstance(n, str) and _RE_NS.match(n):
            safe_namespaces.append(n)
    resources_in = doc.get("resources") or []
    if not isinstance(resources_in, list):
        raise ValueError("resources: expected list")
    # Spot-check: every resource entry has only known keys + safe values.
    safe_resources: list[dict] = []
    for i, r in enumerate(resources_in):
        if not isinstance(r, dict):
            raise ValueError(f"resources[{i}]: expected object")
        kind = r.get("kind", "")
        if not _RE_KIND.match(kind):
            continue
        # Strict per-resource key allowlist.
        allowed_keys = ({"kind", "apiVersion", "namespace", "name",
                         "labels", "owner_refs", "annotations"}
                        | _ALLOWED_KIND_FIELDS.get(kind, set()))
        bad = set(r.keys()) - allowed_keys
        if bad:
            # Drop unknown fields (defensive — shouldn't happen given
            # extractor uses _ALLOWED_KIND_FIELDS, but belt-and-suspenders).
            r = {k: v for k, v in r.items() if k in allowed_keys}
        safe_resources.append(r)

    generated_at = doc.get("generated_at", "")
    if not isinstance(generated_at, str) or len(generated_at) > 40:
        raise ValueError("generated_at: expected ISO-8601 string")

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "generator": "kuberly-skills/k8s_graph.py",
        "cluster": safe_cluster,
        "namespaces": safe_namespaces,
        "resources": safe_resources,
    }


def _write_overlay(overlay: dict, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as fh:
        json.dump(overlay, fh, indent=2, sort_keys=False)
        fh.write("\n")


# ----- CLI ------------------------------------------------------------

def _cmd_generate(args: argparse.Namespace) -> int:
    repo = Path(args.repo or os.getcwd()).resolve()
    namespaces = (
        [n.strip() for n in args.namespaces.split(",") if n.strip()]
        if args.namespaces else None
    )
    overlay = build_overlay(
        repo, args.env,
        include_pods=args.include_pods,
        namespaces=namespaces,
        context=args.context,
    )
    output = Path(args.output) if args.output else (
        repo / ".claude" / f"k8s_overlay_{args.env}.json"
    )
    if args.dry_run:
        print(json.dumps(overlay, indent=2))
        return 0
    _write_overlay(overlay, output)
    by_kind: dict[str, int] = {}
    for r in overlay["resources"]:
        by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
    summary = ", ".join(f"{k}={v}" for k, v in sorted(by_kind.items()))
    rel = output.relative_to(repo) if output.is_relative_to(repo) else output
    print(
        f"wrote {rel} — {len(overlay['resources'])} resources across "
        f"{len(overlay['namespaces'])} namespaces ({summary})"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="k8s_graph",
        description="Build a sanitized live-cluster overlay from kubectl get -o json.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate", help="generate overlay for one env / cluster")
    g.add_argument("--env", required=True, help="env name (matches components/<env>/)")
    g.add_argument("--repo", help="repo root (default: cwd)")
    g.add_argument("--output", help="output path (default: <repo>/.claude/k8s_overlay_<env>.json)")
    g.add_argument("--namespaces",
                   help="comma-separated namespace allowlist (default: all)")
    g.add_argument("--context",
                   help="kubectl context (default: current-context)")
    g.add_argument("--include-pods", action="store_true",
                   help="also include pods (default off — transient/noisy)")
    g.add_argument("--dry-run", action="store_true",
                   help="print to stdout, do not write")
    g.set_defaults(func=_cmd_generate)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
