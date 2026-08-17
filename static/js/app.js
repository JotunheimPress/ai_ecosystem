// stack-ui phase 1 — status, control, and log streaming.
//
// Every mutation is a POST to /api/action carrying the X-Stack-UI header the server
// requires; the browser never names a command, only a verb key and a target the server
// re-validates against the live registry.

const $ = (id) => document.getElementById(id);

async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new Error(body.detail || `${r.status} ${r.statusText}`);
  }
  return r.json();
}

const post = (verb, target) => api("/api/action", {
  method: "POST",
  headers: { "Content-Type": "application/json", "X-Stack-UI": "1" },
  body: JSON.stringify({ verb, target: target || null }),
});

function bytes(n) {
  if (n == null) return "—";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n < 10 && i > 0 ? n.toFixed(1) : Math.round(n)} ${u[i]}`;
}

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

// --- actions --------------------------------------------------------------
let actionES = null;   // EventSource for the running job
let busy = false;

function setBusy(on, label) {
  busy = on;
  $("busy").hidden = !on;
  $("busy-label").textContent = label || "";
  // Disabling every control while an action runs is the cheap way to keep the single
  // server-side slot from being hit with a 409 the user didn't ask for.
  document.querySelectorAll("button").forEach((b) => {
    if (b.id !== "busy-hide" && b.id !== "logs-close") b.disabled = on;
  });
}

function watchJob(job) {
  const out = $("console");
  out.hidden = false;
  out.textContent = "";
  setBusy(true, job.label);
  if (actionES) actionES.close();
  actionES = new EventSource(`/api/action/${encodeURIComponent(job.id)}/events`);
  actionES.onmessage = (ev) => {
    const d = JSON.parse(ev.data);
    if (d.type === "line") {
      out.textContent += d.text + "\n";
      out.scrollTop = out.scrollHeight;
    } else if (d.type === "done") {
      if (d.error) out.textContent += `\n!! ${d.error}\n`;
      else out.textContent += `\n[exit ${d.exit_code}]\n`;
      out.scrollTop = out.scrollHeight;
      actionES.close();
      actionES = null;
      setBusy(false);
      refresh();
    }
  };
  actionES.onerror = () => {
    // The stream ends when the job does; only treat it as an error if we never finished.
    if (actionES) { actionES.close(); actionES = null; setBusy(false); refresh(); }
  };
}

async function act(verb, target, confirmMsg) {
  if (busy) return;
  if (confirmMsg && !confirm(confirmMsg)) return;
  try {
    watchJob(await post(verb, target));
  } catch (e) {
    setBusy(false);
    const out = $("console");
    out.hidden = false;
    out.textContent = `!! ${e.message}\n`;
  }
}

// --- logs -----------------------------------------------------------------
let logsES = null;

function openLogs(name) {
  closeLogs();
  $("logs-section").hidden = false;
  $("logs-app").textContent = name;
  const pre = $("logs");
  pre.textContent = "";
  logsES = new EventSource(`/api/logs/${encodeURIComponent(name)}/events`);
  logsES.onmessage = (ev) => {
    const d = JSON.parse(ev.data);
    if (d.type === "line") {
      pre.textContent += d.text + "\n";
      // Only follow the tail if the reader hasn't scrolled up to look at something.
      const atBottom = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 40;
      if (atBottom) pre.scrollTop = pre.scrollHeight;
    } else if (d.type === "done") {
      pre.textContent += `\n[stream ended]\n`;
      closeLogs(true);
    }
  };
  logsES.onerror = () => { pre.textContent += "\n[stream closed]\n"; closeLogs(true); };
  $("logs-section").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function closeLogs(keepPanel) {
  if (logsES) { logsES.close(); logsES = null; }   // closing the SSE kills podman logs -f
  if (!keepPanel) $("logs-section").hidden = true;
}

// --- status ---------------------------------------------------------------
async function loadStatus() {
  let s;
  try {
    s = await api("/api/status");
  } catch (e) {
    $("apps").replaceChildren(el("div", "error", `stack status failed: ${e.message}`));
    return;
  }

  const sw = $("switch");
  sw.textContent = s.switch ? `backend ${s.switch}` : "switch unknown";
  sw.className = "switch " + (s.switch ? s.switch.toLowerCase() : "unknown");
  $("btn-on").hidden = s.switch === "ON";
  $("btn-off").hidden = s.switch !== "ON";

  const wrap = $("apps");
  wrap.replaceChildren();
  for (const a of s.apps) {
    const card = el("div", `card ${a.up ? "up" : "down"}`);
    const meta = el("div", "meta");
    const name = el("div", "name");
    if (a.up) {
      const link = el("a", null, a.name);
      link.href = `http://localhost:${a.port}/`;
      link.target = "_blank";
      link.rel = "noreferrer";
      name.append(link);
    } else {
      name.textContent = a.name;
    }
    meta.append(name, el("span", "state", a.up ? "up" : "down"));
    card.append(meta, el("div", "desc", a.desc), el("div", "group", `${a.group} · :${a.port}`));

    const row = el("div", "actions");
    if (a.self) {
      // Start/stop/restart are refused server-side (it would kill the request), but now
      // that this is a container its log is a normal `podman logs` like any other.
      row.append(el("span", "dim", "this UI"));
    } else {
      if (a.up) {
        const stop = el("button", null, "stop");
        stop.onclick = () => act("down", a.name);
        const restart = el("button", null, "restart");
        restart.onclick = () => act("restart", a.name);
        row.append(stop, restart);
      } else {
        const start = el("button", "primary", "start");
        start.onclick = () => act("start", a.name);
        row.append(start);
      }
    }
    {
      const lg = el("button", "link", "logs");
      lg.onclick = () => openLogs(a.name);
      row.append(lg);
    }
    card.append(row);
    wrap.append(card);
  }

  const raw = $("status-raw");
  raw.hidden = s.parsed;
  raw.textContent = s.raw;
  if (!s.parsed) wrap.replaceChildren(el("div", "error", "couldn't parse `stack status` — raw output below"));

  // A job started in another tab (or before a reload) still owns the slot — pick it up
  // rather than letting this page think it's idle.
  if (s.busy && s.job && !actionES) watchJob(s.job);

  $("updated").textContent = `updated ${new Date().toLocaleTimeString()} · ${s.duration_ms} ms`;
}

// --- inventory ------------------------------------------------------------
async function loadInventory() {
  let inv;
  try {
    inv = await api("/api/inventory");
  } catch (e) {
    $("disk").textContent = `inventory failed: ${e.message}`;
    return;
  }

  const d = inv.disk;
  $("disk").textContent = `${bytes(d.free)} free of ${bytes(d.total)} on ${d.path}`;

  const k = inv.kobold;
  $("kobold-dir").textContent = k.dir;
  const kb = $("kobold").querySelector("tbody");
  kb.replaceChildren();
  for (const m of k.models) {
    const tr = el("tr", m.active ? "active" : null);
    const acts = el("td", "rowacts");
    if (!m.active) {
      const use = el("button", "link", "use");
      use.title = "write koboldcpp/model.env — applies on the next start";
      use.onclick = () => selectKobold(m.name);
      acts.append(use);
    }
    if (m.running) acts.append(el("span", "loaded", "loaded"));
    const del = el("button", "link danger-link", "delete");
    del.onclick = () => deleteModel("kobold", m.name, m.size);
    acts.append(del);
    tr.append(el("td", "name", m.name), el("td", "size", bytes(m.size)), acts);
    kb.append(tr);
  }
  if (!k.models.length) {
    const tr = el("tr");
    tr.append(el("td", "dim", "no .gguf files"));
    kb.append(tr);
  }

  const pending = $("kobold-pending");
  if (k.restart_needed) {
    pending.replaceChildren(
      el("span", null, `selected ${k.active} — loaded ${k.running}. Restart to apply.`));
    const btn = el("button", "primary", "restart koboldcpp");
    btn.onclick = () => act("restart", "koboldcpp");
    btn.disabled = busy;
    pending.append(btn);
    pending.hidden = false;
  } else {
    pending.hidden = true;
  }

  const active = $("kobold-active");
  if (k.active && !k.active_present) {
    active.className = "missing";
    active.textContent = `selected model is missing from models/: ${k.active}`;
  } else {
    active.className = "dim";
    active.textContent = k.active
      ? `selected: ${k.active} (from ${k.source}${k.gpu_layers ? `, ${k.gpu_layers} layers` : ""})`
        + (k.running ? ` · loaded: ${k.running}` : " · not running")
      : "no selection found";
  }
  $("layers").value = k.gpu_layers || "";
  $("layers").dataset.model = k.active || "";

  $("comfy-dir").textContent = inv.comfyui.dir;
  const cb = $("comfy").querySelector("tbody");
  cb.replaceChildren();
  for (const kind of inv.comfyui.kinds) {
    const tr = el("tr");
    tr.append(el("td", "name", kind.kind),
              el("td", "size", `${kind.count} file${kind.count === 1 ? "" : "s"}`),
              el("td", "size", bytes(kind.size)));
    cb.append(tr);
  }

  const ck = $("checkpoints").querySelector("tbody");
  ck.replaceChildren();
  for (const c of inv.checkpoints) {
    const tr = el("tr");
    const acts = el("td", "rowacts");
    const del = el("button", "link danger-link", "delete");
    del.onclick = () => deleteModel("checkpoint", c.name, c.size);
    acts.append(del);
    tr.append(el("td", "name", c.name), el("td", "size", bytes(c.size)), acts);
    ck.append(tr);
  }
  $("ckpt-wrap").hidden = !inv.checkpoints.length;
}

// --- model management (phase 2) -------------------------------------------
// Both of these write, so both carry the X-Stack-UI header the server demands. Neither
// restarts anything: selecting a model is a file write, and applying it is the ordinary
// restart action the user triggers deliberately.

async function selectKobold(model, gpuLayers) {
  try {
    await api("/api/kobold/select", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Stack-UI": "1" },
      body: JSON.stringify({ model, gpu_layers: gpuLayers ?? null }),
    });
    loadInventory();
  } catch (e) {
    alert(`could not select ${model}: ${e.message}`);
  }
}

async function deleteModel(kind, name, size) {
  if (!confirm(`Delete ${name}? This frees ${bytes(size)} and cannot be undone.`)) return;
  try {
    await api(`/api/models/${encodeURIComponent(kind)}/${encodeURIComponent(name)}`, {
      method: "DELETE",
      headers: { "X-Stack-UI": "1" },
    });
    loadInventory();
  } catch (e) {
    // The server refuses to delete the selected or the loaded model; that reason is
    // worth showing verbatim, since it tells you what to do first.
    alert(`could not delete ${name}: ${e.message}`);
  }
}

// --- model browsing + downloads (phase 3) ---------------------------------
// huggingface.co can't be iframed, so this is our own UI over their JSON API. The fit
// bands are advisory: VRAM is read from ComfyUI's /system_stats, and when that's
// unavailable the hint is suppressed rather than guessed.

let dlES = null;

async function hubSearch() {
  const q = $("hub-q").value.trim();
  const kind = $("hub-kind").value;
  const out = $("hub-results");
  if (!q) return;
  out.replaceChildren(el("p", "dim", "searching…"));
  try {
    const { results } = await api(
      `/api/hub/search?q=${encodeURIComponent(q)}&kind=${kind === "kobold" ? "gguf" : "safetensors"}`);
    out.replaceChildren();
    if (!results.length) return out.append(el("p", "dim", "nothing found"));
    for (const r of results) out.append(repoRow(r, kind));
  } catch (e) {
    out.replaceChildren(el("div", "error", `search failed: ${e.message}`));
  }
}

function repoRow(r, kind) {
  const d = el("details", "repo");
  const sum = el("summary");
  sum.append(el("span", "rid", r.id));
  if (r.gated) sum.append(el("span", "gated", "gated"));
  const stats = el("span", "stats",
    `${r.downloads ? r.downloads.toLocaleString() + " dl" : ""}${r.likes ? " · ♥ " + r.likes : ""}`);
  sum.append(stats);
  d.append(sum);
  const files = el("div", "files");
  files.append(el("p", "dim", "open to list files…"));
  d.append(files);
  // Fetch the file list lazily: one request per repo you actually open, not per result.
  d.addEventListener("toggle", async () => {
    if (!d.open || d.dataset.loaded) return;
    d.dataset.loaded = "1";
    try {
      const data = await api(`/api/hub/files?repo=${encodeURIComponent(r.id)}&kind=${kind}`);
      files.replaceChildren();
      if (!data.files.length) return files.append(el("p", "dim", "no matching files in this repo"));
      if (!data.vram_total) {
        files.append(el("p", "hintnote",
          "VRAM unknown (ComfyUI is down) — showing sizes without fit hints."));
      }
      const tbl = el("table");
      const body = el("tbody");
      for (const f of data.files) body.append(fileRow(r.id, f, kind));
      tbl.append(body);
      files.append(tbl);
    } catch (e) {
      files.replaceChildren(el("div", "error", e.message));
    }
  });
  return d;
}

function fileRow(repo, f, kind) {
  const tr = el("tr");
  const meta = el("td", "rowacts");
  const band = el("span", `band ${f.fit.band}`, f.fit.band);
  if (f.fit.note) band.title = f.fit.note;
  meta.append(band);
  const btn = el("button", "link", "download");
  if (f.fit.disk_ok === false) {
    btn.disabled = true;
    btn.title = "not enough free disk";
  }
  btn.onclick = () => startDownload(repo, f.path, kind, f.size);
  meta.append(btn);
  tr.append(el("td", "name", f.name), el("td", "size", bytes(f.size)), meta);
  return tr;
}

async function startDownload(repo, path, kind, size) {
  const activate = kind === "kobold" &&
    confirm(`Download ${path.split("/").pop()} (${bytes(size)})?\n\n` +
            "OK = download and select it for KoboldCPP (applies on next restart)\n" +
            "Cancel = just download");
  try {
    watchDownload(await api("/api/downloads", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Stack-UI": "1" },
      body: JSON.stringify({ repo, path, kind, activate }),
    }));
  } catch (e) {
    alert(`could not start: ${e.message}`);
  }
}

function watchDownload(d) {
  const box = $("dl-active");
  box.hidden = false;
  if (dlES) dlES.close();
  dlES = new EventSource(`/api/downloads/${encodeURIComponent(d.id)}/events`);
  dlES.onmessage = (ev) => {
    const p = JSON.parse(ev.data);
    box.replaceChildren();
    box.append(el("span", null, `${p.label}`));
    if (p.state === "running") {
      const bar = el("div", "bar");
      bar.append(el("span"));
      bar.firstChild.style.width = `${p.pct || 0}%`;
      box.append(bar, el("span", "dim",
        `${bytes(p.received)}${p.total ? " / " + bytes(p.total) : ""}` +
        `${p.rate ? " · " + bytes(p.rate) + "/s" : ""}${p.eta ? " · " + p.eta + "s left" : ""}`));
      const cancel = el("button", "danger", "cancel");
      cancel.onclick = async () => {
        await fetch(`/api/downloads/${p.id}`, { method: "DELETE", headers: { "X-Stack-UI": "1" } });
      };
      box.append(cancel);
    } else {
      box.append(el("span", p.state === "done" ? "dim" : "missing",
        p.state === "done" ? `done — ${bytes(p.received)}` : `${p.state}: ${p.error || ""}`));
      if (p.error && p.state === "done") box.append(el("span", "missing", p.error));
      dlES.close();
      dlES = null;
      loadInventory();
    }
  };
  dlES.onerror = () => { if (dlES) { dlES.close(); dlES = null; } };
}

// --- doctor ---------------------------------------------------------------
async function runDoctor() {
  const btn = $("run-doctor");
  const out = $("doctor");
  btn.disabled = true;
  out.replaceChildren(el("p", "dim", "running…"));
  try {
    const d = await api("/api/doctor");
    out.replaceChildren();
    for (const w of d.warnings) {
      const line = el("div", "checkline warn");
      line.append(el("span", "mark", "!"), el("span", null, w));
      out.append(line);
    }
    for (const o of d.ok) {
      const line = el("div", "checkline ok");
      line.append(el("span", "mark", "✓"), el("span", null, o));
      out.append(line);
    }
    const det = el("details");
    det.append(el("summary", null, "raw output"), el("pre", null, d.raw));
    out.append(det);
  } catch (e) {
    out.replaceChildren(el("div", "error", `doctor failed: ${e.message}`));
  } finally {
    btn.disabled = busy;
  }
}

// --- wiring ---------------------------------------------------------------
$("run-doctor").onclick = runDoctor;
$("hub-search").onclick = hubSearch;
$("hub-q").addEventListener("keydown", (e) => { if (e.key === "Enter") hubSearch(); });
$("layers-save").onclick = () => {
  const f = $("layers");
  if (!f.dataset.model) return alert("no model selected yet");
  selectKobold(f.dataset.model, f.value === "" ? null : Number(f.value));
};
$("btn-on").onclick = () => act("on");
$("btn-off").onclick = () => act("off", null, "Stop both backends and disable boot auto-start?");
$("btn-up").onclick = () => act("up");
$("btn-downall").onclick = () => act("down", "all", "Stop all backends and frontends?");
$("busy-hide").onclick = () => { $("console").hidden = true; };
$("logs-close").onclick = () => closeLogs();
window.addEventListener("beforeunload", () => { closeLogs(); if (actionES) actionES.close(); });

async function resumeWatch() {
  try {
    const { download } = await api("/api/downloads");
    if (download && download.state === "running" && !dlES) watchDownload(download);
  } catch { /* nothing running */ }
}

function refresh() { loadStatus(); loadInventory(); }
resumeWatch();
refresh();
// Don't poll over a running action: the output stream is the live view, and a status
// refresh mid-action just fights the disabled-button state.
setInterval(() => { if (!busy) refresh(); }, 10000);
