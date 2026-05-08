"""K8sLayer — live k8s resources via MCP `resources_list`.

v0.46.0 cluster-driven API discovery: we no longer ship a hardcoded ~22-kind
list. ``K8sLayer.scan`` calls :func:`kuberly_graph.client.discover_kinds`
which:

  1. Lists every CustomResourceDefinition currently registered on the cluster.
  2. Reads each CRD's ``spec`` to derive ``(group/version, Kind)`` for every
     served version.
  3. Merges the result with :data:`kuberly_graph.client.BUILTIN_K8S_KINDS`
     (~40 standard kinds — workloads, networking, RBAC, storage, autoscaling,
     scheduling, admission webhooks, API services, leases, etc.).

If discovery fails or yields zero kinds, we fall back to ``BUILTIN_K8S_KINDS``.
The legacy ``DEFAULT_K8S_KINDS`` symbol is kept as an alias for that fallback.
"""

from __future__ import annotations

import sys

from .base import Layer
from ..client import BUILTIN_K8S_KINDS

# Keep the v0.45.1 export name working — external callers (and tests) still
# import this. New code should use ``BUILTIN_K8S_KINDS`` from ``client``.
DEFAULT_K8S_KINDS: list[tuple[str, str]] = list(BUILTIN_K8S_KINDS)


def _extract_container_images(spec: dict) -> list[str]:
    """Return distinct image refs declared on the (init+main) containers of a
    Pod-template-shaped spec. Defensive against partial specs.
    """
    if not isinstance(spec, dict):
        return []
    # Workload kinds nest pod spec under spec.template.spec; Pod has it directly.
    pod_spec = spec
    template = spec.get("template")
    if isinstance(template, dict) and isinstance(template.get("spec"), dict):
        pod_spec = template["spec"]
    images: list[str] = []
    seen: set[str] = set()
    for key in ("initContainers", "containers", "ephemeralContainers"):
        items = pod_spec.get(key)
        if not isinstance(items, list):
            continue
        for c in items:
            if not isinstance(c, dict):
                continue
            img = c.get("image")
            if isinstance(img, str) and img and img not in seen:
                seen.add(img)
                images.append(img)
    return images


def _extract_pod_volume_claims(spec: dict) -> list[str]:
    """Return PVC names referenced from a Pod-template-shaped spec."""
    if not isinstance(spec, dict):
        return []
    pod_spec = spec
    template = spec.get("template")
    if isinstance(template, dict) and isinstance(template.get("spec"), dict):
        pod_spec = template["spec"]
    vols = pod_spec.get("volumes")
    if not isinstance(vols, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for v in vols:
        if not isinstance(v, dict):
            continue
        pvc = v.get("persistentVolumeClaim")
        if isinstance(pvc, dict):
            name = pvc.get("claimName")
            if isinstance(name, str) and name and name not in seen:
                seen.add(name)
                out.append(name)
    return out


def _extract_pv_backing(spec: dict) -> dict:
    """Return a flat dict describing a PV's backing storage so StorageLayer
    can match it to TF-state EBS/EFS resources without re-parsing nested specs.
    """
    if not isinstance(spec, dict):
        return {}
    out: dict = {}
    csi = spec.get("csi")
    if isinstance(csi, dict):
        if isinstance(csi.get("driver"), str):
            out["csi_driver"] = csi["driver"]
        if isinstance(csi.get("volumeHandle"), str):
            out["csi_volume_handle"] = csi["volumeHandle"]
    ebs = spec.get("awsElasticBlockStore")
    if isinstance(ebs, dict) and isinstance(ebs.get("volumeID"), str):
        out["aws_ebs_volume_id"] = ebs["volumeID"]
    sc = spec.get("storageClassName")
    if isinstance(sc, str):
        out["storage_class_name"] = sc
    return out


def _extract_pvc_binding(spec: dict) -> dict:
    if not isinstance(spec, dict):
        return {}
    out: dict = {}
    if isinstance(spec.get("volumeName"), str):
        out["volume_name"] = spec["volumeName"]
    if isinstance(spec.get("storageClassName"), str):
        out["storage_class_name"] = spec["storageClassName"]
    return out


def _extract_secret_configmap_refs(spec: dict) -> tuple[list[str], list[str]]:
    """Return (secret_names, configmap_names) referenced by a pod-template spec.

    Walks volumes[].secret / volumes[].configMap, containers[].envFrom[],
    containers[].env[].valueFrom.{secretKeyRef,configMapKeyRef}. Defensive on
    every shape — never raises.
    """
    if not isinstance(spec, dict):
        return [], []
    pod_spec = spec
    template = spec.get("template")
    if isinstance(template, dict) and isinstance(template.get("spec"), dict):
        pod_spec = template["spec"]

    secrets: list[str] = []
    configmaps: list[str] = []
    seen_secret: set[str] = set()
    seen_cm: set[str] = set()

    def _add_secret(name: str) -> None:
        if name and name not in seen_secret:
            seen_secret.add(name)
            secrets.append(name)

    def _add_cm(name: str) -> None:
        if name and name not in seen_cm:
            seen_cm.add(name)
            configmaps.append(name)

    vols = pod_spec.get("volumes")
    if isinstance(vols, list):
        for v in vols:
            if not isinstance(v, dict):
                continue
            sec = v.get("secret")
            if isinstance(sec, dict):
                _add_secret(str(sec.get("secretName") or ""))
            cm = v.get("configMap")
            if isinstance(cm, dict):
                _add_cm(str(cm.get("name") or ""))

    for key in ("initContainers", "containers", "ephemeralContainers"):
        items = pod_spec.get(key)
        if not isinstance(items, list):
            continue
        for c in items:
            if not isinstance(c, dict):
                continue
            envfrom = c.get("envFrom")
            if isinstance(envfrom, list):
                for ef in envfrom:
                    if not isinstance(ef, dict):
                        continue
                    sref = ef.get("secretRef")
                    if isinstance(sref, dict):
                        _add_secret(str(sref.get("name") or ""))
                    cref = ef.get("configMapRef")
                    if isinstance(cref, dict):
                        _add_cm(str(cref.get("name") or ""))
            envs = c.get("env")
            if isinstance(envs, list):
                for e in envs:
                    if not isinstance(e, dict):
                        continue
                    vf = e.get("valueFrom")
                    if not isinstance(vf, dict):
                        continue
                    skr = vf.get("secretKeyRef")
                    if isinstance(skr, dict):
                        _add_secret(str(skr.get("name") or ""))
                    cmkr = vf.get("configMapKeyRef")
                    if isinstance(cmkr, dict):
                        _add_cm(str(cmkr.get("name") or ""))
    return secrets, configmaps


class K8sLayer(Layer):
    name = "k8s"
    refresh_trigger = "on-event:k8s"

    def scan(self, ctx: dict) -> tuple[list[dict], list[dict]]:
        endpoint = ctx.get("mcp_endpoint")
        verbose = bool(ctx.get("verbose"))
        if not endpoint:
            if verbose:
                print("  [K8sLayer] skip — no mcp_endpoint in ctx")
            return [], []

        existing_rendered_ids: set[str] = set(
            ctx.get("_existing_rendered_ids", set())
        )

        from ..client import (
            CLUSTER_SCOPED_BUILTINS,
            discover_kinds_sync,
            fetch_live_resources_sync,
        )

        per_kind_limit = int(ctx.get("k8s_per_kind_limit") or 1000)

        # Discover the live API surface. Soft-degrades to BUILTIN_K8S_KINDS on
        # any failure; never raises.
        try:
            kinds, crd_index, cluster_scoped = discover_kinds_sync(endpoint)
        except ConnectionError:
            raise
        except Exception as exc:
            print(
                f"  warn: K8sLayer discovery failed ({exc}) — "
                f"falling back to BUILTIN_K8S_KINDS",
                file=sys.stderr,
            )
            kinds = list(DEFAULT_K8S_KINDS)
            crd_index = {}
            cluster_scoped = set(CLUSTER_SCOPED_BUILTINS)

        if not kinds:
            kinds = list(DEFAULT_K8S_KINDS)
            cluster_scoped = set(CLUSTER_SCOPED_BUILTINS)

        if verbose:
            print(
                f"  [K8sLayer] discovered {len(kinds)} kinds "
                f"({len(crd_index)} CRDs)"
            )

        try:
            live = fetch_live_resources_sync(endpoint, kinds)
        except ConnectionError:
            raise
        except Exception as exc:
            raise ConnectionError(f"K8sLayer MCP call failed: {exc}") from exc

        nodes: list[dict] = []
        edges: list[dict] = []
        live_index: dict[tuple[str, str, str], str] = {}
        # Track which (apiVersion, Kind) ended up with at least one node, so
        # CRD-defines-kind edges can target only existing nodes.
        kind_to_node_ids: dict[tuple[str, str], list[str]] = {}

        # Build a quick lookup: (group, kind) -> CRD metadata.name. Used to
        # emit `crd:<name> -> k8s_resource:<...>` edges (relation
        # `defines_kind`).
        crd_by_group_kind: dict[tuple[str, str], str] = {}
        for crd_name, spec in crd_index.items():
            grp = spec.get("group") or ""
            knd = spec.get("kind") or ""
            if grp and knd:
                crd_by_group_kind[(grp, knd)] = crd_name

        for (api_version, kind), resources in live.items():
            if not resources:
                continue
            # Apply per-kind cap to keep the graph tractable on huge clusters
            # (e.g. Events / Endpoints can be 10k+).
            if per_kind_limit and len(resources) > per_kind_limit:
                if verbose:
                    print(
                        f"  [K8sLayer] capping {api_version}/{kind} at "
                        f"{per_kind_limit} (was {len(resources)})"
                    )
                resources = resources[:per_kind_limit]
            kind_scope_cluster = kind in cluster_scoped
            for r in resources:
                meta = r.get("metadata") or {}
                name = meta.get("name")
                if not name:
                    continue
                ns_raw = meta.get("namespace") or ""
                if not ns_raw and kind_scope_cluster:
                    ns = "cluster"
                else:
                    ns = ns_raw
                rid = f"k8s_resource:{ns}/{kind}/{name}"
                # Preserve full metadata + spec on the node so DependencyLayer
                # can read ownerReferences / labels / spec.nodeName etc. without
                # hitting the live MCP again.
                spec = r.get("spec") if isinstance(r.get("spec"), dict) else {}
                labels = meta.get("labels") if isinstance(meta.get("labels"), dict) else {}
                owner_refs = meta.get("ownerReferences") if isinstance(meta.get("ownerReferences"), list) else []
                node_name = ""
                if kind == "Pod" and isinstance(spec, dict):
                    node_name = spec.get("nodeName") or ""
                node_class_ref = ""
                if kind == "NodePool" and isinstance(spec, dict):
                    tpl = spec.get("template") or {}
                    tpl_spec = (tpl.get("spec") or {}) if isinstance(tpl, dict) else {}
                    ncr = tpl_spec.get("nodeClassRef") or {}
                    if isinstance(ncr, dict):
                        node_class_ref = ncr.get("name") or ""
                provider_id = ""
                if kind == "Node" and isinstance(spec, dict):
                    provider_id = spec.get("providerID") or ""
                # Capture image refs and PVC references on workload kinds so
                # ImageBuildLayer + StorageLayer + DependencyLayer can read them
                # off the persisted node without re-fetching live data.
                images: list[str] = []
                pvc_claims: list[str] = []
                secret_refs: list[str] = []
                configmap_refs: list[str] = []
                if kind in {"Deployment", "StatefulSet", "DaemonSet", "Job", "ReplicaSet", "Pod"}:
                    images = _extract_container_images(spec)
                    pvc_claims = _extract_pod_volume_claims(spec)
                    secret_refs, configmap_refs = _extract_secret_configmap_refs(spec)
                # Storage — capture binding/backing fields so StorageLayer can
                # do PVC→PV and PV→EBS/EFS matching.
                pv_backing: dict = {}
                pvc_binding: dict = {}
                annotations = (
                    meta.get("annotations") if isinstance(meta.get("annotations"), dict) else {}
                )
                if kind == "PersistentVolume":
                    pv_backing = _extract_pv_backing(spec)
                if kind == "PersistentVolumeClaim":
                    pvc_binding = _extract_pvc_binding(spec)
                # Persist a slim copy of `spec` for kinds that AlertLayer /
                # ComplianceLayer / SecretsLayer want to introspect without
                # re-fetching. We keep the raw dict — downstream walkers must
                # be defensive (they already are).
                spec_for_node = (
                    spec
                    if kind
                    in {
                        "PrometheusRule",
                        "ServiceMonitor",
                        "ExternalSecret",
                        "SecretStore",
                        "ClusterSecretStore",
                        "Service",
                        "Ingress",
                        "Deployment",
                        "StatefulSet",
                        "DaemonSet",
                        "Pod",
                    }
                    else {}
                )
                nodes.append(
                    {
                        "id": rid,
                        "type": "k8s_resource",
                        "label": f"{kind}/{name}",
                        "apiVersion": api_version,
                        "kind": kind,
                        "namespace": ns,
                        "name": name,
                        "creation_timestamp": meta.get("creationTimestamp", ""),
                        "labels": labels,
                        "annotations": annotations,
                        "owner_references": owner_refs,
                        "node_name": node_name,
                        "node_class_ref": node_class_ref,
                        "provider_id": provider_id,
                        "container_images": images,
                        "pvc_claims": pvc_claims,
                        "pv_backing": pv_backing,
                        "pvc_binding": pvc_binding,
                        "secret_refs": secret_refs,
                        "configmap_refs": configmap_refs,
                        "spec": spec_for_node,
                    }
                )
                live_index[(kind, ns, name)] = rid
                kind_to_node_ids.setdefault((api_version, kind), []).append(rid)

        # Emit one CRD node per discovered CustomResourceDefinition + a
        # `crd:<name> -> k8s_resource:<...>` defines_kind edge for every live
        # resource of the kinds it governs. The CRD nodes themselves live
        # under the k8s layer so they refresh together with the live data.
        for crd_name, spec in crd_index.items():
            if not crd_name:
                continue
            grp = spec.get("group") or ""
            knd = spec.get("kind") or ""
            scope = spec.get("scope") or ""
            versions = spec.get("versions") or []
            crd_id = f"crd:{crd_name}"
            nodes.append(
                {
                    "id": crd_id,
                    "type": "crd",
                    "label": crd_name,
                    "name": crd_name,
                    "group": grp,
                    "kind": knd,
                    "scope": scope,
                    "versions": [v.get("name", "") for v in versions if v.get("name")],
                }
            )
            if not (grp and knd):
                continue
            # Wire the CRD to every k8s_resource of any of its served versions.
            for v in versions:
                vname = v.get("name") or ""
                if not vname:
                    continue
                api = f"{grp}/{vname}"
                for rid in kind_to_node_ids.get((api, knd), []):
                    edges.append(
                        {
                            "source": crd_id,
                            "target": rid,
                            "relation": "defines_kind",
                        }
                    )

        for rid_full in existing_rendered_ids:
            try:
                _, body = rid_full.split(":", 1)
                env_app, kind, name = body.rsplit("/", 2)
                _env, _app = env_app.split("/", 1)
            except ValueError:
                continue
            for (k_kind, _k_ns, k_name), live_id in live_index.items():
                if k_kind == kind and k_name == name:
                    edges.append(
                        {
                            "source": rid_full,
                            "target": live_id,
                            "relation": "live_match",
                        }
                    )

        if verbose:
            print(f"  [K8sLayer] emitted {len(nodes)} nodes / {len(edges)} edges")
        return nodes, edges
