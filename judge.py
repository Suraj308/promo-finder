import json
import re

from parse import fmt
from candidates import cut_points, seg, MIN_D, MAX_D
from score import score_window
from llm import Pending

SYSTEM = """You are a senior promo editor for a vertical microdrama app (like ReelShort or DramaBox).
You pick 20-60 second clips from a show's screenplay to post on TikTok/Instagram/YouTube Shorts to pull in NEW viewers.

A good promo:
1. HOOK: the first 3 seconds make a scrolling viewer stop (a slap, a reveal, a shocking or cruel line, a visible threat).
2. CLIFFHANGER: it cuts at an unresolved moment, so the viewer must know what happens next.
3. STANDALONE: someone who has never seen the show understands who is wronging whom within seconds.

For each candidate you get the screenplay around it with timestamps. Lines between [PROMO START] and [PROMO END] are the clip.
Lines outside are context only. You may move the start and/or end, but ONLY to one of the listed allowed cut points, and the clip must stay 20-60 seconds.

Judge only what is in the text. Do not invent events that are not in the screenplay. Quote or closely paraphrase the actual line or action for hook and cliffhanger.

Return ONLY a JSON array, one object per candidate, in the same order:
{"id": str,
 "hook_score": 0-10, "cliff_score": 0-10, "standalone_score": 0-10,
 "start": allowed start cut (mm:ss.ss) or null to keep, "end": allowed end cut or null to keep,
 "hook": what happens in the first 3 seconds of the (possibly moved) clip,
 "cliffhanger": the unresolved moment it cuts on,
 "why": one line on why it works as a promo,
 "verdict": "post" | "maybe" | "reject",
 "issue": short note on the main weakness, or ""}"""


def short(b):
    if b.kind == "LINE":
        tag = f" ({', '.join(b.tags)})" if b.tags else ""
        return f'{b.who}{tag}: "{b.text}"'
    return f"[{b.kind}] {b.text}"


def block(ep, a, e, pad_before=15.0, pad_after=10.0):
    lo, hi = max(0.0, a - pad_before), min(ep.length, e + pad_after)
    out = []
    opened = closed = False
    for b in ep.beats:
        if b.start < lo - 0.05 or b.start > hi:
            continue
        if not opened and b.start >= a - 0.05:
            out.append("[PROMO START]")
            opened = True
        if opened and not closed and b.start >= e - 0.05:
            out.append("[PROMO END]")
            closed = True
        if b.kind == "CAMERA" and not (a - 0.05 <= b.start < e):
            continue
        out.append(f"{fmt(b.start)}-{fmt(b.end)} {short(b)}")
    if not closed:
        out.append("[PROMO END]" + (" (end of episode)" if e >= ep.length - 0.5 else ""))
    return out


def describe(c, byn):
    segs = c["segments"]
    head = f"### Candidate {c['id']}"
    if len(segs) == 1:
        s = segs[0]
        ep = byn[s["episode"]]
        starts, ends = cut_points(ep)
        st = [fmt(t) for t in starts if s["start"] - 15 <= t <= s["start"] + 15]
        en = [fmt(t) for t in ends if s["end"] - 15 <= t <= s["end"] + 10]
        lines = [head, f"Episode {ep.num}. Logline: {ep.logline}",
                 f"Current clip: {s['start_ts']} -> {s['end_ts']} ({s['end']-s['start']:.1f}s)",
                 f"Allowed start cuts: {', '.join(st)}", f"Allowed end cuts: {', '.join(en)}"]
        lines += block(ep, s["start"], s["end"])
        return "\n".join(lines)
    lines = [head, "Multi-segment promo (segments play in this order; start/end cannot be moved, return null)."]
    for k, s in enumerate(segs, 1):
        ep = byn[s["episode"]]
        lines.append(f"-- Segment {k}: Episode {ep.num} {s['start_ts']} -> {s['end_ts']}. Logline: {ep.logline}")
        lines += block(ep, s["start"], s["end"], 0.0, 8.0 if k == len(segs) else 0.0)
    return "\n".join(lines)


def premise(eps):
    return eps[0].logline if eps else ""


def user_prompt(batch, eps, show):
    byn = {e.num: e for e in eps}
    parts = [f"Show: {show}", f"Premise (episode 1): {premise(eps)}", ""]
    parts += [describe(c, byn) + "\n" for c in batch]
    parts.append(f"Return the JSON array for these {len(batch)} candidates: {', '.join(c['id'] for c in batch)}.")
    return "\n".join(parts)


def parse_json(txt):
    t = txt.strip()
    t = re.sub(r"^```(?:json)?|```$", "", t, flags=re.M).strip()
    m = re.search(r"\[.*\]", t, re.S)
    return json.loads(m.group(0) if m else t)


def to_sec(ts):
    m = re.match(r"^\s*(\d+):(\d+(?:\.\d+)?)\s*$", str(ts or ""))
    return int(m.group(1)) * 60 + float(m.group(2)) if m else None


def apply_trim(c, j, byn):
    if len(c["segments"]) != 1:
        return False
    s = c["segments"][0]
    ep = byn[s["episode"]]
    starts, ends = cut_points(ep)
    a, e = s["start"], s["end"]
    na, ne = to_sec(j.get("start")), to_sec(j.get("end"))
    if na is not None and any(abs(na - x) < 0.02 for x in starts):
        a = na
    if ne is not None and any(abs(ne - x) < 0.02 for x in ends):
        e = ne
    if (a, e) == (s["start"], s["end"]) or not (MIN_D <= e - a <= MAX_D):
        return False
    r = score_window(ep, a, e)
    c["trimmed_from"] = [s["start_ts"], s["end_ts"]]
    c["segments"] = [seg(ep, a, e)]
    c.update(r)
    c["duration"] = round(e - a, 1)
    return True


def judge(cands, eps, show, llm, batch_size=10, log=print):
    byn = {e.num: e for e in eps}
    pending = []
    for k in range(0, len(cands), batch_size):
        batch = cands[k:k + batch_size]
        try:
            raw = llm(SYSTEM, user_prompt(batch, eps, show))
        except Pending as p:
            pending.append(str(p))
            continue
        except Exception as ex:
            log(f"  batch {k//batch_size+1}: model call failed ({str(ex)[:200]}); keeping rules scores for this batch")
            continue
        try:
            arr = parse_json(raw)
        except Exception as ex:
            log(f"  batch {k//batch_size+1}: could not parse model output ({ex}); keeping heuristic scores")
            continue
        got = {str(x.get("id")): x for x in arr if isinstance(x, dict)}
        for c in batch:
            j = got.get(c["id"])
            if not j:
                continue
            c["trimmed"] = apply_trim(c, j, byn)
            c["llm"] = {k2: j.get(k2) for k2 in ("hook_score", "cliff_score", "standalone_score", "hook", "cliffhanger", "why", "verdict", "issue", "start", "end")}
        log(f"  judged batch {k//batch_size+1}/{(len(cands)+batch_size-1)//batch_size}")
    return pending


def num(x):
    try:
        return max(0.0, min(10.0, float(x)))
    except Exception:
        return None


def final_score(c):
    j = c.get("llm")
    if not j:
        return c["score"]
    h, cl, st = num(j.get("hook_score")), num(j.get("cliff_score")), num(j.get("standalone_score"))
    if None in (h, cl, st):
        return c["score"]
    llm = 10 * (0.4 * h + 0.4 * cl + 0.2 * st)
    v = 0.35 * c["score"] + 0.65 * llm
    if j.get("verdict") == "reject":
        v *= 0.6
    elif j.get("verdict") == "maybe":
        v *= 0.9
    return round(v, 1)
