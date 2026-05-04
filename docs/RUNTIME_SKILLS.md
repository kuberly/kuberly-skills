# Runtime skill packs

Runtime-specific skills live as **peer directories** next to universal skills under **`.apm/skills/`**. They cover the layer where the application actually runs — the kind of guidance that only makes sense once the runtime is known.

| Today's runtime packs | What they cover |
|------------------------|-----------------|
| **`eks-observability-stack`** | Grafana / Prometheus / Loki / Tempo namespaces and secrets on EKS |
| **`ecs-observability-troubleshooting`** | ECS service triage — logs, events, ALB health checks |
| **`kubernetes-finops-workloads`** | PromQL recipes for usage vs. requests/limits (any K8s, but lives here because it leans on Prometheus) |
| **`loki-logql-alert-patterns`** | LogQL anti-patterns and alert recipes (any Loki) |

## Universal vs runtime — the rule of thumb

| Question | Universal | Runtime |
|----------|-----------|---------|
| Would this guidance still apply if the workload moved to a different runtime? | ✅ universal | ❌ runtime |
| Does the skill mention `kubectl`, ECS task definitions, Lambda config, etc. by name? | ❌ | ✅ |
| Is the audience an agent that already knows the workload's runtime (from `detect-runtime-from-shared-infra` or equivalent)? | either | ✅ |

**Examples:**
- "How to write expand-contract migrations" → universal (`schema-migration-safety`).
- "How to wire a migration as a Helm `pre-install` hook" → would belong in a Helm/EKS-flavoured runtime skill if it grew large.
- "PromQL CPU rank query" → runtime (Prometheus is K8s-flavoured here).
- "How an MCP tool should report errors" → universal (`mcp-tool-authoring`).

When in doubt, start as universal. Promote to a runtime pack only when the universal version starts collecting too many `if runtime == ...` branches.

## Authoring a new runtime pack

Naming: **`<runtime>-<concern>`** in kebab-case. Match the prefix used by existing packs so agent search is predictable:

| Runtime | Prefix |
|---------|--------|
| EKS | `eks-` |
| ECS | `ecs-` |
| Lambda | `lambda-` |
| Cloud Run | `cloudrun-` |
| Generic Kubernetes | `kubernetes-` |
| Loki | `loki-` |
| Prometheus | `prometheus-` |

Each pack is a directory under `.apm/skills/<name>/` with `SKILL.md` + optional `references/`. Frontmatter (`name`, `description`) is mandatory — `scripts/validate-skills.sh` enforces it on every push.

**Description rule for runtime packs:** lead with the runtime name so agents searching "ECS" or "EKS" land on the right skill on the first try. Compare:

> "Triage CloudWatch + ALB + ECS events when a service is unhealthy. Use after `detect-runtime-from-shared-infra` confirms ECS."

vs. the wrong shape (runtime buried):

> "When a service has problems, this skill explains how to look at logs and events and the load balancer."

## Where the skill index lives

- README.md `## Index (universal)` and `## Index (runtime)` are the canonical lists. The validation script enforces parity with `.apm/skills/`.
- `apm install` deploys every skill in the package — agents see all of them. Splitting universal vs runtime is for **human** orientation, not deploy mechanics.

## Customer overlays

Tenant-specific runtime guidance (one customer's particular ECS cluster pattern, one fork's Lambda layer story) belongs in **`.apm/skills/overlays/<tenant>/`**, not in the public packs above. Overlays are opt-in — see **`.apm/skills/overlays/README.md`**.

## Versioning

Runtime packs ship together with universal skills under the same `vMAJOR.MINOR.PATCH` tag (this repo's `apm.yml` `version`). Bump:

- **PATCH** for content fixes inside an existing pack.
- **MINOR** for adding a new pack or changing its description meaningfully (existing pinned consumers still work; the new pack is additive).
- **MAJOR** for renaming or removing a pack (breaks pinned consumers — coordinate).

See the **Releases** section in `README.md` for the tag-and-pin flow.
