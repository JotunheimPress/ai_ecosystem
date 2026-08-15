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
    tr.append(el("td", "name", m.name), el("td", "size", bytes(m.size)));
    kb.append(tr);
  }
  if (!k.models.length) {
    const tr = el("tr");
    tr.append(el("td", "dim", "no .gguf files"));
    kb.append(tr);
  }

  const active = $("kobold-active");
  if (k.active && !k.active_present) {
    active.className = "missing";
    active.textContent = `selected model is missing from models/: ${k.active}`;
  } else {
    active.className = "dim";
    active.textContent = k.active
      ? `active: ${k.active} (from ${k.source}${k.gpu_layers ? `, ${k.gpu_layers} layers` : ""})`
      : "no selection found";
  }

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
    tr.append(el("td", "name", c.name), el("td", "size", bytes(c.size)));
    ck.append(tr);
  }
  $("ckpt-wrap").hidden = !inv.checkpoints.length;
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
$("btn-on").onclick = () => act("on");
$("btn-off").onclick = () => act("off", null, "Stop both backends and disable boot auto-start?");
$("btn-up").onclick = () => act("up");
$("btn-downall").onclick = () => act("down", "all", "Stop all backends and frontends?");
$("busy-hide").onclick = () => { $("console").hidden = true; };
$("logs-close").onclick = () => closeLogs();
window.addEventListener("beforeunload", () => { closeLogs(); if (actionES) actionES.close(); });

function refresh() { loadStatus(); loadInventory(); }
refresh();
// Don't poll over a running action: the output stream is the live view, and a status
// refresh mid-action just fights the disabled-button state.
setInterval(() => { if (!busy) refresh(); }, 10000);
