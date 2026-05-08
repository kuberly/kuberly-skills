import clsx from "clsx";

import { useUI, type GraphMode, type GroupBy, type Tab } from "../store/uiStore";

interface HeaderProps {
  statsLabel: string;
}

const TABS: { id: Tab; label: string }[] = [
  { id: "dashboard", label: "Dashboard" },
  { id: "graph", label: "Graph" },
];

export function Header({ statsLabel }: HeaderProps) {
  const tab = useUI((s) => s.activeTab);
  const setTab = useUI((s) => s.setTab);
  const search = useUI((s) => s.search);
  const setSearch = useUI((s) => s.setSearch);
  const groupBy = useUI((s) => s.groupBy);
  const setGroupBy = useUI((s) => s.setGroupBy);
  const graphMode = useUI((s) => s.graphMode);
  const setGraphMode = useUI((s) => s.setGraphMode);
  const resetCategories = useUI((s) => s.resetCategories);

  return (
    <header className="flex items-center gap-6 px-6 py-3 bg-bg-panel border-b border-border">
      {/* Brand */}
      <div className="flex items-center gap-2.5">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" aria-hidden>
          <path
            d="M11.3647 2.92733C11.7021 2.73258 12.1173 2.73119 12.4559 2.92369L19.8781 7.14305C20.2213 7.33813 20.4333 7.70247 20.4333 8.09721V16.5758C20.4333 16.9679 20.224 17.3303 19.8844 17.5263L19.5582 17.7146V18.6654C19.5582 19.4476 19.3772 20.2041 19.0449 20.8836L21.1282 19.6809C22.2376 19.0404 22.9211 17.8568 22.9211 16.5758V8.09721C22.9211 6.80772 22.2286 5.61756 21.1076 4.98029L13.6854 0.760927C12.5793 0.132111 11.2228 0.136639 10.1208 0.772828L7.66167 2.19263C6.55236 2.83309 5.86899 4.01672 5.86899 5.29765V13.7891C5.86899 15.07 6.55236 16.2536 7.66167 16.8941L12.2536 19.5452L14.1436 18.4542V17.7638L8.90558 14.7396C8.56599 14.5435 8.3568 14.1812 8.3568 13.7891V5.29765C8.3568 4.90553 8.56599 4.54319 8.90558 4.34713L11.3647 2.92733Z"
            fill="#1677ff"
          />
          <path
            d="M11.6634 4.44474L9.82021 5.5089V6.25864L15.0519 9.23272C15.395 9.42781 15.607 9.79214 15.607 10.1869V18.6655C15.607 19.0576 15.3978 19.4199 15.0582 19.616L12.5307 21.0751C12.1911 21.2711 11.7727 21.2711 11.4332 21.075L4.07931 16.8293C3.73972 16.6332 3.53053 16.2709 3.53053 15.8788V7.38732C3.53053 6.9952 3.73972 6.63287 4.07931 6.43681L4.40558 6.24844V5.29767C4.40558 4.51538 4.58658 3.75886 4.91902 3.07933L2.83541 4.2823C1.72609 4.92277 1.04272 6.10639 1.04272 7.38732V15.8788C1.04272 17.1597 1.72609 18.3433 2.83541 18.9838L10.1893 23.2295C11.2985 23.87 12.6652 23.87 13.7745 23.2296L16.302 21.7706C17.4114 21.1301 18.0948 19.9464 18.0948 18.6655V10.1869C18.0948 8.8974 17.4023 7.70723 16.2813 7.06996L11.6634 4.44474Z"
            fill="#1677ff"
          />
        </svg>
        <span className="font-medium text-text">kuberly-platform</span>
        <span className="text-xs text-text-muted">live multi-layer knowledge graph</span>
        <span className="pill-soft border-accent-blue/40 text-accent-blue/90">v0.52.0</span>
      </div>

      {/* Tabs */}
      <nav className="flex items-center gap-1" role="tablist">
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={tab === t.id}
            onClick={() => setTab(t.id)}
            className={clsx(
              "px-3 py-1.5 rounded-md text-sm font-medium transition-colors",
              tab === t.id
                ? "bg-bg-card text-text border border-border-strong"
                : "text-text-muted hover:text-text hover:bg-bg-card"
            )}
          >
            {t.label}
          </button>
        ))}
      </nav>

      {/* Right-side controls */}
      <div className="ml-auto flex items-center gap-3">
        <input
          type="search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search nodes…"
          autoComplete="off"
          className="w-64 px-3 py-1.5 rounded-md bg-bg-card border border-border text-sm
                     text-text placeholder:text-text-dim
                     focus:outline-none focus:border-accent-blue"
        />
        {tab === "graph" && (
          <>
            <div
              className="inline-flex rounded-md border border-border overflow-hidden"
              role="radiogroup"
              aria-label="Render mode"
            >
              {(["force3d", "cosmos"] as GraphMode[]).map((m) => (
                <button
                  key={m}
                  role="radio"
                  aria-checked={graphMode === m}
                  onClick={() => setGraphMode(m)}
                  title={
                    m === "force3d"
                      ? "react-force-graph-3d (Three.js, ~10k node ceiling)"
                      : "cosmos.gl 2D GPU (handles 100k+ nodes)"
                  }
                  className={clsx(
                    "px-2.5 py-1.5 text-xs font-medium transition-colors",
                    graphMode === m
                      ? "bg-accent-blue/20 text-text"
                      : "bg-bg-card text-text-muted hover:text-text",
                  )}
                >
                  {m === "force3d" ? "3D" : "2D · perf"}
                </button>
              ))}
            </div>
            <select
              value={groupBy}
              onChange={(e) => setGroupBy(e.target.value as GroupBy)}
              title="Group / colour by"
              className="px-2.5 py-1.5 rounded-md bg-bg-card border border-border text-sm text-text
                         focus:outline-none focus:border-accent-blue"
            >
              <option value="category">Group by category</option>
              <option value="layer">Group by layer</option>
              <option value="type">Group by type</option>
            </select>
            <button
              onClick={resetCategories}
              title="Clear all filters"
              className="px-2.5 py-1.5 rounded-md text-xs text-text-muted hover:text-text hover:bg-bg-card"
            >
              reset
            </button>
          </>
        )}
        <span className="text-xs font-mono text-text-muted">{statsLabel}</span>
      </div>
    </header>
  );
}
