#!/usr/bin/env python3
"""
NYXOR v2.3 - budgets: per-provider spend caps and a real usage meter. Standard library only.

Every model call in NYXOR (DIRECT, COUNCIL, AGENT, research query writing, the TEST button) goes through
providers.stream_chat, which calls gate() before the request and record() afterwards. So nothing can bypass a cap.

What is counted
  tokens   the provider's own numbers when it reports them (OpenAI-compatible servers and Anthropic do, NYXOR asks for them);
           otherwise an ESTIMATE of about 4 characters per token. Estimated calls are counted and shown as such.
  dollars  tokens x the price YOU enter (US dollars per 1 million tokens, input and output separately). NYXOR ships NO price
           list: prices change and cannot be checked offline. A cloud call without a price adds tokens but no dollars, and
           the meter says so. A dollar cap needs a price for that provider; the API refuses to save one without it.
  not counted  voice (TTS), video, web search APIs: they are not billed in tokens.

Caps (per provider): dollars per day / month and tokens per day / month. 0 = no cap. Days and months are LOCAL calendar days.
A request is refused BEFORE it is sent when the spend so far plus the estimated size of its input would reach a cap. The answer
itself cannot be predicted, so one call can overshoot a cap by its output; calls running at the same moment are reserved
for their input only. Caps are a safety net for accidents, not an accounting guarantee: the provider's own bill is the truth.

Action when a cap is reached (SYSTEMS -> BUDGETS)
  block  the provider refuses requests until the next day / month, or until you press OVERRIDE TODAY in SYSTEMS -> BUDGETS
  ask    same, and the chat shows the refusal with an ALLOW TODAY button next to it
In DIRECT mode with fallback on, a capped provider counts as failed before the first token, so the router tries the next-best model
(which has its own caps) and says so. In AUTO routing a capped provider is not a candidate at all.
"""
import threading
import time

import db
import providers as P

KEEP_DAYS = 62
MAX_PRICE = 100000.0            # dollars per million tokens: anything above is a typo
MAX_CAP = 1e9
CAP_KEYS = ("usd_day", "usd_month", "tok_day", "tok_month")
ACTIONS = ("block", "ask")
_lock = threading.RLock()
_inflight = {}                  # provider id -> {"tok": reserved input tokens, "usd": reserved input dollars}


# ------------------------------------------------------------------ time
def today():
    return time.strftime("%Y-%m-%d")


def this_month():
    return time.strftime("%Y-%m")


# ------------------------------------------------------------------ configuration
def config():
    c = db.get_setting("budget") or {}
    return {"action": c.get("action") if c.get("action") in ACTIONS else "block",
            "prices": dict(c.get("prices") or {}), "caps": dict(c.get("caps") or {}), "overrides": dict(c.get("overrides") or {})}


def _num(v, hi, what):
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ValueError("%s must be a number." % what)
    if x != x or x < 0 or x > hi:
        raise ValueError("%s must be between 0 and %g." % (what, hi))
    return x


def _known_provider(pid):
    return bool(db.get_provider(pid))


def validate(patch):
    """Merge a patch ({action?, prices?, caps?}) into the stored configuration and return the new configuration.
    prices: {"provider" or "provider:model": {"in": usd, "out": usd}} - a null / empty entry removes the price.
    caps: {"provider": {usd_day, usd_month, tok_day, tok_month}} - all zero removes the entry.
    Raises ValueError with a plain message. Nothing is stored here."""
    c = config()
    if "action" in patch:
        if patch["action"] not in ACTIONS:
            raise ValueError("action must be block or ask")
        c["action"] = patch["action"]
    for key, pr in (patch.get("prices") or {}).items():
        key = str(key)[:200]
        pid = key.split(":", 1)[0]
        if not _known_provider(pid):
            raise ValueError("Unknown provider '%s'." % pid)
        if not pr or (pr.get("in") in (None, "") and pr.get("out") in (None, "")):
            c["prices"].pop(key, None)
            continue
        c["prices"][key] = {"in": _num(pr.get("in") or 0, MAX_PRICE, "Input price"), "out": _num(pr.get("out") or 0, MAX_PRICE, "Output price")}
    for pid, cap in (patch.get("caps") or {}).items():
        if not _known_provider(pid):
            raise ValueError("Unknown provider '%s'." % pid)
        cap = cap or {}
        clean = {k: _num(cap.get(k) or 0, MAX_CAP, k.replace("_", " ")) for k in CAP_KEYS}
        if any(clean.values()):
            c["caps"][pid] = clean
        else:
            c["caps"].pop(pid, None)
    for pid, cap in c["caps"].items():          # a dollar cap with no price could never trigger: refuse it
        if (cap.get("usd_day") or cap.get("usd_month")):
            pr = c["prices"].get(pid) or {}
            if not ((pr.get("in") or 0) > 0 or (pr.get("out") or 0) > 0):
                p = db.get_provider(pid) or {}
                raise ValueError("%s has a dollar cap but no price. Enter its price per million tokens, or use a token cap." % (p.get("name") or pid))
    c["overrides"] = {k: v for k, v in c["overrides"].items() if v == today() and _known_provider(k)}
    return c


def save(patch):
    c = validate(patch)
    db.set_settings({"budget": c})
    return c


def override_today(pid, on=True):
    """Ignore this provider's caps until local midnight (or switch the override off again)."""
    if not _known_provider(pid):
        raise ValueError("Unknown provider '%s'." % pid)
    c = config()
    if on:
        c["overrides"][pid] = today()
    else:
        c["overrides"].pop(pid, None)
    db.set_settings({"budget": c})
    return c


def price_for(pid, model, cfg=None):
    """(usd per 1M input tokens, usd per 1M output tokens), or None when no price is set. Loopback local servers are free."""
    cfg = cfg or config()
    for key in ("%s:%s" % (pid, model), pid):
        pr = cfg["prices"].get(key)
        if pr:
            return float(pr.get("in") or 0), float(pr.get("out") or 0)
    p = db.get_provider(pid)
    return (0.0, 0.0) if p and P.is_local_provider(p) else None


# ------------------------------------------------------------------ estimates and the ledger
def estimate_text(n_chars):
    return int(n_chars / 4.0 + 0.999) if n_chars > 0 else 0


def estimate_input(messages):
    """About 4 characters per token; an attached image counts as roughly 800 tokens."""
    chars = 0
    for m in messages or []:
        c = m.get("content")
        if isinstance(c, str):
            chars += len(c)
        else:
            for part in c or []:
                chars += len(part.get("text") or "") if part.get("type") == "text" else 3200
        chars += 16
    return estimate_text(chars)


def _ledger():
    u = db.get_setting("usage") or {}
    u.setdefault("days", {})
    return u


def _add(ref, tin, tout, estimated, usd, unpriced):
    with _lock:
        u = _ledger()
        day = u["days"].setdefault(today(), {})
        r = day.setdefault(ref, {"in": 0, "out": 0, "calls": 0, "est": 0, "usd": 0.0, "nopr": 0})
        r["in"] += int(tin)
        r["out"] += int(tout)
        r["calls"] += 1
        r["est"] += 1 if estimated else 0
        r["usd"] = round(r["usd"] + usd, 6)
        r["nopr"] += 1 if unpriced else 0
        for d in sorted(u["days"])[:-KEEP_DAYS]:
            u["days"].pop(d, None)
        db.set_settings({"usage": u})


def reset_usage():
    with _lock:
        db.set_settings({"usage": {"days": {}}})
    return {"days": {}}


def _sum(rows, pid=None):
    t = {"in": 0, "out": 0, "tok": 0, "calls": 0, "est": 0, "usd": 0.0, "nopr": 0}
    for ref, r in rows.items():
        if pid is not None and ref.split(":", 1)[0] != pid:
            continue
        for k in ("in", "out", "calls", "est", "nopr"):
            t[k] += r.get(k, 0)
        t["usd"] += r.get("usd", 0.0)
    t["tok"] = t["in"] + t["out"]
    t["usd"] = round(t["usd"], 6)
    return t


def _rows(prefix):
    """Merged {ref: row} over every ledger day starting with prefix ('2026-11-19' = one day, '2026-11' = a month)."""
    out = {}
    for day, rows in _ledger()["days"].items():
        if not day.startswith(prefix):
            continue
        for ref, r in rows.items():
            m = out.setdefault(ref, {"in": 0, "out": 0, "calls": 0, "est": 0, "usd": 0.0, "nopr": 0})
            for k in m:
                m[k] += r.get(k, 0)
    return out


def used(pid):
    """{'day': totals, 'month': totals} for one provider, reservations of calls in flight included in tok / usd."""
    d, m = _sum(_rows(today()), pid), _sum(_rows(this_month()), pid)
    with _lock:
        fl = _inflight.get(pid) or {"tok": 0, "usd": 0.0}
    for t in (d, m):
        t["tok"] += fl["tok"]
        t["usd"] = round(t["usd"] + fl["usd"], 6)
    return {"day": d, "month": m}


# ------------------------------------------------------------------ caps
def _fmt_usd(x):
    return "$%.2f" % x if x >= 0.01 or x == 0 else "$%.4f" % x


def over(pid, add_tok=0, add_usd=0.0, cfg=None):
    """The first cap this provider would reach with the given extra tokens / dollars, as {'cap', 'used', 'limit', 'label'}, or None."""
    cfg = cfg or config()
    cap = cfg["caps"].get(pid)
    if not cap:
        return None
    u = used(pid)
    for key, scope, unit in (("usd_day", "day", "usd"), ("usd_month", "month", "usd"), ("tok_day", "day", "tok"), ("tok_month", "month", "tok")):
        lim = cap.get(key) or 0
        if lim <= 0:
            continue
        have = u[scope][unit] + (add_usd if unit == "usd" else add_tok)
        if have >= lim:
            label = ("daily" if scope == "day" else "monthly") + (" spend" if unit == "usd" else " token")
            return {"cap": key, "used": have, "limit": lim, "label": label, "unit": unit}
    return None


def blocked(pid, add_tok=0, add_usd=0.0):
    """None when a request may go ahead, else a plain refusal message."""
    cfg = config()
    if cfg["overrides"].get(pid) == today():
        return None
    hit = over(pid, add_tok, add_usd, cfg)
    if not hit:
        return None
    p = db.get_provider(pid) or {}
    name = p.get("name") or pid
    fmt = _fmt_usd if hit["unit"] == "usd" else (lambda x: "{:,} tokens".format(int(x)))
    where = "SYSTEMS \u2192 BUDGETS"
    return ("%s has reached its %s cap (%s used, cap %s). The request was not sent. The cap resets at the start of the next %s; to continue now "
            "press OVERRIDE TODAY in %s%s." % (name, hit["label"], fmt(hit["used"]), fmt(hit["limit"]), "day" if "day" in hit["cap"] else "month", where,
                                               ", or raise the cap" if cfg["action"] == "block" else ""))


def capped_now(pid):
    """True when new requests to this provider would be refused (used by the router to leave it out)."""
    cfg = config()
    return cfg["overrides"].get(pid) != today() and over(pid, 0, 0.0, cfg) is not None


# ------------------------------------------------------------------ hooks used by providers.stream_chat
def gate(p, model, messages):
    """Called before a request. Raises ProviderError(kind='budget') at a cap, otherwise reserves the input and returns a token for record()."""
    pid = p["id"]
    cfg = config()
    if not cfg["caps"].get(pid):
        return {"pid": pid, "in": estimate_input(messages), "res": None}
    est_in = estimate_input(messages)
    pr = price_for(pid, model, cfg)
    est_usd = est_in * pr[0] / 1e6 if pr else 0.0
    msg = blocked(pid, est_in, est_usd)
    if msg:
        e = P.ProviderError(msg, 0, "budget")
        e.provider = pid
        e.action = cfg["action"]
        raise e
    with _lock:
        fl = _inflight.setdefault(pid, {"tok": 0, "usd": 0.0})
        fl["tok"] += est_in
        fl["usd"] += est_usd
    return {"pid": pid, "in": est_in, "res": (est_in, est_usd)}


def _release(tok):
    if tok.get("res"):
        with _lock:
            fl = _inflight.setdefault(tok["pid"], {"tok": 0, "usd": 0.0})
            fl["tok"] = max(0, fl["tok"] - tok["res"][0])
            fl["usd"] = max(0.0, round(fl["usd"] - tok["res"][1], 8))
        tok["res"] = None


def _int(v):
    try:
        return max(0, int(v))
    except (TypeError, ValueError):
        return None


def record(tok, p, model, usage, out_chars):
    """Called when a request ends (normally, cancelled or broken). Nothing is recorded when nothing came back and the provider reported no usage:
    a request that failed before the first token was not billed. Never raises: accounting must not break an answer."""
    try:
        _release(tok)
        usage = usage or {}
        rin = _int(usage.get("prompt_tokens", usage.get("input_tokens")))
        rout = _int(usage.get("completion_tokens", usage.get("output_tokens")))
        if rin is None and rout is None and out_chars <= 0:
            return
        estimated = rin is None or rout is None
        tin = rin if rin is not None else tok["in"]
        tout = rout if rout is not None else estimate_text(out_chars)
        pr = price_for(tok["pid"], model)
        usd = (tin * pr[0] + tout * pr[1]) / 1e6 if pr else 0.0
        _add("%s:%s" % (tok["pid"], model), tin, tout, estimated, usd, pr is None)
    except Exception:
        pass


def install():
    P.BUDGET["gate"], P.BUDGET["record"] = gate, record


# ------------------------------------------------------------------ reports for the interface
def summary():
    """Everything the BUDGETS tab shows. No keys, no provider rows."""
    cfg = config()
    day_rows, month_rows = _rows(today()), _rows(this_month())
    providers = []
    for row in db.list_providers():
        p = db.get_provider(row["id"])
        if not p or p.get("kind", "llm") != "llm":
            continue
        pid = p["id"]
        d, m = _sum(day_rows, pid), _sum(month_rows, pid)
        if not (d["calls"] or m["calls"] or cfg["caps"].get(pid) or cfg["prices"].get(pid) or p["enabled"]):
            continue
        cap = cfg["caps"].get(pid) or {}
        pct = None
        for key, t in (("usd_day", d["usd"]), ("usd_month", m["usd"]), ("tok_day", d["tok"]), ("tok_month", m["tok"])):
            if cap.get(key):
                x = round(t / cap[key] * 100)
                pct = x if pct is None else max(pct, x)
        models = {}
        for scope, rows in (("day", day_rows), ("month", month_rows)):
            for ref, r in rows.items():
                if ref.split(":", 1)[0] == pid:
                    mm = models.setdefault(ref.split(":", 1)[1], {})
                    mm[scope] = {"in": r["in"], "out": r["out"], "calls": r["calls"], "est": r["est"], "usd": round(r["usd"], 6), "nopr": r["nopr"]}
        providers.append({"id": pid, "name": p["name"], "local": P.is_local_provider(p), "enabled": bool(p["enabled"]), "model": p["model"],
                          "day": d, "month": m, "caps": {k: cap.get(k, 0) for k in CAP_KEYS}, "price": cfg["prices"].get(pid),
                          "model_prices": {k.split(":", 1)[1]: v for k, v in cfg["prices"].items() if k.startswith(pid + ":")},
                          "pct": pct, "capped": capped_now(pid), "override": cfg["overrides"].get(pid) == today(), "models": models})
    return {"action": cfg["action"], "today": today(), "month": this_month(), "providers": providers,
            "totals": {"day": _sum(day_rows), "month": _sum(month_rows)}}


def today_meter():
    """A small reading for the status panel: tokens and dollars today, and how close the tightest cap is (percent, or None)."""
    cfg = config()
    t = _sum(_rows(today()))
    worst = None
    for pid in cfg["caps"]:
        if not _known_provider(pid):
            continue
        d, m, cap = _sum(_rows(today()), pid), _sum(_rows(this_month()), pid), cfg["caps"][pid]
        for key, x in (("usd_day", d["usd"]), ("usd_month", m["usd"]), ("tok_day", d["tok"]), ("tok_month", m["tok"])):
            if cap.get(key):
                pct = round(x / cap[key] * 100)
                worst = pct if worst is None else max(worst, pct)
    return {"usd": round(t["usd"], 4), "tokens": t["tok"], "calls": t["calls"], "estimated": t["est"], "unpriced": t["nopr"], "cap_pct": worst}
