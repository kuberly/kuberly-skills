"""StateLayer — Terraform/OpenTofu remote-state inline scanner.

v0.50.1 (2026-05-08): folded the standalone S3 extractor (formerly
``state_extract.py`` writing ``.kuberly/state_<env>.json``) directly into
``StateLayer.scan(ctx)``. Every other layer writes nodes/edges straight to
the LanceDB store; state now follows the same pattern. No sidecar JSON.

For every env discovered under ``components/<env>/``:

  1. Read ``components/<env>/shared-infra.json`` → derive the canonical
     state-bucket name ``${account}-${region}-${cluster}-tf-states``.
  2. Walk ``clouds/<provider>/modules/<name>/`` directories. For each one,
     parse ``terragrunt.hcl`` to read ``key = "..."`` from the
     ``remote_state { config = { ... } }`` block; fall back to the
     convention ``<provider>/<name>/terraform.tfstate`` when the key is
     interpolated or missing.
  3. ``boto3.s3.get_object(Bucket, Key)`` per module — soft-degrade per
     module on ``ClientError`` / corrupt JSON; per-env on
     ``NoCredentialsError``.

Emits:

  * ``tf_state_module:<env>/<rel-module-path>`` — type ``tf_state_module``,
    metadata ``module_path``, ``resource_count``, ``state_key``.
  * ``tf_state_resource:<env>/<rel-module-path>/<address>`` — type
    ``tf_state_resource``. Metadata: ``address``, ``tf_type``, ``name``,
    ``provider``, ``mode``, ``module_path``.

Edges:

  * ``module:<provider>/<name>`` → ``tf_state_module:<env>/<rel>``
    (relation ``has_state``) — when the cold ``code`` layer has populated
    the matching module id.
  * ``tf_state_module`` → ``tf_state_resource`` (``contains``).
  * ``tf_state_resource`` → ``aws:<service>:<id>`` (``tracks``) —
    best-effort: parse ``id`` / ``arn`` out of the resource attribute
    payload, look it up against the live ``AwsLayer`` ids cached in the
    ctx by the orchestrator.

Soft-degrades cleanly when ``boto3`` is missing or AWS creds are unset
(returns ``([], [])`` with a verbose-mode debug print).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .base import Layer


# State-bucket discovery regex (matches the literal ``key = "..."`` in
# terragrunt.hcl ``remote_state { config = { ... } }`` blocks).
_KEY_RE = re.compile(r'\bkey\s*=\s*"([^"]+)"')


def _read_shared_infra(repo_root: Path, env: str) -> dict:
    p = repo_root / "components" / env / "shared-infra.json"
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text())
    except Exception:
        return {}
    if isinstance(data, dict) and "shared-infra" in data:
        inner = data["shared-infra"]
        if isinstance(inner, dict):
            return inner
    if isinstance(data, dict):
        return data
    return {}


def _bucket_from_shared_infra(shared: dict) -> tuple[str, str, str]:
    """Return (bucket, region, account) using the kuberly-stack convention."""
    target = shared.get("target") or {}
    account = str(target.get("account_id") or "")
    region = str(target.get("region") or "")
    cluster = str((target.get("cluster") or {}).get("name") or "")
    bucket = ""
    if account and region and cluster:
        bucket = f"{account}-{region}-{cluster}-tf-states"
    return bucket, region, account


def _module_state_key(module_dir: Path, repo_root: Path) -> str:
    """Read ``key = "..."`` from terragrunt.hcl, else fall back to the
    canonical ``<provider>/<name>/terraform.tfstate`` convention.
    """
    tg = module_dir / "terragrunt.hcl"
    if tg.exists():
        try:
            txt = tg.read_text()
        except Exception:
            txt = ""
        m = _KEY_RE.search(txt)
        if m:
            key = m.group(1)
            # Skip interpolated keys we can't resolve here — fall through.
            if "${" not in key and "}" not in key:
                return key
    try:
        rel = module_dir.relative_to(repo_root).parts
    except ValueError:
        return ""
    if len(rel) >= 4 and rel[0] == "clouds" and rel[2] == "modules":
        return f"{rel[1]}/{rel[3]}/terraform.tfstate"
    return ""


def _iter_module_dirs(repo_root: Path):
    base = repo_root / "clouds"
    if not base.exists():
        return
    for provider in sorted(base.iterdir()):
        if not provider.is_dir() or provider.name.startswith("."):
            continue
        modules = provider / "modules"
        if not modules.exists():
            continue
        for m in sorted(modules.iterdir()):
            if not m.is_dir() or m.name.startswith("."):
                continue
            yield m


def _resources_from_tfstate(payload: dict) -> list[dict]:
    """Extract minimal address/type/name/provider/mode tuples from a tfstate.

    Each instance returns its raw attributes too so we can later mine for
    AWS ids/arns to build cross-layer ``tracks`` edges.
    """
    out: list[dict] = []
    for res in payload.get("resources") or []:
        if not isinstance(res, dict):
            continue
        instances = res.get("instances") or []
        if not isinstance(instances, list) or not instances:
            continue
        rtype = str(res.get("type") or "")
        rname = str(res.get("name") or "")
        rmod = str(res.get("module") or "")
        rprov = str(res.get("provider") or "")
        rmode = str(res.get("mode") or "managed")
        for idx, inst in enumerate(instances):
            base_addr = f"{rtype}.{rname}"
            if rmod:
                base_addr = f"{rmod}.{base_addr}"
            attrs = {}
            if isinstance(inst, dict):
                if inst.get("index_key") not in (None, ""):
                    base_addr = f"{base_addr}[{inst['index_key']!r}]"
                attrs = inst.get("attributes") or {}
                if not isinstance(attrs, dict):
                    attrs = {}
            out.append(
                {
                    "address": base_addr,
                    "type": rtype,
                    "name": rname,
                    "provider": rprov,
                    "mode": rmode,
                    "index": idx,
                    "attrs": attrs,
                }
            )
    return out


# AWS service-name → id-attribute fallback list (when the resource type
# doesn't follow the obvious ``aws_<service>`` pattern). Keys are
# ``aws_<...>`` resource-type prefixes; values are the AwsLayer service
# token used in the ``aws:<service>:<id>`` node id format. The default
# rule strips the leading ``aws_`` and uses that as the service token.
_AWS_TYPE_TO_SERVICE: dict[str, str] = {
    "aws_eks_cluster": "eks",
    "aws_eks_node_group": "eks_node_group",
    "aws_eks_addon": "eks_addon",
    "aws_vpc": "vpc",
    "aws_subnet": "subnet",
    "aws_security_group": "sg",
    "aws_internet_gateway": "igw",
    "aws_nat_gateway": "nat",
    "aws_route_table": "rtb",
    "aws_vpc_endpoint": "vpce",
    "aws_ebs_volume": "ebs",
    "aws_iam_role": "iam_role",
    "aws_iam_policy": "iam_policy",
    "aws_iam_user": "iam_user",
    "aws_s3_bucket": "s3",
    "aws_db_instance": "rds",
    "aws_rds_cluster": "rds_cluster",
    "aws_lambda_function": "lambda",
    "aws_ecs_cluster": "ecs",
    "aws_ecs_service": "ecs_service",
    "aws_ecr_repository": "ecr_repo",
    "aws_dynamodb_table": "dynamodb",
    "aws_secretsmanager_secret": "secret",
    "aws_kms_key": "kms",
    "aws_sns_topic": "sns",
    "aws_sqs_queue": "sqs",
    "aws_acm_certificate": "acm",
    "aws_route53_zone": "r53_zone",
    "aws_route53_record": "r53_record",
    "aws_cloudwatch_log_group": "log_group",
}


def _candidate_aws_ids(res: dict) -> list[tuple[str, str]]:
    """Best-effort: produce ``(service, id)`` candidates from a tfstate
    resource so we can match against AwsLayer-emitted ``aws:<service>:<id>``
    node ids. Returns at most a handful of plausible candidates.
    """
    rtype = str(res.get("type") or "")
    if not rtype.startswith("aws_"):
        return []
    attrs = res.get("attrs") or {}
    if not isinstance(attrs, dict):
        return []
    service = _AWS_TYPE_TO_SERVICE.get(rtype, rtype[len("aws_"):])
    candidates: list[tuple[str, str]] = []
    # Common id attributes in order of preference.
    for key in ("id", "arn", "name", "function_name", "bucket", "cluster_name"):
        val = attrs.get(key)
        if not val:
            continue
        sval = str(val)
        if sval and len(sval) < 256:
            candidates.append((service, sval))
    return candidates


class StateLayer(Layer):
    name = "state"
    refresh_trigger = "manual"

    def scan(self, ctx: dict) -> tuple[list[dict], list[dict]]:
        repo_root = Path(ctx.get("repo_root", ".")).resolve()
        verbose = bool(ctx.get("verbose"))
        existing_module_ids: set[str] = set(ctx.get("_existing_module_ids", set()))

        # Pre-compute the AwsLayer node-id index so cross-layer ``tracks``
        # edges land only when the target exists. The orchestrator gives us
        # ``graph_store`` directly; cheaper than re-opening the store.
        aws_ids: set[str] = set()
        store = ctx.get("graph_store")
        if store is not None:
            try:
                for n in store.all_nodes():
                    nid = str(n.get("id") or "")
                    if nid.startswith("aws:"):
                        aws_ids.add(nid)
            except Exception:
                aws_ids = set()

        try:
            import boto3  # type: ignore[import-not-found]
            from botocore.exceptions import (  # type: ignore[import-not-found]
                ClientError,
                NoCredentialsError,
            )
        except Exception as exc:
            if verbose:
                print(f"  [StateLayer] boto3 missing: {exc} — skipping")
            return [], []

        comp_dir = repo_root / "components"
        envs: list[str] = []
        if comp_dir.exists():
            envs = sorted(
                p.name
                for p in comp_dir.iterdir()
                if p.is_dir() and not p.name.startswith(".")
            )
        if not envs:
            if verbose:
                print("  [StateLayer] no envs under components/ — skipping")
            return [], []

        nodes: list[dict] = []
        edges: list[dict] = []

        # Cache S3 clients per region so we don't pay the construction cost
        # for every module.
        s3_clients: dict[str, object] = {}

        for env in envs:
            shared = _read_shared_infra(repo_root, env)
            bucket, region, _account = _bucket_from_shared_infra(shared)
            if not bucket:
                if verbose:
                    print(
                        f"  [StateLayer] env={env}: no shared-infra bucket; skipping"
                    )
                continue
            region = region or "eu-west-1"
            try:
                if region not in s3_clients:
                    s3_clients[region] = boto3.client("s3", region_name=region)
                s3 = s3_clients[region]
            except Exception as exc:
                if verbose:
                    print(
                        f"  [StateLayer] env={env}: S3 client init failed: {exc}"
                    )
                continue

            extracted = 0
            skipped = 0

            for module_dir in _iter_module_dirs(repo_root):
                try:
                    rel_dir = module_dir.relative_to(repo_root)
                except ValueError:
                    continue
                key = _module_state_key(module_dir, repo_root)
                if not key:
                    skipped += 1
                    continue
                try:
                    obj = s3.get_object(Bucket=bucket, Key=key)
                    body = obj["Body"].read()
                    try:
                        state_doc = json.loads(body)
                    except Exception:
                        skipped += 1
                        continue
                except ClientError as exc:
                    code = (exc.response.get("Error") or {}).get("Code") or ""
                    if code in {"NoSuchKey", "404", "NotFound"}:
                        skipped += 1
                        continue
                    if verbose:
                        print(
                            f"  [StateLayer] env={env} {rel_dir}: "
                            f"S3 ClientError {code or exc}"
                        )
                    skipped += 1
                    continue
                except NoCredentialsError as exc:
                    if verbose:
                        print(
                            f"  [StateLayer] env={env}: no AWS creds — abort env"
                        )
                    break
                except Exception as exc:  # noqa: BLE001 — soft-degrade per module
                    if verbose:
                        print(
                            f"  [StateLayer] env={env} {rel_dir}: {exc}"
                        )
                    skipped += 1
                    continue

                if not isinstance(state_doc, dict):
                    skipped += 1
                    continue
                resources = _resources_from_tfstate(state_doc)
                if not resources:
                    skipped += 1
                    continue

                module_path = str(rel_dir)
                module_node_id = f"tf_state_module:{env}/{module_path}"
                nodes.append(
                    {
                        "id": module_node_id,
                        "type": "tf_state_module",
                        "label": f"{env}/{module_path}",
                        "env": env,
                        "module_path": module_path,
                        "state_key": key,
                        "state_bucket": bucket,
                        "resource_count": len(resources),
                    }
                )

                # Cross-layer link: cold module:<provider>/<name> →
                # tf_state_module:<env>/<rel-path>.
                segs = module_path.strip("/").split("/")
                if (
                    len(segs) >= 4
                    and segs[0] == "clouds"
                    and segs[2] == "modules"
                ):
                    cold_module_id = f"module:{segs[1]}/{segs[3]}"
                    if (
                        not existing_module_ids
                        or cold_module_id in existing_module_ids
                    ):
                        edges.append(
                            {
                                "source": cold_module_id,
                                "target": module_node_id,
                                "relation": "has_state",
                            }
                        )

                for res in resources:
                    addr = res.get("address") or ""
                    if not addr:
                        continue
                    rid = f"tf_state_resource:{env}/{module_path}/{addr}"
                    nodes.append(
                        {
                            "id": rid,
                            "type": "tf_state_resource",
                            "label": addr,
                            "env": env,
                            "address": addr,
                            "tf_type": res.get("type", ""),
                            "tf_name": res.get("name", ""),
                            "provider": res.get("provider", ""),
                            "mode": res.get("mode", ""),
                            "module_path": module_path,
                        }
                    )
                    edges.append(
                        {
                            "source": module_node_id,
                            "target": rid,
                            "relation": "contains",
                        }
                    )
                    # AWS cross-layer ``tracks`` edges (best-effort).
                    if aws_ids:
                        for service, val in _candidate_aws_ids(res):
                            cand = f"aws:{service}:{val}"
                            if cand in aws_ids:
                                edges.append(
                                    {
                                        "source": rid,
                                        "target": cand,
                                        "relation": "tracks",
                                    }
                                )
                                break  # one tracks-edge per resource is plenty

                extracted += 1

            if verbose:
                print(
                    f"  [StateLayer] env={env} bucket={bucket} "
                    f"extracted={extracted} skipped={skipped}"
                )

        if verbose:
            print(
                f"  [StateLayer] emitted {len(nodes)} nodes / {len(edges)} edges"
            )
        return nodes, edges
