# stack-ui — plan

A web frontend for the stack itself: run the `stack` commands, and browse/download/activate models
for the two backends without leaving the browser or hand-editing anything.

Status: **phases 0–3 built** — status/control/logs, model inventory with selection and delete, and
HuggingFace search + downloads. Lives at `github.com/El-Iguana/stack-ui`; the running notes are in
`stack-ui/CLAUDE.md`, which is the authority where this plan and the code disagree. Phase 4
(Civitai) is next.

## A container that drives the host

> **Superseded.** This was planned host-side and built that way first, on the reasoning that
> `stack` drives `podman` *and* `systemctl --user` and so couldn't be containerized. That was
> wrong — it's harder, not impossible. stack-ui is a container like everything else.

What it took, since none of it is obvious:

- **podman** → mount the host socket; ship the podman *client* only (static `podman-remote`,
  version-pinned to the host engine).
- **health probes** against `localhost:5001` → `--network=host`, or every app reads as down.
- **paths** → mount the ecosystem at its **real host path**. A remote client has the *host* engine
  resolve `-v` and build-context paths, so a container-only path starts apps with broken mounts,
  silently, exit 0.
- **`systemctl --user`** → genuinely cannot work: it only uses systemd's private socket, which
  refuses a cross-userns peer. Worked around with a `systemctl`→D-Bus shim on the session bus, plus
  `--userns=keep-id` so the bus's uid check passes.

The `kind=proc` branch in `stack` therefore goes back to having no users. Details in
`stack-ui/CLAUDE.md`.

Consequence: it is the **most privileged thing in the ecosystem** — it can stop your backends, write
into your models dirs, and pull files off the internet. Security section below is not boilerplate.

## Architecture

```
stack-ui/                      # sibling project, like the other frontends
  server.py                    # FastAPI — command layer + catalog layer + job layer
  services/
    stackctl.py                # allowlisted `stack` invocations   [built]
    jobs.py                    # single-slot action runner          [built]
    inventory.py               # models on disk, select, delete     [built]
    hub.py                     # HuggingFace search / file listing  [built]
    downloads.py               # transfers, progress, resume        [built]
    civitai.py                 # Civitai search                     [phase 4]
  shim/systemctl               # systemctl -> D-Bus, so `stack on/off` works in a container
  static/ templates/           # vanilla JS SPA, no build step (house style)
  run.py                       # uvicorn on 127.0.0.1:7863
  Containerfile · run-podman.sh · entrypoint.sh
```

Registry row:
`reg stackui 7863 "http://127.0.0.1:7863/api/health" "Stack control dashboard" container stackui`

Reuse rather than reinvent: `iguana_studio/comfyui-auto/server.py:191` already lists ComfyUI
checkpoints off `/object_info/CheckpointLoaderSimple`, and companion's SSE chat stream is the
progress-streaming pattern to copy.

## Layer 1 — running stack commands

**No shell, ever.** `subprocess.run(["stack", verb, arg])` with a list, never a string, and both
`verb` and `arg` validated against fixed sets — verbs from a hardcoded allowlist, app names parsed
out of `stack status` rather than taken from the request.

| Verb | Exposed as |
|---|---|
| `status`, `doctor` | dashboard poll (read-only, safe to hit often) |
| `on`, `off` | the big switch — the main reason this exists |
| `up`, `down [target]`, `restart <app>` | per-app buttons |
| `logs <app>` | SSE tail (`podman logs -f`, killed when the client disconnects) |
| `install [target]` | "repair" button; idempotent, so safe to expose |

Deliberately **not** exposed: anything taking free text. There's no command box.

## Layer 2 — browsing models

huggingface.co can't be iframed (framing headers block it), so this is our own UI over their API.
No key needed for public repos:

```
GET https://huggingface.co/api/models?search=<q>&filter=gguf&sort=downloads&direction=-1&limit=50
GET https://huggingface.co/api/models/<repo_id>?blobs=true      # per-file names + sizes
```

`filter=gguf` is what makes this usable for Kobold — it returns the quant repos directly, and the
per-file sizes are the whole point, because choosing a GGUF *is* choosing a quant that fits 12 GB.
(Confirm the exact blobs/size parameter at implementation time; `huggingface_hub`'s
`model_info(..., files_metadata=True)` is the equivalent and is the safer path anyway — it brings
resumable downloads, caching and gated-repo tokens with it. It's not currently installed.)

For ComfyUI checkpoints the equivalent is Civitai, since that's where most of the list in
`comfyui/INSTALL.md` §8 actually comes from:

```
GET  https://civitai.com/api/v1/models?query=<q>&types=Checkpoint&baseModels=SD%201.5
GET  https://civitai.com/api/download/models/<versionId>     # Authorization: Bearer <key>
```

Civitai increasingly requires a key for downloads. It goes in a gitignored file next to the app —
never in the repo, never in a query string that lands in logs.

**Fit hints, shown next to every file:**
- *Disk* — free space vs file size. Currently 346 GB free, with 15 GB of GGUFs and 70 GB of ComfyUI
  models already down; SDXL checkpoints are ~6.5 GB each and this fills faster than it feels.
- *VRAM* — for GGUF, a heuristic band, not a promise: file size plus ~1–1.5 GB of KV cache at 8k
  context against the 12 GB card, rendered as **fits / tight / partial offload**, with a suggested
  `GPU_LAYERS`. Both backends share the card, so the hint should assume ComfyUI wants ~2–4 GB
  alongside (that pairing rule is already in both INSTALL guides).

## Layer 3 — downloading and "installing"

Downloads are long, so: a job per download, run in a thread, progress over SSE, survivable page
reloads (jobs live in the server, the page just re-attaches).

```
POST /api/models/download  {source: hf|civitai, repo/versionId, file, target: kobold|comfyui-checkpoint|comfyui-lora|comfyui-vae}
GET  /api/jobs             list + state
GET  /api/jobs/<id>/events SSE: bytes, total, speed, eta, state
DELETE /api/jobs/<id>      cancel (and unlink the partial)
```

Destination routing — the bind mounts already make this the easy half:

| Target | Path | Visible to the backend |
|---|---|---|
| Kobold GGUF | `$ECO/koboldcpp/models/` | on next start (`model.env` + restart) |
| ComfyUI checkpoint | `$ECO/ComfyUI/models/checkpoints/` | after a rescan; `stack restart comfyui` is ~5 s |
| ComfyUI LoRA / VAE | `$ECO/ComfyUI/models/{loras,vae}/` | same |

**Path safety is not optional here.** The filename comes from a remote API, so it gets basenamed and
validated (no separators, no `..`, expected extension) before it is joined to a destination — the
download layer must not be able to write outside the models dirs. Download to `name.part` and rename
on completion, so a killed transfer can't look like a usable model.

## Layer 4 — activating what you downloaded

- **Kobold**: write `koboldcpp/model.env` (`KOBOLDCPP_MODEL` + a `KOBOLDCPP_GPU_LAYERS` suited to the
  quant), then `stack restart koboldcpp`. Already supported — `start.sh` parses that file strictly,
  and fails fast with the available list if the name is wrong. ~20–30 s to reload.
- **ComfyUI**: nothing to activate — the file is in the bind-mounted tree, so it appears in the
  checkpoint list after a rescan. Offer a restart button for when it doesn't.

A "models" page listing what's installed (name, size, source, active flag) with delete is worth as
much as the download side — 85 GB of models accumulate without any inventory today.

## Security posture

Concentrated here because this app deserves it:

- **Bind `127.0.0.1` only.** Not `0.0.0.0`. (Companion publishes on all interfaces; do not copy that
  here — this one can stop services and write files.)
- No shell interpolation; allowlisted verbs; app names validated against the registry.
- Path-sanitize every remote-supplied filename before it touches a destination path.
- Tokens (Civitai, HF for gated repos) in a gitignored file, `chmod 600`, never echoed back to the
  browser, never in URLs.
- No auth initially *because* it's loopback-only. The moment it binds anything else, it needs auth
  first — companion's `services/auth.py` (scrypt + HMAC cookie, stdlib only) drops straight in.

## Phases

- **Phase 0 — read-only dashboard. ✅ DONE.** `status` + `doctor` + disk + installed-model
  inventory. No mutations — enforced by the verb allowlist, not by the UI omitting buttons.
- **Phase 1 — control. ✅ DONE.** on/off/up/down/restart/start + SSE log tail, single-slot action
  runner, Host/Origin guards. At this point it already replaces most terminal use.
- **Phase 2 — inventory management. ✅ DONE.** Model list with sizes, delete, and Kobold model switching via
  `model.env` (no downloading yet — activation is independently useful).
- **Phase 3 — HuggingFace. ✅ DONE.** Search, file picker with fit hints, download jobs with
  resume, auto-activate. Built on stdlib urllib — see the reversal note in `stack-ui/CLAUDE.md`.
- **Phase 4 — Civitai** for ComfyUI checkpoints, then LoRA/VAE targets.
- **Later:** VRAM planner across both backends; per-model layer presets remembered per file; custom
  node pins from `stack`'s `CUSTOM_NODES` registry surfaced as an update button.

## Non-goals

Not a ComfyUI workflow editor (that's iguana_studio) and not a chat UI (companion). No arbitrary
command execution. No remote access — if that's ever wanted, it's a VPN/reverse-proxy question with
auth in front, not a bind-address change.

## Open decisions

1. **`huggingface_hub` dependency, or plain `httpx`?** The library gives resume, caching and gated
   repos free; httpx matches the rest of the ecosystem and adds no dependency. Leaning library — the
   download path is the part worth not writing twice.
2. **Does stack-ui manage itself?** If it's in the registry, `stack down all` stops the thing you're
   clicking in. Probably exclude it from `all` and treat it like the systemd unit: managed from the
   terminal.
3. **`stack model <name>` CLI verb?** Writing `model.env` from the CLI too would keep the terminal
   and the UI at parity, and it's ~10 lines.
