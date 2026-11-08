import re

HOT_TAGS = {
    "screaming": 1.0, "shouting": 0.9, "furious": 0.9, "enraged": 1.0, "roaring": 1.0, "outraged": 0.8,
    "menacing": 0.9, "threatening": 0.9, "venomous": 0.8, "sinister": 0.8, "murderous": 1.0, "lethal": 1.0,
    "cruel": 0.7, "vicious": 0.8, "viciously calm": 0.8, "gloating": 0.7, "mocking": 0.6, "taunting": 0.7, "sneering": 0.7,
    "spiteful": 0.6, "scornful": 0.6, "smug": 0.5, "cold": 0.5, "icy": 0.6, "commanding": 0.6, "stern": 0.4,
    "shocked": 0.8, "horrified": 0.8, "stunned": 0.6, "gasping": 0.6, "terrified": 0.8, "panicked": 0.7,
    "desperate": 0.6, "pleading": 0.5, "begging": 0.6, "sobbing": 0.6, "heartbroken": 0.5, "devastated": 0.6,
    "defiant": 0.6, "fierce": 0.6, "accusing": 0.7, "demanding": 0.5, "incredulous": 0.5, "dangerous": 0.8,
    "frantic": 0.7, "hysterical": 0.8, "triumphant": 0.5, "authoritative": 0.4, "booming": 0.7, "steely": 0.5,
}

HOT_WORDS = [
    (r"\bslap|\bstrik|\bstruck|\bpunch|\bhit\b|\bkick", 1.0),
    (r"\bkill|\bmurder|\bdie\b|\bdead\b|\bdeath|\bexecut|\bhang(ed|ing)?\b|\bgallows|\bburn", 0.9),
    (r"\bwitch|\bpoison|\bthief|\btheft|\btraitor|\btreason|\bliar|\bmonster|\bbastard", 0.8),
    (r"\bmarr(y|ied|iage)|\bwedding|\bbride|\bhusband|\bwife|\bdivorce|\bpregnan|\bheir\b", 0.5),
    (r"\bduke\b|\bduchess|\bcrown\b|\bsignet|\bcrest\b|\bring\b", 0.5),
    (r"\bhow dare|\bon your knees|\bkneel|\bguards?\b|\barrest|\bseize", 0.8),
    (r"\bworthless|\bnothing\b|\bplain\b|\bbeneath\b|\bdisown|\bdisgrace|\bhumiliat", 0.6),
    (r"\bnever\b|\bmine\b|\bwho are you|\bwhat have you done|\bget out", 0.6),
]

HOT_ACTION = [
    (r"slap|strikes?|struck|punch|kick|backhand|riding crop|whip|lash", 1.0),
    (r"blood|wound|dagger|knife|blade|sword|pistol|iron\b|red-hot|chains?|shackle", 0.8),
    (r"explo|shatter|smash|crash|burst|slams?|flames?|fire\b|inferno|ablaze", 0.9),
    (r"collaps|faint|drops? to|kneel|falls?\b|stumbl|thrown|hurl|throws?|shove|drag|grab|seize|chok", 0.8),
    (r"gasp|scream|sob|tears? (spill|stream)|trembl", 0.5),
    (r"reveal|unmask|recogni|crest|signet|regalia|kiss", 0.7),
    (r"cut to black|cliffhanger|ominous|sting|swell|drone|thunder", 0.4),
]

CALM_END = r"resolv|fades?\b|peace|gentle|tender|smiles? (softly|warmly)|embrace|walk (together|away)|laugh|warmth|serene|sleep|calm|relief|comfort"
CONTEXT_WORDS = r"\bremember\b|\bagain\b|\bearlier\b|\byesterday\b|\bas i said\b|\blast night\b|\bthe other\b|\bstill\b"
WEAK_OPEN = r"^(\.\.\.|…|and\b|but\b|so\b|then\b|he\b|she\b|they\b|it\b|that\b|this\b|yes\b|no\b|because\b)"
CONFLICT = r"sister|husband|wife|father|mother|bride|fianc|duke|duchess|family|witch|wedding|marr"


def rx(pats, text):
    t = text.lower()
    return max([w for p, w in pats if re.search(p, t)], default=0.0)


def line_heat(b):
    tag = max([HOT_TAGS.get(x.lower(), 0.0) for x in b.tags] + [0.0])
    word = rx(HOT_WORDS, b.text)
    punct = 0.3 if "!" in b.text else 0.0
    q = 0.25 if b.text.rstrip().endswith("?") else 0.0
    short = 0.15 if len(b.text.split()) <= 7 else 0.0
    return min(1.0, 0.45 * tag + 0.45 * word + punct + q + short)


def beat_heat(b):
    if b.kind == "LINE":
        return line_heat(b)
    if b.kind in ("ACTION", "SOUND_FX"):
        return rx(HOT_ACTION, b.text)
    if b.kind == "CAMERA":
        return 0.6 * rx(HOT_ACTION, b.text)
    return 0.0


def inside(beats, a, e):
    return [b for b in beats if b.start >= a - 0.05 and b.end <= e + 0.05]


def hook_part(beats, a, e):
    first = [b for b in beats if a - 0.05 <= b.start < a + 3.0 and b.start < e]
    if not first:
        return 0.0, None
    best = max(first, key=beat_heat)
    lines = [b for b in first if b.kind == "LINE"]
    h = beat_heat(best)
    if lines:
        h = max(h, line_heat(lines[0]) + 0.1)
    else:
        h *= 0.75
    first_line = next((b for b in beats if b.kind == "LINE" and b.start >= a - 0.05), None)
    if first_line and first_line.start - a > 3.0 and h < 0.7:
        h -= 0.2
    return max(0.0, min(1.0, h)), (lines[0] if lines else best)


def cliff_part(beats, all_beats, a, e, ep_len):
    tail = [b for b in beats if b.end > e - 5.0]
    if not tail:
        return 0.0, None
    lines = [b for b in tail if b.kind == "LINE"]
    last_line = lines[-1] if lines else None
    s = 0.0
    if last_line:
        t = last_line.text.strip()
        if t.endswith("?"):
            s += 0.35
        if t.endswith("...") or t.endswith("…") or t.endswith("—") or t.endswith("-"):
            s += 0.25
        s += 0.4 * line_heat(last_line)
    s += 0.35 * max([beat_heat(b) for b in tail if b.kind != "LINE"] + [0.0])
    after = [b for b in all_beats if b.start >= e - 0.05 and b.start < e + 4.0 and b.kind == "LINE"]
    if after and last_line and after[0].who != last_line.who:
        s += 0.15
    if ep_len - e < 1.5:
        s += 0.2
    calm = " ".join(b.text.lower() for b in tail if b.kind != "LINE")
    if re.search(CALM_END, calm) and not re.search(r"cliffhanger|ominous|sting", calm):
        s -= 0.3
    return max(0.0, min(1.0, s)), (last_line or tail[-1])


def standalone_part(beats, a):
    lines = [b for b in beats if b.kind == "LINE"]
    if not lines:
        return 0.0
    s = 0.7
    if re.match(WEAK_OPEN, lines[0].text.strip().lower()):
        s -= 0.3
    who = {b.who for b in lines}
    if len(who) > 4:
        s -= 0.1 * (len(who) - 4)
    txt = " ".join(b.text.lower() for b in lines)
    s -= 0.07 * len(re.findall(CONTEXT_WORDS, txt))
    if re.search(CONFLICT, txt):
        s += 0.2
    if any("V.O." in t or "FLASHBACK" in t.upper() for b in lines for t in b.tags):
        s -= 0.15
    return max(0.0, min(1.0, s))


def density_part(beats, a, e):
    dur = max(1.0, e - a)
    hot = sum(1 for b in beats if beat_heat(b) >= 0.6)
    talk = sum(b.end - b.start for b in beats if b.kind == "LINE")
    gaps = 0.0
    edges = sorted([(b.start, b.end) for b in beats if b.kind in ("LINE", "ACTION")])
    cur = a
    for s0, e0 in edges:
        if s0 - cur > 4.0:
            gaps += s0 - cur - 4.0
        cur = max(cur, e0)
    if e - cur > 4.0:
        gaps += e - cur - 4.0
    v = 0.5 * min(1.0, hot / (dur / 8.0)) + 0.5 * min(1.0, talk / (dur * 0.6)) - 0.03 * gaps
    return max(0.0, min(1.0, v))


def dur_part(d):
    if 30 <= d <= 50:
        return 1.0
    if d < 30:
        return 0.6 + 0.4 * (d - 20) / 10
    return 1.0 - 0.4 * (d - 50) / 10


W = {"hook": 0.33, "cliff": 0.33, "stand": 0.14, "dens": 0.12, "dur": 0.08}


def score_window(ep, a, e):
    bs = inside(ep.beats, a, e)
    h, hb = hook_part(bs, a, e)
    c, cb = cliff_part(bs, ep.beats, a, e, ep.length)
    st = standalone_part(bs, a)
    de = density_part(bs, a, e)
    du = dur_part(e - a)
    total = W["hook"] * h + W["cliff"] * c + W["stand"] * st + W["dens"] * de + W["dur"] * du
    return {
        "score": round(100 * total, 1),
        "hook_s": round(h, 2), "cliff_s": round(c, 2), "stand_s": round(st, 2),
        "dens_s": round(de, 2), "dur_s": round(du, 2),
        "hook_beat": hb, "cliff_beat": cb,
    }


def load_lexicon(path):
    import json
    from pathlib import Path
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    for k, v in d.get("hot_tags", {}).items():
        HOT_TAGS[k.lower()] = float(v)
    for k, v in d.get("hot_words", {}).items():
        HOT_WORDS.append((k, float(v)))
    for k, v in d.get("hot_action", {}).items():
        HOT_ACTION.append((k, float(v)))
