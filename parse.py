import re
from dataclasses import dataclass, field, asdict
from pathlib import Path

TS = re.compile(r"Start:\s*\((\d+):(\d+(?:\.\d+)?)\)\s*\|\s*End:\s*\((\d+):(\d+(?:\.\d+)?)\)")
HEAD = re.compile(r"^\*\*(.+?):\*\*\s*$")


@dataclass
class Beat:
    kind: str
    who: str
    tags: list
    start: float
    end: float
    text: str
    i: int = 0

    def d(self):
        return asdict(self)


@dataclass
class Episode:
    num: int
    title: str
    logline: str
    notes: str
    file: str
    beats: list = field(default_factory=list)
    scenes: list = field(default_factory=list)

    @property
    def length(self):
        return max((b.end for b in self.beats), default=0.0)


def secs(m, s):
    return int(m) * 60 + float(s)


def head_info(h):
    a = h.strip()
    if a in ("CAMERA", "ACTION", "SOUND FX"):
        return a.replace(" ", "_"), "", []
    tags = re.findall(r"\(([^)]*)\)", a)
    who = re.sub(r"\(.*", "", a).strip()
    flat = []
    for t in tags:
        flat += [x.strip() for x in t.split(",") if x.strip()]
    return "LINE", who, flat


def parse_file(path):
    raw = Path(path).read_text(encoding="utf-8", errors="replace")
    lines = raw.splitlines()
    meta = {}
    for ln in lines[:6]:
        if ":" in ln:
            k, v = ln.split(":", 1)
            meta[k.strip().upper()] = v.strip()
    title = meta.get("EPISODE NUMBER", Path(path).stem)
    m = re.search(r"(\d+)", title)
    num = int(m.group(1)) if m else 0
    ep = Episode(num, title, meta.get("EPISODE LOGLINE", ""), meta.get("CONTINUITY NOTES", ""), Path(path).name)

    i = 0
    scene = None
    while i < len(lines):
        ln = lines[i].strip()
        if ln.startswith("Scene heading:"):
            scene = {"heading": ln.split(":", 1)[1].strip(), "start": None, "end": None}
            ep.scenes.append(scene)
        if scene is not None and ln.startswith("Scene start:"):
            sm = re.search(r"(\d+):(\d+(?:\.\d+)?)", ln)
            scene["start"] = secs(sm.group(1), sm.group(2)) if sm else None
        if scene is not None and ln.startswith("Scene end:"):
            sm = re.search(r"(\d+):(\d+(?:\.\d+)?)", ln)
            scene["end"] = secs(sm.group(1), sm.group(2)) if sm else None
        hm = HEAD.match(ln)
        if hm and i + 1 < len(lines):
            tm = TS.search(lines[i + 1])
            if tm:
                kind, who, tags = head_info(hm.group(1))
                j = i + 2
                body = []
                while j < len(lines) and lines[j].strip() and not HEAD.match(lines[j].strip()):
                    body.append(lines[j].strip())
                    j += 1
                txt = " ".join(body).strip().strip('"').strip("“”")
                a, b = secs(tm.group(1), tm.group(2)), secs(tm.group(3), tm.group(4))
                ep.beats.append(Beat(kind, who, tags, a, max(a, b), txt))
                i = j
                continue
        i += 1
    ep.beats.sort(key=lambda b: (b.start, b.end))
    for k, b in enumerate(ep.beats):
        b.i = k
    return ep


def load_show(folder):
    files = sorted(Path(folder).glob("*.txt"))
    eps = [parse_file(f) for f in files]
    eps = [e for e in eps if e.beats]
    eps.sort(key=lambda e: e.num)
    return eps


def fmt(t):
    m = int(t // 60)
    s = t - m * 60
    return f"{m:02d}:{s:05.2f}"
