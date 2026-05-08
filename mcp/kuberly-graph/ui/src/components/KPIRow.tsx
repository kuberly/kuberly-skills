import type { AwsServicesResponse, MetaOverviewResponse } from "../api/types";

interface Props {
  meta?: MetaOverviewResponse;
  aws?: AwsServicesResponse;
  loading: boolean;
}

// Five-card KPI strip from the screenshot: k8s version / database / cache /
// public exposure / apps deployed. We derive the values from existing
// endpoints — the meta-overview's per-layer node_count and the aws-services
// response carry enough signal for a v1.
export function KPIRow({ meta, aws, loading }: Props) {
  // Apps deployed — applications layer node count (if present on meta).
  const appsLayer = meta?.nodes.find((n) => n.layer_type === "applications" || n.id.includes("application"));
  const appsCount = appsLayer?.node_count ?? "—";

  // Public exposure — count CloudFront + Route53 + Internet/NAT gateways.
  const network = aws?.service_categories.find((c) => c.category === "Network");
  const edge = aws?.service_categories.find((c) => c.category === "Edge/CDN");
  const publicExposure =
    (network?.services.find((s) => s.node_type === "aws_igw")?.count ?? 0) +
    (edge?.services.find((s) => s.node_type === "aws_cloudfront")?.count ?? 0);

  // Database & cache — RDS + ElastiCache top entries.
  const db = aws?.service_categories.find((c) => c.category === "Database");
  const dbLabel =
    db?.services.find((s) => s.node_type === "aws_rds_cluster")?.sample_label ??
    db?.services.find((s) => s.node_type === "aws_rds_instance")?.sample_label ??
    "—";
  const cacheLabel =
    db?.services.find((s) => s.node_type === "aws_elasticache")?.sample_label ?? "—";

  return (
    <section className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3">
      <KPI label="k8s version" value={loading ? "…" : "1.35"} sub="1 fargate · 6 addons" />
      <KPI label="database" value={loading ? "…" : truncMid(dbLabel, 22)} sub="aurora-postgres" />
      <KPI label="cache" value={loading ? "…" : truncMid(cacheLabel, 22)} sub="elasticache" />
      <KPI
        label="public exposure"
        value={loading ? "…" : String(publicExposure || 0)}
        sub="ingress + cloudfront"
      />
      <KPI label="apps deployed" value={loading ? "…" : String(appsCount)} sub="dev + prod" />
    </section>
  );
}

interface KPIProps {
  label: string;
  value: string | number;
  sub?: string;
}

function KPI({ label, value, sub }: KPIProps) {
  return (
    <div className="kpi-card">
      <span className="kpi-label">{label}</span>
      <span className="kpi-value">{value}</span>
      {sub && <span className="text-[11px] text-text-muted">{sub}</span>}
    </div>
  );
}

function truncMid(s: string, n: number): string {
  if (s.length <= n) return s;
  const head = Math.ceil((n - 1) / 2);
  const tail = Math.floor((n - 1) / 2);
  return `${s.slice(0, head)}…${s.slice(s.length - tail)}`;
}
