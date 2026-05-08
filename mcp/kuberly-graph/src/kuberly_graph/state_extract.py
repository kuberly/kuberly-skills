"""S3 tfstate side-car extractor.

Reads each module's ``terragrunt.hcl`` to derive the state-bucket name + key
(or falls back to the convention ``aws/<module>/terraform.tfstate``), pulls
each tfstate JSON via ``boto3.s3.get_object``, and aggregates the resource
slice into ``.kuberly/state_<env>.json`` so :class:`StateLayer` can ingest
it without shelling out to ``terragrunt``.

Soft-degrades on:
  * missing ``boto3`` (returns ``error="boto3 not installed"``);
  * missing AWS creds / wrong account (per-module skip with logged exception);
  * missing bucket / key (per-module skip);
  * missing ``components/<env>/shared-infra.json`` for bucket discovery
    (falls back to the canonical
    ``${account_id}-${region}-${cluster_name}-tf-states`` template).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


# State-bucket discovery regex (matches the literal ``key = "..."`` in
# terragrunt.hcl ``remote_state { config = { ... } }`` blocks).
_KEY_RE = re.compile(r'\bkey\s*=\s*"([^"]+)"')
_BUCKET_RE = re.compile(r'\bbucket\s*=\s*"([^"]+)"')


def _read_shared_infra(repo_root: Path, env: str) -> dict:
    p = repo_root / "components" / env / "shared-infra.json"
    if not p.exists():
        return {}
    try:
        text = p.read_text()
        data = json.loads(text)
    except Exception:
        return {}
    if isinstance(data, dict) and "shared-infra" in data:
        return data["shared-infra"]
    if isinstance(data, dict):
        return data
    return {}


def _bucket_from_shared_infra(shared: dict) -> tuple[str, str, str]:
    """Return (bucket, region, account) tuples derived from shared-infra.json
    using the kuberly-stack naming convention.
    """
    target = shared.get("target") or {}
    account = str(target.get("account_id") or "")
    region = str(target.get("region") or "")
    cluster = str((target.get("cluster") or {}).get("name") or "")
    bucket = ""
    if account and region and cluster:
        bucket = f"{account}-{region}-{cluster}-tf-states"
    return bucket, region, account


def _module_state_key(module_dir: Path, repo_root: Path) -> str:
    """Try to read ``key = "..."`` from terragrunt.hcl. Fall back to
    convention: ``<provider>/<module>/terraform.tfstate``.
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
            # Filter out interpolated keys we can't resolve here — they
            # contain ``${...}``. Fall through to convention in that case.
            if "${" not in key and "}" not in key:
                return key
    # Convention: clouds/<provider>/modules/<name>/ -> <provider>/<name>/terraform.tfstate
    try:
        rel = module_dir.relative_to(repo_root).parts
    except ValueError:
        return ""
    if (
        len(rel) >= 4
        and rel[0] == "clouds"
        and rel[2] == "modules"
    ):
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
    """Pull a minimal address/type/name/provider/mode tuple out of a tfstate
    JSON. Resources without instances are skipped.
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
            if isinstance(inst, dict) and inst.get("index_key") not in (None, ""):
                base_addr = f"{base_addr}[{inst['index_key']!r}]"
            out.append(
                {
                    "address": base_addr,
                    "type": rtype,
                    "name": rname,
                    "provider": rprov,
                    "mode": rmode,
                    "index": idx,
                }
            )
    return out


def extract_states_from_s3(
    repo_root: str,
    env: str,
    region: str = "eu-west-1",
    persist_dir: str = ".kuberly",
) -> dict:
    """Aggregate every tfstate under ``clouds/**/modules/**/`` into
    ``.kuberly/state_<env>.json`` by pulling from S3 directly.

    Returns ``{path, modules_extracted, modules_skipped, errors, bucket}``.
    """
    repo = Path(repo_root).resolve()
    persist = Path(persist_dir).resolve()
    persist.mkdir(parents=True, exist_ok=True)

    try:
        import boto3  # type: ignore[import-not-found]
        from botocore.exceptions import ClientError, NoCredentialsError  # type: ignore[import-not-found]
    except Exception as exc:
        return {
            "path": "",
            "modules_extracted": 0,
            "modules_skipped": 0,
            "errors": [f"boto3 not installed: {exc}"],
            "bucket": "",
        }

    shared = _read_shared_infra(repo, env)
    bucket, infra_region, account = _bucket_from_shared_infra(shared)
    region = region or infra_region or "eu-west-1"

    if not bucket:
        return {
            "path": "",
            "modules_extracted": 0,
            "modules_skipped": 0,
            "errors": [
                f"could not derive state bucket from components/{env}/shared-infra.json"
            ],
            "bucket": "",
        }

    s3 = boto3.client("s3", region_name=region)
    out: dict = {
        "env": env,
        "bucket": bucket,
        "region": region,
        "modules": {},
    }
    extracted = 0
    skipped = 0
    errors: list[str] = []

    for module_dir in _iter_module_dirs(repo):
        try:
            rel_dir = module_dir.relative_to(repo)
        except ValueError:
            continue
        key = _module_state_key(module_dir, repo)
        if not key:
            skipped += 1
            continue
        try:
            obj = s3.get_object(Bucket=bucket, Key=key)
            body = obj["Body"].read()
            try:
                state_doc = json.loads(body)
            except Exception:
                # Encrypted / corrupt — skip silently.
                skipped += 1
                continue
        except ClientError as exc:
            code = (exc.response.get("Error") or {}).get("Code") or ""
            if code in {"NoSuchKey", "404", "NotFound"}:
                skipped += 1
                continue
            errors.append(f"{rel_dir}: {code or exc}")
            skipped += 1
            continue
        except NoCredentialsError as exc:
            errors.append(f"AWS creds missing: {exc}")
            return {
                "path": "",
                "modules_extracted": 0,
                "modules_skipped": skipped,
                "errors": errors,
                "bucket": bucket,
            }
        except Exception as exc:  # noqa: BLE001 — soft-degrade per module
            errors.append(f"{rel_dir}: {exc}")
            skipped += 1
            continue

        resources = _resources_from_tfstate(state_doc if isinstance(state_doc, dict) else {})
        if not resources:
            skipped += 1
            continue
        out["modules"][str(rel_dir)] = {"resources": resources, "key": key}
        extracted += 1

    out_path = persist / f"state_{env}.json"
    out_path.write_text(json.dumps(out, indent=2, default=str))

    return {
        "path": str(out_path),
        "modules_extracted": extracted,
        "modules_skipped": skipped,
        "errors": errors,
        "bucket": bucket,
    }


def detect_envs(repo_root: str) -> list[str]:
    base = Path(repo_root) / "components"
    if not base.exists():
        return []
    return sorted(
        p.name for p in base.iterdir()
        if p.is_dir() and not p.name.startswith(".")
    )


__all__ = ["extract_states_from_s3", "detect_envs"]
