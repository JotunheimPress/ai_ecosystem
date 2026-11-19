#!/usr/bin/env bash
# Usage:
#   ./start.sh                      # build if needed, then start detached as comfyui-local
#   ./start.sh --lowvram            # extra flags are passed straight to main.py
#
# The container is `comfyui-local` and publishes no host port: the backend router answers
# as `comfyui:8188` on ai-net and on the host port (HOST_COMFY_PORT), and forwards here.
#
# Code ships in the image; mutable state is bind-mounted from the host ComfyUI
# checkout and selected with --base-directory /data.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# ai-stack/comfyui -> ai-stack -> the ecosystem root (projects are siblings).
ECO="${AI_ECOSYSTEM:-$(dirname "$(dirname "$SCRIPT_DIR")")}"
STATE="${COMFYUI_STATE:-$ECO/ComfyUI}"

# NVIDIA (cu130 torch) or AMD (ROCm torch for gfx1151): ai-stack/lib/host.sh.
. "${SCRIPT_DIR}/../lib/host.sh"
host_profile_load
IMAGE="$(comfy_image)"
CONTAINER_NAME="${COMFYUI_NAME:-comfyui-local}"   # override to run a second copy side-by-side
# A second copy isn't behind the router, so it needs its own host port: COMFYUI_PUBLISH=8190.
PUBLISH_ARGS=()
[ -n "${COMFYUI_PUBLISH:-}" ] && PUBLISH_ARGS=(-p "$(publish "${COMFYUI_PUBLISH}" 8188)")
NET="${COMFYUI_NET:-ai-net}"

if ! podman image exists "${IMAGE}"; then
    echo "Image not found — building from $(comfy_containerfile) (this takes a while: torch + GPU wheels)…"
    podman build --format docker -t "${IMAGE}" -f "${SCRIPT_DIR}/$(comfy_containerfile)" "${SCRIPT_DIR}"
fi

for d in models input output user custom_nodes; do
    [ -d "${STATE}/${d}" ] || { echo "missing state dir: ${STATE}/${d}" >&2; exit 1; }
done

if podman container exists "${CONTAINER_NAME}" 2>/dev/null; then
    podman rm -f "${CONTAINER_NAME}" >/dev/null
fi

podman network exists "${NET}" 2>/dev/null || podman network create "${NET}" >/dev/null
gpu_args --security-opt label=disable   # NVIDIA: exactly the two flags it always had

# AMD tuning (gfx1151, unified memory). Measured on the Halo 2026-11-02, SDXL 1024x1024
# 20 steps: ~19.4 s warm either way, cold load 24 -> 22 s with --highvram.
#   --highvram: keep models resident. "Offloading to RAM" moves them within the same
#     unified memory, so it only costs copy time. Skipped if you pass a *vram flag yourself.
#   MIOPEN_FIND_MODE=FAST: skip MIOpen's slow exhaustive kernel search on first use.
#   Not set here: TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL, which ComfyUI's main.py already
#   turns on (flash attention on gfx1151; ~16x faster attention than without it).
AMD_ENV=(); AMD_ARGS=()
case "${HOST_GPU}" in
    amd) AMD_ENV=(-e MIOPEN_FIND_MODE=FAST)
         [[ " $* " == *vram\ * ]] || AMD_ARGS=(--highvram) ;;
esac

echo "Starting ComfyUI as ${CONTAINER_NAME} (state: ${STATE}); the router serves it at http://localhost:${HOST_COMFY_PORT}"
# --security-opt label=disable: SELinux otherwise denies the container access to the
# CDI-injected /dev/nvidia* nodes ("Failed to initialize NVML: Insufficient
# Permissions", then "No CUDA GPUs are available"). This is narrower than the
# --privileged koboldcpp uses — capability confinement stays on. It also makes `:z`
# relabeling redundant, which matters: `:z` walks the whole 58 GB models tree on
# every start.
# --restart=on-failure:5 instead of --rm: podman restarts the container if it crashes
# (OOM, CUDA fault), capped so a bad config can't loop forever. `podman stop` — what
# `stack down`/`stack off` use — counts as a deliberate stop and is never restarted.
# Without --rm a dead container also keeps its logs for `stack logs` post-mortem;
# the `podman rm -f` above clears it before the next start.
podman run -d \
    --name "${CONTAINER_NAME}" \
    --restart=on-failure:5 \
    "${AMD_ENV[@]}" \
    "${GPU_ARGS[@]}" \
    --network "${NET}" \
    "${PUBLISH_ARGS[@]}" \
    -v "${STATE}/models:/data/models" \
    -v "${STATE}/input:/data/input" \
    -v "${STATE}/output:/data/output" \
    -v "${STATE}/user:/data/user" \
    -v "${STATE}/custom_nodes:/data/custom_nodes" \
    "${IMAGE}" \
    --listen 0.0.0.0 \
    --port 8188 \
    --base-directory /data \
    "${AMD_ARGS[@]}" \
    "$@"

echo "==> ComfyUI started (detached, ${NET}). Logs: podman logs -f ${CONTAINER_NAME}"
