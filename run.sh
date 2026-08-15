#!/usr/bin/env bash
# Start stack-ui using its own venv. `stack stackui` calls this; so can you.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$DIR/venv/bin/python"
[ -x "$PY" ] || { echo "no venv — run: python3 -m venv venv && ./venv/bin/pip install -r requirements.txt" >&2; exit 1; }
cd "$DIR"                    # server.py mounts static/ and templates/ by relative path
exec "$PY" run.py "$@"
