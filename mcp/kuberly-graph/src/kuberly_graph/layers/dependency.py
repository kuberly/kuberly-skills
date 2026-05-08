"""DependencyLayer — derive cross-layer edges from the populated GraphStore.

Runs LAST (after every other layer). Pure structural matching against
whatever nodes/edges are already in the store; never calls live MCP. Empty-
store tolerant — emits whatever edges are derivable, skips the rest in
silence.

Edges produced (all tagged ``layer="dependency"`` so re-runs can
``replace_layer`` cleanly):

K8s ownership chain (from Pod / ReplicaSet ``ownerReferences``):
  Pod  →  ReplicaSet | StatefulSet | DaemonSet | Job   (relation ``owned_by``)
  ReplicaSet →  Deployment                              (relation ``owned_by``)

Karpenter chain (from Pod ``spec.nodeName`` and labels):
  Pod         →  Node            (``runs_on``)
  Node        →  NodeClaim       (``claimed_by``)
  NodeClaim   →  NodePool        (``from_pool``)
  NodePool    →  EC2NodeClass    (``uses_class``)

Cross-layer to observability (heuristic name / label match):
  Pod  →  log_template     (``emits``)
  Pod  →  metric           (``exposes``)
  Pod  →  service          (``traced_as``)
  Node →  log_template     (``emits``)     # kubelet/kernel/kube-proxy
  Node →  metric           (``exposes``)   # node-exporter

Cross-layer IaC ↔ live:
  rendered_resource → k8s_resource   (``rendered_into``)
  application       → argo_app       (``tracked_by``)
  module            → resource       (``state_owns``)   # dedup vs StateLayer
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Iterable

from .base import Layer

# Pod-name suffix produced by ReplicaSet / StatefulSet / etc. Strip it so we
# can match a pod to its rendered/log-template service id.
_POD_SUFFIX_RE = re.compile(r"-[a-z0-9]{5,}(?:-[a-z0-9]{5,})?$")


def _strip_pod_suffix(name: str) -> str:
    if not name:
        return ""
    cleaned = _POD_SUFFIX_RE.sub("", name)
    return cleaned or name


def _open_store_from_ctx(ctx: dict):
    """Return the GraphStore, preferring the one orchestrator stashed in ctx
    (so we read the freshly-replaced layers in this run). Re-open from
    ``persist_dir`` when called outside the orchestrator (e.g. a stand-alone
    ``regenerate_layer dependency`` call).
    """
    store = ctx.get("graph_store")
    if store is not None:
        return store
    persist_dir = ctx.get("persist_dir") or str(Path(ctx.get("repo_root", ".")) / ".kuberly")
    from ..store import open_store

    return open_store(Path(persist_dir))


def _index_k8s_resources(nodes: Iterable[dict]):
    """Build (kind, ns, name) → node_id and per-kind buckets for fast lookup."""
    by_id: dict[tuple[str, str, str], dict] = {}
    by_kind: dict[str, list[dict]] = defaultdict(list)
    for n in nodes:
        if n.get("type") != "k8s_resource":
            continue
        kind = str(n.get("kind") or "")
        name = str(n.get("name") or "")
        ns = str(n.get("namespace") or "")
        if not kind or not name:
            continue
        by_id[(kind, ns, name)] = n
        by_kind[kind].append(n)
    return by_id, by_kind


_OWNED_KINDS = {"ReplicaSet", "StatefulSet", "DaemonSet", "Job", "Deployment"}


class DependencyLayer(Layer):
    name = "dependency"
    refresh_trigger = "manual"

    def scan(self, ctx: dict) -> tuple[list[dict], list[dict]]:
        verbose = bool(ctx.get("verbose"))
        store = _open_store_from_ctx(ctx)

        try:
            all_nodes = store.all_nodes()
            all_edges = store.all_edges()
        except Exception as exc:
            if verbose:
                print(f"  [DependencyLayer] store read failed: {exc}")
            return [], []

        if not all_nodes:
            if verbose:
                print("  [DependencyLayer] store empty — nothing to wire")
            return [], []

        node_ids: set[str] = {n["id"] for n in all_nodes if n.get("id")}
        nodes_by_type: dict[str, list[dict]] = defaultdict(list)
        for n in all_nodes:
            t = str(n.get("type") or "")
            if t:
                nodes_by_type[t].append(n)

        k8s_by_id, k8s_by_kind = _index_k8s_resources(all_nodes)

        out_edges: list[dict] = []
        seen_edge_keys: set[tuple[str, str, str]] = set()

        def _emit(source: str, target: str, relation: str, **extra) -> None:
            if not source or not target:
                return
            if source not in node_ids or target not in node_ids:
                return
            key = (source, target, relation)
            if key in seen_edge_keys:
                return
            seen_edge_keys.add(key)
            edge = {"source": source, "target": target, "relation": relation}
            edge.update(extra)
            out_edges.append(edge)

        # ---- K8s ownership chain --------------------------------------------------
        for kind in ("Pod", "ReplicaSet"):
            for n in k8s_by_kind.get(kind, []):
                ns = str(n.get("namespace") or "")
                refs = n.get("owner_references") or []
                if not isinstance(refs, list):
                    continue
                for ref in refs:
                    if not isinstance(ref, dict):
                        continue
                    o_kind = str(ref.get("kind") or "")
                    o_name = str(ref.get("name") or "")
                    if not o_kind or not o_name:
                        continue
                    if o_kind not in _OWNED_KINDS:
                        continue
                    target_node = k8s_by_id.get((o_kind, ns, o_name))
                    if target_node is None:
                        continue
                    _emit(n["id"], target_node["id"], "owned_by")

        # ---- Pod → Node + Karpenter chain ----------------------------------------
        nodes_by_provider_id: dict[str, dict] = {}
        for nd in k8s_by_kind.get("Node", []):
            pid = str(nd.get("provider_id") or "")
            if pid:
                nodes_by_provider_id[pid] = nd

        for pod in k8s_by_kind.get("Pod", []):
            node_name = str(pod.get("node_name") or "")
            if not node_name:
                continue
            node_node = k8s_by_id.get(("Node", "", node_name))
            if node_node is None:
                continue
            _emit(pod["id"], node_node["id"], "runs_on")

        # Node → NodeClaim. Match by Node label `karpenter.sh/nodeclaim=<name>`,
        # or fall back to NodeClaim status.providerID == Node spec.providerID
        # when the label is absent.
        nodeclaims = k8s_by_kind.get("NodeClaim", [])
        for nd in k8s_by_kind.get("Node", []):
            labels = nd.get("labels") if isinstance(nd.get("labels"), dict) else {}
            claim_name = str(labels.get("karpenter.sh/nodeclaim") or "")
            target = None
            if claim_name:
                target = k8s_by_id.get(("NodeClaim", "", claim_name))
            if target is None:
                pid = str(nd.get("provider_id") or "")
                if pid:
                    for nc in nodeclaims:
                        nc_labels = nc.get("labels") if isinstance(nc.get("labels"), dict) else {}
                        if str(nc_labels.get("karpenter.k8s.aws/instance-id") or "") and pid.endswith(
                            str(nc_labels.get("karpenter.k8s.aws/instance-id"))
                        ):
                            target = nc
                            break
            if target is not None:
                _emit(nd["id"], target["id"], "claimed_by")

        # NodeClaim → NodePool (NodeClaim labels carry karpenter.sh/nodepool).
        for nc in nodeclaims:
            labels = nc.get("labels") if isinstance(nc.get("labels"), dict) else {}
            pool_name = str(labels.get("karpenter.sh/nodepool") or "")
            if not pool_name:
                continue
            pool = k8s_by_id.get(("NodePool", "", pool_name))
            if pool is None:
                continue
            _emit(nc["id"], pool["id"], "from_pool")

        # NodePool → EC2NodeClass (NodePool spec.template.spec.nodeClassRef.name).
        for pool in k8s_by_kind.get("NodePool", []):
            cls_name = str(pool.get("node_class_ref") or "")
            if not cls_name:
                continue
            cls = k8s_by_id.get(("EC2NodeClass", "", cls_name))
            if cls is None:
                continue
            _emit(pool["id"], cls["id"], "uses_class")

        # ---- Pod / Node → observability ------------------------------------------
        # Pre-index log_templates by service for O(pods × services_per_match).
        log_templates = nodes_by_type.get("log_template", [])
        log_by_service: dict[str, list[dict]] = defaultdict(list)
        for lt in log_templates:
            svc = str(lt.get("service") or "")
            if svc:
                log_by_service[svc].append(lt)

        scrape_targets = nodes_by_type.get("scrape_target", [])
        scrape_by_pod: dict[str, list[dict]] = defaultdict(list)
        scrape_by_instance: dict[str, list[dict]] = defaultdict(list)
        for st in scrape_targets:
            p = str(st.get("pod") or "")
            if p:
                scrape_by_pod[p].append(st)
            inst = str(st.get("instance") or "")
            if inst:
                scrape_by_instance[inst].append(st)

        # scrape_target → metric edges already exist (MetricsLayer), so to wire
        # Pod → metric we walk all edges with relation=produces from scrape_target.
        scrape_to_metrics: dict[str, list[str]] = defaultdict(list)
        for e in all_edges:
            if e.get("relation") != "produces":
                continue
            src = str(e.get("source") or "")
            tgt = str(e.get("target") or "")
            if src.startswith("scrape_target:") and tgt.startswith("metric:"):
                scrape_to_metrics[src].append(tgt)

        services = nodes_by_type.get("service", [])
        services_by_name = {str(s.get("service") or s.get("name") or ""): s for s in services}

        for pod in k8s_by_kind.get("Pod", []):
            pod_name = str(pod.get("name") or "")
            labels = pod.get("labels") if isinstance(pod.get("labels"), dict) else {}
            app_label = ""
            if isinstance(labels, dict):
                app_label = str(
                    labels.get("app.kubernetes.io/name")
                    or labels.get("app")
                    or ""
                )
            stripped = _strip_pod_suffix(pod_name)
            candidates = {x for x in (app_label, stripped, pod_name) if x}

            # Pod → log_template (emits)
            for cand in candidates:
                for lt in log_by_service.get(cand, []):
                    _emit(pod["id"], lt["id"], "emits")

            # Pod → metric (exposes) via scrape_target.pod match
            for st in scrape_by_pod.get(pod_name, []):
                for metric_id in scrape_to_metrics.get(st["id"], []):
                    _emit(pod["id"], metric_id, "exposes")

            # Pod → service (traced_as) by service-label / app match
            for cand in candidates:
                svc = services_by_name.get(cand)
                if svc is not None:
                    _emit(pod["id"], svc["id"], "traced_as")

        for nd in k8s_by_kind.get("Node", []):
            nd_name = str(nd.get("name") or "")
            # Node → log_template: kubelet / kernel / kube-proxy templates
            for svc_key in ("kubelet", "kernel", "kube-proxy", "node"):
                for lt in log_by_service.get(svc_key, []):
                    _emit(nd["id"], lt["id"], "emits")
            # Node → metric via scrape_target.instance heuristic (instance ==
            # node name or node:port).
            inst_keys = [nd_name]
            for inst, sts in scrape_by_instance.items():
                if inst == nd_name or inst.startswith(nd_name + ":"):
                    inst_keys.append(inst)
            for inst in inst_keys:
                for st in scrape_by_instance.get(inst, []):
                    for metric_id in scrape_to_metrics.get(st["id"], []):
                        _emit(nd["id"], metric_id, "exposes")

        # ---- rendered_resource → k8s_resource ------------------------------------
        for rr in nodes_by_type.get("rendered_resource", []):
            kind = str(rr.get("kind") or "")
            name = str(rr.get("name") or "")
            ns_hint = str(rr.get("namespace") or "")
            if not kind or not name:
                continue
            target = k8s_by_id.get((kind, ns_hint, name))
            if target is None:
                # Best-effort: find a k8s_resource with same Kind+name regardless of ns.
                for cand in k8s_by_kind.get(kind, []):
                    if str(cand.get("name") or "") == name:
                        target = cand
                        break
            if target is None:
                continue
            _emit(rr["id"], target["id"], "rendered_into")

        # ---- application → argo_app ---------------------------------------------
        argo_apps = nodes_by_type.get("argo_app", [])
        argo_by_name = defaultdict(list)
        for aa in argo_apps:
            argo_by_name[str(aa.get("name") or "")].append(aa)
        for app in nodes_by_type.get("application", []):
            app_id = str(app.get("id") or "")
            try:
                _, body = app_id.split(":", 1)
                _env, app_name = body.split("/", 1)
            except ValueError:
                continue
            for aa in argo_by_name.get(app_name, []):
                _emit(app_id, aa["id"], "tracked_by")

        # ---- module → resource (state_owns) — best-effort, dedup vs StateLayer.
        existing_state_owns: set[tuple[str, str]] = {
            (str(e.get("source") or ""), str(e.get("target") or ""))
            for e in all_edges
            if e.get("relation") == "state_owns"
        }
        for res in nodes_by_type.get("resource", []):
            module_path = str(res.get("module_path") or "")
            segs = module_path.strip("/").split("/")
            if len(segs) >= 4 and segs[0] == "clouds" and segs[2] == "modules":
                cand = f"module:{segs[1]}/{segs[3]}"
                if cand in node_ids and (cand, res["id"]) not in existing_state_owns:
                    _emit(cand, res["id"], "state_owns")

        if verbose:
            print(f"  [DependencyLayer] emitted 0 nodes / {len(out_edges)} edges")
        return [], out_edges
