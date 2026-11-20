# The dashboard (AI Ecosystem)

[Wiki home](README.md) · User guide

AI Ecosystem is the stack's control panel. Start it with `stack stackui` and open
`http://localhost:7863`, or `http://<host>.local:7863` from a phone or another computer on the
LAN.

> **No login.** Anyone on your home network can drive it, the same as every other app in the
> stack. Writes are protected against other web pages (cross-site requests are refused), but not
> against people on the LAN.

## Layout

- **Toolbar:** the brand, whether the backends are running ("running", "stopped" or "N of M up"),
  **Start backends** / **Stop backends**, the separate **Start at boot** toggle, and the two
  workspaces.
- **Sidebar:** the screens of the current workspace. The theme swatches are at its foot.
- **Console** (bottom): opens when an action runs and streams its output. Only one action runs at
  a time; while it does, every button that could start another is disabled.

## Workspaces and screens

**Infrastructure** (the landing page)

| Screen | What you can do |
|---|---|
| Overview | The router (where each capability is served, route text to Halo), one card per capability (text, image and sound, decision) with what it has loaded, and the GPU's memory and load |
| Language models | KoboldCPP's GGUFs: pick one, set GPU layers and the context window, delete; **Find on HuggingFace**; the **Fortuna** tab |
| Image and audio | ComfyUI's checkpoints, LoRAs, VAEs, diffusion models and text encoders; find more on Civitai or HuggingFace |
| VRAM planner | Live GPU memory, **Release ComfyUI VRAM**, and how many layers of a GGUF fit next to a checkpoint and Fortuna |
| Logs | Follow any app's logs live |
| Custom nodes | ComfyUI node pins versus what is checked out; re-pin |
| Doctor | `stack doctor`'s report |
| About | The release you're on, whether a newer one is out, how to update, and links to this wiki, the source, releases, the changelog and issues |

**Frontends:** the installed apps with Start/Stop/Logs, links (HTTPS where the app offers it), and
any `frontend.json` problems.

The URL keeps your place (`#/llms`, `#/vram` …), so a reload or a bookmark returns to the same
screen.

## Things worth knowing

- **Choosing a model never restarts anything.** It is saved for the next start, and the screen
  and the card offer a restart when what's loaded differs. See [Models](models.md).
- **Downloads** run one at a time in a strip at the top; cancelling keeps the partial file, and
  starting again resumes it.
- **Update badge:** when a newer release is out, an **Update** button appears in the toolbar and
  opens About. The check asks GitHub at most every 6 hours. The repo is private, so it needs a
  `GITHUB_TOKEN` with read access in `stack-ui/tokens.env`; without one, About says so, and
  `stack version --check` in a terminal does the same check with your own git login.
  `STACK_UPDATE_CHECK=off` turns it off.
- **Doctor from the dashboard:** two checks always fail from inside its container (the NVIDIA
  CDI spec and the systemd unit file). Run `stack doctor` in a terminal for those.

## Themes

The swatches at the foot of the sidebar pick a theme: **Bone, Ember, Moss, Dusk, Harbor** (the
same five dark colourways as Whimsy) or **Daylight** (light). The choice is remembered per
browser. Until you pick one, the page follows your system: Harbor in dark mode, Daylight in light
mode.
