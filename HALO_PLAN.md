# Halo plan: text inference and voice on the AMD Ryzen AI Halo box

Status: **Phase 1 done** (2026-10-29): router live, local copies renamed. Phases 2+ wait for the hardware.

## Goal

Move text inference (KoboldCPP) and Pantheon's voice (Orpheus TTS) to the AMD Ryzen AI Halo
developer box, and keep image, video and music generation (ComfyUI) on the RTX 4070. When the Halo
box is off or unreachable, text and voice fall back to the 4070 by themselves, and the frontends
never notice which machine is serving.

## Decisions (from the 2026-10-29 Q&A)

| Topic | Decision |
|---|---|
| Server on the Halo box | **KoboldCPP, Vulkan build.** Same API as now, so the frontends only see a URL. |
| Control | **Podman over SSH**: a `podman system connection` named `halo`. `stack` and the dashboard start, stop, swap and read logs on the Halo box the same way they do locally. |
| Halo OS | **Ubuntu** with rootless podman. |
| Fallback | **Automatic fallback to the 4070.** The local koboldcpp and orpheus-tts start only when needed. |
| Routing | **A router that keeps the old names.** A small proxy container answers as `koboldcpp:5001` and `orpheus-tts:5005` on `ai-net`, so the frontends need no changes. |
| Network | **Wired LAN** with a DHCP reservation. The Halo firewall accepts :5001 and :5005 only from this workstation. SSH uses key auth. |
| Models | **Kept on the Halo box's own NVMe.** The dashboard lists them and downloads to the Halo box over SSH. |
| Scope | Text LLM, Orpheus TTS, bigger models. STT and Kokoro are optional; see Phase 5. |

## What the code assumes today

- **Frontends** reach the backends by name on `ai-net`: `KOBOLD_URL=http://koboldcpp:5001` and
  `ORPHEUS_URL=http://orpheus-tts:5005`, set in `companion/run-podman.sh` and
  `ai_story_maker/run-podman.sh`. **The router keeps these working unchanged.**
- **Pantheon drives Orpheus**: it loads and unloads Orpheus with `/api/admin/reload_config`
  (`companion/services/orpheus.py`). The router must pass the admin routes through.
- **`koboldcpp/start.sh` is 4070-specific**: it waits on `nvidia-smi`, frees ComfyUI's cache,
  unloads Orpheus, retries in CUDA unified memory and reads the GGUF header from local disk. None
  of that applies on the Halo box, where memory is separate from the 4070.
- **The dashboard reads local files**: `stack-ui/appcode/services/backends.py` (`KOBOLD =
  localhost:5001`), `inventory.py`, `vram.py` (`gguf_meta(Path)`), `ui/llms.py` (writes
  `model.env`) and `downloads.py` all assume models are on this disk.
- **Kokoro and STT are not GPU backends.** Both run on the CPU inside the companion container:
  `companion/services/tts.py` (Kokoro onnx) and `stt.py` (faster-whisper). The
  `kokoro-82m` and `parakeet` GGUFs in `koboldcpp/models/` are unused.

## Target layout

```
                 ai-net (workstation)
  companion ─┐
  story ─────┼──► router  (aliases: koboldcpp, orpheus-tts; publishes 127.0.0.1:5001/5005)
  stack-ui ──┘      │
                    ├── primary ──► halo:5001 / halo:5005   (LAN, podman over SSH)
                    └── fallback ─► koboldcpp-local / orpheus-tts-local  (4070, started on demand)
  comfyui  (4070, unchanged)
```

- The **router** (`router/`, container `backend-router`) is one nginx container with the network
  aliases `koboldcpp` and `orpheus-tts`. Its upstream comes from `$ECO/router/route.conf`, which
  `stack route` writes, followed by `nginx -s reload`.
  It needs `proxy_buffering off` for streaming and long read timeouts for slow generations.
- The local containers are renamed **`koboldcpp-local`** and **`orpheus-tts-local`**.
- **`stack` sets the target; nginx does not health-check on its own.** A hot standby would hold
  ~8 GB of 4070 VRAM all the time, which defeats the point of offloading. So fallback means
  "start the local copy, then flip the router".

## Phases

### Phase 1: router and renames, local only — **done 2026-10-29**

1. Rename the local containers to `koboldcpp-local` and `orpheus-tts-local`, in
   `koboldcpp/start.sh`, `orpheus-tts/start.sh` and the `stack` registry (`CNAME`). Health
   probes go through the router.
2. Add `ai-stack/router/` with a Containerfile or stock nginx image, `nginx.conf`, a
   `start.sh` and a `CLAUDE.md`. Start it first in `cmd_up`.
3. Add `stack route [local|halo]` to write the upstream and reload the router. The current route
   shows on the `router` row of `stack status`.
4. Test with "fake Halo" pointed at the local containers. Pantheon chat, streaming, Orpheus
   load and unload, and Rhapsode must all work unchanged.

   **Result:** verified from inside the Pantheon and Rhapsode containers. Streaming arrives
   incrementally. Orpheus load and unload work through the router; a 502 while it restarts reads
   as "down". A route flip to a raw IP and back works, and the dashboard shows the `router` row.

### Phase 2: Halo provisioning (when the hardware arrives)

1. Install Ubuntu and rootless podman. Run `systemctl --user enable --now podman.socket` and
   `loginctl enable-linger`.
2. Set up a DHCP reservation and the hostname `halo`. Copy this workstation's SSH key to it.
   Add the connection on the workstation with `podman system connection add halo
   ssh://<user>@halo/run/user/<uid>/podman/podman.sock`.
3. Configure the firewall: `ufw allow from <workstation-ip> to any port 5001,5005 proto tcp`.
   Rootless podman's ports are ordinary user-space listeners, so ufw applies to them. Rootful
   Docker would bypass ufw.
4. **Raise the GPU memory limits.** Set BIOS "variable graphics memory" and check the amdgpu
   GTT/TTM limits so Vulkan can use most of the unified pool, not the default half. Measure
   before choosing model sizes.
5. Create a state directory, for example `/srv/ai/{koboldcpp,orpheus-tts}/{models,config}`.
   Mount paths on a remote podman connection resolve **on the Halo box**, the same lesson as
   stack-ui's real-path mount.

### Phase 3: Halo backends in `stack`

1. Add `ai-stack/halo/koboldcpp/`: a Containerfile using KoboldCPP's no-CUDA Linux build
   (Vulkan) and a `start.sh` that runs it with `podman --connection halo`, `--device /dev/dri`
   and `--group-add` for video and render, publishing `5001` on the LAN.
   - Keep the parts of the local `start.sh` that still apply: model.env parsing, the
     model-exists check, adapter selection (read the GGUF header with `ssh halo head -c`) and
     `await_load`.
   - Drop the nvidia-smi waits, ComfyUI `/free`, the Orpheus unload and the CUDA
     unified-memory retry.
2. Do the same for `halo/orpheus-tts/`: the same image in admin mode with no model loaded.
3. Add `ai-stack/halo.env` (git-ignored): `HALO_HOST`, `HALO_CONNECTION=halo`, the state dir and
   `TEXT_TARGET=auto|local|halo`.
4. Update `stack up`: start the router, then try the Halo box. If it doesn't answer, start the
   local copies and set the target to local. Extend `stack status`, `logs`, `down` and
   `restart` to the Halo containers.
5. Add Halo checks to `stack doctor`: SSH reachable, podman connection, `/dev/dri` present, image
   built, models present and firewall rule present.
6. **Failover watcher:** `stack failover-check` on a systemd user timer, every ~15 s.
   - After 3 failed Halo health probes: start the local copy and flip the router.
   - When the Halo box is back: switch back once text has been idle for N minutes, then stop the
     local copy to free the 4070.
   - Do this in a systemd timer, not in the dashboard, so it works when the dashboard isn't
     running.

### Phase 4: dashboard (stack-ui)

1. On the home screen, show where each backend runs: Halo, local fallback, or down. Add a
   manual override with Auto, Halo and Local.
2. Model inventory and the "Change model" action work per target. Halo models are listed with
   `ssh halo ls` and the GGUF header is read over SSH. Selecting a model writes the Halo box's
   `model.env`.
3. Downloads can target the Halo box: run the download inside a container through the `halo`
   podman connection, written straight to its NVMe.
4. VRAM planner: plan the Halo box against its unified memory pool and the 4070 against its 12
   GB. They are separate budgets.
5. Since the backends now live on two machines, check that stackui's mounts and the
   `nvidia-smi` passthrough from 5792a5d still make sense.

### Phase 5: optional, after Phase 3 is stable

- **Bigger models.** Strix Halo's memory bandwidth is far below the 4070's, so large *dense*
  models will be slow. Large **MoE** models are where it shines. Benchmark tokens/s before
  committing. New model families may need new chat adapters next to `chatml.json` and
  `mistral-nemo.json`.
- **Kokoro and STT.** Moving them means new network services, plus Pantheon client changes, to
  save CPU time that isn't a bottleneck today. Recommend skipping this unless Pantheon's container
  becomes CPU-bound.

## Open questions

1. **`stack off` (gaming mode):** should it also stop the Halo backends? Recommendation: **no**.
   The Halo box doesn't use the 4070, so text can stay up while you game. `stack off` stops only
   ComfyUI and any local fallback.
2. **Local fallback model:** the Halo box will run bigger models than the 4070 can hold, so the
   fallback needs its own selection. Recommendation: keep today's `koboldcpp/model.env`
   (Rocinante 12B) as the fallback model.
3. **Switching back:** is "back to Halo once idle" right, or should it switch back immediately and
   accept cutting off an in-progress reply?
4. **Failover gap:** the local load takes ~25–50 s. Is it acceptable for Pantheon and Rhapsode to
   error during that window, or should they retry and wait? Their clients would need a small
   change for that.
