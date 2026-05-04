---
name: mcp-tool-authoring
description: >-
  Author MCP (Model Context Protocol) tools that agents can actually use well — tool
  surface design, input schemas, pagination, structured errors, auth, and how to write
  descriptions agents will read at the right time.
---

# MCP tool authoring

Use this skill when you're **building** an MCP server (a set of tools an agent can call), not just consuming one. The tool **surface** you design is what makes an agent reliable or hopeless — schemas, descriptions, and error shapes carry as much weight as the underlying logic.

## The tool contract

Every MCP tool has three knobs the agent sees: **name**, **description**, **input_schema**. Get all three right.

| Field | Job | Common failure |
|-------|-----|----------------|
| `name` | Identifier the agent invokes | Generic (`query`, `get_data`); collides with siblings |
| `description` | When to use it; what comes back | Restates the name; missing the *when*; missing return shape |
| `input_schema` | JSON Schema for arguments | Free-form `string` blobs; no enums; no defaults |

**Description rule of thumb:** an agent should be able to pick the right tool from the description **without reading the code**. Mention: *what it returns* (one line), *required vs optional inputs*, and *when **not** to use it* (cheaper alternative? read-only sibling?).

## Tool surface design

**Granularity.** Too few large tools (`run_query`) hide behavior; too many tiny tools (`get_user_id_by_name`) bloat context. Aim for **one verb per resource**: `list_X`, `get_X`, `search_X`. Avoid omnibus tools that branch on a `mode` argument.

**Idempotency and side effects.** Mark write-side tools clearly in the description (`Creates a record. Use list_records first to avoid duplicates.`). Read-only tools should say so — agents will be more willing to call them speculatively.

**Pagination.** If a list can exceed a few KB, paginate. Convention:

```json
{
  "items": [...],
  "next_cursor": "opaque-string-or-null",
  "total": 1234
}
```

Document the cursor as **opaque** in the schema description so agents pass it back verbatim. Cap `limit` (default 50, max 200) so a single bad call can't blow the agent's context.

**Filter inputs.** Prefer **structured filters** over free-text predicates. `region: "us-east-1"` beats `query: "region=us-east-1"` — the agent and your server both win on validation.

## Input schema patterns

```json
{
  "type": "object",
  "properties": {
    "region": {
      "type": "string",
      "enum": ["us-east-1", "us-west-2", "eu-west-1"],
      "description": "AWS region. If omitted, defaults to the caller's account home region."
    },
    "limit": { "type": "integer", "minimum": 1, "maximum": 200, "default": 50 },
    "cursor": { "type": ["string", "null"], "description": "Opaque pagination cursor from a previous response." }
  },
  "required": ["region"],
  "additionalProperties": false
}
```

- **`enum`** for closed sets — saves the agent from guessing.
- **`additionalProperties: false`** — fail loud on typos.
- **`default`** values — let the agent omit them; document the default in the field description.

## Errors agents can act on

Return errors as **structured tool output**, not exceptions. Agents read text; give them text that tells them *what to do next*.

```json
{ "error": { "code": "rate_limited", "retry_after_seconds": 30, "message": "Throttled by upstream API." } }
```

Error code conventions:
- `not_found` — resource doesn't exist; the agent should stop, not retry.
- `invalid_argument` — schema-valid but semantically wrong; surface the offending field.
- `rate_limited` — include `retry_after_seconds`; the agent will back off.
- `unauthorized` — likely a config bug; do **not** retry; tell the user.

**Don't** raise on missing optional inputs. **Don't** silently degrade — return `partial: true` with what you got and a reason.

## Server flavors

| Flavor | When |
|--------|------|
| **stdio** | Local dev tools, IDE-side servers (Cursor, Claude Desktop). Lowest friction. |
| **HTTP** (streamable) | Hosted multi-tenant; auth at the edge; observability via standard HTTP middleware. |
| **SSE** | Largely superseded by streamable HTTP; keep for legacy clients. |

For hosted servers: terminate auth before the MCP layer (proxy / API gateway), pass the caller identity into tool handlers via a server-side context — never trust an `auth_token` argument from the agent.

## Authentication patterns

| Caller type | Pattern |
|-------------|---------|
| Personal dev workstation | Reuse existing **env vars** (`AWS_PROFILE`, `KUBECONFIG`, `GH_TOKEN`); document each one in the README. |
| Hosted multi-tenant | OAuth 2.1 (per MCP auth spec) or signed bearer; map token → tenant scope server-side. |
| AWS workloads | Prefer **IRSA / Workload Identity** over static creds — see **`irsa-workload-identity`**. |

Never expose raw cloud credentials as MCP tool inputs. The tool argument should be a **scope** (`account_id`, `region`), not a key.

## Testing

- **Schema tests:** every tool gets a `valid_inputs` and `invalid_inputs` fixture; assert validation rejects the latter.
- **Golden output:** snapshot one happy-path response per tool; diff on changes (forces description/shape stability).
- **Integration:** run the server in stdio mode and exercise it from the official Anthropic MCP **inspector** (or the Claude Desktop "Reload" loop) before shipping.

## Documenting for agents — not humans

The `description` field is read by the model **at every turn**. Treat it like prompt — keep under ~3 short sentences:

> List CloudTrail events from the last hour across all enabled regions. Returns events with `event_time`, `event_name`, `user_identity`, paginated by `next_cursor`. Use `cloudtrail_lookup_events` for older windows or single-region queries.

Compare to the wrong shape (verbose, restates name, no return info):

> This tool allows you to query CloudTrail events. It is useful when you want to find recent activity. You can specify various filters.

## Pair with

- **`cloudtrail-last-hour-all-regions`**, **`vpc-flow-logs-source-destination-grouping`** — example domains where MCP tools materially compress agent loops.
- **`irsa-workload-identity`** — server auth on AWS.

## kuberly-stack notes

The `kuberly-platform` MCP referenced across infra skills is an internal example of these patterns: tools are scoped per-tenant via a server-side context, return paginated graph slices, and use `code` errors so the orchestrator skills can branch on `not_found` vs `unauthorized`.
