// stack-ui phase 0 — read-only dashboard. No mutating calls exist yet; every fetch
// here is a GET, and the server has no endpoint that changes anything.

const $ = (id) => document.getElementById(id);

async function api(path) {
  const r = await fetch(path);
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new Error(body.detail || `${r.status} ${r.statusText}`);
  }
  return r.json();
}

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

  const wrap = $("apps");
  wrap.replaceChildren();
  for (const a of s.apps) {
    const card = el("div", `card ${a.up ? "up" : "down"}`);
    const meta = el("div", "meta");
    const name = el("div", "name");
    // Only link a port we know is answering — a dead link to a down service is worse
    // than no link, since it looks like the app is broken rather than stopped.
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
    wrap.append(card);
  }

  // If the text scrape found nothing, show what stack actually printed rather than an
  // empty page — the parse is the brittle seam here, so it fails visibly, not silently.
  const raw = $("status-raw");
  raw.hidden = s.parsed;
  raw.textContent = s.raw;
  if (!s.parsed) wrap.replaceChildren(el("div", "error", "couldn't parse `stack status` — raw output below"));

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
  if (!k.models.length) kb.append(el("tr")).append(el("td", "dim", "no .gguf files"));

  const active = $("kobold-active");
  if (k.active && !k.active_present) {
    // start.sh refuses to start in this state, so say so here rather than letting it
    // look like a healthy selection.
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
    btn.disabled = false;
  }
}

$("run-doctor").addEventListener("click", runDoctor);

function refresh() { loadStatus(); loadInventory(); }
refresh();
// Status is cheap (one `stack status`); the inventory walk is cached server-side.
setInterval(refresh, 10000);
