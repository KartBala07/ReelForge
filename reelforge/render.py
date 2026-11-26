"""The renderer: one clip + one line → a reel in the studied format.

Filter order matters and is deliberate:

    trim → scale/crop to 9:16 → grade (curves + eq + colour balance)
    → slow drift (zoompan) → grain → vignette → text → yuv420p

The drift exists because the reference format is a single continuous shot: a
perfectly static phone frame reads as a still image, while a 4–6% push reads as
"handheld and slightly alive" without becoming a montage.

`check_match()` measures the finished file against the profile's own targets, so
"looks like it" is a number, not an opinion.
"""
import json
import os
import re
import subprocess
import tempfile

from . import probe, style


def _grade_chain(g, w, h):
    sat = g.get("saturation", 0.45)
    contrast = g.get("contrast", 1.12)
    bright = g.get("brightness", -0.02)
    teal = g.get("teal_shadows", 0.05)
    warm = g.get("warm_highlights", 0.02)
    grain = int(g.get("grain", 6))
    vig = float(g.get("vignette", 0.55))
    return (
        "scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
        "curves=all='0/0 0.22/0.10 0.55/0.62 1/0.93',"
        "eq=contrast={c}:brightness={b}:saturation={s},"
        "colorbalance=rs=-0.012:gs={teal}:bs=0.0:rh={warm}:bh=-0.006,"
        "zoompan=z='min(1.0+0.00030*on,1.06)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        ":d=1:s={w}x{h}:fps=30,"
        "noise=alls={grain}:allf=t+u,"
        "vignette=angle={vig:.3f},deband=1thr=0.004:2thr=0.004:3thr=0.004"
    ).format(w=w, h=h, c=contrast, b=bright, s=sat, teal=teal, warm=warm,
             grain=grain, vig=vig)


def _drawtext(text, t, w, h, textfile):
    size = int(t.get("font_size", 48))
    # keep a 5-line block inside a sane share of the canvas
    size = max(28, min(size, int(w / 12)))
    y = int(h * float(t.get("y", 0.26)))
    fade = float(t.get("fade_in", 0.6))
    font = t.get("font") or style.DEFAULT_FONT
    parts = [
        "drawtext=fontfile='%s'" % font,
        "textfile='%s'" % textfile,
        "fontcolor=%s" % t.get("color", "0xF2E9B8"),
        "fontsize=%d" % size,
        "line_spacing=%d" % int(t.get("line_spacing", 16)),
        "x=(w-text_w)/2",
        "y=%d" % y,
        "text_align=center",
        "shadowcolor=0x000000@0",
    ]
    if fade > 0:
        parts.append("alpha='if(lt(t,%.2f),t/%.2f,1)'" % (fade, fade))
    return ":".join(parts)


def build_cmd(clip, out, duration, text, profile, audio=None, seek=None, textfile=None):
    fmt = profile["format"]
    w, h = int(fmt["w"]), int(fmt["h"])
    g = profile.get("grade", {})
    t = profile.get("text", {})
    chain = ("trim=start=%.3f:duration=%.3f,setpts=PTS-STARTPTS," % (seek or 0.0, duration)
             + _grade_chain(g, w, h) + "," + _drawtext(text, t, w, h, textfile)
             + ",format=yuv420p")
    cmd = [probe.FF, "-y", "-hide_banner", "-nostats", "-progress", "pipe:1",
           "-stream_loop", "-1", "-i", clip]
    if audio:
        cmd += ["-i", audio]
    cmd += ["-filter_complex", "[0:v]" + chain + "[v]", "-map", "[v]"]
    if audio:
        cmd += ["-map", "1:a:0", "-t", "%.3f" % duration]
        cmd += ["-af", "afade=t=in:st=0:d=0.25,afade=t=out:st=%.2f:d=0.45"
                % max(0.1, duration - 0.45)]
    else:
        cmd += ["-map", "0:a?", "-t", "%.3f" % duration]
        cmd += ["-af", "afade=t=in:st=0:d=0.25,afade=t=out:st=%.2f:d=0.45"
                % max(0.1, duration - 0.45)]
    cmd += ["-r", "30", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart", out]
    return cmd


def adapt_grade(profile, clip, seek=0.0, at=0.8):
    """Fit the profile's look to *this* clip.

    A fixed brightness/saturation cannot match a profile measured on dark night
    footage when the source clip is a bright daylight shot. So the profile's
    luma/saturation *targets* are kept and the eq is solved per clip:
    brightness shifts mean luma, saturation is the ratio of the two means.
    """
    g = dict(profile.get("grade", {}))
    tgt = (profile.get("grade") or {}).get("target") or {}
    src = probe.mean_stats(clip)
    if src and tgt:
        sy, ty = src.get("YAVG"), tgt.get("yavg")
        ss, ts = src.get("SATAVG"), tgt.get("satavg")
        if sy and ty:
            g["brightness"] = round(max(-0.45, min(0.30, (ty - sy) / 255.0)), 3)
        if ss and ts:
            g["saturation"] = round(max(0.05, min(1.2, ts / ss)), 3)
        g["source"] = {"yavg": round(sy or 0, 1), "satavg": round(ss or 0, 1)}
    return g


_CAL = {}


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _quick_render(clip, out, duration, profile, seek, g):
    """1-second, no-audio, ultrafast render used only to measure the grade."""
    p = dict(profile)
    p["grade"] = g
    chain = ("trim=start=%.3f:duration=%.3f,setpts=PTS-STARTPTS," % (seek, duration)
             + _grade_chain(g, int(profile["format"]["w"]), int(profile["format"]["h"]))
             + ",format=yuv420p")
    cmd = [probe.FF, "-y", "-hide_banner", "-loglevel", "error",
           "-stream_loop", "-1", "-i", clip, "-filter_complex", "[0:v]" + chain + "[v]",
           "-map", "[v]", "-an", "-t", "%.2f" % duration, "-r", "30",
           "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
           "-pix_fmt", "yuv420p", out]
    subprocess.run(cmd, capture_output=True, timeout=180)


def calibrate(profile, clip, seek=0.0, iters=2, log=None):
    """Solve brightness/saturation so the render lands on the profile's targets.

    `adapt_grade` gets close, but the tone curve sits between the eq and the
    output, so the first estimate always undershoots. Two measure-and-correct
    passes on a one-second render are cheap (about a second each) and turn the
    grade into a *measured* match instead of a constant that happened to look
    right on one clip.
    """
    log = log or (lambda m, k="log": None)
    tgt = (profile.get("grade") or {}).get("target") or {}
    if not tgt:
        return adapt_grade(profile, clip, seek)
    key = (os.path.abspath(clip), os.path.getmtime(clip),
           json.dumps(tgt, sort_keys=True), profile["format"]["w"])
    if key in _CAL:
        return dict(_CAL[key])
    g = adapt_grade(profile, clip, seek)
    for _ in range(iters):
        out = tempfile.mktemp(suffix=".mp4")
        try:
            _quick_render(clip, out, 1.2, profile, seek, g)
            got = probe.mean_stats(out, 1.2)
        except Exception:
            got = {}
        finally:
            try:
                os.unlink(out)
            except OSError:
                pass
        if not got:
            break
        dy = (tgt.get("yavg") or 0) - (got.get("YAVG") or 0)
        ds = (tgt.get("satavg") or 0) - (got.get("SATAVG") or 0)
        if abs(dy) < 1.5 and abs(ds) < 0.6:
            break
        g["brightness"] = round(_clamp((g.get("brightness") or 0) + dy / 255.0, -0.45, 0.30), 3)
        if got.get("SATAVG"):
            g["saturation"] = round(_clamp((g.get("saturation") or 0.4)
                                           * ((tgt.get("satavg") or 1) / got["SATAVG"]), 0.03, 1.3), 3)
    _CAL[key] = dict(g)
    log("  grade calibrated: brightness %.3f saturation %.3f" % (
        g.get("brightness", 0), g.get("saturation", 0)))
    return g


def _encode(cmd, duration, log, cancel):
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, bufsize=1)
    last = -1
    for ln in proc.stdout:
        m = re.search(r"out_time_ms=(\d+)", ln)
        if m:
            pct = min(99, int(int(m.group(1)) / 1e6 / max(0.1, duration) * 100))
            if pct >= last + 10:
                last = pct
                log("    %d%%" % pct)
        if cancel and cancel():
            proc.kill()
            raise RuntimeError("cancelled")
    proc.wait()
    return proc.returncode, (proc.stderr.read() or "")


def render(clip, out, duration, lines, profile, audio=None, seek=None,
           log=None, cancel=None, max_passes=2, tolerance=5.0):
    """Render one reel. `lines` is a list of strings (already wrapped).

    Two passes at most: encode, measure against the profile, and if the luma is
    still off by more than `tolerance`, correct the brightness and encode again.
    A one-second probe is not the same as the finished file, so the *output* is
    what gets corrected.
    """
    log = log or (lambda m, k="log": None)
    if not probe.FF:
        raise RuntimeError("ffmpeg not found")
    info = probe.probe(clip)
    if not info:
        raise RuntimeError("cannot read clip %s" % os.path.basename(clip))
    if seek is None:
        seek = 0.0 if info["dur"] <= duration + 0.2 else round(
            min(info["dur"] - duration - 0.2, info["dur"] * 0.18), 2)
    profile = dict(profile)
    profile["grade"] = calibrate(profile, clip, seek, log=log)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    tmp_text = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
    tmp_text.write("\n".join(lines))
    tmp_text.close()
    log("  render %.2fs from %s @%.2fs" % (duration, os.path.basename(clip), seek))

    match = {}
    try:
        for attempt in range(max_passes):
            cmd = build_cmd(clip, out, duration, profile["text"], profile, audio,
                            seek=seek, textfile=os.path.abspath(tmp_text.name))
            rc, err = _encode(cmd, duration, log, cancel)
            if rc != 0 or not os.path.exists(out):
                tail = "\n".join(err.strip().splitlines()[-4:])
                raise RuntimeError("ffmpeg failed: %s" % tail[:400])
            match = check_match(out, profile)
            dy = (match.get("delta") or {}).get("yavg")
            if dy is None or abs(dy) <= tolerance or attempt == max_passes - 1:
                break
            log("  luma off by %+.1f — correcting and re-encoding" % dy)
            g = profile["grade"]
            g["brightness"] = round(_clamp((g.get("brightness") or 0) - dy / 255.0,
                                           -0.45, 0.30), 3)
    finally:
        try:
            os.unlink(tmp_text.name)
        except OSError:
            pass
    return out


def check_match(out, profile):
    """Measure the render against the profile's own grade targets."""
    got = probe.mean_stats(out, (probe.probe(out) or {}).get("dur"))
    tgt = profile.get("grade", {}).get("target", {})
    if not got or not tgt:
        return {"measured": got, "target": tgt, "delta": {}}
    delta = {}
    for mk, tk in (("YAVG", "yavg"), ("SATAVG", "satavg")):
        if mk in got and tk in tgt:
            delta[tk] = round(got[mk] - tgt[tk], 1)
    return {"measured": {k: round(v, 1) for k, v in got.items()},
            "target": tgt, "delta": delta}


def describe(profile):
    f = profile["format"]
    return "%dx%d · %.1fs · %s" % (f["w"], f["h"], f["duration"]["median"],
                                   "single shot" if f["single_shot"] else "cut montage")