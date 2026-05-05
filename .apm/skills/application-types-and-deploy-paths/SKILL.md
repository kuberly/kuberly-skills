---
name: application-types-and-deploy-paths
description: Pick the right deploy module + manifest shape when adding or modifying an application JSON. Discriminates `argo-app`, `deployment`, `ecs`, `lambda`, and `bedrock_agentcore` by the JSON's top-level keys, points at the module that renders each, and notes the rendering pipeline (CUE -> ArgoCD ApplicationSet vs ECS task def vs Lambda zip).
---

# Application types and deploy paths

A kuberly-stack consumer puts each application's config under
`applications/<env>/<name>.json`. **Five distinct shapes** can appear in
that JSON; each maps to a different module and rendering pipeline. Pick
the right one before editing — the wrong shape silently produces an
ApplicationSet sync error or a no-op terragrunt plan.

## At a glance

| Top-level key (or `application.type`)         | Runtime               | Module                         | Pipeline                                                  |
|-----------------------------------------------|-----------------------|--------------------------------|-----------------------------------------------------------|
| `argo-app`                                    | `argo-app`            | `module:aws/argocd`            | CUE renders K8s manifests, ArgoCD ApplicationSet syncs    |
| `deployment` (no `application` block)         | `deployment` (legacy) | `module:aws/argocd`            | Same as `argo-app` — older shape, still supported         |
| `application.type = "ecs"`                    | `ecs`                 | `module:aws/ecs_app`           | Terragrunt renders ECS task def + service + ALB target    |
| `application.type = "lambda"`                 | `lambda`              | `module:aws/lambda_app`        | Terragrunt packages function + IAM + event sources        |
| `application.type = "bedrock_agentcore"`      | `bedrock_agentcore`   | `module:aws/bedrock_agentcore_app` | Terragrunt provisions a Bedrock AgentCore endpoint    |

The shapes are **mutually exclusive** within a single JSON. An app is one
thing — never both an ArgoCD app *and* an ECS service. If a JSON appears
to carry overlapping keys, the top-level discriminator wins (`argo-app`
> `deployment`-without-`application` > `application.type`).

## Shape 1 — `argo-app` (current EKS+CUE+ArgoCD)

```json
{
  "argo-app": {
    "deployment": {
      "container": {
        "image": { "repository": "...", "tag": "..." },
        "env": { "env_vars": {...}, "secrets": [...] },
        "livenessProbe": { "type": "httpGet", ... },
        "readinessProbe": { "type": "httpGet", ... },
        "pod": { "volumes": [...] }
      },
      "replicas": 2,
      "port": 8080,
      "ingress": {...}
    },
    "workers": {
      "deployments": [
        { "scaledObject": { "triggers": [{ "type": "redis", ... }] }, ... }
      ]
    }
  }
}
```

- **Where it deploys:** an ArgoCD `ApplicationSet` defined inside
  `clouds/aws/modules/argocd/config.tf` (or the env-specific `argo.tf`)
  iterates over `applications/<env>/*.{json,cue}`, runs CUE to render
  Kubernetes manifests, and creates an ArgoCD `Application` per app.
- **Editing it:** change values inside `argo-app.deployment.*` /
  `argo-app.workers.*`. No HCL change needed for typical updates —
  ArgoCD picks up the diff on next sync.
- **Adding a new app:** drop a new `applications/<env>/<name>.json`.
  ApplicationSet auto-discovers it on next argocd sync. *No new
  Terragrunt module is required.*
- **Common fields:** `container.image.{repository, tag}`,
  `container.env.{env_vars, secrets}`, `replicas`, `ingress`,
  `serviceAccount`, `schedulingProfiles`, `pod.volumes`.

## Shape 2 — `deployment` (legacy EKS+CUE+ArgoCD)

```json
{
  "deployment": { "container": {...}, "replicas": 2, "port": 8080 },
  "workers": [...],
  "common": { "cluster_name": "prod", ... },
  "releaseMetadata": { "image_tag": "abc1234", ... }
}
```

- **Same pipeline** as `argo-app` — CUE -> ArgoCD ApplicationSet ->
  Kubernetes. The only difference is the JSON's outer wrapping.
- New apps should prefer the `argo-app` shape; the `deployment`-only
  shape is preserved for backward compatibility with older
  ApplicationSet templates.

## Shape 3 — `application.type = "ecs"`

```json
{
  "application": { "name": "admin-delight", "type": "ecs", "environment": "dev" },
  "common":      { "cluster_name": "dev-cluster", "vpc_name": "dev-vpc",
                   "architectures": ["ARM64"], "tags": {...} },
  "monitoring":  { "alarms": {...}, "cloudwatch": {...} },
  "deployment":  { "image": {...}, "cpu": 512, "memory": 1024, ... }
}
```

- **Where it deploys:** `clouds/aws/modules/ecs_app/` reads the JSON,
  renders ECS task def + service + (optionally) ALB target group +
  Service Connect entry + autoscaling.
- **Adding a new app:** drop the JSON, then add a Terragrunt component
  invocation under `components/<env>/ecs_apps/<name>/` (one per app).
  Conventionally a `terragrunt.hcl` calls `module:aws/ecs_app`.
- **Cluster prerequisite:** `clouds/aws/modules/ecs_infra` must already
  have provisioned the ECS cluster + supporting infra in the env.

## Shape 4 — `application.type = "lambda"`

```json
{
  "application": { "name": "...", "type": "lambda", "environment": "dev" },
  "common":      { "cluster_name": "dev-cluster", ... },
  "deployment":  { "package_type": "Zip" | "Image",
                   "handler": "index.handler",
                   "runtime": "nodejs20.x",
                   "memory_size": 512,
                   "timeout": 30,
                   "env_vars": {...},
                   "event_sources": [...] }
}
```

- **Where it deploys:** `clouds/aws/modules/lambda_app/` provisions the
  function + IAM execution role + event source mappings.
- **Event sources:** API Gateway, EventBridge, SQS, S3, DynamoDB streams
  — all configured inside the JSON, no per-source extra modules.

## Shape 5 — `application.type = "bedrock_agentcore"`

```json
{
  "application": { "name": "...", "type": "bedrock_agentcore", "environment": "dev" },
  "deployment":  { "agent_id": "...", ... }
}
```

- **Where it deploys:** `clouds/aws/modules/bedrock_agentcore_app/`.
- Niche — most repos won't have any. Mention only if the consumer asks
  about Bedrock-backed agents.

## Decision flow for `add new application` / `add new database`

```
read user's intent + existing app JSONs in repo
|
+-- repo has applications/<env>/*.json with `argo-app` key?
|       => use shape 1 (`argo-app`); no new HCL component needed
+-- repo has them with `deployment` key but no `application` block?
|       => use shape 2 (legacy `deployment`); same flow as shape 1
+-- repo has `clouds/aws/modules/ecs_app/`?
|       => offer shape 3 (`application.type = "ecs"`) for HTTP services
+-- repo has `clouds/aws/modules/lambda_app/`?
|       => offer shape 4 (`application.type = "lambda"`) for event-driven /
|          short-lived workloads
+-- otherwise:
        ask the user — repo doesn't expose any of these patterns yet
```

The `kuberly-platform` MCP populates `runtime` on every application
node in `.claude/graph.json`. Call
`mcp__kuberly-platform__query_nodes(node_type="application", environment="<env>")`
and inspect the `runtime` field to know what shape the existing apps
use **before** authoring a new one — match what's there unless the user
explicitly asks for a different shape.

## Common pitfalls

- **Mixing shapes in one JSON.** Don't put both a top-level `deployment`
  AND a top-level `argo-app` — the `argo-app` wrapper wins, the bare
  `deployment` block becomes dead config.
- **Using `argo-app`/`deployment` shape but creating a Terragrunt
  component anyway.** ArgoCD ApplicationSet does the work; the redundant
  Terragrunt component will plan a no-op or worse, conflict.
- **Using ECS/Lambda shape without the cluster module.** The
  per-app module assumes `ecs_infra` / lambda VPC + IAM scaffolding
  already exists. If you add the app first, plan will fail on missing
  data sources.
- **Cluster-name mismatch.** `common.cluster_name` must match a real
  cluster. Cross-check against
  `components/<env>/shared-infra.json: target.cluster.name`.

## What to dispatch

For "add new application" the orchestrator's `plan_persona_fanout`
returns `task_kind = "new-application"`, DAG: scope-planner ->
agent-infra-ops -> review/reconcile. The `agent-infra-ops` should load this
skill and consult the graph (`runtime` field on existing apps in the
target env) before deciding which shape to author.
