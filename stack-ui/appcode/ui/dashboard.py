"""The two workspaces: Infrastructure (the landing page) and Frontends.

**Infrastructure** is the shared backends, grouped by what they provide rather than by
name: the router on top (where each capability is served, and where text is routed), then
one card per capability (text, image and sound, decision), then the GPU. Which card body a
backend gets comes from its `capability` in `stack status --json`, via
``services.backends.ADAPTERS``, so a new backend is a row in the stack, not a name check here.

**Frontends** is the apps that use them: cards for the installed ones only, with HTTPS links
where a frontend offers them, and the problems `stack` found in their frontend.json.

Rendered as escaped HTML with one delegated click listener per view; every value that
reaches the markup goes through ``esc``.
"""
from __future__ import annotations

from wapyt import progress_html

from services.models_api import ModelsApi
from ui.common import Listeners, ago, attr, by_id, closest, esc, failed, fmt_bytes, spawn, toast

_ICONS = {
    "router": "mdi-router-network",
    "text": "mdi-text-box-outline",
    "image-sound": "mdi-image-filter-hdr",
    "decision": "mdi-dice-multiple-outline",
}  # frontends bring their own icon in frontend.json (`icon`)

# Capability -> heading, and the stable address frontends use on ai-net.
_CAPS = {
    "text": ("Text", "koboldcpp:5001"),
    "image-sound": ("Image and sound", "comfyui:8188"),
    "decision": ("Decision", "fortuna:8009"),
}
_CAP_ORDER = ["text", "image-sound", "decision"]

_KIND_LABEL = {"ckpt": "checkpoint", "unet": "diffusion model", "lora": "LoRA", "vae": "VAE",
               "clip": "text encoder", "control_net": "ControlNet"}

# What stopping or restarting a backend takes away, named in the confirm dialog.
_CONFIRM = {
    ("down", "router"): "Stop the router? Every frontend loses text until it starts again. "
                        "The model stays loaded.",
    ("restart", "router"): "Restart the router? Text is unreachable for a moment, and a reply "
                           "streaming right now is cut off. The model stays loaded.",
    ("route_text_halo", ""): "Send text to the Halo box (HALO_HOST in ai-stack/halo.env)? "
                             "Replies already streaming finish here. The local model stays loaded.",
    ("route_text_local", ""): "Serve text from this machine's copy again?",
    ("down", "frontends"): "Stop every frontend? The infrastructure keeps running.",
}


def _host() -> str:
    # The name this page was opened with, so a phone's links point at the stack's host
    # and not at the phone itself.
    import js
    return str(js.window.location.hostname) or "localhost"


class _Cards:
    """Markup shared by both workspaces."""

    app = None

    def _dis(self) -> str:
        return " disabled" if self.app.busy else ""

    def _buttons(self, a: dict, compact: bool = False, up: bool | None = None,
                 noun: str = "") -> str:
        """Start or Stop+Restart, then Logs. ``up`` overrides a.up (the local copy's state)."""
        dis = self._dis()
        name = esc(a["name"])
        noun = f" {noun}" if noun else ""
        if a.get("up") if up is None else up:
            btns = (f'<button class="su-btn" data-act="down" data-target="{name}"{dis}>'
                    f'<i class="mdi mdi-stop"></i>Stop{noun}</button>'
                    f'<button class="su-btn" data-act="restart" data-target="{name}"{dis}>'
                    f'<i class="mdi mdi-restart"></i>Restart{noun}</button>')
        else:
            btns = (f'<button class="su-btn su-primary" data-act="start" data-target="{name}"{dis}>'
                    f'<i class="mdi mdi-play"></i>Start{noun}</button>')
        label = "" if compact else "Logs"
        return btns + (f'<button class="su-btn su-ghost" data-logs="{name}" title="Follow its log">'
                       f'<i class="mdi mdi-text-long"></i>{label}</button>')

    @staticmethod
    def _pill(up: bool, text: str = "") -> str:
        return f'<span class="su-pill {"up" if up else "down"}">{esc(text or ("up" if up else "down"))}</span>'

    def _head(self, a: dict, sub: str, title_html: str = "", pill: str = "") -> str:
        up = a.get("up")
        icon = (a.get("icon") or _ICONS.get(a.get("capability") or "")
                or _ICONS.get(a["name"], "mdi-application-outline"))
        title = title_html or esc(a.get("label") or a["name"])
        return (
            '<div class="su-card-head">'
            f'<i class="mdi {icon} su-card-icon"></i>'
            f'<div class="su-card-titles"><div class="su-card-title">{title}</div>'
            f'<div class="su-dim">{sub}</div></div>'
            f'{pill or self._pill(up)}'
            '</div>')

    @staticmethod
    def _kv(label: str, value_html: str) -> str:
        return f'<div class="su-kv"><span>{esc(label)}</span><div>{value_html}</div></div>'

    def _on_click(self, ev) -> None:
        btn = closest(ev, "button")
        if not btn or btn.disabled:
            return
        if attr(btn, "data-act"):
            verb, target = attr(btn, "data-act"), attr(btn, "data-target") or ""
            self.app.act(verb, target, confirm=_CONFIRM.get((verb, target), ""))
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
            toast("ComfyUI released its models.", "success")
        await self.app.refresh()


class InfrastructureView(_Cards):
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
            '<div class="su-section-head"><h2>Router</h2>'
            '<span class="su-dim">one stable address per capability, for every frontend</span></div>'
            '<div id="in-router"><div class="su-card su-skeleton"></div></div>'
            '</section>'
            '<section class="su-section">'
            '<div class="su-section-head"><h2>Capabilities</h2>'
            '<span class="su-dim">shared by every app on ai-net</span></div>'
            '<div class="su-grid su-grid-3" id="in-caps">'
            '<div class="su-card su-skeleton"></div><div class="su-card su-skeleton"></div>'
            '<div class="su-card su-skeleton"></div></div>'
            '<div class="su-gpu" id="in-gpu"></div>'
            '</section>'
            '<pre class="su-raw" id="in-raw" hidden></pre>'
            '</div>')
        self.listeners.on(root, "click", self._on_click)

    def unmount(self) -> None:
        self.listeners.clear()

    def on_overview(self, ov: dict) -> None:
        self.ov = ov
        self._render()

    def on_busy(self, _busy: bool) -> None:
        self._render()

    # --- render -----------------------------------------------------------------
    def _render(self) -> None:
        ov = self.ov
        if not ov or not by_id("in-caps"):
            return
        raw = by_id("in-raw")
        raw.hidden = bool(ov.get("parsed"))
        if not ov.get("parsed"):
            raw.textContent = ov.get("raw") or ""
        backends = [a for a in ov.get("apps") or [] if a.get("group") == "backends"]
        router = next((a for a in backends if a["name"] == "router"), None)
        by_id("in-router").innerHTML = self._router(router) if router else ""
        caps = [a for a in backends if a["name"] != "router"]
        caps.sort(key=lambda a: _CAP_ORDER.index(a.get("capability"))
                  if a.get("capability") in _CAP_ORDER else len(_CAP_ORDER))
        by_id("in-caps").innerHTML = "".join(self._cap_card(a) for a in caps)
        by_id("in-gpu").innerHTML = self._gpu_line()

    def _route(self) -> dict:
        return self.ov.get("route") or {}

    def _router(self, a: dict) -> str:
        r = self._route()
        dis = self._dis()
        where, target = r.get("where") or "local", r.get("text") or ""
        if where == "local":
            served = "this machine's copy"
            switch = (f'<button class="su-btn" data-act="route_text_halo"{dis} '
                      'title="stack route halo">'
                      '<i class="mdi mdi-swap-horizontal"></i>Route text to Halo</button>')
        else:
            served = f'the Halo box ({esc(target.rsplit(":", 1)[0])})'
            switch = (f'<button class="su-btn" data-act="route_text_local"{dis} '
                      'title="stack route local">'
                      '<i class="mdi mdi-swap-horizontal"></i>Serve text locally</button>')
        rows = [self._kv("Text", f'served by {served} <span class="su-dim">· {esc(target)}</span>')]
        rows.append(self._kv("Image and sound", 'this machine\'s copy <span class="su-dim">· comfyui-local:8188</span>'))
        rows.append(self._kv("Decision", 'this machine\'s copy <span class="su-dim">· fortuna-local:8009</span>'))
        if r.get("router_config") == "stale":
            rows.append(
                '<div class="su-banner"><i class="mdi mdi-information-outline"></i>'
                '<span>The router runs an older config. Restart it (or run <code>stack up</code>) '
                'to apply the current one.</span>'
                f'<button class="su-btn su-primary" data-act="restart" data-target="router"{dis}>'
                'Restart router</button></div>')
        sub = f'{esc(a.get("desc"))} · :{int(a["port"])}'
        return (f'<div class="su-card su-backend su-router {"up" if a.get("up") else "down"}">'
                f'{self._head(a, sub)}<div class="su-card-body">{"".join(rows)}</div>'
                f'<div class="su-actions">{self._buttons(a)}{switch}</div></div>')

    def _cap_card(self, a: dict) -> str:
        cap = a.get("capability") or ""
        heading, addr = _CAPS.get(cap, (cap or "Backend", ""))
        b = self.ov.get("backends") or {}
        sub = (f'{esc(heading)}{" · " + esc(addr) if addr else ""}'
               f' <span class="su-dim">· {esc(a.get("desc"))}</span>')
        local = a.get("local") or {}
        remote = cap == "text" and self._route().get("where") == "halo"
        if cap == "text":
            body = self._kobold_body(a, b.get("kobold") or {}, local, remote)
        elif cap == "image-sound":
            body = self._comfy_body(a, b.get("comfy") or {})
        elif cap == "decision":
            body = self._fortuna_body(a, b.get("fortuna") or {})
        else:
            body = ""
        if remote:
            # Every button acts on the local copy, and says so; the pill shows what is served.
            local_up = local.get("state") in ("up", "loading")
            actions = self._buttons(a, up=local_up, noun="local copy")
        else:
            actions = self._buttons(a)
        return (f'<div class="su-card su-backend {"up" if a.get("up") else "down"}">'
                f'{self._head(a, sub)}<div class="su-card-body">{body}</div>'
                f'<div class="su-actions">{actions}{self._extra(a)}</div></div>')

    def _extra(self, a: dict) -> str:
        cap = a.get("capability")
        if cap == "text":
            return ('<button class="su-btn su-ghost" data-go="llms">'
                    '<i class="mdi mdi-swap-horizontal"></i>Change model</button>')
        if cap == "image-sound" and a.get("up"):
            return ('<button class="su-btn su-ghost" data-free="1"'
                    f'{self._dis()} title="Ask ComfyUI to unload its models and give the memory back">'
                    '<i class="mdi mdi-broom"></i>Release VRAM</button>')
        return ""

    # --- adapters, one per capability ----------------------------------------------
    def _kobold_body(self, a: dict, k: dict, local: dict, remote: bool) -> str:
        rows = []
        if remote:
            rows.append(self._kv("Served by", f'Halo <span class="su-dim">({esc(k.get("text_route") or "")})</span>'))
            state = local.get("state") or "unknown"
            cls = "su-good" if state == "up" else "su-dim"
            rows.append(self._kv("Local copy", f'<span class="{cls}">{esc(state)}</span>'
                                 + ('' if state == "up" else
                                    ' <span class="su-dim">· not needed while text is routed away</span>')))
        if a.get("up") and k.get("loaded"):
            rows.append(self._kv("Loaded", f'<b class="su-model">{esc(k.get("file") or k["loaded"])}</b>'))
            facts = []
            if k.get("size"):
                facts.append(fmt_bytes(k["size"]))
            if k.get("arch"):
                facts.append(esc(k["arch"]))
            # model.env's layer count describes the *selected* model; while a restart is
            # pending that is not the one in memory, so it would be a wrong number here.
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
        elif sel and (not a.get("up") or k.get("restart_needed") or remote):
            src = esc(k.get("selected_source") or "")
            rows.append(self._kv("Boots" if not remote else "Local boots",
                                 f'{esc(sel)} <span class="su-dim">({src})</span>'))
        if k.get("restart_needed"):
            rows.append(
                '<div class="su-banner">'
                '<i class="mdi mdi-information-outline"></i>'
                + (f'<span>Selected <b>{esc(sel)}</b> — restart Oracle to load it.</span>'
                   if "model" in (k.get("restart_for") or ["model"]) else
                   '<span>New context window — restart Oracle to apply it.</span>')
                + f'<button class="su-btn su-primary" data-act="restart" data-target="{esc(a["name"])}"{self._dis()}>'
                'Restart</button></div>')
        return "".join(rows)

    def _comfy_body(self, a: dict, c: dict) -> str:
        if not a.get("up") or not c.get("up"):
            return self._kv("Loaded", '<span class="su-dim">nothing — not running</span>')
        rows = []
        held = c.get("held") or 0
        if c.get("resident"):
            rows.append(self._kv("Holding", f'<b>{fmt_bytes(held)}</b> of GPU memory '
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

    def _fortuna_body(self, a: dict, f: dict) -> str:
        if not a.get("up") or not f.get("up"):
            return self._kv("Loaded", '<span class="su-dim">nothing — not running</span>')
        return (self._kv("Loaded", f'<b class="su-model">{esc(f.get("model") or "unnamed")}</b>')
                + self._kv("Answers", '<span class="su-dim">yes/no, choice and score questions '
                                      '(/v1/systemone)</span>'))

    def _gpu_line(self) -> str:
        g = self.ov.get("gpu") or {}
        icon = '<i class="mdi mdi-expansion-card-variant"></i>'
        if not g.get("available") or not g.get("total"):
            return f'{icon}<span class="su-dim">No GPU figures: {esc(g.get("source") or "unknown")}</span>'
        total, used = int(g["total"]), int(g.get("used") or 0)
        busy = g.get("busy")
        what = "memory (VRAM + GTT)" if g.get("unified") else "VRAM"
        return (f'{icon}<span>{esc(g.get("device") or "GPU")}</span>'
                + progress_html(used, total, text=f"{fmt_bytes(used)} of {fmt_bytes(total)} {what}",
                                state="error" if used / total > 0.9 else "active")
                + (f'<span>{int(busy)}% busy</span>' if busy is not None else "")
                + f'<span class="su-dim">every backend and the desktop share it · {esc(g.get("source"))}</span>')


class FrontendsView(_Cards):
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
            '<div class="su-section-head"><h2>Frontends</h2>'
            '<span class="su-dim">the installed apps that use the infrastructure</span>'
            '<span class="su-flex"></span>'
            '<button class="su-btn" id="fe-stop" data-act="down" data-target="frontends">'
            '<i class="mdi mdi-stop"></i>Stop all frontends</button></div>'
            '<div id="fe-warn"></div>'
            '<div class="su-grid" id="fe-cards">'
            '<div class="su-card su-skeleton"></div><div class="su-card su-skeleton"></div></div>'
            '<p class="su-dim su-infra-line" id="fe-infra"></p>'
            '</section>'
            '<pre class="su-raw" id="fe-raw" hidden></pre>'
            '</div>')
        self.listeners.on(root, "click", self._on_click)

    def unmount(self) -> None:
        self.listeners.clear()

    def on_overview(self, ov: dict) -> None:
        self.ov = ov
        self._render()

    def on_busy(self, _busy: bool) -> None:
        self._render()

    def _render(self) -> None:
        ov = self.ov
        if not ov or not by_id("fe-cards"):
            return
        raw = by_id("fe-raw")
        raw.hidden = bool(ov.get("parsed"))
        if not ov.get("parsed"):
            raw.textContent = ov.get("raw") or ""
        apps = ov.get("apps") or []
        fronts = [a for a in apps if a.get("group") == "frontends"]
        by_id("fe-stop").disabled = self.app.busy or not any(a.get("up") for a in fronts)
        by_id("fe-cards").innerHTML = "".join(self._card(a) for a in fronts) or (
            '<p class="su-dim">No frontends installed — clone a frontend repo next to ai-stack '
            '(any repo with a frontend.json).</p>')
        warns = ov.get("warnings") or []
        by_id("fe-warn").innerHTML = "".join(
            f'<div class="su-banner"><i class="mdi mdi-alert-outline"></i><span>{esc(w)}</span></div>'
            for w in warns)
        caps = [a for a in apps if a.get("group") == "backends" and a.get("capability") in _CAPS]
        caps.sort(key=lambda a: _CAP_ORDER.index(a["capability"]))
        by_id("fe-infra").innerHTML = (
            'Infrastructure: ' + " · ".join(
                f'{esc(_CAPS[a["capability"]][0].lower())} '
                f'<span class="{"su-good" if a.get("up") else "su-bad"}">{"up" if a.get("up") else "down"}</span>'
                for a in caps)
            + ' <button class="su-btn su-ghost" data-go="infrastructure">Open</button>') if caps else ""

    def _card(self, a: dict) -> str:
        # The repo name is the identity; say it whenever the brand differs, so a fork
        # (npc_maker) can't pass for the original (companion) on looks alone.
        repo = "" if a.get("label") == a["name"] else f'{esc(a["name"])} · '
        host = esc(_host())
        port = int(a["port"])
        sub = f'{repo}:{port}'
        title = esc(a.get("label") or a["name"])
        links = ""
        if a.get("up"):
            title = (f'<a href="http://{host}:{port}/" target="_blank" rel="noreferrer">'
                     f'{title}<i class="mdi mdi-open-in-new"></i></a>')
            hp = a.get("https_port")
            if hp:
                # HTTPS is what lets another device use the microphone; the setup page on
                # the plain port explains trusting the app's certificate first.
                links = (f'<a class="su-btn su-ghost" href="https://{host}:{int(hp)}/" target="_blank" '
                         f'rel="noreferrer"><i class="mdi mdi-lock-outline"></i>HTTPS</a>')
                if a.get("https_setup"):
                    links += (f'<a class="su-btn su-ghost" href="http://{host}:{port}{esc(a["https_setup"])}" '
                              'target="_blank" rel="noreferrer" title="Trust its certificate on this device">'
                              '<i class="mdi mdi-certificate-outline"></i>Certificate</a>')
        return (f'<div class="su-card su-frontend {"up" if a.get("up") else "down"}">'
                f'{self._head(a, sub, title_html=title)}'
                f'<div class="su-card-body"><p class="su-desc">{esc(a.get("desc"))}</p></div>'
                f'<div class="su-actions">{self._buttons(a, compact=True)}{links}</div></div>')
