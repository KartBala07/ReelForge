# The studied format

Measured from a 1,000-reel reference set (a reel account archived with ReelVault),
sampled 14–160 files depending on the measurement. Everything here is a number
the renderer uses; nothing visual is copied.

## Method

* **Duration / canvas** — `ffmpeg -i` on a random sample of 160 reels, parsing the
  `Duration:` line and the `Video:` stream (this ffmpeg build ships no `ffprobe`).
* **Shot structure** — `select='gt(scene,0.10)'` over 8–16 reels, counting hard
  cuts. Threshold 0.10 is deliberately low so soft night-time cuts would still
  register.
* **Grade** — `signalstats` on one frame per reel across 12 reels, then the median
  of `YAVG` (mean luma 0–255), `SATAVG` (mean saturation) and `HUEAVG`.
* **Text** — inspection of extracted frame strips from the highest-performing
  reels (OCR on stylised night footage is unreliable, so the block is described,
  not measured). Line breaks were transcribed from the strips.

## Numbers

| property | value |
| --- | --- |
| canvas | 1080×1920 (also archived at 720×1280), aspect 0.5625 |
| duration | min 5.1 s · p25 6.6 · **median 6.7–6.9** · p75 6.9 · max 8.1–11.6 |
| shots | **0 hard cuts** at threshold 0.10 → one continuous take per reel |
| luma (YAVG) | median **36–38** (p15 29, p85 53) — night footage, crushed shadows |
| saturation (SATAVG) | median **2.8–3.6** — effectively near-monochrome, colour only in lights |
| hue | 128–155 (teal-cyan cast in shadows, warm practicals) |
| audio | 100% of reels have a 44.1 kHz stereo track; `audio_title` is "Original audio" |
| captions | empty on 994/1000 reels — the text lives *in* the video |
| likes | median 350, p99 250k+ |

## The text block

From frame strips of the top reels (223k / 247k / 41k likes):

* pale yellow / cream (`0xF0E3A6`-ish), no outline, no scrim, no box;
* plain sans-serif, regular weight, all lowercase;
* 4–5 lines, roughly 19–22 characters per line;
* centred per line, block top at about a fifth to a quarter of the height;
* no emoji, no hashtags, no punctuation decoration;
* the line is a first-person confession, a second-person address, or a
  "if you're seeing this…" hook.

The format is therefore: **one continuous moody shot + one intimate line**. The
footage is the wallpaper; the line is the product.

## What this implies for generation

1. Do not montage — cut nothing, one clip per reel.
2. Length is 6–7 s, not 15 or 30.
3. Grade to near-monochrome with teal shadows; a colour-graded reel does not read
   as the same format.
4. Put the line in the upper third, centred, small enough to leave the footage
   visible.
5. The line has to be worth reading on its own. That is the whole trick.
