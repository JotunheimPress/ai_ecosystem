# Backends

[Wiki home](README.md) · Developer guide

Each backend is a recipe in this repo: a Containerfile per GPU runtime and a `start.sh` that
builds the image if needed, runs the container detached on `ai-net` with
`--restart=on-failure:5`, and returns once it is serving (or fails with its last log lines).

## KoboldCPP (text): [`koboldcpp/`](../../koboldcpp/)

- Images: CUDA (official binary), Vulkan (`-nocuda` binary + Mesa RADV), ROCm (compiled for
  gfx1151). Container `koboldcpp-local`.
- `start.sh` reads `model.env` (parsed, never sourced), waits for the previous copy's memory,
  asks ComfyUI to unload, retries in CUDA unified memory when memory is only fragmented, and
  retries with fewer layers on a real shortage.
- Picks the chat adapter from the GGUF's own vocabulary.
- Notes: [`koboldcpp/CLAUDE.md`](../../koboldcpp/CLAUDE.md); install: [`koboldcpp/INSTALL.md`](../../koboldcpp/INSTALL.md).

## ComfyUI (image and sound): [`comfyui/`](../../comfyui/)

- ComfyUI is cloned into the image at a pinned tag (v0.31.1), with a locked requirements file.
  The host's `ComfyUI/` directory supplies only state, mounted at `/data` with
  `--base-directory /data`; its database is `user/comfyui.db` (`--database-url`).
- NVIDIA needs `--security-opt label=disable` for the CDI device nodes, and therefore no `:z` on
  the 58 GB models mount. AMD runs with `--highvram`.
- Notes: [`comfyui/CLAUDE.md`](../../comfyui/CLAUDE.md); install: [`comfyui/INSTALL.md`](../../comfyui/INSTALL.md).

## Fortuna (decisions): [`fortuna/`](../../fortuna/)

- Answers typed questions about a text (yes/no, choice, score) as calibrated probabilities, in
  TypeSafe's `/v1/systemone` shape, so frontends stop prompting the LLM for labels and parsing
  them. Named after the role, not the engine, so the engine can be swapped.
- Engine: JevK5 (a GGUF) on the official llama.cpp server image pinned by digest, plus a
  stdlib-only adapter (`server.py`). llama-server listens only inside the container.
- Model files come from [`models.table`](../../fortuna/models.table) (pinned commit, sha256, size,
  temperatures, default per GPU), read by both `start.sh` and the dashboard. `--fetch`, `--verify`
  and `--check` maintain and read the integrity stamps.
- Runs with llama-server's context checkpoints and prompt cache off: they cost ~250 ms of CPU per
  question and are never used.
- Notes: [`fortuna/CLAUDE.md`](../../fortuna/CLAUDE.md).

## The router: [`router/`](../../router/)

Stock nginx with a template. Upstreams are variables resolved per request, so a backend that is
down gives a fast 502 instead of keeping nginx from starting. The container is labelled with a
hash of its config, and `stack up` recreates it when that changes. Notes:
[`router/CLAUDE.md`](../../router/CLAUDE.md).
