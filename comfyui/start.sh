#!/usr/bin/env bash
# Usage:
#   ./start.sh                      # build if needed, then start detached on :8188
#   ./start.sh --lowvram            # extra flags are passed straight to main.py
#
# Code ships in the image; mutable state is bind-mounted from the host ComfyUI
# checkout and selected with --base-directory /data.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# ai-stack/comfyui -> ai-stack -> the ecosystem root (projects are siblings).
ECO="${AI_ECOSYSTEM:-$(dirname "$(dirname "$SCRIPT_DIR")")}"
STATE="${COMFYUI_STATE:-$ECO/ComfyUI}"

IMAGE="localhost/comfyui:latest"
CONTAINER_NAME="${COMFYUI_NAME:-comfyui}"   # override to run a second copy side-by-side
PORT="${COMFYUI_PORT:-8188}"
NET="${COMFYUI_NET:-ai-net}"

if ! podman image exists "${IMAGE}"; then
    echo "Image not found — building from Containerfile (this takes a while: torch + CUDA wheels)…"
    podman build --format docker -t "${IMAGE}" "${SCRIPT_DIR}"
fi

for d in models input output user custom_nodes; do
    [ -d "${STATE}/${d}" ] || { echo "missing state dir: ${STATE}/${d}" >&2; exit 1; }
done

if podman container exists "${CONTAINER_NAME}" 2>/dev/null; then
    podman rm -f "${CONTAINER_NAME}" >/dev/null
fi

podman network exists "${NET}" 2>/dev/null || podman network create "${NET}" >/dev/null

echo "Starting ComfyUI at http://localhost:${PORT} (state: ${STATE})"
# --security-opt label=disable: SELinux otherwise denies the container access to the
# CDI-injected /dev/nvidia* nodes ("Failed to initialize NVML: Insufficient
# Permissions", then "No CUDA GPUs are available"). This is narrower than the
# --privileged koboldcpp uses — capability confinement stays on. It also makes `:z`
# relabeling redundant, which matters: `:z` walks the whole 58 GB models tree on
# every start.
podman run -d \
    --name "${CONTAINER_NAME}" \
    --rm \
    --device nvidia.com/gpu=all \
    --security-opt label=disable \
    --network "${NET}" \
    -p "${PORT}:8188" \
    -v "${STATE}/models:/data/models" \
    -v "${STATE}/input:/data/input" \
    -v "${STATE}/output:/data/output" \
    -v "${STATE}/user:/data/user" \
    -v "${STATE}/custom_nodes:/data/custom_nodes" \
    "${IMAGE}" \
    --listen 0.0.0.0 \
    --port 8188 \
    --base-directory /data \
    "$@"

echo "==> ComfyUI started (detached, ${NET}). Logs: podman logs -f ${CONTAINER_NAME}"
