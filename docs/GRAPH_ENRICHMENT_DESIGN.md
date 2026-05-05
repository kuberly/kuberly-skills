# Graph Enrichment — Design Note (v1 draft)

**Status:** draft — circulating for feedback before any code lands. v1 incorporates a cross-fork survey of consumer forks (anonymized) — see §3.5.
**Owner:** TBD.
**Tracks:** sibling to the rules-A/B work (input-edit-precedence + shared-infra spine warning) shipped in agents + `quick_scope`. Graph enrichment is the next layer up: make the graph itself richer so personas don't need to read files to learn basic facts.

---

## 1. Problem

Today's graph (`kuberly_platform.py`) is a **static parse** of repo files: 172 nodes / 423 edges describing modules, components, applications, environments, shared-infra, and the cloud provider. It tells the orchestrator *what exists in the codebase*. It does **not** tell the orchestrator:

- *Where* things actually live — region, account, cluster name, IAM role.
- *How big* deployments are — replica counts, instance classes, db sizes.
- *Whether* a module has even been applied — the difference between "declared in HCL" and "exists in AWS".
- *Which AWS resources* a module manages — for resource-level blast radius (e.g. "this RDS feeds these 3 lambdas via this secret").

Personas currently re-derive these by reading `shared-infra.json`, JSON sidecars, and (for runtime data) calling AWS APIs in shell. That's expensive, repetitive, and bypasses the cheap-pre-flight discipline the orchestrator enforces elsewhere.

## 2. Goals

- **G1.** Eliminate "is X deployed in env Y?" / "what region is the prod cluster in?" file reads — answer from one MCP call.
- **G2.** Enable scope.md to cite **resource-level** facts ("aurora-prod has 2 instances, instance_class db.r6g.large") so agent-infra-ops doesn't need to grep state.
- **G3.** Resource-level blast radius for high-value AWS types (RDS, EKS, Lambda, ECS, IAM roles, SQS, secrets) — much sharper than the current module-level radius.
- **G4.** Travel with the package — every customer fork gets the same enriched graph after `apm install`, no extra installation step.

## 3. Non-goals

- **NG1.** Real-time monitoring. Refresh cadence is bounded; agents must accept staleness.
- **NG2.** Cost or finops data. (Already covered by `kubernetes-finops-workloads` skill for k8s; AWS cost is a different surface.)
- **NG3.** Mutations. The enriched graph is read-only.
- **NG4.** Replacement for AWS Config / a CMDB. We enrich for *agent reasoning*, not asset inventory.
- **NG5.** Cross-org federation. Multi-account is in scope; multi-organization is not.

## 3.5 Cross-fork survey findings

Surveyed five kuberly-stack consumer forks. Layouts and conventions are converging on a single shape, but the historical divergences below still exist in some forks today and the scanner must accept them as input. Customer identities are not load-bearing for this design — characteristics are.

### 3.5.1 Repo layout (uniform target)

All forks use, or are migrating to, this layout:

```
<repo-root>/
  root.hcl
  clouds/<provider>/modules/<m>/        # terragrunt.hcl + .tf source colocated
  components/<env>/<m>.json             # per-component sidecar
  components/<env>/shared-infra.json    # cluster spine (own file)
  applications/<env>/<app>.json         # one JSON per app instance
```

`<provider>` is `aws`, with `azure` and `gcp` populated in some forks. v1 enrichment is AWS-only; non-AWS modules get static-parse nodes only.

### 3.5.2 Cluster-spine wiring patterns (8 observed)

Module `terragrunt.hcl` files read cluster identity through one of these patterns. Substring matching is fragile — prefer graph-structural signals.

| Pattern | Notes |
|---|---|
| `include.root.locals.cluster` | Direct read of root-exposed cluster object. |
| `include.env.locals.cluster_config` | Via an `env.hcl` intermediate include (used in DBs / multi-instance modules). |
| `local.cluster_config` / `locals.cluster_config` | Local rebinding after include. |
| `include.root.locals.<custom>_env` | Some forks expose a synthesized `<fork>_env` local from shared-infra. |
| `include.root.inputs` | Whole-components map exposed as inputs at root. |
| `include.root.locals.components` | Components map exposed as a root local. |
| `jsondecode(file(...))` direct | Fallback when expected include isn't populated. |
| `include.root.locals.aws_env` | Synthesized `{account_id, region, cluster_name, state_bucket, init_state_bucket}` slice. |

**Implication:** scanner relies on graph-structural signals (presence of `components/<env>/<m>.json`, presence of `aws_env`-shaped local in root.hcl) rather than HCL regex.

### 3.5.3 State-bucket formulas (4 variants)

| Formula | Notes |
|---|---|
| `${account_id}-${region}-${cluster_name}-tf-states` | **Default** — every fork. |
| `${account_id}-${region}-${cluster_name}-tf-init-state` | `init_state_bucket` — bootstrap state, every root.hcl. Not enriched in v1. |
| `${project}-${region}-tf-states` (cross-region singleton) | Some forks for shared resources like GitHub OIDC providers. |
| `${account_id}-${region}-${project}-tf-states` (project-keyed) | Some forks for resources scoped by `project` rather than `cluster`. |
| Hub-account formula (cross-account) | Some forks for IAM/identity resources kept in a hub account. |

**Implication:** scanner needs a **per-module override** mechanism. Default to standard; allow `kuberly.json.state_bucket` template string to override.

### 3.5.4 State-key conventions (5 variants)

| Convention | When used |
|---|---|
| `aws/<m>/terraform.tfstate` | Default for singleton modules. |
| `<m>/terraform.tfstate` (no `aws/` prefix) | Older / transitional layouts. |
| `aws/<m>/${component_name}/terraform.tfstate` | Multi-instance modules (rds, redis, valkey…). |
| `clouds/<provider>/modules/<m>/${app_dir}/${app_name}/terraform.tfstate` | Per-app keys with provider prefix. |
| `project/${project}/environment/${env}/<m>/${app_name}/terraform.tfstate` | Pre-kuberly-stack legacy convention. |
| `aws/<m>/${app_dir}/${app_name}/terraform.tfstate` | Per-app keys without provider duplication. |

**Implication:** state key is dynamic for multi-instance and per-app modules. Scanner enumerates `components/<env>/<m>*.json` and `applications/<env>/*.json` to materialize the actual key set. `kuberly.json.state_key` template (with `${component_name}`, `${app_name}`, `${app_dir}` placeholders) is the override mechanism.

### 3.5.5 shared-infra schema (loose)

Common core (every fork has these): `target.{account_id, region, cluster.{name, role_arn}}`.

Beyond the core, presence is fork-dependent:

- `cluster.version` — usually present.
- `cluster.eks_access_iam_users`, `cluster.enabled_log_types` — sometimes.
- `cluster.extra_irsa_namespace_service_accounts` — sometimes.
- `target.{ecs, eks, lambda}` rich blocks — varies.
- `target.vpc.{cidr_block, private_subnets[], public_subnets[]}` — usually present, but some forks read VPC topology from a VPC-module dependency rather than shared-infra.
- `vcs.*`, `org_slug`, `step_timeout` — some forks.
- `internal-ingress` — separate optional overlay JSON merged into cluster after shared-infra in some forks.

**Implication:** enrichment must not assume any key beyond the core. Everything else is best-effort and `try()`-guarded.

### 3.5.6 Cross-account / cross-region references

Three patterns observed across forks:

- **Cross-region singletons** — shared resources (GitHub OIDC, IAM users) live in a fixed hub region.
- **Hub-account state buckets** — identity-layer state in a different account than the workload.
- **Hardcoded partner-account ARNs** — e.g. cross-account Route53 delegation to a DNS account; partner account explicitly known and trusted.
- **`replication_region`** — cross-region read replicas declared in DB modules (often unset by default).

**Implication:** redaction (§6.4) must whitelist intentional cross-account refs. Treat ARNs whose account matches a **declared partner account** as kept; only redact unexpected/foreign accounts. This drives the `partner_accounts` field proposed below.

### 3.5.7 Environment / cluster naming (opaque)

`components/<env>/` folder name is the env id. Some forks use one env, some many; some use ephemeral feature-branch envs (`components/feature-*/`) created at PR time and removed on merge. Cluster names may equal env names or differ. **Treat env names as opaque user-controlled strings** — don't try to normalize or correct.

### 3.5.8 Per-app state files (high cardinality)

Modules whose state file count equals the number of application instances:

- `lambda_app`
- `ecs_app`
- `bedrock_agentcore_app`

**Implication:** Tier 2 must enumerate `applications/<env>/*.json` (not only `components/`) when these modules are in scope. Cap: 200 apps per cluster.

## 4. Scope (Hybrid)

Per the chosen direction in the orchestration session: **enrich existing nodes + add selective resource-level nodes**.

### Tier 1 — enrich existing nodes (no AWS calls)

Add attributes to current `module:` / `component:` / `application:` / `shared-infra:` nodes from data already on disk:

| Attribute | Source | Applies to |
|---|---|---|
| `region` | `components/<env>/shared-infra.json` `target.region` | every node in that env |
| `account_id` | shared-infra `target.account_id` | every node in that env |
| `cluster_name` | shared-infra `target.cluster.name` | every node in that env |
| `kuberly_role_arn` | shared-infra `target.cluster.role_arn` | environment, components |
| `cluster_version` | shared-infra `target.cluster.version` | env + eks module/component |
| `tf_state_bucket` | derived: `<account_id>-<region>-<cluster>-tf-states` | every module that has state |
| `tf_state_key` | catalog `state_key` if present, else `aws/<module>/terraform.tfstate` | each module |
| `instance_count` | from JSON sidecar where obvious (e.g. `instances`, `desired_count`) | components |

**Cost:** zero AWS calls. One pass over `components/*/*.json` + `clouds/*/modules/*/kuberly.json`. Drop-in extension to the current `scan_*` chain.

### Tier 2 — selective resource-level nodes (parse TF state from S3)

Materialize as graph nodes only the AWS resource types that drive blast radius / agent decisions. Everything else is summarized into a count attribute on the parent module node.

**Promoted to nodes (v1 set, decision 6 — 11 types):**

| Resource type | Why | Key attrs |
|---|---|---|
| `aws_eks_cluster` | central; many edges fan out | name, version, vpc_id, subnet_ids, oidc_provider_arn, public_endpoint, log_types |
| `aws_rds_cluster` / `aws_db_instance` | hot blast target | engine, engine_version, instance_class, instances, multi_az, backup_retention, deletion_protection |
| `aws_lambda_function` | per-function reasoning common | runtime, memory_size, timeout, role_arn, vpc_config_present, package_type |
| `aws_ecs_service` | per-service reasoning common | cluster, desired_count, launch_type, target_group_arn, task_definition_family |
| `aws_iam_role` | trust relationships drive cross-resource edges | name, assume_role_principals (sanitized), attached_policy_arns, path |
| `aws_secretsmanager_secret` | identity only — never values | name, kms_key_id, rotation_enabled |
| `aws_sqs_queue` / `aws_sns_topic` | inter-service messaging | name, fifo, dlq_arn, encrypted |
| `aws_kms_key` | encryption topology | key_id, alias, multi_region, rotation_enabled |
| `aws_efs_file_system` | shared storage often consumed via IRSA | name, performance_mode, throughput_mode, encrypted, mount_targets_count |
| `aws_elasticache_cluster` / `aws_elasticache_replication_group` | data plane access via IRSA | engine, engine_version, node_type, num_cache_nodes, automatic_failover_enabled, transit_encryption_enabled |
| `aws_msk_cluster` / `aws_msk_serverless_cluster` | streaming data plane | name, kafka_version, number_of_broker_nodes, instance_type, encryption_in_transit, encryption_at_rest |

**Stays as a count on the module node:**
- IAM policies, security group rules, VPC endpoints, CloudWatch log groups, DNS records, ACM cert validations, S3 bucket policies. (High volume, low per-instance signal.)

**Edges (v1):**
- `module → resource` — `manifests` (the module's terraform state declares this resource).
- `resource → resource` — `references` (derived from terraform state attribute references, e.g. RDS `db_subnet_group_name` → VPC subnets).
- `resource → component` — `configured_by` (the JSON sidecar feeds inputs that produced this resource).

Node id pattern: `resource:<type>:<account_id>/<region>/<terraform_address>` (e.g. `resource:aws_rds_cluster:<account>/<region>/aurora.this`).

## 5. Source of truth

**Terraform / OpenTofu state in S3.** Per the cross-repo survey (§3.5.3), there are 4 bucket-formula variants and 5 key-convention variants. The scanner accepts all of them via a layered resolution:

### 5.1 Bucket resolution (decision 1: parse terragrunt.hcl)

Each module's `terragrunt.hcl` declares `remote_state.config.bucket` either as a literal string or as an interpolated expression. Scanner extracts the expression and classifies it into one of the known shapes from §3.5.3:

```
${get_aws_account_id()}-${region}-${cluster.name}-tf-states   → standard
local.aws_env.state_bucket                                    → standard (via root.hcl)
${project}-${region}-tf-states                                → cross-region singleton
${account_id}-${region}-${project}-tf-states                  → project-keyed
hub-account formula                                           → cross-account (skip in v1, §5.5)
```

Placeholders are resolved from the cluster's `shared-infra.json` (`target.account_id`, `target.region`, `target.cluster.name`) and any `project` field declared in the same shared-infra block. Unrecognized formulas → skip enrichment for that module + emit a warning + state_unavailable: unknown-bucket-formula.

`init_state_bucket` (`${account_id}-${region}-${cluster_name}-tf-init-state`) is recognized but **not enriched** in v1 — bootstrap state is not interesting to agents.

### 5.2 Key resolution (decision 1: parse terragrunt.hcl)

Same approach: scanner extracts `remote_state.config.key` and classifies. Known shapes from §3.5.4:

```
aws/<m>/terraform.tfstate                                              → singleton
<m>/terraform.tfstate                                                  → legacy (no clouds/ at repo root)
aws/<m>/${component_name}/terraform.tfstate                            → multi-instance
clouds/<provider>/modules/<m>/${app_dir}/${app_name}/terraform.tfstate → per-app, provider-prefixed
project/${project}/environment/${env}/<m>/${app_name}/terraform.tfstate → legacy per-app
aws/<m>/${app_dir}/${app_name}/terraform.tfstate                       → per-app
```

Placeholders that depend on `components/<env>/<m>*.json` enumeration (multi-instance) or `applications/<env>/*.json` enumeration (per-app, decision 10) produce **one state-key per match**. Per-app cap: 200 apps per cluster, warn at 150.

Unrecognized key shapes → skip enrichment + state_unavailable: unknown-key-formula.

### 5.3 Auth + access

- Same `KUBERLY_ROLE_ARN` chain agents already SSO into. Read access to the state bucket is required.
- State files use `use_lockfile=true` (set repo-wide). Reads don't need locks.
- Hub-account state buckets (identity-layer modules with state in a different account): require explicit shared-infra `state_targets` map (P5, see §9). v1 skips these and marks `state_unavailable: cross-account`.

### 5.4 Schema fallbacks

Cluster identity is the minimum needed to compute the bucket. Required keys (every fork has them): `target.account_id`, `target.region`, `target.cluster.name`. If any are missing, scanner emits no state-derived nodes for that cluster and surfaces `state_unavailable: missing-cluster-spine` on the env node.

### 5.5 Out of scope for v1

- Encrypted state with customer-managed KMS keys the agent role can't decrypt → skip silently, mark `state_unavailable: kms-denied`, surface in scope.md.
- Multi-region modules within one cluster (e.g. `replication_region` in aurora/rds). Default to the cluster's primary region; flag the secondary region in `state_unavailable` until P5 adds explicit `state_targets`.
- TF Cloud / TF Enterprise / GCS / Azure backends. v1 is S3-only; other backends are a P6+ concern.

## 6. Redaction policy

Three lists, applied in order:

### 6.1 Always-strip (deny-list)

Any attribute whose key matches **any** of:

```
password, master_password, _password
secret, _secret, secret_string
token, _token, _jwt
private_key, ssh_private_key, tls_private_key
access_key, aws_access_key_id, aws_secret_access_key
client_secret
user_data, user_data_base64, user_data_replace_on_change
session_token, refresh_token, api_key
```

Replace value with `"<redacted: <key>>"`. **Never** include in the enriched graph file even hashed.

### 6.2 Always-keep (allow-list)

Identifiers and topology:

```
id, arn (own-account only), name, region, type, engine, version, runtime
instance_class, instance_type, memory_size, timeout, desired_count, replicas
vpc_id, subnet_ids, security_group_ids, route_table_ids
encryption: encrypted, kms_key_id, encryption_at_rest, in_transit_encryption
toggles: deletion_protection, multi_az, public, publicly_accessible
retention: backup_retention_period, log_retention_in_days
```

### 6.3 Hash-and-store

Attributes whose presence matters but content shouldn't leak:

```
user_data       → sha256[:16] + size_bytes
inline_policy   → sha256[:16] + size_bytes
assume_role_policy → parse principals (allow), drop conditions (deny by default)
```

### 6.4 Cross-account ARN handling (decision 8)

Build the **own-account set** at scan time: union of `target.account_id` across every `components/<env>/shared-infra.json` in the repo. This captures both the workload account and any sibling-env accounts that genuinely belong to the customer.

- ARN with account ∈ own-account set → keep verbatim. If the account is not the current env's `target.account_id` but is in the union, annotate `partner: true`.
- ARN with foreign account → keep service+resource portion, redact account → `arn:aws:iam::<redacted>:role/foo`.

Hardcoded cross-account ARNs in module HCL (e.g. cross-account Route53 delegation): if the referenced account is in the own-account union, kept verbatim; otherwise redacted in the enriched graph (the ARN remains visible in the source HCL — redaction only applies to the graph output).

## 7. Refresh cadence + storage

**Three layers, increasing cost:**

1. **Static parse** (current). On every `kuberly_platform.py generate`. Cost: ~50ms.
2. **Tier 1 enrichment** (no AWS). Same generate pass — single parse over JSON sidecars. Cost: ~100ms.
3. **Tier 2 state enrichment** (S3 reads). Opt-in via `--enrich-state` flag or `KUBERLY_ENRICH_STATE=1`. Cost: ~100ms × N modules. Cached for 1h via mtime check on local state cache.

**Cache:**
- `kuberly/state-cache/<cluster>/<module>.tfstate.json` — raw state, gitignored.
- `kuberly/graph.json` — gets a new top-level `enrichment: {tier: 1|2, generated_at, state_age_seconds}` field.
- `kuberly/graph-resources.json` — separate file holding Tier 2 resource nodes. Loaded into the live `KuberlyPlatform` instance only when `KUBERLY_ENRICH_STATE=1`.

**Trigger:**
- SessionStart hook (existing) gains optional `--enrich-state` argument. Default off.
- Manual: `python3 apm_modules/kuberly/kuberly-skills/mcp/kuberly-platform/kuberly_platform.py enrich --cluster prod`.

## 8. New MCP tools

| Tool | Purpose | Output size |
|---|---|---|
| `enrichment_status` | Report tier, last refresh, state-bucket access state per cluster | tiny |
| `state_facts` | Look up enriched attrs for any node id (`region`, `account_id`, etc.) | small |
| `resource_query` | Filter Tier 2 resource nodes by type / name / parent module | medium |
| `resource_blast_radius` | Same as `blast_radius` but at resource granularity | medium |

Existing tools (`query_nodes`, `get_neighbors`, `blast_radius`) gain optional `include_resources: bool` to reach into Tier 2 — default false to keep current callers' costs the same.

## 9. Implementation phases

Each phase ships independently. Each is mergeable on its own.

| Phase | Scope | LOC est. | Risk |
|---|---|---|---|
| **P1** | Tier 1 enrichment from existing JSON sidecars + shared-infra | ~150 | low |
| **P2** | TF state fetch + parse from S3 (no resource nodes yet, just `last_state_seen` + `state_resource_count` per module) | ~250 | medium (S3 auth) |
| **P3** | Tier 2 resource nodes for the v1 set (EKS, RDS, Lambda, ECS, IAM, secrets, queues, KMS) | ~400 | medium (redaction correctness) |
| **P4** | Resource-level edges + new MCP tools + `--include_resources` on existing tools | ~250 | low |
| **P5** | Cross-account state buckets + multi-region modules | ~200 | medium |

P1 alone justifies the work — the orchestrator and `quick_scope` immediately get region/account/cluster context they currently re-derive.

## 10. Risk register

| Risk | Severity | Mitigation |
|---|---|---|
| Redaction misses a secret-like attr | high | Allow-list-default for unknown attrs; deny-list is fail-safe. Snapshot tests on real state files (with synthetic secrets injected). Run snapshot tests across all surveyed forks so per-fork extensions are exercised. |
| State file too large (multi-MB) | medium | Stream parse, drop attributes during parse not after. Hard cap: 50MB per state, error out and mark unavailable. |
| Stale state misleads an agent | medium | Every enriched attr carries `as_of` timestamp; orchestrator must show staleness > 24h to user. |
| TF / OpenTofu state schema drift | low | Use `terraform_version` field to gate; fail closed if unrecognized. Forks may run different versions — accept both. |
| Customer S3 bucket layout differs from convention | medium | Per-module `kuberly.json.state_bucket` + `state_key` template overrides. Resolution order documented in §5.1 / §5.2. |
| Performance of full enrichment (60+ modules × S3 round-trip) | medium | Parallelize fetches (asyncio or thread pool). 1h local cache. Per-cluster lazy load. |
| **Legacy flat-modules layout** (modules at `modules/`, not `clouds/<provider>/modules/`) | low | Scanner accepts both layouts. Detection: presence of `clouds/` dir at repo root → standard layout; otherwise flat. |
| **shared-infra absent as own file** — block embedded inside another component JSON | low | Fallback scan: when `components/<env>/shared-infra.json` is missing, iterate `components/<env>/*.json` and pick the first JSON with a top-level `"shared-infra"` key. |
| **Multi-cloud forks** — Azure / GCP modules alongside AWS | medium | v1 enriches AWS only. `clouds/{azure,gcp}/modules/*` get static-parse nodes (Tier 0) but no state enrichment. Flag in scope.md when changes touch non-AWS clouds. |
| **Per-app state files** (lambda_app, ecs_app, bedrock_agentcore_app) — N state files per module | medium | Auto-enumerate via `applications/<env>/*.json`. Hard cap: 200 apps per cluster (warn at 150). |
| **Feature-branch envs** — `components/feature-*/` ephemeral | low | Treat each `components/*/` dir as an env node. Mark `is_ephemeral: true` for `feature-*` prefixed names. Skip state enrichment for ephemeral envs by default (opt-in via flag). |
| **Cross-account ARN whitelist drift** — known partner accounts get redacted by mistake | medium | Maintain `partner_accounts` field in shared-infra (new optional). Scanner reads it; ARNs matching a partner account are kept verbatim with `partner: true` annotation. |
| **Custom state-bucket formulas** (project-keyed, cross-region singletons) | medium | `kuberly.json.state_bucket` template override. Templates resolved with all known variables; missing variable = skip enrichment for that module + emit warning. |
| **Opaque env / cluster names** (typos, customer-specific suffixes) | low | Treat as opaque strings; don't normalize or "fix". Env names are user-controlled. |

## 11. Open questions

1. **Server-side or client-side enrichment?** Hook generates a file, MCP reads it (current pattern). Cleaner separation, no AWS creds in MCP server. **Recommendation:** client-side hook writes `kuberly/graph-enriched.json`; MCP loads it lazily. Confirm.
2. **OIDC + IRSA topology** — materialize trust relationships as edges (`serviceaccount:backend → resource:aws_iam_role:backend`)? High value for the `irsa-workload-identity` skill's debugging path. **Recommendation:** yes in P4. Trust policy parsing is the hard part — start with the common kuberly-stack patterns, log unknowns.
3. **Helm release state** — Tier 2 covers AWS. Helm releases (`loki`, `alloy`, `argocd`) live in EKS Secrets, not TF state. **Recommendation:** skip for v1; covered by `eks-observability-stack`. Revisit if `kubectl get` access becomes routine.
4. **Application coverage** — ECS task defs, Lambda code hashes, ArgoCD sync states. Some in TF state, some not. **Recommendation:** v1 covers what's in TF state (per-app state files for `lambda_app`/`ecs_app`/`bedrock_agentcore_app`). ArgoCD sync state out of scope until P5+.
5. **Per-customer redaction overrides** — Add `.kuberly/redaction.yaml` for additive deny rules. Customer can broaden but never narrow the deny-list.
6. **Diff vs absolute** — Record diffs vs last-seen or absolute snapshots? **Recommendation:** absolute in v1; diff layer is a P5+ drift-detection concern.
7. **Legacy flat-modules layout** (NEW) — `modules/` flat layout exists in older / transitional forks. Migration is in progress towards uniform `clouds/<provider>/modules/`. **Recommendation:** scanner supports both during migration; deprecate fallback once all forks are uniform.
8. **Cross-account state buckets** (NEW) — some forks keep identity-layer state (IAM users, GitHub OIDC providers) in hub accounts. v1 marks `state_unavailable: cross-account`; P5 adds explicit `state_targets` map in shared-infra. Confirm acceptable to skip these from enrichment in v1.
9. **`partner_accounts` field in shared-infra** (NEW) — whitelist intentional cross-account ARNs so they're not redacted. New optional field. Confirm OK to extend the schema.
10. **State key for multi-instance modules** (NEW) — auto-enumeration vs explicit declaration. Auto-enumeration (scan `components/<env>/<m>*.json`) works for current forks but is fragile if naming convention drifts. **Recommendation:** auto-enumerate by default; warn (don't fail) if `kuberly.json.state_key_pattern` is missing on a module with multiple JSON sidecars.
11. **Multi-cloud parity** (NEW) — some forks actively use Azure + GCP. v1 enriches AWS only. When does Azure / GCP enrichment become P-level work? **Recommendation:** track demand; defer until requested. Static-parse nodes for non-AWS clouds remain (Tier 0).

## 12. Decisions (recorded)

The following were resolved before P1 kickoff. They drive the implementation tasks in §9.

| # | Topic | Decision |
|---|---|---|
| 1 | Bucket / key resolution | **Derive from each module's `terragrunt.hcl` directly** — parse the `remote_state.config.{bucket, key}` expressions. NO new fields in `kuberly.json`. Pattern-match the known shapes from §3.5.3 / §3.5.4; classify the formula; resolve placeholders from cluster context. |
| 2 | Layout fallback | **Support both `clouds/<provider>/modules/` and legacy flat `modules/`** during migration. Detection: presence of `clouds/` at repo root → standard; otherwise flat. |
| 3 | shared-infra discovery | **Require `components/<env>/shared-infra.json` as a file.** No fallback to embedded-key in arbitrary component JSONs. If absent, env is marked `state_unavailable: missing-cluster-spine`. |
| 4 | State cache | **Yes — write `kuberly/state-cache/<cluster>/<module>.tfstate.json`** (gitignored) with 1h TTL. |
| 5 | graph.json freshness field | **Yes — add `enrichment: {tier, generated_at, state_age_seconds}` top-level key** to `kuberly/graph.json`. Non-breaking. |
| 6 | Resource node v1 list | **11 types**: EKS, RDS / Aurora, Lambda, ECS service, IAM role, Secrets Manager, SQS, SNS, KMS — **plus EFS, ElastiCache, MSK**. |
| 7 | Redaction lists | **Adopt §6.1 / §6.2 / §6.3 as v1 baseline.** Lists evolve via PR. Snapshot tests run across all surveyed forks. |
| 8 | Partner-account whitelist | **Derive own + partner accounts from the union of `target.account_id` across every `components/<env>/shared-infra.json` in the repo.** No new schema field. ARNs in that union → keep verbatim with `partner: true` annotation. ARNs in any other account → redact account portion. |
| 9 | Hub-account state buckets | **Skip in v1** with `state_unavailable: cross-account`. P5 adds explicit `state_targets` map for assume-role chains. |
| 10 | Per-app state enumeration | **Yes — enumerate `applications/<env>/*.json`** for `lambda_app`, `ecs_app`, `bedrock_agentcore_app`. Hard cap 200 apps per cluster, warn at 150. |

**Implications baked into the design above:** §4.2 v1 resource list now lists 11 types (decision 6); §5.1 / §5.2 use HCL parsing instead of `kuberly.json` overrides (decision 1); §6.4 uses the own-account union rule (decision 8); §10 risk register entry on `partner_accounts` is removed (replaced by decision 8); §11 open question on `partner_accounts` is closed.

## 13. Not deciding now (deliberately deferred)

- Webhook / live-API freshness path (Phase 5+).
- Multi-cloud (GCP / Azure) state schema — start with AWS, generalize once stable.
- Drift detection vs declared inputs — separate skill (`drift-detection-tf-state`), reads enriched graph.
- IAM access analyzer integration — separate skill.

---

**Next step:** review this note, mark §12 decision points, prioritize phases, then I scope P1 as a concrete task and we run the orchestrator on it.
