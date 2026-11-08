import re

from parse import fmt
from candidates import cut_points, seg, overlap, MIN_D, MAX_D
from score import score_window, hook_part, inside, W
from judge import parse_json
from llm import Pending

SYSTEM = """You are a senior promo editor for a vertical microdrama app (like ReelShort or DramaBox).
You read whole episodes and point at the moments that would make the best 20-60 second promo for cold viewers on TikTok/Reels.

For each episode, pick up to 2 promo moments. For each moment give:
- hook: the beat id (like b23) of the line or action that should open the promo. It must stop a scrolling viewer in 3 seconds:
  a shocking line, a reveal, a slap, a threat. Prefer moments that show who is wronging whom.
- cliff: the beat id where the promo should cut. It must come after the hook and leave an open question
  (an unanswered accusation, a threat about to land, a reveal about to happen). The cut happens right after this beat.
- event: 3-6 words naming the story event (used to avoid posting the same event twice).
- spoiler: true if the moment gives away a major twist of the show.

Use only beat ids that appear in the text. Do not invent events. A moment may be any length; code will cut it.

Return ONLY a JSON array: [{"episode": int, "moments": [{"hook": "b12", "cliff": "b30", "event": str, "spoiler": bool}]}]"""


def compact(ep):
    out = [f"## Episode {ep.num}. Logline: {ep.logline}"]
    for b in ep.beats:
        if b.kind == "CAMERA":
            continue
        if b.kind == "LINE":
            tag = f" ({', '.join(b.tags)})" if b.tags else ""
            t = f'{b.who}{tag}: "{b.text}"'
        else:
            t = f"[{b.kind}] {b.text}"
        out.append(f"b{b.i} {fmt(b.start)} {t[:220]}")
    return "\n".join(out)


def prompt(eps_batch, show, premise):
    parts = [f"Show: {show}", f"Premise (episode 1): {premise}", ""]
    parts += [compact(e) + "\n" for e in eps_batch]
    parts.append(f"Return the JSON array for episodes {', '.join(str(e.num) for e in eps_batch)}.")
    return "\n".join(parts)


def bid(x):
    m = re.search(r"(\d+)", str(x or ""))
    return int(m.group(1)) if m else None


def run_scout(eps, show, llm, per=5, log=print):
    moments, pending = [], []
    for k in range(0, len(eps), per):
        batch = eps[k:k + per]
        try:
            raw = llm(SYSTEM, prompt(batch, show, eps[0].logline))
            arr = parse_json(raw)
        except Pending as p:
            pending.append(str(p))
            continue
        except Exception as ex:
            log(f"  scout batch {k//per+1}: failed ({str(ex)[:200]})")
            continue
        byn = {e.num: e for e in batch}
        for item in arr:
            ep = byn.get(item.get("episode"))
            if not ep:
                continue
            for m in item.get("moments", [])[:2]:
                h, c = bid(m.get("hook")), bid(m.get("cliff"))
                if h is None or c is None or not (0 <= h < len(ep.beats)) or not (0 <= c < len(ep.beats)) or c < h:
                    continue
                moments.append({"episode": ep.num, "hook": h, "cliff": c, "event": m.get("event", ""),
                                "spoiler": bool(m.get("spoiler"))})
        log(f"  scouted episodes {batch[0].num}-{batch[-1].num}")
    return moments, pending


def build(ep, h, c):
    starts, ends = cut_points(ep)
    hb, cb = ep.beats[h], ep.beats[c]
    a = max([t for t in starts if hb.start - 3.0 <= t <= hb.start + 0.05], default=None)
    if a is None:
        a = max([t for t in starts if t <= hb.start + 0.05], default=None)
    e = min([t for t in ends if t >= cb.end - 0.05], default=None)
    if a is None or e is None or e <= a:
        return None
    d = e - a
    if MIN_D <= d <= MAX_D:
        return [seg(ep, a, e)]
    if d < MIN_D:
        a2 = max([t for t in starts if e - MAX_D <= t <= e - MIN_D], default=None)
        return [seg(ep, a2, e)] if a2 is not None else None
    e1 = min([t for t in ends if a + 3.0 <= t <= a + 15.0 and t >= hb.end - 0.05], default=None)
    if e1 is None:
        return None
    room = MAX_D - (e1 - a)
    a2 = min([t for t in starts if t >= e - room and t >= e1 and e - t >= 15.0], default=None)
    if a2 is None:
        return None
    return [seg(ep, a, e1), seg(ep, a2, e)]


def scout_candidates(eps, moments, taken):
    byn = {e.num: e for e in eps}
    out = []
    for k, m in enumerate(moments):
        ep = byn[m["episode"]]
        segs = build(ep, m["hook"], m["cliff"])
        if not segs:
            continue
        last = segs[-1]
        r = score_window(ep, last["start"], last["end"])
        if len(segs) > 1:
            first = segs[0]
            h, hb = hook_part(inside(ep.beats, first["start"], first["end"]), first["start"], first["end"])
            r["hook_s"], r["hook_beat"] = round(h, 2), hb
            r["score"] = round(100 * (W["hook"] * h + W["cliff"] * r["cliff_s"] + W["stand"] * r["stand_s"]
                                      + W["dens"] * r["dens_s"] + W["dur"] * 1.0), 1)
        c = {"segments": segs, **r, "id": f"S{k+1:03d}", "scout": m,
             "duration": round(sum(s["end"] - s["start"] for s in segs), 1)}
        if len(segs) > 1:
            c["montage"] = True
        c["covered"] = round(max([covered(segs, t["segments"]) for t in taken + out] + [0.0]), 2)
        c["strict_dup"] = any(max(overlap(x, y) for x in segs for y in t["segments"]) >= 0.6 for t in taken + out)
        if c["covered"] >= 0.6:
            continue
        out.append(c)
    return out


def covered(segs, other):
    tot = sum(s["end"] - s["start"] for s in segs)
    hit = 0.0
    for s in segs:
        for o in other:
            if s["episode"] == o["episode"]:
                hit += max(0.0, min(s["end"], o["end"]) - max(s["start"], o["start"]))
    return hit / max(tot, 1e-6)
