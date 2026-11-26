#!/usr/bin/env python3
"""Batch renderer: Profile × clips × lines → N reels.

  python3 -m reelforge.render_cli --profile p.json --source-dir ~/clips \
      --out-dir ~/reels --count 3 --lines '<json>'

Prints machine-readable markers the job stream understands:
  RENDERED <file>          FAILED <file> <reason>          SUMMARY ok=N failed=N
"""
import argparse
import json
import os
import random
import re
import sys
import time
import traceback

from reelforge import lines as linebank
from reelforge import render, style


def clips_in(folder):
    out = []
    for name in sorted(os.listdir(folder)):
        if name.lower().endswith((".mp4", ".mov", ".m4v", ".webm")):
            out.append(os.path.join(folder, name))
    return out


def slug(text, limit=26):
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (s[:limit].strip("-") or "reel")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    ap.add_argument("--source-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--lines", default="[]", help="JSON list of {text,lines,theme}")
    ap.add_argument("--clips", default="[]", help="JSON list of clip filenames")
    ap.add_argument("--topic", default="", help="used when --lines is empty")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--audio", default="")
    ap.add_argument("--result", default="")
    a = ap.parse_args()

    def log(msg, kind="log"):
        print(msg, flush=True)

    prof = style.load(a.profile)
    all_clips = clips_in(a.source_dir)
    chosen = [os.path.join(a.source_dir, c) for c in json.loads(a.clips or "[]")]
    chosen = [c for c in chosen if os.path.isfile(c)] or all_clips
    if not chosen:
        log("no clips in %s — add footage first" % a.source_dir, "error")
        print("SUMMARY ok=0 failed=0", flush=True)
        return 2

    rng = random.Random(a.seed)
    pending = json.loads(a.lines or "[]")
    if not pending:
        pending = linebank.generate(topic=a.topic or None, n=a.count, seed=a.seed)
    if len(pending) < a.count:
        pending += linebank.generate(n=a.count - len(pending), seed=rng.randint(0, 10 ** 6))

    log("profile %s · %s" % (prof.get("name"), render.describe(prof)))
    log("rendering %d reel(s) from %d clip(s)" % (a.count, len(chosen)))
    os.makedirs(a.out_dir, exist_ok=True)

    results, ok, failed = [], 0, 0
    for i in range(a.count):
        clip = chosen[i % len(chosen)]
        item = pending[i % len(pending)]
        dur = style.render_duration(prof, i)
        ts = time.strftime("%Y%m%d-%H%M%S")
        name = "%s_%02d_%s_%s.mp4" % (ts, i + 1, slug(item.get("theme") or item.get("text")),
                                      os.path.basename(clip)[:8].strip("_"))
        out = os.path.join(a.out_dir, name)
        log("→ %s ← %s" % (name, os.path.basename(clip)))
        log("  %r" % (item.get("text", "")[:110],))
        try:
            render.render(clip, out, dur, item["lines"], prof, audio=a.audio or None, log=log)
            match = render.check_match(out, prof)
            print("RENDERED %s" % name, flush=True)
            d = match.get("delta") or {}
            log("  match: luma %+0.1f · sat %+0.1f" % (d.get("yavg", 0), d.get("satavg", 0)), "done")
            results.append({"file": name, "clip": os.path.basename(clip),
                            "line": item.get("text", ""), "theme": item.get("theme", ""),
                            "duration": dur, "profile": prof.get("name", ""),
                            "match": match})
            ok += 1
        except Exception as e:
            failed += 1
            log("✗ %s — %s" % (name, str(e)[:200]), "error")
            print("FAILED %s %s" % (name, str(e)[:120].replace("\n", " ")), flush=True)
            results.append({"file": name, "clip": os.path.basename(clip),
                            "line": item.get("text", ""), "error": str(e)[:300]})
        if a.result:
            try:
                with open(a.result, "w") as f:
                    json.dump(results, f)
            except Exception:
                pass

    print("SUMMARY ok=%d failed=%d" % (ok, failed), flush=True)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
