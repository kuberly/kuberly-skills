"""KubectlLayer — full-RBAC kubernetes scan via local ``kubectl``.

The existing :class:`K8sLayer` reaches the cluster through the bearer-token
``ai-agent-tool`` MCP, whose RBAC is intentionally narrow — many sensitive
kinds (``Secret``, ``Role``, ``RoleBinding``, ``ClusterRole``,
``Lease``, ``ValidatingWebhookConfiguration``, etc.) come back with
``forbidden`` and never enter the graph.

This layer takes the opposite approach: it shells out to whatever
``kubectl`` is on the operator's PATH and runs in the user's current
context, which is typically full IAM/admin. Every api-resource the cluster
exposes is enumerated and scanned, including CRDs the bearer-token MCP
couldn't see.

**Soft-degrade chain.** A missing ``kubectl`` binary, a kubeconfig with no
current-context, or a ``kubectl`` that exits non-zero on its initial
``version`` probe all collapse to an empty ``([], [])`` result with a single
stderr WARN — the layer never raises. Per-kind failures are also caught and
logged so one rejected ``get`` doesn't poison the rest of the run.

**Node id namespace.** The same as :class:`K8sLayer` — ``k8s_resource:<ns>/
<Kind>/<name>`` and ``crd:<name>``. This is deliberate: when both layers
are populated, kubectl's richer view OVERWRITES the bearer-token version
because :meth:`GraphStore.upsert_nodes` is keyed on ``id`` only. Every
node carries ``source: "kubectl"`` so callers can disambiguate the origin.
``DependencyLayer`` doesn't care — it walks the same id namespace.

**Edges.** None emitted from this layer directly. Cross-references are
left to :class:`DependencyLayer` (which already operates on
``k8s_resource:`` ids regardless of source). The only edges this layer
itself emits are ``crd:<name> -> k8s_resource:<...>`` ``defines_kind``
links, identical to K8sLayer's pattern.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from typing import Any, Iterable

from .base import Layer
from .k8s import K8sLayer  # for to_document template reuse
from .k8s import (
    _extract_container_images,
    _extract_pod_volume_claims,
    _extract_pv_backing,
    _extract_pvc_binding,
    _extract_secret_configmap_refs,
)


# Kinds whose volume is so high they routinely break graph rendering /
# semantic indexing. The user can override via ctx["kubectl_skip_kinds"].
_DEFAULT_SKIP_KINDS = [
    "events.k8s.io/Event",
    "v1/Event",
]


def _log(verbose: bool, msg: str) -> None:
    if verbose:
        print(msg, file=sys.stderr)


def _warn(msg: str) -> None:
    print(f"WARN: KubectlLayer: {msg}", file=sys.stderr)


def _run_kubectl(
    argv: list[str],
    *,
    timeout: int = 60,
    capture_stderr: bool = False,
) -> tuple[int, str, str]:
    """Run kubectl. Returns (returncode, stdout, stderr). Never raises."""
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        return 127, "", f"FileNotFoundError: {exc}"
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except Exception as exc:  # noqa: BLE001
        return 1, "", f"{type(exc).__name__}: {exc}"
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _split_api_version(api_version: str) -> tuple[str, str]:
    """Return ``(group, version)``. Core ``v1`` returns ``("", "v1")``."""
    if "/" in api_version:
        g, v = api_version.split("/", 1)
        return g, v
    return "", api_version


def _kind_key(api_version: str, kind: str) -> str:
    """Stable key used by ctx["kubectl_skip_kinds"] entries."""
    return f"{api_version}/{kind}"


def _resource_get_target(group: str, name: str) -> str:
    """Build the ``Kind.group`` (or just ``name``) target for ``kubectl get``.

    Using ``<resource-name>.<group>`` disambiguates kinds that exist in
    multiple groups (e.g. ``ingresses.networking.k8s.io`` vs
    ``ingresses.extensions``) and survives short-name collisions.
    """
    if group:
        return f"{name}.{group}"
    return name


class KubectlLayer(Layer):
    name = "kubectl"
    refresh_trigger = "manual"

    # Reuse the k8s document template — kubectl emits the same node
    # shape (k8s_resource / crd) so the same prose summary applies.
    to_document = K8sLayer.to_document  # type: ignore[assignment]

    # ------------------------------------------------------------------ scan

    def scan(self, ctx: dict) -> tuple[list[dict], list[dict]]:
        verbose = bool(ctx.get("verbose"))
        kubectl_path: str = str(ctx.get("kubectl_path") or "kubectl")
        kubeconfig: str | None = ctx.get("kubectl_kubeconfig") or None
        kctx: str | None = ctx.get("kubectl_context") or None
        per_kind_limit = int(ctx.get("kubectl_per_kind_limit") or 5000)
        timeout_per_call = int(ctx.get("kubectl_timeout_seconds") or 90)

        skip_kinds_raw = ctx.get("kubectl_skip_kinds")
        if skip_kinds_raw is None:
            skip_kinds = set(_DEFAULT_SKIP_KINDS)
        elif isinstance(skip_kinds_raw, list):
            skip_kinds = set(str(x) for x in skip_kinds_raw)
        else:
            skip_kinds = set(_DEFAULT_SKIP_KINDS)

        # Build the prefix every kubectl call shares — context / kubeconfig
        # are baked here so the soft-degrade probe and the per-kind loop
        # share the exact same auth surface.
        base_argv: list[str] = [kubectl_path]
        if kubeconfig:
            base_argv.extend(["--kubeconfig", kubeconfig])
        if kctx:
            base_argv.extend(["--context", kctx])

        # 1. Confirm `kubectl` itself is reachable.
        if shutil.which(kubectl_path) is None and "/" not in kubectl_path:
            _warn(f"kubectl binary not found on PATH ({kubectl_path}); soft-degrading to 0/0")
            return [], []

        rc, _out, err = _run_kubectl(
            base_argv + ["version", "--client", "--output=json"],
            timeout=15,
        )
        if rc != 0:
            _warn(
                f"`kubectl version --client` exited rc={rc} ({err.strip()[:200]}); "
                f"soft-degrading to 0/0"
            )
            return [], []

        # 2. Confirm there's a current-context (kubeconfig may be empty).
        rc, ctx_out, err = _run_kubectl(
            base_argv + ["config", "current-context"],
            timeout=15,
        )
        if rc != 0:
            _warn(
                f"`kubectl config current-context` exited rc={rc} "
                f"({err.strip()[:200]}); soft-degrading to 0/0"
            )
            return [], []
        current_ctx = ctx_out.strip()
        _log(verbose, f"  [KubectlLayer] current context: {current_ctx!r}")

        # 3. Enumerate api-resources. Anything that fails here is fatal —
        #    without the resource catalog we can't know what to fetch.
        kinds = self._discover_kinds(base_argv, verbose=verbose)
        if not kinds:
            _warn("kubectl api-resources returned no listable kinds; soft-degrading to 0/0")
            return [], []
        _log(verbose, f"  [KubectlLayer] discovered {len(kinds)} listable kinds")

        # 4. Per-kind GET loop. Per-kind failures are logged and skipped.
        nodes: list[dict] = []
        edges: list[dict] = []
        emitted_node_ids: set[str] = set()
        kind_to_node_ids: dict[tuple[str, str], list[str]] = {}

        scanned = 0
        for entry in kinds:
            api_version: str = entry["api_version"]
            kind: str = entry["kind"]
            namespaced: bool = entry["namespaced"]
            resource_name: str = entry["resource_name"]
            group: str = entry["group"]

            if _kind_key(api_version, kind) in skip_kinds:
                _log(verbose, f"  [KubectlLayer] skip {api_version}/{kind} (in skip list)")
                continue

            target = _resource_get_target(group, resource_name)
            argv = base_argv + ["get", target, "-o", "json"]
            if namespaced:
                argv.append("-A")

            rc, out, err = _run_kubectl(argv, timeout=timeout_per_call)
            if rc != 0:
                _log(
                    verbose,
                    f"  [KubectlLayer] kubectl get {target} rc={rc} "
                    f"({err.strip()[:120]}); skipping kind",
                )
                continue
            try:
                doc = json.loads(out)
            except (json.JSONDecodeError, ValueError) as exc:
                _log(
                    verbose,
                    f"  [KubectlLayer] kubectl get {target} JSON decode failed "
                    f"({exc}); skipping kind",
                )
                continue
            items = doc.get("items") or []
            if not isinstance(items, list):
                continue
            if per_kind_limit and len(items) > per_kind_limit:
                _log(
                    verbose,
                    f"  [KubectlLayer] capping {api_version}/{kind} at "
                    f"{per_kind_limit} (was {len(items)})",
                )
                items = items[:per_kind_limit]

            for r in items:
                node = self._build_resource_node(
                    api_version=api_version,
                    kind=kind,
                    namespaced=namespaced,
                    resource=r,
                )
                if not node:
                    continue
                nid = node["id"]
                if nid in emitted_node_ids:
                    continue
                emitted_node_ids.add(nid)
                nodes.append(node)
                kind_to_node_ids.setdefault((api_version, kind), []).append(nid)
            scanned += 1

        _log(
            verbose,
            f"  [KubectlLayer] scanned {scanned} kinds — emitted "
            f"{len(nodes)} k8s_resource nodes",
        )

        # 5. CRDs — emit `crd:<name>` nodes + `defines_kind` edges to whatever
        #    k8s_resource ids the per-kind loop already produced.
        crd_nodes, crd_edges = self._scan_crds(
            base_argv, kind_to_node_ids, verbose=verbose, timeout=timeout_per_call
        )
        nodes.extend(crd_nodes)
        edges.extend(crd_edges)

        _log(
            verbose,
            f"  [KubectlLayer] total: {len(nodes)} nodes / {len(edges)} edges",
        )
        return nodes, edges

    # ----------------------------------------------------------- discovery

    def _discover_kinds(
        self, base_argv: list[str], *, verbose: bool
    ) -> list[dict]:
        """Run ``kubectl api-resources`` and parse the listable kinds.

        Output columns are: NAME SHORTNAMES APIVERSION NAMESPACED KIND VERBS.
        We use ``-o wide --no-headers`` for a stable, header-less form. Any
        kind whose VERBS column doesn't include ``list`` is dropped — without
        ``list`` we can't bulk-fetch.
        """
        rc, out, err = _run_kubectl(
            base_argv + ["api-resources", "-o", "wide", "--no-headers"],
            timeout=60,
        )
        if rc != 0:
            _warn(
                f"`kubectl api-resources` exited rc={rc} ({err.strip()[:200]}); "
                f"soft-degrading to empty kind list"
            )
            return []

        kinds: list[dict] = []
        for raw_line in out.splitlines():
            line = raw_line.rstrip()
            if not line:
                continue
            # `-o wide` produces 6 whitespace-separated columns (SHORTNAMES
            # may be empty). When SHORTNAMES is missing we'd get 5 cols, so
            # parse defensively from the right.
            parts = line.split()
            if len(parts) < 4:
                continue
            verbs = parts[-1]
            kind = parts[-2]
            namespaced = parts[-3].lower() == "true"
            apiversion = parts[-4]
            name = parts[0]
            # Verbs in -o wide are comma-separated inside square brackets,
            # e.g. ``[get list watch]`` on some versions and ``get,list``
            # on others. Be lenient.
            verbs_norm = verbs.strip("[]").replace(",", " ").lower()
            if "list" not in verbs_norm.split():
                continue
            group, _version = _split_api_version(apiversion)
            kinds.append(
                {
                    "api_version": apiversion,
                    "kind": kind,
                    "namespaced": namespaced,
                    "resource_name": name,
                    "group": group,
                }
            )
        return kinds

    # ------------------------------------------------------------- builders

    def _build_resource_node(
        self,
        *,
        api_version: str,
        kind: str,
        namespaced: bool,
        resource: dict,
    ) -> dict | None:
        if not isinstance(resource, dict):
            return None
        meta = resource.get("metadata") or {}
        if not isinstance(meta, dict):
            return None
        name = meta.get("name")
        if not name:
            return None
        ns_raw = meta.get("namespace") or ""
        if not ns_raw and not namespaced:
            ns = "cluster"
        else:
            ns = ns_raw or "cluster"
        rid = f"k8s_resource:{ns}/{kind}/{name}"

        spec = resource.get("spec") if isinstance(resource.get("spec"), dict) else {}
        labels = meta.get("labels") if isinstance(meta.get("labels"), dict) else {}
        annotations = (
            meta.get("annotations") if isinstance(meta.get("annotations"), dict) else {}
        )
        owner_refs = (
            meta.get("ownerReferences")
            if isinstance(meta.get("ownerReferences"), list)
            else []
        )

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

        images: list[str] = []
        pvc_claims: list[str] = []
        secret_refs: list[str] = []
        configmap_refs: list[str] = []
        if kind in {"Deployment", "StatefulSet", "DaemonSet", "Job", "ReplicaSet", "Pod"}:
            images = _extract_container_images(spec)
            pvc_claims = _extract_pod_volume_claims(spec)
            secret_refs, configmap_refs = _extract_secret_configmap_refs(spec)

        pv_backing: dict = {}
        pvc_binding: dict = {}
        if kind == "PersistentVolume":
            pv_backing = _extract_pv_backing(spec)
        if kind == "PersistentVolumeClaim":
            pvc_binding = _extract_pvc_binding(spec)

        # Mirror K8sLayer's selective spec retention so downstream layers see
        # the same shape regardless of which scanner populated the node.
        spec_for_node: dict | Any = (
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

        return {
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
            "source": "kubectl",
        }

    # ------------------------------------------------------------------ CRDs

    def _scan_crds(
        self,
        base_argv: list[str],
        kind_to_node_ids: dict[tuple[str, str], list[str]],
        *,
        verbose: bool,
        timeout: int,
    ) -> tuple[list[dict], list[dict]]:
        rc, out, err = _run_kubectl(
            base_argv + ["get", "crds", "-o", "json"],
            timeout=timeout,
        )
        if rc != 0:
            _log(
                verbose,
                f"  [KubectlLayer] kubectl get crds rc={rc} "
                f"({err.strip()[:120]}); skipping CRD pass",
            )
            return [], []
        try:
            doc = json.loads(out)
        except (json.JSONDecodeError, ValueError) as exc:
            _log(verbose, f"  [KubectlLayer] CRD JSON decode failed ({exc})")
            return [], []
        items = doc.get("items") or []
        if not isinstance(items, list):
            return [], []

        nodes: list[dict] = []
        edges: list[dict] = []
        for crd in items:
            if not isinstance(crd, dict):
                continue
            meta = crd.get("metadata") or {}
            spec = crd.get("spec") or {}
            crd_name = meta.get("name")
            if not crd_name:
                continue
            group = spec.get("group") or ""
            names = spec.get("names") or {}
            kind = names.get("kind") if isinstance(names, dict) else ""
            scope = spec.get("scope") or ""
            versions = spec.get("versions") or []
            crd_id = f"crd:{crd_name}"
            nodes.append(
                {
                    "id": crd_id,
                    "type": "crd",
                    "label": crd_name,
                    "name": crd_name,
                    "group": group,
                    "kind": kind,
                    "scope": scope,
                    "versions": [
                        v.get("name", "")
                        for v in versions
                        if isinstance(v, dict) and v.get("name")
                    ],
                    "source": "kubectl",
                }
            )
            if not (group and kind):
                continue
            for v in versions:
                if not isinstance(v, dict):
                    continue
                vname = v.get("name") or ""
                if not vname:
                    continue
                api = f"{group}/{vname}"
                for rid in kind_to_node_ids.get((api, kind), []):
                    edges.append(
                        {
                            "source": crd_id,
                            "target": rid,
                            "relation": "defines_kind",
                        }
                    )
        return nodes, edges
