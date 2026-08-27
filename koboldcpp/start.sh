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
    podman build --format docker -t "${IMAGE}" "${SCRIPT_DIR}"
fi

# Remove any stopped container with the same name
if podman container exists "${CONTAINER_NAME}" 2>/dev/null; then
    podman rm -f "${CONTAINER_NAME}" >/dev/null
fi

# Persisted model selection: models/model.env's sibling, written by tooling (and by
# hand) so a chosen model survives `stack up`, a restart, and a reboot. Without it the
# selection lived only in the caller's environment, which the systemd unit and `stack`
# don't set — so the hardcoded default below was the *only* model the stack could boot.
#
# Parsed strictly, never sourced: this file gets written from model names that came off
# the internet, and `.` would execute whatever ended up in it.
MODEL_ENV="${SCRIPT_DIR}/model.env"
FILE_MODEL=""; FILE_LAYERS=""
if [ -f "${MODEL_ENV}" ]; then
    while IFS='=' read -r k v; do
        v="${v%\"}"; v="${v#\"}"; v="${v%\'}"; v="${v#\'}"      # tolerate quoting
        case "$k" in
            KOBOLDCPP_MODEL)      FILE_MODEL="$v" ;;
            KOBOLDCPP_GPU_LAYERS) FILE_LAYERS="$v" ;;
        esac
    done < "${MODEL_ENV}"
fi

# Precedence: explicit env var > model.env > built-in default. A one-off
# `KOBOLDCPP_MODEL=other.gguf ./start.sh` still wins, and doesn't rewrite the selection.
MODEL="${KOBOLDCPP_MODEL:-${FILE_MODEL:-Qwen3.5-9B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf}}"
GPU_LAYERS="${KOBOLDCPP_GPU_LAYERS:-${FILE_LAYERS:-33}}"

# Fail here rather than inside the container: it runs --rm and detached, so a bad model
# name otherwise vanishes with the container and `stack up` just reports "did not come up".
if [ ! -f "${MODELS_DIR}/${MODEL}" ]; then
    echo "Model not found: ${MODELS_DIR}/${MODEL}" >&2
    echo "Available:" >&2
    find "${MODELS_DIR}" -maxdepth 1 -name '*.gguf' -printf '  %f\n' 2>/dev/null | sort >&2 \
        || echo "  (none — see ai-stack/koboldcpp/INSTALL.md §2)" >&2
    exit 1
fi

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
