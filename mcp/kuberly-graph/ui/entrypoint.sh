#!/bin/sh
# Template the upstream backend URL into nginx.conf at container start.
# Runs from /docker-entrypoint.d/ before nginx itself starts (see
# nginx:alpine's stock entrypoint chain).
set -eu

: "${KUBERLY_GRAPH_BACKEND:?KUBERLY_GRAPH_BACKEND must be set}"

envsubst '${KUBERLY_GRAPH_BACKEND}' \
    < /etc/nginx/templates/default.conf.template \
    > /etc/nginx/conf.d/default.conf
