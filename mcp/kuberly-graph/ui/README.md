# kuberly-platform-ui

Standalone SPA for the kuberly-platform live multi-layer knowledge graph. Replaces the vanilla
`dashboard/static/` HTML+JS bundle that was glued onto the FastMCP Starlette app.

```
ui/
├── src/
│   ├── api/           — typed HTTP client for /api/v1/*
│   ├── components/    — Header, ArchGrid, KPIRow, OverlaysStrip, NodeSpotlight, GraphSidebar
│   ├── tabs/          — DashboardTab, GraphTab
│   ├── store/         — Zustand UI store (selected node, filters, group-by)
│   └── lib/           — categories, useElementSize
├── helm/kuberly-graph-ui/
├── Dockerfile
├── nginx.conf.template
└── entrypoint.sh
```

## Stack

- **Node 24** (latest LTS line in 2026) — required at build time; `engines` in
  `package.json` enforces it.
- **React 18 + Vite 6** — fast HMR, ES2022 build target.
- **TypeScript Go-based compiler** — `@typescript/native-preview` (`tsgo`),
  the new TypeScript compiler written in Go. Roughly 10× faster than the
  classic `tsc` on large projects. Still beta; the classic `tsc` is kept as a
  devDep + a `typecheck:tsc` fallback script in case `tsgo` regresses on a
  new TS feature we use.
- **Tailwind CSS** — atomic styling for the data-dense Dashboard tab.
- **TanStack Query v5** — caching layer over the 16 `/api/v1/*` endpoints.
- **Zustand** — UI state (active tab, selected node, category filters, group-by).
- **react-force-graph-3d + three** — 3D force layout with built-in particle flows on edges.
  UnrealBloomPass adds the glow look. Default mode handles up to ~10k nodes smoothly; big-graph
  fallback (Cosmos 2D) deferred.

## Why a separate project?

Until v0.52.0 the dashboard was 922 lines of vanilla JS inlined into the Python package. Pain points:

1. No type safety against the rapidly-changing API shape.
2. The 3D graph silently failed to render when the tab started hidden — `ForceGraph3D` mounted into a 0×0 div, and the `ResizeObserver` hook fired *after* the WebGL viewport latched onto the bad dims. The React rewrite uses a `useElementSize` hook + conditional render so the canvas only mounts once the container has real dimensions.
3. Adding new tabs / panels meant editing one 900-line file.
4. No code-splitting — the 3D bundle loaded for users that only wanted the Dashboard.

## Local dev

```bash
cd mcp/kuberly-graph/ui
npm install
npm run dev          # Vite on :5173, proxies /api → 127.0.0.1:8000
```

In another shell, run the kuberly-platform MCP server in streamable-http mode:

```bash
cd mcp/kuberly-graph
.venv/bin/kuberly-platform serve --transport streamable-http --host 127.0.0.1 --port 8000
```

The Vite dev server proxies `/api/*` to that backend, so the SPA talks to it as if same-origin.

### Pointing at a different backend

```bash
VITE_API_BASE=http://other-graph.internal:8000 npm run dev
```

## Production build

```bash
npm run build       # outputs to dist/, ready to drop into nginx
docker build -t kuberly-graph-ui:dev .
docker run --rm -p 8080:8080 \
    -e KUBERLY_GRAPH_BACKEND=http://kuberly-platform:8000 \
    kuberly-graph-ui:dev
```

The `entrypoint.sh` runs `envsubst` over `nginx.conf.template` at container start so the same image works against any backend URL.

## Helm

```bash
helm install graph-ui ./helm/kuberly-graph-ui \
    --set image.tag=main-<sha> \
    --set backend.url=http://kuberly-platform.monitoring.svc.cluster.local:8000
```

The chart ships a Deployment + Service + (optional) Ingress. Default Service is `ClusterIP` on port 80; flip `ingress.enabled=true` and pass `ingress.hosts` to expose externally.

## CORS

`/api/v1/*` responses on the kuberly-platform backend always carry CORS headers. The default is `Access-Control-Allow-Origin: *`; set `DASHBOARD_CORS_ORIGINS=https://graph.example.com,http://localhost:5173` on the backend to lock it down to specific origins.

## Endpoints consumed

The full list mirrors `dashboard/api.py`:

| Tool / Component | Endpoint(s) |
|---|---|
| Header counter | `/api/v1/stats` |
| Overlays strip (Dashboard) | `/api/v1/layers` |
| KPI cards (Dashboard) | `/api/v1/meta-overview`, `/api/v1/aws-services` |
| AWS architecture grid (Dashboard) | `/api/v1/aws-services` |
| Tile detail (Dashboard) | `/api/v1/graph?type=...` |
| Node spotlight search | `/api/v1/search`, `/api/v1/nodes/{id}`, `/api/v1/nodes/{id}/neighbors` |
| Graph tab | `/api/v1/graph` (cursor-paginated) |
| Graph sidebar | `/api/v1/nodes/{id}`, `/api/v1/nodes/{id}/neighbors` |

Compliance and Stack Overview tabs from the legacy dashboard are deferred — same endpoints exist (`/api/v1/compliance`, `/api/v1/meta-overview`) and can be wired up in a follow-up PR.

## Migrating off the legacy dashboard

While both UIs coexist, nginx proxies `/dashboard*` to the backend so the old static dashboard keeps working. Once this rewrite is paritied, drop `dashboard/static/` and the corresponding routes from `dashboard/routes.py`.
