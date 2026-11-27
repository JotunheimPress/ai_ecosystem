"""The two workspaces: Infrastructure (the landing page) and Frontends.

**Infrastructure** is the shared backends, grouped by what they provide rather than by
name: the router on top (where each capability is served, and where text is routed), then
one card per capability (text, image and sound, decision), then the GPU. Which card body a
backend gets comes from its `capability` in `stack status --json`, via
``services.backends.ADAPTERS``, so a new backend is a row in the stack, not a name check here.

**Frontends** is the apps that use them: cards for the installed ones, with HTTPS links where a
frontend offers them, an Official / Custom label, a "vX.Y.Z available" badge when its origin has
a newer release (`stack frontend check`), Update and Remove, and the problems `stack`
found in their frontend.json; then the official frontends not installed yet, with Install
(`stack frontend available|add|update|remove`; the browser sends a name); then installing
any frontend from its git URL, which only a browser on the local network may do
(`url_install` from the server, which checks again: `stackctl.REQUEST_LAN`).

Rendered as escaped HTML with one delegated click listener per view; every value that
reaches the markup goes through ``esc``.
"""
from __future__ import annotations

from wapyt import progress_html

from services.models_api import ModelsApi
from services.stack_api import StackApi
from ui.common import (
    RELEASE_TEXT_CONFIRM,
    Listeners,
    ago,
    attr,
    by_id,
    closest,
    esc,
    failed,
    fmt_bytes,
    spawn,
    toast,
)

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
    ("gpu_release_text", ""): RELEASE_TEXT_CONFIRM,
}


# What a frontend needs, in the words the Infrastructure cards use.
_NEEDS = {"text": "text", "image-sound": "image and sound", "decision": "decisions"}


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
            self.app.act(verb, target,
                         confirm=attr(btn, "data-confirm") or _CONFIRM.get((verb, target), ""),
                         danger=verb == "frontend_remove")
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
        if cap == "text" and local.get("state") == "released":
            # Released (`stack gpu-release`): Restore loads the same model in its running
            # container; Stop and Logs still act on that container.
            dis, name = self._dis(), esc(a["name"])
            actions = (f'<button class="su-btn su-primary" data-act="gpu_restore_text"{dis} '
                       'title="Load the same model again (stack gpu-restore)">'
                       '<i class="mdi mdi-backup-restore"></i>Restore</button>'
                       f'<button class="su-btn" data-act="down" data-target="{name}"{dis}>'
                       '<i class="mdi mdi-stop"></i>Stop</button>'
                       f'<button class="su-btn su-ghost" data-logs="{name}" title="Follow its log">'
                       '<i class="mdi mdi-text-long"></i>Logs</button>')
        elif remote:
            # Every button acts on the local copy, and says so; the pill shows what is served.
            local_up = local.get("state") in ("up", "loading")
            actions = self._buttons(a, up=local_up, noun="local copy")
        else:
            actions = self._buttons(a)
        pill = self._pill(False, "released") if cap == "text" and local.get("state") == "released" else ""
        return (f'<div class="su-card su-backend {"up" if a.get("up") else "down"}">'
                f'{self._head(a, sub, pill=pill)}<div class="su-card-body">{body}</div>'
                f'<div class="su-actions">{actions}{self._extra(a, local)}</div></div>')

    def _extra(self, a: dict, local: dict | None = None) -> str:
        cap = a.get("capability")
        if cap == "text":
            # Release: this machine's copy is loaded (served here, or kept warm while text is
            # routed to Halo). Its memory goes back to the card until Restore.
            state = (local or {}).get("state")
            loaded_here = state == "up" or (state is None and a.get("up")
                                            and self._route().get("where") != "halo")
            release = ('<button class="su-btn su-ghost" data-act="gpu_release_text"'
                       f'{self._dis()} title="Unload the model and give its GPU memory back for '
                       'image work or games (stack gpu-release); Restore loads it again">'
                       '<i class="mdi mdi-broom"></i>Release VRAM</button>') if loaded_here else ""
            return (release + '<button class="su-btn su-ghost" data-go="llms">'
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
        elif local.get("state") == "released" and not remote:
            rows.append(self._kv("Loaded", '<span class="su-dim">nothing — released, its GPU memory '
                                           'is free for other work. Restore loads it again.</span>'))
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
        up = a.get("up") and f.get("up")
        if up:
            rows = [self._kv("Loaded", f'<b class="su-model">{esc(f.get("model") or "unnamed")}</b>'),
                    self._kv("Answers", '<span class="su-dim">yes/no, choice and score questions '
                                        '(/v1/systemone)</span>')]
        else:
            rows = [self._kv("Loaded", '<span class="su-dim">nothing — not running</span>')]
        sel, integ = f.get("selected"), f.get("selected_integrity")
        # The file it boots, when that is news: not running, about to change, or not verified.
        if sel and (not up or f.get("restart_needed") or integ != "verified"):
            note = {"verified": ("su-dim", "sha256 verified"),
                    "unchecked": ("su-warn", "not verified yet — checked on its next start"),
                    "mismatch": ("su-bad", "failed its sha256 check — it will not start"),
                    "absent": ("su-bad", "not downloaded"),
                    "partial": ("su-bad", "download not finished")}.get(
                        integ, ("su-bad", "not in models.table — it will not start"))
            rows.append(self._kv("Boots", f'{esc(sel)} <span class="{note[0]}">· {note[1]}</span>'))
        # Where it runs, when that isn't the default (all layers on the GPU).
        if f.get("ngl_invalid"):
            rows.append(self._kv("GPU", '<span class="su-bad">NGL= in fortuna.env is not a whole '
                                        'number — it will not start</span>'))
        elif f.get("ngl_effective") == 0:
            rows.append(self._kv("GPU", '<span class="su-dim">none — runs on the CPU '
                                        '(NGL=0)</span>'))
        elif f.get("ngl") is not None and f["ngl"] < 99:
            rows.append(self._kv("GPU", f'{int(f["ngl"])} layers <span class="su-dim">'
                                        '(NGL in fortuna.env)</span>'))
        if f.get("restart_needed"):
            rows.append(
                '<div class="su-banner"><i class="mdi mdi-information-outline"></i>'
                f'<span>Selected <b>{esc(sel)}</b> — restart Fortuna to load it.</span>'
                f'<button class="su-btn su-primary" data-act="restart" data-target="{esc(a["name"])}"'
                f'{self._dis()}>Restart</button></div>')
        return "".join(rows)

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
        self.catalog: dict | None = None     # StackApi.frontend_catalog(); None until it answers
        self._url_mode: bool | None = None   # the URL box as rendered: LAN or not; None = not yet

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
            '<section class="su-section">'
            '<div class="su-section-head"><h2>Available</h2>'
            '<span class="su-dim">official frontends you can install</span></div>'
            '<div id="fe-cat-warn"></div>'
            '<div class="su-grid" id="fe-available"><div class="su-card su-skeleton"></div></div>'
            '</section>'
            '<section class="su-section">'
            '<div class="su-section-head"><h2>From a git URL</h2>'
            '<span class="su-dim">any other frontend: a repo with a frontend.json</span></div>'
            '<div id="fe-url"></div>'
            '</section>'
            '<pre class="su-raw" id="fe-raw" hidden></pre>'
            '</div>')
        self.listeners.on(root, "click", self._on_click)
        spawn(self._load_catalog(), "frontend catalog")

    def unmount(self) -> None:
        self.listeners.clear()
        self._url_mode = None

    def _on_click(self, ev) -> None:
        btn = closest(ev, "button")
        if btn and not btn.disabled and btn.id == "fe-url-go":
            self._install_url()
            return
        super()._on_click(ev)

    def _install_url(self) -> None:
        box = by_id("fe-url-input")
        url = str(box.value or "").strip() if box else ""
        if not url:
            toast("Paste the frontend's git URL first.", "warning")
            return
        # Danger style: Cancel has focus. The server checks the URL and where it came from.
        self.app.act("frontend_add_url", url, danger=True, confirm=(
            f"Install the frontend at {url}? It isn't on the official list, so nobody has "
            "reviewed it: its build and start scripts run with control of this machine's "
            "containers. Only install a repo you trust. It is cloned next to ai-stack at its "
            "newest release, checked against the stack, and built; the log shows each step."))

    def _render_url(self) -> None:
        """The URL box, drawn once per mode so polling never wipes what's being typed."""
        box = by_id("fe-url")
        if not box or self.catalog is None:
            return
        local = bool(self.catalog.get("url_install"))
        if local != self._url_mode:
            self._url_mode = local
            if local:
                box.innerHTML = (
                    '<div class="su-searchbar su-url-bar">'
                    '<input type="url" id="fe-url-input" spellcheck="false" autocomplete="off" '
                    'placeholder="https://github.com/owner/repo.git  or  git@github.com:owner/repo">'
                    '<button class="su-btn su-primary" id="fe-url-go">'
                    '<i class="mdi mdi-download"></i>Install</button></div>'
                    '<p class="su-dim">Its newest release is installed next to ai-stack and built, '
                    'after you confirm. Nobody reviewed it, so only install a repo you trust. A '
                    'private GitHub repo needs <code>GITHUB_TOKEN</code> with read access in '
                    '<code>stack-ui/tokens.env</code>. Shown as Custom once installed.</p>')
                self.listeners.on(by_id("fe-url-input"), "keydown",
                                  lambda e: self._install_url() if e.key == "Enter" else None)
            else:
                box.innerHTML = (
                    '<p class="su-dim"><i class="mdi mdi-lock-outline"></i> Only from your local '
                    'network, since it runs code nobody reviewed: open the dashboard by the stack '
                    'machine\'s address or local name (<code>http://&lt;host&gt;:7863</code>, '
                    '<code>&lt;host&gt;.local</code>), or run <code>stack frontend add &lt;url&gt;</code> '
                    'in a terminal there.</p>')
        go = by_id("fe-url-go")
        if go:
            go.disabled = bool(self.app.busy)

    def on_overview(self, ov: dict) -> None:
        self.ov = ov
        self._render()

    def on_frontend_updates(self) -> None:
        self._render()

    def on_busy(self, busy: bool) -> None:
        self._render()
        if not busy:                      # an install, update or remove may have just finished
            spawn(self._load_catalog(), "frontend catalog")

    async def _load_catalog(self) -> None:
        r = await StackApi().frontend_catalog_async()
        if not isinstance(r, dict) or not r.get("ok"):
            r = {"frontends": [], "warnings": ["couldn't read the official frontend list"]}
        self.catalog = r
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
            '<p class="su-dim">No frontends installed. Install an official one below, or clone '
            'any repo with a frontend.json next to ai-stack.</p>')
        self._render_available()
        self._render_url()
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
        # The label goes at the start of the description, not in the subtitle: the subtitle is
        # one line that ellipsises, and a long repo name would hide the label.
        listed = {f.get("repo"): f for f in (self.catalog or {}).get("frontends") or []}
        label = ""
        if self.catalog is not None:
            official = bool(listed.get(a["name"], {}).get("official"))
            tip = ("Installed from the official list" if official else
                   "Not from the official list: a repo you cloned yourself, or a fork")
            label = (f'<span class="su-tag {"su-official" if official else "su-custom"}" '
                     f'title="{tip}">{"Official" if official else "Custom"}</span> ')
        # A newer release on its origin (stack frontend check, via the app's cached check).
        upd = (self.app.frontend_updates or {}).get(a["name"]) or {}
        latest = str(upd.get("latest") or "") if upd.get("newer") else ""
        if latest:
            now = f'; this is {upd["current"]}' if upd.get("current") else ""
            label += (f'<span class="su-tag su-newer" title="{esc(f"{latest} is out{now}. Update installs it.")}">'
                      f'<i class="mdi mdi-arrow-up-circle"></i>{esc(latest)} available</span> ')
        manage = self._manage_buttons(a["name"], a.get("label") or a["name"], latest)
        return (f'<div class="su-card su-frontend {"up" if a.get("up") else "down"}">'
                f'{self._head(a, sub, title_html=title)}'
                f'<div class="su-card-body"><p class="su-desc">{label}{esc(a.get("desc"))}</p></div>'
                f'<div class="su-actions">{self._buttons(a, compact=True)}{links}'
                f'<span class="su-fe-manage">{manage}</span></div></div>')

    def _manage_buttons(self, repo: str, brand: str, latest: str = "") -> str:
        """Update and Remove, each confirmed with what it does. ``latest``: a newer release is
        out, so Update is labelled and green like the badge."""
        update = (f"Update {brand}? It moves to its newest release (or pulls its branch) and "
                  "rebuilds its image. If it is running, restart it afterwards to use the new "
                  "version.")
        up_cls, up_label = ("su-newer", "Update") if latest else ("su-ghost", "")
        up_tip = f"Update to {latest}, rebuilt" if latest else "Update: its newest release, rebuilt"
        remove = (f"Remove {brand} completely? It is stopped, and its container, its image and "
                  "its folder next to ai-stack are deleted, including everything it saved "
                  "(characters, chats, outputs) and any local changes. This can't be undone; "
                  "the log lists what was there.")
        dis = self._dis()
        return (f'<button class="su-btn {up_cls}" data-act="frontend_update" data-target="{esc(repo)}" '
                f'data-confirm="{esc(update)}" title="{esc(up_tip)}"{dis}>'
                f'<i class="mdi mdi-update"></i>{up_label}</button>'
                f'<button class="su-btn su-ghost" data-act="frontend_remove" data-target="{esc(repo)}" '
                f'data-confirm="{esc(remove)}" title="Remove: stop it, delete its image and folder"{dis}>'
                '<i class="mdi mdi-delete-outline"></i></button>')

    def _render_available(self) -> None:
        box, warn = by_id("fe-available"), by_id("fe-cat-warn")
        if not box or self.catalog is None:
            return
        warn.innerHTML = "".join(
            f'<div class="su-banner"><i class="mdi mdi-alert-outline"></i><span>{esc(w)}</span></div>'
            for w in self.catalog.get("warnings") or [])
        rows = [f for f in self.catalog.get("frontends") or [] if not f.get("installed")]
        listed = self.catalog.get("frontends") or []
        box.innerHTML = "".join(self._available_card(f) for f in rows) or (
            '<p class="su-dim">' + ("Every official frontend is installed." if listed else
                                    "No official frontends are listed in this release yet.")
            + '</p>')

    def _available_card(self, f: dict) -> str:
        repo, brand = esc(f.get("repo")), esc(f.get("brand") or f.get("repo"))
        icon = f.get("icon") or "mdi-application-outline"
        needs = "".join(f'<span class="su-tag">{esc(_NEEDS.get(n, n))}</span>'
                        for n in f.get("needs") or [])
        problems = f.get("problems") or []
        sub = (f'{repo} · ' if f.get("brand") and f["brand"] != f.get("repo") else "") + f':{int(f.get("port") or 0)}'
        body = f'<p class="su-desc">{esc(f.get("description"))}</p>'
        if needs:
            body += self._kv("Uses", needs)
        if problems:
            body += "".join(f'<p class="su-bad"><i class="mdi mdi-alert-circle-outline"></i> {esc(p)}</p>'
                            for p in problems)
        dis = " disabled" if self.app.busy or problems else ""
        confirm = esc(f"Install {f.get('brand') or f.get('repo')}? It is cloned from "
                      f"{f.get('url')} next to ai-stack, checked against the official list, "
                      "and its image is built. That can take several minutes; the log shows "
                      "each step.")
        return (
            '<div class="su-card su-frontend su-available">'
            '<div class="su-card-head">'
            f'<i class="mdi {esc(icon)} su-card-icon"></i>'
            f'<div class="su-card-titles"><div class="su-card-title">{brand}</div>'
            f'<div class="su-dim">{sub}</div></div>'
            '<span class="su-tag su-official">Official</span></div>'
            f'<div class="su-card-body">{body}</div>'
            f'<div class="su-actions"><button class="su-btn su-primary" data-act="frontend_add" '
            f'data-target="{repo}" data-confirm="{confirm}"{dis}>'
            '<i class="mdi mdi-download"></i>Install</button></div></div>')
