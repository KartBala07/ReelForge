"""The text engine.

The reference format is not the footage — it is the *line*. One intimate,
lowercase, first-person confession, broken into 4–5 short lines, no emoji, no
hashtags, no punctuation flourishes. So this module is a small grammar of
frames + word banks (deterministic, offline, always available) with an optional
local-LLM pass on top when an OpenAI-compatible endpoint is configured.

Frames are generic; the reference account's actual lines are never copied.
"""
import random
import re
import urllib.request
import json

# ---------------------------------------------------------------- word banks

NIGHT = ["2am", "3am", "4am", "midnight", "1am", "dawn", "3am"]
PLACES = ["street", "parking lot", "old road", "bus stop", "bridge", "corner store",
          "driveway", "petrol station", "back road", "empty street"]
NOUNS = ["silence", "distance", "quiet", "cold air", "streetlight", "rain",
         "radio static", "last message", "empty seat", "rear view"]
FEELINGS = ["heavier", "quieter", "colder", "smaller", "louder", "lighter"]
VERBS = ["drive", "walk", "sit", "stare", "wait", "listen", "smoke", "park"]
VERBING = {"drive": "driving", "walk": "walking", "sit": "sitting", "stare": "staring",
           "wait": "waiting", "listen": "listening", "smoke": "smoking", "park": "parking"}
ADJ = ["quiet", "ordinary", "honest", "slow", "tired", "gentle"]

THEMES = {
    "longing": [
        "i still {verb} past the {place} where we used to park",
        "nobody tells you how {adj} the {noun} gets after {time}",
        "i wonder if you still {verb} with the radio off too",
        "the {place} looks the same. i don't.",
        "if you're {verbing} home alone tonight, so am i",
        "i kept your {noun} in the car like it still means something",
        "i don't miss you. i miss who i was in that {place}",
        "some nights the {noun} is the only thing that answers",
    ],
    "late-night": [
        "it's {time} and i'm {verbing} with nowhere to be",
        "{time} thoughts hit different when the {place} is empty",
        "if you see headlights behind you at {time}, that's not me",
        "i only feel {adj} at {time}, in the {place}, engine off",
        "everyone's asleep and i'm {verbing} through the {place} again",
        "the city goes quiet at {time} and i finally breathe",
    ],
    "self-worth": [
        "i wonder what my most {adj} feature is",
        "i spent years shrinking so people would stay. they didn't",
        "nobody claps when you finally choose yourself. do it anyway",
        "i'm not hard to love. i was just loved badly",
        "the version of me that apologises for existing is gone",
        "i used to beg for {adj} attention. now i leave",
    ],
    "crush": [
        "if she sends you this, she's been {verbing} about you all week",
        "you don't have to be {adj}. you just have to be there at {time}",
        "i re-read your last message like it's a {noun}",
        "i hope you notice me the way i notice the {place} at {time}",
        "if he wanted to, he would. i'm still learning that",
    ],
    "heartbreak": [
        "the worst part wasn't the end. it was the {noun} after",
        "i stopped {verbing} past your {place}. that took a year",
        "you said forever at {time}. i believed it",
        "i don't hate you. i just don't {verb} the same anymore",
        "i hope the {place} treats you better than i did",
    ],
    "nostalgia": [
        "this {place} used to mean everything at {time}",
        "i'd give anything to {verb} that {place} one more time",
        "we were {adj} and broke and it was enough",
        "the {noun} sounds like being 19 again",
    ],
}

FILL = {
    "time": NIGHT, "place": PLACES, "noun": NOUNS, "adj": ADJ,
    "verb": VERBS, "verbing": list(VERBING.values()),
}

SLOT_RE = re.compile(r"\{(\w+)\}")


def wrap(text, width=22, max_lines=5):
    """Greedy wrap with a slightly wider last line so a lone word never dangles."""
    words = text.split()
    lines, cur = [], ""
    for w in words:
        candidate = (cur + " " + w).strip()
        if len(candidate) <= width or not cur:
            cur = candidate
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        # merge the tail rather than cutting text away
        head = lines[:max_lines - 1]
        head.append(" ".join(lines[max_lines - 1:]))
        lines = head
    return lines


def _fill(frame, rng):
    def sub(m):
        bank = FILL.get(m.group(1))
        return rng.choice(bank) if bank else m.group(0)
    return SLOT_RE.sub(sub, frame)


def generate(topic=None, n=8, seed=None, width=22, max_lines=5):
    """Return n distinct lines: [{'text','lines','theme'}]."""
    rng = random.Random(seed)
    themes = list(THEMES)
    if topic and topic in THEMES:
        themes = [topic] * 3 + [t for t in themes if t != topic]
    out, seen = [], set()
    tries = 0
    while len(out) < n and tries < n * 40:
        tries += 1
        theme = rng.choice(themes)
        text = _fill(rng.choice(THEMES[theme]), rng)
        text = text.lower().strip()
        if text in seen:
            continue
        seen.add(text)
        out.append({"text": text, "lines": wrap(text, width, max_lines), "theme": theme})
    return out


# ---------------------------------------------------------------- optional LLM

def llm_lines(topic, n, url, model):
    """Ask a local OpenAI-compatible server for more lines; None on any failure."""
    prompt = (
        "Write %d short Instagram text-overlay lines in this exact style: "
        "lowercase, no emoji, no hashtags, first person, intimate, 8-16 words, "
        "like a note-to-self at 2am. One per line, no numbering. Topic: %s."
        % (n, topic or "late-night longing"))
    body = json.dumps({
        "model": model, "temperature": 0.9, "max_tokens": 400,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(url.rstrip("/") + "/chat/completions", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.load(r)
        text = data["choices"][0]["message"]["content"]
    except Exception:
        return None
    lines = []
    for raw in text.splitlines():
        s = raw.strip().strip("-•*0123456789. ").lower()
        if 20 <= len(s) <= 140 and not s.startswith("#"):
            lines.append({"text": s, "lines": wrap(s), "theme": "llm"})
    return lines[:n] or None
