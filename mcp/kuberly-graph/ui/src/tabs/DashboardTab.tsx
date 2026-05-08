import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import type { LayerSummary } from "../api/types";
import { ArchGrid } from "../components/ArchGrid";
import { DashboardCharts } from "../components/DashboardCharts";
import { OverlaysStrip } from "../components/OverlaysStrip";
import { KPIRow } from "../components/KPIRow";

export function DashboardTab() {
  const layers = useQuery({ queryKey: ["layers"], queryFn: api.layers });
  const meta = useQuery({ queryKey: ["meta-overview"], queryFn: api.metaOverview });
  const aws = useQuery({ queryKey: ["aws-services"], queryFn: api.awsServices });

  const layerRows: LayerSummary[] = layers.data ?? [];

  return (
    <div className="px-6 py-5 flex flex-col gap-6">
      {/* Layer chips — colour-keyed roll-up of every populated layer. */}
      <OverlaysStrip layers={layerRows} loading={layers.isLoading} error={layers.error?.message} />

      {/* KPI strip — five tiles, every value sourced from real data. */}
      <KPIRow meta={meta.data} aws={aws.data} loading={meta.isLoading || aws.isLoading} />

      {/* Charts — layer breakdown + AWS donut + compliance donut. */}
      <DashboardCharts />

      {/* Architecture grid — clickable categories + tiles, drilldown row. */}
      <section className="flex flex-col gap-3">
        <h2 className="text-base font-medium text-text">
          Architecture <span className="text-text-muted text-sm">— deployed AWS services</span>
        </h2>
        <p className="text-xs text-text-muted -mt-2">
          Click a category header to see every resource of that family, or a single tile for one node type.
        </p>
        <ArchGrid data={aws.data} loading={aws.isLoading} error={aws.error?.message} />
      </section>
    </div>
  );
}
