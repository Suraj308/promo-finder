import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path


class Pending(Exception):
    pass


def post(url, body, headers, tries=4):
    data = json.dumps(body).encode()
    for k in range(tries):
        try:
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **headers})
            with urllib.request.urlopen(req, timeout=180) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and k < tries - 1:
                time.sleep(10 * (k + 1))
                continue
            raise RuntimeError(f"HTTP {e.code}: {e.read().decode(errors='replace')[:400]}")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if k < tries - 1:
                time.sleep(5 * (k + 1))
                continue
            raise RuntimeError(f"network error: {e}")


SAFETY = [{"category": c, "threshold": "BLOCK_ONLY_HIGH"} for c in
          ("HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH",
           "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT")]


class Gemini:
    def __init__(self, model=None):
        self.model = model or os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        self.key = os.environ["GEMINI_API_KEY"]

    def __call__(self, system, user):
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.key}"
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
            "safetySettings": SAFETY,
        }
        r = post(url, body, {})
        cands = r.get("candidates") or []
        parts = (cands[0].get("content") or {}).get("parts") if cands else None
        if not parts:
            why = (cands[0].get("finishReason") if cands else None) or r.get("promptFeedback", {}).get("blockReason")
            raise RuntimeError(f"Gemini returned no text ({why})")
        return "".join(p.get("text", "") for p in parts)


class OpenAICompat:
    def __init__(self, model=None):
        self.base = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        self.key = os.getenv("OPENAI_API_KEY", "none")

    def __call__(self, system, user):
        body = {"model": self.model, "temperature": 0.2,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        r = post(self.base + "/chat/completions", body, {"Authorization": f"Bearer {self.key}"})
        return r["choices"][0]["message"]["content"]


class Anthropic:
    def __init__(self, model=None):
        self.model = model or os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5")
        self.key = os.environ["ANTHROPIC_API_KEY"]

    def __call__(self, system, user):
        body = {"model": self.model, "max_tokens": 8000, "temperature": 0.2, "system": system,
                "messages": [{"role": "user", "content": user}]}
        r = post("https://api.anthropic.com/v1/messages", body,
                 {"x-api-key": self.key, "anthropic-version": "2023-06-01"})
        return "".join(b.get("text", "") for b in r["content"])


class FileExchange:
    def __init__(self, folder):
        self.dir = Path(folder)
        self.dir.mkdir(parents=True, exist_ok=True)

    def __call__(self, system, user):
        h = hashlib.sha1((system + user).encode()).hexdigest()[:10]
        p = self.dir / f"{h}.prompt.txt"
        r = self.dir / f"{h}.response.json"
        if r.exists():
            return r.read_text(encoding="utf-8")
        p.write_text("=== SYSTEM ===\n" + system + "\n\n=== USER ===\n" + user, encoding="utf-8")
        raise Pending(str(p))


PROVIDERS = {"gemini": Gemini, "openai": OpenAICompat, "anthropic": Anthropic}


class Cached:
    def __init__(self, inner, path, tag):
        self.inner = inner
        self.path = Path(path)
        self.tag = tag
        self.db = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}

    def __call__(self, system, user):
        k = hashlib.sha1((self.tag + system + user).encode()).hexdigest()
        if k not in self.db:
            self.db[k] = self.inner(system, user)
            self.path.write_text(json.dumps(self.db, indent=1), encoding="utf-8")
        return self.db[k]


def make(name, model=None, outdir="out"):
    if name in (None, "", "none"):
        return None
    if name == "file":
        return FileExchange(Path(outdir) / "llm_exchange")
    m = PROVIDERS[name](model)
    return Cached(m, Path(outdir) / "llm_cache.json", f"{name}:{getattr(m, 'model', '')}")
