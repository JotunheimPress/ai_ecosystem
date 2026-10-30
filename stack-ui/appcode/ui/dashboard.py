"""The home screen: the shared backends on top, the frontends below.

Backends get the detail, because they are what the frontends contend for: which model
KoboldCPP has in VRAM (and whether that differs from the one selected to boot), and what
ComfyUI is holding. ComfyUI cannot name its resident models, so its card says how much
VRAM it holds and which files the last job used — see services/backends.py.

Rendered as escaped HTML with one delegated click listener; every value that reaches the
markup goes through ``esc``.
"""
from __future__ import annotations

from wapyt import progress_html

from services.models_api import ModelsApi
from ui.common import Listeners, ago, attr, by_id, closest, esc, failed, fmt_bytes, spawn, toast

_ICONS = {
    "koboldcpp": "mdi-text-box-outline",
    "comfyui": "mdi-image-filter-hdr",
    "companion": "mdi-account-heart-outline",
    "story": "mdi-book-open-page-variant-outline",
    "iguana": "mdi-palette-outline",
    "nocturne": "mdi-music-note-outline",
}

_KIND_LABEL = {"ckpt": "checkpoint", "unet": "diffusion model", "lora": "LoRA", "vae": "VAE",
               "clip": "text encoder", "control_net": "ControlNet"}


class DashboardView:
    def __init__(self, app, **_):
        self.app = app
        self.root = None
        self.listeners = Listeners()
        self.ov: dict = {}

    def mount(self, root) -> None:
        self.root = root
        root.innerHTML = (
            '<div class="su-page">'
            '<section class="su-section">'
            '<div class="su-section-head"><h2>Backends</h2>'
            '<span class="su-dim">shared by every app on ai-net</span></div>'
            '<div class="su-grid su-grid-2" id="db-backends">'
            '<div class="su-card su-skeleton"></div><div class="su-card su-skeleton"></div>'
            '</div>'
            '<div class="su-gpu" id="db-gpu"></div>'
            '</section>'
            '<section class="su-section">'
            '<div class="su-section-head"><h2>Frontends</h2>'
            '<span class="su-dim">open, start or stop the apps that use the backends</span></div>'
            '<div class="su-grid" id="db-frontends"></div>'
            '<pre class="su-raw" id="db-raw" hidden></pre>'
            '</section>'
            '</div>')
        self.listeners.on(root, "click", self._on_click)

    def unmount(self) -> None:
        self.listeners.clear()

    # --- data ------------------------------------------------------------------
    def on_overview(self, ov: dict) -> None:
        self.ov = ov
        self._render()

    def on_busy(self, _busy: bool) -> None:
        self._render()

    def _render(self) -> None:
        ov = self.ov
        if not ov or not by_id("db-backends"):
            return
        apps = ov.get("apps") or []
        raw = by_id("db-raw")
        if not ov.get("parsed"):
            # `stack status` is scraped text; when the parse finds nothing, show the text
            # rather than an empty page.
            raw.hidden = False
            raw.textContent = ov.get("raw") or ""
        else:
            raw.hidden = True
        by_id("db-backends").innerHTML = "".join(
            self._backend_card(a) for a in apps if a.get("group") == "backends")
        by_id("db-frontends").innerHTML = "".join(
            self._frontend_card(a) for a in apps if a.get("group") == "frontends") or (
            '<p class="su-dim">No frontends registered.</p>')
        by_id("db-gpu").innerHTML = self._gpu_line()

    # --- markup ----------------------------------------------------------------
    def _buttons(self, a: dict, compact: bool = False) -> str:
        dis = " disabled" if self.app.busy else ""
        name = esc(a["name"])
        if a.get("up"):
            btns = (f'<button class="su-btn" data-act="down" data-target="{name}"{dis}>'
                    '<i class="mdi mdi-stop"></i>Stop</button>'
                    f'<button class="su-btn" data-act="restart" data-target="{name}"{dis}>'
                    '<i class="mdi mdi-restart"></i>Restart</button>')
        else:
            btns = (f'<button class="su-btn su-primary" data-act="start" data-target="{name}"{dis}>'
                    '<i class="mdi mdi-play"></i>Start</button>')
        label = "" if compact else "Logs"
        return btns + (f'<button class="su-btn su-ghost" data-logs="{name}" title="Follow its log">'
                       f'<i class="mdi mdi-text-long"></i>{label}</button>')

    def _head(self, a: dict, sub: str) -> str:
        up = a.get("up")
        icon = _ICONS.get(a["name"], "mdi-application-outline")
        title = esc(a.get("label") or a["name"])
        if up and a.get("group") == "frontends":
            title = (f'<a href="http://localhost:{int(a["port"])}/" target="_blank" '
                     f'rel="noreferrer">{title}<i class="mdi mdi-open-in-new"></i></a>')
        return (
            '<div class="su-card-head">'
            f'<i class="mdi {icon} su-card-icon"></i>'
            f'<div class="su-card-titles"><div class="su-card-title">{title}</div>'
            f'<div class="su-dim">{sub}</div></div>'
            f'<span class="su-pill {"up" if up else "down"}">{"up" if up else "down"}</span>'
            '</div>')

    def _backend_card(self, a: dict) -> str:
        b = (self.ov.get("backends") or {})
        sub = f'{esc(a.get("desc"))} · :{int(a["port"])}'
        if a["name"] == "koboldcpp":
            body = self._kobold_body(a, b.get("kobold") or {})
        elif a["name"] == "comfyui":
            body = self._comfy_body(a, b.get("comfy") or {})
        else:
            body = ""
        return (f'<div class="su-card su-backend {"up" if a.get("up") else "down"}">'
                f'{self._head(a, sub)}<div class="su-card-body">{body}</div>'
                f'<div class="su-actions">{self._buttons(a)}{self._extra(a)}</div></div>')

    def _extra(self, a: dict) -> str:
        if a["name"] == "koboldcpp":
            return ('<button class="su-btn su-ghost" data-go="llms">'
                    '<i class="mdi mdi-swap-horizontal"></i>Change model</button>')
        if a["name"] == "comfyui" and a.get("up"):
            dis = " disabled" if self.app.busy else ""
            return ('<button class="su-btn su-ghost" data-free="1"'
                    f'{dis} title="Ask ComfyUI to unload its models and give the VRAM back">'
                    '<i class="mdi mdi-broom"></i>Release VRAM</button>')
        return ""

    @staticmethod
    def _kv(label: str, value_html: str) -> str:
        return f'<div class="su-kv"><span>{esc(label)}</span><div>{value_html}</div></div>'

    def _kobold_body(self, a: dict, k: dict) -> str:
        rows = []
        if a.get("up") and k.get("loaded"):
            rows.append(self._kv("Loaded", f'<b class="su-model">{esc(k.get("file") or k["loaded"])}</b>'))
            facts = []
            if k.get("size"):
                facts.append(fmt_bytes(k["size"]))
            if k.get("arch"):
                facts.append(esc(k["arch"]))
            # model.env's layer count describes the *selected* model; while a restart is
            # pending that is not the one in VRAM, so it would be a wrong number here.
            if k.get("gpu_layers") and k.get("n_layers") and not k.get("restart_needed"):
                facts.append(f'{esc(k["gpu_layers"])} of {int(k["n_layers"])} layers on GPU')
            elif k.get("n_layers"):
                facts.append(f'{int(k["n_layers"])} layers')
            if k.get("context"):
                facts.append(f'{int(k["context"]):,} ctx')
            if facts:
                rows.append(self._kv("", '<span class="su-dim">' + " · ".join(facts) + "</span>"))
        elif a.get("up"):
            rows.append(self._kv("Loaded", '<span class="su-dim">up, but it did not name a model</span>'))
        else:
            rows.append(self._kv("Loaded", '<span class="su-dim">nothing — not running</span>'))

        sel = k.get("selected")
        if sel and not k.get("selected_present"):
            rows.append(self._kv("Boots", f'<span class="su-bad">{esc(sel)} — missing from models/</span>'))
        elif sel and (not a.get("up") or k.get("restart_needed")):
            src = esc(k.get("selected_source") or "")
            rows.append(self._kv("Boots", f'{esc(sel)} <span class="su-dim">({src})</span>'))
        if k.get("restart_needed"):
            dis = " disabled" if self.app.busy else ""
            rows.append(
                '<div class="su-banner">'
                '<i class="mdi mdi-information-outline"></i>'
                + (f'<span>Selected <b>{esc(sel)}</b> — restart KoboldCPP to load it.</span>'
                   if "model" in (k.get("restart_for") or ["model"]) else
                   '<span>New context window — restart KoboldCPP to apply it.</span>')
                + f'<button class="su-btn su-primary" data-act="restart" data-target="koboldcpp"{dis}>'
                'Restart</button></div>')
        return "".join(rows)

    def _comfy_body(self, a: dict, c: dict) -> str:
        if not a.get("up") or not c.get("up"):
            return self._kv("Loaded", '<span class="su-dim">nothing — not running</span>')
        rows = []
        held = c.get("held") or 0
        if c.get("resident"):
            rows.append(self._kv("Holding", f'<b>{fmt_bytes(held)}</b> of VRAM '
                                            '<span class="su-dim">(models kept from the last job)</span>'))
        else:
            rows.append(self._kv("Holding", '<span class="su-dim">no models resident</span>'))
        if c.get("running") or c.get("pending"):
            rows.append(self._kv("Queue", f'{int(c.get("running") or 0)} running · '
                                          f'{int(c.get("pending") or 0)} waiting'))
        models = c.get("models") or []
        if models:
            if c.get("models_from") == "running":
                when = "Running job uses"
            else:
                when = f"Last job, {ago(c['last_job_at'])}" if c.get("last_job_at") else "Last job used"
            items = "".join(
                f'<li><span class="su-tag">{esc(_KIND_LABEL.get(m.get("kind"), m.get("kind")))}</span>'
                f'<span class="su-model">{esc(m.get("name"))}</span></li>' for m in models[:6])
            more = f'<li class="su-dim">+{len(models) - 6} more</li>' if len(models) > 6 else ""
            rows.append(self._kv(when, f'<ul class="su-models">{items}{more}</ul>'))
        else:
            rows.append(self._kv("Last job", '<span class="su-dim">none since it started</span>'))
        return "".join(rows)

    def _gpu_line(self) -> str:
        c = ((self.ov.get("backends") or {}).get("comfy") or {})
        total, free = c.get("vram_total"), c.get("vram_free")
        if not c.get("up") or not total:
            return ('<i class="mdi mdi-expansion-card-variant"></i>'
                    '<span class="su-dim">GPU figures come from ComfyUI — start it to see VRAM use.</span>')
        used = max(int(total) - int(free or 0), 0)
        return ('<i class="mdi mdi-expansion-card-variant"></i>'
                f'<span>{esc(c.get("device") or "GPU")}</span>'
                + progress_html(used, int(total), text=f"{fmt_bytes(used)} of {fmt_bytes(total)}",
                                state="error" if used / int(total) > 0.9 else "active")
                + '<span class="su-dim">both backends and the desktop share this card</span>')

    def _frontend_card(self, a: dict) -> str:
        sub = f':{int(a["port"])}'
        return (f'<div class="su-card su-frontend {"up" if a.get("up") else "down"}">'
                f'{self._head(a, sub)}'
                f'<div class="su-card-body"><p class="su-desc">{esc(a.get("desc"))}</p></div>'
                f'<div class="su-actions">{self._buttons(a, compact=True)}</div></div>')

    # --- events ----------------------------------------------------------------
    def _on_click(self, ev) -> None:
        btn = closest(ev, "button")
        if not btn or btn.disabled:
            return
        if attr(btn, "data-act"):
            self.app.act(attr(btn, "data-act"), attr(btn, "data-target"))
        elif attr(btn, "data-logs"):
            self.app.open_logs(attr(btn, "data-logs"))
        elif attr(btn, "data-go"):
            self.app.go(attr(btn, "data-go"))
        elif attr(btn, "data-free"):
            btn.disabled = True
            spawn(self._free(), "free vram")

    async def _free(self) -> None:
        r = await ModelsApi().free_vram_async()
        if not failed(r, "release VRAM"):
            v = r.get("vram") or {}
            toast(f"ComfyUI released its models — {fmt_bytes(v.get('free'))} free" if v.get("available")
                  else "ComfyUI released its models", "success")
        await self.app.refresh()
