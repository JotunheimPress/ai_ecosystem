#!/usr/bin/env bash
# Orpheus TTS (Canopy Labs, 3B) for Pantheon's voice — served by KoboldCPP, loaded on demand.
#
# Usage:
#   ./start.sh            start the (idle) server on :5005, on ai-net as `orpheus-tts`
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

IMAGE="localhost/koboldcpp:latest"
CONTAINER_NAME="orpheus-tts"
PORT="${ORPHEUS_PORT:-5005}"
NET="ai-net"
MODEL="${ORPHEUS_MODEL:-orpheus-3b-0.1-ft-q4_k_m.gguf}"
IDLE_UNLOAD="${ORPHEUS_IDLE_UNLOAD:-600}"

if ! podman image exists "${IMAGE}"; then
    echo "${IMAGE} not found — run: stack install koboldcpp" >&2
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
# Regenerated whenever the model changes. Context 4096 is plenty: one sentence of
# speech is a few hundred audio tokens.
KCPPS="${CONFIG_DIR}/orpheus.kcpps"
STAMP="${CONFIG_DIR}/.model"
if [ ! -f "${KCPPS}" ] || [ "$(cat "${STAMP}" 2>/dev/null)" != "${MODEL}" ]; then
    podman run --rm -v "${MODELS_DIR}:/models:z" -v "${CONFIG_DIR}:/config:z" "${IMAGE}" \
        --usecublas --gpulayers 99 --contextsize 4096 \
        --model "/models/${MODEL}" --exportconfig /config/orpheus.kcpps >/dev/null
    echo "${MODEL}" > "${STAMP}"
fi

if podman container exists "${CONTAINER_NAME}" 2>/dev/null; then
    podman rm -f "${CONTAINER_NAME}" >/dev/null
fi
podman network exists "${NET}" 2>/dev/null || podman network create "${NET}" >/dev/null

echo "Starting Orpheus TTS at http://localhost:${PORT} (idle; loads ${MODEL} on demand, unloads after ${IDLE_UNLOAD}s idle)"
# Same restart policy and GPU flags as koboldcpp (see ai-stack/CLAUDE.md). Published on
# loopback only: the admin API has no password, and only Pantheon (over ai-net) and
# koboldcpp/start.sh (to free VRAM before a model swap) need it.
podman run -d \
    --name "${CONTAINER_NAME}" \
    --restart=on-failure:5 \
    --privileged \
    --device nvidia.com/gpu=all \
    --network "${NET}" \
    -p "127.0.0.1:${PORT}:${PORT}" \
    -v "${MODELS_DIR}:/models:z" \
    -v "${CONFIG_DIR}:/config:z" \
    "${IMAGE}" \
    --host 0.0.0.0 \
    --port "${PORT}" \
    --admin --admindir /config --nomodel \
    --adminunloadtimeout "${IDLE_UNLOAD}" \
    "$@" >/dev/null

for _ in $(seq 1 30); do
    curl -sf -m 2 -o /dev/null "http://localhost:${PORT}/api/v1/model" && {
        echo "==> Orpheus TTS up (idle). Logs: podman logs -f ${CONTAINER_NAME}"; exit 0; }
    sleep 1
done
echo "Orpheus TTS did not answer on :${PORT}. Logs:" >&2
podman logs --tail 15 "${CONTAINER_NAME}" 2>&1 | sed 's/^/    /' >&2
exit 1
