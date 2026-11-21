#!/usr/bin/env python3
"""NYXOR v2 - shared model helpers and DIRECT mode (council.py and agent.py build on these)."""
import re
import time

import attachments as A
import db
import privacy
import providers as P
import research as R
import router
import telemetry as T
import websearch as S
import workspace as W

FILE_RULE = ("To create a file, use a fenced code block with info string file:relative/path.ext. "
             "Never truncate file contents. If a file itself contains code fences, wrap it in a longer fence (four backticks).")


def build_system(extra=""):
    parts = [db.get_setting("system_prompt") or db.DEFAULT_SYSTEM, FILE_RULE]
    mem = db.memory_prompt()
    if mem:
        parts.append(mem.strip())
    if privacy.store():
        parts.append(privacy.SAFETY)
    if extra:
        parts.append(extra)
    return "\n\n".join(parts)


def resolve_ref(ref):
    pid, model = P.split_ref(ref)
    p = db.get_provider(pid)
    if not p or p.get("kind", "llm") != "llm":
        raise P.ProviderError("Unknown model provider '%s'. Use /use provider:model." % pid)
    return p, model or p["model"]


def check_usable(p, model):
    if not p["enabled"]:
        raise P.ProviderError("%s is disabled. Enable it in SYSTEMS \u2192 PROVIDERS." % p["name"])
    if not p["is_local"] and not p["api_key"]:
        raise P.ProviderError("%s has no API key. Add one in SYSTEMS \u2192 PROVIDERS." % p["name"])
    if not model:
        raise P.ProviderError("No model selected for %s. Use DETECT in SYSTEMS \u2192 PROVIDERS." % p["name"])


def apply_think(msgs, model):
    """Qwen3: add /no_think to the last user message when deep reasoning is off (v1 behaviour)."""
    if db.get_setting("think") or not re.search(r"qwen3", model or "", re.I):
        return msgs
    msgs = [dict(m) for m in msgs]
    for m in reversed(msgs):
        if m["role"] == "user":
            if isinstance(m["content"], str):
                m["content"] += " /no_think"
            else:
                m["content"] = [dict(x) for x in m["content"]]
                for x in m["content"]:
                    if x.get("type") == "text":
                        x["text"] += " /no_think"
                        break
            break
    return msgs


def prepare(emit, cid, text, attachments=None, web=None, task=None, local=False, expander_model=None):
    """Process attachments and (when wanted) live web research. Returns {text, images, meta}: text is what the model
    should read, meta is stored with the user's message. web: 'on' | 'off' | 'auto' | None (= the saved setting)."""
    att = A.process(cid, attachments)
    for n in att["notices"]:
        emit({"type": "notice", "text": n})
    mode = web if web in ("on", "off", "auto") else (db.get_setting("web_mode") or "auto")
    depth = db.get_setting("web_depth") or "read"
    urls = R.find_urls(text) if mode != "off" else []
    want = bool(text.strip()) and (mode == "on" or (mode == "auto" and R.looks_current(text)))
    block, sources = "", None
    if want and S.pick(news=R.looks_news(text)) is None:
        want = False
        if mode == "on":
            emit({"type": "notice", "text": "Web research is on, but no search source is available. Paste a Tavily, Brave or Serper key (SYSTEMS \u2192 PROVIDERS) or enable the keyless fallback (SYSTEMS \u2192 WEB)."})
    if want or urls:
        try:
            budget = int(db.get_setting("research_chars") or 24000)
            expander = None
            if depth == "deep" and want and expander_model:
                p_, m_ = expander_model
                expander = lambda q_: P.complete(p_, m_, [{"role": "system", "content": "Write 3 different web search queries (one per line, no numbering, no commentary) that would together cover this question from different angles, including the very latest developments."},
                                                          {"role": "user", "content": q_}], 0.3, task, 80)
            block, sources = R.run(emit, text, task, read=depth != "snippets", total_chars=min(budget, 12000) if local else budget,
                                   depth=depth, expander=expander, urls=urls, search=want)
        except P.ProviderError as e:
            emit({"type": "notice", "text": "Web research failed: %s" % e})
        if task and task.cancel.is_set():
            raise P.Cancelled()
    meta = {}
    if att["meta"]:
        meta["attachments"] = att["meta"]
    if att["text"]:
        meta["attach_text"] = att["text"][:100000]
    if sources:
        meta["sources"] = [{"title": s["title"], "url": s["url"], "snippet": s["snippet"], "date": s.get("date", ""), "ok": s.get("ok", False),
                            "text": (s.get("text") or "")[:8000]} for s in sources]
    full = text + ("\n\n" + att["text"] if att["text"] else "") + ("\n\n" + block if block else "")
    return {"text": full, "images": att["images"], "meta": meta}


def build_user(inp, text=None):
    t = inp["text"] if text is None else text
    if not inp["images"]:
        return t
    return [{"type": "text", "text": t or "Describe the attached image(s)."}] + [
        {"type": "image_url", "image_url": {"url": "data:%s;base64,%s" % (i["mime"], i["b64"])}} for i in inp["images"]]


def stream(task, emit, p, model, msgs, temp, member=None, tools=None):
    """Stream one completion, emitting events. Returns dict(content, reasoning, toks, secs, tool_calls)."""
    content, reasoning, calls, toks, first = [], [], None, 0, None
    for kind, val in P.stream_chat(p, model, apply_think(msgs, model), temp, task, tools):
        if kind == "content":
            content.append(val)
            toks += 1
            first = first or time.time()
            emit({"type": "token" if member is None else "member_token", "text": val, **({} if member is None else {"idx": member})})
        elif kind == "reasoning":
            reasoning.append(val)
            toks += 1
            first = first or time.time()
            if member is None:
                emit({"type": "reasoning", "text": val})
        elif kind == "tool_calls":
            calls = val
    secs = max(time.time() - (first or time.time()), 0.01)
    return {"content": "".join(content), "reasoning": "".join(reasoning), "toks": toks, "secs": secs, "tool_calls": calls}


def save_assistant(emit, cid, content, reasoning, p, model, meta):
    mid = db.add_message(cid, "assistant", content, reasoning, p["id"], model, meta)
    saved, skipped = W.save_blocks(cid, content, mid)
    if saved:
        meta["files"] = saved
        db.update_message(mid, meta=meta)
        emit({"type": "files", "files": saved, "conv_id": cid})
    for path, why in skipped:
        emit({"type": "notice", "text": "File '%s' not saved: %s" % (path, why)})
    return mid


def _meta(model, r, **extra):
    return dict({"model": model, "toks": r["toks"], "tps": round(r["toks"] / r["secs"], 1)}, **extra)


def run_direct(task, emit, cid, text, p, model, attachments=None, web=None):
    """One answer from one model. With routing on, NYXOR picks the model per message (router.py) and shows why. If the model
    fails BEFORE it starts answering (bad key, rate limit, server down, model missing) the next-best model is tried, up to
    MAX_FALLBACKS times, with a visible notice. A failure after tokens have appeared is not retried: the partial answer stays and
    the error is shown, because a second answer would contradict what is already on screen."""
    selected = "%s:%s" % (p["id"], model)
    ctx_all = db.recent_context(cid, include_local=True)
    ctx_safe = db.recent_context(cid, include_local=False)
    private = len(ctx_all) != len(ctx_safe)       # the chat holds local-only (agent) output
    dec = router.decide(text, attachments, selected, private)
    auto = dec["mode"] == "auto"
    fallback_on = bool(db.get_setting("router_fallback"))
    escalate = auto or bool(db.get_setting("router_fallback_cloud"))
    route = {"auto": auto, "chosen": dec["chosen"], "complexity": dec["task"]["complexity"], "private": private, "fallbacks": []}
    if auto:
        route["why"] = router.line(dec)
        emit(dict(router.summary(dec), type="route"))
        p, model = resolve_ref(dec["chosen"])
    started = {"v": False}

    def track(ev):
        if ev.get("type") in ("token", "reasoning"):
            started["v"] = True
        emit(ev)

    inp, user_saved, failures, r = None, False, [], None
    while True:
        ref = "%s:%s" % (p["id"], model)
        try:
            try:
                check_usable(p, model)
            except P.ProviderError as e:
                e.kind = "config"
                raise
            if inp is None:
                inp = prepare(emit, cid, text, attachments, web, task, P.is_local_provider(p), (p, model))
            if not user_saved:
                db.add_message(cid, "user", text or "[attachment]", meta=dict(inp["meta"], mode="direct"))
                user_saved = True
            msgs = [{"role": "system", "content": build_system()}] + (ctx_all if P.is_local_provider(p) else ctx_safe) + [{"role": "user", "content": build_user(inp)}]
            r = stream(task, track, p, model, msgs, db.get_setting("temperature", 0.7))
        except P.Cancelled:
            emit({"type": "aborted"})
            return
        except P.ProviderError as e:
            kind = getattr(e, "kind", "error")
            if kind not in ("config", "budget"):
                router.record(ref, False, kind=kind)
            T.add_event("%s failed (%s)" % (ref, kind), "hot")
            nxt = None
            if fallback_on and not started["v"] and len(failures) < router.MAX_FALLBACKS and not task.cancel.is_set():
                failures.append({"ref": ref, "kind": kind})
                nxt = router.next_fallback(dec, failures, escalate)
            if not nxt:
                raise
            try:
                np_, nm_ = resolve_ref(nxt)
            except P.ProviderError:
                raise e
            reason = str(e).split("\n")[0][:200]
            route["fallbacks"].append({"from": ref, "to": nxt, "reason": reason})
            T.add_event("fallback %s \u2192 %s" % (ref, nxt), "hot")
            emit({"type": "fallback", "from": ref, "to": nxt, "reason": reason, "line": "%s failed (%s) \u2014 switching to %s" % (ref, reason, nxt)})
            p, model = np_, nm_
            continue
        break
    router.record("%s:%s" % (p["id"], model), True, r["toks"] / r["secs"])
    T.add_event("answered by %s:%s \u00b7 %d tokens \u00b7 %.1f tok/s" % (p["id"], model, r["toks"], r["toks"] / r["secs"]), "ok")
    extra = {"route": dict(route, chosen="%s:%s" % (p["id"], model))} if (auto or route["fallbacks"]) else {}
    save_assistant(emit, cid, r["content"].strip(), r["reasoning"], p, model, _meta(model, r, **extra))
    emit({"type": "done", "meta": _meta(model, r, **extra)})
