import { create } from "zustand";

export type Tab = "dashboard" | "graph";

export type GroupBy = "category" | "layer" | "type";

// "force3d" → react-force-graph-3d (default; pretty, ~10k node ceiling).
// "cosmos"  → cosmos.gl 2D GPU mode (perf mode for very large graphs).
export type GraphMode = "force3d" | "cosmos";

// "internal" → let react-force-graph-3d run d3-force on the main thread.
// "worker"   → run d3-force-3d in a Web Worker, then pin positions on the
//              graph nodes. Recommended for graphs ≥ ~3k nodes.
export type SimMode = "internal" | "worker";

interface UIState {
  activeTab: Tab;
  setTab: (t: Tab) => void;

  selectedNodeId: string | null;
  selectNode: (id: string | null) => void;

  search: string;
  setSearch: (s: string) => void;

  groupBy: GroupBy;
  setGroupBy: (g: GroupBy) => void;

  graphMode: GraphMode;
  setGraphMode: (m: GraphMode) => void;

  simMode: SimMode;
  setSimMode: (m: SimMode) => void;

  // Dashboard arch tile drilldown — when set, the bottom row shows the
  // full resource list for this {category, node_type}.
  awsTileSelection: { category: string; nodeType: string } | null;
  selectAwsTile: (sel: { category: string; nodeType: string } | null) => void;

  // Whole-category drilldown — when set, the row shows every resource
  // across all tiles in this category. Clears the per-tile selection.
  awsCategorySelection: string | null;
  selectAwsCategory: (cat: string | null) => void;

  // Active category filter for the Graph view (toggle chips).
  activeCategories: Set<string>;
  toggleCategory: (c: string) => void;
  setAllCategories: (cs: string[]) => void;
  resetCategories: () => void;
}

export const useUI = create<UIState>((set) => ({
  activeTab: "dashboard",
  setTab: (t) => set({ activeTab: t }),

  selectedNodeId: null,
  selectNode: (id) => set({ selectedNodeId: id }),

  search: "",
  setSearch: (s) => set({ search: s }),

  groupBy: "category",
  setGroupBy: (g) => set({ groupBy: g }),

  graphMode: "force3d",
  setGraphMode: (m) => set({ graphMode: m }),

  simMode: "worker",
  setSimMode: (m) => set({ simMode: m }),

  awsTileSelection: null,
  selectAwsTile: (sel) => set({ awsTileSelection: sel, awsCategorySelection: null }),

  awsCategorySelection: null,
  selectAwsCategory: (cat) => set({ awsCategorySelection: cat, awsTileSelection: null }),

  activeCategories: new Set([
    "iac_files",
    "tg_state",
    "k8s_resources",
    "docs",
    "cue",
    "ci_cd",
    "applications",
    "live_observability",
    "aws",
    "dependency",
    "meta",
  ]),
  toggleCategory: (c) =>
    set((state) => {
      const next = new Set(state.activeCategories);
      if (next.has(c)) {
        next.delete(c);
      } else {
        next.add(c);
      }
      return { activeCategories: next };
    }),
  setAllCategories: (cs) => set({ activeCategories: new Set(cs) }),
  resetCategories: () =>
    set({
      activeCategories: new Set([
        "iac_files",
        "tg_state",
        "k8s_resources",
        "docs",
        "cue",
        "ci_cd",
        "applications",
        "live_observability",
        "aws",
        "dependency",
        "meta",
      ]),
    }),
}));
