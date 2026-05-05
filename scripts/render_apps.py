#!/usr/bin/env python3
"""render_apps.py — manually-run renderer for kuberly application JSONs.

Walks every `applications/<env>/<app>.json` in the consumer repo, invokes
the consumer's CUE module via `cue cmd dump -t instance=<env> -t app=<n>`,
parses the YAML manifest stream, and writes one summary JSON per env to
`.kuberly/rendered_apps_<env>.json`.

The output answers questions like:
  - how many k8s objects does this app actually expand into?
  - which Deployment kinds / Service ports / IRSA SA's get rendered?
  - is there drift between what the JSON declares and what's running?

DESIGN NOTE — manual run only:
  This script is INTENTIONALLY NOT invoked by `kuberly_platform.py` or
  any pre-commit hook. CUE rendering can be slow, requires the `cue`
  binary, and shells out — none of which fits a pre-commit budget. Run
  it explicitly when you want fresh data:

      python3 apm_modules/kuberly/kuberly-skills/scripts/render_apps.py

  Or, from a checkout of kuberly-skills:

      python3 scripts/render_apps.py --repo /path/to/consumer

  Output is read by the dashboard automatically on the next graph regen
  if `.kuberly/rendered_apps_<env>.json` exists.

Stdlib + external `cue` binary only. Tested with cue v0.10+.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# ----- helpers --------------------------------------------------------

def _find_cue_dir(repo: Path) -> Path | None:
    """Locate the consumer's CUE module — `cue/` at the repo root."""
    cdir = repo / "cue"
    if (cdir / "cue.mod" / "module.cue").is_file():
        return cdir
    return None


def _list_apps(repo: Path) -> list[tuple[str, str, Path]]:
    """Return [(env, app_name, json_path), ...] for every app sidecar."""
    out = []
    apps_dir = repo / "applications"
    if not apps_dir.is_dir():
        return out
    for env_dir in sorted(apps_dir.iterdir()):
        if not env_dir.is_dir() or env_dir.name.startswith("."):
            continue
        for jp in sorted(env_dir.glob("*.json")):
            app = jp.stem
            out.append((env_dir.name, app, jp))
    return out


def _extract_app_meta(json_path: Path) -> tuple[str, str]:
    """Pull `name` and `namespace` from the app JSON. Mirrors what
    cue/generate.sh does — falls back to filename / instance if absent.

    Returns (app_name, namespace_or_empty).
    """
    try:
        data = json.loads(json_path.read_text())
    except (ValueError, OSError):
        return (json_path.stem, "")
    # The JSON top-level key is the app type (deployment / argo-app /
    # ecs / lambda / bedrock_agentcore / knative / ...). The `name`
    # we want is one level inside.
    app_name = ""
    namespace = ""
    for top_key, payload in data.items():
        if not isinstance(payload, dict):
            continue
        # name lives either at payload.name or payload.common.name
        if isinstance(payload.get("name"), str):
            app_name = app_name or payload["name"]
        common = payload.get("common")
        if isinstance(common, dict):
            if isinstance(common.get("name"), str) and not app_name:
                app_name = common["name"]
            if isinstance(common.get("namespace"), str):
                namespace = namespace or common["namespace"]
        if isinstance(payload.get("namespace"), str):
            namespace = namespace or payload["namespace"]
    return (app_name or json_path.stem, namespace)


def _run_cue_dump(cue_dir: Path, env: str, app_json: Path,
                  timeout_s: int = 60) -> tuple[bool, str, str]:
    """Render an app via the canonical two-step workflow:
        1) cue import -f -l 'config:' -p applications <json> -o config_gen_<file>.cue
        2) cue cmd -t instance=<ns> -t app=<app_name> dump .
        3) rm config_gen_<file>.cue

    Mirrors `cue/generate.sh` exactly. Without step 1, dump emits an
    empty stream because the `config: _` placeholder in app.cue is
    never bound.
    Returns (ok, stdout, stderr).
    """
    file_stem = app_json.stem  # e.g. "backend" or "webapp"
    app_name, ns_from_json = _extract_app_meta(app_json)
    target_ns = ns_from_json or env
    config_gen = cue_dir / f"config_gen_{file_stem}.cue"
    try:
        # Step 1 — JSON → CUE (overwrites any prior config_gen for this stem).
        rel_json = os.path.relpath(str(app_json), str(cue_dir))
        imp = subprocess.run(
            ["cue", "import", "-f", "-l", "config:", "-p", "applications",
             rel_json, "-o", config_gen.name],
            cwd=str(cue_dir),
            capture_output=True, text=True, timeout=timeout_s,
        )
        if imp.returncode != 0:
            return False, "", (imp.stderr or "cue import failed").strip()
        # Step 2 — render.
        proc = subprocess.run(
            ["cue", "cmd",
             "-t", f"instance={target_ns}",
             "-t", f"app={app_name}",
             "dump", "."],
            cwd=str(cue_dir),
            capture_output=True, text=True, timeout=timeout_s,
        )
        return proc.returncode == 0, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return False, "", f"timeout after {timeout_s}s"
    except FileNotFoundError:
        return False, "", "cue binary not found in PATH"
    finally:
        # Step 3 — cleanup the generated config file regardless of success.
        try:
            if config_gen.exists():
                config_gen.unlink()
        except OSError:
            pass


try:
    import yaml as _yaml  # PyYAML — preferred when available
except ImportError:
    _yaml = None


def _summarize_doc(doc: dict) -> dict | None:
    """Project a fully-parsed YAML doc onto the dashboard-friendly fields."""
    if not isinstance(doc, dict):
        return None
    kind = doc.get("kind") or ""
    api_version = doc.get("apiVersion") or ""
    if not kind:
        return None
    md = doc.get("metadata") or {}
    if not isinstance(md, dict):
        md = {}
    spec = doc.get("spec") or {}
    if not isinstance(spec, dict):
        spec = {}
    # Ports: top-level spec.ports OR spec.template.spec.containers[].ports.
    ports: list[int] = []
    raw_ports = spec.get("ports")
    if isinstance(raw_ports, list):
        for p in raw_ports:
            if isinstance(p, dict) and isinstance(p.get("port"), int):
                ports.append(p["port"])
    tmpl = (spec.get("template") or {}).get("spec") if isinstance(spec.get("template"), dict) else None
    if isinstance(tmpl, dict):
        for c in (tmpl.get("containers") or []):
            for p in (c or {}).get("ports", []):
                if isinstance(p, dict) and isinstance(p.get("containerPort"), int):
                    ports.append(p["containerPort"])
    sa = ""
    if isinstance(tmpl, dict):
        sa = tmpl.get("serviceAccountName") or ""
    annotations = md.get("annotations") if isinstance(md.get("annotations"), dict) else {}
    return {
        "kind": kind,
        "api_version": api_version,
        "name": md.get("name") or "",
        "namespace": md.get("namespace") or "",
        "replicas": spec.get("replicas") if isinstance(spec.get("replicas"), int) else None,
        "ports": sorted(set(ports))[:8],
        "service_account": sa,
        "annotation_app": annotations.get("application", "") if isinstance(annotations, dict) else "",
        "target_revision": "",  # ArgoCD only — leave blank for k8s manifests
    }


def _parse_yaml_stream(text: str) -> list[dict]:
    """Parse a YAML manifest stream into our summary dicts.

    Prefers PyYAML's `safe_load_all` (handles every YAML quirk). Falls
    back to a regex-based extractor when PyYAML isn't installed —
    the fallback misses nested specs but still picks up kind / name /
    namespace / top-level replicas / ports.
    """
    if _yaml is not None:
        try:
            out = []
            for doc in _yaml.safe_load_all(text):
                rec = _summarize_doc(doc)
                if rec is not None:
                    out.append(rec)
            return out
        except _yaml.YAMLError:
            pass  # fall through to regex parser
    # ----- regex fallback (no PyYAML) -----
    docs = []
    cur = []
    for line in text.splitlines():
        if line.strip() == "---":
            if cur:
                docs.append("\n".join(cur))
                cur = []
            continue
        cur.append(line)
    if cur:
        docs.append("\n".join(cur))

    out = []
    for raw in docs:
        if not raw.strip():
            continue
        kind = ""
        api_version = ""
        name = ""
        namespace = ""
        replicas = None
        ports: list[int] = []
        annotation_app = ""
        annotation_target_revision = ""
        sa = ""
        for line in raw.splitlines():
            stripped = line.strip()
            indent = len(line) - len(line.lstrip(" "))
            if indent == 0:
                if stripped.startswith("kind:"):
                    kind = stripped.split(":", 1)[1].strip()
                elif stripped.startswith("apiVersion:"):
                    api_version = stripped.split(":", 1)[1].strip()
            elif indent == 2:
                if stripped.startswith("name:") and not name:
                    name = stripped.split(":", 1)[1].strip().strip('"')
                elif stripped.startswith("namespace:") and not namespace:
                    namespace = stripped.split(":", 1)[1].strip().strip('"')
                elif stripped.startswith("replicas:") and replicas is None:
                    try:
                        replicas = int(stripped.split(":", 1)[1].strip())
                    except ValueError:
                        pass
                elif stripped.startswith("serviceAccountName:") and not sa:
                    sa = stripped.split(":", 1)[1].strip().strip('"')
            elif indent >= 4:
                if stripped.startswith("port:") or stripped.startswith("- port:"):
                    val = stripped.split(":", 1)[1].strip()
                    try:
                        ports.append(int(val))
                    except ValueError:
                        pass
                elif "application:" in stripped and not annotation_app:
                    annotation_app = stripped.split(":", 1)[1].strip().strip('"')
                elif "targetRevision:" in stripped and not annotation_target_revision:
                    annotation_target_revision = stripped.split(":", 1)[1].strip().strip('"')
        if not kind:
            continue
        out.append({
            "kind": kind,
            "api_version": api_version,
            "name": name,
            "namespace": namespace,
            "replicas": replicas,
            "ports": sorted(set(ports))[:8],
            "service_account": sa,
            "annotation_app": annotation_app,
            "target_revision": annotation_target_revision,
        })
    return out


# ----- main -----------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Render application JSON sidecars via `cue cmd dump`. "
                    "Manual-only — never wired into pre-commit.",
    )
    p.add_argument("--repo", default=".",
                   help="consumer repo root (default: cwd)")
    p.add_argument("--output-dir", default=None,
                   help="overlay output dir (default: <repo>/.kuberly)")
    p.add_argument("--env", action="append",
                   help="restrict to env(s), e.g. --env prod --env dev")
    p.add_argument("--app", action="append",
                   help="restrict to specific app name(s)")
    p.add_argument("--timeout", type=int, default=60,
                   help="per-app render timeout (s)")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args(argv)

    if not shutil.which("cue"):
        print("error: `cue` binary not found in PATH. Install from "
              "https://cuelang.org/docs/install/ or via brew install cue.",
              file=sys.stderr)
        return 2

    repo = Path(args.repo).resolve()
    cue_dir = _find_cue_dir(repo)
    if not cue_dir:
        print(f"error: no `cue/` module at {repo}/cue/cue.mod/module.cue",
              file=sys.stderr)
        return 2
    out_dir = Path(args.output_dir) if args.output_dir else (repo / ".kuberly")
    out_dir.mkdir(parents=True, exist_ok=True)

    apps = _list_apps(repo)
    if args.env:
        apps = [a for a in apps if a[0] in args.env]
    if args.app:
        apps = [a for a in apps if a[1] in args.app]
    if not apps:
        print("no application JSONs found.", file=sys.stderr)
        return 1

    by_env: dict[str, dict] = {}
    failures = 0
    for env, app, jp in apps:
        if args.verbose:
            print(f"-> {env}/{app} ({jp})", file=sys.stderr)
        ok, stdout, stderr = _run_cue_dump(cue_dir, env, jp,
                                            timeout_s=args.timeout)
        env_bucket = by_env.setdefault(env, {
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc)
                              .replace(microsecond=0).isoformat()
                              .replace("+00:00", "Z"),
            "generator": "kuberly-skills/render_apps.py",
            "env": env,
            "apps": [],
        })
        if not ok:
            failures += 1
            env_bucket["apps"].append({
                "app": app,
                "json_path": str(jp.relative_to(repo)),
                "ok": False,
                "error": (stderr or "render failed").strip().splitlines()[:5],
                "resources": [],
            })
            continue
        resources = _parse_yaml_stream(stdout)
        kinds: dict[str, int] = {}
        for r in resources:
            kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
        env_bucket["apps"].append({
            "app": app,
            "json_path": str(jp.relative_to(repo)),
            "ok": True,
            "resource_count": len(resources),
            "kind_counts": kinds,
            "resources": resources,
        })

    for env, bucket in by_env.items():
        bucket["app_count"] = len(bucket["apps"])
        bucket["resource_count"] = sum(
            len(a.get("resources", [])) for a in bucket["apps"])
        out_path = out_dir / f"rendered_apps_{env}.json"
        out_path.write_text(json.dumps(bucket, indent=2) + "\n")
        print(f"wrote {out_path} — {bucket['app_count']} apps, "
              f"{bucket['resource_count']} rendered resources")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
