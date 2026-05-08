import { create } from "zustand";

export type Tab = "dashboard" | "graph";

export type GroupBy = "category" | "layer" | "type";

interface UIState {
  activeTab: Tab;
  setTab: (t: Tab) => void;

  selectedNodeId: string | null;
  selectNode: (id: string | null) => void;

  search: string;
  setSearch: (s: string) => void;

  groupBy: GroupBy;
  setGroupBy: (g: GroupBy) => void;

  // Dashboard arch tile drilldown — when set, the bottom row shows the
  // full resource list for this {category, node_type}.
  awsTileSelection: { category: string; nodeType: string } | null;
  selectAwsTile: (sel: { category: string; nodeType: string } | null) => void;

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

  awsTileSelection: null,
  selectAwsTile: (sel) => set({ awsTileSelection: sel }),

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
