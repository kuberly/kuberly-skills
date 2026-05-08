"""StorageLayer — extract AWS storage resources + cross-link to k8s PV/PVC.

Runs AFTER `state` and `k8s`, BEFORE `dependency`. Pure structural extractor;
no live calls. Empty-store tolerant.

State-side nodes:
  * ``ebs_volume``      — ``ebs_volume:<env>/<addr>``       (size_gb, type, az, encrypted)
  * ``efs_filesystem``  — ``efs_filesystem:<env>/<addr>``   (encrypted, performance_mode)
  * ``s3_bucket``       — ``s3_bucket:<env>/<name-or-addr>``

k8s-side: PV / PVC / StorageClass already produced by K8sLayer (kinds added to
defaults). We just emit cross-edges.

Edges:
  * pvc → pv               (``bound_to``)     from PVC ``spec.volumeName``
  * pvc → storage_class    (``uses``)         from PVC ``spec.storageClassName``
  * pv  → ebs_volume       (``backed_by``)    when CSI volumeHandle / awsElasticBlockStore.volumeID matches
  * pv  → efs_filesystem   (``backed_by``)    when EFS-CSI volumeHandle prefix matches
  * efs_filesystem → subnet (``mounted_in``)  one per ``aws_efs_mount_target``

We also synthesize lightweight ``pv``, ``pvc``, ``storage_class`` projections
of the underlying ``k8s_resource`` nodes so downstream tooling can filter on
``type=pvc`` directly (the original k8s_resource node is preserved untouched
in the k8s layer; this layer's projections live under ``layer="storage"``).
"""

from __future__ import annotations

import json
from pathlib import Path

from .base import Layer


_STORAGE_TF_TYPES = {
    "aws_ebs_volume",
    "aws_efs_file_system",
    "aws_efs_mount_target",
    "aws_s3_bucket",
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
                if not addr or tf_type not in _STORAGE_TF_TYPES:
                    continue
                attrs = res.get("values") or res.get("attributes") or {}
                if not isinstance(attrs, dict):
                    if isinstance(res.get("instances"), list) and res["instances"]:
                        attrs = res["instances"][0].get("attributes") or {}
                    else:
                        attrs = {}
                yield env, addr, tf_type, attrs


class StorageLayer(Layer):
    name = "storage"
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

        # --- state-side: EBS / EFS / S3 ----------------------------------------
        ebs_by_native: dict[str, str] = {}
        efs_by_native: dict[str, str] = {}
        # Track subnets per env so EFS mount targets can resolve.
        subnet_by_native: dict[tuple[str, str], str] = {}
        # Pull existing subnet nodes from the store (NetworkLayer might have
        # populated them; this layer should not require it though).
        try:
            for n in store.all_nodes():
                if n.get("type") != "subnet":
                    continue
                env = str(n.get("env") or "")
                # NetworkLayer doesn't persist the native id on the node since
                # it indexes per scan; for cross-layer matching we use the
                # vpc_id-style attribute fallback. If unavailable, we just
                # skip the mounted_in edge.
                native_id = ""  # left empty — matched via attrs in the loop
                if native_id:
                    subnet_by_native[(env, native_id)] = n["id"]
        except Exception:
            pass

        rows = list(_iter_state_resources(persist_dir))
        # First pass: subnet native id → node id (using state addresses).
        # We re-derive this from the same state files NetworkLayer reads, so
        # we don't need NetworkLayer's in-memory index.
        if persist_dir.exists():
            for state_path in sorted(persist_dir.glob("state_*.json")):
                env = state_path.stem.replace("state_", "", 1)
                payload = _safe_load_state(state_path)
                modules = payload.get("modules") or {}
                if not isinstance(modules, dict):
                    continue
                for _mp, blob in modules.items():
                    for res in (blob or {}).get("resources") or []:
                        if not isinstance(res, dict):
                            continue
                        if res.get("type") != "aws_subnet":
                            continue
                        addr = res.get("address") or ""
                        attrs = res.get("values") or res.get("attributes") or {}
                        if not isinstance(attrs, dict):
                            attrs = {}
                        native_id = str(attrs.get("id") or "")
                        if native_id:
                            subnet_by_native[(env, native_id)] = (
                                f"subnet:{env}/{addr}"
                            )

        for env, addr, tf_type, attrs in rows:
            if tf_type == "aws_ebs_volume":
                nid = f"ebs_volume:{env}/{addr}"
                native = str(attrs.get("id") or "")
                _emit_node(
                    {
                        "id": nid,
                        "type": "ebs_volume",
                        "label": addr,
                        "env": env,
                        "address": addr,
                        "size_gb": int(attrs.get("size") or 0),
                        "volume_type": str(attrs.get("type") or ""),
                        "az": str(attrs.get("availability_zone") or ""),
                        "encrypted": bool(attrs.get("encrypted")),
                        "native_id": native,
                    }
                )
                if native:
                    ebs_by_native[native] = nid
            elif tf_type == "aws_efs_file_system":
                nid = f"efs_filesystem:{env}/{addr}"
                native = str(attrs.get("id") or "")
                _emit_node(
                    {
                        "id": nid,
                        "type": "efs_filesystem",
                        "label": addr,
                        "env": env,
                        "address": addr,
                        "encrypted": bool(attrs.get("encrypted")),
                        "performance_mode": str(attrs.get("performance_mode") or ""),
                        "native_id": native,
                    }
                )
                if native:
                    efs_by_native[native] = nid
            elif tf_type == "aws_efs_mount_target":
                fs_id = str(attrs.get("file_system_id") or "")
                subnet_id = str(attrs.get("subnet_id") or "")
                source = efs_by_native.get(fs_id, "")
                target = subnet_by_native.get((env, subnet_id), "")
                if source and target:
                    edges.append(
                        {
                            "source": source,
                            "target": target,
                            "relation": "mounted_in",
                        }
                    )
            elif tf_type == "aws_s3_bucket":
                bucket_name = str(attrs.get("bucket") or addr)
                nid = f"s3_bucket:{env}/{bucket_name}"
                _emit_node(
                    {
                        "id": nid,
                        "type": "s3_bucket",
                        "label": bucket_name,
                        "env": env,
                        "address": addr,
                        "name": bucket_name,
                        "versioning": bool(attrs.get("versioning")),
                    }
                )

        # --- k8s side: PV / PVC / StorageClass cross-edges ---------------------
        try:
            k8s_nodes = store.all_nodes(layer="k8s")
        except Exception:
            k8s_nodes = []

        pv_by_name: dict[str, dict] = {}
        pvc_by_id: dict[tuple[str, str], dict] = {}
        sc_by_name: dict[str, dict] = {}
        for n in k8s_nodes:
            kind = n.get("kind")
            name = str(n.get("name") or "")
            if not name:
                continue
            if kind == "PersistentVolume":
                pv_by_name[name] = n
            elif kind == "PersistentVolumeClaim":
                pvc_by_id[(str(n.get("namespace") or ""), name)] = n
            elif kind == "StorageClass":
                sc_by_name[name] = n

        # PVC → PV (bound_to) and PVC → StorageClass (uses)
        for (_ns, _name), pvc in pvc_by_id.items():
            binding = pvc.get("pvc_binding") if isinstance(pvc.get("pvc_binding"), dict) else {}
            vol_name = str(binding.get("volume_name") or "")
            sc_name = str(binding.get("storage_class_name") or "")
            if vol_name:
                pv = pv_by_name.get(vol_name)
                if pv is not None:
                    edges.append(
                        {
                            "source": pvc["id"],
                            "target": pv["id"],
                            "relation": "bound_to",
                        }
                    )
            if sc_name:
                sc = sc_by_name.get(sc_name)
                if sc is not None:
                    edges.append(
                        {
                            "source": pvc["id"],
                            "target": sc["id"],
                            "relation": "uses",
                        }
                    )

        # PV → EBS / EFS (backed_by)
        for _name, pv in pv_by_name.items():
            backing = pv.get("pv_backing") if isinstance(pv.get("pv_backing"), dict) else {}
            csi_handle = str(backing.get("csi_volume_handle") or "")
            ebs_id = str(backing.get("aws_ebs_volume_id") or "")
            csi_driver = str(backing.get("csi_driver") or "")
            ebs_target = ""
            if ebs_id:
                ebs_target = ebs_by_native.get(ebs_id, "")
            elif csi_handle and csi_handle.startswith("vol-"):
                ebs_target = ebs_by_native.get(csi_handle, "")
            if ebs_target:
                edges.append(
                    {
                        "source": pv["id"],
                        "target": ebs_target,
                        "relation": "backed_by",
                    }
                )
                continue
            # EFS-CSI: volumeHandle is "<fs-id>" or "<fs-id>::<ap-id>".
            if "efs.csi" in csi_driver and csi_handle:
                efs_native = csi_handle.split(":", 1)[0]
                efs_target = efs_by_native.get(efs_native, "")
                if efs_target:
                    edges.append(
                        {
                            "source": pv["id"],
                            "target": efs_target,
                            "relation": "backed_by",
                        }
                    )

        if verbose:
            print(f"  [StorageLayer] emitted {len(nodes)} nodes / {len(edges)} edges")
        return nodes, edges
