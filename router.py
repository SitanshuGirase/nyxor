#!/usr/bin/env python3
"""
NYXOR v2.3 - model router (DIRECT mode): transparent rules plus a small score decide which connected
model answers a DIRECT message, and a fallback chain picks the next-best model when one fails before it starts
answering. Standard library only. No network calls happen here: decisions use settings, provider rows and the
records NYXOR keeps itself.

What the router looks at
  task        complexity (light / standard / heavy) from length, code and a few keywords; images attached
  privacy     if the chat holds output produced in AGENT mode (local_only), only local models may answer
  availability  enabled, has a key, not recently rate-limited or unreachable, not in a failure cool-down
  capability  a tier 1-3 GUESSED FROM THE MODEL NAME (mini / 8b -> 1, sonnet / 70b / opus -> 3, else 2). It is a
              heuristic, not a benchmark; the learned record (below) corrects it over time.
  cost        a tier 0-3 guessed the same way (local and :free models are 0). Real prices and caps live in budget.py; a provider at its cap is left out.
  record      per model: runs, successes, failure streak and tokens per second, learned from real answers

Every decision is returned with the reasons, so the UI can show why a model was chosen.
"""
import re
import threading
import time

import budget
import db
import privacy
import providers as P

COOLDOWN = 300            # seconds a model that failed 3 times in a row is skipped
MAX_FALLBACKS = 2         # extra attempts after the first one
PROVIDER_LEVEL = ("network", "auth", "rate", "server", "config", "consent", "budget")   # failures that make the whole provider pointless to retry
_lock = threading.Lock()

SMALL = re.compile(r"mini|nano|lite|haiku|instant|tiny|small|(?<![\d.])(?:0\.5|1\.5|[1-9])b\b", re.I)
BIG = re.compile(r"opus|gpt-5|(?<![a-z0-9])o[134](?:-|:|$)|sonnet|reasoner|(?<![\d.])(?:405|235|120|90|72|70|671)b\b|large|ultra|-pro\b|sonar-pro|"
                 r"grok-[3-9]|maverick|deepseek-r1|command-r-plus", re.I)
VISION = re.compile(r"gpt-4o|gpt-4\.1|gpt-5|(?<![a-z0-9])o[134](?:-|$)|claude|gemini|llava|vision|(?<![a-z])vl\b|-vl|pixtral|gemma-?3|minicpm-v|moondream|grok-4|llama-?4", re.I)
HEAVY_WORDS = re.compile(r"architect|refactor|debug|optimi[sz]|algorithm|implement|\bprove\b|\bproof\b|derive|analy[sz]|compare|trade-?off|step[- ]by[- ]step|"
                         r"\b(design|build|create|write|develop|make) (me )?(a|an|the)\b.{0,40}\b(app|game|program|script|system|compiler|database|api|essay|report|plan|strategy|module|class|function)\b",
                         re.I | re.S)
CODE_HINT = re.compile(r"```|\bdef \w+\(|\bfunction\s+\w+\(|\bclass \w+|#include|import \w+|=>")
IMG_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp")


# ------------------------------------------------------------------ heuristics
def capability(model):
    """1 = small/fast, 2 = general, 3 = large/strong. Guessed from the model name."""
    m = model or ""
    if SMALL.search(m):
        return 1
    return 3 if BIG.search(m) else 2


def cost_tier(p, model):
    """0 free (local or :free), 1 cheap, 2 mid, 3 expensive. Guessed from the name; the prices you enter live in budget.py."""
    m = model or ""
    if P.is_local_provider(p) or m.endswith(":free"):
        return 0
    if SMALL.search(m) or "flash" in m.lower():
        return 1
    return 3 if BIG.search(m) else 2


def sees_images(model):
    return bool(VISION.search(model or ""))


def classify(text, attachments=None):
    """-> {complexity: light|standard|heavy, reasons: [...], images: bool, files: bool}"""
    t = (text or "").strip()
    atts = [a for a in (attachments or []) if isinstance(a, dict)]
    names = [str(a.get("name") or "").lower() for a in atts]
    images = any(n.endswith(IMG_EXT) for n in names)
    files = any(not n.endswith(IMG_EXT) for n in names)
    pts, why = 0, []
    if len(t) > 600:
        pts += 2
        why.append("long message")
    elif len(t) > 250:
        pts += 1
        why.append("medium-length message")
    if CODE_HINT.search(t):
        pts += 1
        why.append("contains code")
    if HEAVY_WORDS.search(t):
        pts += 1
        why.append("asks for building, analysis or design")
    if t.count("?") >= 3:
        pts += 1
        why.append("several questions")
    if files:
        pts += 1
        why.append("attached file")
    if pts >= 3:
        level = "heavy"
    elif pts >= 1 or len(t) > 120:
        level = "standard"
    else:
        level = "light"
    if not why:
        why.append("short, simple message" if level == "light" else "ordinary question")
    return {"complexity": level, "reasons": why, "images": images, "files": files}


# ------------------------------------------------------------------ records
def stats():
    return db.get_setting("router_stats") or {}


def record(ref, ok, tps=0.0, kind=""):
    """Learn from one real attempt. Cancelled requests and configuration problems are not recorded."""
    with _lock:
        st = stats()
        r = st.setdefault(ref, {"runs": 0, "ok": 0, "fail": 0, "tps": 0.0, "streak": 0, "last_fail": 0, "last_kind": ""})
        r["runs"] += 1
        if ok:
            r["ok"] += 1
            r["streak"] = 0
            if tps and tps > 0:
                r["tps"] = round(tps if not r["tps"] else 0.7 * r["tps"] + 0.3 * tps, 1)
        else:
            r["fail"] += 1
            r["streak"] += 1
            r["last_fail"] = int(time.time())
            r["last_kind"] = kind or "error"
        db.set_settings({"router_stats": st})


def reset_stats():
    with _lock:
        db.set_settings({"router_stats": {}})


def _state(pid):
    with P._slock:
        return P._status.get(pid, ("ok", 0))


# ------------------------------------------------------------------ candidates and scoring
def _usable(p):
    return bool(p and p.get("kind", "llm") == "llm" and p["enabled"] and (p["is_local"] or p["api_key"]) and p["model"])


def candidates(selected_ref=""):
    """One candidate per connected LLM provider (its configured model), plus the model the user selected.
    SYSTEMS -> ROUTER can limit the pool. Returns [(ref, provider, model)] and a list of reasons for skipped ones."""
    pool = set(db.get_setting("router_pool") or [])
    out, skipped, seen = [], [], set()
    for row in db.list_providers():
        p = db.get_provider(row["id"])
        if not p or p.get("kind", "llm") != "llm":
            continue
        ref = "%s:%s" % (p["id"], p["model"])
        if not _usable(p):
            continue
        if pool and ref not in pool and ref != selected_ref:
            continue
        out.append((ref, p, p["model"]))
        seen.add(ref)
    if selected_ref and selected_ref not in seen:
        pid, model = P.split_ref(selected_ref)
        p = db.get_provider(pid)
        if p and p.get("kind", "llm") == "llm" and p["enabled"] and (p["is_local"] or p["api_key"]) and model:
            out.append((selected_ref, p, model))
        else:
            skipped.append({"ref": selected_ref, "why": "selected model is disabled or has no key"})
    return out, skipped


def _score(ref, p, model, task, rec):
    local, cap, cost = P.is_local_provider(p), capability(model), cost_tier(p, model)
    level = task["complexity"]
    target = {"light": 1, "standard": 2, "heavy": 3}[level]
    s = 3 - (0.5 * (cap - target) if cap >= target else 1.5 * (target - cap))
    why = ["capability %d/3 for a %s task" % (cap, level)]
    if local:
        s += {"light": 0.6, "standard": 0.2, "heavy": 0.0}[level]
        why.append("local: free and private")
    elif cost == 0:
        why.append("cloud, free tier")
    else:
        s -= cost * {"light": 0.7, "standard": 0.35, "heavy": 0.15}[level]
        why.append("cloud, cost tier %d/3" % cost)
    runs = rec.get("runs", 0)
    if runs:
        s += ((rec.get("ok", 0) + 1) / (runs + 2) - 0.5) * 2
        why.append("record %d/%d ok" % (rec.get("ok", 0), runs))
    if rec.get("tps"):
        s += min(rec["tps"], 60) / 60 * {"light": 0.6, "standard": 0.3, "heavy": 0.15}[level]
        why.append("~%s tok/s" % rec["tps"])
    if rec.get("streak", 0):
        s -= min(2.0, rec["streak"])
        why.append("%d failure(s) in a row" % rec["streak"])
    state, ts = _state(p["id"])
    if state == "rate" and time.time() - ts < 90:
        s -= 2
        why.append("rate-limited a moment ago")
    if task["images"] and not sees_images(model):
        s -= 3
        why.append("may not read images")
    return round(s, 2), why, local, cap, cost


def decide(text, attachments, selected_ref, private=False, mode=None):
    """Rank every usable model for this message. In 'auto' mode the top one is chosen; in 'manual' mode the chosen model
    is the one the user selected and the ranking is only used for fallbacks. Pure decision: nothing is called here."""
    mode = mode or db.get_setting("router_mode") or "manual"
    task = classify(text, attachments)
    task["private"] = bool(private)
    cands, excluded = candidates(selected_ref)
    st, now, ranked = stats(), time.time(), []
    for ref, p, model in cands:
        local = P.is_local_provider(p)
        if not local and not privacy.allowed("cloud_models"):
            excluded.append({"ref": ref, "why": "cloud models are not allowed yet (SYSTEMS \u2192 PRIVACY)"})
            continue
        if private and not local:
            excluded.append({"ref": ref, "why": "this chat holds local-only content, so only local models may answer"})
            continue
        if budget.capped_now(p["id"]):
            excluded.append({"ref": ref, "why": "spend cap reached (SYSTEMS \u2192 BUDGETS)"})
            continue
        rec = st.get(ref, {})
        if rec.get("streak", 0) >= 3 and now - rec.get("last_fail", 0) < COOLDOWN:
            excluded.append({"ref": ref, "why": "failed %d times in a row, cooling down" % rec["streak"]})
            continue
        state, ts = _state(p["id"])
        if state == "down" and now - ts < 60:
            excluded.append({"ref": ref, "why": "unreachable a moment ago"})
            continue
        score, why, loc, cap, cost = _score(ref, p, model, task, rec)
        ranked.append({"ref": ref, "score": score, "local": loc, "cap": cap, "cost": cost, "why": why})
    ranked.sort(key=lambda c: (-c["score"], not c["local"], c["ref"]))
    chosen, note = selected_ref, ""
    if mode == "auto":
        if ranked:
            chosen = ranked[0]["ref"]
        else:
            note = "no usable model passed the filters; using the model you selected"
    pid, _ = P.split_ref(chosen)
    cp = db.get_provider(pid)
    return {"mode": mode, "task": task, "chosen": chosen, "selected": selected_ref, "ranked": ranked, "excluded": excluded,
            "note": note, "origin_local": bool(cp and P.is_local_provider(cp))}


def line(dec):
    """One readable line for the UI: which model and why."""
    top = next((c for c in dec["ranked"] if c["ref"] == dec["chosen"]), None)
    why = top["why"][:3] if top else [dec["note"] or "your selection"]
    flags = []
    if dec["task"]["private"]:
        flags.append("private chat: local only")
    return "%s \u2014 %s task%s; %s" % (dec["chosen"], dec["task"]["complexity"], " (%s)" % ", ".join(flags) if flags else "", ", ".join(why))


def summary(dec):
    """JSON for the browser (no keys, no provider rows)."""
    return {"mode": dec["mode"], "chosen": dec["chosen"], "selected": dec["selected"], "line": line(dec), "note": dec["note"],
            "task": dec["task"], "excluded": dec["excluded"],
            "ranked": [{"ref": c["ref"], "score": c["score"], "local": c["local"], "why": c["why"]} for c in dec["ranked"][:8]]}


def next_fallback(dec, failures, escalate):
    """The next model to try after `failures` ([{ref, kind}]), or None.
    Rules: never a model that already failed; never another model of a provider that is down, rejecting the key, rate-limiting or
    disabled; private chats stay local; a local model falls back to cloud only when `escalate` (it sends the message off this PC)."""
    tried = {f["ref"] for f in failures}
    bad = {P.split_ref(f["ref"])[0] for f in failures if f.get("kind") in PROVIDER_LEVEL}
    for c in dec["ranked"]:
        if c["ref"] in tried or P.split_ref(c["ref"])[0] in bad:
            continue
        if dec["task"]["private"] and not c["local"]:
            continue
        if dec["origin_local"] and not c["local"] and not escalate:
            continue
        return c["ref"]
    return None
