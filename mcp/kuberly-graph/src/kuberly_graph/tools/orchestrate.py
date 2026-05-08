"""Central graph-first orchestration tools for kuberly-platform.

The orchestrator stays read-only by default. It indexes the graph, gathers
evidence, plans agent fanout, and materializes task files under
``.agents/prompts/<session>/`` so OpenCode/Claude can run subagents in parallel
using the existing filesystem protocol.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
from pathlib import Path
from typing import Any

from ..server import SERVER_CONFIG, mcp
from .platform import platform_index
from .troubleshoot import troubleshoot


_WRITE_INTENTS = {"new-application", "new-database", "cleanup", "resource-bump", "drift-fix", "cicd"}


def _repo_root(repo_root: str | None = None) -> Path:
    return Path(repo_root or SERVER_CONFIG.get("repo_root", ".")).resolve()


def _slugify(text: str) -> str:
    out: list[str] = []
    for ch in (text or "").lower().strip():
        if ch.isalnum():
            out.append(ch)
        elif out and out[-1] != "-":
            out.append("-")
    slug = "".join(out).strip("-")
    return slug[:80] or "session"


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _session_dir(repo: Path, session_id: str) -> Path:
    return repo / ".agents" / "prompts" / session_id


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str) + "\n")


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _extract_evidence(index: dict[str, Any], limit: int = 12) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for match in index.get("matches") or []:
        evidence.append(
            {
                "kind": "node_match",
                "node_id": match.get("id"),
                "score": match.get("score"),
                "type": match.get("type"),
                "layer": match.get("layer"),
                "label": match.get("label"),
                "env": match.get("env"),
                "namespace": match.get("namespace"),
            }
        )
        if len(evidence) >= limit:
            return evidence
    for rel in index.get("relation_hints") or []:
        evidence.append(
            {
                "kind": "relation_hint",
                "source": rel.get("source"),
                "target": rel.get("target"),
                "relation": rel.get("relation"),
                "layer": rel.get("layer"),
            }
        )
        if len(evidence) >= limit:
            return evidence
    semantic = index.get("semantic") or {}
    if semantic.get("available"):
        for hit in semantic.get("hits") or []:
            node = hit.get("node") or {}
            evidence.append(
                {
                    "kind": "semantic_hit",
                    "node_id": hit.get("id"),
                    "score": hit.get("score"),
                    "type": node.get("type"),
                    "layer": node.get("layer"),
                    "label": node.get("label"),
                }
            )
            if len(evidence) >= limit:
                return evidence
    return evidence


def _task_kind(goal: str, primary_intent: str | None) -> str:
    lower = goal.lower()
    if primary_intent == "runtime_troubleshooting":
        return "incident"
    if primary_intent == "security_review":
        return "security-review"
    if primary_intent == "impact_analysis":
        return "impact-analysis"
    if primary_intent == "deployment_state":
        return "drift-fix" if any(w in lower for w in ("drift", "fix", "sync")) else "deployment-state"
    if any(w in lower for w in ("bump", "increase", "decrease", "rightsize", "memory", "cpu")):
        return "resource-bump"
    if any(w in lower for w in ("add app", "new app", "new application")):
        return "new-application"
    if any(w in lower for w in ("database", "postgres", "redis", "rds")) and "new" in lower:
        return "new-database"
    if any(w in lower for w in ("pipeline", "github actions", "ci", "cd")):
        return "cicd"
    return "graph-analysis"


def _fanout_for(task_kind: str, route: dict[str, Any], mode: str) -> list[dict[str, Any]]:
    needs_live = bool(route.get("may_need_live"))
    phases: list[dict[str, Any]] = [
        {
            "id": "index",
            "parallel": False,
            "agents": ["agent-planner"],
            "tools": ["platform_index", "graph_evidence"],
            "writes": ["scope.md"],
            "needs_approval": False,
        }
    ]
    diagnose_agents = ["agent-sre", "agent-k8s-ops"] if needs_live else ["agent-planner"]
    if task_kind == "security-review":
        diagnose_agents = ["agent-sre", "agent-infra-ops"]
    phases.append(
        {
            "id": "diagnose",
            "parallel": len(diagnose_agents) > 1,
            "agents": diagnose_agents,
            "tools": route.get("recommended_tools", []),
            "writes": ["diagnosis.md" if a == "agent-sre" else f"tasks/{a}.md" for a in diagnose_agents],
            "needs_approval": False,
        }
    )
    if mode != "read_only" and task_kind in _WRITE_INTENTS:
        phases.append(
            {
                "id": "implement",
                "parallel": False,
                "agents": ["agent-infra-ops" if task_kind != "cicd" else "agent-cicd"],
                "tools": [],
                "writes": ["repo changes"],
                "needs_approval": True,
            }
        )
    phases.append(
        {
            "id": "review",
            "parallel": False,
            "agents": ["pr-reviewer"],
            "tools": [],
            "writes": ["findings/in-context.md"],
            "needs_approval": False,
            "optional": True,
        }
    )
    return phases


def _agent_prompt(goal: str, phase: dict[str, Any], agent: str, index: dict[str, Any], evidence: list[dict[str, Any]]) -> str:
    return "\n".join(
        [
            f"# Task for {agent}",
            "",
            f"Goal: {goal}",
            f"Phase: {phase.get('id')}",
            "",
            "## Routing",
            json.dumps(index.get("routing", {}), indent=2, default=str),
            "",
            "## Evidence",
            json.dumps(evidence[:8], indent=2, default=str),
            "",
            "## Instructions",
            "Use the graph evidence first. Do not mutate files or infrastructure unless the orchestrator explicitly approves an implementation phase.",
            "Write only the file assigned to your persona by the session protocol.",
            "",
        ]
    )


def _materialize_session(
    repo: Path,
    session_id: str,
    goal: str,
    index: dict[str, Any],
    evidence: list[dict[str, Any]],
    phases: list[dict[str, Any]],
    mode: str,
) -> dict[str, Any]:
    root = _session_dir(repo, session_id)
    (root / "tasks").mkdir(parents=True, exist_ok=True)
    (root / "findings").mkdir(parents=True, exist_ok=True)
    context = [
        f"# Context - session: {session_id}",
        "",
        f"Created: {_now()}",
        f"Mode: {mode}",
        "",
        "## Goal",
        goal,
        "",
        "## Graph Routing",
        "```json",
        json.dumps(index.get("routing", {}), indent=2, default=str),
        "```",
        "",
        "## Evidence",
        "```json",
        json.dumps(evidence, indent=2, default=str),
        "```",
        "",
        "## Constraints",
        "- Read-only until an implementation phase is explicitly approved.",
        "- Graph evidence is the source of truth for routing.",
        "- Live ai-agent-tool data is downstream signal, not the primary index.",
        "",
    ]
    (root / "context.md").write_text("\n".join(context))
    if not (root / "decisions.md").exists():
        (root / "decisions.md").write_text(f"# Decisions - {session_id}\n\n")
    task_files: list[str] = []
    task_idx = 1
    for phase in phases:
        for agent in phase.get("agents", []):
            path = root / "tasks" / f"{task_idx:02d}-{phase.get('id')}-{agent}.md"
            path.write_text(_agent_prompt(goal, phase, agent, index, evidence))
            task_files.append(str(path.relative_to(repo)))
            task_idx += 1
    state = {
        "session_id": session_id,
        "created_at": _now(),
        "goal": goal,
        "mode": mode,
        "status": "planned",
        "phases": phases,
        "evidence": evidence,
        "task_files": task_files,
    }
    _write_json(root / "state.json", state)
    return {"session_dir": str(root), "task_files": task_files, "state": state}


@mcp.tool()
def graph_evidence(
    query: str,
    environment: str | None = None,
    limit: int = 12,
    persist_dir: str | None = None,
) -> dict[str, Any]:
    """Return concise graph evidence for a question: node matches, relations, and semantic hits."""
    index = platform_index(query=query, environment=environment, limit=limit, persist_dir=persist_dir)
    return {
        "query": query,
        "environment": environment,
        "evidence": _extract_evidence(index, limit=limit),
        "routing": index.get("routing", {}),
        "layer_summary": {
            "total_nodes": index.get("summary", {}).get("total_nodes"),
            "total_edges": index.get("summary", {}).get("total_edges"),
            "populated_layers": index.get("summary", {}).get("populated_layers", []),
        },
    }


@mcp.tool()
def plan_agent_fanout(
    goal: str,
    environment: str | None = None,
    mode: str = "read_only",
    persist_dir: str | None = None,
) -> dict[str, Any]:
    """Plan parallel agent phases from graph routing without creating a session."""
    index = platform_index(query=goal, environment=environment, limit=12, persist_dir=persist_dir)
    route = (index.get("routing", {}).get("routes") or [{}])[0]
    task_kind = _task_kind(goal, route.get("intent"))
    phases = _fanout_for(task_kind, route, mode)
    return {
        "goal": goal,
        "environment": environment,
        "mode": mode,
        "task_kind": task_kind,
        "route": route,
        "phases": phases,
        "parallel_groups": [p for p in phases if p.get("parallel")],
        "approval_required": any(p.get("needs_approval") for p in phases),
    }


@mcp.tool()
def orchestrate(
    goal: str,
    environment: str | None = None,
    namespace: str | None = None,
    mode: str = "read_only",
    run_live: bool = True,
    create_session: bool = True,
    session_id: str | None = None,
    repo_root: str | None = None,
    persist_dir: str | None = None,
) -> dict[str, Any]:
    """Central graph-first orchestrator.

    Indexes the graph, collects evidence, optionally runs live troubleshooting
    for runtime-shaped questions, plans parallel agent fanout, and optionally
    creates a filesystem session under ``.agents/prompts/<session>``.
    """
    repo = _repo_root(repo_root)
    index = platform_index(query=goal, environment=environment, limit=12, persist_dir=persist_dir)
    route = (index.get("routing", {}).get("routes") or [{}])[0]
    task_kind = _task_kind(goal, route.get("intent"))
    evidence = _extract_evidence(index)
    phases = _fanout_for(task_kind, route, mode)
    live = None
    if run_live and route.get("may_need_live"):
        live = troubleshoot(
            subject=goal,
            environment=environment,
            namespace=namespace,
            use_live=True,
            repo_root=str(repo),
            persist_dir=persist_dir,
        )
    session = None
    if create_session:
        sid = session_id or f"{_dt.datetime.now().strftime('%Y%m%d%H%M%S')}-{_slugify(goal)}"
        session = _materialize_session(repo, sid, goal, index, evidence, phases, mode)
    return {
        "goal": goal,
        "environment": environment,
        "namespace": namespace,
        "mode": mode,
        "task_kind": task_kind,
        "index": index,
        "evidence": evidence,
        "live": live,
        "agent_fanout": {
            "phases": phases,
            "parallel_groups": [p for p in phases if p.get("parallel")],
            "approval_required": any(p.get("needs_approval") for p in phases),
        },
        "session": session,
    }


@mcp.tool()
def orchestrate_status(session_id: str, repo_root: str | None = None) -> dict[str, Any]:
    """Read orchestrator session state and known persona output files."""
    repo = _repo_root(repo_root)
    root = _session_dir(repo, session_id)
    state = _read_json(root / "state.json")
    outputs: dict[str, bool] = {}
    for rel in ("scope.md", "diagnosis.md", "plan.md", "findings/in-context.md", "findings/cold.md", "findings/reconciled.md"):
        outputs[rel] = (root / rel).exists()
    return {
        "session_id": session_id,
        "session_dir": str(root),
        "exists": root.exists(),
        "state": state,
        "outputs": outputs,
    }


@mcp.tool()
def orchestrate_continue(
    session_id: str,
    note: str,
    status: str | None = None,
    repo_root: str | None = None,
) -> dict[str, Any]:
    """Append a decision/note to an orchestration session and optionally update status."""
    repo = _repo_root(repo_root)
    root = _session_dir(repo, session_id)
    root.mkdir(parents=True, exist_ok=True)
    decisions = root / "decisions.md"
    with decisions.open("a") as f:
        f.write(f"\n## {_now()}\n\n{note}\n")
    state = _read_json(root / "state.json")
    if status:
        state["status"] = status
    state["updated_at"] = _now()
    _write_json(root / "state.json", state)
    return {"session_id": session_id, "status": state.get("status"), "decisions": str(decisions)}


@mcp.tool()
def collect_agent_results(session_id: str, repo_root: str | None = None) -> dict[str, Any]:
    """Collect persona output snippets from a filesystem session."""
    repo = _repo_root(repo_root)
    root = _session_dir(repo, session_id)
    results: dict[str, str] = {}
    for rel in ("scope.md", "diagnosis.md", "plan.md", "findings/in-context.md", "findings/cold.md", "findings/reconciled.md"):
        path = root / rel
        if path.exists():
            results[rel] = path.read_text()[:8000]
    return {"session_id": session_id, "session_dir": str(root), "results": results}
