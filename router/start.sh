#!/usr/bin/env bash
# Backend router: one nginx container that answers as `koboldcpp` (:5001) and `orpheus-tts`
# (:5005) on ai-net and forwards to whichever copy is serving. See router.conf.template and
# HALO_PLAN.md.
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

mkdir -p "${STATE}"
ROUTE="${STATE}/route.conf"
if [ ! -f "${ROUTE}" ]; then
    # First start: route to the local 4070 copies. Same text `stack route local` writes.
    printf 'set $kobold_up koboldcpp-local:5001;\nset $orpheus_up orpheus-tts-local:5005;\n' > "${ROUTE}"
fi

# Before the router, these containers WERE `koboldcpp` and `orpheus-tts`. Left running,
# they'd hold host ports 5001/5005 and share the router's DNS names on ai-net, so name
# lookups would answer with either one. They're ours (koboldcpp image), not user data.
for legacy in koboldcpp orpheus-tts; do
    if podman container exists "${legacy}" 2>/dev/null; then
        echo "Removing pre-router container '${legacy}' (now ${legacy}-local behind the router)"
        podman stop -t 10 "${legacy}" >/dev/null 2>&1 || true
        podman rm -f "${legacy}" >/dev/null
    fi
done

if podman container exists "${CONTAINER_NAME}" 2>/dev/null; then
    podman rm -f "${CONTAINER_NAME}" >/dev/null
fi
podman network exists "${NET}" 2>/dev/null || podman network create "${NET}" >/dev/null

echo "Starting backend router (text :5001, voice :5005 → $(grep -oP 'kobold_up \K[^;]+' "${ROUTE}"))"
# Host ports match what the backends published before the router, so nothing on the host
# notices: 5001 on all interfaces as koboldcpp had it, 5005 loopback-only because Orpheus's
# admin API has no password.
# NGINX_ENTRYPOINT_LOCAL_RESOLVERS: the image reads /etc/resolv.conf (aardvark-dns on
# ai-net) into ${NGINX_LOCAL_RESOLVERS} for the template.
podman run -d \
    --name "${CONTAINER_NAME}" \
    --restart=on-failure:5 \
    --network "${NET}" \
    --network-alias koboldcpp \
    --network-alias orpheus-tts \
    -p 5001:5001 \
    -p 127.0.0.1:5005:5005 \
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
