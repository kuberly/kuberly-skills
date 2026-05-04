# CloudWatch Logs Insights — VPC flow v2 examples

Use only after confirming **`@message`** token count matches **version 2** (or adjust `parse` field list for v3–v5 per AWS documentation).

## Top REJECTed flows (possible SG / NACL issues)

```sql
fields @timestamp, @message
| parse @message "* * * * * * * * * * * *" as
    version, account_id, interface_id,
    srcaddr, dstaddr, srcport, dstport,
    protocol, packets, bytes,
    windowstart, windowend, action
| filter action = "REJECT"
| stats sum(bytes) as bytes_total, count() as flows
    by srcaddr, dstaddr, dstport, protocol
| sort bytes_total desc
| limit 30
```

## Egress from a known source IP

```sql
fields @message
| parse @message "* * * * * * * * * * * *" as
    version, account_id, interface_id,
    srcaddr, dstaddr, srcport, dstport,
    protocol, packets, bytes,
    windowstart, windowend, action
| filter srcaddr = "10.0.1.50"
| stats sum(bytes) as bytes_total by dstaddr, dstport
| sort bytes_total desc
```

Replace the filter with **`interface_id`** when correlating to a specific ENI from the AWS console.
