# Kuberly agent skills

Central **Agent Skills** for **kuberly-stack** customer forks and internal use. Hosted on **GitHub** (`github.com/kuberly/kuberly-skills`).

Do not commit org-specific IAM ARNs, internal-only URLs, or customer PII into shared skills. Tenant-specific content belongs in **`.apm/skills/overlays/`** (if you use it) with strict access controls, or in generated fork-only files.

## Not using kuberly-stack? Steal these.

These skills are useful regardless of stack — no kuberly-specific tooling required:

| Skill | What it gives you |
|-------|-------------------|
| **`loki-logql-alert-patterns`** | LogQL anti-patterns, cardinality hazards, alert recipes |
| **`cloudtrail-last-hour-all-regions`** | Multi-region CloudTrail `lookup-events` with pagination; when to switch to Athena |
| **`vpc-flow-logs-source-destination-grouping`** | Source/destination grouping in CW Insights and Athena |
| **`kubernetes-finops-workloads`** | PromQL recipes for usage vs. requests/limits and ranking waste |
| **`ecs-observability-troubleshooting`** | ECS service triage — logs, events, ALB |
| **`troubleshooting-aws-observability`** | Incident routing across CloudWatch / CloudTrail / EKS |
| **`irsa-workload-identity`** | Bind K8s ServiceAccounts to cloud IAM (EKS / GKE / AKS); the four common breakages |
| **`secrets-rotation-lifecycle`** | Dual-credential rotation, retire gates, audit checks across DB/API key/TLS |
| **`helm-chart-authoring`** | Values shape, schema validation, dependencies, anti-patterns |
| **`schema-migration-safety`** | Expand-contract for DB schema changes; backfill / index / NOT NULL patterns |
| **`mcp-tool-authoring`** | Designing MCP tool surfaces agents can actually use — schemas, errors, auth |

The remaining skills assume the kuberly-stack layout (Terragrunt monorepo, OpenSpec, `shared-infra.json`, `kuberly-graph` MCP). They're still readable as patterns; adapt or skip.

## Layout

| Path | Purpose |
|------|---------|
| `.apm/skills/<name>/` | Each skill is a directory with **`SKILL.md`** at the leaf (flat layout for APM deploy). |
| **Claude Code** | After **`apm install`** in the infra repo, APM copies the same skills into **`.claude/skills/`** when that tree exists — see **`apm-skills-bootstrap`** and **kuberly-stack** **`.claude/README.md`**. |
| **`docs/RUNTIME_SKILLS.md`** | How ECS/EKS runtime packs are named in **`.apm/skills/`** |
| `.apm/skills/overlays/` | Optional tenant-specific skills |
| `references/` | Long-form markdown linked from `SKILL.md` |
| `scripts/validate-skills.sh` | Local + CI validation |

Skills follow the [Agent Skills](https://agentskills.io) layout: each skill is a directory containing **`SKILL.md`** with YAML frontmatter (`name`, `description`).

## Index (universal)

| Skill | Use when |
|-------|----------|
| **`kuberly-stack-context`** | First orientation in the monorepo |
| **`components-vs-applications`** | `components/` vs `applications/` and RAG / retrieval hints |
| **`kuberly-gitops-execution-model`** | Kuberly-side plan/apply from git; branch ↔ many clusters & app envs |
| **`short-session-memory`** | Ephemeral debug loop vs durable git / OpenSpec / PR notes |
| **`detect-runtime-from-shared-infra`** | Decide ECS vs EKS vs Lambda from JSON |
| **`terragrunt-local-workflow`** | `CLUSTER_NAME`, `KUBERLY_ROLE`, local `terragrunt run plan` |
| **`kuberly-cli-customer`** | Customer **`scripts/kuberly-cli-customer.bash`** wrapper |
| **`troubleshooting-aws-observability`** | Incident routing (CloudWatch / CloudTrail / EKS split) |
| **`cloudtrail-last-hour-all-regions`** | `lookup-events` for the last hour, every region (pagination; when to use Athena) |
| **`vpc-flow-logs-source-destination-grouping`** | Group flow logs by src/dst (Insights, Athena, hygiene) |
| **`mcp-tool-authoring`** | Designing MCP tool surfaces — schemas, pagination, errors, auth |
| **`irsa-workload-identity`** | K8s ServiceAccount → cloud IAM (EKS / GKE / AKS) with the four common breakages |
| **`secrets-rotation-lifecycle`** | Dual-credential rotation, retire gates, audit (DB / API key / TLS) |
| **`helm-chart-authoring`** | Helm chart skeleton, values shape, schema validation, anti-patterns |
| **`schema-migration-safety`** | Expand-contract migrations; backfill / index / NOT NULL patterns |
| **`github-reusable-ci-kuberly-stack`** | App repo CI calling **kuberly-stack** reusable GitOps / ECR workflows |
| **`openspec-changelog-audit`** | Mandatory **`CHANGELOG.md`** per OpenSpec change (audit + collect from forks) |
| **`infra-change-git-pr-workflow`** | Pick the merge base (integration branch or current long-lived branch) → feature branch → commit → PR with Problem/Solution/OpenSpec/Testing/Risks + Mermaid |
| **`pre-commit-infra-mandatory`** | Install hooks, run pre-commit, re-add and re-commit after auto-fixes |
| **`application-env-and-secrets`** | `env_vars`, `env.secrets`, and `components/.../secrets.json` + empty SM secrets |
| **`apm-skills-bootstrap`** | Clone → `apm install` (default **Caveman** + add org **skills**), hooks, which skills to use for infra work |
| **`git-pr-templates`** | Paste-ready PR bodies for **skills** repo vs **infra fork** (pairs with **`infra-change-git-pr-workflow`**) |
| **`infra-orchestrator`** | Top-level Orchestrator mode for non-trivial infra work — delegates Explore / Implement / Review / Verify subagents, manages shared `.agents/prompts/` context, enforces plan-only and OpenSpec gates |
| **`revise-infra-plan`** | Interview-style plan revision (sub-flow of **`infra-orchestrator`** or standalone) — walks the design tree env-by-env, OpenSpec, IAM, shared-infra blast |
| **`infra-self-review`** | Post-change review loop — Verify (pre-commit + plan) then in-context + out-of-context Review subagents in parallel, fix and repeat until clean |

## Index (runtime)

| Skill | Use when |
|-------|----------|
| **`eks-observability-stack`** | Grafana / Prometheus / Loki / Tempo namespaces and secrets on EKS |
| **`kubernetes-finops-workloads`** | PromQL FinOps: usage vs requests/limits, rank waste, Helm values via git |
| **`ecs-observability-troubleshooting`** | ECS services — logs, events, ALB |
| **`loki-logql-alert-patterns`** | LogQL anti-patterns, cardinality hazards, alert recipes |

## Releases

1. Merge via pull request (use checklist in **`docs/CONTRIBUTING.md`**).
2. Tag **`vMAJOR.MINOR.PATCH`** on the commit customers should pin.
3. In a **kuberly-stack** fork **`apm.yml`**, depend on this repo with a **ref** (tag or commit), for example:

```yaml
dependencies:
  apm:
    - git: https://github.com/kuberly/kuberly-skills.git
      ref: v0.7.1
```

Or HTTPS one-liner (see [APM dependencies](https://microsoft.github.io/apm/guides/dependencies/)):

```yaml
  apm:
    - https://github.com/kuberly/kuberly-skills.git#v0.7.1
```

Public GitHub access needs no token. APM reads **`GITHUB_TOKEN`/`GH_TOKEN`** if present (rate-limit relief or private mirrors).

4. Run **`apm install`** in the fork and commit the updated **`apm.lock.yaml`**.

## Local validation

```bash
./scripts/validate-skills.sh
```
