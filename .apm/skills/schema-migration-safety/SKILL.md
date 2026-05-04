---
name: schema-migration-safety
description: >-
  Database / message-schema migrations without downtime: expand-contract, gating
  queries, online migration patterns (concurrent index, NOT NULL with default),
  rollback rules, and how to wire migrations into Kubernetes/ECS deploys.
---

# Schema migration safety

Use this skill when changing a **shared schema** that running workloads depend on — relational tables, document collections, message envelopes, columnar warehouse tables. Schema migrations break differently from app migrations: the wrong order takes the system down before any code runs.

## The expand–contract pattern

Every breaking change is **two deploys**, never one.

| Phase | Schema | Code | Safe to ship? |
|-------|--------|------|---------------|
| **Expand** | New shape **added**, old shape still works | Code reads/writes either; **prefers old** | Yes — independently |
| **Cutover** | Both shapes exist | Code prefers **new**; falls back to old on read | Yes — feature-flagged |
| **Contract** | Old shape **removed** | Code only knows new | Only after no traffic uses old for **N read cycles** |

If a single PR does both expand and contract, you have **no rollback** during the deploy window. Reviewers should reject it.

## Migration classes — pick the right one

| Change | Class | Notes |
|--------|-------|-------|
| Add a column with default | Online | Postgres ≥ 11 makes this O(1); older versions rewrite. Check before pushing. |
| Add `NOT NULL` to existing column | **Two-step**: set DEFAULT + backfill in batches → flip NOT NULL when null count = 0 |
| Drop column | **Three-step expand-contract**: stop reading → stop writing → drop |
| Add index | `CREATE INDEX CONCURRENTLY` (Postgres) — does **not** block writes |
| Rename column | **Never** rename. Add new, dual-write, retire old. |
| Change column type | Add new column, dual-write with cast, retire old |
| Foreign key | `ADD CONSTRAINT … NOT VALID` then `VALIDATE CONSTRAINT` (Postgres pattern) |

## Verification queries — gate every phase

A migration without a "did it actually work" query is a wish. Pre- and post-conditions:

```sql
-- Backfill gate (must reach 0 before NOT NULL flip)
SELECT count(*) FROM users WHERE country IS NULL;

-- Long-running index — has it finished?
SELECT phase, blocks_done, blocks_total
FROM pg_stat_progress_create_index;

-- Drop column gate — is anything still reading the old shape?
-- Application telemetry, not the DB. Look at app logs / metrics for the old field name.
```

For NoSQL or warehouse: aggregate count of rows missing the new field; aggregate count of rows still carrying the deprecated field. Hold each gate.

## Online migration patterns

**Backfill in batches**, never `UPDATE table SET …` in one transaction:

```sql
-- Loop in app code or a one-off worker, NOT a single long transaction.
WITH batch AS (
  SELECT id FROM users WHERE country IS NULL ORDER BY id LIMIT 1000 FOR UPDATE SKIP LOCKED
)
UPDATE users SET country = 'XX' WHERE id IN (SELECT id FROM batch);
```

`FOR UPDATE SKIP LOCKED` lets concurrent jobs run without blocking each other. Sleep briefly between batches; watch replication lag.

**Concurrent index** (Postgres) — does not block writes but takes longer:

```sql
CREATE INDEX CONCURRENTLY idx_users_country ON users (country);
-- Cannot run inside a transaction; orchestrate from a script, not a migration tool's tx wrapper.
```

If the migration tool wraps everything in `BEGIN/COMMIT`, drop to a raw script for this one statement.

## Wiring migrations into a deploy

The migration step must be **idempotent** and run **before** the new code receives traffic. Choose one:

| Pattern | Runtime | Notes |
|---------|---------|-------|
| **Init container** | Kubernetes Deployment | Runs before app starts; replicas race — guard with advisory lock or migration tool's own lock |
| **Kubernetes Job** | Pre-deploy | Single-runner; gate the rollout on Job success (Argo Sync waves, Helm `pre-install`/`pre-upgrade` hook) |
| **CI step** | Pre-deploy in pipeline | Simplest; needs DB credentials in CI |
| **ECS one-off task** | Pre-deploy | Run before service update; check exit status |

**Always use a migration tool's lock** — Flyway/Alembic/Liquibase/Atlas all guard against concurrent runners with a row in a `schema_migrations` table. Hand-rolled `psql -f` does not; init containers will race.

## Rollback rules

- Forward-only is fine **if** every migration is expand-contract and every breaking change ships behind a feature flag. The "rollback" is the next deploy that drops the old shape — there's nothing to revert.
- Mid-deploy rollback (current deploy fails): the schema must still work for the **previous** code version. This is what expand-contract buys you.
- **Never** put a rollback that drops a column in the same change as the column's add. If you have to roll back, you'll re-create-then-re-drop, losing data.

## Long-running migrations — the patient approach

For migrations that take hours/days (large backfills, partitioning a multi-TB table):

1. Cap each batch's runtime; emit progress to a metrics endpoint.
2. Write a "resumable" gate — the migration should be killable and restartable.
3. Tail replication lag during the run; pause if it exceeds threshold.
4. Do **not** combine with code deploys; migrate first, deploy after.

## Pair with

- **`secrets-rotation-lifecycle`** — same dual-credential / dual-shape mental model.
- **`pre-commit-infra-mandatory`** — migrations should be linted (sqlfluff, atlas-lint, …) on commit.
- **`troubleshooting-aws-observability`** — what to watch in CloudWatch / Loki during the deploy.

## kuberly-stack notes

For RDS/Aurora schema changes that ride alongside `components/<cluster>/` updates: stage the migration as its own OpenSpec change with the gating queries written into `tasks.md`. The component JSON change (e.g. enabling a new param group setting) goes in a **second** change after the schema cuts over — keep them separable for backport.
