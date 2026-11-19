#!/usr/bin/env bash
# Fortuna — typed decisions for the frontends: TypeSafe's /v1/systemone shape, answered by
# JevK5 (a GGUF on llama.cpp). Yes/no, choice and score questions about a state, returned as
# calibrated probabilities; nothing is generated.
#
# Usage:
#   ./start.sh            start detached on ai-net as `fortuna-local`, behind the backend router,
#                         which answers as http://fortuna:8009 by name and http://127.0.0.1:8009
#                         from the host (loopback only). Returns once the model has loaded, or
#                         fails with the container's last log lines.
#   ./start.sh --fetch [NAME]   download a model file (default: the configured one) at the pinned
#                         commit and check its sha256. The only thing that downloads Fortuna's
#                         weights, and only when asked (`stack fortuna-model fetch`, stack-ui).
#   ./start.sh --verify [NAME]  hash a downloaded file now and record the result.
#   ./start.sh --check    one read-only line about the configured model (stack doctor); exit 0
#                         only when it is present and verified.
#   ./start.sh --models   list the model files this recipe knows (models.table), with sizes.
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
CONTAINER_NAME="fortuna-local"
PORT=8009
NET="ai-net"

# The model files this recipe will serve live in models.table (name | file | sha256 | bytes |
# temperature | knockout T | default for), pinned to one Hub commit. stack-ui reads the same file.
TABLE="${FORTUNA_TABLE:-${SCRIPT_DIR}/models.table}"
HF_REPO="$(sed -n 's/^REPO=//p' "$TABLE" | tail -1)"
HF_REV="$(sed -n 's/^REV=//p' "$TABLE" | tail -1)"
mapfile -t MODELS < <(grep -E '^[a-z0-9][a-z0-9._-]*\|' "$TABLE")
[ -n "$HF_REPO" ] && [ -n "$HF_REV" ] && ((${#MODELS[@]})) \
  || { echo "fortuna: no models in $TABLE" >&2; exit 1; }

_env_get() {   # strict KEY=value read from fortuna.env, quotes tolerated, nothing executed
  [ -f "$ENV_FILE" ] || return 0
  { grep -E "^[[:space:]]*$1=" "$ENV_FILE" || true; } | tail -1 | cut -d= -f2- \
    | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e "s/^[\"']//" -e "s/[\"']$//"
}

# Default when fortuna.env names no MODEL: the row whose last column lists this GPU.
DEFAULT_MODEL=""
for r in "${MODELS[@]}"; do
  [[ ",${r##*|}," == *",${HOST_GPU},"* ]] && DEFAULT_MODEL="${r%%|*}"
done
WANT="${FORTUNA_MODEL:-$(_env_get MODEL)}"; WANT="${WANT:-$DEFAULT_MODEL}"
API_KEY="${FORTUNA_API_KEY:-$(_env_get API_KEY)}"

MODE=run
case "${1:-}" in
  --models) MODE=models ;;
  --check)  MODE=check ;;
  # --fetch / --verify take an optional model name; without one, the configured model.
  --fetch|--verify) MODE="${1#--}"; [ -n "${2:-}" ] && WANT="$2" ;;
  "") ;;
  *) echo "usage: start.sh [--models | --check | --fetch [NAME] | --verify [NAME]]" >&2; exit 2 ;;
esac

if [[ $MODE == models ]]; then
  for r in "${MODELS[@]}"; do
    IFS='|' read -r n f _ b t _ _ <<<"$r"
    printf '  %-12s %-28s %5s GB  T=%-5s %s\n' "$n" "$f" "$(awk "BEGIN{printf \"%.1f\", $b/1e9}")" \
      "$t" "$([[ $n == "$WANT" ]] && echo '<- configured')"
  done
  exit 0
fi

ROW=""
for r in "${MODELS[@]}"; do [[ "${r%%|*}" == "$WANT" ]] && ROW="$r"; done
[ -n "$ROW" ] || { echo "unknown MODEL '$WANT' — one of: $(printf '%s ' "${MODELS[@]%%|*}")" >&2; exit 1; }
IFS='|' read -r NAME FILE SHA BYTES TEMP KO_TEMP _ <<<"$ROW"
GGUF="${MODELS_DIR}/${FILE}"

# Integrity stamps next to each file, both "<size> <mtime> <sha256>" of the file as it was hashed:
#   <file>.verified  the sha256 matched the table — trusted while size and mtime still match
#   <file>.mismatch  it did not — the file is refused while size and mtime still match
# stack-ui reads the same two files (inventory.fortuna_models), never hashing anything itself.
stamp_now() { echo "$(stat -c '%s %Y' "$GGUF") $SHA"; }
state() {   # verified | mismatch | unchecked | absent
  [ -f "$GGUF" ] || { echo absent; return; }
  local s; s="$(stamp_now)"
  if [ "$(cat "${GGUF}.verified" 2>/dev/null)" = "$s" ]; then echo verified
  elif [ "$(cat "${GGUF}.mismatch" 2>/dev/null)" = "$s" ]; then echo mismatch
  else echo unchecked; fi
}
hash_check() {   # hash the file now and record the result; 0 = matches the table
  echo "checking ${FILE} sha256 ($(awk "BEGIN{printf \"%.1f\", $BYTES/1e9}") GB)…"
  local s; s="$(stamp_now)"
  if echo "${SHA}  ${GGUF}" | sha256sum -c --quiet - >/dev/null 2>&1; then
    echo "$s" > "${GGUF}.verified"; rm -f "${GGUF}.mismatch"
    echo "==> ${FILE}: sha256 matches ${HF_REPO} @ ${HF_REV:0:12}"
    return 0
  fi
  echo "$s" > "${GGUF}.mismatch"; rm -f "${GGUF}.verified"
  echo "sha256 MISMATCH for ${GGUF} — not the file ${HF_REPO} publishes at ${HF_REV:0:12}." >&2
  return 1
}

# Read-only, for `stack doctor` and `stack install`: one line about the configured model,
# exit 0 only when it is present and verified. Never hashes (that can take a while).
if [[ $MODE == check ]]; then
  if [ -n "${FORTUNA_MODEL:-}" ]; then src="FORTUNA_MODEL"
  elif [ -n "$(_env_get MODEL)" ]; then src="fortuna.env"
  else src="default for GPU=${HOST_GPU}"; fi
  case "$(state)" in
    verified)  echo "Fortuna model ${NAME} (${src}) present, sha256 verified"; exit 0 ;;
    unchecked) echo "Fortuna model ${NAME} (${src}) present but not verified yet — stack fortuna-model verify ${NAME} (or it is checked on its next start)" ;;
    mismatch)  echo "Fortuna model ${NAME} (${src}) FAILED its sha256 check — delete ${FILE} and run: stack fortuna-model fetch ${NAME}" ;;
    absent)    echo "Fortuna model ${NAME} (${src}) not downloaded — stack fortuna-model fetch ${NAME} ($(awk "BEGIN{printf \"%.1f\", $BYTES/1e9}") GB, sha256-checked)" ;;
  esac
  exit 1
fi

if [[ $MODE == verify ]]; then
  [ -f "$GGUF" ] || { echo "not downloaded: ${GGUF} — start.sh --fetch ${NAME}" >&2; exit 1; }
  hash_check && exit 0
  echo "Delete it and fetch it again: stack fortuna-model fetch ${NAME}" >&2
  exit 1
fi

if [[ $MODE == fetch ]]; then
  mkdir -p "$MODELS_DIR"
  if [ -f "$GGUF" ]; then
    echo "already present: $GGUF ($(state))"
    case "$(state)" in
      verified) exit 0 ;;
      unchecked) hash_check && exit 0 ;;
    esac
    echo "it does not match — delete it, then fetch again" >&2
    exit 1
  fi
  url="https://huggingface.co/${HF_REPO}/resolve/${HF_REV}/${FILE}"
  echo "downloading ${FILE} (${HF_REPO} @ ${HF_REV:0:12}, $(awk "BEGIN{printf \"%.1f\", $BYTES/1e9}") GB)…"
  # Quiet curl plus our own progress line every 5 s: curl's meter redraws with \r, which shows
  # as nothing at all in the dashboard console until the end. -C - resumes a kept .part.
  curl -fsSL --retry 3 -C - -o "${GGUF}.part" "$url" &
  pid=$!
  trap 'kill $pid 2>/dev/null' EXIT
  while kill -0 "$pid" 2>/dev/null; do
    sleep 5
    have=$(stat -c %s "${GGUF}.part" 2>/dev/null || echo 0)
    awk "BEGIN{printf \"  %.2f of %.2f GB (%d%%)\n\", $have/1e9, $BYTES/1e9, 100*$have/$BYTES}"
  done
  wait "$pid" || { echo "download failed — the .part is kept; run --fetch again to resume" >&2; exit 1; }
  trap - EXIT
  mv "${GGUF}.part" "$GGUF"
  # A complete download with the wrong hash is discarded, not kept as a .part: resuming it
  # would only append to the wrong bytes.
  hash_check || { rm -f "$GGUF" "${GGUF}.mismatch"
                  echo "the download did not match its pinned sha256 and was discarded" >&2; exit 1; }
  echo "==> ${GGUF}"
  exit 0
fi

if [ ! -f "$GGUF" ]; then
  echo "Fortuna model not found: ${GGUF}" >&2
  echo "Fetch it (pinned commit, sha256-checked): ${SCRIPT_DIR}/start.sh --fetch ${NAME}" >&2
  exit 1
fi
# Hash once, then trust the file while its size and mtime match the stamp: 4.5 GB on every start
# would add seconds to `stack up` for nothing. A file dropped in by hand is checked the first time.
case "$(state)" in
  verified) ;;
  mismatch) echo "sha256 mismatch for ${GGUF} (recorded) — not starting. Delete it, then start.sh --fetch ${NAME}" >&2; exit 1 ;;
  *) hash_check >&2 || { echo "not starting." >&2; exit 1; } ;;
esac

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

echo "Starting Fortuna (${NAME}) as ${CONTAINER_NAME} — http://fortuna:${PORT} on ${NET} via the router"
# No host port: the router publishes 127.0.0.1:8009 and answers as `fortuna` on ai-net.
# --restart=on-failure:5 as the other backends. Models are mounted read-only; no :z (labels
# are disabled above).
podman run -d \
    --name "${CONTAINER_NAME}" \
    --restart=on-failure:5 \
    "${GPU_ARGS[@]}" \
    --network "${NET}" \
    -v "${MODELS_DIR}:/models:ro" \
    "${ENV_ARGS[@]}" \
    "${IMAGE}" >/dev/null

for _ in $(seq 1 90); do
  # Asked inside the container: through the router, a load in progress and a router that's
  # down look the same.
  if podman exec "${CONTAINER_NAME}" curl -sf -m 2 -o /dev/null "http://127.0.0.1:${PORT}/health" 2>/dev/null; then
    echo "==> Fortuna up (${NAME}). Logs: podman logs -f ${CONTAINER_NAME}"; exit 0
  fi
  if [ "$(podman inspect -f '{{.State.Status}}' "${CONTAINER_NAME}" 2>/dev/null)" = exited ]; then break; fi
  sleep 2
done
echo "Fortuna did not come up (${CONTAINER_NAME}). Logs:" >&2
podman logs --tail 20 "${CONTAINER_NAME}" 2>&1 | sed 's/^/    /' >&2
exit 1
