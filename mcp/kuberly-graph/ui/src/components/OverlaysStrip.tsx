import clsx from "clsx";

import type { LayerSummary } from "../api/types";
import { CATEGORY_COLORS, CATEGORY_LABELS } from "../lib/categories";

interface Props {
  layers: LayerSummary[];
  loading: boolean;
  error?: string;
}

// Layer name → category id used for colouring. Mirrors the python
// _LAYER_TO_CATEGORY mapping but only the slice we actually surface in the
// strip; unknown layers fall through to "dependency".
function layerToCategory(layer: string): string {
  const map: Record<string, string> = {
    code: "iac_files",
    iac: "iac_files",
    static: "iac_files",
    terragrunt: "iac_files",
    state: "tg_state",
    tg_state: "tg_state",
    tofu_state: "tg_state",
    k8s: "k8s_resources",
    kubernetes: "k8s_resources",
    docs: "docs",
    cue: "cue",
    ci_cd: "ci_cd",
    image_build: "ci_cd",
    applications: "applications",
    rendered_apps: "applications",
    aws: "aws",
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
  const ranked = [...layers].sort((a, b) => {
    const ai = order.indexOf(a.layer);
    const bi = order.indexOf(b.layer);
    if (ai === -1 && bi === -1) return a.layer.localeCompare(b.layer);
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
        const cat = layerToCategory(row.layer);
        const color = CATEGORY_COLORS[cat] ?? "#888";
        const label = CATEGORY_LABELS[cat] ?? row.layer;
        return (
          <span
            key={row.layer}
            className={clsx(
              "pill border bg-bg-card text-xs",
              "border-[var(--cat-border)]"
            )}
            style={
              {
                "--cat-border": `${color}55`,
              } as React.CSSProperties
            }
            title={`${row.layer} · last_refresh=${row.last_refresh || "—"}`}
          >
            <span
              className="w-2 h-2 rounded-full"
              style={{ background: color }}
              aria-hidden
            />
            {label}
            <span className="text-text-muted font-mono ml-1">{row.node_count.toLocaleString()}</span>
          </span>
        );
      })}
    </section>
  );
}
