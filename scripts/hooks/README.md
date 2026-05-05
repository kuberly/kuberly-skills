# Hooks shipped by kuberly-skills

Hooks are not deployed by APM into `.claude/hooks/`. Instead, the consumer
repo's `.claude/settings.json` references them in-place under the APM cache —
no copy step needed. Versioning is automatic: an `apm install` of a new
`kuberly-skills` tag refreshes the script.

## `orchestrator_route.py` — UserPromptSubmit hook

Pre-flight router for the `agent-orchestrator` flow. Reads the user prompt,
classifies it as trivial vs. infra-relevant, and looks up named entities
(`loki`, `eks`, `aurora`, ...) in `.claude/graph.json` *before* the
orchestrator spawns any subagent. Two outputs:

- **Graph slice** — for entities that *do* exist as nodes, the matching node
  ids are pasted into `additionalContext` so the orchestrator does not have
  to re-query MCP.
- **STOP banner** — when an entity is named in the prompt but absent from
  the graph (typical: "bump Loki memory" in a fork that does not deploy
  Loki), a hard banner tells the orchestrator to confirm with the user
  *before* dispatching personas to re-discover the absence. This is the
  primary token-burn the hook prevents.

The hook is stdlib-only Python 3, exits silently on any error (it must
never block the prompt), and runs in well under 50 ms.

### Wire-up — add to the consumer repo's `.claude/settings.json`

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"$CLAUDE_PROJECT_DIR/apm_modules/kuberly/kuberly-skills/scripts/hooks/orchestrator_route.py\"",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

`$CLAUDE_PROJECT_DIR` is set by Claude Code; the hook resolves
`.claude/graph.json` relative to it. If your repo vendors `kuberly_platform.py`
under a different path, the only requirement is that `.claude/graph.json`
exists — the SessionStart hook (also from this package) generates it.

### Updating the named-entity list

Open `orchestrator_route.py` and edit the `NAMED_ENTITIES` tuple. Anything
listed there is checked against graph node labels; anything *not* listed is
ignored (so the STOP banner only fires for known infra primitives, not for
random user vocabulary).
