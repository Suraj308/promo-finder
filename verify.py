import re

from judge import num
from score import inside

GATES = {"hook_score": 7, "cliff_score": 7, "standalone_score": 6}


def norm(t):
    t = t.lower().replace("…", "...").replace("’", "'")
    t = re.sub(r"\.\.\.+", " ", t)
    t = re.sub(r"[^a-z0-9' ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def quotes(text):
    q = re.findall(r'"([^"]+)"|“([^”]+)”', str(text or ""))
    out = [norm(a or b) for a, b in q]
    return [x for x in out if len(x.split()) >= 2 or len(x) >= 6]


def find(ep, q):
    hits = []
    for b in ep.beats:
        if b.kind != "LINE":
            continue
        t = norm(b.text)
        if q in t or (len(t) >= 8 and t in q):
            hits.append(b)
    return hits


def hook_check(c, byn):
    s = c["segments"][0]
    ep = byn[s["episode"]]
    qs = quotes(c.get("hook"))
    if not qs:
        return None, "no quoted line to verify"
    found = [b for q in qs for b in find(ep, q)]
    if not found:
        return None, "quoted line not found in script"
    if any(s["start"] - 0.1 <= b.start <= s["start"] + 3.0 for b in found):
        return True, ""
    first = min((b for b in found if b.start >= s["start"] - 0.1), key=lambda b: b.start, default=None)
    if first and first.start <= s["end"]:
        return False, f"quoted hook starts {first.start - s['start']:.1f}s into the clip"
    return False, "quoted hook is not inside the clip"


def cliff_check(c, byn):
    s = c["segments"][-1]
    ep = byn[s["episode"]]
    qs = quotes(c.get("cliffhanger"))
    if not qs:
        return None, "no quoted line to verify"
    found = [b for q in qs for b in find(ep, q)]
    if not found:
        return None, "quoted line not found in script"
    inside_clip = [b for b in found if s["start"] - 0.1 <= b.start and b.end <= s["end"] + 0.1]
    if any(b.start >= s["end"] - 10 for b in inside_clip):
        return True, ""
    if inside_clip:
        return True, "cliff line is more than 10s before the cut"
    after = [b for b in found if b.start >= s["end"] - 0.1]
    if after:
        return False, f"quoted cliffhanger is spoken {min(b.start for b in after) - s['end']:.1f}s after the clip ends"
    return False, "quoted cliffhanger is not inside the clip"


def gates(c):
    j = c.get("llm") or {}
    if not j:
        return None, []
    miss = []
    for k, v in GATES.items():
        x = num(j.get(k))
        if x is None or x < v:
            miss.append(f"{k.replace('_score', '')} {x if x is not None else '?'}<{v}")
    return not miss, miss


def verify(c, byn):
    h, hn = hook_check(c, byn)
    cl, cn = cliff_check(c, byn)
    g, miss = gates(c)
    c["verify"] = {"hook_in_first_3s": h, "hook_note": hn, "cliff_at_cut": cl, "cliff_note": cn,
                   "passes_gates": g, "gate_misses": miss}
    reasons = []
    if g is False:
        reasons.append("fails gate: " + ", ".join(miss))
    if h is False:
        reasons.append(hn)
    if cl is False:
        reasons.append(cn)
    if c["verdict"] == "post" and reasons:
        c["verdict"] = "maybe"
        c["downgraded"] = reasons
    return c


def scene_of(ep, t):
    for k, sc in enumerate(ep.scenes):
        if sc.get("start") is not None and sc.get("end") is not None and sc["start"] - 0.1 <= t <= sc["end"] + 0.1:
            return k, sc
    return None, None


def place(h):
    return re.split(r"\s+[–-]\s+", h or "")[0].strip().upper()


def same_scene(a, b, byn):
    if abs(a["episode"] - b["episode"]) != 1:
        return False
    x, y = (a, b) if a["episode"] < b["episode"] else (b, a)
    ex, ey = byn[x["episode"]], byn[y["episode"]]
    kx, sx = scene_of(ex, x["start"])
    ky, sy = scene_of(ey, y["start"])
    if sx is None or sy is None or ky != 0 or kx != len(ex.scenes) - 1:
        return False
    cont = "CONTINUOUS" in sy["heading"].upper() or re.search(r"picks up|continues", ey.notes, re.I)
    return bool(cont) and place(sx["heading"]) == place(sy["heading"])


def lines_of(c, byn):
    out = set()
    for s in c["segments"]:
        for b in inside(byn[s["episode"]].beats, s["start"], s["end"]):
            if b.kind == "LINE" and len(norm(b.text)) >= 15:
                out.add(norm(b.text))
    return out


def same_story(c, o, byn):
    if any(same_scene(a, b, byn) for a in c["segments"] for b in o["segments"]):
        return "same scene across an episode cut"
    if len(lines_of(c, byn) & lines_of(o, byn)) >= 2:
        return "repeats the same dialogue"
    return ""
