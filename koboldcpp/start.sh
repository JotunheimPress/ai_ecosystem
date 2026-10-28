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

# ---- VRAM helpers -------------------------------------------------------------
# Best-effort throughout: no nvidia-smi (or a GPU it can't see) just skips the waits.
vram_used() { nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' '; }
vram_free() {   # "?" when unknown
    local f; f="$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' ')"
    echo "${f:-?}"
}

# Wait until GPU memory use stops falling. The old process's VRAM is released after
# `podman rm` returns, not by it; loading the next model before that lands is a race.
wait_vram_settled() {
    local prev cur i
    prev="$(vram_used)"; [ -n "${prev}" ] || return 0
    for i in $(seq 1 30); do                 # <= 15 s
        sleep 0.5
        cur="$(vram_used)"; [ -n "${cur}" ] || return 0
        if [ $(( prev - cur )) -lt 64 ] && [ "${i}" -ge 2 ]; then return 0; fi
        prev="${cur}"
    done
}

# Remove any previous container with the same name. Stop it first so it shuts down
# cleanly, then wait for its VRAM to actually come back.
if podman container exists "${CONTAINER_NAME}" 2>/dev/null; then
    podman stop -t 10 "${CONTAINER_NAME}" >/dev/null 2>&1 || true
    podman rm -f "${CONTAINER_NAME}" >/dev/null
    wait_vram_settled
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
case "${GPU_LAYERS}" in
    ''|*[!0-9]*) echo "KOBOLDCPP_GPU_LAYERS must be a whole number, got '${GPU_LAYERS}'" >&2; exit 1 ;;
esac

# Fail here rather than inside the container: it runs detached, so a bad model name would
# otherwise burn through the restart budget and `stack up` would just report "did not come up".
if [ ! -f "${MODELS_DIR}/${MODEL}" ]; then
    echo "Model not found: ${MODELS_DIR}/${MODEL}" >&2
    echo "Available:" >&2
    find "${MODELS_DIR}" -maxdepth 1 -name '*.gguf' -printf '  %f\n' 2>/dev/null | sort >&2 \
        || echo "  (none — see ai-stack/koboldcpp/INSTALL.md §2)" >&2
    exit 1
fi

# ComfyUI keeps its last models cached in VRAM between jobs, and on this box memory
# another process holds costs a new process about twice its size in allocatable VRAM
# (measured 2026-10-28: 8.7 GB reported free, 5.4 GB allocatable with ComfyUI holding
# 2.3 GB; 9.5 GB once it let go). That is what made a 12B model fail to load after a
# swap. Ask ComfyUI to drop its cache first — it reloads on its next job.
# KOBOLDCPP_FREE_COMFY=0 skips this.
if [ "${KOBOLDCPP_FREE_COMFY:-1}" != "0" ]; then
    if curl -fsS -m 5 -X POST "${COMFYUI_URL:-http://localhost:8188}/free" \
            -H 'Content-Type: application/json' \
            -d '{"unload_models":true,"free_memory":true}' >/dev/null 2>&1; then
        echo "Asked ComfyUI to release its cached models"
        wait_vram_settled
    fi
fi

# --restart=on-failure:5 instead of --rm: podman restarts the container if it crashes
# (OOM, CUDA fault), capped so a bad config can't loop forever. `podman stop` — what
# `stack down`/`stack off` use — counts as a deliberate stop and is never restarted.
# Without --rm a dead container also keeps its logs for `stack logs` post-mortem;
# the `podman rm -f` above clears it before the next start.
launch() {
    podman run -d \
        --name "${CONTAINER_NAME}" \
        --restart=on-failure:5 \
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
        --gpulayers "$1" \
        --model "/models/${MODEL}" \
        --chatcompletionsadapter /chatml.json \
        "${EXTRA_ARGS[@]}" >/dev/null
}

# Watch the load instead of returning as soon as the container exists. A model that
# doesn't fit fails the same way every time, and the restart policy alone just repeated
# it five times while `stack` reported "did not come up". Prints ok | oom | fail | timeout.
await_load() {
    local i logs
    for i in $(seq 1 "${KOBOLDCPP_LOAD_TIMEOUT:-300}"); do
        logs="$(podman logs "${CONTAINER_NAME}" 2>&1 || true)"
        if grep -q "Load Text Model OK: True" <<<"${logs}"; then echo ok; return; fi
        if grep -q "Load Text Model OK: False\|failed to load model\|unable to allocate" <<<"${logs}"; then
            if grep -q "out of memory\|unable to allocate\|failed to allocate" <<<"${logs}"; then
                echo oom; else echo fail; fi
            return
        fi
        if [ "$(podman inspect -f '{{.State.Status}}' "${CONTAINER_NAME}" 2>/dev/null)" = "exited" ] \
            && [ "${i}" -gt 5 ]; then echo fail; return; fi
        sleep 1
    done
    echo timeout
}

EXTRA_ARGS=("$@")
LAYERS="${GPU_LAYERS}"
# Out of memory -> retry with fewer layers on the GPU (the rest run on the CPU: slower,
# but it answers). KOBOLDCPP_LAYER_STEP layers per retry, at most 4 retries.
STEP="${KOBOLDCPP_LAYER_STEP:-4}"
for attempt in 1 2 3 4 5; do
    echo "Starting KoboldCPP at http://localhost:5001 (model: ${MODEL}, gpu layers: ${LAYERS}, free VRAM: $(vram_free) MiB)"
    launch "${LAYERS}"
    result="$(await_load)"
    case "${result}" in
        ok)
            if [ "${LAYERS}" != "${GPU_LAYERS}" ]; then
                echo "WARNING: ${MODEL} did not fit at ${GPU_LAYERS} GPU layers; running at ${LAYERS}." >&2
                echo "         To skip the retries next time, set its layers to ${LAYERS} in stack-ui" >&2
                echo "         (Change model) or KOBOLDCPP_GPU_LAYERS=${LAYERS} in ${MODEL_ENV}." >&2
            fi
            echo "==> KoboldCPP loaded (detached, ai-net). Logs: podman logs -f ${CONTAINER_NAME}"
            exit 0 ;;
        timeout)
            echo "==> KoboldCPP started but still loading after ${KOBOLDCPP_LOAD_TIMEOUT:-300}s. Logs: podman logs -f ${CONTAINER_NAME}"
            exit 0 ;;
        oom)
            podman rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true   # stop the restart loop
            wait_vram_settled
            if [ "${LAYERS}" -le 0 ] || [ "${attempt}" -eq 5 ]; then break; fi
            next=$(( LAYERS - STEP )); [ "${next}" -lt 0 ] && next=0
            echo "Out of GPU memory at ${LAYERS} layers — retrying with ${next}" >&2
            LAYERS="${next}" ;;
        *)
            echo "KoboldCPP failed to load ${MODEL} (not a memory error). Last log lines:" >&2
            podman logs --tail 15 "${CONTAINER_NAME}" 2>&1 | sed 's/^/    /' >&2
            exit 1 ;;
    esac
done

echo "KoboldCPP could not load ${MODEL}: out of GPU memory even at ${LAYERS} layers." >&2
echo "Free VRAM (close GPU-heavy apps) or pick a smaller model. Last log lines:" >&2
echo "  VRAM now: $(vram_used || true) MiB used, $(vram_free) MiB free" >&2
exit 1
