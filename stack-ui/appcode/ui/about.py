"""About AI Ecosystem: the release this checkout is on, whether a newer one is out, how to
update, and the project's links. Modelled on IguanaXterm's About dialog, as a screen here
because every other page of the dashboard is one (``#/about``).

The version comes from the overview (``release``, from ``stack status --json``); the check
from ``StackApi.latest_release`` via the app (``app.release``), which also drives the
toolbar's Update badge.
"""
from __future__ import annotations

from services.about import (CHANGELOG_URL, ISSUES_URL, NAME, RELEASES_URL, REPO, REPO_URL,
                            WIKI_URL)
from ui.common import Listeners, by_id, esc, spawn
from ui.llms import _div

_LINKS = (
    ("mdi-book-open-variant", "Wiki", "How to install, run and use it", WIKI_URL),
    ("mdi-github", "Source", REPO, REPO_URL),
    ("mdi-tag-outline", "Releases", "Every release and its notes", RELEASES_URL),
    ("mdi-format-list-bulleted", "Changelog", "What each release changed", CHANGELOG_URL),
    ("mdi-bug-outline", "Issues", "Report a problem or ask for a feature", ISSUES_URL),
)


def _a(href: str, label: str) -> str:
    return f'<a href="{esc(href)}" target="_blank" rel="noopener noreferrer">{esc(label)}</a>'


def release_html(rel: dict) -> str:
    """The line on the release check. ``rel`` is ``StackApi.latest_release()``, or {} while
    it hasn't answered."""
    if not rel:
        return '<i class="mdi mdi-loading mdi-spin"></i> Checking for a newer release…'
    if rel.get("newer"):
        when = f" (released {esc(rel['published'])})" if rel.get("published") else ""
        return (f'<i class="mdi mdi-arrow-up-circle"></i> <b>{NAME} {esc(rel.get("latest"))} '
                f'is out</b>{when}. {_a(rel.get("url") or RELEASES_URL, "What changed")} · '
                'update with the commands below.')
    if rel.get("checked"):
        return '<i class="mdi mdi-check-circle-outline"></i> This is the latest release.'
    reason = rel.get("reason")
    if reason == "off":
        msg = "Release checks are off (<code>STACK_UPDATE_CHECK</code>)."
    elif reason == "private":
        msg = ("Can't check: the repo is private. Save a token with read access to "
               f"{esc(REPO)} (<code>stack credential add https://github.com/{esc(REPO)}</code>), "
               "or run <code>stack version --check</code> in a terminal.")
    elif reason == "token":
        msg = (f"Can't check: the saved token can't read {esc(REPO)}'s releases. "
               "Give it read-only <i>Contents</i> on that repo, or run "
               "<code>stack version --check</code> in a terminal.")
    else:
        msg = "Couldn't reach GitHub to check for a newer release."
    return (f'<i class="mdi mdi-cloud-off-outline"></i> {msg} '
            f'{_a(RELEASES_URL, "See releases")}')


class AboutView:
    def __init__(self, app, **_):
        self.app = app
        self.listeners = Listeners()

    def mount(self, root) -> None:
        page = _div(root, "su-page su-about")
        _div(page, "su-about-head",
             '<span class="su-about-logo">AI</span>'
             f'<div><div class="su-about-name">{NAME} <span class="su-about-version" '
             'id="ab-version"></span></div>'
             '<div class="su-dim su-about-sub" id="ab-sub"></div></div>')
        _div(page, "su-panel su-about-update", '<div id="ab-update"></div>'
             '<button class="su-btn su-ghost" id="ab-recheck" hidden>'
             '<i class="mdi mdi-refresh"></i>Check again</button>')
        _div(page, "su-panel",
             '<div class="su-panel-head"><h2>Updating</h2></div>'
             '<p>In a terminal on the machine running the stack:</p>'
             '<pre class="su-raw su-about-cmds">cd ai-stack\ngit pull --ff-only\n'
             'stack install      # rebuilds what changed, this dashboard included\n'
             'stack version</pre>'
             '<p class="su-dim">The dashboard restarts on its new version during '
             '<code>stack install</code>: reload this page afterwards.</p>')
        _div(page, "su-about-links", "".join(
            f'<a class="su-about-link" href="{esc(url)}" target="_blank" rel="noopener noreferrer">'
            f'<i class="mdi {icon}"></i><span><b>{esc(label)}</b><small>{esc(hint)}</small></span></a>'
            for icon, label, hint, url in _LINKS))
        self.listeners.on(by_id("ab-recheck"), "click",
                          lambda _e: spawn(self.app.check_release(force=True), "release check"))
        if self.app.overview:
            self.on_overview(self.app.overview)
        self.on_release(self.app.release)
        if not self.app.release:
            spawn(self.app.check_release(), "release check")

    def unmount(self) -> None:
        self.listeners.clear()

    def on_overview(self, ov: dict) -> None:
        rel = ov.get("release") if isinstance(ov, dict) else None
        ver, sub = by_id("ab-version"), by_id("ab-sub")
        if not ver:
            return
        if isinstance(rel, dict) and rel.get("version"):
            ver.textContent = rel["version"]
            commit = rel.get("commit")
            where = (f'ai-stack <code>{esc(commit)}</code>'
                     f'{" (modified)" if rel.get("modified") else ""}') if commit else "ai-stack"
        else:
            ver.textContent = ""
            where = "ai-stack (this stack doesn't report its release; update it)"
        sub.innerHTML = f'{where} · backends, frontends and this dashboard · pytincture · wapyt'

    def on_release(self, rel: dict) -> None:
        box, again = by_id("ab-update"), by_id("ab-recheck")
        if not box:
            return
        box.innerHTML = release_html(rel or {})
        box.parentElement.classList.toggle("newer", bool((rel or {}).get("newer")))
        again.hidden = not rel or bool(rel.get("checked")) or rel.get("reason") == "off"
