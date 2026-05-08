/// <reference lib="webworker" />
//
// Off-main-thread d3-force-3d simulation.
//
// Why this exists: react-force-graph-3d's built-in simulation runs on the
// main thread, so for graphs above a few thousand nodes the cooldown phase
// jank-locks the UI for several seconds. This worker takes the same
// {nodes, links} payload, runs the simulation to a stable state in batch
// mode, and posts back final positions as a single Float32Array. The main
// thread can then pin those positions on the force-graph nodes and render
// statically (no internal physics needed).
//
// Protocol:
//   <- { type: "run", nodes: Node[], links: Link[], ticks?, charge?, distance? }
//   -> { type: "progress", ticksDone: number, ticksTotal: number }
//   -> { type: "done", ids: string[], positions: Float32Array (transferred) }
//   -> { type: "error", message: string }
//
// Positions layout: positions[i*3+0]=x, positions[i*3+1]=y, positions[i*3+2]=z
// matched index-wise with `ids`.

import {
  forceCenter,
  forceLink,
  forceManyBody,
  forceSimulation,
  type SimulationLinkDatum,
  type SimulationNodeDatum,
} from "d3-force-3d";

interface Node extends SimulationNodeDatum {
  id: string;
}

interface Link {
  source: string;
  target: string;
}

interface RunMessage {
  type: "run";
  nodes: Node[];
  links: Link[];
  ticks?: number;
  charge?: number;
  distance?: number;
}

const ctx = self as unknown as DedicatedWorkerGlobalScope;

ctx.addEventListener("message", (e: MessageEvent<RunMessage>) => {
  const msg = e.data;
  if (!msg || msg.type !== "run") return;
  try {
    runSim(msg);
  } catch (err) {
    ctx.postMessage({
      type: "error",
      message: err instanceof Error ? err.message : String(err),
    });
  }
});

function runSim(msg: RunMessage): void {
  const ticks = Math.max(1, msg.ticks ?? 300);
  const charge = msg.charge ?? -55;
  const distance = msg.distance ?? 40;

  // d3-force mutates node objects in place, adding x/y/z. Make a copy so we
  // don't keep references to the postMessage'd structured-clone payload.
  const nodes: Node[] = msg.nodes.map((n) => ({ ...n }));
  const indexById = new Map(nodes.map((n, i) => [n.id, i]));
  const links = msg.links.filter(
    (l) => indexById.has(l.source) && indexById.has(l.target),
  ) as SimulationLinkDatum<Node>[];

  const sim = forceSimulation(nodes, 3)
    .force(
      "charge",
      forceManyBody().strength(charge),
    )
    .force(
      "link",
      forceLink(links)
        .id((d: SimulationNodeDatum) => (d as Node).id)
        .distance(distance),
    )
    .force("center", forceCenter())
    .stop();

  // Run synchronously. Post progress every ~30 ticks so the main thread can
  // show a progress bar without flooding the message channel.
  const progressEvery = Math.max(1, Math.floor(ticks / 10));
  for (let i = 0; i < ticks; i++) {
    sim.tick();
    if (i % progressEvery === 0 || i === ticks - 1) {
      ctx.postMessage({ type: "progress", ticksDone: i + 1, ticksTotal: ticks });
    }
  }

  const ids = nodes.map((n) => n.id);
  const positions = new Float32Array(nodes.length * 3);
  for (let i = 0; i < nodes.length; i++) {
    const n = nodes[i] as Node & { x?: number; y?: number; z?: number };
    positions[i * 3] = n.x ?? 0;
    positions[i * 3 + 1] = n.y ?? 0;
    positions[i * 3 + 2] = n.z ?? 0;
  }

  ctx.postMessage(
    { type: "done", ids, positions },
    { transfer: [positions.buffer] },
  );
}
