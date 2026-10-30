# ComfyUI

> **Notes and recipe live here; mutable state lives at `~/Development/ai_ecosystem/ComfyUI/`.**
> That directory is an upstream `comfyanonymous/ComfyUI` checkout, so anything of ours kept in it
> would be untracked and at risk from updates/reinstalls. Since the containerization, the stack uses
> that checkout **only for its state dirs** (`models`, `input`, `output`, `user`, `custom_nodes`) —
> the code that runs is cloned into the image at a pinned tag.

Node-graph image/video generation for the **AI companion (project #1)** — character portraits and
expression sprites. Runs as a **Podman container** on `ai-net`, GPU via CDI, listening on `:8188`.

## Structure (this folder = the canonical recipe)

- `Containerfile` — `ubuntu:24.04` + venv + cu130 torch + ComfyUI cloned at `ARG COMFYUI_VERSION`
- `start.sh` — builds the image if missing, then runs detached; mirrors `koboldcpp/start.sh`
- `.containerignore` — keeps docs/rollback lock out of the build context
- `requirements.lock.v0.31.1.txt` — the pinned environment, COPYed into the build
- `requirements.lock.pre-0.31.txt` — rollback artifact (see branch `pre-update-v0.18.1`); not a target
- `INSTALL.md` — full install/repro guide

## Running

```bash
stack comfyui          # preferred
stack logs comfyui     # podman logs -f comfyui
./start.sh --lowvram   # extra flags pass through to main.py
```

UI/API at http://localhost:8188. Cold start ~5 s.

## Custom nodes

Custom nodes themselves are **mutable state** — they live in
`~/Development/ai_ecosystem/ComfyUI/custom_nodes/` and are bind-mounted to `/data/custom_nodes`.
Their **Python dependencies are not**: `start.sh` recreates the container every start
(`podman rm -f`), so anything `pip install`ed at runtime is gone on the next restart. Deps must go
in the `Containerfile`, in the *Custom-node dependencies* layer at the end (placed last so it
doesn't invalidate the expensive torch/lockfile layers).

Installed today:

| Node | Deps added to the image | Used by |
|---|---|---|
| `ComfyUI-PhotoMaker-Plus` (shiimizu, pinned `459f5c0`) | `insightface==1.0.1`, `onnxruntime==1.28.0` | iguana studio's Person mode |
| `ComfyUI-GGUF` (city96, pinned `6ea2651`) | `gguf==0.19.0` | iguana studio's Edit mode (Flux Kontext) |

`ComfyUI-GGUF` supplies `UnetLoaderGGUF`, which is **required, not an optimization**: the fp8 build
of Flux Kontext needs ~20 GB VRAM against this card's 12, so the quantized GGUF is the only local
path. Only `gguf` is installed from its requirements — `sentencepiece`/`protobuf` are for its GGUF
*text-encoder* path, and we load clip_l/t5xxl as safetensors through the stock `DualCLIPLoader`.

`stack install comfyui` clones these at their pins from the `CUSTOM_NODES=(…)` registry in `stack`
and `stack doctor` reports drift, so the checkout half is reproducible on a fresh machine. **Weights
still aren't** — PhotoMaker needs `models/photomaker/photomaker-v{1,2}.bin`, and V2 additionally
needs InsightFace `buffalo_l` (5 `.onnx` files) at `models/insightface/models/buffalo_l/` — note the
doubled path, which is how insightface resolves `<root>/models/<name>`. Install reports both as
missing rather than downloading them.

**Don't install a custom node's `requirements.txt` blindly.** PhotoMaker-Plus's pins `numpy<2`
(for the old insightface 0.7.3 sdist), which would downgrade numpy under torch 2.11 and break the
image; and it wants `onnxruntime-gpu`, which has no CUDA 13 build to match this image's torch. See
the comment block in the `Containerfile` and `iguana_studio/comfyui-auto/CLAUDE.md` for the full
reasoning.

## GPU

**Two images, picked by the host profile** (`../lib/host.sh`): `Containerfile` (cu130 torch) →
`localhost/comfyui:latest` on NVIDIA, and `Containerfile.rocm` → `localhost/comfyui:rocm` on the
AMD Halo box. The ROCm one installs **the same torch 2.11.0** from AMD's gfx1151 wheel index
(`+rocm7.13.0`; the wheels bring the ROCm runtime, no ROCm base image), then the same lock file
minus `nvidia-*`, `cuda-*`, `triton` and the torch trio. It needs `libatomic1` (+ `libnuma1`)
or `import torch` fails, and the build ends by asserting `torch.version.hip`. Built and smoke-tested
on the workstation (CPU), 8.4 GB; untested on the GPU until the box arrives.
`ComfyUI --quick-test-for-ci` ends with a harmless `sys.meta_path is None` traceback in both images.

On NVIDIA: `--device nvidia.com/gpu=all` (CDI) **plus `--security-opt label=disable`** — without the latter,
SELinux blocks `/dev/nvidia*` and torch reports `No CUDA GPUs are available` after an otherwise
healthy-looking startup. Confirm with `curl -s localhost:8188/system_stats | jq '.devices[0]'`.

Only **12 GB VRAM** total, shared with KoboldCPP — running both large models at once will OOM.
Prefer one heavy model at a time, or pair KoboldCPP with an SD 1.5 checkpoint.

**ComfyUI holds VRAM after a job** and won't give it back on its own, which reads as a *different*
service being broken: with a Flux Kontext model resident (~6.4 GB), KoboldCPP OOMs the instant it
starts, burns through its `--restart=on-failure:5` budget, and `stack up` reports "did not come up";
`podman logs koboldcpp` shows the CUDA OOM. Make ComfyUI release it — no restart needed:

```bash
curl -X POST http://localhost:8188/free -H 'Content-Type: application/json' \
     -d '{"unload_models":true,"free_memory":true}'
```

## Role in the companion

Image backend for the **custom companion app** at `~/Development/ai_ecosystem/companion/` (NOT
SillyTavern — that's legacy). The app's `services/comfyui.py` drives this server over the HTTP API at
**`http://comfyui:8188`** (by container name on `ai-net`; it was `host.containers.internal:8188`
before the containerization). It generates one base portrait (txt2img) then derives each emotion
sprite via img2img, so all expressions are the same character. See
`~/Development/ai_ecosystem/companion/CLAUDE.md` for the full expression pipeline.

## History

Ran in a `--nvidia` distrobox until Aug 2026. Nothing required distrobox; containerizing removed the
`kind=proc` special case in `stack`, the `distrobox enter` hang, the hand-rolled log rotation, and
the host-venv interpreter trap. The old container was renamed `comfyui-distrobox-old` rather than
deleted. See `INSTALL.md` → *Why not distrobox*.
