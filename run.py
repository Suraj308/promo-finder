import argparse
import json
import sys
import time
from pathlib import Path

from parse import load_show
from candidates import generate, overlap
from judge import judge
from llm import make
from report import fill, dedupe, post_order, checks, write_all
from verify import verify
from score import load_lexicon
from scout import run_scout, scout_candidates


def shortlist(cands, extra):
    best = {}
    for c in cands:
        ep = c["segments"][0]["episode"]
        if ep not in best and not c.get("montage"):
            best[ep] = c
    chosen = list(best.values())
    for c in cands:
        if len(chosen) >= len(best) + extra:
            break
        if c in chosen:
            continue
        if all(max(overlap(a, b) for a in c["segments"] for b in o["segments"]) < 0.35 for o in chosen):
            chosen.append(c)
    return sorted(chosen, key=lambda c: (c["segments"][0]["episode"], c["segments"][0]["start"]))


def main():
    p = argparse.ArgumentParser(description="Find promo clips in a microdrama screenplay pack")
    p.add_argument("folder", help="folder with one .txt screenplay per episode")
    p.add_argument("--out", default="out")
    p.add_argument("--provider", default="none", choices=["none", "gemini", "openai", "anthropic", "file"])
    p.add_argument("--model", default=None)
    p.add_argument("--extra", type=int, default=10, help="extra top candidates judged beyond best-per-episode")
    p.add_argument("--batch", type=int, default=10)
    p.add_argument("--scout", action="store_true", help="LLM reads whole episodes and names hook/cliff beats; code cuts clips around them")
    p.add_argument("--lexicon", default=None, help="optional JSON with extra hot_words / hot_tags for a genre")
    p.add_argument("--top", type=int, default=0, help="keep only top N promos in output (0 = all)")
    a = p.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    t0 = time.time()
    if not Path(a.folder).is_dir():
        sys.exit(f"folder not found: {a.folder}")
    eps = load_show(a.folder)
    if not eps:
        sys.exit("no episodes parsed: expected .txt screenplays with 'Start: (mm:ss.xx) | End: (mm:ss.xx)' lines")
    show = Path(a.folder).resolve().name
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    byn = {e.num: e for e in eps}
    print(f"parsed {len(eps)} episodes, {sum(len(e.beats) for e in eps)} beats")

    if a.lexicon:
        load_lexicon(a.lexicon)
    cands = generate(eps)
    print(f"scored candidates: {len(cands)}")
    (out / "all_candidates.json").write_text(json.dumps(
        [{k: v for k, v in c.items() if k not in ("hook_beat", "cliff_beat")} for c in cands], indent=1), encoding="utf-8")
    sl = shortlist(cands, a.extra)

    try:
        llm = make(a.provider, a.model, a.out)
    except KeyError as e:
        sys.exit(f"missing environment variable {e} for provider '{a.provider}' (see README)")
    pending = []
    if llm:
        print(f"judging {len(sl)} candidates with {a.provider}")
        pending = judge(sl, eps, show, llm, a.batch)
        if pending:
            print(f"\n{len(pending)} prompt file(s) waiting for answers in {out/'llm_exchange'}.")
            print("Paste each .prompt.txt into any chat LLM, save its JSON reply next to it as <same-id>.response.json, then rerun.")

    sc, moments = [], []
    if llm and a.scout:
        print(f"scouting {len(eps)} episodes with {a.provider}")
        moments, p2 = run_scout(eps, show, llm)
        pending += p2
        (out / "moments.json").write_text(json.dumps(moments, indent=1), encoding="utf-8")
        sc = scout_candidates(eps, moments, sl)
        print(f"scout moments: {len(moments)}, new clips after removing ones already shortlisted: {len(sc)}")
        first = [c for c in sc if not c.get("strict_dup")]
        extra = [c for c in sc if c.get("strict_dup")]
        if first:
            pending += judge(first, eps, show, llm, a.batch)
        if extra:
            pending += judge(extra, eps, show, llm, a.batch)
        if pending:
            print(f"\n{len(pending)} prompt file(s) waiting for answers in {out/'llm_exchange'}.")

    pool = [fill(c) for c in sl + sc]
    pool = [checks(verify(c, byn), byn) for c in pool]
    rejected = [c for c in pool if c["verdict"] == "reject"]
    kept = dedupe([c for c in pool if c["verdict"] != "reject"], byn)
    ranked = post_order(kept)
    if a.top:
        ranked = ranked[: a.top]

    meta = {"provider": a.provider, "model": getattr(getattr(llm, "inner", llm), "model", None) if llm else None,
            "episodes": len(eps), "candidates": len(cands), "judged": sum(1 for c in sl + sc if c.get("llm")), "scout_moments": len(moments), "scout_clips": len(sc),
            "pending_prompts": len(pending), "seconds": round(time.time() - t0, 1)}
    write_all(out, show, ranked, rejected, byn, meta)
    print(f"wrote {len(ranked)} promos to {out}/ in {meta['seconds']}s")
    for c in ranked[:10]:
        segs = " + ".join(f"Ep{s['episode']} {s['start_ts']}-{s['end_ts']}" for s in c["segments"])
        print(f"  #{c['post_rank']:>2} {c['final']:>5}  {segs}  | {str(c['hook'])[:60]}")


if __name__ == "__main__":
    main()
