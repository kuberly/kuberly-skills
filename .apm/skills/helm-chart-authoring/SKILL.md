---
name: helm-chart-authoring
description: >-
  Author Helm charts that survive review and upgrades — values shape, schema
  validation, dependencies, testing (helm template / lint / chart-testing),
  versioning, and the anti-patterns that produce 3 a.m. pages.
---

# Helm chart authoring

Use this skill when **writing or editing a Helm chart**, not just rendering one. Reading-side workflows belong in **`kubernetes-finops-workloads`** and runtime observability skills.

## Skeleton

```
mychart/
├── Chart.yaml              # name, version (chart), appVersion (the app), dependencies
├── values.yaml             # the contract. Every key gets a comment.
├── values.schema.json      # JSON Schema; rejects bad values at install time.
├── templates/
│   ├── _helpers.tpl        # naming, common labels, image refs
│   ├── deployment.yaml
│   ├── service.yaml
│   └── NOTES.txt           # printed after install — keep useful
└── tests/                  # `helm test` hooks (optional but recommended)
```

`Chart.yaml` minimum:

```yaml
apiVersion: v2
name: mychart
description: One-line, what this chart deploys.
type: application
version: 0.1.0      # the *chart* version — bump on chart changes
appVersion: "1.4.2" # the *application* version — bump when image tag default changes
```

Bump `version` on **any chart change** (template tweak, helper rename); bump `appVersion` only when the default `image.tag` moves. Mixing them up breaks chart-museum diffs.

## Values shape

The `values.yaml` is the chart's **public contract**. Treat changes the way you'd treat an API.

**Flat for primitives, structured for resources:**

```yaml
# Good
replicaCount: 2
image:
  repository: ghcr.io/acme/api
  tag: ""           # defaults to .Chart.AppVersion via _helpers.tpl
  pullPolicy: IfNotPresent
resources:
  requests: { cpu: 100m, memory: 256Mi }
  limits:   { cpu: 500m, memory: 512Mi }
ingress:
  enabled: false
  className: nginx
  hosts: []
```

**Anti-patterns:**

- `extraArgs: []` of free-form strings — replace with named keys (`logLevel`, `enableMetrics`).
- Lists where users will need to **override one item**. Helm merges scalars per-key and **replaces lists wholesale**. Use a **map** keyed by name when consumers need to merge:
  ```yaml
  containers:
    main: { image: ..., resources: ... }
    sidecar: { image: ..., resources: ... }
  ```
  Then `range $name, $cfg := .Values.containers` in the template.
- `nameOverride` + `fullnameOverride` both undocumented. Pick one.

## Schema validation

`values.schema.json` makes `helm install` reject typos before they hit the cluster:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "properties": {
    "replicaCount": { "type": "integer", "minimum": 0, "maximum": 100 },
    "image": {
      "type": "object",
      "required": ["repository"],
      "properties": {
        "repository": { "type": "string" },
        "tag":        { "type": "string" },
        "pullPolicy": { "type": "string", "enum": ["Always", "IfNotPresent", "Never"] }
      }
    }
  },
  "additionalProperties": false
}
```

`additionalProperties: false` at the top level is aggressive but worth it — it catches typos like `replicas:` (vs `replicaCount:`) immediately.

## Helpers — name once, reuse everywhere

`templates/_helpers.tpl` should define at minimum:

```gotemplate
{{- define "mychart.fullname" -}}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "mychart.labels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{- end -}}
```

Use these everywhere. Hand-rolling labels per-template guarantees they drift.

## Dependencies

```yaml
# Chart.yaml
dependencies:
  - name: postgresql
    version: ~13.0.0    # SemVer range; pin to a major
    repository: https://charts.bitnami.com/bitnami
    condition: postgresql.enabled
```

Run `helm dependency build` (uses `Chart.lock`) — never `helm dependency update` in CI without committing the lock; it produces nondeterministic builds.

## Testing the chart

```bash
helm lint .                                              # template syntax + Chart.yaml
helm template release-name . -f values.yaml | kubectl apply --dry-run=client -f -   # Kubernetes accepts the output
helm install release-name . --dry-run --debug            # full render with values.schema validation
ct lint-and-install --charts .                           # CI tool; runs install in kind
```

For unit-style tests of templates: [`helm-unittest`](https://github.com/helm-unittest/helm-unittest) plugin lets you assert specific rendered output without spinning up a cluster.

## Versioning and publishing

- Chart version follows **SemVer** of the chart, not the app.
- Breaking values changes (renaming a key, removing a default) → **major** bump. Document in `Chart.yaml` `annotations.artifacthub.io/changes`.
- Publish via OCI (preferred):
  ```bash
  helm package .
  helm push mychart-0.2.0.tgz oci://ghcr.io/<org>/charts
  ```

## Anti-patterns to flag in review

- **`{{ .Values.someBool }}`** rendered into YAML without quoting — strings `"false"` are truthy in Go templates; use `{{ if eq .Values.someBool true }}`.
- **`tpl` on user-supplied values** — template injection. Only `tpl` strings *you* control.
- **`range`** over a map without a stable order: use `keys` + `sortAlpha` for deterministic output.
- **Hard-coded namespaces** in templates — they fight Helm's `--namespace`.
- **No `resources:` defaults** — clusters with LimitRange will reject pods; clusters without it will scheduler-thrash.

## Pair with

- **`kubernetes-finops-workloads`** — once installed, your `resources:` defaults get audited there.
- **`application-env-and-secrets`** — env injection patterns and Secrets Manager wiring.
- **`secrets-rotation-lifecycle`** — Reloader / external-secrets patterns when values reference Secrets.

## kuberly-stack notes

Charts consumed by kuberly-stack apps are usually thin wrappers; the heavy logic stays in CUE under `cue/` and component JSON. If you find yourself reaching for chart logic that already exists in CUE, push the change there instead — the chart should stay near-mechanical.
