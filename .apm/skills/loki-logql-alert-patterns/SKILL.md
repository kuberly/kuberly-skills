---
name: loki-logql-alert-patterns
description: >-
  Write Loki / LogQL alerts and panels that fire on real errors, not substring matches in info
  logs — covers the JSON-parse / level-filter / line_format trio, common false-positive patterns,
  and cardinality pitfalls.
---

# Loki / LogQL — alert and panel patterns (and what goes wrong)

Alerts written against Loki with a naive regex filter look correct in Explore but fire on info logs in production. The three anti-patterns below cover almost every case where a "log error burst" alert resolves with a `level: "info"` line attached.

## Anti-pattern 1 — regex on the raw line

```logql
{namespace="$ns", pod=~"$pod"} |~ "(?i)(error|exception|panic|fatal)"
```

`|~` matches the substring **anywhere in the log line**, including:
- Module / class names (`WorkflowErrorHandler`, `panic_recovery`)
- URL paths, file paths, stack-trace frames being logged at info level
- Message bodies like `"no errors found"`, `"recovered from panic"`, `"fatal=false"`
- JSON keys that contain those words

**Fix — parse and filter on the structured field:**

```logql
{namespace="$ns", pod=~"$pod"}
  | json
  | level =~ "(?i)error|fatal|panic"
```

Most Kuberly app loggers (Pino / zap / logrus / slog) emit JSON with a `level` field. After `| json`, the level is a real label expression — agents only fire on what the application classified as an error.

## Anti-pattern 2 — preview shows JSON envelope, not the message

A first-N-chars preview of a JSON log line burns the whole budget on `{"level":"info","time":1777…,"pid":1,"hostname":"…","mod":"…","publishOpe…` and never reaches the actual `msg`.

**Fix — `line_format` extracts only the fields a human reads:**

```logql
{namespace="$ns", pod=~"$pod"}
  | json
  | level =~ "(?i)error|fatal|panic"
  | line_format "{{.level}} {{.mod}} {{.msg}}"
```

For Alertmanager / Slack templates, render the same projection in the annotation body — never paste raw log lines.

## Anti-pattern 3 — diagnostic rule

> **If the resolved alert preview shows `level=info`, your filter is matching raw text, not the structured level. Apply Anti-pattern 1's fix.**

Use this as a one-line debugging check before deeper investigation. It identifies the bug in seconds and applies to every JSON-emitting backend.

## Recipe — rate-based error-burst alert

```logql
sum by (namespace, pod) (
  rate({namespace="$ns", pod=~"$pod"}
        | json
        | level =~ "(?i)error|fatal|panic" [5m])
) > 0.1
```

- `rate(... [5m])` smooths bursts; `> 0.1` ≈ more than ~30 error lines in 5 minutes per pod. Tune to traffic.
- Group by `namespace, pod` (label-only). **Do not** group by `msg` or other line fields — see cardinality note.
- Pair with a `for: 2m` clause in the alert rule so single-line blips do not page.

## When `| json` does not apply

| Logger output | Parser |
|---------------|--------|
| JSON | `\| json` |
| logfmt (`key=value`) | `\| logfmt` |
| Plain text with a known shape | `\| pattern "<_> level=<level> <_>"` |
| Multiline stack traces | Configure multiline parsing at the agent (Promtail / Alloy) — LogQL cannot reassemble after the fact |

If the application emits a mix, use `| json | __error__ != ""` to surface lines that failed JSON parsing during alert development, then narrow.

## Label cardinality — what to keep as a line field vs a label

- **Labels** (cheap to filter, indexed): `namespace`, `pod`, `container`, `app`, `level` after parse.
- **Line fields** (queryable via `|` after parse, **not** indexed): `msg`, `request_id`, `user_id`, `trace_id`, error class names.
- **Never** promote an unbounded field to a label via `| label_format` — Loki indexes labels, and high-cardinality labels (one series per `request_id`) explode the index and drive cost.

## Alert annotation template

When wiring rules into Alertmanager / Slack, render only the projected line — not raw JSON:

```yaml
annotations:
  summary: "{{ $labels.namespace }}/{{ $labels.pod }} error burst"
  description: |
    rate(error|fatal|panic) > 0.1/s for 5m
    Sample line:
    {{ with $first := (query "topk(1, ... | line_format \"{{.level}} {{.mod}} {{.msg}}\")") -}}
    {{ $first }}
    {{- end }}
```

Exact templating syntax depends on the alert engine (Loki ruler, Grafana alerting, Mimir). The principle is the same: project to readable fields before rendering.

## Cross-references

- **`eks-observability-stack`** — where Grafana / Prometheus / Loki / Tempo run and how to reach them.
- **`troubleshooting-aws-observability`** — when in-cluster signals are insufficient and you need CloudWatch / CloudTrail.
- **`kubernetes-finops-workloads`** — Prometheus-side patterns, not Loki.
