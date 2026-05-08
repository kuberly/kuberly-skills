import clsx from "clsx";

import type { LayerSummary } from "../api/types";
import { CATEGORY_COLORS, CATEGORY_LABELS } from "../lib/categories";

interface Props {
  layers: LayerSummary[];
  loading: boolean;
  error?: string;
}

// Layer name → category id used for colouring + labelling. Mirrors the
// Python _LAYER_TO_CATEGORY in dashboard/api.py; covers every layer the
// LanceDB stats endpoint may emit (24 distinct names as of v0.58.0).
// Unknown layers fall through to "dependency", which renders neutral grey.
function layerToCategory(layer: string): string {
  const map: Record<string, string> = {
    // IaC source code
    code: "iac_files",
    iac: "iac_files",
    static: "iac_files",
    terragrunt: "iac_files",
    treesitter: "iac_files",
    components: "iac_files",
    // OpenTofu / Terraform state
    state: "tg_state",
    tg_state: "tg_state",
    tofu_state: "tg_state",
    // Kubernetes
    k8s: "k8s_resources",
    kubernetes: "k8s_resources",
    kubectl: "k8s_resources",
    // Docs
    docs: "docs",
    doc: "docs",
    // CUE / schema
    cue: "cue",
    schema: "cue",
    cue_schema: "cue",
    // CI/CD
    ci_cd: "ci_cd",
    image_build: "ci_cd",
    github_actions: "ci_cd",
    // Applications (rendered + argo)
    applications: "applications",
    rendered: "applications",
    rendered_apps: "applications",
    argo: "applications",
    // Live observability
    logs: "live_observability",
    metrics: "live_observability",
    traces: "live_observability",
    alerts: "live_observability",
    alert: "live_observability",
    profiles: "live_observability",
    compliance: "live_observability",
    cost: "live_observability",
    dns: "live_observability",
    secrets: "live_observability",
    // AWS scanner output
    aws: "aws",
    aws_network: "aws",
    aws_iam: "aws",
    aws_compute: "aws",
    aws_storage: "aws",
    aws_rds: "aws",
    aws_s3: "aws",
    network: "aws",
    iam: "aws",
    storage: "aws",
    // Meta
    meta: "meta",
    cold: "meta",
  };
  return map[layer] ?? "dependency";
}

export function OverlaysStrip({ layers, loading, error }: Props) {
  if (loading) {
    return <div className="text-xs text-text-muted">loading layers…</div>;
  }
  if (error) {
    return <div className="text-xs text-accent-red">layers failed: {error}</div>;
  }
  if (!layers.length) {
    return null;
  }

  // Sort high-value layers (iac/state/k8s/docs/cue/ci_cd/apps) first,
  // others after. Mirrors the screenshot's grouping intent.
  const order = ["iac", "code", "state", "tg_state", "k8s", "docs", "cue", "ci_cd", "applications", "aws"];
  const ranked = [...layers]
    .filter((row) => typeof row?.name === "string" && row.name.length > 0)
    .sort((a, b) => {
      const ai = order.indexOf(a.name);
      const bi = order.indexOf(b.name);
      if (ai === -1 && bi === -1) return a.name.localeCompare(b.name);
      if (ai === -1) return 1;
      if (bi === -1) return -1;
      return ai - bi;
    });

  return (
    <section className="flex flex-wrap gap-2 items-center">
      <span className="text-[10px] font-mono uppercase tracking-wider text-text-muted mr-2">
        graph layers
      </span>
      {ranked.map((row) => {
        const cat = layerToCategory(row.name);
        const color = CATEGORY_COLORS[cat] ?? "#888";
        const catLabel = CATEGORY_LABELS[cat] ?? cat;
        return (
          <span
            key={row.name}
            className={clsx(
              "pill border bg-bg-card text-xs",
              "border-[var(--cat-border)]"
            )}
            style={
              {
                "--cat-border": `${color}55`,
              } as React.CSSProperties
            }
            title={`${row.name} · ${catLabel} · last_refresh=${row.last_refresh || "—"}`}
          >
            <span
              className="w-2 h-2 rounded-full"
              style={{ background: color }}
              aria-hidden
            />
            {row.name}
            <span className="text-text-muted font-mono ml-1">{(row.node_count ?? 0).toLocaleString()}</span>
          </span>
        );
      })}
    </section>
  );
}
