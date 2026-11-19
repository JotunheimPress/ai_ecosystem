#!/usr/bin/env bash
# Backend router: one nginx container that answers as `koboldcpp` (:5001) on ai-net and
# forwards to whichever copy is serving. See router.conf.template and HALO_PLAN.md.
#
# Usage:
#   ./start.sh          (re)create the router; keeps the current route (`stack route` sets it)
#   ./start.sh --check  exit 0 if the running router was created from the current config,
#                       1 if it is stale or not running. Changes nothing. (`stack` uses it to
#                       recreate a router whose template, image or ports have changed.)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# ai-stack/router -> ai-stack -> the ecosystem root (projects are siblings).
ECO="${AI_ECOSYSTEM:-$(dirname "$(dirname "$SCRIPT_DIR")")}"
STATE="${ROUTER_STATE:-$ECO/router}"         # route.conf lives here, never committed
IMAGE="docker.io/library/nginx:1.28-alpine"
CONTAINER_NAME="backend-router"
NET="ai-net"
# BIND from ai-stack/host.env: the AMD box publishes :5001 on loopback only (HALO_PLAN.md).
. "${SCRIPT_DIR}/../lib/host.sh"
host_profile_load

TEMPLATE="${SCRIPT_DIR}/router.conf.template"
# Everything the container is created from, except the name and its config label. The
# template is in it because nginx renders it once at start; route.conf is not, because
# `stack route` reloads that into the running router.
RUN_ARGS=(
    --restart=on-failure:5
    --network "${NET}"
    --network-alias koboldcpp
    -p "$(publish 5001 5001)"
    -e NGINX_ENTRYPOINT_LOCAL_RESOLVERS=1
    -v "${TEMPLATE}:/etc/nginx/templates/router.conf.template:ro,z"
    -v "${STATE}:/etc/nginx/route:ro,z"
    --health-cmd "wget -qO- http://127.0.0.1:5001/__router/health >/dev/null || exit 1"
    --health-interval 30s --health-timeout 5s --health-start-period 5s --health-retries 3
    "${IMAGE}"
)
CONFIG_HASH="$({ printf '%s\n' "${RUN_ARGS[@]}"; cat "${TEMPLATE}"; } | sha256sum | cut -c1-16)"

if [ "${1:-}" = --check ]; then
    have="$(podman inspect -f '{{.State.Status}} {{index .Config.Labels "ai-stack.config"}}' "${CONTAINER_NAME}" 2>/dev/null || true)"
    if [ "${have}" = "running ${CONFIG_HASH}" ]; then exit 0; fi
    exit 1
fi

mkdir -p "${STATE}"
ROUTE="${STATE}/route.conf"
if [ ! -f "${ROUTE}" ]; then
    # First start: route to the local copy. Same text `stack route local` writes.
    printf 'set $kobold_up koboldcpp-local:5001;\n' > "${ROUTE}"
elif grep -q 'orpheus_up' "${ROUTE}"; then
    # Written before Orpheus TTS was removed: keep the text route, drop the voice line.
    grep '^set \$kobold_up ' "${ROUTE}" > "${ROUTE}.tmp" && mv "${ROUTE}.tmp" "${ROUTE}"
fi

# Containers from earlier layouts: `koboldcpp` predates the router and, left running, would
# hold host port 5001 and share the router's DNS name on ai-net. The other two are the
# removed Orpheus TTS backend. All are ours (koboldcpp image), not user data; its model
# files in $ECO/orpheus-tts are left alone.
for legacy in koboldcpp orpheus-tts orpheus-tts-local; do
    if podman container exists "${legacy}" 2>/dev/null; then
        echo "Removing old container '${legacy}'"
        podman stop -t 10 "${legacy}" >/dev/null 2>&1 || true
        podman rm -f "${legacy}" >/dev/null
    fi
done

# Port check BEFORE the old router is removed: if something else holds 5001, removing it
# would leave text with no router at all. A running router holds the port itself.
running="$(podman inspect -f '{{.State.Running}}' "${CONTAINER_NAME}" 2>/dev/null || echo false)"
if [ "${running}" != true ] && command -v ss >/dev/null 2>&1 \
   && ss -tlnH 'sport = :5001' 2>/dev/null | grep -q .; then
    echo "Port 5001 is in use by something other than ${CONTAINER_NAME}; the router was left as it is." >&2
    ss -tlnHp 'sport = :5001' 2>/dev/null | sed 's/^/    /' >&2
    exit 1
fi

if podman container exists "${CONTAINER_NAME}" 2>/dev/null; then
    podman rm -f "${CONTAINER_NAME}" >/dev/null
fi
podman network exists "${NET}" 2>/dev/null || podman network create "${NET}" >/dev/null

echo "Starting backend router (text :5001 → $(grep -oP 'kobold_up \K[^;]+' "${ROUTE}"))"
# The host port matches what koboldcpp published before the router, so nothing on the host
# notices.
# NGINX_ENTRYPOINT_LOCAL_RESOLVERS: the image reads /etc/resolv.conf (aardvark-dns on
# ai-net) into ${NGINX_LOCAL_RESOLVERS} for the template.
podman run -d --name "${CONTAINER_NAME}" --label "ai-stack.config=${CONFIG_HASH}" \
    "${RUN_ARGS[@]}" >/dev/null

for _ in $(seq 1 20); do
    curl -sf -m 2 -o /dev/null "http://localhost:5001/__router/health" && {
        echo "==> Backend router up. Logs: podman logs -f ${CONTAINER_NAME}"; exit 0; }
    sleep 0.5
done
echo "Backend router did not answer on :5001. Logs:" >&2
podman logs --tail 15 "${CONTAINER_NAME}" 2>&1 | sed 's/^/    /' >&2
exit 1
