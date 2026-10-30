#!/usr/bin/env bash
# Orpheus TTS (Canopy Labs, 3B) for Pantheon's voice — served by KoboldCPP, loaded on demand.
#
# Usage:
#   ./start.sh            start the (idle) server on ai-net as `orpheus-tts-local`; the backend
#                         router answers as `orpheus-tts` / localhost:5005 and forwards here
#
# The server starts with NO model loaded and costs no VRAM. Pantheon loads the model when
# a voice is switched on (POST /api/admin/reload_config {"filename":"orpheus.kcpps"}) and
# unloads it when voice is switched off ({"filename":"unload_model"}). KoboldCPP also
# unloads it on its own after ORPHEUS_IDLE_UNLOAD seconds without a request, so a closed
# browser can't leave ~3 GB pinned on the GPU.
#
# Orpheus only emits audio codes; Pantheon turns them into sound with the SNAC decoder
# (services/orpheus.py). Reuses the koboldcpp image — there is no image of its own.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# ai-stack/orpheus-tts -> ai-stack -> the ecosystem root (projects are siblings).
ECO="${AI_ECOSYSTEM:-$(dirname "$(dirname "$SCRIPT_DIR")")}"
STATE="${ORPHEUS_STATE:-$ECO/orpheus-tts}"     # models/ + config/, never committed
MODELS_DIR="${STATE}/models"
CONFIG_DIR="${STATE}/config"

# Same image and GPU runtime as the chat model (ai-stack/lib/host.sh).
. "${SCRIPT_DIR}/../lib/host.sh"
host_profile_load
IMAGE="$(kobold_image)"
CONTAINER_NAME="orpheus-tts-local"     # `orpheus-tts` is the router's name now
PORT="${ORPHEUS_PORT:-5005}"
NET="ai-net"
MODEL="${ORPHEUS_MODEL:-orpheus-3b-0.1-ft-q4_k_m.gguf}"
IDLE_UNLOAD="${ORPHEUS_IDLE_UNLOAD:-600}"

if ! podman image exists "${IMAGE}"; then
    echo "${IMAGE} not found (${HOST_LLM_BACKEND}) — run: stack install koboldcpp" >&2
    exit 1
fi
if [ ! -f "${MODELS_DIR}/${MODEL}" ]; then
    echo "Orpheus model not found: ${MODELS_DIR}/${MODEL}" >&2
    echo "Download it (2.4 GB):" >&2
    echo "  mkdir -p ${MODELS_DIR} && curl -fL -o ${MODELS_DIR}/${MODEL} \\" >&2
    echo "    https://huggingface.co/isaiahbjork/orpheus-3b-0.1-ft-Q4_K_M-GGUF/resolve/main/orpheus-3b-0.1-ft-q4_k_m.gguf" >&2
    exit 1
fi
mkdir -p "${CONFIG_DIR}"

# The load recipe the admin API swaps in. Exported by KoboldCPP itself rather than
# hand-written, so the .kcpps always matches what this KoboldCPP version expects.
# Regenerated whenever the model or the GPU runtime changes (the recipe names the
# runtime: --usecublas vs --usevulkan). Context 4096 is plenty: one sentence of speech
# is a few hundred audio tokens.
KCPPS="${CONFIG_DIR}/orpheus.kcpps"
STAMP="${CONFIG_DIR}/.model"
WANT="${MODEL} ${HOST_LLM_BACKEND}"
if [ ! -f "${KCPPS}" ] || [ "$(cat "${STAMP}" 2>/dev/null)" != "${WANT}" ]; then
    podman run --rm -v "${MODELS_DIR}:/models:z" -v "${CONFIG_DIR}:/config:z" "${IMAGE}" \
        "$(kobold_gpu_flag)" --gpulayers 99 --contextsize 4096 \
        --model "/models/${MODEL}" --exportconfig /config/orpheus.kcpps >/dev/null
    echo "${WANT}" > "${STAMP}"
fi

# Also the pre-router `orpheus-tts` container, which would still hold :5005.
for c in "${CONTAINER_NAME}" orpheus-tts; do
    if podman container exists "${c}" 2>/dev/null; then podman rm -f "${c}" >/dev/null; fi
done
podman network exists "${NET}" 2>/dev/null || podman network create "${NET}" >/dev/null
gpu_args --privileged     # NVIDIA: --device nvidia.com/gpu=all --privileged, as always

echo "Starting Orpheus TTS at http://localhost:${PORT} (idle; loads ${MODEL} on demand, unloads after ${IDLE_UNLOAD}s idle)"
# Same restart policy and GPU flags as koboldcpp (see ai-stack/CLAUDE.md). Its own health
# check: the image's probes :5001 (the chat server), which marked this one unhealthy. No host
# port: the router publishes localhost:5005 (loopback only — the admin API has no password)
# and Pantheon reaches it over ai-net through the router.
podman run -d \
    --name "${CONTAINER_NAME}" \
    --restart=on-failure:5 \
    "${GPU_ARGS[@]}" \
    --network "${NET}" \
    -v "${MODELS_DIR}:/models:z" \
    -v "${CONFIG_DIR}:/config:z" \
    --health-cmd "curl -fs http://localhost:${PORT}/api/v1/model || exit 1" \
    --health-interval 30s --health-timeout 10s --health-start-period 30s --health-retries 3 \
    "${IMAGE}" \
    --host 0.0.0.0 \
    --port "${PORT}" \
    --admin --admindir /config --nomodel \
    --adminunloadtimeout "${IDLE_UNLOAD}" \
    "$@" >/dev/null

for _ in $(seq 1 30); do
    podman exec "${CONTAINER_NAME}" curl -sf -m 2 -o /dev/null "http://localhost:${PORT}/api/v1/model" 2>/dev/null && {
        echo "==> Orpheus TTS up (idle). Logs: podman logs -f ${CONTAINER_NAME}"; exit 0; }
    sleep 1
done
echo "Orpheus TTS did not answer on :${PORT}. Logs:" >&2
podman logs --tail 15 "${CONTAINER_NAME}" 2>&1 | sed 's/^/    /' >&2
exit 1
