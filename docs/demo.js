// ReelForge static demo: the real UI, real studied numbers, real renders —
// with the API stubbed out. Rendering is local-only, so the build call explains
// itself instead of pretending.
window.REELFORGE_DEMO = true;

const DEMO_DATA = { state: null };

const fmt = (n) => (n || 0).toLocaleString();

async function bootDemo() {
  const r = await fetch("data/demo.json", { cache: "no-store" });
  const d = await r.json();
  const p = d.profile;
  DEMO_DATA.state = {
    profiles: [{
      name: p.name,
      file: p.name + ".json",
      format: p.format,
      grade: p.grade,
      reference: p.reference,
      created: 0,
    }],
    sources: [{
      id: 1,
      name: "demo clips (synthetic)",
      path: "—",
      clips: d.clipNames.length,
      bytes: d.renders.reduce((n, r) => n + (r.bytes || 0), 0),
    }],
    renders: d.renders.map((r) => Object.assign({ created_at: 0 }, r)),
    deps: { ffmpeg: true, version: "demo" },
    job: null,
    topics: ["longing", "late-night", "self-worth", "crush", "heartbreak", "nostalgia"],
    defaults: { y: 0.22, font_size: 52, max_chars: 26 },
    lines: d.lines,
  };
  return DEMO_DATA.state;
}

function pickLines(n) {
  const pool = DEMO_DATA.state.lines.slice();
  for (let i = pool.length - 1; i > 0; i--) {           // fisher-yates
    const j = Math.floor(Math.random() * (i + 1));
    [pool[i], pool[j]] = [pool[j], pool[i]];
  }
  return pool.slice(0, n);
}

window.demoApi = async function (path, opts) {
  const body = opts && opts.body ? JSON.parse(opts.body) : {};
  if (path === "/api/state") {
    if (!DEMO_DATA.state) await bootDemo();
    const s = DEMO_DATA.state;
    return Object.assign({}, s, { renders: s.renders.map((r) => Object.assign({}, r)) });
  }
  if (path === "/api/lines") {
    if (!DEMO_DATA.state) await bootDemo();
    const n = Math.max(1, Math.min(20, body.count || 1));
    return { ok: true, lines: pickLines(Math.max(n, 3)) };
  }
  if (path === "/api/build") {
    throw new Error(
      "This is the static demo — the real app renders with ffmpeg on your machine. " +
      "Clone github.com/KartBala07/ReelForge and run: python3 server.py");
  }
  if (path === "/api/study") {
    throw new Error("Studying runs locally: clone the repo, then point Study at a folder of reels.");
  }
  if (path === "/api/sources") {
    throw new Error("Adding footage runs locally — the demo has no filesystem access.");
  }
  if (path === "/api/reveal") return { ok: true, path: "—" };
  if (path === "/api/render/delete") throw new Error("Read-only demo.");
  if (path.indexOf("/api/jobs/") === 0) return { ok: true };
  throw new Error("Not available in the static demo.");
};
