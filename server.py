#!/usr/bin/env python3
"""ReelForge — study a reel format, then forge new reels in it.

    python3 server.py            # http://127.0.0.1:8792
    python3 server.py --port 9000 --open

Binds to loopback only. Rendering is local ffmpeg; nothing is uploaded anywhere.
"""
import argparse
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from reelforge import lines as linebank
from reelforge import probe, render, style
from reelforge.jobs import JobManager
from reelforge.store import Store

HOME = os.path.expanduser("~")
APP_ROOT = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(APP_ROOT, "web")
ROOT = os.path.join(HOME, ".reelforge")
PROFILES = os.path.join(ROOT, "profiles")
RENDERS = os.path.join(ROOT, "renders")
THUMBS = os.path.join(ROOT, "thumbs")
DB = os.path.join(ROOT, "reelforge.db")

for d in (ROOT, PROFILES, RENDERS, THUMBS):
    os.makedirs(d, exist_ok=True)

STORE = Store(DB)
JOBS = JobManager()
NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,48}$")
MEDIA_RE = re.compile(r"\.(mp4|mov|m4v|webm)$", re.I)


def _reap_orphaned_jobs():
    try:
        for j in STORE.running_jobs():
            STORE.update_job(j["id"], status="interrupted", finished_at=time.time())
    except Exception:
        pass


_reap_orphaned_jobs()


# --------------------------------------------------------------------------- helpers
def profile_path(name):
    if not NAME_RE.match(name or ""):
        raise ValueError("bad profile name")
    return os.path.join(PROFILES, name + ".json")


def profiles():
    out = []
    for name in sorted(os.listdir(PROFILES)):
        if not name.endswith(".json"):
            continue
        try:
            p = style.load(os.path.join(PROFILES, name))
        except Exception:
            continue
        out.append({
            "name": p.get("name") or name[:-5],
            "file": name,
            "format": p.get("format", {}),
            "grade": (p.get("grade") or {}).get("measured", {}),
            "reference": p.get("reference", {}),
            "created": p.get("created", 0),
        })
    return out


def clips_in(folder, limit=400):
    out = []
    if not os.path.isdir(folder):
        return out
    for name in sorted(os.listdir(folder)):
        if not MEDIA_RE.search(name) or name.startswith("."):
            continue
        path = os.path.join(folder, name)
        out.append({"file": name, "path": path, "bytes": os.path.getsize(path)})
        if len(out) >= limit:
            break
    return out


def render_items():
    items = []
    for r in STORE.renders(400):
        path = os.path.join(RENDERS, r["file"])
        if not os.path.isfile(path):
            continue
        row = dict(r)
        row["bytes"] = os.path.getsize(path)
        try:
            row["match"] = json.loads(r.get("match_json") or "{}")
        except Exception:
            row["match"] = {}
        row.pop("match_json", None)
        items.append(row)
    return items


def thumb_for(name):
    base = os.path.splitext(os.path.basename(name))[0]
    tp = os.path.join(THUMBS, base + ".jpg")
    if os.path.exists(tp):
        return tp
    src = os.path.join(RENDERS, os.path.basename(name))
    if not os.path.isfile(src):
        return None
    return probe.frame(src, tp, at=1.0, width=270)


def subprocess_env():
    env = dict(os.environ)
    if probe.FF:
        env["PATH"] = os.path.dirname(probe.FF) + os.pathsep + env.get("PATH", "")
    env["PYTHONPATH"] = APP_ROOT
    return env


# --------------------------------------------------------------------------- handler
class Handler(BaseHTTPRequestHandler):
    server_version = "ReelForge"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    # ---- plumbing ----
    def json(self, obj, code=200):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _send(self, code, body, ctype="text/plain; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return {}

    def file(self, path, ctype=None, extra=None):
        if not os.path.isfile(path):
            return self._send(404, "not found")
        extra = extra or {}
        if not ctype:
            ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        size = os.path.getsize(path)
        rng = self.headers.get("Range")
        if rng and ctype.startswith("video") or (rng and ctype.startswith("audio")):
            m = re.match(r"bytes=(\d*)-(\d*)", rng)
            if m:
                start = int(m.group(1) or 0)
                end = int(m.group(2) or (size - 1))
                end = min(end, size - 1)
                if start > end:
                    start = 0
                length = end - start + 1
                self.send_response(206)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(length))
                for k, v in extra.items():
                    self.send_header(k, v)
                self.end_headers()
                with open(path, "rb") as f:
                    f.seek(start)
                    remaining = length
                    while remaining > 0:
                        chunk = f.read(min(65536, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
                return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(size))
        for k, v in extra.items():
            self.send_header(k, v)
        self.end_headers()
        with open(path, "rb") as f:
            shutil.copyfileobj(f, self.wfile)

    # ---- routing ----
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        p = urllib.parse.unquote(u.path)
        q = urllib.parse.parse_qs(u.query)

        if p in ("/", "/index.html"):
            return self.file(os.path.join(WEB_DIR, "index.html"), "text/html; charset=utf-8")
        if p.startswith("/static/"):
            return self.file(os.path.join(WEB_DIR, os.path.basename(p)))
        if p == "/api/state":
            return self.api_state()
        m = re.match(r"^/api/profiles/([^/]+)$", p)
        if m:
            return self.api_profile(m.group(1))
        m = re.match(r"^/api/jobs/(\d+)/events$", p)
        if m:
            return self.sse(int(m.group(1)))
        m = re.match(r"^/media/(.+)$", p)
        if m:
            return self.media(m.group(1))
        m = re.match(r"^/thumb/(.+)$", p)
        if m:
            return self.thumb(m.group(1))
        return self._send(404, "not found")

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        p = urllib.parse.unquote(u.path)
        if p == "/api/study":
            return self.api_study()
        if p == "/api/lines":
            return self.api_lines()
        if p == "/api/sources":
            return self.api_add_source()
        if p == "/api/build":
            return self.api_build()
        if p == "/api/clips":
            return self.api_clips()
        if p == "/api/render/delete":
            return self.api_delete_render()
        if p == "/api/reveal":
            return self.api_reveal()
        m = re.match(r"^/api/jobs/(\d+)/cancel$", p)
        if m:
            return self.api_cancel(int(m.group(1)))
        m = re.match(r"^/api/profiles/([^/]+)/delete$", p)
        if m:
            return self.api_delete_profile(m.group(1))
        return self._send(404, "not found")

    # ---- api ----
    def api_state(self):
        srcs = []
        for s in STORE.sources():
            cl = clips_in(s["path"])
            srcs.append({"id": s["id"], "name": s["name"], "path": s["path"],
                         "clips": len(cl), "bytes": sum(c["bytes"] for c in cl)})
        profs = profiles()
        items = render_items()
        return self.json({
            "profiles": profs,
            "sources": srcs,
            "renders": items,
            "deps": {"ffmpeg": bool(probe.FF), "version": (probe.FF or "")},
            "job": STORE.active_job(0),
            "topics": list(linebank.THEMES.keys()),
            "defaults": {"y": style.DEFAULT_TEXT["y"],
                         "font_size": style.DEFAULT_TEXT["font_size"],
                         "max_chars": style.DEFAULT_TEXT["max_chars"]},
        })

    def api_profile(self, name):
        try:
            path = profile_path(name)
        except ValueError:
            return self.json({"ok": False, "error": "bad name"}, 400)
        if not os.path.isfile(path):
            return self.json({"ok": False, "error": "no such profile"}, 404)
        return self.json(style.load(path))

    def api_study(self):
        body = self.body()
        folder = os.path.expanduser((body.get("folder") or "").strip())
        if not os.path.isdir(folder):
            return self.json({"ok": False, "error": "folder not found: %s" % folder}, 400)
        name = (body.get("name") or os.path.basename(os.path.normpath(folder))).strip()
        name = re.sub(r"[^A-Za-z0-9._-]+", "-", name)[:48] or "profile"
        sample = int(body.get("sample") or 14)
        try:
            prof = style.study(folder, sample=sample)
        except Exception as e:
            return self.json({"ok": False, "error": str(e)}, 400)
        prof["name"] = name
        style.save(prof, profile_path(name))
        return self.json({"ok": True, "profile": name, "format": prof["format"],
                          "grade": prof["grade"].get("measured", {})})

    def api_lines(self):
        body = self.body()
        n = max(1, min(40, int(body.get("count") or 8)))
        topic = (body.get("topic") or "").strip() or None
        seed = body.get("seed")
        seed = int(seed) if seed not in (None, "") else None
        width = int(body.get("width") or 22)
        items = linebank.generate(topic=topic, n=n, seed=seed, width=width)
        return self.json({"ok": True, "lines": items})

    def api_add_source(self):
        body = self.body()
        path = os.path.expanduser((body.get("path") or "").strip())
        if not os.path.isdir(path):
            return self.json({"ok": False, "error": "folder not found: %s" % path}, 400)
        name = (body.get("name") or os.path.basename(os.path.normpath(path))).strip()
        name = re.sub(r"[^A-Za-z0-9._-]+", "-", name)[:48] or "clips"
        if not clips_in(path, 1):
            return self.json({"ok": False, "error": "no .mp4/.mov clips in that folder"}, 400)
        src = STORE.add_source(name, path)
        return self.json({"ok": True, "source": {"id": src["id"], "name": src["name"],
                                                 "path": src["path"],
                                                 "clips": len(clips_in(path))}})

    def api_clips(self):
        body = self.body()
        src = STORE.source_by_id(int(body.get("source_id") or 0))
        if not src:
            return self.json({"ok": False, "error": "no such source"}, 404)
        out = []
        for c in clips_in(src["path"])[:120]:
            info = probe.probe(c["path"])
            out.append({"file": c["file"], "bytes": c["bytes"],
                        "dur": info.get("dur"), "w": info.get("w"), "h": info.get("h")})
        return self.json({"ok": True, "clips": out, "folder": src["path"]})

    def api_build(self):
        body = self.body()
        src = STORE.source_by_id(int(body.get("source_id") or 0))
        if not src:
            return self.json({"ok": False, "error": "pick a clip source first"}, 400)
        name = (body.get("profile") or "").strip()
        try:
            ppath = profile_path(name)
        except ValueError:
            return self.json({"ok": False, "error": "bad profile name"}, 400)
        if not os.path.isfile(ppath):
            return self.json({"ok": False, "error": "no such profile"}, 400)
        live = STORE.active_job(0)
        if live:
            j = JOBS.get(live["id"])
            if j and j.status == "running":
                return self.json({"ok": False, "error": "a render is already running"}, 409)
            STORE.update_job(live["id"], status="interrupted", finished_at=time.time())

        count = max(1, min(20, int(body.get("count") or 1)))
        clips = body.get("clips") or []
        items = body.get("lines") or []
        items = [it for it in items if isinstance(it, dict) and it.get("lines")]
        if not items:
            items = linebank.generate(topic=(body.get("topic") or None), n=count,
                                      seed=body.get("seed"))
        result_path = os.path.join(ROOT, "build-%d.json" % int(time.time()))
        cmd = [sys.executable, "-m", "reelforge.render_cli",
               "--profile", ppath, "--source-dir", src["path"], "--out-dir", RENDERS,
               "--count", str(count), "--lines", json.dumps(items),
               "--clips", json.dumps(clips), "--result", result_path]
        if body.get("seed") not in (None, ""):
            cmd += ["--seed", str(body.get("seed"))]
        audio = os.path.expanduser((body.get("audio") or "").strip())
        if audio and os.path.isfile(audio):
            cmd += ["--audio", audio]

        jid = STORE.new_job(src["id"], count, count, "render")
        job = JOBS.create(jid=jid, account=src["name"], dest=RENDERS, cmd=cmd,
                          expected=count, limit_n=count, quality="render",
                          subprocess_env=subprocess_env(), mode="render", cwd=APP_ROOT)
        job.start()

        def finalize():
            job._thread.join()
            try:
                STORE.update_job(jid, status=job.status,
                                 finished_at=job.finished or time.time(),
                                 downloaded=job.downloaded)
            except Exception:
                pass
            try:
                with open(result_path) as f:
                    results = json.load(f)
            except Exception:
                results = []
            for r in results:
                if r.get("error"):
                    continue
                STORE.add_render(r["file"], clip=r.get("clip", ""), line=r.get("line", ""),
                                 theme=r.get("theme", ""), profile=r.get("profile", ""),
                                 duration=r.get("duration", 0),
                                 match_json=json.dumps(r.get("match") or {}))
            try:
                os.remove(result_path)
            except OSError:
                pass
        threading.Thread(target=finalize, daemon=True).start()
        return self.json({"ok": True, "job": job.as_dict(), "queued": count})

    def api_delete_render(self):
        body = self.body()
        name = os.path.basename(body.get("file") or "")
        if not name:
            return self.json({"ok": False, "error": "file required"}, 400)
        for d in (RENDERS, THUMBS):
            p = os.path.join(d, name if d == RENDERS else os.path.splitext(name)[0] + ".jpg")
            try:
                os.remove(p)
            except OSError:
                pass
        STORE.remove_render(name)
        return self.json({"ok": True})

    def api_delete_profile(self, name):
        try:
            p = profile_path(name)
        except ValueError:
            return self.json({"ok": False, "error": "bad name"}, 400)
        try:
            os.remove(p)
        except OSError:
            pass
        return self.json({"ok": True})

    def api_reveal(self):
        body = self.body()
        what = body.get("what") or "renders"
        if what == "renders":
            d = RENDERS
        else:
            src = STORE.source_by_id(int(body.get("source_id") or 0))
            if not src:
                return self.json({"ok": False, "error": "no such source"}, 404)
            d = src["path"]
        if sys.platform == "darwin":
            subprocess.Popen(["open", d])
        elif sys.platform.startswith("linux"):
            subprocess.Popen(["xdg-open", d])
        return self.json({"ok": True, "path": d})

    def api_cancel(self, jid):
        job = JOBS.get(jid)
        if not job:
            db = STORE.job(jid)
            if db and db["status"] == "running":
                STORE.update_job(jid, status="cancelled", finished_at=time.time())
                return self.json({"ok": True, "note": "orphan cancelled"})
            return self.json({"ok": False, "error": "no such job"}, 404)
        job.cancel()
        STORE.update_job(jid, status="cancelled", finished_at=time.time())
        return self.json({"ok": True})

    # ---- media ----
    def media(self, filename):
        base = os.path.abspath(RENDERS)
        path = os.path.abspath(os.path.join(base, os.path.basename(filename)))
        if not path.startswith(base + os.sep) or not os.path.isfile(path):
            return self._send(404, "not found")
        return self.file(path)

    def thumb(self, filename):
        tp = thumb_for(filename)
        if not tp:
            return self._send(404, "no thumb")
        return self.file(tp, "image/jpeg", {"Cache-Control": "max-age=600"})

    # ---- sse ----
    def sse(self, jid):
        job = JOBS.get(jid)
        if not job:
            return self.json({"ok": False, "error": "no such job"}, 404)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        q = job.subscribe()
        try:
            self.wfile.write(("data: %s\n\n" % json.dumps(
                {"type": "state", "job": job.as_dict()})).encode())
            self.wfile.flush()
            last = 0.0
            while True:
                try:
                    entry = q.get(timeout=1.0)
                    self.wfile.write(("data: %s\n\n" % json.dumps(
                        {"type": "log", "entry": entry})).encode())
                    self.wfile.flush()
                except Exception:
                    pass
                if time.time() - last > 1.0:
                    last = time.time()
                    self.wfile.write(("data: %s\n\n" % json.dumps(
                        {"type": "state", "job": job.as_dict()})).encode())
                    self.wfile.flush()
                if job.status != "running" and q.empty():
                    self.wfile.write(("data: %s\n\n" % json.dumps(
                        {"type": "end", "job": job.as_dict()})).encode())
                    self.wfile.flush()
                    break
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            job.unsubscribe(q)


def main():
    ap = argparse.ArgumentParser(description="ReelForge — reels in a studied format")
    ap.add_argument("--port", type=int, default=int(os.environ.get("REELFORGE_PORT", 8792)))
    ap.add_argument("--open", action="store_true")
    args = ap.parse_args()

    if not probe.FF:
        print("! ffmpeg not found on PATH — install it (brew install ffmpeg)")
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    srv.daemon_threads = True
    url = "http://127.0.0.1:%d" % args.port
    print("ReelForge running at %s  (data: %s)" % (url, ROOT))
    if args.open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
