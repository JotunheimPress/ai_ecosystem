# Models

[Wiki home](README.md) · User guide

The stack never downloads weights on its own. You fetch them from the dashboard (or by hand),
and you choose which one each backend boots.

## Text: KoboldCPP

**Dashboard → Language models → Installed.** Files live in `koboldcpp/models/`.

- **Use for KoboldCPP** writes `koboldcpp/model.env` (`KOBOLDCPP_MODEL`). It takes effect at the
  next start; a restart is offered when another model is loaded.
- **Use with GPU layers…** also sets `KOBOLDCPP_GPU_LAYERS`. The value is remembered per model
  (`koboldcpp/model-presets.json`), so switching back keeps it.
- **Context window** sets `KOBOLDCPP_CONTEXT` (4K to 128K; KoboldCPP's default is 8K). Each size
  shows its KV-cache cost for the selected model and is flagged past the model's trained length.
- **Delete** refuses the selected model and the loaded one.
- **Find on HuggingFace:** search GGUF repos, pick a quant. Each file has an advisory fit hint
  for your GPU. Downloads land as `.part` and are renamed only when complete.
- The chat format is picked per model from the file itself (ChatML, Mistral Nemo, GLM, or
  KoboldCPP's AutoGuess).

`model.env` is read, never executed, so it is safe to edit by hand.

## Image and sound: ComfyUI

**Dashboard → Image and audio.** Files live under `ComfyUI/models/` in `checkpoints`, `loras`,
`vae`, `diffusion_models` and `text_encoders`.

- **Find on Civitai:** model → version → file. Many downloads need a token.
- **Find on HuggingFace:** each `.safetensors` gets a suggested folder; selecting a file reads its
  tensor index (not the weights) to tell a full checkpoint from a bare diffusion model.
- **Tokens:** put `CIVITAI_TOKEN=…` and/or `HF_TOKEN=…` lines in `stack-ui/tokens.env`
  (git-ignored, `chmod 600`) and rebuild the dashboard. A 401/403 says which one is missing.
  Tokens for private git repos are saved separately, per owner, with `stack credential add`
  ([Frontends](frontends.md)).
- ComfyUI loads models per job and keeps them, so it can hold GPU memory after a render.
  **Release VRAM** on its card (or **Release ComfyUI VRAM** in the VRAM planner) gives it back
  without a restart.

## Decisions: Fortuna

**Dashboard → Language models → Fortuna.** Fortuna serves only the files listed in
[`fortuna/models.table`](../../fortuna/models.table), each pinned to one HuggingFace commit with its
sha256 and its own calibration temperatures. The default is `jevk5-4b-q8` on AMD and
`jevk5-4b-q4` on the 12 GB NVIDIA card.

| Column | Meaning |
|---|---|
| Integrity: verified | its sha256 matched, and the file hasn't changed since |
| not checked yet | present but never hashed; it is hashed on Fortuna's next start |
| failed sha256 | wrong file; Fortuna refuses it. Delete it and download again |
| partial / not downloaded | a download stopped part-way / nothing there yet |

- **Use for Fortuna** writes `MODEL=` in `fortuna/fortuna.env` and keeps its other lines
  (e.g. `API_KEY`). It refuses a missing or failed file and asks first for an unchecked one.
- **Download** fetches the pinned file and checks it; **Verify** re-hashes a file you have. Both
  show progress in the console. From a terminal:

  ```bash
  stack fortuna-model                 # list the files
  stack fortuna-model fetch jevk5-4b-q4
  stack fortuna-model verify jevk5-4b-q8
  ```
- `stack doctor` reports whether the model Fortuna boots is present and verified.
- **GPU layers…** sets how much of Fortuna runs on the GPU (`NGL=` in `fortuna/fortuna.env`).
  Leave it empty for all layers (the default). **0 runs it on the CPU only** and frees its VRAM,
  about 3.3 GB for `jevk5-4b-q4`. On a 12 GB card that is the difference between no image work at
  all and an SD 1.5/SDXL checkpoint running next to the text model. Each question then takes a
  little longer, which suits Fortuna's short typed questions. It applies on Fortuna's next start,
  and the dashboard offers the restart.

## The VRAM planner

Every backend shares one GPU. The planner reads live memory from the host and estimates how many
layers of a GGUF fit next to a ComfyUI checkpoint **and** Fortuna's model (its file plus its
16K-context cache, in proportion to its GPU layers and nothing when it runs on the CPU; set it to
"not counted" to leave it out). A fixed allowance is held back for
the desktop. The numbers are estimates and say so. **Use … with N GPU layers** saves the result.
