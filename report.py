import csv
import html
import json
import re

from parse import fmt
from candidates import spans_line, overlap
from score import inside, line_heat
from judge import final_score, num, short
from verify import same_story


def heur_text(c):
    tags = []
    if c["hook_s"] >= 0.8:
        tags.append("loud opening line/action in the first 3s")
    elif c["hook_s"] < 0.5:
        tags.append("weak opening")
    if c["cliff_s"] >= 0.8:
        tags.append("cuts on an unanswered line")
    if c["stand_s"] >= 0.8:
        tags.append("conflict readable without context")
    if c.get("montage"):
        tags.append("cold-open hook stitched before the main scene")
    return "; ".join(tags) or "average on all signals"


def fill(c):
    j = c.get("llm") or {}
    c["final"] = final_score(c)
    c["hook"] = j.get("hook") or (c["hook_beat"].text if c.get("hook_beat") else "")
    c["cliffhanger"] = j.get("cliffhanger") or (c["cliff_beat"].text if c.get("cliff_beat") else "")
    c["why"] = j.get("why") or heur_text(c)
    c["verdict"] = j.get("verdict") or "unjudged"
    c["issue"] = j.get("issue") or ""
    c["judged_by"] = "llm+rules" if j else "rules"
    return c


def dedupe(cs, byn):
    out = []
    for c in sorted(cs, key=lambda x: (TIER.get(x["verdict"], 1), -x["final"])):
        clash = None
        for o in out:
            if max(overlap(a, b) for a in c["segments"] for b in o["segments"]) >= 0.35:
                clash = (o, "overlaps in time")
                break
            why = same_story(c, o, byn)
            if why:
                clash = (o, why)
                break
        if clash:
            o, why = clash
            o.setdefault("alternates", []).append(f"{c['id']} {seg_str(c)} ({why})")
        else:
            out.append(c)
    return out


TIER = {"post": 0, "unjudged": 0, "maybe": 1, "reject": 2}


def funnel(ep, last):
    if ep <= 3:
        return 1.10
    if ep <= 10:
        return 1.05
    if ep > 0.7 * last:
        return 0.85
    return 1.0


def post_order(cs):
    left = [c for c in cs if c["verdict"] != "reject"]
    last = max((s["episode"] for c in cs for s in c["segments"]), default=1)
    picked = []
    while left:
        def pri(c):
            ep = c["segments"][-1]["episode"]
            near = sum(1 for p in picked if abs(p["segments"][-1]["episode"] - ep) <= 2)
            sp = 0.85 if (c.get("scout") or {}).get("spoiler") else 1.0
            return c["final"] * funnel(c["segments"][0]["episode"], last) * (0.9 ** near) * sp
        best = min(left, key=lambda c: (TIER.get(c["verdict"], 1), -pri(c)))
        best["post_priority"] = round(pri(best), 1)
        ep0 = best["segments"][0]["episode"]
        why = [best["verdict"] if best["verdict"] != "unjudged" else "rules only"]
        if best["verdict"] == "post":
            why.append("passes hook/cliff/standalone gates")
        if best.get("downgraded"):
            why.append("downgraded: " + "; ".join(best["downgraded"]))
        f = funnel(ep0, last)
        if f > 1:
            why.append(f"early episode, funnel boost x{f}")
        elif f < 1:
            why.append(f"late episode (spoiler risk) x{f}")
        if (best.get("scout") or {}).get("spoiler"):
            why.append("scout flagged spoiler x0.85")
        n = sum(1 for p in picked if abs(p["segments"][-1]["episode"] - best["segments"][-1]["episode"]) <= 2)
        if n:
            why.append(f"{n} nearby promo(s) already ranked, variety penalty")
        best["rank_reason"] = "; ".join(why)
        picked.append(best)
        left.remove(best)
    for k, c in enumerate(picked, 1):
        c["post_rank"] = k
    return picked


def checks(c, byn):
    res = {}
    res["duration_20_60"] = 20 <= c["duration"] <= 60
    res["no_mid_line_cut"] = all(not spans_line(byn[s["episode"]], s["start"]) and not spans_line(byn[s["episode"]], s["end"]) for s in c["segments"])
    s0 = c["segments"][0]
    b0 = inside(byn[s0["episode"]].beats, s0["start"], s0["end"])
    res["line_or_action_in_first_3s"] = any(b.kind in ("LINE", "ACTION", "SOUND_FX") and b.start < s0["start"] + 3 for b in b0)
    sl = c["segments"][-1]
    bl = [b for b in inside(byn[sl["episode"]].beats, sl["start"], sl["end"]) if b.kind == "LINE"]
    last = bl[-1].text.strip() if bl else ""
    res["ends_unresolved_line"] = bool(last) and (last.endswith("?") or last.endswith("...") or last.endswith("…") or line_heat(bl[-1]) >= 0.6)
    res["segments_in_story_order"] = all((a["episode"], a["start"]) < (b["episode"], b["start"]) for a, b in zip(c["segments"], c["segments"][1:]))
    j = c.get("llm") or {}
    hs, ls = c["score"], None
    if j and None not in (num(j.get("hook_score")), num(j.get("cliff_score")), num(j.get("standalone_score"))):
        ls = 10 * (0.4 * num(j["hook_score"]) + 0.4 * num(j["cliff_score"]) + 0.2 * num(j["standalone_score"]))
    res["rules_and_llm_agree"] = None if ls is None else abs(hs - ls) <= 25
    v = c.get("verify") or {}
    res["hook_quote_in_first_3s"] = v.get("hook_in_first_3s")
    res["cliff_quote_at_cut"] = v.get("cliff_at_cut")
    res["passes_hook_cliff_standalone_gates"] = v.get("passes_gates")
    c["checks"] = res
    c["needs_human"] = [k for k, v in res.items() if v is False]
    return c


def public(c):
    keep = ["post_rank", "id", "final", "verdict", "duration", "segments", "hook", "cliffhanger", "why", "issue",
            "score", "hook_s", "cliff_s", "stand_s", "dens_s", "montage", "trimmed_from", "llm", "judged_by",
            "checks", "needs_human", "verify", "downgraded", "alternates", "rank_reason", "scout"]
    return {k: c[k] for k in keep if k in c}


def seg_str(c):
    return " + ".join(f"Ep{s['episode']} {s['start_ts']}-{s['end_ts']}" for s in c["segments"])


def transcript(c, byn):
    out = []
    for k, s in enumerate(c["segments"], 1):
        ep = byn[s["episode"]]
        out.append(f"-- segment {k}: Ep{ep.num} {s['start_ts']}-{s['end_ts']}")
        for b in inside(ep.beats, s["start"], s["end"]):
            if b.kind != "CAMERA":
                out.append(f"{fmt(b.start)} {short(b)}")
    return out


def write_all(out, show, ranked, rejected, byn, meta):
    out.mkdir(parents=True, exist_ok=True)
    (out / "promos.json").write_text(json.dumps({"show": show, "meta": meta,
                                                  "promos": [public(c) for c in ranked],
                                                  "rejected": [public(c) for c in rejected]}, indent=1), encoding="utf-8")
    with open(out / "promos.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["post_rank", "id", "final_score", "verdict", "duration_s", "segments", "hook", "cliffhanger", "why", "issue", "needs_human", "downgraded", "alternates", "rank_reason"])
        for c in ranked:
            w.writerow([c["post_rank"], c["id"], c["final"], c["verdict"], c["duration"], seg_str(c), c["hook"], c["cliffhanger"], c["why"], c["issue"], ";".join(c["needs_human"]), "; ".join(c.get("downgraded", [])), "; ".join(c.get("alternates", [])), c.get("rank_reason", "")])
    md = [f"# Promos for: {show}", "", f"Run: {meta['provider']} {meta.get('model') or ''} | episodes: {meta['episodes']} | candidates scored: {meta['candidates']} | judged: {meta['judged']}", ""]
    for c in ranked:
        md += [f"## #{c['post_rank']} {c['id']} | score {c['final']} | {c['verdict']} | {c['duration']}s",
               f"- Segments: {seg_str(c)}", f"- Hook: {c['hook']}", f"- Cliffhanger: {c['cliffhanger']}",
               f"- Why: {c['why']}"]
        if c["issue"]:
            md.append(f"- Issue: {c['issue']}")
        if c["needs_human"]:
            md.append(f"- Flags: {', '.join(c['needs_human'])}")
        if c.get("downgraded"):
            md.append(f"- Downgraded to maybe: {'; '.join(c['downgraded'])}")
        if c.get("alternates"):
            md.append(f"- Alternates (same story): {'; '.join(c['alternates'])}")
        md.append(f"- Rank reason: {c.get('rank_reason', '')}")
        md.append("")
    if rejected:
        md += ["# Rejected by judge", ""]
        for c in rejected:
            md.append(f"- {c['id']} {seg_str(c)}: {c['issue'] or c['why']}")
    (out / "promos.md").write_text("\n".join(md), encoding="utf-8")
    write_checks(out, ranked, rejected)
    write_html(out, show, ranked, rejected, byn, meta)


def write_checks(out, ranked, rejected):
    allc = ranked + rejected
    keys = list(allc[0]["checks"].keys()) if allc else []
    lines = ["# Automatic quality checks", "", "| check | pass | fail | n/a |", "|---|---|---|---|"]
    for k in keys:
        v = [c["checks"][k] for c in ranked]
        lines.append(f"| {k} | {v.count(True)} | {v.count(False)} | {v.count(None)} |")
    lines += ["", f"Promos kept: {len(ranked)}; rejected by judge: {len(rejected)}", "",
              "## Human review queue (any failed check)", ""]
    for c in ranked:
        if c["needs_human"]:
            lines.append(f"- #{c['post_rank']} {c['id']} {seg_str(c)}: {', '.join(c['needs_human'])}")
    (out / "quality_checks.md").write_text("\n".join(lines), encoding="utf-8")


def write_html(out, show, ranked, rejected, byn, meta):
    e = html.escape
    cards = []
    for c in ranked:
        flags = "".join(f'<span class="flag">{e(x)}</span>' for x in c["needs_human"])
        tx = "\n".join(e(x) for x in transcript(c, byn))
        cards.append(f"""<article>
<header><b>#{c['post_rank']}</b> <span class="id">{c['id']}</span> <span class="v {e(c['verdict'])}">{e(c['verdict'])}</span>
<span class="sc">{c['final']}</span> <span class="du">{c['duration']}s</span></header>
<div class="segs">{e(seg_str(c))}</div>
<p><b>Hook:</b> {e(str(c['hook']))}</p><p><b>Cliffhanger:</b> {e(str(c['cliffhanger']))}</p>
<p><b>Why:</b> {e(str(c['why']))}</p>{'<p class="iss"><b>Issue:</b> ' + e(c['issue']) + '</p>' if c['issue'] else ''}
<div>{flags}</div><details><summary>Transcript</summary><pre>{tx}</pre></details></article>""")
    rej = "".join(f"<li>{c['id']} {e(seg_str(c))}: {e(c['issue'] or c['why'])}</li>" for c in rejected)
    page = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Promo picks</title><style>
:root{{--bg:#faf9f7;--fg:#1d1d1f;--mu:#6b6b70;--card:#fff;--line:#e4e2dd;--acc:#b4452f}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151517;--fg:#ececee;--mu:#9a9aa0;--card:#1f1f22;--line:#333;--acc:#e07a5f}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif}}
main{{max-width:880px;margin:0 auto;padding:24px 16px}} h1{{font-size:22px;margin:0 0 4px}} .meta{{color:var(--mu);margin-bottom:20px}}
article{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:12px 0}}
header{{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap}} .id{{color:var(--mu)}} .sc{{margin-left:auto;font-weight:600;color:var(--acc)}}
.du{{color:var(--mu)}} .segs{{font-family:ui-monospace,monospace;font-size:13px;margin:4px 0 8px}} p{{margin:4px 0}}
.v{{font-size:12px;padding:1px 8px;border-radius:99px;border:1px solid var(--line)}} .flag{{display:inline-block;font-size:12px;color:var(--acc);margin-right:8px}}
pre{{white-space:pre-wrap;font-size:12.5px;color:var(--mu)}} .iss{{color:var(--mu)}}
</style></head><body><main><h1>{e(show)}</h1>
<div class="meta">Ranked in posting order. Judge: {e(meta['provider'])} {e(str(meta.get('model') or ''))} · {meta['episodes']} episodes · {meta['candidates']} candidates scored · {meta['judged']} judged</div>
{''.join(cards)}<h2>Rejected</h2><ul>{rej}</ul></main></body></html>"""
    (out / "report.html").write_text(page, encoding="utf-8")
