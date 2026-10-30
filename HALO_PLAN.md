# Halo plan: the whole AI stack on the AMD Ryzen AI Halo box

Status: **Phase A done 2026-10-30** (host profiles, AMD images built, NVIDIA unchanged).
Phase 1 (router, 2026-10-29) is done and still used. Phases B+ wait for the hardware.

Hardware: AMD Ryzen AI Halo (Strix Halo, Zen 5), **Radeon 8060S** iGPU (RDNA 3.5, **gfx1151**),
**128 GB unified memory** shared by CPU and GPU.

## Goal

The Halo box can run the whole AI stack: every backend (KoboldCPP, Orpheus TTS, ComfyUI), every
frontend (Pantheon, Rhapsode, Iguana Studio, Nocturne) and the AI Ecosystem dashboard, with their
data. **It is an option to switch to, not a replacement:** the NVIDIA install on the workstation
stays fully supported and runnable, and the same checkout runs on both (host profiles, Phase A).
Running on Halo frees the 4070 for games.

## Decisions

| Topic | Decision (2026-10-30 unless noted) |
|---|---|
| What runs on Halo | **Everything**: backends, frontends, dashboard, and their data. |
| Halo OS | **Preinstalled: AMD's "Rex"** (Ryzen AI Developer Platform 1), based on Debian 13 (Trixie). Reported in its Sept 2026 update to ship Linux 7.2.7 and ROCm 10.0 (one source, not yet confirmed with AMD). Kept if `stack doctor` passes on it. Replaces the earlier Ubuntu 24.04 choice. |
| GPU runtime | **ROCm for ComfyUI** (PyTorch has no other path). **KoboldCPP and Orpheus: build both ROCm and Vulkan, benchmark, keep the winner as default** and the other switchable. |
| How Halo is run | **Its own `stack`**: a clone of ai-stack on the Halo box, driven locally there (CLI + its own dashboard). Replaces the 2026-10-29 plan of driving a remote podman over SSH from the workstation, which only made sense while the frontends stayed here. |
| Access from the workstation | **SSH tunnel** (`stack tunnel`, Phase D). Pantheon and Iguana only sign in or serve on loopback, and a tunnel makes the browser's address `127.0.0.1` again, so they work unchanged. TLS (Pantheon's PYTINCTURE_PLAN Phase 8) can replace it later. |
| Network | Wired LAN, DHCP reservation, hostname `halo`, SSH key auth only (2026-10-29). |
| Router | **Kept** (Phase 1). On Halo it routes `koboldcpp` / `orpheus-tts` to the ROCm or Vulkan copy, which is what makes the runtime switchable without touching the frontends. |
| NVIDIA install | **Kept as a first-class option** (2026-10-30). Same checkout, NVIDIA profile, unchanged behaviour. Switching between hosts is choosing which stack you run and which data copy is current (Phase D). No automatic text failover: with the frontends on Halo, "Halo is down" means the apps are down too. |

## Kernel and firmware (check first)

- **gfx1151 needs a recent kernel.** Community reports put the floor for a stable ROCm at
  **6.18.4**; earlier kernels crash or hang under load. Rex reportedly ships 7.2.7, which clears
  it; `stack doctor` checks `uname -r` on the box. The containers bring their own ROCm userspace
  (7.13/7.14), so the host's ROCm 10 matters only for its kernel driver; `ROCM_IMAGE` lets the
  benchmark try a 10.x build image too.
- **Avoid `linux-firmware-20251125`**: reported to break ROCm on Strix Halo.
- Debian confines containers with AppArmor, not SELinux; `lib/host.sh`'s AMD flags include
  `seccomp=unconfined` and `keep-groups` (for `render`/`video`). Check podman's version on Rex.
- **Unified memory for the GPU:** by default ROCm sees about half of the 128 GB. Set BIOS UMA
  (dedicated VRAM) to its minimum and raise the TTM limits on the kernel command line, e.g.
  `ttm.pages_limit=27648000 ttm.page_pool_size=27648000` (~105 GB, 4 KiB pages). `amdgpu.gttsize`
  is the older way and may be ignored. Put these in GRUB, not modprobe.d (reported not to take).
  Leave ~16 GB for the OS and the CPU-side apps.
- **llama.cpp on AMD APUs:** never load with mmap under ROCm (reported catastrophic above half
  of memory). This KoboldCPP version has mmap off unless `--usemmap` is passed; `--nommap` doesn't
  exist. Watch llama.cpp issue #18159 (APUs misdetected as UMA, capping usable memory at
  `MemAvailable`).

## Expectations

- **Memory bandwidth is about half the 4070's** (~256 vs ~504 GB/s). Today's dense 12B
  (Rocinante) will generate **slower** on Halo than on the 4070. Halo wins on **size**: big
  MoE models (tens of GB, few active parameters) that the 4070 can't hold at all, long
  contexts, and Flux/video models at full precision.
- **ComfyUI compute is slower** than the 4070 per image (40 RDNA 3.5 CUs vs Ada). In exchange the
  12 GB workarounds go away: Flux Kontext can run fp8/fp16 instead of the GGUF, and ComfyUI and
  KoboldCPP no longer evict each other.
- Benchmark before committing a default (Phase B).

## What has to change in the code

Measured on the workstation, 2026-10-30. **The frontends have no GPU code at all** (Pantheon's
TTS, STT, embeddings and SNAC run on the CPU) and Halo is x86-64, so their images build and run
unchanged. Everything GPU-specific is in the three backend recipes, `stack` and the dashboard:

| Where | NVIDIA / single-host assumption | Change |
|---|---|---|
| `koboldcpp/Containerfile` | `nvidia/cuda` base, `--usecublas` | Add `Containerfile.vulkan` (official binary + Mesa RADV) and `Containerfile.rocm` (YellowRoseCx `koboldcpp-rocm`, built for gfx1151). Tags `koboldcpp:cuda`, `:vulkan`, `:rocm`. |
| `koboldcpp/start.sh` | `--device nvidia.com/gpu=all`, `nvidia-smi` VRAM waits, CUDA unified-memory retry, default 33 GPU layers, ComfyUI `/free`, Orpheus unload | Pick device flags and backend flag (`--usecublas` / `--usevulkan`) from the host profile. On AMD: all layers on the GPU, `--nommap` for ROCm, VRAM waits read `/sys/class/drm/card*/device/mem_info_*` or skip, no CUDA UM retry. The ComfyUI / Orpheus unloads stay but matter far less. |
| `orpheus-tts/start.sh` | nvidia device, `--usecublas` in the exported `.kcpps` | Same host profile; regenerate the `.kcpps` per backend. |
| `comfyui/Containerfile` | cu130 torch wheels; GGUF node justified by 12 GB | `Containerfile.rocm`: torch from AMD's gfx1151 wheel index (`repo.amd.com/rocm/whl/gfx1151/`) or TheRock nightlies; its own requirements lock (torch line differs). insightface/onnxruntime stay CPU. |
| `comfyui/start.sh` | `--device nvidia.com/gpu=all`, `label=disable` | AMD: `--device /dev/kfd --device /dev/dri --group-add keep-groups`, plus `--security-opt seccomp=unconfined` if ROCm needs it (Ubuntu uses AppArmor, not SELinux). |
| `stack` (`check_prereqs`, `doctor`) | `nvidia-ctk` CDI check | Per profile: AMD checks `/dev/kfd`, `render`/`video` group membership, kernel ≥ 6.18.4, TTM limit, ROCm visible in a test container. |
| `stack on/off` | "free the GPU to game" | On Halo, `off` just stops backends; wording per host. |
| `stack-ui/run-podman.sh` | NVIDIA CDI to get `nvidia-smi` | AMD: no injection; read memory from sysfs / `amd-smi`. |
| `stack-ui` VRAM planner, `vram.py` | ComfyUI `/system_stats` as the only GPU view; 12 GB budgets | Unified-memory budget (TTM limit minus OS headroom); one pool shared by both backends. |
| Context sizes (`start.sh`, stack-ui `CONTEXT_SIZES`, Pantheon `models/schema.py`) | Max 32K, sized for 12 GB | Add 49152 / 65536 / 131072. Lists must stay identical (stack-ui's test checks start.sh). |
| Ports bound on all interfaces | router :5001, ComfyUI :8188, Rhapsode :7860, Nocturne :7864 | Fine on a workstation; **on a LAN server they're open without a login.** Halo profile binds them to 127.0.0.1 (the tunnel reaches them), plus `ufw default deny incoming` with only SSH allowed. |
| Data that moves | — | ComfyUI models 120 GB, KoboldCPP models 30 GB, Orpheus 2.3 GB, `companion/data` 151 MB, Rhapsode, Iguana, Nocturne outputs, `jarvis` (Piper voices). Rhapsode mounts Pantheon's data read-only, so the two must stay on one host (they will). |
| Pantheon desktop avatar | `/desktop` on `127.0.0.1:7861` | The GTK shell stays on the workstation and points at the tunnelled port. |

## Phases

### Phase 1: router and renames — **done 2026-10-29**

Router container `backend-router` owns `koboldcpp:5001` / `orpheus-tts:5005`; the copies run as
`*-local`. Verified with Pantheon, Rhapsode and Orpheus load/unload. On Halo it switches between
the ROCm and Vulkan copies.

### Phase A: host profiles — **done 2026-10-30** (on the workstation)

Make the stack portable before the box arrives, and prove nothing changes on the 4070.

**Result:**
- `lib/host.sh` + `host.env` (see `host.env.example`). Detection checks NVIDIA's CDI first: this
  workstation also has `/dev/kfd` (the CPU's integrated graphics), so `/dev/kfd` alone would
  have called it AMD.
- **NVIDIA unchanged, verified:** router, koboldcpp, orpheus-tts and comfyui restarted through
  the new scripts with a byte-identical `podman run` configuration (image, command, devices,
  security options, ports); chat answers at 16K context; ComfyUI on `cuda:0`. `stack doctor`
  reads as before.
- **The dashboard passes `STACK_GPU`** into its container. Found while doing this: the start
  scripts it runs can't detect the GPU from inside it and would have started KoboldCPP with no
  GPU at all.
- Images built here: `koboldcpp:vulkan` (0.5 GB; ran end to end on the CPU Vulkan device),
  `koboldcpp:rocm` (20.5 GB, compiled for gfx1151, hipBLAS library present, starts),
  `comfyui:rocm` (8.4 GB, torch 2.11.0+rocm7.13.0 imports, ComfyUI initializes on the CPU).
  None can touch an AMD GPU until Phase B.
- Context sizes 48K/64K/96K/128K added to all three lists.
- Not done here: binding the frontends' own ports (Rhapsode, Nocturne) to `BIND`; that lives
  in their repos and belongs with Phase C.

1. `ai-stack/host.env` (git-ignored, parsed never sourced): `GPU=nvidia|amd`,
   `LLM_BACKEND=cuda|vulkan|rocm`, `BIND=0.0.0.0|127.0.0.1`. Auto-detected when absent
   (`nvidia-ctk` CDI → nvidia; `/dev/kfd` → amd).
2. One helper for device flags per GPU, used by the koboldcpp, orpheus-tts, comfyui and
   stack-ui scripts, replacing the four hardcoded `--device nvidia.com/gpu=all`.
3. Vendor-aware `check_prereqs` / `doctor`.
4. The three new Containerfiles (Vulkan KoboldCPP, ROCm KoboldCPP, ROCm ComfyUI). The Vulkan
   KoboldCPP image can be smoke-tested here (Vulkan runs on the 4070); the ROCm ones can only be
   built here.
5. Larger context sizes in all three lists.
6. **Check:** the workstation stack still starts, chats and generates images exactly as before.

### Phase B: Halo provisioning (hardware)

1. Keep the preinstalled Rex if `stack doctor` passes (kernel, `/dev/kfd`, groups, TTM). Rootless podman,
   `systemctl --user enable --now podman.socket`, `loginctl enable-linger`, user in `render`
   and `video`.
2. BIOS UMA minimum; TTM kernel parameters; reboot; confirm the GPU sees ~105 GB
   (`rocminfo`, `vulkaninfo`, a test container).
3. DHCP reservation, hostname `halo`, workstation SSH key, firewall: deny incoming except SSH
   from the workstation (Debian: `nftables`, or install `ufw`).
4. Clone ai-stack and the frontends as siblings, `stack install`, copy the models.
5. **Benchmark** KoboldCPP ROCm vs Vulkan on Rocinante 12B and one large MoE (prompt
   processing and generation, empty and full context). Record the numbers here and pick the
   default `LLM_BACKEND`. Same for Orpheus real-time factor (the 4070 does ~1.5×).

### Phase C: move the frontends and data

1. Stop the workstation stack. Copy `companion/data`, `ai_story_maker/data`, Iguana and Nocturne
   outputs, `jarvis`, the ComfyUI state and Orpheus state to the same relative paths on Halo.
2. `stack up` and each frontend on Halo; verify chat, voice (Orpheus + Kokoro), sprites, Iguana,
   Nocturne, the narrator, Rhapsode's read of Pantheon's library.
3. Set up backups of the Halo data dirs: they now live on one machine only.

### Phase D: switching between the hosts

1. `stack tunnel` on the workstation: one `ssh -N -L` for 7860–7864, 8188 and 5001 to Halo's
   loopback, with a systemd user unit so it comes up at login. Bookmarks keep their
   `127.0.0.1:<port>` addresses.
2. `stack desktop` uses the tunnelled Pantheon.
3. **Switching**: `stack off` on one host, `stack on` on the other, with the tunnel up when
   Halo is the one in use. The data has a single home at a time; a `stack sync` (rsync of the
   app data dirs in the direction you're switching) keeps the copies from drifting. Models stay
   per host (Halo will hold bigger ones).

### Phase E: optional

- **ComfyUI back on the 4070** if image generation on the 8060S is too slow for sprites:
  point `COMFYUI_URL` (Pantheon, Iguana, Nocturne) at the workstation through a reverse tunnel.
  Decide after Phase B's numbers.
- **Bigger default chat model** (MoE) once benchmarked. New families may need new chat adapters
  next to `chatml.json` and `mistral-nemo.json`.
- **TLS** in front of Pantheon (its Phase 8) to retire the tunnel and reach it from phones.

## Open questions

1. **Rex:** confirm the kernel / ROCm versions on the box; replace the OS only if `stack
   doctor` or the Phase B benchmark says so.
2. **Where image generation lives** long term: Halo only, or keep the 4070 for ComfyUI
   (Phase E)? Needs Phase B's numbers.
3. **Remote access beyond the workstation** (phone, laptop): tunnel per device, or TLS first?

## Sources (2026-10-30)

- gfx1151 kernel/firmware and container routes: [Strix Halo knowledge base](https://whylucian.github.io/softab/KNOWLEDGE_BASE.html),
  [kyuz0 Strix Halo toolboxes](https://github.com/kyuz0/amd-strix-halo-toolboxes),
  [ROCm 7 on Ubuntu 24.04 for gfx1151](https://datawhalechina.github.io/hello-rocm/01-deploy/gemma4/env-prepare-ubuntu24-rocm7)
- TTM limits: [Level1Techs: ROCm only uses half the memory](https://forum.level1techs.com/t/rocm-can-only-use-half-of-the-unified-memory-on-my-strix-halo/253152),
  [Framework community: iGPU VRAM](https://community.frame.work/t/igpu-vram-how-much-can-be-assigned/73081),
  [llama.cpp #18159](https://github.com/ggml-org/llama.cpp/issues/18159)
- ROCm vs Vulkan on Strix Halo: [Level1Techs inference notes](https://forum.level1techs.com/t/strix-halo-llm-inference-notes/249466/9),
  [llama.cpp ROCm performance discussion](https://github.com/ggml-org/llama.cpp/discussions/15021)
- KoboldCPP ROCm: [YellowRoseCx/koboldcpp-rocm](https://github.com/YellowRoseCx/koboldcpp-rocm)
- Rex (Debian 13, Linux 7.2.7, ROCm 10.0 — single source): [Neoteo](https://www.neoteo.com/en/amd-updates-ryzen-ai-developer-platform-with-linux-727-and-rocm-100)
- AMD gfx1151 wheel index (torch 2.11.0+rocm7.13.0): `https://repo.amd.com/rocm/whl/gfx1151/`
- Ubuntu kernels (earlier OS choice): [24.04.4 with 6.17 HWE](https://phoronix.com/news/ubuntu-24.04.4-lts),
  [26.04 LTS with Linux 7.0](https://phoronix.com/news/Ubuntu-26.04-LTS)
