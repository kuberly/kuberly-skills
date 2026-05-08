import { Graph as CosmosGraph } from "@cosmograph/cosmos";
import { useEffect, useMemo, useRef, useState } from "react";

import type { GraphEdge, GraphNode } from "../api/types";
import { CATEGORY_COLORS } from "../lib/categories";
import { useElementSize } from "../lib/useElementSize";
import { useUI } from "../store/uiStore";

// 2D big-graph view backed by cosmos.gl (regl/WebGL). Trades the 3D depth
// of react-force-graph-3d for raw GPU throughput — handles 100k+ nodes at
// 60 fps on a laptop, and the dark space aesthetic comes for free.
//
// cosmos v2 takes Float32Array buffers (point colors/sizes, link index pairs)
// rather than the v1 object-shaped API; this component does the marshalling.
//
// Renders as a sibling alternative to GraphTab's ForceGraph3D, gated by
// `useUI().graphMode === "cosmos"`. Lazy-loaded so the cosmos bundle only
// downloads when the toggle is flipped.

export function Graph2DCosmos({
  nodes,
  edges,
}: {
  nodes: GraphNode[];
  edges: GraphEdge[];
}) {
  const [hostRef, size] = useElementSize<HTMLDivElement>();
  const containerRef = useRef<HTMLDivElement>(null);
  const cosmosRef = useRef<CosmosGraph | null>(null);
  const idsRef = useRef<string[]>([]);
  const selectNode = useUI((s) => s.selectNode);
  const [initError, setInitError] = useState<string | null>(null);

  // Pre-compute the parallel Float32Array buffers cosmos consumes. Done
  // in a useMemo so a re-render with the same data identity doesn't
  // reallocate.
  const buffers = useMemo(() => {
    const n = nodes.length;
    const ids = nodes.map((node) => node.id);
    const indexById = new Map<string, number>();
    for (let i = 0; i < n; i++) indexById.set(nodes[i].id, i);

    const colors = new Float32Array(n * 4);
    for (let i = 0; i < n; i++) {
      const cat = (nodes[i].category as string) || "dependency";
      const [r, g, b] = hexToRgb(CATEGORY_COLORS[cat] ?? "#9DA3AD");
      colors[i * 4] = r;
      colors[i * 4 + 1] = g;
      colors[i * 4 + 2] = b;
      colors[i * 4 + 3] = 1;
    }

    // 8 px points so they're visible at the default zoom level. (4 px was
    // sub-pixel for graphs with this many points spread across spaceSize.)
    const sizes = new Float32Array(n).fill(8);

    // Seed initial positions on a small disk inside cosmos's spaceSize
    // (4096 default). Without this cosmos can't run its GPU sim — the
    // points buffer is required and must have non-NaN values.
    const positions = new Float32Array(n * 2);
    for (let i = 0; i < n; i++) {
      const r = Math.sqrt(Math.random()) * 800;
      const t = Math.random() * Math.PI * 2;
      positions[i * 2] = Math.cos(t) * r;
      positions[i * 2 + 1] = Math.sin(t) * r;
    }

    const validEdges: GraphEdge[] = [];
    for (const e of edges) {
      if (indexById.has(e.source) && indexById.has(e.target)) validEdges.push(e);
    }
    const links = new Float32Array(validEdges.length * 2);
    for (let i = 0; i < validEdges.length; i++) {
      links[i * 2] = indexById.get(validEdges[i].source)!;
      links[i * 2 + 1] = indexById.get(validEdges[i].target)!;
    }

    return { ids, colors, sizes, links, positions };
  }, [nodes, edges]);

  // Set up the cosmos instance once, and feed it data whenever the buffers
  // change. The instance is reused across data updates to avoid the GPU
  // context churn of destroying + recreating regl.
  useEffect(() => {
    if (!containerRef.current || size.width === 0 || size.height === 0) return;

    try {
      if (!cosmosRef.current) {
        cosmosRef.current = new CosmosGraph(containerRef.current, {
          backgroundColor: "#02030a",
          spaceSize: 4096,
          pointSize: 8,
          renderLinks: true,
          linkColor: [1, 1, 1, 0.12],
          linkWidth: 1,
          linkArrows: false,
          showFPSMonitor: false,
          simulationGravity: 0.1,
          simulationRepulsion: 1.0,
          simulationLinkSpring: 0.5,
          simulationLinkDistance: 1.5,
          simulationFriction: 0.85,
          scalePointsOnZoom: true,
          // cosmos returns the clicked point's index plus its 2D position
          // and the source MouseEvent; we only need the index here.
          onClick: (idx: number | undefined) => {
            if (idx == null) {
              selectNode(null);
              return;
            }
            const id = idsRef.current[idx];
            if (id) selectNode(id);
          },
        });
      }

      idsRef.current = buffers.ids;
      cosmosRef.current.setPointPositions(buffers.positions);
      cosmosRef.current.setPointColors(buffers.colors);
      cosmosRef.current.setPointSizes(buffers.sizes);
      cosmosRef.current.setLinks(buffers.links);
      cosmosRef.current.start(0.5);
      // Fit view after a beat so the initial sim has spread the points out.
      setTimeout(() => cosmosRef.current?.fitView(800, 60), 250);
      // eslint-disable-next-line no-console
      console.info("[cosmos] initialized", {
        nodes: buffers.ids.length,
        links: buffers.links.length / 2,
      });
      setInitError(null);
    } catch (err) {
      // eslint-disable-next-line no-console
      console.error("[cosmos] init failed", err);
      setInitError(err instanceof Error ? err.message : String(err));
    }
  }, [buffers, size.width, size.height, selectNode]);

  // Cleanup on unmount — terminate the regl context and free GPU memory.
  useEffect(() => {
    return () => {
      cosmosRef.current?.destroy?.();
      cosmosRef.current = null;
    };
  }, []);

  return (
    <div
      ref={hostRef}
      className="absolute inset-0"
      style={{
        background:
          "radial-gradient(ellipse at center, #050714 0%, #02030a 60%, #000000 100%)",
      }}
    >
      <div ref={containerRef} style={{ width: "100%", height: "100%" }} />
      <div className="absolute bottom-2 right-3 text-[10px] font-mono text-text-dim pointer-events-none">
        cosmos.gl · 2D GPU mode
      </div>
      {initError && (
        <div className="absolute top-3 left-3 right-3 text-xs text-accent-red bg-bg-panel/90 border border-accent-red/40 rounded-md px-3 py-2 font-mono">
          cosmos.gl init failed: {initError}
        </div>
      )}
    </div>
  );
}

// "#1677ff" → [0.086, 0.467, 1.0] in 0..1 floats.
function hexToRgb(hex: string): [number, number, number] {
  const s = hex.replace("#", "");
  const r = parseInt(s.slice(0, 2), 16) / 255;
  const g = parseInt(s.slice(2, 4), 16) / 255;
  const b = parseInt(s.slice(4, 6), 16) / 255;
  return [r, g, b];
}
