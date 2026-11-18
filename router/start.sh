#!/usr/bin/env bash
# Backend router: one nginx container that answers as `koboldcpp` (:5001) on ai-net and
# forwards to whichever copy is serving. See router.conf.template and HALO_PLAN.md.
#
# Usage:
#   ./start.sh        (re)start the router; keeps the current route (`stack route` sets it)
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

if podman container exists "${CONTAINER_NAME}" 2>/dev/null; then
    podman rm -f "${CONTAINER_NAME}" >/dev/null
fi
podman network exists "${NET}" 2>/dev/null || podman network create "${NET}" >/dev/null

echo "Starting backend router (text :5001 → $(grep -oP 'kobold_up \K[^;]+' "${ROUTE}"))"
# The host port matches what koboldcpp published before the router, so nothing on the host
# notices.
# NGINX_ENTRYPOINT_LOCAL_RESOLVERS: the image reads /etc/resolv.conf (aardvark-dns on
# ai-net) into ${NGINX_LOCAL_RESOLVERS} for the template.
podman run -d \
    --name "${CONTAINER_NAME}" \
    --restart=on-failure:5 \
    --network "${NET}" \
    --network-alias koboldcpp \
    -p "$(publish 5001 5001)" \
    -e NGINX_ENTRYPOINT_LOCAL_RESOLVERS=1 \
    -v "${SCRIPT_DIR}/router.conf.template:/etc/nginx/templates/router.conf.template:ro,z" \
    -v "${STATE}:/etc/nginx/route:ro,z" \
    --health-cmd "wget -qO- http://127.0.0.1:5001/__router/health >/dev/null || exit 1" \
    --health-interval 30s --health-timeout 5s --health-start-period 5s --health-retries 3 \
    "${IMAGE}" >/dev/null

for _ in $(seq 1 20); do
    curl -sf -m 2 -o /dev/null "http://localhost:5001/__router/health" && {
        echo "==> Backend router up. Logs: podman logs -f ${CONTAINER_NAME}"; exit 0; }
    sleep 0.5
done
echo "Backend router did not answer on :5001. Logs:" >&2
podman logs --tail 15 "${CONTAINER_NAME}" 2>&1 | sed 's/^/    /' >&2
exit 1
