import { Icon } from "@iconify/react";
import { useQueries, useQuery } from "@tanstack/react-query";
import clsx from "clsx";

import { api } from "../api/client";
import type { AwsServiceCategory, AwsServicesResponse, GraphNode } from "../api/types";
import { awsIconForType } from "../lib/awsIcons";
import { AWS_CAT_COLORS, awsTypeMark } from "../lib/categories";
import { useUI } from "../store/uiStore";

interface Props {
  data?: AwsServicesResponse;
  loading: boolean;
  error?: string;
}

export function ArchGrid({ data, loading, error }: Props) {
  const tileSelection = useUI((s) => s.awsTileSelection);
  const selectTile = useUI((s) => s.selectAwsTile);
  const categorySelection = useUI((s) => s.awsCategorySelection);
  const selectCategory = useUI((s) => s.selectAwsCategory);

  if (loading) {
    return <div className="text-xs text-text-muted">loading AWS scanner data…</div>;
  }
  if (error) {
    return <div className="text-xs text-accent-red">aws-services failed: {error}</div>;
  }
  if (!data || !data.service_categories.length) {
    return <div className="text-xs text-text-muted">No AWS resources scanned yet.</div>;
  }

  const selectedCategory = categorySelection
    ? data.service_categories.find((c) => c.category === categorySelection)
    : undefined;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-3 text-xs">
        <span className="font-mono text-text-muted">
          {data.total_resources.toLocaleString()} deployed resources
        </span>
        <span className="text-text-dim">·</span>
        <span className="font-mono text-text-muted">
          {data.service_categories.reduce((acc, c) => acc + c.service_count, 0)} AWS services
        </span>
        <span className="text-text-dim">·</span>
        <span className="font-mono text-text-muted">{data.category_count} categories</span>
      </div>

      <div className="grid grid-cols-[160px_1fr] gap-x-4 gap-y-3">
        {data.service_categories.map((cat) => {
          const catActive = categorySelection === cat.category;
          return (
            <div key={cat.category} className="contents">
              <button
                onClick={() => selectCategory(catActive ? null : cat.category)}
                className={clsx(
                  "flex flex-col items-start text-left rounded-md px-2 py-1.5 transition-colors",
                  catActive
                    ? "bg-bg-card border border-border-strong"
                    : "hover:bg-bg-hover border border-transparent",
                )}
                title={`Click to see all ${cat.resource_count} resources in ${cat.category}`}
              >
                <span
                  className="text-sm font-medium"
                  style={{ color: AWS_CAT_COLORS[cat.category] ?? "#fff" }}
                >
                  {cat.category}
                </span>
                <span className="text-[11px] text-text-muted">
                  {cat.service_count} services · {cat.resource_count} resources
                </span>
              </button>
              <div className="flex flex-wrap gap-2">
                {cat.services.map((svc) => {
                  const tileActive =
                    tileSelection?.category === cat.category &&
                    tileSelection?.nodeType === svc.node_type;
                  return (
                    <button
                      key={svc.node_type}
                      className={clsx("arch-tile", tileActive && "active")}
                      onClick={() =>
                        selectTile(
                          tileActive
                            ? null
                            : { category: cat.category, nodeType: svc.node_type },
                        )
                      }
                    >
                      <span className="badge-count">{svc.count}</span>
                      <div className="flex items-center gap-2">
                        <span
                          className="w-7 h-7 flex items-center justify-center rounded-sm shrink-0"
                          style={{
                            background: `${AWS_CAT_COLORS[cat.category] ?? "#888"}22`,
                          }}
                        >
                          <Icon
                            icon={awsIconForType(svc.node_type)}
                            width={20}
                            height={20}
                            // Inline fallback if iconify can't resolve — render
                            // the 2-letter glyph in the category colour so we
                            // never show a blank tile.
                            fallback={
                              <span
                                className="font-mono text-[10px] font-medium"
                                style={{ color: AWS_CAT_COLORS[cat.category] ?? "#fff" }}
                              >
                                {awsTypeMark(svc.node_type)}
                              </span>
                            }
                          />
                        </span>
                        <span className="text-sm text-text">{svc.service}</span>
                      </div>
                      <span className="text-[10px] font-mono text-text-muted truncate max-w-[180px]">
                        {svc.sample_label}
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>

      {tileSelection && <ArchTileDetail selection={tileSelection} />}
      {selectedCategory && <ArchCategoryDetail category={selectedCategory} />}
    </div>
  );
}

// Detail row for a single tile selection (one node_type).
function ArchTileDetail({ selection }: { selection: { category: string; nodeType: string } }) {
  const setTab = useUI((s) => s.setTab);
  const selectNode = useUI((s) => s.selectNode);

  const q = useQuery({
    queryKey: ["graph", "by-type", selection.nodeType],
    queryFn: () => api.graph({ type: selection.nodeType, limit: 200 }),
    staleTime: 60_000,
  });

  return (
    <div className="mt-2 rounded-lg border border-accent-blue/40 bg-bg-card overflow-hidden">
      <div className="px-4 py-2.5 border-b border-border flex items-center gap-3">
        <span
          className="w-2 h-2 rounded-full"
          style={{ background: AWS_CAT_COLORS[selection.category] ?? "#888" }}
        />
        <span className="text-sm font-medium text-text">{selection.nodeType}</span>
        <span className="text-xs text-text-muted">{q.data?.node_count ?? 0} resources</span>
        <button
          onClick={() => setTab("graph")}
          className="ml-auto text-xs text-accent-blue hover:underline"
        >
          open in 3D graph →
        </button>
      </div>
      <div className="max-h-72 overflow-auto">
        {q.isLoading && <div className="px-4 py-3 text-xs text-text-muted">loading…</div>}
        {q.error && (
          <div className="px-4 py-3 text-xs text-accent-red">
            {(q.error as Error).message}
          </div>
        )}
        {q.data?.nodes.length === 0 && (
          <div className="px-4 py-3 text-xs text-text-muted">no resources of this type yet</div>
        )}
        <ResourceTable nodes={q.data?.nodes ?? []} onPick={(id) => { selectNode(id); setTab("graph"); }} />
      </div>
    </div>
  );
}

// Detail row for a whole category — fetches every tile's nodes in parallel
// and merges them into one table.
function ArchCategoryDetail({ category }: { category: AwsServiceCategory }) {
  const setTab = useUI((s) => s.setTab);
  const selectNode = useUI((s) => s.selectNode);

  const queries = useQueries({
    queries: category.services.map((svc) => ({
      queryKey: ["graph", "by-type", svc.node_type],
      queryFn: () => api.graph({ type: svc.node_type, limit: 200 }),
      staleTime: 60_000,
    })),
  });

  const allNodes: GraphNode[] = queries.flatMap((q) => q.data?.nodes ?? []);
  const isLoading = queries.some((q) => q.isLoading);
  const firstError = queries.find((q) => q.error)?.error as Error | undefined;

  return (
    <div className="mt-2 rounded-lg border border-accent-blue/40 bg-bg-card overflow-hidden">
      <div className="px-4 py-2.5 border-b border-border flex items-center gap-3">
        <span
          className="w-2 h-2 rounded-full"
          style={{ background: AWS_CAT_COLORS[category.category] ?? "#888" }}
        />
        <span className="text-sm font-medium text-text">{category.category}</span>
        <span className="text-xs text-text-muted">
          {allNodes.length} of {category.resource_count} resources · {category.service_count} services
        </span>
        <button
          onClick={() => setTab("graph")}
          className="ml-auto text-xs text-accent-blue hover:underline"
        >
          open in 3D graph →
        </button>
      </div>
      <div className="max-h-96 overflow-auto">
        {isLoading && <div className="px-4 py-3 text-xs text-text-muted">loading…</div>}
        {firstError && (
          <div className="px-4 py-3 text-xs text-accent-red">{firstError.message}</div>
        )}
        <ResourceTable
          nodes={allNodes}
          showType
          onPick={(id) => {
            selectNode(id);
            setTab("graph");
          }}
        />
      </div>
    </div>
  );
}

function ResourceTable({
  nodes,
  showType,
  onPick,
}: {
  nodes: GraphNode[];
  showType?: boolean;
  onPick: (id: string) => void;
}) {
  if (nodes.length === 0) return null;
  return (
    <table className="w-full text-xs font-mono">
      <thead className="text-text-muted">
        <tr className="text-left">
          <th className="px-4 py-2 font-normal">id</th>
          {showType && <th className="px-4 py-2 font-normal">type</th>}
          <th className="px-4 py-2 font-normal">layer</th>
          <th className="px-4 py-2 font-normal">label</th>
        </tr>
      </thead>
      <tbody>
        {nodes.map((n) => (
          <tr
            key={n.id}
            className="border-t border-border hover:bg-bg-hover cursor-pointer"
            onClick={() => onPick(n.id)}
          >
            <td className="px-4 py-1.5 text-text truncate max-w-[420px]">{n.id}</td>
            {showType && <td className="px-4 py-1.5 text-text-muted">{n.type}</td>}
            <td className="px-4 py-1.5 text-text-muted">{n.layer}</td>
            <td className="px-4 py-1.5 text-text-muted truncate max-w-[300px]">{n.label}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
