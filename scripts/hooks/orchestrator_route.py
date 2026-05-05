#!/usr/bin/env python3
"""UserPromptSubmit hook: nudge the main agent toward `agent-orchestrator`.

Reads the hook payload from stdin, classifies the prompt as trivial vs.
non-trivial infra work, performs a pre-flight graph existence check, and
emits a single JSON object on stdout that Claude Code injects as
`additionalContext`.

The pre-flight check is the cost-saving piece: when the prompt names an
infra entity (loki, eks, aurora, ...), we look it up in `kuberly/graph.json`
*before* the orchestrator spawns any subagent. If the entity is absent we
prepend a STOP banner; if present we paste the matching node ids as a graph
slice so the orchestrator doesn't re-query.

Hard requirements:
- stdlib only, < 50 ms runtime budget
- never block the user prompt: any error -> exit 0 silently
- never write to stderr in a way that confuses the harness
- diagnostic logging (if needed) goes to /tmp/orchestrator_route.log
"""

import json
import os
import re
import sys

# --- classification tables -------------------------------------------------

# Lower-cased substrings that indicate infra-relevant work.
# Order doesn't matter; we OR them.
INFRA_KEYWORDS = (
    # IaC tooling
    "terragrunt", "tofu", "terraform", "opentofu",
    "module", "component", "shared-infra",
    # Kubernetes / clusters
    "eks", "gke", "aks", "helm", "karpenter", "irsa", "serviceaccount",
    "kubernetes", "kubectl", "argocd", "argo cd",
    # Observability
    "loki", "prometheus", "alloy", "grafana", "tempo", "logql", "promql",
    # Networking / security / data
    "vpc", "subnet", "iam", "kms", "secret", "aurora", "redis", "nats",
    "cloudfront", "route53", "alb", "nlb",
    # App + config
    "application", "app config", "cue", "applications/",
    # Lifecycle verbs
    "apply", "plan", "destroy", "deploy", "provision", "rollout",
    "rotate", "migrate", "bootstrap",
    # OpenSpec
    "openspec",
)

# Entities we expect to find as nodes in `kuberly/graph.json`. Subset of
# INFRA_KEYWORDS — only the *named things* (modules, components, apps), not
# verbs or generic terms. Use hyphens (graph labels are normalized that way).
NAMED_ENTITIES = (
    # observability stack
    "loki", "prometheus", "grafana", "tempo", "alloy",
    # cluster runtimes
    "eks", "ecs", "gke", "aks", "knative",
    # data / messaging
    "aurora", "redis", "nats", "temporal", "dkron",
    # platform
    "karpenter", "argocd", "harbor", "forgejo", "github-arc", "adminer",
    "bedrock", "cloudflared", "external-secrets",
    # AWS primitives
    "vpc", "iam", "kms", "ecr", "s3", "secrets", "alb", "nlb",
    "route53", "cloudfront",
)

# Prompts that look like a quick lookup / read-only question -> suppress.
TRIVIAL_PREFIXES = (
    "what is", "what's", "whats",
    "where is", "where's", "wheres",
    "who is", "who's",
    "list ", "ls ", "show ", "display ",
    "explain ", "describe ", "summarize ",
    "tell me ", "remind me ",
    "how do i read", "how do i view", "how do i list",
)

TRIVIAL_EXACT = {"hi", "hello", "hey", "ping", "ok", "thanks", "thank you", "?"}

# Lifecycle verbs that ALWAYS mean non-trivial even if prompt is short.
STRONG_INFRA_VERBS = (
    "apply", "destroy", "deploy", "provision", "rollout",
    "rotate", "migrate", "bootstrap", "increase", "decrease",
    "bump", "upgrade", "downgrade", "refactor", "rename",
)

NUDGE_BASE = """[agent-orchestrator] non-trivial infra task. Routing:
1. Invoke `agent-orchestrator` skill before editing.
2. Graph-first: call `mcp__kuberly-platform__plan_persona_fanout` then `session_init`.
3. Sequential: scope-planner alone first; fan out only after scope confirms target exists.
4. New feature branch before any edit (see infra-bootstrap-mandatory).
5. Plan-only: no `apply`/`destroy`. Only `terragrunt run plan`, `validate`, `fmt`, lint.
6. Approval required for impl/cicd persona dispatch; read-only personas auto-run."""


def _emit_silent_exit(code: int = 0) -> None:
    """Exit without printing anything. Hook must never block the prompt."""
    sys.exit(code)


def _emit_context(text: str) -> None:
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": text,
        }
    }
    sys.stdout.write(json.dumps(payload))
    sys.stdout.flush()
    sys.exit(0)


def _looks_trivial(prompt: str, lower: str) -> bool:
    """Cheap heuristic for prompts that don't deserve a nudge."""
    stripped = lower.strip()
    if not stripped:
        return True
    if stripped in TRIVIAL_EXACT:
        return True
    # Single short question with no infra keywords.
    if len(stripped) < 80 and not any(k in lower for k in INFRA_KEYWORDS):
        return True
    if stripped.endswith("?") and not any(v in lower for v in STRONG_INFRA_VERBS):
        # Pure question — only nudge if it carries a strong verb.
        if any(stripped.startswith(p) for p in TRIVIAL_PREFIXES):
            return True
    if any(stripped.startswith(p) for p in TRIVIAL_PREFIXES):
        # Read-only question prefixes; skip unless it carries a strong verb.
        if not any(v in lower for v in STRONG_INFRA_VERBS):
            return True
    return False


def _graph_path() -> str:
    """Locate `kuberly/graph.json` relative to CLAUDE_PROJECT_DIR or cwd."""
    root = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    return os.path.join(root, "kuberly", "graph.json")


def _preflight_graph_check(lower: str):
    """Return (present_block, absent_block) strings, or ('', '').

    `present_block` lists entities that DO have matching nodes (as a graph
    slice the orchestrator can drop into context.md without re-querying).
    `absent_block` is a STOP banner for entities the prompt names that have
    no node in the graph — typical when a user says "bump loki" but loki
    isn't deployed in this fork.
    """
    mentioned = []
    for ent in NAMED_ENTITIES:
        # Word-boundary match; allow underscore or hyphen interchange in prompt.
        ent_alt = ent.replace("-", "[-_]")
        if re.search(r"\b" + ent_alt + r"\b", lower):
            mentioned.append(ent)
    if not mentioned:
        return "", ""

    try:
        with open(_graph_path(), "r", encoding="utf-8") as fh:
            graph = json.load(fh)
    except Exception:
        return "", ""

    nodes = graph.get("nodes") or []
    by_label = {}
    for n in nodes:
        lbl = (n.get("label") or "").lower().replace("_", "-")
        if not lbl:
            continue
        by_label.setdefault(lbl, []).append(n.get("id") or "")

    present_rows = []
    absent = []
    for ent in mentioned:
        ids = by_label.get(ent, [])
        if ids:
            shown = ",".join(ids[:6])
            if len(ids) > 6:
                shown += f"+{len(ids) - 6}"
            present_rows.append(f"{ent}={shown}")
        else:
            absent.append(ent)

    present_block = ""
    if present_rows:
        present_block = "\n\ngraph-slice:\n" + "\n".join(present_rows)

    absent_block = ""
    if absent:
        names = ", ".join(absent)
        absent_block = (
            f"\n\nSTOP target-absent: {names} not in graph. "
            "Confirm with user before any persona dispatch."
        )

    return present_block, absent_block


def main() -> None:
    # Read stdin; never raise.
    try:
        raw = sys.stdin.read()
    except Exception:
        _emit_silent_exit(0)

    if not raw or not raw.strip():
        _emit_silent_exit(0)

    try:
        payload = json.loads(raw)
    except Exception:
        _emit_silent_exit(0)

    if not isinstance(payload, dict):
        _emit_silent_exit(0)

    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        _emit_silent_exit(0)

    # Normalize.
    lower = prompt.lower()
    # Collapse whitespace for prefix matching.
    lower = re.sub(r"\s+", " ", lower).strip()

    # Trivial -> silent.
    if _looks_trivial(prompt, lower):
        _emit_silent_exit(0)

    # Must contain at least one infra keyword to be considered infra-relevant.
    if not any(k in lower for k in INFRA_KEYWORDS):
        _emit_silent_exit(0)

    present_block, absent_block = _preflight_graph_check(lower)
    nudge = NUDGE_BASE + present_block + absent_block

    _emit_context(nudge)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        # Last-resort catch — never fail the hook.
        try:
            with open("/tmp/orchestrator_route.log", "a", encoding="utf-8") as fh:
                import traceback
                traceback.print_exc(file=fh)
        except Exception:
            pass
        sys.exit(0)
