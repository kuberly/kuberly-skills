---
name: application-env-and-secrets
description: >-
  Add plain env vars or Secrets Manager-backed env to application JSON (EKS/CUE apps), and add
  empty secrets via components secrets.json for the secrets module before wiring keys into apps.
---

# Application env vars and Secrets Manager wiring

Use this skill when changing **`applications/<env>/*.json`** (Kubernetes-style apps: **`deployment`**, **`workers`**, **`cronJobs`**, **`jobs`**, etc.) **and/or** **`components/<cluster>/secrets.json`** so workloads read values from **AWS Secrets Manager**.

Authoritative schema: **`APPLICATION_CONFIGURATION_GUIDE.md`** (**`EnvConfig`**, **`ContainerSpec`**). Infra side: **`INFRASTRUCTURE_CONFIGURATION_GUIDE.md`** (secrets module, **`secretsmanager_secrets.json`** example). OpenSpec applies to meaningful infra/app JSON edits per **`AGENTS.md`**.

## Two layers (do not confuse them)

| Layer | File | What it does |
|-------|------|----------------|
| **Infra — empty Secrets Manager secrets** | **`components/<cluster>/secrets.json`** (root key **`secrets`**, consumed by **`clouds/aws/modules/secrets`**) | Terraform creates **`aws_secretsmanager_secret`** **shells only** — **no secret value** in state. You **populate values** after apply (Console or **`aws secretsmanager put-secret-value`**). |
| **App — inject into pods** | **`applications/<env>/<app>.json`** under **`container.env`** (or equivalent **`EnvConfig`** path for workers/cron/jobs) | **`env_vars`**: plain strings. **`secrets`**: array of **`{ "envVar", "secretName", "key" }`** — **`envVar`** is the process env name; **`secretName`** is the **Kubernetes Secret** name the cluster uses (must match what your platform syncs from SM); **`key`** is the **key inside** that K8s secret (usually matches the **JSON key** you store in Secrets Manager). |

Some forks name the component file **`secretsmanager_secrets.json`** with the same idea — follow what exists under **`components/<cluster>/`**.

## 1. Add or extend `components/<cluster>/secrets.json`

Pattern (matches **`variables.secrets`** on the secrets module — empty placeholders):

```json
{
  "secrets": {
    "my-logical-key": {
      "name": "my-app-secrets",
      "description": "Backend secrets for my-app",
      "recovery_window_in_days": 30,
      "tags": {
        "Application": "my-app"
      }
    }
  }
}
```

- **`name`** — **Secrets Manager secret name** (globally unique per account/region).
- **`my-logical-key`** — Terraform **`for_each`** key; choose a stable identifier (not the SM name unless you want them aligned).

After **`terragrunt apply`** on the **secrets** module, the secret exists **empty**. Until **`put-secret-value`**, apps that read missing keys will fail at runtime.

### Optional: placeholder / generated values (local only)

For **dev** or **bootstrap**, you may set an **initial** secret string (still do **not** commit real prod secrets into git):

```bash
# Single string secret
openssl rand -base64 32 | aws secretsmanager put-secret-value \
  --secret-id my-app-secrets \
  --secret-string file:///dev/stdin

# JSON secret with multiple keys (keys must match what app JSON "key" fields expect)
aws secretsmanager put-secret-value --secret-id my-app-secrets --secret-string '{
  "API_KEY":"'"$(openssl rand -hex 16)"'",
  "DATABASE_URL":"postgres://user:pass@host:5432/db"
}'
```

Prefer **Console** or your **secret pipeline** for production; never paste production values into agent chat logs.

## 2. Put values in Secrets Manager (human / CI)

```bash
aws secretsmanager put-secret-value \
  --secret-id <same as .name in secrets.json> \
  --secret-string '<json-or-string-matching-keys>'
```

If the secret stores **JSON**, each **`key`** in application **`env.secrets`** must exist as a top-level property in that JSON (unless your ExternalSecret template maps differently — **verify in your fork**).

## 3. Wire the application JSON

### Plain environment variables

Under the right **`container`** (or worker/cron/job container), set:

```json
"env": {
  "env_vars": {
    "LOG_LEVEL": "info",
    "FEATURE_FLAG_NEW_AUTH": "true"
  }
}
```

### Environment variables from secrets

Add or extend the **`secrets`** array next to **`env_vars`**:

```json
"env": {
  "env_vars": {
    "ENV": "dev"
  },
  "secrets": [
    {
      "envVar": "DATABASE_PASSWORD",
      "secretName": "my-app-secrets",
      "key": "DATABASE_PASSWORD"
    }
  ]
}
```

- **`envVar`** — name the container process sees (`os.environ` / `process.env`).
- **`secretName`** — **Kubernetes Secret** resource name that must exist in the app namespace (commonly produced by **External Secrets** from the SM secret **`name`** — **keep naming consistent** with your cluster’s sync rules).
- **`key`** — field inside the K8s secret data; for SM JSON secrets this is typically the **same string** as the JSON property name.

**ECS split apps** (`applications/...` **`ecs-app`**): env may live under **`service.environment_variables`** and a different secrets shape if your fork extended **`example-ecs.json`** — read the **actual** app JSON and **`APPLICATION_CONFIGURATION_GUIDE.md`** section for **ECS** before editing.

## 4. Safe order of operations

1. Add **`secrets.json`** entry → **`terragrunt run plan`** / apply **secrets** module (human approves apply).
2. **`put-secret-value`** with real or dev-only payload.
3. Add **`env.secrets`** (and **`env_vars`**) in **application** JSON → plan/apply **app** stack (ArgoCD / **`kubernetes_*`** modules per your layout).

## 5. Agent and hygiene

- **Plan-only** for agents unless the user asked for apply.
- **OpenSpec** when required by repo policy.
- **`pre-commit-infra-mandatory`** before commit.
- Do **not** commit **real** secret values into **`applications/*.json`** or **`secrets.json`** — only **references** and **non-secret** defaults in **`env_vars`**.
