#!/usr/bin/env bash
# HOME has to be created AT RUNTIME, by the process that will use it.
#
# The container runs with --userns=keep-id, so the uid inside is the host user's — a
# number the image can't know at build time. A HOME baked into the image is therefore
# owned by root, and the podman client refuses a config dir it doesn't own ("path
# /... exists and it is not owned by the current user"), which breaks EVERY podman call.
# Creating it here means it's owned by whoever we actually are.
set -euo pipefail
export HOME="${HOME:-/tmp/home}"
# Same story for XDG_RUNTIME_DIR: the podman client wants to mkdir under it (libpod/),
# and the HOST's runtime dir is not ours to write into — we only mount two sockets out of
# it. Point it at a container-side dir we own. The session bus is addressed explicitly via
# DBUS_SESSION_BUS_ADDRESS, so nothing depends on this pointing at the real one.
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/tmp/xdg}"
mkdir -p "$HOME/.config/containers" "$XDG_RUNTIME_DIR"
exec "$@"
