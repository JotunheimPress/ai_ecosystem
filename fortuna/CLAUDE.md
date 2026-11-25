# Fortuna — typed decisions for the frontends

**Fortuna** answers typed questions about a piece of text (the *state*): yes/no (`noul`), pick one
(`choice`), or a level on a scale (`score`), each as calibrated probabilities. Nothing is generated.
It speaks TypeSafe's `/v1/systemone` shape, so a frontend asks it instead of prompting KoboldCPP for
a label and regex-parsing the reply (emotion of a reply, "is the AI writing the user's character?",
player intent, does this need a roll…). Added 2026-11-17.

- **Name everywhere is `fortuna`** — `stack fortuna`, `http://fortuna:8009` on ai-net — not the
  engine's name. Since 2026-11-19 that address is the backend router's; the container is
  `fortuna-local` with no host port, and the router publishes `127.0.0.1:8009`. The engine behind it is swappable; frontends never know which.
- **Engine today: JevK5** (`allebee/jevk5`, Apache-2.0): Qwen3.5-4B + a distilled LoRA, as a GGUF on
  llama.cpp. Chosen over Kev (`jaredpalmer/kev`) because Kev needs PyTorch (`<2.9`, i.e. 2.8 on
  ROCm), which carries an unfixed checkpoint-loading code-execution advisory (fixed in 2.10, outside
  Kev's range), while a GGUF holds no code that runs when loaded and this path has no Python dependencies at all.
  Kev's evaluation is better documented (locked test 0.838 OOD); JevK5's numbers are self-reported.
  Swapping engines = a new recipe behind the same name and API.

## Files

| File | What |
|---|---|
| `Containerfile` | `FROM` the official llama.cpp server image (b11146, pinned **by digest**, one per GPU runtime — `lib/host.sh` `fortuna_base_image`), plus JevK5's three stdlib files fetched at commit `f944fe3` (tag v0.3.3) and **sha256-checked**. No pip, no apt. |
| `server.py` | The adapter: `POST /v1/systemone`, `GET /v1/models`, `GET /health`. Stdlib only. Request/response shape follows `jevk5/server.py` (which imports torch, so it can't be used on this path). |
| `entrypoint.sh` | Runs llama-server on **127.0.0.1:8080 inside the container** + the adapter on :8009; if either dies both stop and podman restarts the container. |
| `models.table` | The files Fortuna will serve: name, file, sha256, size, calibration temperatures, and which GPU boots it by default. Pinned to one Hub commit. Read by `start.sh` **and** stack-ui, so the two can't disagree. |
| `USE_CASES.md` | What the frontends use it for (Whimsy, Cantinero, World Builder) and what each gained, measured. How the apps were changed: the wiki's *Decision models* page (`docs/wiki/decision-models.md`). |
| `start.sh` | The recipe: run, `--fetch [NAME]`, `--verify [NAME]`, `--check`, `--models`. State in `$ECO/fortuna/` (`models/`, `fortuna.env`). |

## Models

`models.table` lists four files from `alibiserikbay/JevK5-GGUF` at Hub commit `ec67b0b`, each with
its sha256, its size and **its own calibration temperatures** (from the model card — the wrong pair
silently mis-calibrates every probability). Default: `jevk5-4b-q8` (4.5 GB; 229/231 answers
identical to bf16) on AMD, `jevk5-4b-q4` (2.7 GB) on the 12 GB 4070, where it shares VRAM with
KoboldCPP — the table's last column, one row per GPU. Pick another with `MODEL=` in
`$ECO/fortuna/fortuna.env`, or in the dashboard: **Language models → Fortuna** (it writes that line
and keeps the rest of the file). Selecting never restarts; the tab and the dashboard's decision card
offer the restart when Fortuna has another model loaded. Adding a file = a row in the table.

**Weights are fetched only by `start.sh --fetch NAME`** (`stack fortuna-model fetch NAME`, or
Download in the dashboard): explicit, pinned commit, into a `.part`, then sha256-checked.
`stack install` never downloads them, as for every backend. A complete download whose hash is wrong
is **discarded**, not kept as a `.part` (resuming it would only append to the wrong bytes).

### Verification

Two stamps next to each file, both `<size> <mtime> <sha256>` of the file as it was hashed:
`<file>.verified` (matched) and `<file>.mismatch` (didn't). A stamp vouches for the file only while
its size and mtime still match, so a file changed after hashing reads as *unchecked* again.

- **Written by** `--fetch`, `--verify NAME` (`stack fortuna-model verify NAME`, Verify in the
  dashboard), and the first start with a file. A start trusts a matching `.verified` (hashing
  4.5 GB on every `stack up` would cost seconds for nothing), refuses a matching `.mismatch`, and
  hashes anything else first.
- **Read by** `start.sh --check` (one line about the configured model; exit 0 only when present and
  verified), which `stack doctor` and `stack install fortuna` print, and by stack-ui
  (`inventory.fortuna_models`), which shows each file as verified / not checked yet / failed
  sha256 / partial / not downloaded. Neither ever hashes.
- The dashboard refuses to select a file that is missing or failed its check, asks before selecting
  one that is unchecked, and refuses to delete the file Fortuna boots or has loaded.

## Security

- Host port **127.0.0.1:8009 only**, regardless of `BIND` — frontends use ai-net. llama-server is
  not reachable from ai-net at all (loopback inside the container; verified: connection refused).
- Request bounds in `server.py`: 1 MB body, 32 questions, 128 options per question; states past the
  16,384-token context come back as 422. Requests are serialised (one llama-server slot).
- `API_KEY=` in `fortuna.env` turns on `Authorization: Bearer` for `/v1/*` (`/health` stays open for
  `stack status`). Off by default: nothing off this machine can reach it.
- Request bodies are never logged — states hold story text.

## API

```bash
curl -s localhost:8009/v1/systemone -H 'Content-Type: application/json' -d '{
  "state": "I swing my sword at the goblin!",
  "questions": {
    "intent": {"type": "choice", "instructions": "What is the player trying to do?",
               "criteria": {"attack": "fight or harm", "talk": "converse", "sneak": "stay hidden"}},
    "roll":   {"type": "noul",  "instructions": "Does this need a skill check?"},
    "heat":   {"type": "score", "instructions": "How intense is the scene?",
               "criteria": ["calm", "mild", "heated", "explosive"]}}}'
```

Answers: `noul` → `noul` (P(true)); `choice` → `choice` + `probabilities`; `score` → `score`
(expected level index) + `probabilities`; each with `confidence`. More than 16 options costs
ceil(n/16)+1 passes (JevK5's knockout).

## Performance (Halo, measured 2026-11-17)

Each question is one llama-server evaluation of the whole prompt (JevK5's client sends
`cache_prompt: false`), so cost grows with state length. With the GPU otherwise idle:
**~0.1 s for a ~120-token prompt, ~0.3 s for ~500 tokens**; Whimsy's 28-label emotion question
(3 passes) **~0.7 s**. KoboldCPP shares the GPU, so while it generates, everything here is slower
(2–3x seen) — check `/sys/class/drm/card0/device/gpu_busy_percent` before trusting a measurement.

**Fixed: `--ctx-checkpoints 0` (entrypoint.sh).** Before it, the llama-server Fortuna started took
~0.36 s / ~0.57 s for the same prompts (emotion: ~2.6 s), while a *second* llama-server started
next to it ran at full speed. How it was found:
- The GPU kernels took the same time in both (`GGML_VK_PERF_LOGGER=1`); the slow process spent
  ~250 ms more **user-space CPU** per evaluation, on its main thread.
- The slow process was the only one holding any of the Halo's 512 MiB VRAM carve-out (~52 MB,
  which is the size of the model's recurrent-state buffer), but moving memory to the host
  (`GGML_VK_PREFER_HOST_MEMORY`, `GGML_VK_DISABLE_HOST_VISIBLE_VIDMEM`) changed nothing.
- Turning off llama-server's context checkpoints — snapshots of Qwen3.5's recurrent state saved
  after each prompt — took the first process from 358 ms to 94 ms. Fortuna never restores a
  checkpoint (no prompt caching), so they were pure cost. Why only the first process paid it is
  not established; with checkpoints off it no longer matters.
`--cache-ram 0` was added with it: the host-RAM prompt cache (8 GiB default) is unused too.
Ruled out along the way: flash attention, ubatch size, context size, log driver, mmap / load
mode, process attributes (affinity, nice, cgroup, env), `n_probs`.
