# Orpheus TTS — on-demand expressive voice

Canopy Labs' **Orpheus TTS 3B** (finetuned, Q4_K_M GGUF) served by **KoboldCPP in admin mode** for
Pantheon's voice. It sits idle with **no model loaded and no VRAM** until a voice that uses it is
switched on, and gives the ~3 GB back when voice is switched off.

## Structure

- `start.sh` — starts container `orpheus-tts` from the **koboldcpp image** (no image of its own) on
  `ai-net`, port `5005` published on **127.0.0.1 only**. Exports `config/orpheus.kcpps` with
  KoboldCPP's own `--exportconfig` on first run or when the model changes.
- State (never committed) lives in **`~/Development/ai_ecosystem/orpheus-tts/`**:
  `models/orpheus-3b-0.1-ft-q4_k_m.gguf` (2.4 GB, from
  `isaiahbjork/orpheus-3b-0.1-ft-Q4_K_M-GGUF`) and `config/`.

## Running

```bash
stack orpheus-tts            # or: ai-stack/orpheus-tts/start.sh — part of `stack up` (a backend)
curl localhost:5005/api/v1/model                     # {"result":"inactive"} = idle, no VRAM
curl -X POST localhost:5005/api/admin/reload_config -H 'Content-Type: application/json' \
     -d '{"filename":"orpheus.kcpps"}'               # load   (~3 s cached, ~15 s cold, +3 GB)
curl -X POST localhost:5005/api/admin/reload_config -H 'Content-Type: application/json' \
     -d '{"filename":"unload_model"}'                # unload
```

| var | default | what |
|---|---|---|
| `ORPHEUS_STATE` | `$ECO/orpheus-tts` | models/ + config/ |
| `ORPHEUS_MODEL` | `orpheus-3b-0.1-ft-q4_k_m.gguf` | GGUF in `models/` |
| `ORPHEUS_PORT` | `5005` | listen + loopback publish |
| `ORPHEUS_IDLE_UNLOAD` | `600` | s without a request before KoboldCPP unloads by itself |

## Who loads and unloads it

- **Pantheon** (`companion/services/orpheus.py`): `voice.js` asks for a load when the speaker or
  hands-free is on for a card whose voice is `orpheus:<name>`, and a release 20 s after that stops.
  A line requested while unloaded loads it first. A failed load (out of VRAM) is detected from the
  admin API (idle → down → idle) in ~5 s; lines then fall back to a Kokoro voice for 60 s.
- **`koboldcpp/start.sh`** unloads it before loading the chat model and waits for the memory.
- **The idle timeout** covers a closed browser tab. Note it counts from the last *generate request*,
  not from a load — Pantheon sends a 1-token request before and after each load, or a server idle
  past the timeout unloads the model a second after loading it.

## Format (Canopy's engine)

Prompt `<custom_token_3><|begin_of_text|>{voice}: {text}<|eot_id|><custom_token_4><custom_token_5><custom_token_1>`,
temperature 0.6, top_p 0.8, rep_pen 1.3, stop `<custom_token_2>`. Output is `<custom_token_N>` SNAC
codes, 7 per frame; Pantheon decodes them on the CPU with the SNAC 24 kHz ONNX decoder. Voices:
tara, leah, jess, mia, zoe, leo, dan, zac. Inline tags it voices: `<laugh> <chuckle> <sigh> <gasp>
<cough> <sniffle> <groan> <yawn>`.

Measured 2026-10-28 (RTX 4070, Rocinante 12B loaded alongside): ~140 tok/s, **~1.5× real time**
per sentence; ~3 GB VRAM loaded, 0 idle (60 MB RAM). The admin API has no password — it is only
reachable on loopback and `ai-net`.

Not to be confused with **Orpheus**, the media server project (`~/Development/ai_ecosystem/orpheus`).
