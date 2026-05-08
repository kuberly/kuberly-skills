import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { lazy, Suspense, useEffect, useMemo, useRef } from "react";
import ForceGraph3D, { type ForceGraphMethods } from "react-force-graph-3d";
import * as THREE from "three";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";

import { api } from "../api/client";
import type { GraphEdge, GraphNode } from "../api/types";
import { CATEGORY_COLORS, CATEGORY_LABELS } from "../lib/categories";
import { useElementSize } from "../lib/useElementSize";
import { useForceWorker } from "../lib/useForceWorker";
import { useUI } from "../store/uiStore";
import { GraphSidebar } from "../components/GraphSidebar";

// Cosmos is heavy (~250 KB gzip) and only needed in perf mode — keep it
// out of the initial chunk.
const Graph2DCosmos = lazy(() =>
  import("../components/Graph2DCosmos").then((m) => ({ default: m.Graph2DCosmos })),
);

// Force-graph data shape after mapping (links use string ids; library
// resolves them to node refs internally).
interface FGData {
  nodes: GraphNode[];
  links: { source: string; target: string; relation: string }[];
}

export function GraphTab() {
  const [hostRef, size] = useElementSize<HTMLDivElement>();
  const fgRef = useRef<ForceGraphMethods<GraphNode, GraphEdge>>(undefined);

  const search = useUI((s) => s.search);
  const groupBy = useUI((s) => s.groupBy);
  const activeCategories = useUI((s) => s.activeCategories);
  const toggleCategory = useUI((s) => s.toggleCategory);
  const selectedNodeId = useUI((s) => s.selectedNodeId);
  const selectNode = useUI((s) => s.selectNode);
  const graphMode = useUI((s) => s.graphMode);
  const simMode = useUI((s) => s.simMode);

  const graphQ = useQuery({
    queryKey: ["graph-all"],
    // Walk all pages so the agent sees the whole graph; capped at 20k
    // to protect the browser. At 622 nodes today this is one page.
    queryFn: () => api.graphAll({ pageSize: 5000, maxNodes: 20_000 }),
    staleTime: 60_000,
  });

  // Filter visible nodes by toggled categories. Edges drop with their
  // endpoints so the simulation doesn't see dangling refs.
  const data: FGData = useMemo(() => {
    const all = graphQ.data;
    if (!all) return { nodes: [], links: [] };
    const visibleIds = new Set<string>();
    const nodes = all.nodes.filter((n) => {
      const cat = (n.category || "dependency") as string;
      if (!activeCategories.has(cat)) return false;
      visibleIds.add(n.id);
      return true;
    });
    const links = all.edges
      .filter((e) => visibleIds.has(e.source) && visibleIds.has(e.target))
      .map((e) => ({ source: e.source, target: e.target, relation: e.relation }));
    return { nodes, links };
  }, [graphQ.data, activeCategories]);

  // Off-thread d3-force simulation. Disabled in cosmos mode (cosmos has its
  // own GPU sim). When `done`, we pin positions onto the force-graph nodes
  // and let the internal engine treat them as fixed (no main-thread compute).
  const sim = useForceWorker(
    data.nodes,
    data.links as GraphEdge[],
    simMode === "worker" && graphMode === "force3d",
  );

  // Enrich nodes with pinned positions from the worker. Only pay the
  // map-allocation cost when the worker is done.
  const renderData: FGData = useMemo(() => {
    if (!sim.positions) return data;
    const pinned = data.nodes.map((n) => {
      const p = sim.positions!.get(n.id);
      if (!p) return n;
      return { ...n, x: p[0], y: p[1], z: p[2], fx: p[0], fy: p[1], fz: p[2] };
    });
    return { nodes: pinned, links: data.links };
  }, [data, sim.positions]);

  // Bloom postprocess pass — gives the "glowy graph" look. Hooked once,
  // after the renderer exists.
  useEffect(() => {
    const g = fgRef.current;
    if (!g) return;
    if (size.width === 0 || size.height === 0) return;
    try {
      const composer = (g as unknown as { postProcessingComposer: () => unknown }).postProcessingComposer();
      if (!composer || typeof (composer as { addPass?: unknown }).addPass !== "function") return;
      // Don't add bloom twice if React re-runs this effect.
      const flag = "__bloomAdded";
      type Carry = Record<string, unknown> & {
        addPass: (p: unknown) => void;
      };
      const c = composer as Carry;
      if (c[flag]) return;
      // Bloom params (strength, radius, threshold). Strength was 0.8 which
      // blew out yellow/orange categories into single huge blobs at scale —
      // dialing down + raising threshold so only genuinely bright pixels
      // bloom. The "glow" stays, the blowout doesn't.
      const bloom = new UnrealBloomPass(new THREE.Vector2(size.width, size.height), 0.35, 0.4, 0.85);
      c.addPass(bloom);
      c[flag] = true;
    } catch (err) {
      console.warn("bloom pass init failed", err);
    }
  }, [size.width, size.height]);

  // Camera: focus on selected node when it changes.
  useEffect(() => {
    const g = fgRef.current;
    if (!g || !selectedNodeId) return;
    const node = data.nodes.find((n) => n.id === selectedNodeId);
    if (!node) return;
    // Force-graph stores positions on the node objects after layout.
    const n = node as GraphNode & { x?: number; y?: number; z?: number };
    if (n.x == null || n.y == null || n.z == null) return;
    const dist = 90;
    const px = n.x;
    const py = n.y;
    const pz = n.z || 1;
    const ratio = 1 + dist / Math.hypot(px, py, pz);
    g.cameraPosition({ x: px * ratio, y: py * ratio, z: pz * ratio }, { x: px, y: py, z: pz }, 800);
  }, [selectedNodeId, data.nodes]);

  // Tweak forces once after first render — softer charge, slightly longer
  // links so the categories spread out instead of all clumping.
  useEffect(() => {
    const g = fgRef.current;
    if (!g) return;
    const charge = g.d3Force?.("charge") as { strength?: (n: number) => unknown } | undefined;
    if (charge?.strength) charge.strength(-55);
    const link = g.d3Force?.("link") as { distance?: (n: number) => unknown } | undefined;
    if (link?.distance) link.distance(40);
  }, [data.nodes.length === 0]);

  // One-time scene polish: drop a sparse starfield behind the graph and run
  // a cinematic camera dolly-in on first paint. Both run inside the same
  // effect so the THREE scene is guaranteed to exist.
  const introPlayed = useRef(false);
  useEffect(() => {
    const g = fgRef.current;
    if (!g || size.width === 0 || size.height === 0) return;
    const sceneFn = (g as unknown as { scene?: () => THREE.Scene }).scene;
    if (typeof sceneFn !== "function") return;
    const scene = sceneFn.call(g);
    // Avoid stacking starfields if the effect re-runs.
    if (!scene.getObjectByName("starfield")) {
      const starCount = 1500;
      const positions = new Float32Array(starCount * 3);
      for (let i = 0; i < starCount; i++) {
        // Distribute on a large sphere so stars sit "outside" the graph.
        const r = 1800 + Math.random() * 800;
        const theta = Math.random() * Math.PI * 2;
        const phi = Math.acos(2 * Math.random() - 1);
        positions[i * 3] = r * Math.sin(phi) * Math.cos(theta);
        positions[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
        positions[i * 3 + 2] = r * Math.cos(phi);
      }
      const geom = new THREE.BufferGeometry();
      geom.setAttribute("position", new THREE.BufferAttribute(positions, 3));
      const mat = new THREE.PointsMaterial({
        color: 0xffffff,
        size: 1.4,
        sizeAttenuation: true,
        transparent: true,
        opacity: 0.55,
        depthWrite: false,
      });
      const stars = new THREE.Points(geom, mat);
      stars.name = "starfield";
      scene.add(stars);
    }
  }, [size.width, size.height]);

  // Cinematic intro: pull the camera way out, then dolly back in over ~2.5s
  // once the engine settles. Runs once per data load.
  function onEngineStop() {
    if (introPlayed.current) return;
    introPlayed.current = true;
    const g = fgRef.current;
    if (!g) return;
    // Snap far out, then animate to a comfortable distance.
    g.cameraPosition({ x: 0, y: 0, z: 1400 }, undefined, 0);
    setTimeout(() => g.cameraPosition({ x: 0, y: 0, z: 380 }, { x: 0, y: 0, z: 0 }, 2400), 60);
  }

  const nodeColor = (n: GraphNode): string => {
    if (search) {
      const q = search.toLowerCase();
      const hit =
        (n.id || "").toLowerCase().includes(q) ||
        (n.label || "").toLowerCase().includes(q);
      return hit ? "#ffffff" : "rgba(255,255,255,0.10)";
    }
    if (groupBy === "type") {
      // Stable colour per type via a tiny hash.
      const palette = [
        "#1677ff", "#ff9900", "#ff5552", "#a259ff", "#3ddc84",
        "#ff4f9c", "#f5b800", "#9da3ad", "#c0c4cc",
      ];
      let h = 0;
      for (let i = 0; i < (n.type || "").length; i++) h = (h * 31 + n.type.charCodeAt(i)) | 0;
      return palette[Math.abs(h) % palette.length];
    }
    if (groupBy === "layer") {
      return CATEGORY_COLORS[layerToCategoryUi(n.layer)] ?? "#888";
    }
    return CATEGORY_COLORS[(n.category as string) || "dependency"] ?? "#888";
  };

  return (
    <div className="h-[calc(100vh-57px)] flex">
      {/* Main 3D canvas region — true space-black background with a faint
          radial vignette so depth reads. Stars render on top via Three.js. */}
      <div
        className="flex-1 relative"
        ref={hostRef}
        style={{
          background:
            "radial-gradient(ellipse at center, #050714 0%, #02030a 60%, #000000 100%)",
        }}
      >
        {/* Active-category chips */}
        <div className="absolute top-3 left-3 z-10 flex flex-wrap gap-1.5 max-w-[60%]">
          {Object.entries(CATEGORY_LABELS).map(([cat, label]) => {
            const active = activeCategories.has(cat);
            return (
              <button
                key={cat}
                onClick={() => toggleCategory(cat)}
                className={clsx(
                  "pill text-[11px] border transition-colors",
                  active
                    ? "bg-bg-card text-text border-border-strong"
                    : "bg-bg-panel text-text-muted border-border opacity-60 hover:opacity-100"
                )}
              >
                <span
                  className="w-1.5 h-1.5 rounded-full"
                  style={{ background: CATEGORY_COLORS[cat] || "#888" }}
                />
                {label}
              </button>
            );
          })}
        </div>

        {graphQ.isLoading && (
          <div className="absolute inset-0 flex items-center justify-center text-text-muted text-sm">
            loading graph…
          </div>
        )}
        {graphQ.error && (
          <div className="absolute inset-0 flex items-center justify-center text-accent-red text-sm">
            {(graphQ.error as Error).message}
          </div>
        )}

        {graphMode === "force3d" && size.width > 0 && size.height > 0 && (
          <ForceGraph3D
            ref={fgRef}
            graphData={renderData}
            width={size.width}
            height={size.height}
            // Transparent so the radial-gradient on the host shows through;
            // gives a deeper "space" look than a flat colour.
            backgroundColor="rgba(0,0,0,0)"
            nodeId="id"
            nodeRelSize={5}
            nodeOpacity={1}
            nodeColor={nodeColor}
            nodeLabel={(n) => makeNodeTooltip(n as GraphNode)}
            nodeResolution={renderData.nodes.length > 4000 ? 6 : 10}
            linkColor={() => "rgba(255,255,255,0.08)"}
            linkOpacity={0.6}
            linkWidth={0.6}
            linkDirectionalParticles={renderData.nodes.length > 4000 ? 0 : 1}
            linkDirectionalParticleSpeed={0.005}
            linkDirectionalParticleWidth={1.4}
            controlType="orbit"
            // When the worker pinned positions, we don't need the main-thread
            // sim — pinned nodes have fx/fy/fz set, so internal forces are a
            // no-op anyway. Skip warmup + cooldown to render instantly.
            cooldownTicks={sim.status === "done" ? 0 : undefined}
            cooldownTime={sim.status === "done" ? 0 : 15_000}
            warmupTicks={sim.status === "done" ? 0 : 60}
            enableNodeDrag={sim.status !== "done"}
            onEngineStop={onEngineStop}
            onNodeClick={(n) => selectNode((n as GraphNode).id)}
            onBackgroundClick={() => selectNode(null)}
          />
        )}

        {graphMode === "cosmos" && (
          <Suspense
            fallback={
              <div className="absolute inset-0 flex items-center justify-center text-text-muted text-sm">
                loading cosmos.gl engine…
              </div>
            }
          >
            <Graph2DCosmos nodes={renderData.nodes} edges={renderData.links as unknown as GraphEdge[]} />
          </Suspense>
        )}

        {/* Worker progress indicator — only shown while the off-thread
            simulation is grinding through ticks. */}
        {graphMode === "force3d" && sim.status === "running" && (
          <div className="absolute bottom-3 left-3 z-10 text-[11px] font-mono text-text-muted bg-bg-panel/85 border border-border rounded-md px-2.5 py-1.5">
            laying out (worker) · {Math.round(sim.progress * 100)}%
          </div>
        )}

        {!graphQ.isLoading && renderData.nodes.length === 0 && (
          <div className="absolute inset-0 flex items-center justify-center text-text-muted text-sm">
            no nodes match the current filters
          </div>
        )}
      </div>

      <GraphSidebar />
    </div>
  );
}

function makeNodeTooltip(n: GraphNode): string {
  // Returned string is rendered as innerHTML by force-graph; keep it simple.
  const safe = (s: string) => s.replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c] as string));
  return `<div style="font-family:Geist,system-ui,sans-serif;font-size:12px;padding:6px 8px;background:rgba(20,24,30,0.95);border:1px solid rgba(255,255,255,0.18);border-radius:6px;color:#fff;">${safe(n.label || n.id)}<br><span style="opacity:0.6;font-family:JetBrains Mono,ui-monospace,monospace;font-size:10px;">${safe(n.type || "")} · ${safe(n.layer || "")}</span></div>`;
}

function layerToCategoryUi(layer: string): string {
  if (layer.startsWith("iac") || layer === "code") return "iac_files";
  if (layer.includes("state")) return "tg_state";
  if (layer.includes("k8s") || layer.includes("kubernetes")) return "k8s_resources";
  if (layer.includes("doc")) return "docs";
  if (layer.includes("cue") || layer.includes("schema")) return "cue";
  if (layer.includes("ci") || layer.includes("workflow") || layer.includes("image")) return "ci_cd";
  if (layer.includes("app") || layer.includes("rendered")) return "applications";
  if (layer.startsWith("aws")) return "aws";
  return "dependency";
}
