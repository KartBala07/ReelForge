// ReelForge UI
const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];

let STATE = { profiles: [], sources: [], renders: [], topics: [], deps: {} };
let LINES = [];          // [{text, lines:[..], theme}]
let ES = null;
let CURRENT = { profile: null, source: null, topic: "" };

// ---------------------------------------------------------------- helpers
const fmtNum = (n) => (n || 0).toLocaleString();
const fmtBytes = (b) => {
  if (!b) return "0 MB";
  const u = ["B", "KB", "MB", "GB"];
  let i = 0, n = b;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(n < 10 && i > 0 ? 1 : 0)} ${u[i]}`;
};
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function toast(msg, isErr) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast on" + (isErr ? " err" : "");
  clearTimeout(t._h);
  t._h = setTimeout(() => (t.className = "toast"), 3600);
}

async function api(path, opts) {
  const r = await fetch(path, Object.assign({ headers: { "Content-Type": "application/json" } }, opts || {}));
  const ct = r.headers.get("content-type") || "";
  const body = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw Object.assign(new Error((body && body.error) || r.statusText), { body });
  return body;
}

// ---------------------------------------------------------------- state
async function load() {
  STATE = await api("/api/state");
  if (!CURRENT.profile && STATE.profiles.length) CURRENT.profile = STATE.profiles[0].name;
  if (!CURRENT.source && STATE.sources.length) CURRENT.source = STATE.sources[0].id;
  renderDeps();
  renderProfiles();
  renderSelects();
  renderLines();
  renderGrid();
  $("#heroProfiles").textContent = fmtNum(STATE.profiles.length);
  $("#heroClips").textContent = fmtNum(STATE.sources.reduce((n, s) => n + s.clips, 0));
  $("#heroRenders").textContent = fmtNum(STATE.renders.length);
  if (STATE.job) setRunning(STATE.job);
}

function renderDeps() {
  const ok = STATE.deps && STATE.deps.ffmpeg;
  $("#ffDot").className = "dot" + (ok ? "" : " off");
  $("#ffText").textContent = ok ? "ffmpeg ready" : "ffmpeg missing";
}

function renderProfiles() {
  const box = $("#profiles");
  $("#profCount").textContent = STATE.profiles.length;
  if (!STATE.profiles.length) {
    box.innerHTML = `<div class="empty"><b>No profiles yet.</b> Study a folder of reels above — the measured numbers show up here.</div>`;
    return;
  }
  box.innerHTML = STATE.profiles.map((p) => {
    const d = p.format && p.format.duration || {};
    const g = p.grade || {};
    const on = p.name === CURRENT.profile;
    return `<div class="acc-card ${on ? "active" : ""}" data-profile="${esc(p.name)}">
      <div class="ava">${esc((p.name || "?")[0])}</div>
      <div>
        <div class="acc-name">${esc(p.name)}</div>
        <div class="acc-sub">${fmtNum((p.reference || {}).reels || 0)} reels studied</div>
        <div class="prof-metrics">
          <span>${p.format.w}×${p.format.h}</span>
          <span class="hot">${(d.median || 0).toFixed(1)}s</span>
          <span>${p.format.single_shot ? "single shot" : "montage"}</span>
          <span>luma ${(g.yavg || 0).toFixed(0)}</span>
          <span>sat ${(g.satavg || 0).toFixed(1)}</span>
        </div>
      </div>
    </div>`;
  }).join("");
  $$("[data-profile]").forEach((el) => {
    el.onclick = () => { CURRENT.profile = el.dataset.profile; renderProfiles(); renderSelects(); };
  });
}

function renderSelects() {
  const ps = $("#profileSel");
  ps.innerHTML = STATE.profiles.map((p) =>
    `<option value="${esc(p.name)}" ${p.name === CURRENT.profile ? "selected" : ""}>${esc(p.name)}</option>`).join("");
  const ss = $("#sourceSel");
  ss.innerHTML = STATE.sources.map((s) =>
    `<option value="${s.id}" ${s.id === CURRENT.source ? "selected" : ""}>${esc(s.name)} (${s.clips})</option>`).join("");
  const ts = $("#topicSel");
  const topics = ["", ...(STATE.topics || [])];
  ts.innerHTML = topics.map((t) =>
    `<option value="${esc(t)}" ${t === CURRENT.topic ? "selected" : ""}>${t === "" ? "any topic" : esc(t)}</option>`).join("");

  ps.onchange = () => { CURRENT.profile = ps.value; renderProfiles(); };
  ss.onchange = () => { CURRENT.source = Number(ss.value); renderSelects(); };
  ts.onchange = () => { CURRENT.topic = ts.value; };

  const p = STATE.profiles.find((x) => x.name === CURRENT.profile);
  $("#badgeProfile").textContent = p ? `${p.name} · ${p.format.w}×${p.format.h}` : "no profile";
  const s = STATE.sources.find((x) => x.id === CURRENT.source);
  $("#badgeSource").textContent = s ? `${s.name} · ${s.clips} clips` : "no clips";
  $("#badgeClips").textContent = `${(s ? s.clips : 0)} clips`;
  $("#badgeSize").textContent = fmtBytes(s ? s.bytes : 0);
  $("#buildBtn").disabled = !p || !s;
}

// ---------------------------------------------------------------- lines
function renderLines() {
  const box = $("#lineList");
  if (!LINES.length) {
    box.innerHTML = `<div class="line-empty">No lines yet — <b>Generate lines</b> writes one per reel and you can edit every one before rendering.</div>`;
    return;
  }
  box.innerHTML = LINES.map((l, i) => `
    <div class="line-item" data-i="${i}">
      <div class="num">${i + 1}</div>
      <textarea spellcheck="false">${esc((l.lines || []).join("\n"))}</textarea>
      <div style="display:grid;gap:4px;justify-items:end">
        <button class="kill" data-kill="${i}" title="Remove">✕</button>
        <span class="theme">${esc(l.theme || "")}</span>
      </div>
    </div>`).join("");
  $$("#lineList textarea").forEach((ta) => {
    ta.oninput = () => {
      const i = Number(ta.closest(".line-item").dataset.i);
      LINES[i].lines = ta.value.split("\n").map((s) => s.trim()).filter(Boolean);
      LINES[i].text = LINES[i].lines.join(" ");
    };
  });
  $$("[data-kill]").forEach((b) => {
    b.onclick = () => { LINES.splice(Number(b.dataset.kill), 1); renderLines(); };
  });
}

async function generateLines() {
  const count = Math.max(1, Math.min(20, Number($("#countInput").value) || 1));
  const btn = $("#genBtn");
  btn.disabled = true; btn.textContent = "…";
  try {
    const r = await api("/api/lines", {
      method: "POST",
      body: JSON.stringify({ count, topic: CURRENT.topic || "" }),
    });
    LINES = r.lines;
    renderLines();
    toast(`${r.lines.length} line(s) ready — edit, then render`);
  } catch (e) { toast(e.message, true); }
  btn.disabled = false; btn.textContent = "Generate lines";
}

// ---------------------------------------------------------------- study / sources
async function study() {
  const folder = $("#refFolder").value.trim();
  if (!folder) return toast("Paste a folder of reference reels", true);
  const btn = $("#studyBtn");
  btn.disabled = true; btn.textContent = "Studying…";
  try {
    const r = await api("/api/study", {
      method: "POST",
      body: JSON.stringify({ folder, sample: Number($("#refSample").value) || 14 }),
    });
    CURRENT.profile = r.profile;
    await load();
    toast(`Studied ${fmtNum(r.format ? (r.format.duration.median || 0) : 0)}s median · ${r.profile} ✓`);
  } catch (e) { toast(e.message, true); }
  btn.disabled = false; btn.textContent = "Study →";
}

async function addSource() {
  const path = $("#srcPath").value.trim();
  if (!path) return toast("Paste a folder of your own clips", true);
  try {
    const r = await api("/api/sources", { method: "POST", body: JSON.stringify({ path }) });
    CURRENT.source = r.source.id;
    await load();
    toast(`Added ${r.source.name} (${r.source.clips} clips)`);
  } catch (e) { toast(e.message, true); }
}

// ---------------------------------------------------------------- build
async function build() {
  if (!CURRENT.profile) return toast("Study or pick a profile first", true);
  if (!CURRENT.source) return toast("Add a clip folder first", true);
  const count = Math.max(1, Math.min(20, Number($("#countInput").value) || 1));
  if (LINES.length < count) await generateLines();
  const payload = {
    profile: CURRENT.profile,
    source_id: CURRENT.source,
    count,
    topic: CURRENT.topic,
    lines: LINES.slice(0, count).map((l) => ({ text: l.text, lines: l.lines, theme: l.theme })),
  };
  try {
    const r = await api("/api/build", { method: "POST", body: JSON.stringify(payload) });
    $("#progress").classList.add("on");
    subscribe(r.job.id);
    setRunning(r.job);
    toast(`Rendering ${r.queued} reel(s)…`);
  } catch (e) { toast(e.message, true); }
}

// ---------------------------------------------------------------- progress
function setRunning(job) {
  const running = !!(job && job.status === "running");
  $("#progress").classList.toggle("on", !!job);
  $("#cancelBtn").style.display = running ? "" : "none";
  $("#buildBtn").disabled = running || !CURRENT.profile || !CURRENT.source;
  if (!job) return;
  $("#pCount").textContent = fmtNum(job.downloaded || 0);
  $("#pTotal").textContent = job.expected ? `of ${fmtNum(job.expected)}` : "";
  $("#pStatus").textContent = job.status;
  $("#pTitle").textContent = running
    ? `Rendering @${job.account}…`
    : `${job.status === "done" ? "Finished" : job.status === "cancelled" ? "Stopped" : "Ended"} — ${fmtNum(job.downloaded || 0)} reel(s)`;
  const hasTotal = !!job.expected;
  $("#pbar").classList.toggle("indet", !hasTotal && running);
  $("#barFill").style.width = hasTotal
    ? Math.max(3, Math.min(100, ((job.downloaded || 0) / job.expected) * 100)) + "%"
    : "35%";
}

function subscribe(jid) {
  if (ES) ES.close();
  const log = $("#log");
  log.innerHTML = "";
  ES = new EventSource(`/api/jobs/${jid}/events`);
  ES.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "log") {
      const d = document.createElement("div");
      d.className = msg.entry.kind || "";
      d.textContent = msg.entry.text;
      log.appendChild(d);
      log.scrollTop = log.scrollHeight;
      if (log.children.length > 400) log.removeChild(log.firstChild);
    } else if (msg.type === "state") {
      setRunning(msg.job);
    } else if (msg.type === "end") {
      ES.close(); ES = null;
      const j = msg.job;
      toast(j.status === "done" ? `Finished — ${j.downloaded} reel(s) ✓` : `Ended: ${j.status}`, j.status !== "done");
      setTimeout(load, 600);
    }
  };
  ES.onerror = () => { if (ES) { ES.close(); ES = null; } };
}

// ---------------------------------------------------------------- grid
function renderGrid() {
  const grid = $("#grid");
  const items = STATE.renders || [];
  $("#renderCount").textContent = items.length;
  $("#emptyState").hidden = items.length > 0;
  grid.innerHTML = items.map((r) => {
    const m = (r.match && r.match.delta) || {};
    const luma = m.yavg == null ? "" : `Δluma ${m.yavg > 0 ? "+" : ""}${m.yavg}`;
    return `<div class="forge-card" data-file="${esc(r.file)}">
      <div class="poster" data-play="${esc(r.file)}">
        <img src="/thumb/${encodeURIComponent(r.file)}" alt="" loading="lazy">
        <div class="play">▶</div>
      </div>
      <div class="body">
        <div class="line">${esc((r.line || "").slice(0, 160))}</div>
        <div class="meta">
          <span>${(r.duration || 0).toFixed(1)}s</span>
          <span>·</span>
          <span>${esc(r.clip || "")}</span>
          ${luma ? `<span class="match">${luma}</span>` : ""}
        </div>
        <div class="actions">
          <button data-open="${esc(r.file)}">Open</button>
          <button data-del="${esc(r.file)}">Delete</button>
        </div>
      </div>
    </div>`;
  }).join("");

  $$("[data-play]").forEach((p) => {
    p.onclick = () => {
      const file = p.dataset.play;
      p.innerHTML = `<video src="/media/${encodeURIComponent(file)}" autoplay loop playsinline></video>`;
    };
  });
  $$("[data-open]").forEach((b) => {
    b.onclick = () => window.open(`/media/${encodeURIComponent(b.dataset.open)}`, "_blank");
  });
  $$("[data-del]").forEach((b) => {
    b.onclick = async () => {
      if (!confirm("Delete this render from disk?")) return;
      try {
        await api("/api/render/delete", { method: "POST", body: JSON.stringify({ file: b.dataset.del }) });
        await load();
      } catch (e) { toast(e.message, true); }
    };
  });
}

// ---------------------------------------------------------------- boot
function applyTheme(t) {
  document.documentElement.setAttribute("data-theme", t);
  try { localStorage.setItem("reelforge.theme", t); } catch (e) {}
  $("#themeIcon").textContent = t === "dark" ? "☀" : "☾";
  $("#themeLabel").textContent = t === "dark" ? "Light" : "Dark";
}
$("#themeBtn").onclick = () => {
  const cur = document.documentElement.getAttribute("data-theme");
  applyTheme(cur === "dark" ? "light" : "dark");
};
$("#studyBtn").onclick = study;
$("#addSourceBtn").onclick = addSource;
$("#genBtn").onclick = generateLines;
$("#buildBtn").onclick = build;
$("#revealBtn").onclick = () => api("/api/reveal", { method: "POST", body: JSON.stringify({ what: "renders" }) }).catch(() => {});
$("#logToggle").onclick = () => {
  const l = $("#log");
  l.classList.toggle("on");
  $("#logToggle").textContent = l.classList.contains("on") ? "Hide log" : "Show log";
};
$("#cancelBtn").onclick = async () => {
  const job = STATE.job;
  if (!job) return;
  try { await api(`/api/jobs/${job.id}/cancel`, { method: "POST", body: "{}" }); } catch (e) {}
};

applyTheme(document.documentElement.getAttribute("data-theme") || "light");
load().catch((e) => toast(e.message, true));
setInterval(() => { if (!ES) load().catch(() => {}); }, 15000);
