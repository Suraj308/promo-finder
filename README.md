# Promo Finder

Reads a microdrama screenplay pack (one `.txt` per episode, timestamped) and returns ranked 20–60s promo clips: episode, start/end of every segment, the hook, the cliffhanger, a one-line reason and quality flags.

Built for the Kuku AI Generalist assignment (Problem 1). No dependencies beyond Python 3.9+ standard library. All files sit in one folder; run commands from inside it.

## Run it

```bash
git clone https://github.com/Suraj308/promo-finder && cd promo-finder

# 1. Rules only (free, offline, ~5s for 50 episodes)
python3 run.py path/to/episodes_folder --out out/rules

# 2. Rules + LLM judge (Gemini free tier)
# Windows PowerShell: $env:GEMINI_API_KEY="..."   (and use python instead of python3)
python3 run.py path/to/episodes_folder --out out/gemini --provider gemini --scout
#   --scout: the model also reads every full episode and names hook/cliff beats (recommended)
#   optional: --model gemini-2.5-flash-lite

# 3. Any OpenAI-compatible endpoint (OpenAI, Groq, OpenRouter, Ollama, a company gateway)
export OPENAI_API_KEY=... OPENAI_BASE_URL=https://api.groq.com/openai/v1
python3 run.py path/to/episodes_folder --out out/groq --provider openai --model llama-3.3-70b-versatile

# 4. Anthropic
export ANTHROPIC_API_KEY=...
python3 run.py path/to/episodes_folder --out out/claude --provider anthropic --model claude-haiku-4-5

# 5. No API key: file exchange (any chat LLM, copy-paste)
python3 run.py path/to/episodes_folder --out out/manual --provider file
#   paste each out/manual/llm_exchange/<id>.prompt.txt into a chat model,
#   save its JSON reply as <id>.response.json next to it, run the same command again
```

Try it on the made-up sample episode `episode-001-sample.txt` in this folder (the real input pack is confidential and not in this repo):

```bash
python3 run.py . --out out/sample
```

Options: `--lexicon modern_corporate.json` (extra genre words for a non-period show), `--extra N` (judge N more top candidates beyond best-per-episode, default 10), `--batch N` (candidates per LLM call, default 10), `--top N` (keep top N promos).

## Output (in `--out`)

| file | what |
|---|---|
| `promos.csv` / `promos.md` | ranked promos in posting order: segments, hook, cliffhanger, why, judge's issue note, failed checks, downgrade reasons, alternates, rank reason |
| `report.html` | the same list with the transcript of each clip, for an editor to review |
| `promos.json` | everything, including per-signal scores and raw judge output |
| `quality_checks.md` | automatic checks and the human review queue |
| `all_candidates.json` | every candidate the rules scored (before any judge edits) |
| `moments.json` | the scout's hook/cliff picks per episode (with `--scout`) |
| `llm_cache.json` | cached model replies, so reruns are free |

## How it works

```
episodes/*.txt
  -> parse.py       beats: dialogue / action / camera / sound, with timestamps and emotion tags
  -> candidates.py  every 20-60s window that starts and ends on a camera cut and never splits a line;
                    + cold-open montages (a hot 3-10s line stitched before a clip with a weak opening)
  -> score.py       rules: hook (first 3s), cliffhanger (last 5s + what comes after the cut),
                    standalone, density, length  -> score 0-100, keep 6 non-overlapping per episode
  -> shortlist      best clip per episode + next 10 best overall (~60)
  -> scout.py       (--scout) LLM reads each full episode, 5 per call, and names hook and cliff beat ids
                    for up to 2 moments; code cuts a clip from the hook to just after the cliff, snapped
                    to camera cuts (two segments if the moment is longer than 60s); clips already covered
                    by the shortlist are dropped; the rest go to the judge
  -> judge.py       LLM, 10 clips per call: scores hook / cliffhanger / standalone 0-10,
                    may move start/end ONLY to listed cut points (code validates), writes hook,
                    cliffhanger, why, verdict post|maybe|reject, issue
  -> verify.py      "post" only if hook >=7, cliff >=7, standalone >=6 AND the judge's quoted hook line
                    is found in the first 3s AND its quoted cliff line is inside the clip; else "maybe"
  -> report.py      final = 0.35 rules + 0.65 judge; drop rejects; merge the same story (time overlap,
                    shared dialogue, or the same scene continuing across an episode cut) keeping the
                    weaker cut as an alternate; posting order = post before maybe, early episodes
                    boosted, last 30% of the show damped (spoilers), variety penalty, rank_reason per promo
```

Swap models with `--provider` / `--model`. Prompts live in `judge.py` and `scout.py` (`SYSTEM`).
