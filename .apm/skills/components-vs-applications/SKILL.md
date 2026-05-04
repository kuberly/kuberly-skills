---
name: components-vs-applications
description: >-
  Distinguish kuberly-stack components/ (shared cluster/platform JSON) from applications/
  (one JSON per app); includes RAG / retrieval hints for infra monorepos.
---

# Components vs applications (kuberly-stack)

Use this skill when the agent must choose **where to edit** or **how to explain** platform vs workload config in a **kuberly-stack** fork or clone.

## Mental model

| | **`components/<cluster>/`** | **`applications/<env>/`** |
|---|------------------------------|----------------------------|
| **What it is** | **Shared platform** pieces for a named cluster (or maintainer sandbox), one concern per file in many setups | **One deployable application** per file (one ECS service, one Lambda, one AgentCore agent, or one CUE/K8s app) |
| **Granularity** | Often **one JSON file ↔ one Terragrunt module family** (EKS, RDS, shared secrets, …) — filenames like `eks.json`, `rds.json` | **Exactly one app** per filename (`backend.json` → app name `backend`) |
| **Typical env vars for runs** | `CLUSTER_NAME` / `COMPONENT_DIR` patterns (see **`terragrunt-local-workflow`**, **`kuberly-cli-customer`**, fork **`AGENTS.md`**) | `APPLICATION_DIR` + `APPLICATION_NAME` for **`ecs_app`**, **`lambda_app`**, **`bedrock_agentcore_app`**; CUE reads the same paths |
| **Secrets** | Cluster-wide or shared secrets files (e.g. `secrets.json`) — coordinate with **`application-env-and-secrets`** | Per-app `env_config.secrets` (Lambda), ECS patterns, AgentCore `agent.secrets`, etc. |

**Rule of thumb:** if the user says “**the cluster** / **EKS** / **RDS** / **shared Argo**”, start under **`components/`**. If they say “**this service** / **this Lambda** / **image tag for api**”, start under **`applications/<env>/`**.

**GitOps note:** one **git branch** can still carry edits for **multiple** cluster folders and **multiple** application envs; **Kuberly** selects the Terragrunt target per run via env vars — **`kuberly-gitops-execution-model`**.

## Authoritative long-form

- **Applications** schema and examples: **`applications/README.md`** in the infra repo (ECS, Lambda, AgentCore, CUE shapes).
- **Terragrunt + JSON** conventions: **`INFRASTRUCTURE_CONFIGURATION_GUIDE.md`**, **`MODULE_CONVENTIONS.md`**.
- **OpenSpec** scope includes **both** trees when behavior or contracts change.

## RAG and retrieval (infra repo)

Vector or hybrid RAG helps most when the model must **disambiguate trees** and **module entrypoints** without reading the whole monorepo.

### Prefer before embeddings

1. **Curated skills** (this file, **`kuberly-stack-context`**) — cheap, versioned with the agent package.
2. **Read-only MCP or internal search** over markdown — see **`docs/agent-packaging/RAG_AND_MCP_ROADMAP.md`** in **kuberly-stack** (MCP before vector index).

### If you build an index (suggested chunk sources)

- **Always include (low churn, high signal):** `AGENTS.md`, `applications/README.md`, `ARCHITECTURE.md`, OpenSpec pointer docs, module READMEs under `clouds/*/modules/*/README.md` where they exist.
- **Components:** short **synthetic** doc pages or “card” summaries per module **name** (what JSON file keys mean, required `CLUSTER_NAME`) — avoid embedding **raw secrets** or **account-specific** JSON values.
- **Applications:** one chunk per **doc section** in `applications/README.md` plus **sanitized** `dev/example-*.json` (strip ARNs, domains, real image repos if needed).
- **Chunk boundaries:** split at **headings** and **top-level JSON keys**; cross-link “this app module reads `applications/{env}/{name}.json`” in metadata.

### Safety

- Do not index **live** `secrets.json` **values**; index **structure** docs only.
- Pin retrieved snippets to **git SHA** in citations so answers stay reviewable.

### Deeper checklist

See **`references/rag-index-hints.md`** in this skill directory (same repo).

## Related skills

- **`kuberly-stack-context`** — first orientation.
- **`short-session-memory`** — keep hypotheses in-thread; persist decisions in git/OpenSpec.
- **`application-env-and-secrets`** — env and Secrets Manager patterns.
