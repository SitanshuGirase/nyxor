#!/usr/bin/env python3
"""
NYXOR v2 - provider catalog + API-key detection.

Every service NYXOR can use is one row here: LLMs (OpenAI-compatible, plus a native Anthropic adapter),
web-search tools (Tavily, Brave, Serper, Exa, SerpAPI, Jina, Firecrawl), video back-ends (Colab, Replicate, fal)
and text-to-speech (ElevenLabs). detect_key() recognises a pasted key from its shape; when the shape is
ambiguous it returns candidates and NEVER probes other providers with your key (that would leak it).
"""
import re
from urllib.parse import parse_qs, urlparse


def E(id, name, kind="llm", protocol="openai", base="", model="", prefer="", url="", local=0, models=()):
    return dict(id=id, name=name, kind=kind, protocol=protocol, base_url=base, model=model, prefer=prefer,
                key_url=url, is_local=local, static_models=list(models))


CATALOG = [
    # ---- local models (no key)
    E("ollama", "Ollama (local)", base="http://localhost:11434/v1", model="qwen3:8b", prefer="qwen", url="https://ollama.com", local=1),
    E("lmstudio", "LM Studio (local)", base="http://localhost:1234/v1", prefer="qwen", url="https://lmstudio.ai", local=1),
    E("llamacpp", "llama.cpp (local)", base="http://localhost:8080/v1", prefer="qwen", url="https://github.com/ggml-org/llama.cpp", local=1),
    # ---- cloud LLMs
    E("openai", "OpenAI", base="https://api.openai.com/v1", prefer=r"gpt-.*mini", url="https://platform.openai.com/api-keys"),
    E("anthropic", "Anthropic Claude", protocol="anthropic", base="https://api.anthropic.com/v1", prefer=r"haiku|sonnet", url="https://console.anthropic.com/settings/keys"),
    E("xai", "xAI Grok", base="https://api.x.ai/v1", model="grok-4.7", prefer="grok", url="https://console.x.ai"),
    E("groq", "Groq", base="https://api.groq.com/openai/v1", model="llama-3.3-70b-versatile", prefer=r"llama-3\.3", url="https://console.groq.com/keys"),
    E("gemini", "Google Gemini", base="https://generativelanguage.googleapis.com/v1beta/openai", model="gemini-2.5-flash", prefer="flash", url="https://aistudio.google.com/apikey"),
    E("openrouter", "OpenRouter", base="https://openrouter.ai/api/v1", prefer=":free", url="https://openrouter.ai/keys"),
    E("nvidia", "NVIDIA NIM", base="https://integrate.api.nvidia.com/v1", prefer=r"llama-3\.[13]-(70|8)b|nemotron", url="https://build.nvidia.com/settings/api-keys"),
    E("mistral", "Mistral", base="https://api.mistral.ai/v1", model="mistral-small-latest", prefer="small", url="https://console.mistral.ai/api-keys"),
    E("deepseek", "DeepSeek", base="https://api.deepseek.com/v1", model="deepseek-chat", prefer="chat", url="https://platform.deepseek.com/api_keys", models=["deepseek-chat", "deepseek-reasoner"]),
    E("together", "Together AI", base="https://api.together.xyz/v1", prefer=r"llama.*turbo", url="https://api.together.ai/settings/api-keys"),
    E("fireworks", "Fireworks AI", base="https://api.fireworks.ai/inference/v1", prefer="llama", url="https://fireworks.ai/account/api-keys"),
    E("cerebras", "Cerebras", base="https://api.cerebras.ai/v1", prefer="llama", url="https://cloud.cerebras.ai"),
    E("sambanova", "SambaNova", base="https://api.sambanova.ai/v1", prefer="llama", url="https://cloud.sambanova.ai/apis"),
    E("cohere", "Cohere", base="https://api.cohere.ai/compatibility/v1", prefer="command", url="https://dashboard.cohere.com/api-keys"),
    E("perplexity", "Perplexity", base="https://api.perplexity.ai", model="sonar", url="https://www.perplexity.ai/settings/api",
      models=["sonar", "sonar-pro", "sonar-reasoning", "sonar-reasoning-pro"]),
    E("huggingface", "Hugging Face", base="https://router.huggingface.co/v1", prefer="llama|qwen", url="https://huggingface.co/settings/tokens"),
    E("github", "GitHub Models", base="https://models.github.ai/inference", model="openai/gpt-4.1-mini", prefer="mini", url="https://github.com/settings/personal-access-tokens"),
    # ---- web search tools (give models live web results)
    E("tavily", "Tavily Search", "search", "tavily", "https://api.tavily.com", url="https://app.tavily.com"),
    E("brave", "Brave Search", "search", "brave", "https://api.search.brave.com", url="https://brave.com/search/api/"),
    E("serper", "Serper (Google)", "search", "serper", "https://google.serper.dev", url="https://serper.dev"),
    E("exa", "Exa Search", "search", "exa", "https://api.exa.ai", url="https://dashboard.exa.ai/api-keys"),
    E("serpapi", "SerpAPI", "search", "serpapi", "https://serpapi.com", url="https://serpapi.com/manage-api-key"),
    E("jina", "Jina Search", "search", "jina", "https://s.jina.ai", url="https://jina.ai"),
    E("firecrawl", "Firecrawl Search", "search", "firecrawl", "https://api.firecrawl.dev", url="https://www.firecrawl.dev/app/api-keys"),
    # ---- video back-ends
    E("colab", "Google Colab GPU (free)", "video", "colab", "", url="https://colab.research.google.com"),
    E("replicate", "Replicate", "video", "replicate", "https://api.replicate.com/v1", url="https://replicate.com/account/api-tokens"),
    E("fal", "fal.ai", "video", "fal", "https://queue.fal.run", model="fal-ai/ltx-video", url="https://fal.ai/dashboard/keys"),
    # ---- voice
    E("elevenlabs", "ElevenLabs (voice)", "tts", "elevenlabs", "https://api.elevenlabs.io/v1", url="https://elevenlabs.io/app/settings/api-keys"),
]
BY_ID = {e["id"]: e for e in CATALOG}
KINDS = ("llm", "search", "video", "tts")
PROTOCOLS = ("openai", "anthropic", "tavily", "brave", "serper", "exa", "serpapi", "jina", "firecrawl", "colab", "replicate", "fal", "elevenlabs")

# (regex, [(provider id, confidence)]) - evaluated top to bottom, results merged and ranked
CERTAIN, LIKELY, POSSIBLE = "certain", "likely", "possible"
RULES = [
    (r"^sk-ant-", [("anthropic", CERTAIN)]),
    (r"^sk-or-", [("openrouter", CERTAIN)]),
    (r"^xai-", [("xai", CERTAIN)]),
    (r"^gsk_", [("groq", CERTAIN)]),
    (r"^AIza[0-9A-Za-z_\-]{30,}$", [("gemini", CERTAIN)]),
    (r"^nvapi-", [("nvidia", CERTAIN)]),
    (r"^csk-", [("cerebras", CERTAIN)]),
    (r"^pplx-", [("perplexity", CERTAIN)]),
    (r"^hf_", [("huggingface", CERTAIN)]),
    (r"^(ghp_|gho_|ghu_|github_pat_)", [("github", CERTAIN)]),
    (r"^fw_", [("fireworks", CERTAIN)]),
    (r"^tgp_v1_", [("together", CERTAIN)]),
    (r"^tvly-", [("tavily", CERTAIN)]),
    (r"^BSA", [("brave", CERTAIN)]),
    (r"^fc-", [("firecrawl", CERTAIN)]),
    (r"^jina_", [("jina", CERTAIN)]),
    (r"^r8_", [("replicate", CERTAIN)]),
    (r"^sk_[0-9a-fA-F]{32,}$", [("elevenlabs", CERTAIN)]),
    (r"^[0-9a-f\-]{36}:[0-9a-f]{32}$", [("fal", CERTAIN)]),
    (r"^sk-(proj|svcacct|admin)-", [("openai", CERTAIN)]),
    (r"^sk-[0-9a-f]{32}$", [("deepseek", LIKELY), ("openai", POSSIBLE)]),
    (r"^sk-", [("openai", LIKELY), ("deepseek", POSSIBLE)]),
    (r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", [("exa", LIKELY), ("sambanova", POSSIBLE)]),
    (r"^[0-9a-f]{64}$", [("serpapi", LIKELY), ("together", POSSIBLE)]),
    (r"^[0-9a-f]{40}$", [("serper", LIKELY), ("cohere", POSSIBLE)]),
    (r"^[A-Za-z0-9]{40}$", [("cohere", LIKELY)]),
    (r"^[A-Za-z0-9]{32}$", [("mistral", LIKELY)]),
]
RANK = {CERTAIN: 0, LIKELY: 1, POSSIBLE: 2}


def parse_connect(text):
    """A Colab/ngrok/cloudflared tunnel URL, optionally with ?token=...  ->  (base_url, token) or None."""
    t = (text or "").strip()
    try:
        u = urlparse(t)
    except ValueError:
        return None
    if u.scheme in ("http", "https") and re.search(r"(trycloudflare\.com|ngrok(-free)?\.(app|io|dev)|loca\.lt|lhr\.life)$", u.hostname or ""):
        token = (parse_qs(u.query).get("token") or [""])[0]
        return "%s://%s" % (u.scheme, u.netloc), token
    return None


def detect_key(text):
    """Candidates for a pasted key, best first: [{id, name, kind, confidence}]. Pure function, no network."""
    t = (text or "").strip().strip("\"'")
    found = {}
    if parse_connect(t):
        found["colab"] = CERTAIN
    for rx, cands in RULES:
        if re.match(rx, t):
            for pid, conf in cands:
                if pid not in found or RANK[conf] < RANK[found[pid]]:
                    found[pid] = conf
    out = [{"id": pid, "name": BY_ID[pid]["name"], "kind": BY_ID[pid]["kind"], "confidence": c} for pid, c in found.items()]
    out.sort(key=lambda c: RANK[c["confidence"]])
    return [c for c in out if c["confidence"] == CERTAIN] if out and out[0]["confidence"] == CERTAIN else out


def auto_pick(cands):
    """Return the provider id to use without asking, or None if the user must choose."""
    if not cands:
        return None
    if cands[0]["confidence"] == CERTAIN:
        return cands[0]["id"]
    if len(cands) == 1:
        return cands[0]["id"]
    return None


def pick_model(provider_id, models, current=""):
    """Choose a sensible default from detected models using the catalog's preference regex."""
    if current and current in models:
        return current
    pref = BY_ID.get(provider_id, {}).get("prefer") or ""
    if pref and models:
        hit = [m for m in models if re.search(pref, m, re.I)]
        if hit:
            return sorted(hit)[0]
    return models[0] if models else current
