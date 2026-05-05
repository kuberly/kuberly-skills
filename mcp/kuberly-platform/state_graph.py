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
        --output .claude/state_overlay_prod.json

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

SCHEMA_VERSION = 1

_RE_CLUSTER_STR = re.compile(r"^[a-zA-Z0-9._-]+$")
_RE_MODULE_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_RE_STATE_KEY = re.compile(
    r"^aws/[a-z0-9_]+/(?:[a-z0-9._-]+/[a-zA-Z0-9._-]+/)?terraform\.tfstate$"
)
_RE_APP_NAME = re.compile(r"^[a-zA-Z0-9._-]+$")
_RE_ENV_NAME = re.compile(r"^[a-z0-9_-]+$")

_MAX_STR = 128  # any field longer than this is suspicious — refuse


def _sanitize_str(value: object, pattern: re.Pattern, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field}: expected string, got {type(value).__name__}")
    if len(value) > _MAX_STR:
        raise ValueError(f"{field}: too long ({len(value)} > {_MAX_STR})")
    if not pattern.match(value):
        raise ValueError(f"{field}: failed safety regex {pattern.pattern!r}")
    return value


def _validate_overlay(doc: dict) -> dict:
    """Pass the overlay through an allowlist filter. Returns a fresh dict
    containing only known-safe fields. Raises ValueError on any value
    that fails the schema."""
    if doc.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"unexpected schema_version: {doc.get('schema_version')!r}")

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

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "generator": "kuberly-skills/state_graph.py",
        "cluster": safe_cluster,
        "deployed_modules": sorted(safe_modules, key=lambda m: m["name"]),
        "deployed_applications": sorted(safe_apps, key=lambda a: (a["module"], a["env"], a["name"])),
    }


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

def build_overlay(repo_root: Path, env: str, profile: str | None) -> dict:
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
    overlay = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generator": "kuberly-skills/state_graph.py",
        "cluster": cluster,
        "deployed_modules": modules,
        "deployed_applications": apps,
    }
    return _validate_overlay(overlay)


def _write_overlay(overlay: dict, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as fh:
        json.dump(overlay, fh, indent=2, sort_keys=False)
        fh.write("\n")


# ----- CLI ------------------------------------------------------------

def _cmd_generate(args: argparse.Namespace) -> int:
    repo = Path(args.repo or os.getcwd()).resolve()
    overlay = build_overlay(repo, args.env, args.profile)
    output = Path(args.output) if args.output else (
        repo / ".claude" / f"state_overlay_{args.env}.json"
    )
    if args.dry_run:
        print(json.dumps(overlay, indent=2))
        return 0
    _write_overlay(overlay, output)
    print(
        f"wrote {output.relative_to(repo) if output.is_relative_to(repo) else output} — "
        f"{len(overlay['deployed_modules'])} modules, "
        f"{len(overlay['deployed_applications'])} applications"
    )
    return 0


def _cmd_generate_all(args: argparse.Namespace) -> int:
    repo = Path(args.repo or os.getcwd()).resolve()
    si_files = _find_shared_infra_files(repo)
    if not si_files:
        raise SystemExit("no components/<env>/shared-infra.json files found")
    output_dir = Path(args.output_dir) if args.output_dir else (repo / ".claude")
    overall = 0
    for env in sorted(si_files):
        try:
            overlay = build_overlay(repo, env, args.profile)
        except (RuntimeError, ValueError) as e:
            print(f"[{env}] FAILED: {e}", file=sys.stderr)
            overall = 1
            continue
        out = output_dir / f"state_overlay_{env}.json"
        _write_overlay(overlay, out)
        print(
            f"[{env}] wrote {out.relative_to(repo) if out.is_relative_to(repo) else out} — "
            f"{len(overlay['deployed_modules'])} modules, "
            f"{len(overlay['deployed_applications'])} applications"
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
    g.add_argument("--output", help="output path (default: <repo>/.claude/state_overlay_<env>.json)")
    g.add_argument("--profile", help="AWS CLI profile (default: AWS_PROFILE / default chain)")
    g.add_argument("--dry-run", action="store_true", help="print to stdout, do not write")
    g.set_defaults(func=_cmd_generate)

    ga = sub.add_parser("generate-all", help="generate overlay for every env in components/")
    ga.add_argument("--repo", help="repo root (default: cwd)")
    ga.add_argument("--output-dir", help="output dir (default: <repo>/.claude)")
    ga.add_argument("--profile", help="AWS CLI profile (default: AWS_PROFILE / default chain)")
    ga.set_defaults(func=_cmd_generate_all)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
