#!/usr/bin/env bash
# Usage:
#   ./start.sh                              # launches the web UI on :5001
#   ./start.sh --model /models/foo.gguf    # load a specific model at start
#   ./start.sh --help                       # show KoboldCPP CLI options
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODELS_DIR="${SCRIPT_DIR}/models"
IMAGE="localhost/koboldcpp:latest"
CONTAINER_NAME="koboldcpp"

# Build image if it doesn't exist yet
if ! podman image exists "${IMAGE}"; then
    echo "Image not found — building from Containerfile..."
    podman build -t "${IMAGE}" "${SCRIPT_DIR}"
fi

# Remove any stopped container with the same name
if podman container exists "${CONTAINER_NAME}" 2>/dev/null; then
    podman rm -f "${CONTAINER_NAME}" >/dev/null
fi

MODEL="${KOBOLDCPP_MODEL:-Qwen3.5-9B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf}"
GPU_LAYERS="${KOBOLDCPP_GPU_LAYERS:-33}"

echo "Starting KoboldCPP at http://localhost:5001 (model: ${MODEL}, gpu layers: ${GPU_LAYERS})"
podman run -d \
    --name "${CONTAINER_NAME}" \
    --rm \
    --privileged \
    --device nvidia.com/gpu=all \
    --network ai-net \
    -p 5001:5001 \
    -v "${MODELS_DIR}:/models:z" \
    -v "${SCRIPT_DIR}/chatml.json:/chatml.json:z" \
    "${IMAGE}" \
    --host 0.0.0.0 \
    --port 5001 \
    --usecublas \
    --gpulayers "${GPU_LAYERS}" \
    --model "/models/${MODEL}" \
    --chatcompletionsadapter /chatml.json \
    "$@"

echo "==> KoboldCPP started (detached, ai-net). Logs: podman logs -f ${CONTAINER_NAME}"
