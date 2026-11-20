#!/usr/bin/env bash
# ============================================================
# stack-ui — Podman run script
#
# This container is unusual, and every flag below is load-bearing:
#
#   --network=host   `stack` health-probes http://localhost:5001 etc. In its own netns
#                    those are the CONTAINER's ports and everything reads as down. Host
#                    netns also means uvicorn binds the host's own interfaces directly
#                    (0.0.0.0: the whole LAN) — no -p needed.
#   podman.sock      the host container engine. `stack` drives podman; this is how.
#                    Its DIRECTORY is mounted, not the socket file: a file bind-mount pins
#                    the inode, so when podman.socket re-creates the socket (it did on
#                    2026-09-23) the container keeps the dead one and every `stack` call
#                    fails with "connection refused" until the dashboard is restarted.
#   session bus      the host user systemd. `stack boot on|off` is systemctl --user, so
#                    without this the single most useful button doesn't work.
#   $ECO             the projects themselves — the `stack` script, models, model.env.
#                    Mounted at its REAL host path, not a tidy /eco: podman-remote has the
#                    HOST engine resolve -v and build-context paths, so a container-only
#                    path makes `stack <app>` start apps with broken mounts.
#
# Those first three amount to "this container can control the host's containers and user
# services". That is the same authority the host-side version had; it is not new power,
# but it is real power, so the container is deliberately minimal and loopback-only.
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# stack-ui -> the checkout (any folder name) -> the ecosystem root. This app ships inside
# the checkout (like the comfyui recipe), not as a sibling project.
CHECKOUT="$(dirname "$SCRIPT_DIR")"
ECO="${AI_ECOSYSTEM:-$(dirname "$CHECKOUT")}"
IMAGE="localhost/stackui:latest"
CONTAINER="stackui"
PORT="${STACK_UI_PORT:-7863}"

REBUILD=0
BUILD_ONLY=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --rebuild|-r) REBUILD=1; shift ;;
    --build-only) BUILD_ONLY=1; shift ;;
    --help|-h) echo "Usage: $0 [--rebuild] [--build-only]"
               echo "  --rebuild     build the image even if it matches this checkout"
               echo "  --build-only  build the image if needed, then stop: don't (re)start the dashboard"
               exit 0 ;;
    *) echo "Unknown flag: $1"; exit 1 ;;
  esac
done

# The code is baked into the image, so an image built from an older checkout serves the
# old dashboard. The image carries the hash of the source it was built from
# (source-hash.sh); a different hash here means the checkout moved on, so build again.
SOURCE="$("$SCRIPT_DIR/source-hash.sh")"
WHY=""
if [[ "$REBUILD" -eq 1 ]]; then
  WHY="asked to rebuild"
elif ! podman image exists "$IMAGE"; then
  WHY="no image yet"
elif [[ "$(podman image inspect --format '{{index .Labels "ai-stack.source"}}' "$IMAGE" 2>/dev/null)" != "$SOURCE" ]]; then
  WHY="stack-ui/ has changed since the image was built"
fi
if [[ -n "$WHY" ]]; then
  echo "==> Building image $IMAGE ($WHY) …"
  podman build --format docker --label "ai-stack.source=$SOURCE" \
    -t "$IMAGE" -f "$SCRIPT_DIR/Containerfile" "$SCRIPT_DIR"
fi
[[ "$BUILD_ONLY" -eq 1 ]] && exit 0

RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
SOCK="$RUNTIME_DIR/podman/podman.sock"
BUS="$RUNTIME_DIR/bus"
# --userns=keep-id is required, not cosmetic: the session bus authenticates with EXTERNAL
# against the uid the HOST sees. Under the default rootless mapping the process is uid 0
# inside and 1000 outside, the two disagree, and the bus answers REJECTED. keep-id makes
# them the same number, which is what lets the systemctl shim reach the host manager.

# Checked BEFORE the running dashboard is removed: without the socket the new one can't
# start, and removing first left no dashboard at all (2026-11-19: the unit said "listening"
# but the socket file was gone; a restart of the unit re-creates it).
if [[ ! -S "$SOCK" ]]; then
  echo "!! no podman socket at $SOCK — the running dashboard was left as it is" >&2
  echo "   enable it:  systemctl --user enable --now podman.socket" >&2
  echo "   or, if that unit is already active:  systemctl --user restart podman.socket" >&2
  exit 1
fi
if podman container exists "$CONTAINER"; then
  echo "==> Removing existing container $CONTAINER …"
  podman rm -f "$CONTAINER" >/dev/null
fi

# The bus is only needed by `stack boot`; everything else works without it, so this
# is a warning rather than a hard failure.
BUS_ARGS=()
if [[ -S "$BUS" ]]; then
  BUS_ARGS=(-v "$BUS:$BUS")
else
  echo "!! no session bus at $BUS — the on/off switch will not work" >&2
fi

# Download tokens. Civitai gates many files behind an account (401 without one), and
# HuggingFace gates some repos. The code has always read CIVITAI_TOKEN / HF_TOKEN, but
# nothing passed them in, so the containerized dashboard could never use them. Two
# sources, neither of which puts a value on this command line (where `ps` would show it):
#   stack-ui/tokens.env   KEY=value lines, git-ignored — keep it chmod 600
#   your environment      `-e NAME` with no value copies it from this shell
TOKEN_ARGS=()
TOKENS="$SCRIPT_DIR/tokens.env"
if [[ -f "$TOKENS" ]]; then
  TOKEN_ARGS+=(--env-file "$TOKENS")
fi
# GITHUB_TOKEN: read access to the private ai-stack repo, for About's release check;
# STACK_UPDATE_CHECK=off turns that check off.
for var in CIVITAI_TOKEN HF_TOKEN GITHUB_TOKEN STACK_UPDATE_CHECK; do
  if [[ -n "${!var:-}" ]]; then TOKEN_ARGS+=(-e "$var"); fi
done

# The host's GPU profile (ai-stack/lib/host.sh), decided HERE on the host and handed in as
# STACK_GPU: the start scripts this dashboard runs detect the GPU with nvidia-ctk and
# /dev/kfd, neither of which exists in this container, so on their own they would decide
# "no GPU" and start KoboldCPP without one.
. "$SCRIPT_DIR/../lib/host.sh"
host_profile_load

# NVIDIA: nvidia-smi, injected from the HOST by the NVIDIA CDI spec rather than installed
# in the image: it has to match the host driver exactly, and the driver moves with Bazzite
# updates. koboldcpp/start.sh reads free VRAM through it to wait for a swapped-out model's
# memory to come back, and `stack doctor` checks it. No CUDA context is created, so it
# costs no VRAM. AMD needs nothing: start.sh reads amdgpu's counters in /sys, which the
# container sees as it is.
GPU_ARGS=()
case "$HOST_GPU" in
  nvidia) GPU_ARGS=(--device nvidia.com/gpu=all) ;;
  amd)    ;;
  *)      echo "!! no GPU found on this host — the backends will start without one" >&2 ;;
esac

echo "==> Starting $CONTAINER on http://127.0.0.1:${PORT} …"
podman run -d \
  --name "$CONTAINER" \
  --restart unless-stopped \
  --network=host \
  --security-opt label=disable \
  --userns=keep-id \
  -e "STACK_UI_PORT=${PORT}" \
  -e "XDG_RUNTIME_DIR=/tmp/xdg" \
  -e "HOME=/tmp/home" \
  -e "AI_ECOSYSTEM=${ECO}" \
  -e "STACK_CHECKOUT=${CHECKOUT}" \
  -e "DBUS_SESSION_BUS_ADDRESS=unix:path=${BUS}" \
  -v "$(dirname "$SOCK"):/run/podman" \
  "${BUS_ARGS[@]}" \
  "${TOKEN_ARGS[@]}" \
  "${GPU_ARGS[@]}" \
  -e STACK_GPU="$HOST_GPU" \
  -e COMFYUI_PORT="$HOST_COMFY_PORT" \
  -v "$ECO:$ECO" \
  "$IMAGE"

echo ""
echo "✓  AI Ecosystem is running."
echo "   Open: http://localhost:${PORT}   (other devices: http://$(hostname -s).local:${PORT})"
echo "   Logs: podman logs -f $CONTAINER"
echo "   Stop: podman stop $CONTAINER"
