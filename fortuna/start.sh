#!/usr/bin/env bash
# Fortuna — typed decisions for the frontends: TypeSafe's /v1/systemone shape, answered by
# JevK5 (a GGUF on llama.cpp). Yes/no, choice and score questions about a state, returned as
# calibrated probabilities; nothing is generated.
#
# Usage:
#   ./start.sh            start detached on ai-net as `fortuna` (http://fortuna:8009 by name;
#                         http://127.0.0.1:8009 from the host, loopback only). Returns once the
#                         model has loaded, or fails with the container's last log lines.
#   ./start.sh --fetch    download the configured model file (pinned commit) and check its
#                         sha256. The only thing here that downloads weights, and only when asked.
#   ./start.sh --models   list the model files this recipe knows, with their sizes.
#
# Settings: $ECO/fortuna/fortuna.env (git-ignored state dir; parsed, never sourced), or the
# environment (FORTUNA_MODEL, FORTUNA_API_KEY), environment first.
#   MODEL=jevk5-4b-q8     which known file to serve (--models); default by GPU, see below
#   API_KEY=...           require "Authorization: Bearer <key>" on /v1/* (off by default:
#                         nothing outside this machine can reach it)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# ai-stack/fortuna -> ai-stack -> the ecosystem root (projects are siblings).
ECO="${AI_ECOSYSTEM:-$(dirname "$(dirname "$SCRIPT_DIR")")}"
STATE="${FORTUNA_STATE:-$ECO/fortuna}"     # models/ + fortuna.env, never committed
MODELS_DIR="${STATE}/models"
ENV_FILE="${STATE}/fortuna.env"

. "${SCRIPT_DIR}/../lib/host.sh"
host_profile_load
IMAGE="$(fortuna_image)"
BASE="$(fortuna_base_image)"
CONTAINER_NAME="fortuna"
PORT=8009
NET="ai-net"

# The model files this recipe will serve: name | file | sha256 | temperature | knockout T.
# All from huggingface.co/alibiserikbay/JevK5-GGUF at commit HF_REV; the hashes are the Hub's LFS
# sha256 and the temperatures are the per-file values on that commit's model card (they differ by
# file, and the wrong one silently mis-calibrates every probability).
HF_REPO="alibiserikbay/JevK5-GGUF"
HF_REV="ec67b0bfce5119a8b11a2cdb430bb43e3fa3e82a"
MODELS=(
  "jevk5-4b-q8|jevk5-4b-v0.3-Q8_0.gguf|aea433883bc7ed399f2fbd539e53d2eac7caf71a946fe6650995a413979d4a30|1.22|0.93"
  "jevk5-4b-q5|jevk5-4b-v0.3-Q5_K_M.gguf|b1df9869dd14c0f5b243fa956dd985f0141c3d23ec4847da4c503c73c1050601|1.22|0.93"
  "jevk5-4b-q4|jevk5-4b-v0.3-Q4_K_M.gguf|94ca0d7745c47f79091b0892ca657c81d9dc9e4ed0238ba0a7ea261d8938c882|1.22|0.93"
  "jevk5-9b-q8|jevk5-9b-v0.3.3-Q8_0.gguf|283de8fd216ad2200506d9236e574ddb5902b16e6a806a6fa38e9dd1b4286edf|1.316|1.05"
)

_env_get() {   # strict KEY=value read from fortuna.env, quotes tolerated, nothing executed
  [ -f "$ENV_FILE" ] || return 0
  { grep -E "^[[:space:]]*$1=" "$ENV_FILE" || true; } | tail -1 | cut -d= -f2- \
    | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e "s/^[\"']//" -e "s/[\"']$//"
}

# Default: Q8 (229/231 answers identical to bf16, 4.5 GB) where memory is plentiful — the Halo's
# unified memory. On the 12 GB 4070 it shares VRAM with KoboldCPP, so Q4_K_M (2.7 GB, 221/231).
case "$HOST_GPU" in nvidia) DEFAULT_MODEL=jevk5-4b-q4 ;; *) DEFAULT_MODEL=jevk5-4b-q8 ;; esac
WANT="${FORTUNA_MODEL:-$(_env_get MODEL)}"; WANT="${WANT:-$DEFAULT_MODEL}"
API_KEY="${FORTUNA_API_KEY:-$(_env_get API_KEY)}"

ROW=""
for r in "${MODELS[@]}"; do [[ "${r%%|*}" == "$WANT" ]] && ROW="$r"; done
if [[ "${1:-}" == --models ]]; then
  for r in "${MODELS[@]}"; do
    IFS='|' read -r n f _ t k <<<"$r"
    printf '  %-12s %-28s T=%-5s %s\n' "$n" "$f" "$t" "$([[ $n == "$WANT" ]] && echo '<- configured')"
  done
  exit 0
fi
[ -n "$ROW" ] || { echo "unknown MODEL '$WANT' — one of: $(printf '%s ' "${MODELS[@]%%|*}")" >&2; exit 1; }
IFS='|' read -r NAME FILE SHA TEMP KO_TEMP <<<"$ROW"
GGUF="${MODELS_DIR}/${FILE}"

if [[ "${1:-}" == --fetch ]]; then
  mkdir -p "$MODELS_DIR"
  if [ -f "$GGUF" ]; then echo "already present: $GGUF"; exit 0; fi
  url="https://huggingface.co/${HF_REPO}/resolve/${HF_REV}/${FILE}"
  echo "downloading ${FILE} (${HF_REPO} @ ${HF_REV:0:12})…"
  curl -fL --retry 3 -C - -o "${GGUF}.part" "$url"
  echo "checking sha256…"
  echo "${SHA}  ${GGUF}.part" | sha256sum -c --quiet - \
    || { echo "sha256 mismatch — kept as ${GGUF}.part, not used" >&2; exit 1; }
  mv "${GGUF}.part" "$GGUF"
  echo "$(stat -c '%s %Y' "$GGUF") $SHA" > "${GGUF}.verified"
  echo "==> ${GGUF}"
  exit 0
fi

if [ ! -f "$GGUF" ]; then
  echo "Fortuna model not found: ${GGUF}" >&2
  echo "Fetch it (pinned commit, sha256-checked): ${SCRIPT_DIR}/start.sh --fetch" >&2
  exit 1
fi
# Hash once, then trust the file while its size and mtime match the stamp: 4.5 GB on every start
# would add seconds to `stack up` for nothing. A file dropped in by hand is checked the first time.
stamp="$(stat -c '%s %Y' "$GGUF") $SHA"
if [ "$(cat "${GGUF}.verified" 2>/dev/null)" != "$stamp" ]; then
  echo "checking ${FILE} sha256 (first start with this file)…"
  echo "${SHA}  ${GGUF}" | sha256sum -c --quiet - \
    || { echo "sha256 mismatch for ${GGUF} — not starting. Re-fetch: rm it, then start.sh --fetch" >&2; exit 1; }
  echo "$stamp" > "${GGUF}.verified"
fi

if ! podman image exists "${IMAGE}"; then
  echo "Image not found — building ${IMAGE} (llama.cpp ${HOST_LLM_BACKEND} base + adapter)…"
  podman build --format docker --build-arg "BASE=${BASE}" -t "${IMAGE}" "${SCRIPT_DIR}"
fi

if podman container exists "${CONTAINER_NAME}" 2>/dev/null; then
  podman rm -f "${CONTAINER_NAME}" >/dev/null
fi
podman network exists "${NET}" 2>/dev/null || podman network create "${NET}" >/dev/null

# GPU: same devices as the other llama.cpp backends (lib/host.sh). label=disable on NVIDIA for
# the CDI device nodes, as ComfyUI; AMD's gpu_args already carry it. With no GPU, CPU only.
gpu_args --security-opt label=disable
NGL=99; [ "$HOST_GPU" = none ] && NGL=0
ENV_ARGS=(-e "FORTUNA_GGUF=${FILE}" -e "FORTUNA_MODEL_NAME=${NAME}"
          -e "FORTUNA_TEMPERATURE=${TEMP}" -e "FORTUNA_KNOCKOUT_TEMPERATURE=${KO_TEMP}"
          -e "FORTUNA_NGL=${NGL}")
[ -n "$API_KEY" ] && ENV_ARGS+=(-e "FORTUNA_API_KEY=${API_KEY}")

echo "Starting Fortuna (${NAME}) at http://127.0.0.1:${PORT} — http://fortuna:${PORT} on ${NET}"
# Published on loopback only, whatever BIND says: the frontends reach it over ai-net, and the
# host port is for curl and the dashboard. --restart=on-failure:5 as the other backends.
# Models are mounted read-only; no :z (labels are disabled above).
podman run -d \
    --name "${CONTAINER_NAME}" \
    --restart=on-failure:5 \
    "${GPU_ARGS[@]}" \
    --network "${NET}" \
    -p "127.0.0.1:${PORT}:${PORT}" \
    -v "${MODELS_DIR}:/models:ro" \
    "${ENV_ARGS[@]}" \
    "${IMAGE}" >/dev/null

for _ in $(seq 1 90); do
  if curl -sf -m 2 -o /dev/null "http://127.0.0.1:${PORT}/health"; then
    echo "==> Fortuna up (${NAME}). Logs: podman logs -f ${CONTAINER_NAME}"; exit 0
  fi
  if [ "$(podman inspect -f '{{.State.Status}}' "${CONTAINER_NAME}" 2>/dev/null)" = exited ]; then break; fi
  sleep 2
done
echo "Fortuna did not come up on :${PORT}. Logs:" >&2
podman logs --tail 20 "${CONTAINER_NAME}" 2>&1 | sed 's/^/    /' >&2
exit 1
