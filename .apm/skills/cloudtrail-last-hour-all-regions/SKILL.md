---
name: cloudtrail-last-hour-all-regions
description: >-
  Pull and summarize AWS CloudTrail management events for roughly the last hour across every
  enabled region (lookup-events loop, pagination); when to use org-trail Athena instead.
---

# CloudTrail — last ~1 hour, all regions

Use this skill when you need a **quick, API-driven** slice of **management events** across **all commercial/opt-in regions** the caller can access. It complements **`troubleshooting-aws-observability`** (routing) and **`terragrunt-local-workflow`** (AWS auth / role context).

## What `lookup-events` gives you

- **Regional API**: `aws cloudtrail lookup-events` returns events recorded **in that region** (management events for regional APIs, plus events replicated to that region depending on trail configuration).
- **Rough window**: use **UTC** `StartTime` / `EndTime` (ISO 8601). For “last hour”, compute end = now, start = now − 1h.
- **Pagination**: repeat with `--next-token` until empty. Default page size is capped (**50** events per call unless your CLI/SDK version documents otherwise).

## IAM

- **`cloudtrail:LookupEvents`** in each region you query (often covered by **`ReadOnlyAccess`**-style policies; confirm for your role).
- **No** permission to read **S3** trail buckets is required for `lookup-events`.

## Bash: all enabled regions, last hour

Set credentials / profile first (`AWS_PROFILE`, **`KUBERLY_ROLE`** assume-role, or SSO). **Do not** paste real account IDs or access keys into tickets or upstream PRs.

```bash
END=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
START=$(date -u -v-1H +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || date -u -d "1 hour ago" +"%Y-%m-%dT%H:%M:%SZ")

for r in $(aws ec2 describe-regions --query 'Regions[].RegionName' --output text); do
  echo "=== $r ==="
  aws cloudtrail lookup-events --region "$r" --max-results 50 \
    --start-time "$START" --end-time "$END" \
    --output json --no-cli-pager \
    | jq -r '.Events[]? | "\(.EventTime) \(.EventName) \(.Username // "")"' 2>/dev/null \
    | head -n 20
done
```

Notes:

- **macOS** uses `date -v-1H`; **GNU date** uses `date -d '1 hour ago'`. Adjust one line for your OS.
- **`.Username`** may be empty for assumed roles; parsing **`CloudTrailEvent`** JSON gives richer identity (wrap in a small script if you need consistent columns).
- **Opt-in regions** may return `UnauthorizedOperation` for some principals — skip or collect failures.
- For **full** results, wrap `lookup-events` in a **`while`** loop on **`NextToken`** (not shown above; add when you need complete history for the hour).

## Narrow the noise

Add filters when the question is specific:

- **`--lookup-attributes AttributeKey=EventName,AttributeValue=AssumeRole`**
- **`AttributeKey=ResourceName,AttributeValue=...`**
- **`AttributeKey=Username,AttributeValue=...`**

## When **not** to use this skill

| Situation | Better approach |
|-----------|------------------|
| **Organization trail** in **S3**, long retention, many accounts | **Athena** (partitioned by `region` / `date`), or **CloudTrail Lake** SQL |
| **Data events** (S3 object-level, Lambda invoke data) | Trail must have data events on; query **S3** / **Lake**, not quick `lookup-events` across dozens of regions for volume |
| **Real-time detection** | **EventBridge** rules on trail, **GuardDuty**, SIEM — not ad-hoc CLI |

## Agent behavior

- Prefer **UTC** timestamps in commands and summaries.
- If output is huge, **summarize** by `EventName` and principal type, then offer to drill into one region or one event name.
- Tie findings back to **infra** (who changed SG, IAM, EKS API) using **`infra-change-git-pr-workflow`** when the outcome is a code change.

## Related skills

- **`troubleshooting-aws-observability`** — where CloudTrail fits in ECS/EKS/Lambda incidents.
- **`short-session-memory`** — keep raw event dumps out of durable docs; link or redact.
