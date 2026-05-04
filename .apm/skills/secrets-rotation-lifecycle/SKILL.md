---
name: secrets-rotation-lifecycle
description: >-
  Rotate secrets (DB passwords, API keys, TLS certs) without downtime: dual-credential
  windows, verification queries, retire-old gates. Covers AWS Secrets Manager,
  external-secrets-operator, SealedSecrets, and cert-manager patterns.
---

# Secrets rotation lifecycle

Use this skill when rotating a secret that is **read by running workloads** — database passwords, third-party API keys, TLS certs, signing keys. Rotation is not a single edit; it's a **dual-credential window** with measured cutover and audit.

**First rule:** if the secret is consumed by an AWS service, ask whether **IRSA / Workload Identity** can replace it entirely (see **`irsa-workload-identity`**). The cheapest rotation is the one you don't need.

## The four-phase pattern

| Phase | Goal | Verify before next phase |
|-------|------|--------------------------|
| **1. Dual** | Both old and new credentials are valid | `OLD` and `NEW` each authenticate against the system of record |
| **2. Roll** | Workloads read `NEW` | Per-workload metric: zero auth failures for **two full read cycles** |
| **3. Retire** | `OLD` revoked | `OLD` returns `unauthorized`; metric still clean |
| **4. Audit** | No one held a stale copy | CloudTrail / audit log shows zero `OLD`-keyed reads in retire+1h |

Skipping verification between phases is how rotations cause incidents. Hold each gate even if it feels redundant.

## Patterns by secret class

### Database password (RDS / CloudSQL / Postgres)

Postgres supports two passwords briefly via `ALTER USER`:

```sql
-- Phase 1: dual (NEW issued; OLD still valid until the next ALTER)
ALTER USER app_user WITH PASSWORD 'NEW';
-- Apps still using OLD will fail; this is why you rotate via Secrets Manager
-- with a *rotation lambda*, not by hand:
```

For RDS, use **Secrets Manager rotation** with the [single-user / alternating-user](https://docs.aws.amazon.com/secretsmanager/latest/userguide/rotating-secrets.html) Lambda; alternating-user keeps two valid credentials at all times — far safer for high-throughput services. Avoid single-user rotation for anything that can't tolerate a brief 401.

**Verification query (read replica):**
```sql
SELECT count(*) FROM pg_stat_activity
WHERE usename = 'app_user' AND state IS NOT NULL;
```

Run before/after; the count should stay flat (no connection storm from auth failures).

### Third-party API key (Stripe, Datadog, …)

Most providers let you keep **multiple active keys**. Issue `NEW`, deploy, **then** revoke `OLD`. Watch:

- Provider's audit log for last-used timestamp on `OLD` (must reach zero before retire).
- App's outbound 401/403 rate (any non-zero spike during phase 2 means a workload didn't pick up the rotation).

If the provider only allows one active key, you have downtime. Schedule it.

### TLS cert (ingress, mTLS)

Use **cert-manager** with short-lived certs (≤ 90 days). Rotation is automatic; your job is to verify:

```bash
kubectl -n <ns> get certificate <name> -o jsonpath='{.status.notAfter}'
kubectl -n <ns> get secret <tls-secret> -o jsonpath='{.data.tls\.crt}' | base64 -d | openssl x509 -noout -dates
```

For **mTLS** (clients trust a CA): rotate the **CA** with overlap. New CA issued first → distributed to all verifiers → server cert reissued from new CA → old CA retired. Forgetting overlap takes the mesh down.

### Kubernetes Secret consumed by Pods

K8s does **not** restart pods when a referenced `Secret` changes. Three options:

1. **`stakater/Reloader`** — restart on change (simplest).
2. **External Secrets Operator** with `refreshInterval` — pulls from Secrets Manager / Vault / Parameter Store; pod must read on every request or be restarted.
3. **Sealed Secrets** — fine for *config*, painful for *rotation* (controller decrypt happens at apply time; rotation = re-seal + re-apply).

Document which one this workload uses **in the workload's README**, not just the platform docs.

## The retire gate — don't skip

The most common rotation incident isn't the cutover; it's a **forgotten consumer** still holding `OLD`. Before flipping retire:

```bash
# AWS: who's used the secret in the last hour?
aws cloudtrail lookup-events \
  --lookup-attributes AttributeKey=ResourceName,AttributeValue=<secret-arn> \
  --start-time "$(date -u -d '1 hour ago' +%FT%TZ)" \
  --query 'Events[].{u:Username,e:EventName,t:EventTime}'

# Datadog API: count requests using the old key
# (provider-specific — check their audit log endpoint)
```

If anything non-zero used `OLD` in the last cycle, **stop**. Find the consumer, redeploy, recheck.

## Schedule, not heroics

Rotation cadence should match the threat model, not "whenever someone notices":

| Secret class | Cadence | Mechanism |
|--------------|---------|-----------|
| Database password | 30–90 days | Secrets Manager rotation Lambda |
| Third-party API key | 90 days | Calendar reminder + runbook |
| TLS leaf cert | 60–90 days | cert-manager (automated) |
| TLS CA (private) | Annual | Documented CA-rotation runbook |
| Signing key (JWT, OAuth) | Quarterly | Dual-key window in JWKS |

Anything you can't automate becomes a calendar item with an explicit owner.

## Pair with

- **`irsa-workload-identity`** — the rotation you don't have to do.
- **`application-env-and-secrets`** — env / Secrets Manager wiring in components/applications JSON.
- **`troubleshooting-aws-observability`** — CloudTrail audit queries during the retire gate.

## kuberly-stack notes

Per-cluster Secrets Manager ARNs live in `components/<cluster>/shared-infra.json`; per-app secret keys go in `applications/<env>/<app>.json` and are consumed by CUE. Add a one-line "rotation:" comment in the app JSON pointing at the runbook for any non-managed secret.
