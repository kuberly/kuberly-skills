import { useEffect, useRef, useState } from "react";

import type { GraphEdge, GraphNode } from "../api/types";

// Spawn a Web Worker (see forceSim.worker.ts) that runs d3-force-3d off the
// main thread, and return its progress + final positions. Vite resolves the
// `?worker` import into a worker constructor at build time.
//
// Lifecycle: re-runs whenever the input data identity changes. The previous
// worker is terminated before a new one is spawned, so a fast-changing
// filter set doesn't accumulate workers.

interface SimResult {
  // Map<id, [x,y,z]>; absent until status === "done".
  positions: Map<string, [number, number, number]> | null;
  status: "idle" | "running" | "done" | "error";
  progress: number; // 0..1
  error: string | null;
}

export function useForceWorker(
  nodes: GraphNode[],
  edges: GraphEdge[],
  enabled: boolean,
): SimResult {
  const [status, setStatus] = useState<SimResult["status"]>("idle");
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const positionsRef = useRef<Map<string, [number, number, number]> | null>(null);

  // Stable identity key so we don't restart the worker on every re-render
  // when only React state (selection, search) changed.
  const dataKey = `${nodes.length}:${edges.length}:${nodes[0]?.id ?? ""}`;

  useEffect(() => {
    if (!enabled || nodes.length === 0) {
      setStatus("idle");
      setProgress(0);
      positionsRef.current = null;
      return;
    }

    let cancelled = false;
    setStatus("running");
    setProgress(0);
    setError(null);

    // Vite-specific worker import. The file extension matters; the URL
    // suffix `?worker` tells Vite to bundle it as a Web Worker entry.
    const worker = new Worker(
      new URL("./forceSim.worker.ts", import.meta.url),
      { type: "module" },
    );

    worker.onmessage = (e: MessageEvent) => {
      if (cancelled) return;
      const msg = e.data;
      if (msg?.type === "progress") {
        setProgress(msg.ticksDone / msg.ticksTotal);
      } else if (msg?.type === "done") {
        const map = new Map<string, [number, number, number]>();
        const ids: string[] = msg.ids;
        const pos: Float32Array = msg.positions;
        for (let i = 0; i < ids.length; i++) {
          map.set(ids[i], [pos[i * 3], pos[i * 3 + 1], pos[i * 3 + 2]]);
        }
        positionsRef.current = map;
        setStatus("done");
        setProgress(1);
      } else if (msg?.type === "error") {
        setError(msg.message ?? "worker error");
        setStatus("error");
      }
    };

    worker.onerror = (e) => {
      if (cancelled) return;
      setError(e.message ?? "worker error");
      setStatus("error");
    };

    worker.postMessage({
      type: "run",
      nodes: nodes.map((n) => ({ id: n.id })),
      links: edges.map((e) => ({ source: e.source, target: e.target })),
      ticks: 300,
      charge: -55,
      distance: 40,
    });

    return () => {
      cancelled = true;
      worker.terminate();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dataKey, enabled]);

  return {
    positions: status === "done" ? positionsRef.current : null,
    status,
    progress,
    error,
  };
}
