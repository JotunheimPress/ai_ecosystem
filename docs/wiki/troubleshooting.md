# Troubleshooting

[Wiki home](README.md) · User guide

## Start here

```bash
stack doctor           # prerequisites, images, network, unit, model files, frontends, status
stack status           # what's up right now
stack logs <app>       # follow one app
```

`stack doctor` changes nothing. From the dashboard's Doctor screen, two checks always fail
(the NVIDIA CDI spec and the systemd unit file can't be seen from its container); run it in a
terminal for those.

## Known problems

**KoboldCPP won't start, or runs out of memory.** ComfyUI keeps models in GPU memory after a
render. Release its memory (**Release VRAM** on the image card, or **Release ComfyUI VRAM** in the VRAM planner), then restart KoboldCPP.
Its start script already asks ComfyUI to free memory, waits for the old copy's memory, and retries
with fewer GPU layers; lower the layers yourself if it still fails.

**`podman ps` says KoboldCPP is "starting" for days.** Ignore it: that is podman's own health
field. The stack checks KoboldCPP directly.

**A capability returns 502.** The router is up but the backend behind it is down or still
loading. Check `stack status`, then `stack logs <backend>`.

**The dashboard says the router config is older.** The router was created from an older
template. The next `stack up` (or `stack router`) recreates it.

**Port 8188 is taken (AMD Rex).** The OS ships its own ComfyUI service on `127.0.0.1:8188`. Set
`COMFYUI_PORT=8189` in `host.env`; inside `ai-net` it is still `comfyui:8188`. If that OS service
crash-loops ("No CUDA GPUs are available"), it can be turned off with
`systemctl --user mask comfyui@8188.socket` (undo with `unmask`).

**Don't re-enable the old `comfyui.service`.** It competed with the stack for port 8188 and undid
`stack down comfyui`. It stays disabled.

**Fortuna won't start: sha256 mismatch.** The file is not the pinned one. Delete it
(Language models → Fortuna → Delete, or remove it from `fortuna/models/`) and download it again.

**The dashboard's buttons do nothing.** Its container reaches podman through the user's
`podman.socket`: `systemctl --user enable --now podman.socket`, then rebuild it
(`stack-ui/run-podman.sh --rebuild`).

**No "Start at boot" toggle.** The systemd unit isn't installed: `stack install service`.

**A gated download fails with 401/403.** Add `HF_TOKEN` or `CIVITAI_TOKEN` to
`stack-ui/tokens.env` and rebuild the dashboard.

**A backend crashed.** Each one restarts itself up to 5 times. A stopped container stays around
until its next start, so `stack logs <app>` still shows what happened.
