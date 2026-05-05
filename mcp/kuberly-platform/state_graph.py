#!/usr/bin/env python3
"""state_graph — derive a sanitized "what is actually deployed" overlay
from Terragrunt state buckets.

Pairs with kuberly_platform.py: that builds a *static* graph from
HCL+JSON in the repo. This script enumerates real S3 state-bucket
contents and emits a per-cluster overlay JSON listing the modules that
have an applied state — committable to the infra repo and consumed by
KuberlyPlatform on graph build.

Default mode is **list-only**: only S3 object keys are read
(`s3api list-objects-v2`), never object bodies. Keys + cluster name +
region are already public in `components/<env>/shared-infra.json`, so
the output adds no new sensitive surface.

The script shells out to the AWS CLI and assumes the caller has run
`aws sso login` (or otherwise has working credentials). Stdlib only.

Usage:

    # one cluster
    python3 state_graph.py generate --env prod \
        --output .kuberly/state_overlay_prod.json

    # every cluster with a shared-infra.json under components/
    python3 state_graph.py generate-all --output-dir .claude

    # pass --profile <name> to pick an AWS CLI profile
    python3 state_graph.py generate --env prod --profile my-prod-sso

State key convention (clouds/aws):

    aws/<module>/terraform.tfstate
    aws/<module>/<env>/<app_name>/terraform.tfstate   # per-app modules
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
# The whole point of this overlay is that anyone reviewing a PR can
# verify by eye that nothing leaked from state. The schema is therefore
# deliberately tight — any field that isn't in this allowlist is dropped
# or causes a refusal to write.

# schema_version 1: list-only — only `deployed_modules` + `deployed_applications`.
# schema_version 2: schema 1 + per-module `resources` graph (resource type/name/
#   provider/dependencies, NEVER attribute values). The producer (`generate
#   --resources`) downloads each state file via `aws s3 cp`, parses JSON, and
#   passes only whitelisted fields through. Consumers tolerate either schema.
SCHEMA_VERSION_LATEST = 2
SCHEMA_VERSIONS_SUPPORTED = {1, 2}

_RE_CLUSTER_STR = re.compile(r"^[a-zA-Z0-9._-]+$")
_RE_MODULE_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_RE_STATE_KEY = re.compile(
    r"^aws/[a-z0-9_]+/(?:[a-z0-9._-]+/[a-zA-Z0-9._-]+/)?terraform\.tfstate$"
)
_RE_APP_NAME = re.compile(r"^[a-zA-Z0-9._-]+$")
_RE_ENV_NAME = re.compile(r"^[a-z0-9_-]+$")

# Resource-graph regexes (schema 2 only).
_RE_RESOURCE_TYPE = re.compile(r"^[a-z][a-z0-9_]*$")
_RE_RESOURCE_NAME = re.compile(r"^[a-zA-Z0-9_-]+$")
_RE_RESOURCE_ADDR = re.compile(r'^[a-zA-Z0-9._\[\]"/\\-]+$')
_RE_PROVIDER     = re.compile(r"^[a-z0-9_./\\-]+$")
_RE_OUTPUT_NAME  = re.compile(r"^[a-zA-Z0-9_-]+$")

_MAX_STR = 128  # any cluster/module-name field longer than this is suspicious — refuse
_MAX_ADDR = 512  # resource addresses can be long (deep module paths + for_each keys)


def _sanitize_str(value: object, pattern: re.Pattern, field: str,
                  max_len: int = _MAX_STR) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field}: expected string, got {type(value).__name__}")
    if len(value) > max_len:
        raise ValueError(f"{field}: too long ({len(value)} > {max_len})")
    if not pattern.match(value):
        raise ValueError(f"{field}: failed safety regex {pattern.pattern!r}")
    return value


def _validate_resource_entry(entry: dict, ctx: str) -> dict:
    """Schema 2: per-resource whitelist. Drops attributes / values entirely —
    only structural metadata + dependency edges survive."""
    if not isinstance(entry, dict):
        raise ValueError(f"{ctx}: expected object")
    safe = {
        "address": _sanitize_str(entry.get("address"), _RE_RESOURCE_ADDR,
                                 f"{ctx}.address", max_len=_MAX_ADDR),
        "type": _sanitize_str(entry.get("type"), _RE_RESOURCE_TYPE, f"{ctx}.type"),
        "name": _sanitize_str(entry.get("name"), _RE_RESOURCE_NAME, f"{ctx}.name"),
    }
    provider = entry.get("provider", "")
    if provider:
        safe["provider"] = _sanitize_str(provider, _RE_PROVIDER, f"{ctx}.provider")
    else:
        safe["provider"] = ""
    inst_count = entry.get("instance_count", 0)
    if not isinstance(inst_count, int) or inst_count < 0 or inst_count > 100000:
        raise ValueError(f"{ctx}.instance_count: expected non-negative int")
    safe["instance_count"] = inst_count
    deps_in = entry.get("depends_on", []) or []
    if not isinstance(deps_in, list):
        raise ValueError(f"{ctx}.depends_on: expected list")
    safe_deps = []
    for j, d in enumerate(deps_in):
        safe_deps.append(_sanitize_str(d, _RE_RESOURCE_ADDR,
                                       f"{ctx}.depends_on[{j}]", max_len=_MAX_ADDR))
    safe["depends_on"] = sorted(set(safe_deps))
    return safe


def _validate_module_resources(name: str, payload: dict) -> dict:
    """Schema 2: per-module resource graph."""
    if not isinstance(payload, dict):
        raise ValueError(f"modules.{name}: expected object")
    rcount = payload.get("resource_count", 0)
    if not isinstance(rcount, int) or rcount < 0 or rcount > 1_000_000:
        raise ValueError(f"modules.{name}.resource_count: expected non-negative int")
    resources_in = payload.get("resources", []) or []
    if not isinstance(resources_in, list):
        raise ValueError(f"modules.{name}.resources: expected list")
    resources = [
        _validate_resource_entry(r, f"modules.{name}.resources[{i}]")
        for i, r in enumerate(resources_in)
    ]
    output_names_in = payload.get("output_names", []) or []
    if not isinstance(output_names_in, list):
        raise ValueError(f"modules.{name}.output_names: expected list")
    output_names = []
    for i, o in enumerate(output_names_in):
        output_names.append(
            _sanitize_str(o, _RE_OUTPUT_NAME, f"modules.{name}.output_names[{i}]")
        )
    return {
        "resource_count": rcount,
        "resources": sorted(resources, key=lambda r: r["address"]),
        "output_names": sorted(set(output_names)),
    }


def _validate_overlay(doc: dict) -> dict:
    """Pass the overlay through an allowlist filter. Returns a fresh dict
    containing only known-safe fields. Raises ValueError on any value
    that fails the schema."""
    sv = doc.get("schema_version")
    if sv not in SCHEMA_VERSIONS_SUPPORTED:
        raise ValueError(f"unexpected schema_version: {sv!r}")

    cluster = doc.get("cluster", {})
    if not isinstance(cluster, dict):
        raise ValueError("cluster: expected object")
    safe_cluster = {
        "env": _sanitize_str(cluster.get("env"), _RE_ENV_NAME, "cluster.env"),
        "name": _sanitize_str(cluster.get("name"), _RE_CLUSTER_STR, "cluster.name"),
        "region": _sanitize_str(cluster.get("region"), _RE_CLUSTER_STR, "cluster.region"),
        "account_id": _sanitize_str(cluster.get("account_id"), _RE_CLUSTER_STR, "cluster.account_id"),
        "state_bucket": _sanitize_str(cluster.get("state_bucket"), _RE_CLUSTER_STR, "cluster.state_bucket"),
    }

    modules = doc.get("deployed_modules", [])
    if not isinstance(modules, list):
        raise ValueError("deployed_modules: expected list")
    safe_modules = []
    seen = set()
    for i, entry in enumerate(modules):
        if not isinstance(entry, dict):
            raise ValueError(f"deployed_modules[{i}]: expected object")
        name = _sanitize_str(entry.get("name"), _RE_MODULE_NAME, f"deployed_modules[{i}].name")
        key = _sanitize_str(entry.get("state_key"), _RE_STATE_KEY, f"deployed_modules[{i}].state_key")
        if name in seen:
            continue
        seen.add(name)
        safe_modules.append({"name": name, "state_key": key})

    apps = doc.get("deployed_applications", [])
    if not isinstance(apps, list):
        raise ValueError("deployed_applications: expected list")
    safe_apps = []
    for i, entry in enumerate(apps):
        if not isinstance(entry, dict):
            raise ValueError(f"deployed_applications[{i}]: expected object")
        safe_apps.append({
            "module": _sanitize_str(entry.get("module"), _RE_MODULE_NAME, f"deployed_applications[{i}].module"),
            "env": _sanitize_str(entry.get("env"), _RE_ENV_NAME, f"deployed_applications[{i}].env"),
            "name": _sanitize_str(entry.get("name"), _RE_APP_NAME, f"deployed_applications[{i}].name"),
            "state_key": _sanitize_str(entry.get("state_key"), _RE_STATE_KEY, f"deployed_applications[{i}].state_key"),
        })

    generated_at = doc.get("generated_at", "")
    if not isinstance(generated_at, str) or len(generated_at) > 40:
        raise ValueError("generated_at: expected ISO-8601 string")

    safe: dict = {
        "schema_version": sv,
        "generated_at": generated_at,
        "generator": "kuberly-skills/state_graph.py",
        "cluster": safe_cluster,
        "deployed_modules": sorted(safe_modules, key=lambda m: m["name"]),
        "deployed_applications": sorted(safe_apps, key=lambda a: (a["module"], a["env"], a["name"])),
    }

    # Schema 2: per-module resource graph. Optional even at schema 2 — a doc
    # that declared schema 2 but has no `modules` section is still valid (just
    # equivalent to schema 1 content).
    if sv >= 2:
        modules_in = doc.get("modules", {}) or {}
        if not isinstance(modules_in, dict):
            raise ValueError("modules: expected object")
        modules_safe: dict[str, dict] = {}
        for mod_name, mod_payload in modules_in.items():
            _sanitize_str(mod_name, _RE_MODULE_NAME, "modules.<name>")
            modules_safe[mod_name] = _validate_module_resources(mod_name, mod_payload)
        safe["modules"] = modules_safe

    return safe


# ----- shared-infra discovery -----------------------------------------

def _find_shared_infra_files(repo_root: Path) -> dict[str, Path]:
    """Map env name -> path to components/<env>/shared-infra.json."""
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
    account_id = str(target.get("account_id", "")).strip()
    region = str(target.get("region", "")).strip()
    name = str(cluster.get("name", "")).strip()
    if not (account_id and region and name):
        raise ValueError(
            f"{shared_infra_path}: missing target.account_id / target.region / target.cluster.name"
        )
    return {
        "env": env_name,
        "name": name,
        "region": region,
        "account_id": account_id,
        "state_bucket": f"{account_id}-{region}-{name}-tf-states",
    }


# ----- AWS CLI shell-out ----------------------------------------------

def _aws_list_keys(bucket: str, prefix: str, region: str,
                   profile: str | None) -> list[str]:
    """Run `aws s3api list-objects-v2` and return all keys under prefix.

    Paginated by --max-keys; we use --no-paginate=false (default) and
    follow the NextContinuationToken to be safe against >1000 objects.
    """
    if not shutil.which("aws"):
        raise RuntimeError(
            "aws CLI not found on PATH. Install AWS CLI v2 and run "
            "`aws sso login` before running this script."
        )

    keys: list[str] = []
    cont_token: str | None = None
    while True:
        cmd = [
            "aws", "s3api", "list-objects-v2",
            "--bucket", bucket,
            "--prefix", prefix,
            "--region", region,
            "--output", "json",
        ]
        if profile:
            cmd.extend(["--profile", profile])
        if cont_token:
            cmd.extend(["--starting-token", cont_token])
        # Cap response size to keep memory bounded; --max-items pages.
        cmd.extend(["--max-items", "1000"])

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        except OSError as e:
            raise RuntimeError(f"aws CLI failed to launch: {e}") from e

        if res.returncode != 0:
            stderr = (res.stderr or "").strip()
            # Sanitize: don't echo stderr verbatim (could contain
            # environment-leaking detail). Show truncated, generic.
            tail = stderr.splitlines()[-1] if stderr else ""
            raise RuntimeError(
                f"aws s3api list-objects-v2 failed (exit {res.returncode}): {tail[:200]}"
            )

        try:
            payload = json.loads(res.stdout) if res.stdout.strip() else {}
        except json.JSONDecodeError as e:
            raise RuntimeError(f"aws CLI returned non-JSON output: {e}") from e

        for item in payload.get("Contents", []) or []:
            k = item.get("Key", "")
            if k:
                keys.append(k)

        cont_token = payload.get("NextToken")
        if not cont_token:
            break

    return keys


def _aws_get_object_json(bucket: str, key: str, region: str,
                         profile: str | None) -> dict:
    """Stream an S3 object via `aws s3 cp ... -` and parse as JSON.

    Used to read Terraform state files for resource extraction. The bytes
    are kept in memory only long enough to parse + extract whitelisted
    fields. The raw state contents (attributes, secrets, kubeconfig data
    etc.) are discarded — never written to disk by this script.
    """
    cmd = [
        "aws", "s3", "cp",
        f"s3://{bucket}/{key}",
        "-",  # stdout
        "--region", region,
        "--no-progress",
    ]
    if profile:
        cmd.extend(["--profile", profile])
    try:
        res = subprocess.run(cmd, capture_output=True, check=False)
    except OSError as e:
        raise RuntimeError(f"aws s3 cp failed to launch: {e}") from e
    if res.returncode != 0:
        # stderr only — never echo stdout (potentially state contents).
        tail = (res.stderr or b"").decode("utf-8", errors="replace").splitlines()
        last = tail[-1] if tail else ""
        raise RuntimeError(
            f"aws s3 cp s3://{bucket}/{key} failed (exit {res.returncode}): {last[:200]}"
        )
    try:
        return json.loads(res.stdout)
    except json.JSONDecodeError as e:
        # Don't include the bytes in the error message.
        raise RuntimeError(f"state file at {key} is not valid JSON: {e.msg}") from e


# ----- resource extraction (schema 2) ---------------------------------

# Resource types known to carry sensitive payloads. We still emit them
# as nodes (the user explicitly asked: "secret exists in graph, value
# hidden"), but the whitelist already drops attributes for ALL
# resources, so this set is just a marker for the consumer to flag.
_SENSITIVE_RESOURCE_TYPES = frozenset({
    "aws_secretsmanager_secret",
    "aws_secretsmanager_secret_version",
    "aws_ssm_parameter",
    "aws_iam_access_key",
    "aws_iam_user_login_profile",
    "aws_db_instance",
    "aws_rds_cluster",
    "kubernetes_secret",
    "kubernetes_secret_v1",
    "helm_release",  # values blob can carry creds
    "tls_private_key",
    "tls_self_signed_cert",
    "random_password",
    "random_string",
    "external",
})


def _clean_provider(raw: str) -> str:
    """Convert `provider["registry.terraform.io/hashicorp/helm"]` to
    `hashicorp/helm`. Returns "" if it can't parse."""
    m = re.search(r'"([^"]+)"', raw or "")
    if not m:
        return ""
    parts = m.group(1).split("/")
    if len(parts) >= 2:
        return "/".join(parts[-2:])
    return parts[-1] if parts else ""


def _extract_module_resources(state_doc: dict) -> dict:
    """Whitelist-only extraction from a Terraform state v4 JSON.

    Returns a dict with `resource_count`, `resources[]`, `output_names[]`.
    Attribute values, output values, provider state, and sensitive
    metadata are discarded — never enter the return value.
    """
    out_resources: list[dict] = []
    for r in state_doc.get("resources", []) or []:
        if r.get("mode") != "managed":
            # Skip data sources — they aren't "deployed" things.
            continue
        rtype = r.get("type", "")
        rname = r.get("name", "")
        rmod = r.get("module", "") or ""
        rprov = _clean_provider(r.get("provider", ""))
        instances = r.get("instances", []) or []
        depends_on: set[str] = set()
        for inst in instances:
            for d in (inst.get("dependencies", []) or []):
                if isinstance(d, str):
                    depends_on.add(d)

        # Skip oddly-shaped entries silently — better to underreport than
        # raise mid-extraction and lose the rest of the module's graph.
        if not rtype or not rname:
            continue
        if not _RE_RESOURCE_TYPE.match(rtype):
            continue
        if not _RE_RESOURCE_NAME.match(rname):
            continue
        if rprov and not _RE_PROVIDER.match(rprov):
            rprov = ""

        address = f"{rmod}.{rtype}.{rname}" if rmod else f"{rtype}.{rname}"
        if len(address) > _MAX_ADDR or not _RE_RESOURCE_ADDR.match(address):
            continue

        # Filter dependency refs through the same regex; drop oddballs.
        clean_deps = sorted({
            d for d in depends_on
            if isinstance(d, str)
            and len(d) <= _MAX_ADDR
            and _RE_RESOURCE_ADDR.match(d)
        })

        out_resources.append({
            "address": address,
            "type": rtype,
            "name": rname,
            "provider": rprov,
            "instance_count": len(instances),
            "depends_on": clean_deps,
        })

    output_names: list[str] = []
    for k in (state_doc.get("outputs") or {}).keys():
        if isinstance(k, str) and _RE_OUTPUT_NAME.match(k) and len(k) <= _MAX_STR:
            output_names.append(k)

    return {
        "resource_count": len(out_resources),
        "resources": sorted(out_resources, key=lambda r: r["address"]),
        "output_names": sorted(set(output_names)),
    }


# ----- key parsing ----------------------------------------------------

# Per-app modules use `aws/<module>/<env>/<app>/terraform.tfstate`.
# Everything else is `aws/<module>/terraform.tfstate`.
_PER_APP_MODULES = {"ecs_app", "lambda_app", "bedrock_agentcore_app"}
# Modules to skip — bootstrap / not-a-real-component.
_SKIP_MODULES = {"init"}


def _parse_state_keys(keys: list[str]) -> tuple[list[dict], list[dict]]:
    """Return (deployed_modules, deployed_applications)."""
    modules: dict[str, dict] = {}
    apps: list[dict] = []
    for k in keys:
        # We only care about terraform.tfstate; ignore .tflock, history, etc.
        if not k.endswith("/terraform.tfstate"):
            continue
        if not k.startswith("aws/"):
            continue
        parts = k.split("/")
        # aws / <module> / terraform.tfstate           -> 3 parts
        # aws / <module> / <env> / <app> / terraform.tfstate -> 5 parts
        if len(parts) == 3:
            mod = parts[1]
            if mod in _SKIP_MODULES:
                continue
            if not _RE_MODULE_NAME.match(mod):
                continue
            modules.setdefault(mod, {"name": mod, "state_key": k})
        elif len(parts) == 5:
            mod, env, app = parts[1], parts[2], parts[3]
            if mod not in _PER_APP_MODULES:
                continue
            if not (_RE_MODULE_NAME.match(mod)
                    and _RE_ENV_NAME.match(env)
                    and _RE_APP_NAME.match(app)):
                continue
            modules.setdefault(mod, {"name": mod,
                                     "state_key": f"aws/{mod}/terraform.tfstate"})
            apps.append({"module": mod, "env": env, "name": app, "state_key": k})
        # Anything else: ignore.
    return list(modules.values()), apps


# ----- top-level build ------------------------------------------------

def build_overlay(repo_root: Path, env: str, profile: str | None,
                  with_resources: bool = False,
                  module_filter: list[str] | None = None,
                  progress: bool = False) -> dict:
    si_files = _find_shared_infra_files(repo_root)
    if env not in si_files:
        avail = ", ".join(sorted(si_files)) or "<none>"
        raise SystemExit(f"no components/{env}/shared-infra.json found. Available envs: {avail}")
    cluster = _read_cluster_meta(si_files[env], env)
    keys = _aws_list_keys(
        bucket=cluster["state_bucket"],
        prefix="aws/",
        region=cluster["region"],
        profile=profile,
    )
    modules, apps = _parse_state_keys(keys)
    schema = 2 if with_resources else 1
    overlay: dict = {
        "schema_version": schema,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generator": "kuberly-skills/state_graph.py",
        "cluster": cluster,
        "deployed_modules": modules,
        "deployed_applications": apps,
    }

    if with_resources:
        modules_section: dict[str, dict] = {}
        targets = [m for m in modules
                   if not module_filter or m["name"] in set(module_filter)]
        for i, m in enumerate(targets, 1):
            mod_name = m["name"]
            state_key = m["state_key"]
            if progress:
                print(
                    f"  [{i}/{len(targets)}] fetching {mod_name} ({state_key})...",
                    file=sys.stderr,
                )
            try:
                state_doc = _aws_get_object_json(
                    bucket=cluster["state_bucket"],
                    key=state_key,
                    region=cluster["region"],
                    profile=profile,
                )
            except RuntimeError as e:
                # Don't fail the whole run on one bad state — record an
                # empty section and move on. Producer-side validator will
                # accept it.
                if progress:
                    print(f"     -> WARNING: {e}", file=sys.stderr)
                modules_section[mod_name] = {
                    "resource_count": 0, "resources": [], "output_names": [],
                }
                continue
            modules_section[mod_name] = _extract_module_resources(state_doc)
            # Free the parsed state immediately — no need to retain bytes.
            del state_doc
        overlay["modules"] = modules_section

    return _validate_overlay(overlay)


def _write_overlay(overlay: dict, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as fh:
        json.dump(overlay, fh, indent=2, sort_keys=False)
        fh.write("\n")


# ----- CLI ------------------------------------------------------------

def _cmd_generate(args: argparse.Namespace) -> int:
    repo = Path(args.repo or os.getcwd()).resolve()
    module_filter = (
        [m.strip() for m in args.modules.split(",") if m.strip()]
        if getattr(args, "modules", None) else None
    )
    overlay = build_overlay(
        repo, args.env, args.profile,
        with_resources=getattr(args, "resources", False),
        module_filter=module_filter,
        progress=getattr(args, "resources", False) and not args.dry_run,
    )
    output = Path(args.output) if args.output else (
        repo / ".kuberly" / f"state_overlay_{args.env}.json"
    )
    if args.dry_run:
        print(json.dumps(overlay, indent=2))
        return 0
    _write_overlay(overlay, output)
    rcount = sum(
        m.get("resource_count", 0) for m in (overlay.get("modules") or {}).values()
    )
    extra = f", {rcount} resources" if "modules" in overlay else ""
    print(
        f"wrote {output.relative_to(repo) if output.is_relative_to(repo) else output} — "
        f"{len(overlay['deployed_modules'])} modules, "
        f"{len(overlay['deployed_applications'])} applications"
        f"{extra}"
    )
    return 0


def _cmd_generate_all(args: argparse.Namespace) -> int:
    repo = Path(args.repo or os.getcwd()).resolve()
    si_files = _find_shared_infra_files(repo)
    if not si_files:
        raise SystemExit("no components/<env>/shared-infra.json files found")
    output_dir = Path(args.output_dir) if args.output_dir else (repo / ".kuberly")
    module_filter = (
        [m.strip() for m in args.modules.split(",") if m.strip()]
        if getattr(args, "modules", None) else None
    )
    overall = 0
    for env in sorted(si_files):
        try:
            overlay = build_overlay(
                repo, env, args.profile,
                with_resources=getattr(args, "resources", False),
                module_filter=module_filter,
                progress=getattr(args, "resources", False),
            )
        except (RuntimeError, ValueError) as e:
            print(f"[{env}] FAILED: {e}", file=sys.stderr)
            overall = 1
            continue
        out = output_dir / f"state_overlay_{env}.json"
        _write_overlay(overlay, out)
        rcount = sum(
            m.get("resource_count", 0) for m in (overlay.get("modules") or {}).values()
        )
        extra = f", {rcount} resources" if "modules" in overlay else ""
        print(
            f"[{env}] wrote {out.relative_to(repo) if out.is_relative_to(repo) else out} — "
            f"{len(overlay['deployed_modules'])} modules, "
            f"{len(overlay['deployed_applications'])} applications"
            f"{extra}"
        )
    return overall


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="state_graph",
        description="Build a sanitized state-overlay graph from Terragrunt state buckets.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="generate overlay for one env")
    g.add_argument("--env", required=True, help="env name (matches components/<env>/)")
    g.add_argument("--repo", help="repo root (default: cwd)")
    g.add_argument("--output", help="output path (default: <repo>/.kuberly/state_overlay_<env>.json)")
    g.add_argument("--profile", help="AWS CLI profile (default: AWS_PROFILE / default chain)")
    g.add_argument("--resources", action="store_true",
                   help="schema 2: download each state, extract resource graph "
                        "(type/name/depends_on; never attribute values). "
                        "Requires s3:GetObject in addition to s3:ListBucket.")
    g.add_argument("--modules",
                   help="comma-separated allowlist (only with --resources): "
                        "e.g. --modules loki,grafana,alloy")
    g.add_argument("--dry-run", action="store_true", help="print to stdout, do not write")
    g.set_defaults(func=_cmd_generate)

    ga = sub.add_parser("generate-all", help="generate overlay for every env in components/")
    ga.add_argument("--repo", help="repo root (default: cwd)")
    ga.add_argument("--output-dir", help="output dir (default: <repo>/.kuberly)")
    ga.add_argument("--profile", help="AWS CLI profile (default: AWS_PROFILE / default chain)")
    ga.add_argument("--resources", action="store_true",
                    help="schema 2: include resource graph for every env (slower)")
    ga.add_argument("--modules",
                    help="comma-separated allowlist (only with --resources)")
    ga.set_defaults(func=_cmd_generate_all)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
