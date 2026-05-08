import { useQuery } from "@tanstack/react-query";
import { lazy, Suspense } from "react";

import { Header } from "./components/Header";
import { DashboardTab } from "./tabs/DashboardTab";
import { useUI } from "./store/uiStore";
import { api } from "./api/client";

// Heavy 3D bundle stays out of the initial chunk — split via lazy().
const GraphTab = lazy(() => import("./tabs/GraphTab").then((m) => ({ default: m.GraphTab })));

export default function App() {
  const tab = useUI((s) => s.activeTab);

  // Stats drive the header counter ("N nodes · M edges"). Stale-while-revalidate.
  const stats = useQuery({ queryKey: ["stats"], queryFn: api.stats });

  return (
    <div className="h-full flex flex-col">
      <Header
        statsLabel={
          stats.data
            ? `${stats.data.node_count.toLocaleString()} nodes · ${stats.data.edge_count.toLocaleString()} edges`
            : "— nodes · — edges"
        }
      />
      <main className="flex-1 overflow-auto">
        {tab === "dashboard" && <DashboardTab />}
        {tab === "graph" && (
          <Suspense
            fallback={
              <div className="h-full flex items-center justify-center text-text-muted text-sm">
                Loading 3D engine…
              </div>
            }
          >
            <GraphTab />
          </Suspense>
        )}
      </main>
    </div>
  );
}
