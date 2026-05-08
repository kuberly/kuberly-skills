import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import {
  Bar,
  BarChart,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { api } from "../api/client";
import type { LayerSummary } from "../api/types";
import { CATEGORY_COLORS } from "../lib/categories";

// Top-of-dashboard charts panel. Three side-by-side cards:
//   1. Layer breakdown — horizontal bars, biggest at top, coloured by
//      category. Answers "where is the data weight?"
//   2. AWS architecture — donut share by category. Answers "what kind
//      of cloud surface do we have?"
//   3. Compliance — donut share by severity. Answers "how worried
//      should I be?"
//
// All three lazy-fetch via TanStack Query and render skeletons while
// loading. Never crash the dashboard if a chart's data source is empty;
// degrade to a soft "no data yet" message inline.

const SEVERITY_COLORS: Record<string, string> = {
  HIGH: "#ff5552",
  MEDIUM: "#f5b800",
  LOW: "#3ddc84",
  INFO: "#9da3ad",
};

const AWS_CAT_COLORS_ARRAY: Record<string, string> = {
  Compute: "#1677ff",
  Storage: "#3ddc84",
  Database: "#3ddc84",
  Network: "#ff9900",
  "Security/IAM": "#f5b800",
  "Edge/CDN": "#a259ff",
  Monitoring: "#9da3ad",
  Messaging: "#ff4f9c",
  "Lambda/Serverless": "#ff4f9c",
  Other: "#c0c4cc",
};

function layerToCategory(layer: string): string {
  const m: Record<string, string> = {
    code: "iac_files", iac: "iac_files", static: "iac_files", terragrunt: "iac_files",
    treesitter: "iac_files", components: "iac_files",
    state: "tg_state", tg_state: "tg_state", tofu_state: "tg_state",
    k8s: "k8s_resources", kubernetes: "k8s_resources", kubectl: "k8s_resources",
    docs: "docs", doc: "docs",
    cue: "cue", schema: "cue", cue_schema: "cue",
    ci_cd: "ci_cd", image_build: "ci_cd", github_actions: "ci_cd",
    applications: "applications", rendered: "applications", argo: "applications",
    logs: "live_observability", metrics: "live_observability", traces: "live_observability",
    alerts: "live_observability", alert: "live_observability", profiles: "live_observability",
    compliance: "live_observability", cost: "live_observability", dns: "live_observability",
    secrets: "live_observability",
    aws: "aws", network: "aws", iam: "aws", storage: "aws",
    meta: "meta", cold: "meta",
  };
  return m[layer] ?? "dependency";
}

export function DashboardCharts() {
  const layers = useQuery({ queryKey: ["layers"], queryFn: api.layers });
  const aws = useQuery({ queryKey: ["aws-services"], queryFn: api.awsServices });
  const compliance = useQuery({ queryKey: ["compliance"], queryFn: () =>
    fetch("/api/v1/compliance").then((r) => (r.ok ? r.json() : Promise.reject(r))),
  });

  return (
    <section className="grid grid-cols-1 lg:grid-cols-3 gap-3">
      <ChartCard title="Layer weight" subtitle="Nodes per layer, top 10">
        <LayerBreakdownChart layers={layers.data ?? []} loading={layers.isLoading} />
      </ChartCard>

      <ChartCard
        title="AWS architecture"
        subtitle={`${aws.data?.total_resources ?? 0} resources · ${aws.data?.category_count ?? 0} categories`}
      >
        <AwsCategoryDonut data={aws.data} loading={aws.isLoading} />
      </ChartCard>

      <ChartCard
        title="Compliance"
        subtitle={`${(compliance.data as ComplianceResp | undefined)?.total ?? 0} findings`}
      >
        <ComplianceDonut data={compliance.data as ComplianceResp | undefined} loading={compliance.isLoading} />
      </ChartCard>
    </section>
  );
}

// -----------------------------------------------------------------
// Components

function ChartCard({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="bg-bg-card border border-border rounded-lg p-4 flex flex-col gap-2 min-h-[200px]">
      <div className="flex items-baseline justify-between">
        <h3 className="text-sm font-medium text-text">{title}</h3>
        {subtitle && (
          <span className="text-[11px] font-mono text-text-muted">{subtitle}</span>
        )}
      </div>
      <div className="flex-1 min-h-[160px]">{children}</div>
    </div>
  );
}

function LayerBreakdownChart({ layers, loading }: { layers: LayerSummary[]; loading: boolean }) {
  const data = useMemo(() => {
    return layers
      .filter((r) => typeof r?.name === "string" && (r.node_count ?? 0) > 0)
      .map((r) => ({
        name: r.name,
        nodes: r.node_count,
        color: CATEGORY_COLORS[layerToCategory(r.name)] ?? "#888",
      }))
      .sort((a, b) => b.nodes - a.nodes)
      .slice(0, 10);
  }, [layers]);

  if (loading) return <Skeleton />;
  if (data.length === 0) return <Empty msg="no layers populated yet" />;

  return (
    <ResponsiveContainer width="100%" height={220}>
      <BarChart
        data={data}
        layout="vertical"
        margin={{ top: 2, right: 24, bottom: 2, left: 4 }}
      >
        <XAxis type="number" hide />
        <YAxis
          type="category"
          dataKey="name"
          width={80}
          tick={{ fill: "#8a92a3", fontSize: 11, fontFamily: "JetBrains Mono, monospace" }}
          axisLine={false}
          tickLine={false}
        />
        <Tooltip
          contentStyle={{
            background: "#11141a",
            border: "1px solid rgba(255,255,255,0.14)",
            borderRadius: 6,
            fontSize: 12,
          }}
          itemStyle={{ color: "#e6e8eb" }}
          cursor={{ fill: "rgba(255,255,255,0.04)" }}
        />
        <Bar dataKey="nodes" radius={[0, 4, 4, 0]} label={{ position: "right", fill: "#8a92a3", fontSize: 10 }}>
          {data.map((d) => (
            <Cell key={d.name} fill={d.color} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

function AwsCategoryDonut({
  data,
  loading,
}: {
  data?: { service_categories: { category: string; resource_count: number }[] };
  loading: boolean;
}) {
  const slices = useMemo(() => {
    if (!data) return [];
    return data.service_categories.map((c) => ({
      name: c.category,
      value: c.resource_count,
      color: AWS_CAT_COLORS_ARRAY[c.category] ?? "#888",
    }));
  }, [data]);

  if (loading) return <Skeleton />;
  if (slices.length === 0) return <Empty msg="no AWS resources scanned yet" />;

  return (
    <div className="flex items-center gap-3 h-full">
      <ResponsiveContainer width="55%" height={220}>
        <PieChart>
          <Pie
            data={slices}
            dataKey="value"
            nameKey="name"
            innerRadius={42}
            outerRadius={75}
            paddingAngle={2}
            stroke="none"
          >
            {slices.map((s) => (
              <Cell key={s.name} fill={s.color} />
            ))}
          </Pie>
          <Tooltip
            contentStyle={{
              background: "#11141a",
              border: "1px solid rgba(255,255,255,0.14)",
              borderRadius: 6,
              fontSize: 12,
            }}
            itemStyle={{ color: "#e6e8eb" }}
          />
        </PieChart>
      </ResponsiveContainer>
      <div className="flex-1 flex flex-col gap-1 text-[11px] font-mono">
        {slices.map((s) => (
          <div key={s.name} className="flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full shrink-0" style={{ background: s.color }} />
            <span className="text-text-muted truncate">{s.name}</span>
            <span className="ml-auto text-text">{s.value}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

interface ComplianceResp {
  total?: number;
  by_severity?: Record<string, number>;
}

function ComplianceDonut({ data, loading }: { data?: ComplianceResp; loading: boolean }) {
  const slices = useMemo(() => {
    if (!data?.by_severity) return [];
    return Object.entries(data.by_severity)
      .filter(([, v]) => (v ?? 0) > 0)
      .map(([k, v]) => ({
        name: k,
        value: v,
        color: SEVERITY_COLORS[k.toUpperCase()] ?? "#9da3ad",
      }))
      .sort((a, b) => b.value - a.value);
  }, [data]);

  if (loading) return <Skeleton />;
  if (slices.length === 0) return <Empty msg="no compliance findings" />;

  return (
    <div className="flex items-center gap-3 h-full">
      <ResponsiveContainer width="55%" height={220}>
        <PieChart>
          <Pie
            data={slices}
            dataKey="value"
            nameKey="name"
            innerRadius={42}
            outerRadius={75}
            paddingAngle={2}
            stroke="none"
          >
            {slices.map((s) => (
              <Cell key={s.name} fill={s.color} />
            ))}
          </Pie>
          <Tooltip
            contentStyle={{
              background: "#11141a",
              border: "1px solid rgba(255,255,255,0.14)",
              borderRadius: 6,
              fontSize: 12,
            }}
            itemStyle={{ color: "#e6e8eb" }}
          />
        </PieChart>
      </ResponsiveContainer>
      <div className="flex-1 flex flex-col gap-1 text-[11px] font-mono">
        {slices.map((s) => (
          <div key={s.name} className="flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full shrink-0" style={{ background: s.color }} />
            <span className="text-text-muted">{s.name}</span>
            <span className="ml-auto text-text">{s.value}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function Skeleton() {
  return (
    <div className="h-[220px] flex items-center justify-center text-xs text-text-muted">
      loading…
    </div>
  );
}

function Empty({ msg }: { msg: string }) {
  return (
    <div className="h-[220px] flex items-center justify-center text-xs text-text-dim">
      {msg}
    </div>
  );
}
