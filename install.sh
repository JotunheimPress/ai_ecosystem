#!/usr/bin/env bash
# Idempotent: venv + deps + rendered systemd user unit. Mirrors `stack install`.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT="stack-ui.service"
DST="$HOME/.config/systemd/user/$UNIT"

if [ ! -x "$DIR/venv/bin/python" ]; then
  echo "==> creating venv"
  python3 -m venv "$DIR/venv"
fi
echo "==> installing deps"
"$DIR/venv/bin/pip" install -q --upgrade pip
"$DIR/venv/bin/pip" install -q -r "$DIR/requirements.txt"

rendered=$(sed "s|@SELF@|$DIR|g" "$DIR/$UNIT")
if [ -f "$DST" ] && [ "$rendered" = "$(cat "$DST")" ]; then
  echo "==> systemd unit already installed ($DST)"
else
  echo "==> installing systemd unit -> $DST"
  mkdir -p "$(dirname "$DST")"
  printf '%s\n' "$rendered" > "$DST"
  systemctl --user daemon-reload
fi
echo "==> done. start it:  stack stackui    (or: systemctl --user enable --now $UNIT)"
