"""Credentials: the read-only tokens the stack uses for private git repos.

One card per saved token (by host or owner prefix: ``github.com/OWNER``), never the token
itself: its last four characters, when it was added, when git last used it, which installed
frontends it covers, and what the last **Test** found (GitHub also says when it expires).
**Remove** forgets it. Above the cards, **Add a token** saves one for a repo's owner. Adding and
removing work only from the local network (``lan``, which the server checks again), as
installing from a URL does. The Frontends screen links here (``go("credentials", url=…)``) when
it can't reach a frontend's repo, with that URL filled in.

Server side: ``StackApi.credentials|credential_test|credential_remove|save_credential``
(``services/credential_check.py``, ``services/credentials.py``).
"""
from __future__ import annotations

import js

from wapyt import message

from services.stack_api import StackApi
from ui.common import Listeners, attr, by_id, closest, esc, failed, spawn, toast
from ui.llms import _div

# How to make a read-only token, per host.
_HOW = {
    "github.com": ('<a href="https://github.com/settings/personal-access-tokens/new" '
                   'target="_blank" rel="noreferrer">Create a fine-grained token</a> on GitHub: '
                   'resource owner = the repo\'s owner, only the repos it needs, '
                   '<i>Contents: Read-only</i>.'),
    "gitlab.com": ('<a href="https://gitlab.com/-/user_settings/personal_access_tokens" '
                   'target="_blank" rel="noreferrer">Create a personal access token</a> on '
                   'GitLab with the <i>read_repository</i> scope.'),
}
_SOON_DAYS = 14


def _days_left(date: str):
    """Days until an ISO date (negative when past), or None."""
    try:
        y, m, d = (int(x) for x in date[:10].split("-"))
        when = js.Date.new(y, m - 1, d).getTime()
    except Exception:  # noqa: BLE001 - not a date
        return None
    return int((when - js.Date.now()) // 86_400_000) + 1


class CredentialsView:
    def __init__(self, app, url: str = "", **_):
        self.app = app
        self.listeners = Listeners()
        self.data: dict | None = None
        self.prefill = url
        self.testing: set[str] = set()
        self._lan_drawn: bool | None = None

    def mount(self, root) -> None:
        page = _div(root, "su-page")
        _div(page, "su-page-head",
             '<h1>Credentials</h1><span class="su-dim">read-only tokens for private git repos: '
             'installs, updates and update checks</span>')
        _div(page, "", '<div id="cr-add"></div>')
        _div(page, "su-section",
             '<div class="su-section-head"><h2>Saved</h2><span class="su-dim">git asks your own '
             'login first; these answer when it has none, as in this dashboard</span></div>'
             '<div class="su-grid" id="cr-cards"><div class="su-card su-skeleton"></div></div>'
             '<p class="su-dim" id="cr-uncovered"></p>')
        self.listeners.on(root, "click", self._on_click)
        spawn(self._load(), "credentials")

    def unmount(self) -> None:
        self.listeners.clear()
        self._lan_drawn = None

    def apply_args(self, url: str = "", **_) -> None:
        self.prefill = url
        self._fill_url()

    # --- data ------------------------------------------------------------------
    async def _load(self) -> None:
        r = await StackApi().credentials_async()
        if failed(r, "read the saved tokens"):
            r = {"credentials": [], "frontends": [], "lan": False}
        self.data = r
        self._render()

    def _render(self) -> None:
        if self.data is None or not by_id("cr-cards"):
            return
        self._render_add()
        rows = self.data.get("credentials") or []
        by_id("cr-cards").innerHTML = "".join(self._card(r) for r in rows) or (
            '<p class="su-dim">No tokens saved. A private repo asks for one when an install '
            'can\'t clone it, or add one above.</p>')
        bare = [f for f in self.data.get("frontends") or []
                if not f.get("prefix") and str(f.get("origin") or "").startswith("https://")]
        by_id("cr-uncovered").innerHTML = (
            'Installed frontends without a token (public, or reached with your own git login): '
            + ", ".join(f'<b>{esc(f.get("brand") or f["repo"])}</b>' for f in bare)) if bare else ""

    def _render_add(self) -> None:
        box = by_id("cr-add")
        lan = bool(self.data.get("lan"))
        if not box or lan == self._lan_drawn:
            return
        self._lan_drawn = lan
        if not lan:
            box.innerHTML = (
                '<div class="su-panel"><p class="su-dim"><i class="mdi mdi-lock-outline"></i> '
                'Adding or removing a token works only from your local network: open the '
                'dashboard by the stack machine\'s address or local name, or use '
                '<code>stack credential add &lt;repo url&gt;</code> in a terminal there.</p></div>')
            return
        how = " ".join(_HOW.values())
        box.innerHTML = (
            '<div class="su-panel"><div class="su-panel-head"><h2>Add a token</h2></div>'
            '<div class="su-searchbar su-url-bar">'
            '<input type="url" id="cr-url" spellcheck="false" autocomplete="off" '
            'placeholder="a repo it is for: https://github.com/OWNER/REPO">'
            '<input type="password" id="cr-token" spellcheck="false" autocomplete="off" '
            'placeholder="token">'
            '<button class="su-btn su-primary" id="cr-save"><i class="mdi mdi-key-plus"></i>Save'
            '</button></div>'
            '<p class="su-dim">Saved for the repo\'s owner (<code>github.com/OWNER</code>), so it '
            'covers that owner\'s other repos too; one token per owner. It replaces a token '
            f'already saved there. {how}</p></div>')
        self.listeners.on(by_id("cr-token"), "keydown",
                          lambda e: spawn(self._save(), "save token") if e.key == "Enter" else None)
        self._fill_url()

    def _fill_url(self) -> None:
        box = by_id("cr-url")
        if box and self.prefill:
            box.value = self.prefill
            self.prefill = ""
            tok = by_id("cr-token")
            if tok:
                tok.focus()

    def _card(self, r: dict) -> str:
        prefix = str(r.get("prefix") or "")
        busy = prefix in self.testing
        covers = r.get("covers") or []
        rows = [self._kv("Covers", ", ".join(f'<b>{esc(c)}</b>' for c in covers) if covers
                         else '<span class="su-dim">no installed frontend</span>')]
        rows.append(self._kv("Used", esc(r["used"]) if r.get("used") else
                             '<span class="su-dim">not yet</span>'))
        if r.get("checked"):
            ok = r.get("check_ok")
            cls, icon = {True: ("su-good", "mdi-check-circle-outline"),
                         False: ("su-bad", "mdi-alert-circle-outline")}.get(
                ok, ("su-dim", "mdi-help-circle-outline"))
            rows.append(self._kv("Tested", f'<span class="{cls}"><i class="mdi {icon}"></i> '
                                           f'{esc(r.get("check_note"))}</span> '
                                           f'<span class="su-dim">({esc(r["checked"])})</span>'))
        exp = str(r.get("expires") or "")
        if exp:
            left = _days_left(exp)
            cls = "su-bad" if left is not None and left <= _SOON_DAYS else ""
            when = ("expired" if left is not None and left < 0 else
                    f"in {left} day{'s' if left != 1 else ''}" if left is not None and left <= _SOON_DAYS
                    else "")
            rows.append(self._kv("Expires", f'<span class="{cls}">{esc(exp)}'
                                            f'{" (" + when + ")" if when else ""}</span>'))
        sub = f'{esc(r.get("hint"))} · added {esc(r.get("added") or "?")}'
        if r.get("username"):
            sub += f' · as {esc(r["username"])}'
        lan = bool((self.data or {}).get("lan"))
        dis = " disabled" if busy else ""
        remove = (f'<button class="su-btn su-ghost" data-cr="remove" data-prefix="{esc(prefix)}" '
                  f'title="Forget this token"{dis}><i class="mdi mdi-delete-outline"></i></button>'
                  if lan else "")
        return (
            '<div class="su-card su-frontend">'
            '<div class="su-card-head"><i class="mdi mdi-key-variant su-card-icon"></i>'
            f'<div class="su-card-titles"><div class="su-card-title">{esc(prefix)}</div>'
            f'<div class="su-dim">{sub}</div></div></div>'
            f'<div class="su-card-body">{"".join(rows)}</div>'
            '<div class="su-actions">'
            f'<button class="su-btn" data-cr="test" data-prefix="{esc(prefix)}"{dis} '
            'title="Try it on the frontends it covers (and GitHub, for github.com)">'
            f'<i class="mdi {"mdi-loading mdi-spin" if busy else "mdi-connection"}"></i>'
            f'{"Testing…" if busy else "Test"}</button>'
            f'<span class="su-fe-manage">{remove}</span></div></div>')

    @staticmethod
    def _kv(label: str, value_html: str) -> str:
        return f'<div class="su-kv"><span>{esc(label)}</span><div>{value_html}</div></div>'

    # --- actions ---------------------------------------------------------------
    def _on_click(self, ev) -> None:
        btn = closest(ev, "button")
        if not btn or btn.disabled:
            return
        if btn.id == "cr-save":
            spawn(self._save(), "save token")
        elif attr(btn, "data-cr") == "test":
            spawn(self._test(attr(btn, "data-prefix")), "test token")
        elif attr(btn, "data-cr") == "remove":
            spawn(self._remove(attr(btn, "data-prefix")), "remove token")

    async def _save(self) -> None:
        url_box, tok_box = by_id("cr-url"), by_id("cr-token")
        url = str(url_box.value or "").strip() if url_box else ""
        token = str(tok_box.value or "").strip() if tok_box else ""
        if not url.startswith("https://") or not token:
            toast("Give the https URL of a repo it is for, and the token.", "warning")
            return
        r = await StackApi().save_credential_async(url, token)
        if failed(r, "save the token"):
            return
        url_box.value = ""
        tok_box.value = ""
        toast(f"Saved a token for {r.get('prefix')}.", "success")
        await self._load()
        spawn(self.app.check_frontend_updates(force=True), "frontend update check")
        spawn(self._test(str(r.get("prefix") or "")), "test token")

    async def _test(self, prefix: str) -> None:
        if not prefix or prefix in self.testing:
            return
        self.testing.add(prefix)
        self._render()
        try:
            r = await StackApi().credential_test_async(prefix)
        finally:
            self.testing.discard(prefix)
        if not failed(r, "test the token"):
            ok = r.get("passed")
            toast(f"{prefix}: {r.get('note')}",
                  "success" if ok else "warning" if ok is None else "error", 6000)
        await self._load()

    async def _remove(self, prefix: str) -> None:
        covers = next((r.get("covers") or [] for r in (self.data or {}).get("credentials") or []
                       if r.get("prefix") == prefix), [])
        what = (f" {', '.join(covers)} will then be reached only with your own git login, which "
                "this dashboard doesn't have." if covers else "")
        if not await message.confirm(f"Forget the token for {prefix}?{what} The token itself "
                                     "stays valid on its host until you revoke it there.",
                                     title="Confirm", ok_text="Remove", danger=True):
            return
        r = await StackApi().credential_remove_async(prefix)
        if failed(r, "remove the token"):
            return
        toast(f"Removed the token for {prefix}.", "success")
        await self._load()
        spawn(self.app.check_frontend_updates(force=True), "frontend update check")
