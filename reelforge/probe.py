"""ffmpeg probing without ffprobe.

Several ffmpeg builds ship without `ffprobe` (the one on this machine does), so
everything here reads `ffmpeg -i` stderr instead. Cheap enough to call per clip
and per render check.
"""
import json
import os
import re
import shutil
import subprocess


def ffmpeg_bin():
    p = shutil.which("ffmpeg")
    if p:
        return p
    for base in (os.path.expanduser("~/.hermes/tools"), "/usr/local/bin", "/opt/homebrew/bin"):
        if not os.path.isdir(base):
            continue
        for name in sorted(os.listdir(base)):
            cand = os.path.join(base, name, "ffmpeg")
            if os.path.isfile(cand) and os.access(cand, os.X_OK):
                return cand
    return None


FF = ffmpeg_bin()

DUR_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)")
VID_RE = re.compile(r"Video:\s*(\w+).*?(\d{2,5})x(\d{2,5})(?:.*?,\s*([\d.]+)\s*fps)?")
AUD_RE = re.compile(r"Audio:\s*(\w+).*?(\d+) Hz, (\w+)")


def probe(path):
    """{'dur','w','h','fps','vcodec','acodec','ar','ch','bytes'} or {} if unreadable."""
    if not FF or not os.path.isfile(path):
        return {}
    try:
        p = subprocess.run([FF, "-hide_banner", "-i", path],
                           capture_output=True, text=True, timeout=60)
    except Exception:
        return {}
    err = p.stderr or ""
    out = {"file": os.path.basename(path), "bytes": os.path.getsize(path)}
    m = DUR_RE.search(err)
    if m:
        out["dur"] = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    v = VID_RE.search(err)
    if v:
        out["vcodec"], out["w"], out["h"] = v.group(1), int(v.group(2)), int(v.group(3))
        out["fps"] = float(v.group(4)) if v.group(4) else 30.0
    a = AUD_RE.search(err)
    if a:
        out["acodec"], out["ar"], out["ch"] = a.group(1), int(a.group(2)), a.group(3)
    return out if out.get("dur") else {}


def scene_cuts(path, threshold=0.10, timeout=180):
    """Timestamps of hard cuts. Reference reels score 0 here — one continuous shot."""
    if not FF:
        return []
    try:
        p = subprocess.run(
            [FF, "-hide_banner", "-i", path, "-filter:v",
             "select='gt(scene,%s)',metadata=print" % threshold, "-an", "-f", "null", "-"],
            capture_output=True, text=True, timeout=timeout)
    except Exception:
        return []
    return [float(x) for x in re.findall(r"pts_time:([\d.]+)", p.stderr)]


def color_stats(path, at=1.0, timeout=120):
    """Brightness / saturation / hue of one frame, via lavfi signalstats."""
    if not FF:
        return {}
    try:
        p = subprocess.run(
            [FF, "-hide_banner", "-ss", str(at), "-i", path, "-frames:v", "1", "-vf",
             "scale=64:114,signalstats,metadata=print", "-f", "null", "-"],
            capture_output=True, text=True, timeout=timeout)
    except Exception:
        return {}
    vals = {}
    for k in ("YAVG", "YMIN", "YMAX", "SATAVG", "SATMAX", "HUEAVG"):
        m = re.search(r"lavfi\.signalstats\.%s=([\d.]+)" % k, p.stderr)
        if m:
            vals[k] = float(m.group(1))
    return vals


def mean_stats(path, duration=None, points=(0.2, 0.5, 0.8)):
    """Average colour stats over several frames.

    One frame is not a clip: a daytime shot can be black in one corner and blown
    out 200 px away, and a calibration done on a single frame misses the target
    by 50 luma.
    """
    dur = duration or (probe(path) or {}).get("dur") or 0
    ats = [max(0.0, dur * p) for p in points] if dur else [0.4, 0.8, 1.2]
    got = [color_stats(path, at=a) for a in ats]
    got = [g for g in got if g]
    if not got:
        return {}
    out = {}
    for k in ("YAVG", "SATAVG", "HUEAVG", "YMIN", "YMAX"):
        vals = [g[k] for g in got if k in g]
        if vals:
            out[k] = round(sum(vals) / float(len(vals)), 2)
    return out


def frame(path, out_jpg, at=1.0, width=540):
    if not FF:
        return None
    tmp = out_jpg + ".tmp.jpg"
    try:
        subprocess.run([FF, "-y", "-hide_banner", "-loglevel", "error", "-ss", str(at),
                        "-i", path, "-frames:v", "1", "-vf", "scale=%d:-2" % width,
                        "-q:v", "4", tmp], capture_output=True, timeout=90)
        if os.path.exists(tmp) and os.path.getsize(tmp):
            os.replace(tmp, out_jpg)
            return out_jpg
    except Exception:
        pass
    return None


def contact_sheet(files, out_jpg, per=5, width=240):
    """One strip of frames per file — the fastest way to eyeball what a clip is."""
    if not FF or not files:
        return None
    parts = []
    for i, f in enumerate(files):
        p = subprocess.run(
            [FF, "-hide_banner", "-loglevel", "error", "-i", f, "-vf",
             "fps=1/2.2,scale=%d:-2,tile=%dx1" % (width, per), "-frames:v", "1",
             os.path.join(os.path.dirname(out_jpg), "sheet-%d.jpg" % i), "-y"],
            capture_output=True, timeout=180)
        if p.returncode == 0:
            parts.append(os.path.join(os.path.dirname(out_jpg), "sheet-%d.jpg" % i))
    return parts


def json_dump(obj, path):
    with open(path, "w") as f:
        json.dump(obj, f, indent=1, sort_keys=True)
    return path
