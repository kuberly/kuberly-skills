#!/usr/bin/env python3
"""diff_apps.py — diff CUE-rendered application manifests against the live
cluster overlay.

Reads:
  .kuberly/rendered_apps_<env>.json   (produced by render_apps.py)
  .kuberly/k8s_overlay_<env>.json     (produced by k8s_graph.py)

Writes:
  .kuberly/app_drift_<env>.json — per-app delta of declared vs running:

  {
    "schema_version": 1,
    "env": "prod",
    "generated_at": "...",
    "apps": [
      {
        "app": "backend",
        "summary": {"declared": 12, "running": 14, "matched": 11,
                    "missing_in_cluster": 1, "extra_in_cluster": 3},
        "missing_in_cluster": [{"kind": "...", "name": "...", ...}],
        "extra_in_cluster":   [{"kind": "...", "name": "...", ...}],
        "matched":            [{"kind": "...", "name": "...", ...}]
      }
    ]
  }

DESIGN NOTE — manual run only:
  Like render_apps.py, this script is INTENTIONALLY NOT invoked by
  kuberly_platform.py or any pre-commit hook. Run it explicitly when
  you want fresh drift data:

      python3 apm_modules/kuberly/kuberly-skills/scripts/diff_apps.py

  Output is consumed by the dashboard automatically on the next graph
  regen if `.kuberly/app_drift_<env>.json` exists.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Match keys: kind + name + namespace.
def _key(r: dict) -> tuple[str, str, str]:
    return (r.get("kind", ""), r.get("name", ""), r.get("namespace", ""))


def _load(p: Path) -> dict | None:
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text())
    except (ValueError, OSError):
        return None


def _diff_one_app(declared: list[dict], running: list[dict],
                  app: str) -> dict:
    decl_by = {_key(r): r for r in declared}
    run_by = {}
    for r in running:
        # k8s overlay rows often look like: kind, namespace, name + extra
        # fields. Normalize the shape so we can intersect with `_key`.
        row = {
            "kind": r.get("kind") or r.get("k8s_kind") or "",
            "name": r.get("name") or r.get("k8s_name") or "",
            "namespace": r.get("namespace") or r.get("k8s_namespace") or "",
            "labels_app": (r.get("labels") or {}).get("app", ""),
            "owner_kind": (r.get("owner_refs") or [{}])[0].get("kind", "") if r.get("owner_refs") else "",
        }
        run_by[_key(row)] = row

    decl_keys = set(decl_by.keys())
    run_keys  = set(run_by.keys())
    matched   = sorted(decl_keys & run_keys)
    missing   = sorted(decl_keys - run_keys)
    extra     = sorted(run_keys - decl_keys)
    return {
        "app": app,
        "summary": {
            "declared": len(declared),
            "running":  len(running),
            "matched":  len(matched),
            "missing_in_cluster": len(missing),
            "extra_in_cluster":   len(extra),
        },
        "matched":            [decl_by[k] for k in matched],
        "missing_in_cluster": [decl_by[k] for k in missing],
        "extra_in_cluster":   [run_by[k]  for k in extra],
    }


def _filter_running_for_app(running_all: list[dict], app: str) -> list[dict]:
    """Pull the slice of the cluster overlay attributable to one app.

    We trust the `application` annotation that the CUE templates write
    onto every rendered object. Falls back to the labels.app convention.
    """
    out = []
    for r in running_all:
        ann = (r.get("annotations") or {})
        labels = (r.get("labels") or {})
        if ann.get("application") == app or labels.get("app") == app:
            out.append(r)
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Diff CUE-rendered application manifests against the "
                    "live k8s cluster overlay. Manual-only.")
    p.add_argument("--repo", default=".")
    p.add_argument("--env", action="append",
                   help="restrict to env(s), e.g. --env prod")
    args = p.parse_args(argv)
    repo = Path(args.repo).resolve()
    out_dir = repo / ".kuberly"
    if not out_dir.is_dir():
        print(f"error: {out_dir} not found.", file=sys.stderr)
        return 2

    envs: list[str] = []
    for p_ in sorted(out_dir.glob("rendered_apps_*.json")):
        envs.append(p_.stem[len("rendered_apps_"):])
    if args.env:
        envs = [e for e in envs if e in args.env]
    if not envs:
        print("no rendered_apps_*.json found — run render_apps.py first.",
              file=sys.stderr)
        return 1

    for env in envs:
        rendered = _load(out_dir / f"rendered_apps_{env}.json")
        cluster  = _load(out_dir / f"k8s_overlay_{env}.json")
        if not rendered:
            print(f"  {env}: rendered file missing; skipping.", file=sys.stderr)
            continue
        cluster_resources = (cluster or {}).get("resources", []) or []
        per_app = []
        for app_row in rendered.get("apps", []):
            if not app_row.get("ok"):
                per_app.append({
                    "app": app_row["app"],
                    "summary": {"declared": 0, "running": 0,
                                "matched": 0, "missing_in_cluster": 0,
                                "extra_in_cluster": 0},
                    "matched": [], "missing_in_cluster": [],
                    "extra_in_cluster": [],
                    "render_failed": True,
                    "error": app_row.get("error"),
                })
                continue
            running = _filter_running_for_app(cluster_resources, app_row["app"])
            per_app.append(_diff_one_app(app_row["resources"], running,
                                          app_row["app"]))
        out = {
            "schema_version": 1,
            "env": env,
            "generated_at": datetime.now(timezone.utc)
                              .replace(microsecond=0).isoformat()
                              .replace("+00:00", "Z"),
            "generator": "kuberly-skills/diff_apps.py",
            "apps": per_app,
        }
        path = out_dir / f"app_drift_{env}.json"
        path.write_text(json.dumps(out, indent=2) + "\n")
        # Quick console summary.
        decl = sum(a["summary"]["declared"] for a in per_app)
        miss = sum(a["summary"]["missing_in_cluster"] for a in per_app)
        extra = sum(a["summary"]["extra_in_cluster"] for a in per_app)
        print(f"wrote {path} — {len(per_app)} apps, declared={decl}, "
              f"missing_in_cluster={miss}, extra_in_cluster={extra}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
