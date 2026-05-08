import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import type { LayerSummary } from "../api/types";
import { ArchGrid } from "../components/ArchGrid";
import { OverlaysStrip } from "../components/OverlaysStrip";
import { NodeSpotlight } from "../components/NodeSpotlight";
import { KPIRow } from "../components/KPIRow";

export function DashboardTab() {
  const layers = useQuery({ queryKey: ["layers"], queryFn: api.layers });
  const meta = useQuery({ queryKey: ["meta-overview"], queryFn: api.metaOverview });
  const aws = useQuery({ queryKey: ["aws-services"], queryFn: api.awsServices });

  const layerRows: LayerSummary[] = layers.data ?? [];

  return (
    <div className="px-6 py-5 flex flex-col gap-6">
      {/* Top overlays strip — OpenSpec / docs / state snapshots / doc-linked. */}
      <OverlaysStrip layers={layerRows} loading={layers.isLoading} error={layers.error?.message} />

      {/* Five-column KPI bar — k8s ver / db / cache / public exposure / apps. */}
      <KPIRow meta={meta.data} aws={aws.data} loading={meta.isLoading || aws.isLoading} />

      {/* Architecture grid — categories x services. The big rebuild target
          from the legacy dashboard screenshot. */}
      <section className="flex flex-col gap-3">
        <h2 className="text-base font-medium text-text">
          Architecture <span className="text-text-muted text-sm">— deployed AWS services</span>
        </h2>
        <p className="text-xs text-text-muted -mt-2">
          Click a tile to see every resource of that type, or open the 3D graph filtered to it.
        </p>
        <ArchGrid data={aws.data} loading={aws.isLoading} error={aws.error?.message} />
      </section>

      {/* Node spotlight — search + filter chips + neighborhood preview. */}
      <NodeSpotlight />
    </div>
  );
}
