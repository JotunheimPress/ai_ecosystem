#!/bin/bash
# Fortuna container entrypoint: llama-server (loopback only) + the /v1/systemone adapter.
#
# Two processes, one container: if either exits, the other is stopped and the container exits
# with its status, so podman's --restart=on-failure brings both back together. `podman stop`
# sends TERM, which is passed to both.
set -euo pipefail

: "${FORTUNA_GGUF:?FORTUNA_GGUF (the model file under /models) is not set}"
MODEL="/models/${FORTUNA_GGUF}"
[ -f "$MODEL" ] || { echo "fortuna: model not found: $MODEL" >&2; exit 1; }

# -c 16384: JevK5 refuses states past 16,384 tokens; a longer prompt gets llama-server's 400,
#   which the adapter returns as a 422.
# --parallel 1: one slot; the adapter serialises requests (server.py).
# --no-webui: nothing here is for a browser. --host 127.0.0.1: only the adapter reaches it.
# --ctx-checkpoints 0: llama-server otherwise saves a checkpoint of Qwen3.5's recurrent state
#   after every prompt. JevK5 sends cache_prompt: false, so nothing ever restores one, and on
#   the Halo it cost ~240 ms of CPU per evaluation (121 tokens: 358 ms -> 94 ms; measured
#   2026-11-17, fortuna/CLAUDE.md "Performance").
# --cache-ram 0: the host-RAM prompt cache (8 GiB by default) is unused for the same reason.
/app/llama-server \
    --host 127.0.0.1 --port 8080 \
    --model "$MODEL" \
    --ctx-size "${FORTUNA_CTX:-16384}" \
    --n-gpu-layers "${FORTUNA_NGL:-99}" \
    --parallel 1 \
    --ctx-checkpoints 0 \
    --cache-ram 0 \
    --no-webui &
llama=$!

python3 /opt/fortuna/server.py &
adapter=$!

trap 'kill -TERM "$llama" "$adapter" 2>/dev/null' TERM INT
set +e
wait -n "$llama" "$adapter"
status=$?
kill -TERM "$llama" "$adapter" 2>/dev/null
wait
exit "$status"
