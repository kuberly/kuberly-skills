import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import type { AwsServicesResponse, MetaOverviewResponse, StatsResponse } from "../api/types";

interface Props {
  meta?: MetaOverviewResponse;
  aws?: AwsServicesResponse;
  loading: boolean;
}

// Five compact KPI tiles. Every value comes from real backend data — no
// hand-wavy "aurora-postgres" placeholders. When a layer is empty the
// tile shows 0 (which is itself signal — "no compliance findings" is a
// meaningful answer).
export function KPIRow({ meta, aws, loading }: Props) {
  const stats = useQuery({ queryKey: ["stats"], queryFn: api.stats });
  const compliance = useQuery({
    queryKey: ["compliance"],
    queryFn: () =>
      fetch("/api/v1/compliance").then((r) => (r.ok ? r.json() : Promise.reject(r))),
  });

  const totalNodes = (stats.data as StatsResponse | undefined)?.total_nodes ?? 0;
  const totalEdges = (stats.data as StatsResponse | undefined)?.total_edges ?? 0;
  const awsResources = aws?.total_resources ?? 0;
  const awsCategories = aws?.category_count ?? 0;
  const apps =
    meta?.nodes.find((n) => n.layer_type === "applications" || n.id.includes("application"))
      ?.node_count ?? 0;

  const cmp = compliance.data as
    | { total?: number; by_severity?: Record<string, number> }
    | undefined;
  const findings = cmp?.total ?? 0;
  const high = cmp?.by_severity?.HIGH ?? cmp?.by_severity?.high ?? 0;

  // K8s = sum of k8s + kubectl layers. Pulled from per-layer stats.
  const perLayer = (stats.data as StatsResponse | undefined)?.per_layer ?? {};
  const k8sCount = (perLayer.k8s?.nodes ?? 0) + (perLayer.kubectl?.nodes ?? 0);

  return (
    <section className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3">
      <KPI
        label="graph size"
        value={loading ? "…" : totalNodes.toLocaleString()}
        sub={`${totalEdges.toLocaleString()} edges`}
        accent="text"
      />
      <KPI
        label="AWS resources"
        value={loading ? "…" : awsResources.toLocaleString()}
        sub={`${awsCategories} categories`}
        accent="orange"
      />
      <KPI
        label="kubernetes"
        value={loading ? "…" : k8sCount.toLocaleString()}
        sub="k8s + kubectl"
        accent="red"
      />
      <KPI
        label="compliance"
        value={loading ? "…" : findings.toLocaleString()}
        sub={high > 0 ? `${high} HIGH` : "no high-severity"}
        accent={high > 0 ? "red" : "green"}
      />
      <KPI
        label="apps deployed"
        value={loading ? "…" : apps.toLocaleString()}
        sub="across envs"
        accent="pink"
      />
    </section>
  );
}

type Accent = "text" | "blue" | "orange" | "red" | "green" | "pink";

const ACCENT_STYLE: Record<Accent, string> = {
  text: "text-text",
  blue: "text-accent-blue",
  orange: "text-accent-orange",
  red: "text-accent-red",
  green: "text-accent-green",
  pink: "text-accent-pink",
};

interface KPIProps {
  label: string;
  value: string | number;
  sub?: string;
  accent?: Accent;
}

function KPI({ label, value, sub, accent = "text" }: KPIProps) {
  return (
    <div className="kpi-card">
      <span className="kpi-label">{label}</span>
      <span className={`kpi-value ${ACCENT_STYLE[accent]}`}>{value}</span>
      {sub && <span className="text-[11px] text-text-muted">{sub}</span>}
    </div>
  );
}
