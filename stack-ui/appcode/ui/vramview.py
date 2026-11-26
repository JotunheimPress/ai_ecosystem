"""VRAM planner: live use, the release button, and "how many layers fit next to that".

Every backend shares one card: KoboldCPP's layers are planned around a ComfyUI checkpoint and
Fortuna's model (weights + its 16K-context KV cache), each of which can be left out. The recurring failure: ComfyUI holds VRAM after a job,
KoboldCPP OOMs the instant it starts. "Release VRAM" is the fix and needs no restart.

The layer count comes from the GGUF header (exact); the VRAM those layers take is an
estimate, and every figure here says so. Live numbers need ComfyUI running — there is no
NVIDIA tooling in this container.
"""
from __future__ import annotations

from wapyt import progress_html

from services.models_api import ModelsApi
from ui.common import RELEASE_TEXT_CONFIRM, Listeners, by_id, esc, failed, fmt_bytes, spawn, toast
from ui.llms import _div


class VramView:
    def __init__(self, app, **_):
        self.app = app
        self.listeners = Listeners()
        self.data: dict = {}
        self.plan: dict = {}

    def mount(self, root) -> None:
        page = _div(root, "su-page")
        _div(page, "su-page-head",
             '<h1>VRAM planner</h1><span class="su-dim">one GPU shared by every backend and the desktop</span>')
        _div(page, "su-panel",
             '<div class="su-panel-head"><h2>Now</h2>'
             '<button class="su-btn" id="vr-free" disabled '
             'title="Ask ComfyUI to unload its models and give the VRAM back">'
             '<i class="mdi mdi-broom"></i>Release ComfyUI VRAM</button>'
             '<button class="su-btn" id="vr-text" disabled></button></div>'
             '<div id="vr-now" class="su-dim">loading…</div>')
        _div(page, "su-panel",
             '<div class="su-panel-head"><h2>Plan</h2>'
             '<span class="su-dim">how many layers of a GGUF fit next to a checkpoint and '
             'Fortuna</span></div>'
             '<div class="su-planner">'
             '<label>Language model<select id="vr-model"></select></label>'
             '<label>Alongside<select id="vr-ckpt"></select></label>'
             '<label>Fortuna<select id="vr-fortuna"></select></label>'
             '</div>'
             '<div id="vr-out" class="su-plan-out"></div>'
             '<div class="su-actions">'
             '<button class="su-btn su-primary" id="vr-apply" hidden></button></div>')
        self.listeners.on(by_id("vr-free"), "click", lambda _e: spawn(self._free(), "free"))
        self.listeners.on(by_id("vr-text"), "click", lambda _e: self._text())
        self.listeners.on(by_id("vr-model"), "change", lambda _e: spawn(self._plan(), "plan"))
        self.listeners.on(by_id("vr-ckpt"), "change", lambda _e: spawn(self._plan(), "plan"))
        self.listeners.on(by_id("vr-fortuna"), "change", lambda _e: spawn(self._plan(), "plan"))
        self.listeners.on(by_id("vr-apply"), "click", lambda _e: spawn(self._apply(), "apply"))
        spawn(self.load(), "load vram")

    def unmount(self) -> None:
        self.listeners.clear()

    async def load(self) -> None:
        r = await ModelsApi().vram_async()
        if failed(r, "read VRAM"):
            return
        self.data = r
        self._render_now()
        models = r.get("models") or []
        options = []
        for m in models:
            label = m["name"] + (f" ({int(m['n_layers'])} layers)" if m.get("n_layers") else "")
            selected = " selected" if m.get("active") else ""
            options.append(f'<option value="{esc(m["name"])}"{selected}>{esc(label)}</option>')
        by_id("vr-model").innerHTML = "".join(options) or '<option value="">no GGUF files</option>'
        by_id("vr-ckpt").innerHTML = '<option value="">no checkpoint</option>' + "".join(
            f'<option value="{esc(c["name"])}">{esc(c["name"])} ({fmt_bytes(c["size"])})</option>'
            for c in r.get("checkpoints") or [])
        # Fortuna runs whenever the stack is up, so its boot model is counted by default, at
        # the GPU layers fortuna.env gives it (0 = CPU only: nothing on the card).
        where = " on the CPU" if r.get("fortuna_ngl") == 0 else ""
        by_id("vr-fortuna").innerHTML = '<option value="">not counted</option>' + "".join(
            f'<option value="{esc(m["name"])}"{" selected" if m.get("selected") else ""}>'
            f'{esc(m["name"])} ({fmt_bytes(m["size"])}){" — boots" + where if m.get("selected") else ""}'
            '</option>' for m in r.get("fortuna") or [])
        await self._plan()

    def _render_now(self) -> None:
        v = self.data.get("vram") or {}
        # Releasing is a ComfyUI call: offered only while it answers, whatever the figures' source.
        by_id("vr-free").disabled = not self.data.get("comfy_up")
        self._render_text_button()
        if not v.get("available"):
            by_id("vr-now").innerHTML = f'<span class="su-dim">{esc(v.get("source"))}</span>'
            return
        total, used = int(v.get("total") or 0), int(v.get("used") or 0)
        by_id("vr-now").innerHTML = (
            f'<div class="su-kv"><span>{esc(v.get("device") or "GPU")}</span><div>'
            + progress_html(used, total or 1, text=f"{fmt_bytes(used)} used of {fmt_bytes(total)}",
                            state="error" if total and used / total > 0.9 else "active")
            + '</div></div>'
            f'<div class="su-kv"><span>Free</span><div>{fmt_bytes(v.get("free"))}'
            f'<span class="su-dim"> — from {esc(v.get("source"))}</span></div></div>'
            + "".join(
                f'<div class="su-kv"><span>{label}</span><div>{fmt_bytes(p.get("used"))} of '
                f'{fmt_bytes(p.get("total"))}</div></div>'
                for key, label in (("vram", "VRAM carve-out"), ("gtt", "GTT (shared system RAM)"))
                if (p := (v.get("parts") or {}).get(key)))
            + (f'<div class="su-kv"><span>Busy</span><div>{int(v["busy"])}%</div></div>'
               if v.get("busy") is not None else ""))

    async def _plan(self) -> None:
        model = str(by_id("vr-model").value or "")
        ckpt = str(by_id("vr-ckpt").value or "")
        fortuna = str(by_id("vr-fortuna").value or "")
        out, btn = by_id("vr-out"), by_id("vr-apply")
        btn.hidden = True
        if not model:
            out.innerHTML = ""
            return
        r = await ModelsApi().plan_async(model, ckpt, fortuna)
        if not isinstance(r, dict) or not r.get("ok"):
            out.innerHTML = f'<span class="su-bad">{esc((r or {}).get("error"))}</span>'
            return
        self.plan = r
        if r.get("suggested_layers") is None:
            out.innerHTML = f'<span class="su-dim">{esc(r.get("note") or "not enough information to plan")}</span>'
            return
        n, total = int(r["suggested_layers"]), int(r.get("n_layers") or 0)
        cls = "su-good" if r.get("fits_fully") else ("su-warn" if n > 0 else "su-bad")
        # Fortuna on the CPU only (NGL=0) takes nothing on the card: fortuna_size is 0.
        room = [f"{fmt_bytes(r.get('checkpoint_size'))} of checkpoint" if ckpt else "",
                f"{fmt_bytes(r.get('fortuna_size'))} for Fortuna" if fortuna and r.get("fortuna_size")
                else ""]
        room = [x for x in room if x]
        alongside = f", leaving room for {' and '.join(room)}" if room else ""
        out.innerHTML = (
            f'<div class="su-plan-big {cls}">{n}<span>/ {total} layers</span></div>'
            f'<div><b>{esc(r.get("note"))}</b><br>'
            f'<span class="su-dim">about {fmt_bytes(r.get("estimated_vram"))} of '
            f'{fmt_bytes(r.get("vram_total"))}{alongside}; '
            f'{fmt_bytes(r.get("headroom"))} held back for the desktop. An estimate — real use '
            'depends on the quant, context and batch size.</span></div>')
        if n > 0:
            btn.hidden = False
            btn.disabled = self.app.busy
            btn.textContent = f"Use {model} with {n} GPU layers"

    async def _apply(self) -> None:
        model = str(by_id("vr-model").value or "")
        n = int(self.plan.get("suggested_layers") or 0)
        r = await ModelsApi().select_llm_async(model, n)
        if failed(r, "save the layer count"):
            return
        k = r.get("kobold") or {}
        toast(f"{model} selected with {n} GPU layers"
              + (" — restart KoboldCPP to apply" if k.get("restart_needed") or k.get("running")
                 else " — applies on its next start"), "success", 6000)
        await self.load()

    def _render_text_button(self) -> None:
        """Release / Restore the text model (stack gpu-release | gpu-restore). Only while text
        is served here: routed to Halo, the loaded model isn't this card's."""
        t = self.data.get("text") or {}
        btn = by_id("vr-text")
        if t.get("released"):
            btn.innerHTML = '<i class="mdi mdi-backup-restore"></i>Restore text model'
            btn.title = "Load the same model again (stack gpu-restore)"
        else:
            btn.innerHTML = '<i class="mdi mdi-broom"></i>Release text model VRAM'
            btn.title = ("Unload KoboldCPP's model and give its GPU memory back "
                         "(stack gpu-release); Restore loads it again")
        btn.hidden = not t.get("local", True)
        btn.disabled = bool(self.app.busy) or not (t.get("loaded") or t.get("released"))

    def _text(self) -> None:
        if (self.data.get("text") or {}).get("released"):
            self.app.act("gpu_restore_text")
        else:
            self.app.act("gpu_release_text", confirm=RELEASE_TEXT_CONFIRM)

    async def _free(self) -> None:
        by_id("vr-free").disabled = True
        r = await ModelsApi().free_vram_async()
        if not failed(r, "release VRAM"):
            toast("ComfyUI released its models.", "success")
        await self.load()

    def on_busy(self, busy: bool) -> None:
        btn = by_id("vr-apply")
        if btn:
            btn.disabled = busy
        if by_id("vr-text"):
            if busy:
                by_id("vr-text").disabled = True
            else:
                spawn(self.load(), "reload vram")   # a release or restore just finished
