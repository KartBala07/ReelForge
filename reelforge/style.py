"""Study a set of reference reels and turn them into a renderable style profile.

What is measured (and why):

* **duration band** — p25/p50/p75 of the reference reels. Reels outside this band
  do not read as the same format.
* **shot structure** — hard cuts at a low scene threshold. A profile with a
  median of 0 cuts is a *single continuous shot* format, so the renderer must not
  cut; one with cuts gets a shot list.
* **grade** — mean luma, saturation and hue of a frame from each reel. These
  become the targets the renderer is checked against, not just a vibe.

Everything the renderer needs to be faithful is here; `render.check_match()`
measures an output against the same numbers.
"""
import glob
import json
import os
import statistics
from collections import Counter
import time
from concurrent.futures import ThreadPoolExecutor

from . import probe

DEFAULT_FONT = "/System/Library/Fonts/Supplemental/Arial.ttf"

# The text block is the one part that cannot be measured from pixels reliably
# (OCR of stylised night footage is unreliable), so it is a layout *derived from
# inspection of the reference set* and kept editable in the profile.
DEFAULT_TEXT = {
    "color": "0xF0E3A6",          # pale yellow, no outline
    "font": DEFAULT_FONT,
    "bold": False,
    "font_size": 52,              # 1080px wide canvas
    "line_spacing": 12,
    "y": 0.22,                    # top of the block, fraction of height
    "max_chars": 26,              # wrap width per line
    "max_lines": 5,
    "fade_in": 0.6,
    "align": "center",
}


def _pct(vals, q):
    vals = sorted(vals)
    if not vals:
        return None
    return vals[min(len(vals) - 1, max(0, int(round((len(vals) - 1) * q))))]


def _median(vals):
    return float(statistics.median(vals)) if vals else 0.0


def study(folder, sample=16, threshold=0.10, log=None):
    """Analyse `folder` of reference reels and return a style profile dict."""
    log = log or (lambda m, k="log": None)
    files = sorted(glob.glob(os.path.join(folder, "*.mp4")) +
                   glob.glob(os.path.join(folder, "*.mov")))
    if not files:
        raise RuntimeError("no .mp4/.mov files in %s" % folder)
    step = max(1, len(files) // sample)
    picked = files[::step][:sample]
    log("studying %d of %d reference reels" % (len(picked), len(files)))

    with ThreadPoolExecutor(max_workers=6) as ex:
        probes = [p for p in ex.map(probe.probe, picked) if p]
    with ThreadPoolExecutor(max_workers=4) as ex:
        stats = [s for s in ex.map(lambda f: probe.color_stats(f, 1.0), picked[:12]) if s]
    with ThreadPoolExecutor(max_workers=3) as ex:
        cuts = list(ex.map(lambda f: probe.scene_cuts(f, threshold), picked[:8]))
    if not probes:
        raise RuntimeError("could not read any reference reel in %s" % folder)

    durs = [p["dur"] for p in probes]
    # the canvas is the most common (w, h) pair, not a derived ratio: dividing
    # 1080 by a rounded aspect gives 1922 and Instagram rejects off-spec sizes
    dims = Counter((p["w"], p["h"]) for p in probes if p.get("w") and p.get("h"))
    cw, ch = dims.most_common(1)[0][0] if dims else (1080, 1920)
    ratios = sorted({round(p["w"] / float(p["h"]), 4) for p in probes if p.get("h")})
    cut_counts = [len(c) for c in cuts]
    single = (statistics.median(cut_counts) if cut_counts else 0) == 0

    profile = {
        "name": os.path.basename(os.path.normpath(folder)),
        "created": time.time(),
        "reference": {"folder": folder, "reels": len(files), "sampled": len(probes)},
        "format": {
            "w": cw,
            "h": ch,
            "aspect": round(cw / float(ch), 4),
            "fps": 30,
            "duration": {
                "median": round(_median(durs), 2),
                "p25": round(_pct(durs, 0.25), 2),
                "p75": round(_pct(durs, 0.75), 2),
                "min": round(min(durs), 2),
                "max": round(max(durs), 2),
            },
            "single_shot": bool(single),
            "cuts_per_reel": round(_median(cut_counts), 2) if cut_counts else 0,
            "shot_length": round(_median(durs) / (1 + (_median(cut_counts) or 0)), 2),
            "audio_share": round(sum(1 for p in probes if p.get("acodec")) / float(len(probes)), 2),
        },
        "grade": {},
        "text": dict(DEFAULT_TEXT),
    }

    if stats:
        y = _median([s.get("YAVG", 0) for s in stats])
        sat = _median([s.get("SATAVG", 0) for s in stats])
        hue = _median([s.get("HUEAVG", 0) for s in stats])
        lo = _pct([s.get("YAVG", 0) for s in stats], 0.15) or y
        hi = _pct([s.get("YAVG", 0) for s in stats], 0.85) or y
        profile["grade"] = {
            "measured": {"yavg": round(y, 1), "satavg": round(sat, 1),
                         "hueavg": round(hue, 1), "yavg_spread": round(hi - lo, 1)},
            "target": {"yavg": round(y, 1), "satavg": round(sat, 1)},
            "brightness": round(max(-0.15, min(0.15, (y - 46) / 900.0)), 3),
            "contrast": round(1.0 + max(0.0, (55 - y)) / 130.0, 3),
            "saturation": round(max(0.15, min(1.0, sat / 22.0)), 2),
            "teal_shadows": round(0.006 + max(0.0, (150 - hue)) / 2400.0, 3),
            "warm_highlights": 0.01,
            "vignette": 0.55,
            "grain": int(max(3, min(12, 14 - sat))),
        }
    else:
        profile["grade"] = {"measured": {}, "target": {}, "brightness": 0.0,
                            "contrast": 1.1, "saturation": 0.45, "teal_shadows": 0.05,
                            "warm_highlights": 0.01, "vignette": 0.55, "grain": 7}

    log("format: %d×%d · median %.1fs (%.1f–%.1f) · single shot: %s" % (
        profile["format"]["w"], profile["format"]["h"],
        profile["format"]["duration"]["median"], profile["format"]["duration"]["p25"],
        profile["format"]["duration"]["p75"], profile["format"]["single_shot"]))
    g = profile["grade"]["measured"]
    log("grade : luma %.1f · sat %.1f · hue %.1f → saturation %.2f, contrast %.2f" % (
        g.get("yavg", 0), g.get("satavg", 0), g.get("hueavg", 0),
        profile["grade"]["saturation"], profile["grade"]["contrast"]))
    return profile


def save(profile, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(profile, f, indent=1, sort_keys=True)
    return path


def load(path):
    with open(path) as f:
        return json.load(f)


def render_duration(profile, index=0):
    """Pick a duration inside the reference band, walking p25→p75 per item."""
    d = profile["format"]["duration"]
    lo, hi = d.get("p25") or d["median"], d.get("p75") or d["median"]
    band = [lo, (lo + hi) / 2.0, hi]
    return max(2.5, round(band[index % len(band)], 2))
