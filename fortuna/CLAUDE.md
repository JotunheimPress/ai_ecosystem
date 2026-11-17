# Fortuna — typed decisions for the frontends

**Fortuna** answers typed questions about a piece of text (the *state*): yes/no (`noul`), pick one
(`choice`), or a level on a scale (`score`), each as calibrated probabilities. Nothing is generated.
It speaks TypeSafe's `/v1/systemone` shape, so a frontend asks it instead of prompting KoboldCPP for
a label and regex-parsing the reply (emotion of a reply, "is the AI writing the user's character?",
player intent, does this need a roll…). Added 2026-11-17.

- **Name everywhere is `fortuna`** — `stack fortuna`, container `fortuna`, `http://fortuna:8009` on
  ai-net — not the engine's name. The engine behind it is swappable; frontends never know which.
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
| `start.sh` | The recipe: model table, `--fetch`, `--models`, run. State in `$ECO/fortuna/` (`models/`, `fortuna.env`). |

## Models

`start.sh` knows four files from `alibiserikbay/JevK5-GGUF` at Hub commit `ec67b0b`, each with its
sha256 and **its own calibration temperatures** (from the model card — the wrong pair silently
mis-calibrates every probability). Default: `jevk5-4b-q8` (4.5 GB; 229/231 answers identical to
bf16) on AMD, `jevk5-4b-q4` (2.7 GB) on the 12 GB 4070, where it shares VRAM with KoboldCPP. Pick
another with `MODEL=` in `$ECO/fortuna/fortuna.env` (`start.sh --models` lists them).

**Weights are fetched only by `start.sh --fetch`** (explicit, pinned commit, hash-checked into a
`.part` first) — `stack install` never downloads them, as for every backend. A file is hashed on its
first start and trusted afterwards while its size+mtime match `<file>.verified`.

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

## Performance (Halo, measured 2026-11-17) — open issue

Each question is one llama-server evaluation of the whole prompt (JevK5's client sends
`cache_prompt: false`), so cost grows with state length. Measured: **~0.36 s for a ~120-token
prompt, ~0.56 s for ~500 tokens**, on the GPU (33/33 layers on Vulkan0, 2 graph splits).

**But a second llama.cpp process does the same work in 0.12 s / 0.32 s.** Reproduced with
throwaway containers from the same image: whichever llama.cpp server starts *first* (with KoboldCPP
also on Vulkan) pays ~240 ms extra per evaluation; any started after it doesn't, and the first
stays slow even while the others run. Not the cause (each tested): flash attention, ubatch size,
context size, log driver, mmap / load mode, process attributes (affinity, nice, cgroup, env),
`n_probs`, prompt caching. Not yet tried: with KoboldCPP stopped, the ROCm base image, RADV env
knobs. Until then a request with three questions on a short state takes ~1–1.5 s.
