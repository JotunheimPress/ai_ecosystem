#!/usr/bin/env bash
# Usage:
#   ./start.sh                              # launches the web UI on :5001
#   ./start.sh --model /models/foo.gguf    # load a specific model at start
#   ./start.sh --help                       # show KoboldCPP CLI options
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODELS_DIR="${SCRIPT_DIR}/models"
IMAGE="localhost/koboldcpp:latest"
# `-local`: the name `koboldcpp` belongs to the backend router (ai-stack/router), which
# forwards to this container or to the Halo box. Frontends keep http://koboldcpp:5001.
CONTAINER_NAME="koboldcpp-local"

# Build image if it doesn't exist yet
if ! podman image exists "${IMAGE}"; then
    echo "Image not found — building from Containerfile..."
    podman build --format docker -t "${IMAGE}" "${SCRIPT_DIR}"
fi

# ---- VRAM helpers -------------------------------------------------------------
# Best-effort throughout: no nvidia-smi (or a GPU it can't see) just skips the waits.
# `|| true`: under pipefail a missing nvidia-smi (e.g. run from the stack-ui container,
# which has none) failed the assignment and set -e killed the script without a word.
vram_used() { nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' ' || true; }
vram_free() {   # "?" when unknown
    local f; f="$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' ' || true)"
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

# Remove any previous container with the same name, plus the pre-router `koboldcpp` one
# (it would still hold :5001 and its VRAM). Stop it first so it shuts down cleanly, then
# wait for its VRAM to actually come back.
for c in "${CONTAINER_NAME}" koboldcpp; do
    if podman container exists "${c}" 2>/dev/null; then
        podman stop -t 10 "${c}" >/dev/null 2>&1 || true
        podman rm -f "${c}" >/dev/null
        wait_vram_settled
    fi
done

# Persisted model selection: models/model.env's sibling, written by tooling (and by
# hand) so a chosen model survives `stack up`, a restart, and a reboot. Without it the
# selection lived only in the caller's environment, which the systemd unit and `stack`
# don't set — so the hardcoded default below was the *only* model the stack could boot.
#
# Parsed strictly, never sourced: this file gets written from model names that came off
# the internet, and `.` would execute whatever ended up in it.
MODEL_ENV="${SCRIPT_DIR}/model.env"
FILE_MODEL=""; FILE_LAYERS=""; FILE_ADAPTER=""; FILE_CONTEXT=""
if [ -f "${MODEL_ENV}" ]; then
    while IFS='=' read -r k v; do
        v="${v%\"}"; v="${v#\"}"; v="${v%\'}"; v="${v#\'}"      # tolerate quoting
        case "$k" in
            KOBOLDCPP_MODEL)      FILE_MODEL="$v" ;;
            KOBOLDCPP_GPU_LAYERS) FILE_LAYERS="$v" ;;
            KOBOLDCPP_ADAPTER)    FILE_ADAPTER="$v" ;;
            KOBOLDCPP_CONTEXT)    FILE_CONTEXT="$v" ;;
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

# Context window (tokens). Unset = KoboldCPP's own default (8192), exactly as before.
# Standard sizes only, so a typo can't ask for a window that eats the card: the KV cache
# grows with it (a 9B Q4_K_M at 16384 is affordable on 12 GB; 32768 may cost GPU layers).
# Apps read the real value from /api/extra/true_max_context_length, so nothing else to set.
CONTEXT="${KOBOLDCPP_CONTEXT:-${FILE_CONTEXT:-}}"
CONTEXT_ARGS=()
if [ -n "${CONTEXT}" ]; then
    case "${CONTEXT}" in
        4096|6144|8192|12288|16384|24576|32768) CONTEXT_ARGS=(--contextsize "${CONTEXT}") ;;
        *) echo "KOBOLDCPP_CONTEXT must be one of 4096 6144 8192 12288 16384 24576 32768, got '${CONTEXT}'" >&2
           exit 1 ;;
    esac
fi

# Fail here rather than inside the container: it runs detached, so a bad model name would
# otherwise burn through the restart budget and `stack up` would just report "did not come up".
if [ ! -f "${MODELS_DIR}/${MODEL}" ]; then
    echo "Model not found: ${MODELS_DIR}/${MODEL}" >&2
    echo "Available:" >&2
    find "${MODELS_DIR}" -maxdepth 1 -name '*.gguf' -printf '  %f\n' 2>/dev/null | sort >&2 \
        || echo "  (none — see ai-stack/koboldcpp/INSTALL.md §2)" >&2
    exit 1
fi

# ---- chat format ------------------------------------------------------------------
# /v1/chat/completions has to be formatted the way the MODEL was trained, and that differs
# per model — a fixed adapter breaks the moment the model is swapped. chatml.json was right
# for Qwen and wrong for Rocinante (Mistral Nemo: [INST]…[/INST], no ChatML tokens in its
# vocabulary at all), which then occasionally wrote "<|im_end|>" as text and carried on
# with an invented user turn. Picked from the model file's own metadata (the vocabulary
# and chat template sit in its first few MB):
#   <|im_start|>      -> chatml.json         (Qwen and other ChatML models)
#   [SYSTEM_PROMPT]   -> AutoGuess           (newer Mistral, which has real system tokens)
#   [INST]            -> mistral-nemo.json   (Nemo-style: instructions inside [INST])
#   anything else     -> AutoGuess           (KoboldCPP reads the template itself)
# KOBOLDCPP_ADAPTER (env or model.env) overrides: a file in this folder or a built-in name.
pick_adapter() {
    local seen
    seen="$(head -c 67108864 "${MODELS_DIR}/${MODEL}" 2>/dev/null \
        | LC_ALL=C grep -a -o -e '<|im_start|>' -e '\[SYSTEM_PROMPT\]' -e '\[INST\]' 2>/dev/null \
        | sort -u | tr '\n' ' ')"
    case "${seen}" in
        *'<|im_start|>'*)     echo chatml.json ;;
        *'[SYSTEM_PROMPT]'*)  echo AutoGuess ;;
        *'[INST]'*)           echo mistral-nemo.json ;;
        *)                    echo AutoGuess ;;
    esac
}
ADAPTER="${KOBOLDCPP_ADAPTER:-${FILE_ADAPTER:-$(pick_adapter)}}"
ADAPTER_ARGS=(--chatcompletionsadapter "${ADAPTER}")
ADAPTER_MOUNT=()
if [[ "${ADAPTER}" == *.json ]]; then
    if [ ! -f "${SCRIPT_DIR}/${ADAPTER}" ]; then
        echo "Chat adapter not found: ${SCRIPT_DIR}/${ADAPTER} (run: stack install koboldcpp)" >&2
        exit 1
    fi
    ADAPTER_MOUNT=(-v "${SCRIPT_DIR}/${ADAPTER}:/adapter.json:z")
    ADAPTER_ARGS=(--chatcompletionsadapter /adapter.json)
fi

# Orpheus TTS (Pantheon's voice) holds ~3 GB while voice is on. Unload it so the chat
# model gets the room; Pantheon reloads it on the next spoken line. Only when it is
# actually loaded, and then wait for the memory itself: the unload call returns at once
# and the server frees its VRAM a second or two later, when its process restarts —
# wait_vram_settled alone returned before that and the chat model fell back to fewer
# layers (measured: 29 instead of 33).
# Asked through `podman exec`, not the router: this is about the copy sharing THIS GPU.
# Through the router it would unload whichever Orpheus is serving, the Halo box's included.
ORPHEUS_LOCAL="orpheus-tts-local"
orpheus_local() { podman exec "${ORPHEUS_LOCAL}" curl "$@" 2>/dev/null; }
if orpheus_local -fsS -m 3 "http://localhost:5005/api/v1/model" | grep -qi orpheus; then
    before="$(vram_used)"
    if orpheus_local -fsS -m 5 -X POST "http://localhost:5005/api/admin/reload_config" \
            -H 'Content-Type: application/json' -d '{"filename":"unload_model"}' >/dev/null 2>&1; then
        echo "Unloaded Orpheus TTS to make room (Pantheon reloads it when it next speaks)"
        for _ in $(seq 1 40); do                 # <= 20 s
            sleep 0.5
            now="$(vram_used)"
            [ -n "${before}" ] && [ -n "${now}" ] && [ $(( before - now )) -ge 1000 ] && break
            [ -z "${before}" ] && break
        done
        wait_vram_settled
    fi
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
# $1 = GPU layers, $2 = 1 for CUDA unified memory (see the fragmentation note below).
launch() {
    local um=()
    [ "${2:-0}" = 1 ] && um=(-e GGML_CUDA_ENABLE_UNIFIED_MEMORY=1)
    podman run -d \
        --name "${CONTAINER_NAME}" \
        "${um[@]}" \
        --restart=on-failure:5 \
        --privileged \
        --device nvidia.com/gpu=all \
        --network ai-net \
        -v "${MODELS_DIR}:/models:z" \
        "${ADAPTER_MOUNT[@]}" \
        "${IMAGE}" \
        --host 0.0.0.0 \
        --port 5001 \
        --usecublas \
        --gpulayers "$1" \
        --model "/models/${MODEL}" \
        "${ADAPTER_ARGS[@]}" \
        "${CONTEXT_ARGS[@]}" \
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

# Fragmentation: on this box the desktop apps' small GPU allocations land in the middle of
# VRAM, so "free" can be large while no single block is. Measured 2026-10-28: 10.4 GB
# free, largest single block ~5 GB, and the 33-layer Rocinante load asks for one 5.5 GB
# weights buffer — it failed while 256 MB pieces summing to 9.5 GB succeeded. CUDA
# unified memory (GGML_CUDA_ENABLE_UNIFIED_MEMORY=1) doesn't need one contiguous block:
# same model, same state, it loaded at 33 layers and generated 26-32 tok/s, against
# 14-17 tok/s after falling back to 29 layers in normal mode.
# It is used ONLY when the failed attempt's own log shows the room was there (free >=
# its weights buffer + UM_MARGIN). Unified memory never fails when VRAM is truly short —
# it spills into system RAM and crawls — so a real shortage must still drop layers.
UM_MARGIN="${KOBOLDCPP_UM_MARGIN:-2500}"     # MiB for KV cache + compute buffers
fragmented() {   # 0 if the last attempt failed with room to spare
    local logs free buf
    logs="$(podman logs "${CONTAINER_NAME}" 2>&1 || true)"
    free="$(grep -oP '\d+(?= MiB free)' <<<"${logs}" | tail -1)"
    buf="$(grep -oP 'CUDA0 model buffer size = +\K\d+' <<<"${logs}" | tail -1)"
    [ -n "${buf}" ] || buf="$(grep -oP 'allocating \K\d+(?=\.\d+ MiB on device 0: cudaMalloc failed)' <<<"${logs}" | head -1)"
    [ -n "${free}" ] && [ -n "${buf}" ] || return 1
    echo "${free} ${buf}" > "${FRAG_NOTE}"
    [ "${free}" -ge $(( buf + UM_MARGIN )) ]
}
FRAG_NOTE="$(mktemp)"; trap 'rm -f "${FRAG_NOTE}"' EXIT

EXTRA_ARGS=("$@")
LAYERS="${GPU_LAYERS}"
UNIFIED=0
# Out of memory -> retry with fewer layers on the GPU (the rest run on the CPU: slower,
# but it answers). KOBOLDCPP_LAYER_STEP layers per retry, at most 4 retries.
STEP="${KOBOLDCPP_LAYER_STEP:-4}"
for attempt in 1 2 3 4 5 6; do
    echo "Starting KoboldCPP at http://localhost:5001 (model: ${MODEL}, chat format: ${ADAPTER}, context: ${CONTEXT:-default}, gpu layers: ${LAYERS}$([ "${UNIFIED}" = 1 ] && echo ', unified memory'), free VRAM: $(vram_free) MiB)"
    launch "${LAYERS}" "${UNIFIED}"
    result="$(await_load)"
    case "${result}" in
        ok)
            if [ "${UNIFIED}" = 1 ]; then
                echo "Note: loaded with CUDA unified memory — GPU memory was fragmented." >&2
            fi
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
            if [ "${UNIFIED}" = 0 ] && fragmented; then
                read -r f b < "${FRAG_NOTE}"
                podman rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true
                wait_vram_settled
                echo "GPU memory is fragmented (${f} MiB free, but no single ${b} MiB block) — retrying ${LAYERS} layers with unified memory" >&2
                UNIFIED=1
                continue
            fi
            podman rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true   # stop the restart loop
            wait_vram_settled
            UNIFIED=0
            if [ "${LAYERS}" -le 0 ] || [ "${attempt}" -eq 6 ]; then break; fi
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
