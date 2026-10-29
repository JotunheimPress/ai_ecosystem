#!/usr/bin/env bash
# ============================================================
# stack-ui — Podman run script
#
# This container is unusual, and every flag below is load-bearing:
#
#   --network=host   `stack` health-probes http://localhost:5001 etc. In its own netns
#                    those are the CONTAINER's ports and everything reads as down. Host
#                    netns also means uvicorn's 127.0.0.1 bind IS the host loopback, so
#                    the service stays unreachable from the network — no -p needed.
#   podman.sock      the host container engine. `stack` drives podman; this is how.
#                    Its DIRECTORY is mounted, not the socket file: a file bind-mount pins
#                    the inode, so when podman.socket re-creates the socket (it did on
#                    2026-09-23) the container keeps the dead one and every `stack` call
#                    fails with "connection refused" until the dashboard is restarted.
#   session bus      the host user systemd. `stack on`/`off` are systemctl --user, so
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
# ai-stack/stack-ui -> ai-stack -> the ecosystem root. Two dirnames, not one: this app
# ships inside the ai-stack checkout (like the comfyui recipe), not as a sibling project.
ECO="${AI_ECOSYSTEM:-$(dirname "$(dirname "$SCRIPT_DIR")")}"
IMAGE="localhost/stackui:latest"
CONTAINER="stackui"
PORT="${STACK_UI_PORT:-7863}"

REBUILD=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --rebuild|-r) REBUILD=1; shift ;;
    --help|-h) echo "Usage: $0 [--rebuild]"; exit 0 ;;
    *) echo "Unknown flag: $1"; exit 1 ;;
  esac
done

if [[ "$REBUILD" -eq 1 ]] || ! podman image exists "$IMAGE"; then
  echo "==> Building image $IMAGE …"
  podman build --format docker -t "$IMAGE" -f "$SCRIPT_DIR/Containerfile" "$SCRIPT_DIR"
fi

if podman container exists "$CONTAINER"; then
  echo "==> Removing existing container $CONTAINER …"
  podman rm -f "$CONTAINER" >/dev/null
fi

RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
SOCK="$RUNTIME_DIR/podman/podman.sock"
BUS="$RUNTIME_DIR/bus"
# --userns=keep-id is required, not cosmetic: the session bus authenticates with EXTERNAL
# against the uid the HOST sees. Under the default rootless mapping the process is uid 0
# inside and 1000 outside, the two disagree, and the bus answers REJECTED. keep-id makes
# them the same number, which is what lets the systemctl shim reach the host manager.

if [[ ! -S "$SOCK" ]]; then
  echo "!! no podman socket at $SOCK" >&2
  echo "   enable it: systemctl --user enable --now podman.socket" >&2
  exit 1
fi
# The bus is only needed by `stack on`/`off`; everything else works without it, so this
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
for var in CIVITAI_TOKEN HF_TOKEN; do
  if [[ -n "${!var:-}" ]]; then TOKEN_ARGS+=(-e "$var"); fi
done

# nvidia-smi, injected from the HOST by the NVIDIA CDI spec rather than installed in the
# image: it has to match the host driver exactly, and the driver moves with Bazzite
# updates. koboldcpp/start.sh reads free VRAM through it to wait for a swapped-out model's
# memory to come back, and `stack doctor` checks it. No CUDA context is created, so it
# costs no VRAM. Optional: without the CDI spec the dashboard runs and those waits skip.
GPU_ARGS=()
if command -v nvidia-ctk >/dev/null 2>&1 && nvidia-ctk cdi list 2>/dev/null | grep -q "nvidia.com/gpu=all"; then
  GPU_ARGS=(--device nvidia.com/gpu=all)
else
  echo "!! no CDI device nvidia.com/gpu=all — nvidia-smi won't be available in $CONTAINER" >&2
fi

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
  -e "DBUS_SESSION_BUS_ADDRESS=unix:path=${BUS}" \
  -v "$(dirname "$SOCK"):/run/podman" \
  "${BUS_ARGS[@]}" \
  "${TOKEN_ARGS[@]}" \
  "${GPU_ARGS[@]}" \
  -v "$ECO:$ECO" \
  "$IMAGE"

echo ""
echo "✓  AI Ecosystem is running."
echo "   Open: http://localhost:${PORT}"
echo "   Logs: podman logs -f $CONTAINER"
echo "   Stop: podman stop $CONTAINER"
