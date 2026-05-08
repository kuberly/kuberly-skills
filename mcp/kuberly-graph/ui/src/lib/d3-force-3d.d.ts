// Minimal type stub for d3-force-3d. The package ships ESM source but no
// types; we only use a small slice (forceSimulation/Link/ManyBody/Center)
// and the `tick()` / `force()` chain methods, so a hand-written stub is
// cheaper than pulling in a separate @types package (which doesn't ship
// for d3-force-3d anyway).

declare module "d3-force-3d" {
  export interface SimulationNodeDatum {
    index?: number;
    x?: number;
    y?: number;
    z?: number;
    vx?: number;
    vy?: number;
    vz?: number;
    fx?: number | null;
    fy?: number | null;
    fz?: number | null;
  }

  export interface SimulationLinkDatum<N extends SimulationNodeDatum> {
    source: string | number | N;
    target: string | number | N;
    index?: number;
  }

  export interface Force<N extends SimulationNodeDatum, _L> {
    (alpha: number): void;
    initialize?(nodes: N[], random: () => number): void;
  }

  export interface Simulation<N extends SimulationNodeDatum, L extends SimulationLinkDatum<N>> {
    tick(iterations?: number): this;
    stop(): this;
    restart(): this;
    nodes(): N[];
    nodes(nodes: N[]): this;
    force(name: string): Force<N, L> | undefined;
    force(name: string, force: Force<N, L> | null): this;
    alpha(): number;
    alpha(alpha: number): this;
    alphaDecay(): number;
    alphaDecay(decay: number): this;
  }

  export function forceSimulation<N extends SimulationNodeDatum, L extends SimulationLinkDatum<N>>(
    nodes?: N[],
    numDimensions?: 1 | 2 | 3,
  ): Simulation<N, L>;

  export interface LinkForce<N extends SimulationNodeDatum, L extends SimulationLinkDatum<N>>
    extends Force<N, L> {
    links(): L[];
    links(links: L[]): this;
    id(accessor: (d: N) => string | number): this;
    distance(d: number | ((link: L) => number)): this;
    strength(s: number | ((link: L) => number)): this;
  }
  export function forceLink<N extends SimulationNodeDatum, L extends SimulationLinkDatum<N>>(
    links?: L[],
  ): LinkForce<N, L>;

  export interface ManyBodyForce<N extends SimulationNodeDatum> extends Force<N, never> {
    strength(s: number | ((d: N) => number)): this;
    distanceMin(d: number): this;
    distanceMax(d: number): this;
  }
  export function forceManyBody<N extends SimulationNodeDatum>(): ManyBodyForce<N>;

  export interface CenterForce<N extends SimulationNodeDatum> extends Force<N, never> {
    x(x: number): this;
    y(y: number): this;
    z(z: number): this;
    strength(s: number): this;
  }
  export function forceCenter<N extends SimulationNodeDatum>(
    x?: number,
    y?: number,
    z?: number,
  ): CenterForce<N>;
}
