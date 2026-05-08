# Multi-stage build for the kuberly-graph SPA.
#
# Stage 1: install deps + Vite build → /app/dist (static).
# Stage 2: nginx alpine serves dist/, proxies /api/* and /dashboard to the
#          kuberly-graph MCP server (configurable at deploy time via the
#          KUBERLY_GRAPH_BACKEND env var consumed by entrypoint.sh).

# ---------- Stage 1: build ----------
# Node 24 (latest LTS line in 2026). Pin to the alpine image for a small build
# context; we only need npm + the JS toolchain, no native modules.
FROM public.ecr.aws/docker/library/node:24-alpine AS build

WORKDIR /app

# Install only manifests first so layer cache survives source-only edits.
COPY package.json package-lock.json* ./
RUN npm ci || npm install

# Copy source + config and build.
COPY . ./
RUN npm run build

# ---------- Stage 2: serve ----------
FROM public.ecr.aws/docker/library/nginx:1.27-alpine AS serve

# nginx needs envsubst (from gettext) to template the upstream into nginx.conf
# at container start. Alpine ships gettext in `gettext` package.
RUN apk add --no-cache gettext

COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY entrypoint.sh /docker-entrypoint.d/40-template-backend.sh
RUN chmod +x /docker-entrypoint.d/40-template-backend.sh

# Vite output
COPY --from=build /app/dist /usr/share/nginx/html

# In-cluster default points at the Service DNS for kuberly-graph; override
# in the Deployment env when deploying elsewhere.
ENV KUBERLY_GRAPH_BACKEND=http://kuberly-graph.monitoring.svc.cluster.local:8000

EXPOSE 8080

# nginx:alpine's stock entrypoint already iterates /docker-entrypoint.d/
# scripts before launching nginx -g 'daemon off;'.
