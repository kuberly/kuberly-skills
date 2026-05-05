#!/usr/bin/env python3
"""
init_agent_session.py — manage multi-agent session directories under .agents/prompts/<session>/.

Each session is a working directory the orchestrator and persona subagents share.
Personas (see .claude/agents/*.md) read every file in the session dir; each persona
writes only its assigned file. The orchestrator owns context.md and decisions.md.

Repo-root detection uses `git rev-parse --show-toplevel`, so this script runs the same
whether it lives at <repo>/scripts/ or under apm_modules/kuberly/kuberly-skills/scripts/
in a consumer repo. Always operates on the current git working tree.

Usage:
    init_agent_session.py init <session>          [--task TEXT] [--node ID ...]
    init_agent_session.py cleanup <session>
    init_agent_session.py list

File layout (session dir):
    context.md                 orchestrator: goal, graph snapshot, constraints
    scope.md                   agent-planner output
    decisions.md               orchestrator: irreversible calls + reasons
    plan.md                    revise-infra-plan output (if used)
    diagnosis.md               agent-sre output (if used)
    findings/in-context.md     pr-reviewer-in-context output
    findings/cold.md           pr-reviewer-cold output
    findings/reconciled.md     findings-reconciler output
    tasks/<NN>-<slug>.md       implementation prompts the orchestrator hands to agent-infra-ops
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def find_repo_root() -> Path:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        )
        return Path(result.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        sys.exit("not in a git repository (run from inside an infra repo)")


REPO_ROOT = find_repo_root()
PROMPTS_DIR = REPO_ROOT / ".agents" / "prompts"
GRAPH_JSON = REPO_ROOT / ".claude" / "graph.json"

CONTEXT_TEMPLATE = """\
# Context — session: {session}

Created: {created}

## Goal
{task}

## Graph snapshot at session start
{graph_summary}

## Constraints (standing)
- **Plan-only.** No `terragrunt apply`, no `tofu apply`, no `--destroy`.
- **OpenSpec required** for changes under `clouds/`, `components/`, `applications/`, `cue/`, behavioral `*.hcl`.
- **Pre-commit must pass** before any commit; never `--no-verify`.
- **Branch off MERGE_BASE** before any file edit (see `infra-bootstrap-mandatory`).
- **No recursive subagents.** Personas are leaves.

## Decisions
_(orchestrator records irreversible calls in `decisions.md` as the session unfolds)_

## Roster reference
Personas live under `.claude/agents/*.md` (deployed via `apm install` + `sync_agents.sh`).
Each persona reads everything in this directory; each writes only its own file. The
orchestrator (you, top level) owns `context.md` and `decisions.md`.
"""

PERSONA_NOTE = """subagents read every file in this dir; each writes only its own:
  agent-planner    -> scope.md
  agent-sre         -> diagnosis.md
  agent-infra-ops          -> repo files (no md write)
  agent-cicd      -> repo files in infra repo or app repo (no md write)
  pr-reviewer-in-context -> findings/in-context.md
  pr-reviewer-cold       -> findings/cold.md
  findings-reconciler    -> findings/reconciled.md
orchestrator owns context.md and decisions.md"""


def graph_summary(node_ids: list[str]) -> str:
    if not GRAPH_JSON.is_file():
        return "_kuberly-platform not generated yet — run `python3 scripts/kuberly_platform.py generate --repo .`_"
    try:
        graph = json.loads(GRAPH_JSON.read_text())
    except Exception as exc:
        return f"_failed to read {GRAPH_JSON}: {exc}_"

    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    by_type = graph.get("stats", {}).get("by_type") or {}

    lines = [f"- **Nodes:** {len(nodes)}    **Edges:** {len(edges)}"]
    if by_type:
        lines.append("- **By type:** " + ", ".join(f"`{k}`={v}" for k, v in sorted(by_type.items())))

    if node_ids:
        lines.append("")
        lines.append("### Named nodes (passed via --node)")
        node_index = {n["id"]: n for n in nodes if "id" in n}
        for nid in node_ids:
            node = node_index.get(nid)
            if node:
                env = node.get("environment", "-")
                lines.append(f"- `{nid}` (type=`{node.get('type')}`, env=`{env}`)")
            else:
                lines.append(f"- `{nid}` — **not found in graph**")

    return "\n".join(lines)


def slugify(text: str) -> str:
    out = []
    for ch in text.lower().strip():
        if ch.isalnum():
            out.append(ch)
        elif ch in "-_ ":
            out.append("-")
    slug = "".join(out).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or "session"


def cmd_init(args: argparse.Namespace) -> None:
    session = slugify(args.session)
    if session != args.session:
        print(f"normalized session name: {args.session!r} -> {session!r}", file=sys.stderr)
    session_dir = PROMPTS_DIR / session
    if session_dir.exists():
        sys.exit(f"session already exists: {session_dir}")

    session_dir.mkdir(parents=True)
    (session_dir / "findings").mkdir()
    (session_dir / "tasks").mkdir()
    (session_dir / "findings" / ".gitkeep").touch()
    (session_dir / "tasks" / ".gitkeep").touch()

    context = CONTEXT_TEMPLATE.format(
        session=session,
        created=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        task=args.task or "_(orchestrator: fill in)_",
        graph_summary=graph_summary(args.node or []),
    )
    (session_dir / "context.md").write_text(context)

    print(f"created {session_dir}")
    print(PERSONA_NOTE)


def cmd_cleanup(args: argparse.Namespace) -> None:
    session_dir = PROMPTS_DIR / args.session
    if not session_dir.exists():
        sys.exit(f"no such session: {session_dir}")
    shutil.rmtree(session_dir)
    print(f"removed {session_dir}")


def cmd_list(_args: argparse.Namespace) -> None:
    if not PROMPTS_DIR.exists():
        return
    for entry in sorted(PROMPTS_DIR.iterdir()):
        if not entry.is_dir():
            continue
        ctx = entry / "context.md"
        created = ""
        if ctx.is_file():
            for line in ctx.read_text().splitlines():
                if line.startswith("Created:"):
                    created = line.split(":", 1)[1].strip()
                    break
        print(f"{entry.name}\t{created}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="create a session directory")
    p_init.add_argument("session", help="session name (will be slugified)")
    p_init.add_argument("--task", help="one-line task description for context.md")
    p_init.add_argument(
        "--node",
        action="append",
        help="prefill a named graph node id into context.md (repeatable, e.g. --node component:prod/eks)",
    )
    p_init.set_defaults(func=cmd_init)

    p_cleanup = sub.add_parser("cleanup", help="remove a session directory")
    p_cleanup.add_argument("session")
    p_cleanup.set_defaults(func=cmd_cleanup)

    p_list = sub.add_parser("list", help="list active sessions")
    p_list.set_defaults(func=cmd_list)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
