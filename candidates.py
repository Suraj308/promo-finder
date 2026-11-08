from score import score_window, beat_heat, inside
from parse import fmt

MIN_D, MAX_D = 20.0, 60.0


def spans_line(ep, t):
    return any(b.kind == "LINE" and b.start + 0.05 < t < b.end - 0.05 for b in ep.beats)


def cut_points(ep):
    cams = [b for b in ep.beats if b.kind == "CAMERA"]
    src = cams if len(cams) >= 5 else ep.beats
    starts = sorted({round(b.start, 2) for b in src if not spans_line(ep, b.start)})
    ends = sorted({round(b.end, 2) for b in src if not spans_line(ep, b.end)})
    return starts, ends


def seg(ep, a, e):
    return {"episode": ep.num, "start": a, "end": e, "start_ts": fmt(a), "end_ts": fmt(e)}


def overlap(x, y):
    if x["episode"] != y["episode"]:
        return 0.0
    lo, hi = max(x["start"], y["start"]), min(x["end"], y["end"])
    return max(0.0, hi - lo) / max(1e-6, min(x["end"] - x["start"], y["end"] - y["start"]))


def single_candidates(ep, keep=6):
    starts, ends = cut_points(ep)
    pool = []
    for a in starts:
        for e in ends:
            if MIN_D <= e - a <= MAX_D:
                r = score_window(ep, a, e)
                pool.append((r["score"], a, e, r))
    pool.sort(key=lambda x: -x[0])
    out = []
    for sc, a, e, r in pool:
        c = {"segments": [seg(ep, a, e)], **r}
        if all(overlap(c["segments"][0], o["segments"][0]) < 0.35 for o in out):
            out.append(c)
        if len(out) >= keep:
            break
    return out


def hook_clips(ep, before=None, n=3):
    starts, ends = cut_points(ep)
    lim = before if before is not None else ep.length + 1
    res = []
    for a in starts:
        for e in ends:
            if 3.0 <= e - a <= 10.0 and e <= lim:
                bs = inside(ep.beats, a, e)
                if not any(b.kind == "LINE" for b in bs):
                    continue
                h = max(beat_heat(b) for b in bs)
                res.append((h, a, e))
    res.sort(key=lambda x: -x[0])
    out = []
    for h, a, e in res:
        if all(abs(a - o[1]) > 8 for o in out):
            out.append((h, a, e))
        if len(out) >= n:
            break
    return out


def montage_candidates(eps, singles):
    byn = {e.num: e for e in eps}
    out = []
    for c in singles:
        if c["hook_s"] >= 0.6 or c["cliff_s"] < 0.5:
            continue
        s0 = c["segments"][0]
        ep = byn[s0["episode"]]
        room = MAX_D - (s0["end"] - s0["start"])
        if room < 4:
            continue
        opts = [(h, ep, a, e) for h, a, e in hook_clips(ep, before=s0["start"] - 0.01)]
        prev = byn.get(ep.num - 1)
        if prev:
            opts += [(h, prev, a, e) for h, a, e in hook_clips(prev)]
        opts = [o for o in opts if o[3] - o[2] <= room and o[0] >= 0.75]
        if not opts:
            continue
        h, hep, a, e = max(opts, key=lambda x: x[0])
        hb = max(inside(hep.beats, a, e), key=beat_heat)
        gain = (h - c["hook_s"]) * 33
        m = dict(c)
        m["segments"] = [seg(hep, a, e), dict(s0)]
        m["hook_s"] = round(h, 2)
        m["hook_beat"] = hb
        m["score"] = round(c["score"] + gain - 3.0, 1)
        m["montage"] = True
        out.append(m)
    return out


def total_dur(c):
    return sum(s["end"] - s["start"] for s in c["segments"])


def generate(eps, per_ep=6):
    singles = []
    for ep in eps:
        singles += single_candidates(ep, keep=per_ep)
    monts = montage_candidates(eps, singles)
    allc = singles + monts
    allc.sort(key=lambda c: -c["score"])
    for k, c in enumerate(allc):
        c["id"] = f"C{k+1:03d}"
        c["duration"] = round(total_dur(c), 1)
    return allc
