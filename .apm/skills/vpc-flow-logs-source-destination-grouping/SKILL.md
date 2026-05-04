---
name: vpc-flow-logs-source-destination-grouping
description: >-
  Analyze VPC Flow Logs grouped by source and destination (and ports): CloudWatch Logs Insights,
  Athena on S3, or CLI/jq on samples; interpret bytes/packets and action (ACCEPT/REJECT).
---

# VPC Flow Logs — group by source / destination traffic

Use this skill when you need to **aggregate** flow records by **who talked to whom** (IP / ENI / optional port), split **egress vs ingress** perspectives, and optionally **chart** or export for a dashboard. Pairs with **`troubleshooting-aws-observability`** for VPC-level incident work.

## Know your delivery path

| Delivery | Good for | Grouping tool |
|----------|-----------|---------------|
| **CloudWatch Logs** | Interactive investigation, smaller volume | **Logs Insights** — `stats`, `parse` |
| **S3** (often with **Athena**) | Large history, Parquet/JSON, cost control | **SQL** — `GROUP BY` |
| **Kinesis Data Firehose** → S3 / OpenSearch | Central analytics | Same as S3 / OpenSearch aggregations |

Flow log **version** (2, 3, 4, 5, …) changes which fields exist (`pkt-srcaddr`, `subnet-id`, `vpc-id`, etc.). **Always sample one raw line** (from the log group prefix or S3 object) and match it to the [AWS VPC flow log records](https://docs.aws.amazon.com/vpc/latest/userguide/flow-log-records.html) field table before writing `parse` or `CREATE TABLE`.

## CloudWatch Logs Insights (default space-delimited format)

Default **version 2** lines are **space-separated** tokens (no JSON). You must **`parse @message`** before **`stats`**.

Example pattern for the **classic v2** ordering (adjust if your first line has more/fewer tokens — see AWS docs for v3–v5):

```sql
fields @timestamp, @message
| parse @message "* * * * * * * * * * * *" as
    version, account_id, interface_id,
    srcaddr, dstaddr, srcport, dstport,
    protocol, packets, bytes,
    windowstart, windowend, action
| stats sum(bytes) as bytes_total, sum(packets) as pkt_total, count() as flows
    by srcaddr, dstaddr, srcport, dstport, protocol, action
| sort bytes_total desc
| limit 50
```

**Split “who sent” vs “who received”:** a single row already has **`srcaddr` → `dstaddr`**. For “top talkers outbound from subnet X”, filter `srcaddr` or `pkt-srcaddr` in versions that support packet address fields.

**REJECT vs ACCEPT:** keep **`action`** in the `by` clause or filter `action = "REJECT"` to surface security-group or NACL drops.

## Athena (S3 delivery)

Use a **Glue** / **Hive** table with columns matching your format (or start from **CREATE TABLE … ROW FORMAT DELIMITED** for space-delimited text — less ideal at scale than **Parquet** from Firehose).

Illustrative aggregation (column names assumed; align types with your DDL):

```sql
SELECT
  srcaddr,
  dstaddr,
  srcport,
  dstport,
  protocol,
  action,
  SUM(CAST(bytes AS BIGINT)) AS bytes_sum,
  SUM(CAST(packets AS BIGINT)) AS packets_sum,
  COUNT(*) AS flow_lines
FROM vpc_flow_logs
WHERE date_partition BETWEEN date_format(current_timestamp - interval '1' hour, '%Y-%m-%d')
                         AND date_format(current_timestamp, '%Y-%m-%d')
GROUP BY 1, 2, 3, 4, 5, 6
ORDER BY bytes_sum DESC
LIMIT 100;
```

Partition predicates on **`date` / `hour`** (or whatever your delivery pipeline creates) are **required** for cost and speed.

## Small files: CLI + jq (JSON lines only)

If you already converted flows to **JSONL** (ETL step), you can group with **`jq`** using `group_by(.srcaddr + .dstaddr)` patterns. Raw VPC text lines are **not** JSON — normalize first.

## Plotting / charts

- **QuickSight** or **Grafana** (Athena or CloudWatch data source): bar/line from the grouped query above.
- **Spreadsheet**: export CSV from Athena or Insights.
- **In chat**: print a **text table** (top N rows) — do not claim pixel charts unless the user’s environment renders them.

## Hygiene

- Flow logs contain **real IPs**; redact or generalize when pasting into **public** PRs or external tickets.
- **IPv6** and **NAT** semantics: `srcaddr`/`dstaddr` may be private or translated; correlate with **ENI** / **interface-id** when debugging a specific workload.

## Related skills

- **`troubleshooting-aws-observability`** — VPC subsection and cross-signal correlation.
- **`short-session-memory`** — keep huge query outputs ephemeral; persist conclusions in the incident ticket or PR.

## Extra patterns

See **`references/cw-insights-v2-example.md`** for a slightly longer Insights variant (optional filters).
