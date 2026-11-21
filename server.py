#!/usr/bin/env python3
"""
NYXOR v2 local launcher.  python server.py  ->  http://localhost:8777

Serves the interface and a JSON/SSE API. Every model call goes through this process (providers.py);
API keys stay in nyxor.db and are never sent to the browser. Binds to 127.0.0.1 only.
Standard library only.  python server.py --selftest  runs the built-in test suite against a mock model server.
"""
import os
import sys

if "--selftest" in sys.argv:  # isolate: must happen before db is imported
    import tempfile
    _tmp = tempfile.mkdtemp(prefix="nyxor-selftest-")
    os.environ["NYXOR_DB"] = os.path.join(_tmp, "nyxor.db")
    os.environ["NYXOR_WORKSPACE"] = os.path.join(_tmp, "nyxor-workspace")
    os.environ["NYXOR_KDF_FAST"] = "1"      # cheap key derivation for tests only

import argparse
import atexit
import hmac
import json
import re
import secrets
import signal
import threading
import time
import traceback
import uuid
import webbrowser
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

import agent
import attachments as A
import budget
import catalog
import council
import db
import modes
import assets
import privacy
import providers as P
import router
import telemetry
import terminal
import tts
import videogen
import websearch
import workspace as W

ROOT = os.path.dirname(os.path.abspath(__file__))
VERSION = "2.3.0"
HOST = "127.0.0.1"          # fixed: never exposed to the network
STATIC = {"/": "index.html", "/index.html": "index.html", "/style.css": "style.css", "/app.js": "app.js", "/core.js": "core.js", "/privacy": "privacy.html"}
FONTS = ("Inter-Regular.otf", "Inter-SemiBold.otf", "JetBrainsMono-Regular.ttf", "JetBrainsMono-Bold.ttf")
MIME = {".otf": "font/otf", ".ttf": "font/ttf", ".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "application/javascript; charset=utf-8"}
TASKS, TLOCK = {}, threading.Lock()
SESSION = [None]                  # random token issued to the browser that unlocked the vault (HttpOnly cookie)
LAST = [time.time()]              # last real user activity (for auto-lock)
FAILS = [0]
OPEN_ROUTES = {("GET", "/api/vault/status"), ("POST", "/api/vault/create"), ("POST", "/api/vault/unlock")}
IDLE_EXEMPT = {"/api/matrix", "/api/video/jobs", "/api/telemetry", "/api/terminal/output"}      # background polling must not keep the vault open
RED, DIM, RST = "\033[38;5;197m", "\033[2m", "\033[0m"


def providers_public():
    out = db.list_providers()
    for p in out:
        p["local"] = P.is_local_provider(db.get_provider(p["id"]))
        p["key_url"] = catalog.BY_ID.get(p["id"], {}).get("key_url", "")
    return out


CSP = ("default-src 'self'; style-src 'self' 'unsafe-inline'; "
       "font-src 'self' data:; img-src 'self' data: blob:; "
       "media-src 'self' blob:; connect-src 'self'; frame-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")


class Handler(BaseHTTPRequestHandler):
    server_version = "NYXOR/" + VERSION

    def log_message(self, fmt, *args):
        pass

    # ---------- plumbing
    def _send(self, code, data, ctype, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _send_file(self, path, ctype):
        size = os.path.getsize(path)
        start, end, code = 0, size - 1, 200
        m = re.match(r"bytes=(\d*)-(\d*)$", self.headers.get("Range", ""))
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
            else:
                start = max(0, size - int(m.group(2)))
            if start > end or start >= size:
                return self._send(416, b"", ctype, {"Content-Range": "bytes */%d" % size})
            code = 206
        self.send_response(code)
        for k, v in (("Content-Type", ctype), ("Content-Length", str(end - start + 1)), ("Accept-Ranges", "bytes"), ("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff")):
            self.send_header(k, v)
        if code == 206:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(65536, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj).encode(), "application/json")

    def _body(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        if n > 60 * 1024 * 1024:
            raise ValueError("body too large")
        raw = self.rfile.read(n) if n else b""
        return json.loads(raw or b"{}")

    def _guard(self, method):
        """Block DNS-rebinding and cross-site requests to the local API."""
        host = self.headers.get("Host", "")
        if not re.match(r"^(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$", host):
            self._json(403, {"error": "bad Host header"})
            return False
        if method == "POST":
            o = self.headers.get("Origin")
            if o and urlparse(o).netloc != host:
                self._json(403, {"error": "cross-origin request blocked"})
                return False
        return True

    def _session_ok(self):
        c = SimpleCookie()
        try:
            c.load(self.headers.get("Cookie", ""))
        except Exception:
            return False
        tok = c.get("nyx_sid")
        return bool(tok and SESSION[0] and hmac.compare_digest(tok.value, SESSION[0]))

    def _dispatch(self, method):
        if not self._guard(method):
            return
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path.startswith("/api/"):
                fn = ROUTES.get((method, u.path))
                if not fn:
                    return self._json(404, {"error": "no such endpoint"})
                if (method, u.path) not in OPEN_ROUTES:
                    if not db.is_unlocked() or not self._session_ok():
                        return self._json(423, {"error": "The vault is locked. Unlock it in the browser.", "locked": True})
                    if u.path not in IDLE_EXEMPT:
                        LAST[0] = time.time()
                return fn(self, q, self._body() if method == "POST" else {})
            if method == "GET" and u.path.startswith("/fonts/") and u.path[7:] in FONTS:
                return self._send(200, assets.read("fonts/" + u.path[7:]), MIME[os.path.splitext(u.path)[1]], {"Cache-Control": "public, max-age=86400"})
            if method == "GET" and u.path in STATIC:
                name = STATIC[u.path]
                body = assets.read(name)
                if name == "privacy.html":       # fill in the publisher's contact details (store_config.json)
                    cfg = assets.store_config()
                    body = body.replace(b"{{SUPPORT_EMAIL}}", (cfg["support_email"] or "the support address in the app listing").encode()) \
                               .replace(b"{{PUBLISHER_LINE}}", (" &middot; Publisher: " + cfg["publisher"]).encode() if cfg["publisher"] else b"")
                return self._send(200, body, MIME[os.path.splitext(name)[1]], {"Content-Security-Policy": CSP} if name.endswith(".html") else None)
            self._json(404, {"error": "not found"})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except (ValueError, P.ProviderError) as e:
            self._json(400, {"error": str(e)})
        except Exception as e:
            traceback.print_exc()
            try:
                self._json(500, {"error": "internal error: %s" % e})
            except Exception:
                pass

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")


# ------------------------------------------------------------------ routes
def r_state(h, q, b):
    return h._json(200, {"version": VERSION, "os": os.name, "providers": providers_public(), "settings": db.get_settings(),
                         "conversations": db.list_conversations(), "memories": db.list_memories(),
                         "defaults": {"system_prompt": db.DEFAULT_SYSTEM},
                         "workspace": db.WORKSPACE, "terminal": {"enabled": terminal.enabled(), "cwd": terminal.cwd() if terminal.enabled() else ""},
                         "store": dict(assets.store_config(), enabled=privacy.store()), "consent": privacy.state(), "flows": privacy.FLOWS,
                         "report_categories": list(db.REPORT_CATEGORIES)})


def r_settings(h, q, b):
    patch, s = {}, db.get_settings()
    for k, v in b.items():
        if k not in db.DEFAULT_SETTINGS:
            continue
        if k == "history_turns":
            v = max(0, min(int(v), 50))
        elif k == "temperature":
            v = max(0.0, min(float(v), 2.0))
        elif k == "agent_max_steps":
            v = max(1, min(int(v), 15))
        elif k == "agent_command_timeout":
            v = max(5, min(int(v), 120))
        elif k == "consent":
            continue                      # changed only through /api/consent
        elif k == "terminal_enabled":
            v = bool(v)
        elif k == "terminal_timeout":
            v = max(5, min(int(v), 600))
        elif k == "agent_approval" and v not in ("readonly", "ask", "auto"):
            raise ValueError("agent_approval must be readonly, ask or auto")
        elif k == "agent_approval" and v == "auto" and privacy.store():
            raise ValueError("AUTO approval is not available in the Store build: the agent always asks")
        elif k == "mode" and v not in ("direct", "council", "agent"):
            raise ValueError("bad mode")
        elif k == "agent_allowed_dirs":
            v = [os.path.realpath(x.strip()) for x in v if isinstance(x, str) and x.strip()]
            if not v:
                raise ValueError("at least one allowed folder is required")
        elif k == "council_members":
            v = [str(x) for x in v][:8]
        elif k in ("think", "show_think", "voice", "openrouter_free_only"):
            v = bool(v)
        elif k == "system_prompt":
            v = str(v).strip() or db.DEFAULT_SYSTEM
        elif k == "council_roles":
            v = {str(a)[:200]: str(c)[:40] for a, c in dict(v).items()}
        elif k in ("voice_fx", "ui_focus", "voice_control", "web_keyless", "web_reader", "council_auto", "council_review"):
            v = bool(v)
        elif k in ("router_stats", "budget", "usage"):
            continue                      # learned / validated by the backend only (budgets: /api/budget)
        elif k == "router_mode" and v not in ("manual", "auto"):
            raise ValueError("router_mode must be manual or auto")
        elif k in ("router_fallback", "router_fallback_cloud"):
            v = bool(v)
        elif k == "router_pool":
            v = [str(x)[:200] for x in v if isinstance(x, str) and ":" in x][:40]
        elif k == "council_strategy" and v not in ("auto", "candidates", "split", "merge"):
            raise ValueError("council_strategy must be auto, candidates, split or merge")
        elif k == "council_member_timeout":
            v = max(30, min(int(v), 1800))
        elif k == "council_grace":
            v = max(0, min(int(v), 120))
        elif k == "web_mode" and v not in ("auto", "on", "off"):
            raise ValueError("web_mode must be auto, on or off")
        elif k == "web_depth" and v not in ("snippets", "read", "deep"):
            raise ValueError("web_depth must be snippets, read or deep")
        elif k == "research_chars":
            v = max(4000, min(int(v), 60000))
        elif k == "voice_max_chars":
            v = max(0, min(int(v), 20000))
        elif k == "auto_lock_minutes":
            v = max(0, min(int(v), 1440))
        elif k == "council_fix_rounds":
            v = max(0, min(int(v), 3))
        elif k == "voice_wake":
            v = str(v).strip().lower()[:30]
        elif k == "voice_engine" and v not in ("browser", "cloud"):
            raise ValueError("voice_engine must be browser or cloud")
        elif k == "voice_pitch":
            v = max(0.0, min(float(v), 2.0))
        elif k == "voice_rate":
            v = max(0.4, min(float(v), 2.0))
        elif k in ("voice_name", "voice_provider", "voice_model", "voice_voice", "search_provider"):
            v = str(v)[:120]
        patch[k] = v
    return h._json(200, {"settings": db.set_settings(patch)})


def r_provider_save(h, q, b):
    return h._json(200, {"provider": db.upsert_provider(b)})


def r_provider_delete(h, q, b):
    return h._json(200, {"ok": db.delete_provider(b.get("id"))})


def _prov(b):
    p = db.get_provider(b.get("id"))
    if not p:
        raise ValueError("unknown provider")
    return p


def r_provider_models(h, q, b):
    p = _prov(b)
    try:
        models = P.detect(p, bool(db.get_setting("openrouter_free_only")))
    except P.ProviderError as e:
        return h._json(200, {"ok": False, "error": str(e)})
    cache = dict(db.get_setting("model_cache") or {})
    cache[p["id"]] = models
    db.set_settings({"model_cache": cache})
    if not p["model"] and models:
        db.upsert_provider({"id": p["id"], "model": catalog.pick_model(p["id"], models)})
    return h._json(200, {"ok": True, "models": models, "provider": db.list_providers() and [x for x in providers_public() if x["id"] == p["id"]][0]})


def r_provider_test(h, q, b):
    p = _prov(b)
    fn = {"llm": P.test, "search": websearch.test, "video": videogen.test}.get(p["kind"])
    if fn:
        return h._json(200, fn(p))
    return h._json(200, {"ok": bool(p["api_key"]), "latency_ms": 0, "message": "key saved \u2014 pick this voice in SYSTEMS \u2192 VOICE", "error": "no key"})


def r_keys_detect(h, q, b):
    return h._json(200, {"candidates": catalog.detect_key(b.get("key", ""))})


def _verify(pid):
    """Check a freshly added key against ITS provider only. Returns a result dict (never contains the key)."""
    p = db.get_provider(pid)
    out = {"ok": True, "provider": pid, "name": p["name"], "kind": p["kind"]}
    try:
        if p["kind"] == "llm":
            models = P.detect(p, bool(db.get_setting("openrouter_free_only")))
            cache = dict(db.get_setting("model_cache") or {})
            cache[pid] = models
            db.set_settings({"model_cache": cache})
            model = catalog.pick_model(pid, models, p["model"])
            db.upsert_provider({"id": pid, "model": model})
            t = P.test(db.get_provider(pid))
            out.update(models=len(models), model=model, test=t, ok=t["ok"])
            if not t["ok"]:
                out["error"] = t["error"]
            elif not db.get_setting("active_model") or db.get_provider(db.get_setting("active_provider"))["api_key"] == "" and not db.get_provider(db.get_setting("active_provider"))["is_local"]:
                db.set_settings({"active_provider": pid, "active_model": model})
        elif p["kind"] == "search":
            t = websearch.test(p)
            out.update(test=t, ok=t["ok"])
            if t["ok"] and not db.get_setting("search_provider"):
                db.set_settings({"search_provider": pid})
            if not t["ok"]:
                out["error"] = t["error"]
        elif p["kind"] == "video":
            t = videogen.test(p)
            out.update(test=t, ok=t["ok"])
            if not t["ok"]:
                out["error"] = t.get("error", "")
        else:
            out["message"] = "Key saved. Choose it in SYSTEMS \u2192 VOICE."
            if not db.get_setting("voice_provider"):
                db.set_settings({"voice_provider": pid})
    except P.ProviderError as e:
        out.update(ok=False, error=str(e), status=e.status)
    if not out["ok"] and out.get("status") in (401, 403):  # a rejected key is not worth keeping
        db.upsert_provider({"id": pid, "api_key": "", "enabled": False})
        out["removed"] = True
    return out


def r_keys_add(h, q, b):
    """Paste ANY key: the provider is recognised from its shape. If the shape is ambiguous the key is NOT sent anywhere;
    the browser gets the candidates and asks you to choose."""
    key = (b.get("key") or "").strip().strip("\"'")
    if not key:
        raise ValueError("Paste a key first.")
    pid = b.get("provider")
    cands = catalog.detect_key(key)
    if not pid:
        pid = catalog.auto_pick(cands)
        if not pid:
            return h._json(200, {"ok": False, "ambiguous": True, "candidates": cands})
    if pid == "custom":
        base = (b.get("base_url") or "").strip()
        if not re.match(r"^https?://", base):
            raise ValueError("Give the provider's API base URL (OpenAI-compatible), e.g. https://host/v1")
        rec = db.upsert_provider({"name": (b.get("name") or urlparse(base).hostname or "Custom").strip(), "base_url": base, "api_key": key, "enabled": True})
        pid = rec["id"]
    elif pid == "colab":
        conn = catalog.parse_connect(key)
        if not conn:
            raise ValueError("Paste the connect string the Colab notebook printed (https://\u2026trycloudflare.com?token=\u2026).")
        db.upsert_provider({"id": "colab", "base_url": conn[0], "api_key": conn[1], "enabled": True})
    elif db.get_provider(pid):
        db.upsert_provider({"id": pid, "api_key": key, "enabled": True})
    else:
        raise ValueError("Unknown provider '%s'." % pid)
    return h._json(200, dict(_verify(pid), providers=providers_public()))


def r_matrix(h, q, b):
    return h._json(200, {"matrix": P.matrix()})


def r_convs(h, q, b):
    return h._json(200, {"conversations": db.list_conversations(q.get("q", ""))})


def r_conv(h, q, b):
    db.reconcile_files()
    cid = int(q.get("id", 0))
    c = db.get_conversation(cid)
    if not c:
        return h._json(404, {"error": "no such conversation"})
    return h._json(200, {"conversation": c, "messages": db.list_messages(cid), "files": db.list_files(cid)})


def r_conv_new(h, q, b):
    return h._json(200, {"id": db.create_conversation(mode=b.get("mode", "direct"))})


def r_conv_update(h, q, b):
    cid = int(b["id"])
    if "title" in b:
        db.rename_conversation(cid, b["title"])
    if "pinned" in b:
        db.pin_conversation(cid, bool(b["pinned"]))
    return h._json(200, {"conversations": db.list_conversations()})


def r_conv_delete(h, q, b):
    db.delete_conversation(int(b["id"]))
    return h._json(200, {"conversations": db.list_conversations()})


def r_memories(h, q, b):
    if h.command == "POST":
        mid = db.add_memory(b.get("fact", ""))
        if not mid:
            return h._json(400, {"error": "empty fact"})
    return h._json(200, {"memories": db.list_memories(), **({"id": mid} if h.command == "POST" else {})})


def r_memory_delete(h, q, b):
    return h._json(200, {"ok": db.forget_memory(int(b["id"])), "memories": db.list_memories()})


def r_files(h, q, b):
    db.reconcile_files()
    return h._json(200, {"files": db.list_files(int(q.get("conv", 0)))})


def r_file(h, q, b):
    try:
        data = W.read_file(int(q.get("conv", 0)), q.get("path", ""))
    except (ValueError, FileNotFoundError, OSError):
        return h._json(404, {"error": "file not found or path not allowed"})
    name = os.path.basename(q["path"].replace("\\", "/"))
    ext = os.path.splitext(name)[1].lower()
    if q.get("img") and ext in A.IMG:
        return h._send(200, data, A.IMG[ext], {"Content-Disposition": "inline"})
    if q.get("view"):  # shown as plain text, never rendered, so generated HTML cannot run on this origin
        return h._send(200, data[:400000], "text/plain; charset=utf-8")
    return h._send(200, data, "application/octet-stream", {"Content-Disposition": "attachment; filename*=UTF-8''%s" % quote(name)})


def r_zip(h, q, b):
    cid = int(q.get("conv", 0))
    return h._send(200, W.zip_conv(cid), "application/zip", {"Content-Disposition": 'attachment; filename="nyxor_conv_%d.zip"' % cid})


def r_file_save(h, q, b):
    cid = int(b["conv_id"])
    if not db.get_conversation(cid):
        raise ValueError("no such conversation")
    return h._json(200, {"file": W.write_file(cid, b.get("path", ""), str(b.get("content", "")))})


def r_export(h, q, b):
    data = json.dumps(db.export_all(include_keys=q.get("keys") == "1"), indent=1).encode()
    return h._send(200, data, "application/json", {"Content-Disposition": 'attachment; filename="nyxor-export.json"'})


def r_import(h, q, b):
    try:
        return h._json(200, {"imported": db.import_all(b)})
    except ValueError as e:
        return h._json(400, {"error": str(e)})


def r_abort(h, q, b):
    with TLOCK:
        targets = [t for t in TASKS.values() if b.get("task_id") in (None, t.id) or b.get("all")]
    for t in targets:
        t.abort()
    return h._json(200, {"aborted": len(targets)})


def r_decision(h, q, b):
    with TLOCK:
        t = TASKS.get(b.get("task_id"))
    if not t or b.get("decision") not in ("allow", "deny", "allow_all"):
        return h._json(400, {"error": "unknown task or decision"})
    rid = int(b["run_id"])
    t.decisions[rid] = "allow" if (b["decision"] == "allow_all" and privacy.store()) else b["decision"]
    ev = t.events.get(rid)
    if ev:
        ev.set()
    return h._json(200, {"ok": True})


def r_chat(h, q, b):
    s = db.get_settings()
    mode, text = b.get("mode") or s["mode"], (b.get("text") or "").strip()
    if mode not in ("direct", "council", "agent") or not (text or b.get("attachments")):
        return h._json(400, {"error": "need text and a valid mode"})
    ref = b.get("model_ref") or "%s:%s" % (s["active_provider"], s["active_model"])
    try:
        p, model = modes.resolve_ref(ref)
    except P.ProviderError as e:
        return h._json(400, {"error": str(e)})
    if mode == "agent" and not P.is_local_provider(p):
        return h._json(403, {"error": "Agent mode is restricted to local models (Ollama, LM Studio, llama.cpp on this PC). "
                                      "Cloud models never see local files or command output."})
    atts = b.get("attachments") if isinstance(b.get("attachments"), list) else []
    web = {True: "on", False: None}.get(b.get("web"), b.get("web"))
    cid = b.get("conv_id")
    if not (cid and db.get_conversation(int(cid))):
        cid = db.create_conversation(mode=mode)
    cid = int(cid)
    db.set_conversation_mode(cid, mode)
    task = P.Task(uuid.uuid4().hex[:12])
    task.conv_id = cid
    with TLOCK:
        TASKS[task.id] = task
    h.send_response(200)
    for k, v in (("Content-Type", "text/event-stream"), ("Cache-Control", "no-store"), ("X-Accel-Buffering", "no"), ("Connection", "close")):
        h.send_header(k, v)
    h.end_headers()
    wl, done = threading.Lock(), threading.Event()

    def raw(chunk):
        with wl:
            try:
                h.wfile.write(chunk)
                h.wfile.flush()
            except (OSError, ValueError):
                task.abort()  # browser went away

    emit = lambda ev: raw(b"data: " + json.dumps(dict(ev, message_id=db.last_assistant_id(cid)) if ev.get("type") == "done" else ev).encode() + b"\n\n")

    def heartbeat():
        while not done.wait(4):
            raw(b": ping\n\n")

    threading.Thread(target=heartbeat, daemon=True).start()
    emit({"type": "start", "conv_id": cid, "task_id": task.id, "mode": mode})
    try:
        if mode == "direct":
            modes.run_direct(task, emit, cid, text, p, model, atts, web)
        elif mode == "council":
            council.run_council(task, emit, cid, text, s["council_lead"] or ref, s["council_members"], atts, web)
        else:
            agent.run_agent(task, emit, cid, text, p, model, atts)
    except P.Cancelled:
        emit({"type": "aborted"})
    except P.ProviderError as e:
        db.add_message(cid, "system", "Error: %s" % e)
        emit({"type": "error", "message": str(e), "kind": getattr(e, "kind", ""), "provider": getattr(e, "provider", ""), "action": getattr(e, "action", "")})
    except Exception as e:
        traceback.print_exc()
        db.add_message(cid, "system", "Internal error: %s" % e)
        emit({"type": "error", "message": "Internal error: %s" % e})
    finally:
        done.set()
        task.abort()
        with TLOCK:
            TASKS.pop(task.id, None)
    emit({"type": "end"})
    h.close_connection = True


def r_video_generate(h, q, b):
    return h._json(200, {"job": videogen.submit(b.get("backend", ""), b.get("prompt", ""), b)})


def r_video_jobs(h, q, b):
    jobs = db.list_video_jobs()
    for j in jobs:
        j["url"] = "/api/video/file?id=" + j["id"] if j["status"] == "done" else ""
        j.pop("path", None)
        j["params"] = json.loads(j["params"] or "{}")
    return h._json(200, {"jobs": jobs})


def r_video_file(h, q, b):
    j = db.get_video_job(q.get("id", ""))
    path = j and j["path"]
    if not path or j["status"] != "done" or not os.path.isfile(path) or os.path.dirname(os.path.realpath(path)) != os.path.realpath(videogen.video_dir()):
        return h._json(404, {"error": "no such video"})
    return h._send_file(path, "video/mp4")


def r_video_cancel(h, q, b):
    return h._json(200, {"ok": videogen.cancel(b.get("id", ""))})


def r_video_delete(h, q, b):
    j = db.get_video_job(b.get("id", ""))
    if j and j["path"] and os.path.isfile(j["path"]) and os.path.dirname(os.path.realpath(j["path"])) == os.path.realpath(videogen.video_dir()):
        os.remove(j["path"])
    if j:
        db.delete_video_job(j["id"])
    return r_video_jobs(h, q, b)


def r_file_delete(h, q, b):
    W.delete_file(int(b["conv"]), b.get("path", ""))
    return h._json(200, {"files": db.list_files(int(b["conv"]))})


PREVIEW_CSP = ("sandbox allow-scripts allow-modals allow-pointer-lock; default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
               "img-src data: blob:; media-src data: blob:; font-src data:; connect-src 'none'; frame-ancestors 'self'")
STORAGE_SHIM = ("<script>try{var _m=function(){var d={};return{getItem:function(k){return k in d?d[k]:null},setItem:function(k,v){d[k]=String(v)},"
                "removeItem:function(k){delete d[k]},clear:function(){d={}},key:function(i){return Object.keys(d)[i]||null},get length(){return Object.keys(d).length}}};"
                "Object.defineProperty(window,'localStorage',{value:_m(),configurable:true});Object.defineProperty(window,'sessionStorage',{value:_m(),configurable:true})}catch(e){}</script>")


def bundle_html(cid, rel):
    """One self-contained page: local scripts/styles/small images are inlined so the sandboxed preview needs no requests."""
    html = W.read_file(cid, rel).decode("utf-8", "replace")
    folder = os.path.dirname(rel)

    def local(ref):
        if re.match(r"(?:[a-z]+:)?//|data:|#", ref, re.I):
            return None
        try:
            return W.read_file(cid, os.path.normpath(os.path.join(folder, ref.split("?")[0].split("#")[0])).replace("\\", "/"))
        except (ValueError, FileNotFoundError, OSError):
            return None

    def script(m):
        data = local(m.group(2))
        return m.group(0) if data is None else "<script%s%s>%s</script>" % (m.group(1), m.group(3), data.decode("utf-8", "replace").replace("</script", "<\\/script"))

    def style(m):
        data = local(m.group(1) if m.group(1) else m.group(2))
        return m.group(0) if data is None else "<style>%s</style>" % data.decode("utf-8", "replace").replace("</style", "<\\/style")

    def img(m):
        data = local(m.group(2))
        if data is None or len(data) > 2 * 1024 * 1024:
            return m.group(0)
        import base64
        ext = os.path.splitext(m.group(2))[1].lower()
        mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}.get(ext)
        return m.group(0) if not mime else '%s"data:%s;base64,%s"' % (m.group(1), mime, base64.b64encode(data).decode())
    html = re.sub(r"<script([^>]*?)\ssrc\s*=\s*[\"']([^\"']+)[\"']([^>]*)>\s*</script>", script, html, flags=re.I)
    html = re.sub(r"<link[^>]*\brel\s*=\s*[\"']stylesheet[\"'][^>]*\bhref\s*=\s*[\"']([^\"']+)[\"'][^>]*>|<link[^>]*\bhref\s*=\s*[\"']([^\"']+)[\"'][^>]*\brel\s*=\s*[\"']stylesheet[\"'][^>]*>", style, html, flags=re.I)
    html = re.sub(r"(<img[^>]*\ssrc\s*=\s*)[\"']([^\"']+)[\"']", img, html, flags=re.I)
    m = re.search(r"<head[^>]*>", html, re.I)
    return (html[:m.end()] + STORAGE_SHIM + html[m.end():]) if m else STORAGE_SHIM + html


def r_preview(h, q, b):
    """Play a generated page inside NYXOR: sandboxed (opaque origin), no network, no access to the API or your chats."""
    try:
        rel = W.safe_rel(q.get("path", ""))
        if not rel.lower().endswith((".html", ".htm")):
            raise ValueError("only HTML files can be previewed")
        page = bundle_html(int(q.get("conv", 0)), rel)
    except (ValueError, FileNotFoundError, OSError):
        return h._json(404, {"error": "file not found or not an HTML page"})
    return h._send(200, page.encode("utf-8"), "text/html; charset=utf-8", {"Content-Security-Policy": PREVIEW_CSP, "Referrer-Policy": "no-referrer"})


def r_council_stats(h, q, b):
    return h._json(200, {"stats": db.get_setting("council_stats") or {}})


def r_council_stats_reset(h, q, b):
    council.reset_stats()
    return h._json(200, {"stats": {}})


def r_telemetry(h, q, b):
    """Measured system, app and model readings plus the real event feed (see telemetry.py)."""
    try:
        since = int(q.get("since") or 0)
    except (TypeError, ValueError):
        since = 0
    mins = int(db.get_setting("auto_lock_minutes") or 0)
    return h._json(200, telemetry.snapshot(LAST[0], mins, bool(TASKS) or videogen.active(), since))


def r_terminal_run(h, q, b):
    try:
        res = terminal.start(str(b.get("command") or ""))
    except PermissionError as e:
        return h._json(403, {"error": str(e)})
    except ValueError as e:
        return h._json(400, {"error": str(e)})
    if res.get("id"):
        telemetry.add_event("terminal command started")
    return h._json(200, res)


def r_terminal_output(h, q, b):
    try:
        since = max(0, int(q.get("since") or 0))
    except ValueError:
        since = 0
    return h._json(200, terminal.poll(str(q.get("id") or ""), since))


def r_terminal_kill(h, q, b):
    return h._json(200, {"ok": terminal.kill(str(b.get("id") or ""))})


def r_consent(h, q, b):
    """Set which kinds of data may leave this PC. Body: {flows: {cloud_models: bool, ...}}."""
    before = privacy.state()["flows"]
    st = privacy.set_consent(b.get("flows") or {})
    changed = [k for k in st["flows"] if st["flows"][k] != before.get(k)]
    if changed:
        telemetry.add_event("privacy: " + ", ".join("%s %s" % (k, "allowed" if st["flows"][k] else "blocked") for k in changed))
    return h._json(200, {"consent": st})


def r_report(h, q, b):
    """Save a report about an AI answer (Store policy 11.16). It stays on this PC until the user sends it."""
    rid = db.add_report(int(b.get("conv_id") or 0) or None, int(b.get("message_id") or 0) or None, str(b.get("category") or "other"),
                        str(b.get("note") or ""), bool(b.get("include_text")))
    telemetry.add_event("report #%d saved on this PC" % rid)
    return h._json(200, {"id": rid})


def r_reports(h, q, b):
    return h._json(200, {"reports": db.list_reports()})


def r_report_delete(h, q, b):
    return h._json(200, {"ok": db.delete_report(int(b.get("id") or 0))})


def r_router_preview(h, q, b):
    """Show which model the router would pick for a message (nothing is sent to any model)."""
    s = db.get_settings()
    ref = b.get("model_ref") or "%s:%s" % (s["active_provider"], s["active_model"])
    atts = [{"name": str(a.get("name", ""))[:120]} for a in (b.get("attachments") or [])[:6] if isinstance(a, dict)]
    private = False
    try:
        cid = int(b.get("conv_id") or 0)
    except (TypeError, ValueError):
        cid = 0
    if cid and db.get_conversation(cid):
        private = len(db.recent_context(cid, include_local=True)) != len(db.recent_context(cid, include_local=False))
    mode = b.get("mode") if b.get("mode") in ("manual", "auto") else None
    return h._json(200, {"decision": router.summary(router.decide(str(b.get("text") or "")[:4000], atts, ref, private, mode))})


def r_router_stats(h, q, b):
    return h._json(200, {"stats": router.stats()})


def r_router_stats_reset(h, q, b):
    router.reset_stats()
    return h._json(200, {"stats": {}})


def r_budget(h, q, b):
    return h._json(200, {"budget": budget.summary()})


def r_budget_save(h, q, b):
    try:
        budget.save({k: b[k] for k in ("action", "prices", "caps") if k in b})
    except ValueError as e:
        return h._json(400, {"error": str(e)})
    return h._json(200, {"budget": budget.summary()})


def r_budget_override(h, q, b):
    try:
        budget.override_today(str(b.get("provider") or ""), bool(b.get("on", True)))
    except ValueError as e:
        return h._json(400, {"error": str(e)})
    return h._json(200, {"budget": budget.summary()})


def r_budget_reset(h, q, b):
    budget.reset_usage()
    return h._json(200, {"budget": budget.summary()})


def r_notebook(h, q, b):
    if True:
        return h._send(200, assets.read("nyxor_colab_video.ipynb"), "application/x-ipynb+json", {"Content-Disposition": 'attachment; filename="nyxor_colab_video.ipynb"'})


def r_tts(h, q, b):
    s = db.get_settings()
    p = db.get_provider(b.get("provider") or s["voice_provider"])
    if not p or p["kind"] not in ("llm", "tts"):
        raise ValueError("Choose a voice provider in SYSTEMS \u2192 VOICE.")
    data, mime = tts.synth(p, b.get("text", ""), b.get("voice") or s["voice_voice"], b.get("model") or s["voice_model"])
    return h._send(200, data, mime)


def _new_session():
    SESSION[0] = secrets.token_urlsafe(32)
    LAST[0] = time.time()
    FAILS[0] = 0


def _cookie():
    return {"Set-Cookie": "nyx_sid=%s; HttpOnly; SameSite=Strict; Path=/" % SESSION[0]}


def lock_now():
    """Abort everything running, then drop the key and the plaintext database from memory."""
    with TLOCK:
        ts = list(TASKS.values())
    for t in ts:
        t.abort()
    videogen.cancel_all()
    SESSION[0] = None
    telemetry.clear_events()
    db.lock()


def r_vault_status(h, q, b):
    st = db.status()
    st["session"] = bool(st["unlocked"] and h._session_ok())
    return h._json(200, st)


def r_vault_create(h, q, b):
    try:
        code = db.create_vault(b.get("passphrase", ""))
    except ValueError as e:
        return h._json(400, {"error": str(e)})
    _new_session()
    telemetry.add_event("vault created", "ok")
    return h._send(200, json.dumps({"recovery_code": code}).encode(), "application/json", _cookie())


def r_vault_unlock(h, q, b):
    secret = b.get("secret", "")
    try:
        if db.is_unlocked():
            if not db.VAULT.check(secret):
                raise db.BadSecret("Wrong passphrase or recovery code.")
        else:
            db.unlock(secret)
    except db.BadSecret as e:
        FAILS[0] += 1
        time.sleep(min(0.4 * 2 ** FAILS[0], 8))       # slow down guessing
        return h._json(401, {"error": str(e)})
    except db.Corrupt as e:
        return h._json(500, {"error": str(e)})
    _new_session()
    telemetry.add_event("vault unlocked", "ok")
    return h._send(200, json.dumps({"ok": True}).encode(), "application/json", _cookie())


def r_vault_lock(h, q, b):
    lock_now()
    return h._json(200, {"ok": True})


def r_vault_passphrase(h, q, b):
    try:
        db.change_passphrase(b.get("old", ""), b.get("new", ""))
    except (db.BadSecret, ValueError) as e:
        return h._json(400, {"error": str(e)})
    return h._json(200, {"ok": True})


def r_vault_recovery(h, q, b):
    try:
        return h._json(200, {"recovery_code": db.new_recovery_code(b.get("passphrase", ""))})
    except db.BadSecret as e:
        return h._json(400, {"error": str(e)})


def r_vault_backup(h, q, b):
    return h._send(200, db.backup_bytes(), "application/octet-stream", {"Content-Disposition": 'attachment; filename="nyxor-vault-backup.db"'})


ROUTES = {
    ("GET", "/api/preview"): r_preview, ("GET", "/api/council/stats"): r_council_stats, ("POST", "/api/council/stats/reset"): r_council_stats_reset,
    ("GET", "/api/telemetry"): r_telemetry, ("POST", "/api/terminal/run"): r_terminal_run, ("GET", "/api/terminal/output"): r_terminal_output,
    ("POST", "/api/terminal/kill"): r_terminal_kill, ("POST", "/api/consent"): r_consent, ("POST", "/api/report"): r_report, ("GET", "/api/reports"): r_reports,
    ("POST", "/api/report/delete"): r_report_delete,
    ("GET", "/api/budget"): r_budget, ("POST", "/api/budget"): r_budget_save, ("POST", "/api/budget/override"): r_budget_override,
    ("POST", "/api/budget/reset"): r_budget_reset,
    ("POST", "/api/router/preview"): r_router_preview, ("GET", "/api/router/stats"): r_router_stats, ("POST", "/api/router/stats/reset"): r_router_stats_reset,
    ("GET", "/api/vault/status"): r_vault_status, ("POST", "/api/vault/create"): r_vault_create, ("POST", "/api/vault/unlock"): r_vault_unlock,
    ("POST", "/api/vault/lock"): r_vault_lock, ("POST", "/api/vault/passphrase"): r_vault_passphrase,
    ("POST", "/api/vault/recovery"): r_vault_recovery, ("GET", "/api/vault/backup"): r_vault_backup,
    ("POST", "/api/file/delete"): r_file_delete,
    ("POST", "/api/keys/detect"): r_keys_detect, ("POST", "/api/keys/add"): r_keys_add,
    ("POST", "/api/video/generate"): r_video_generate, ("GET", "/api/video/jobs"): r_video_jobs, ("GET", "/api/video/file"): r_video_file,
    ("POST", "/api/video/cancel"): r_video_cancel, ("POST", "/api/video/delete"): r_video_delete, ("GET", "/api/colab/notebook"): r_notebook,
    ("POST", "/api/tts"): r_tts,
    ("GET", "/api/state"): r_state, ("POST", "/api/settings"): r_settings,
    ("POST", "/api/providers"): r_provider_save, ("POST", "/api/providers/delete"): r_provider_delete,
    ("POST", "/api/providers/models"): r_provider_models, ("POST", "/api/providers/test"): r_provider_test,
    ("GET", "/api/matrix"): r_matrix,
    ("GET", "/api/conversations"): r_convs, ("GET", "/api/conversation"): r_conv,
    ("POST", "/api/conversation/new"): r_conv_new, ("POST", "/api/conversation/update"): r_conv_update,
    ("POST", "/api/conversation/delete"): r_conv_delete,
    ("GET", "/api/memories"): r_memories, ("POST", "/api/memories"): r_memories, ("POST", "/api/memories/delete"): r_memory_delete,
    ("GET", "/api/files"): r_files, ("GET", "/api/file"): r_file, ("GET", "/api/zip"): r_zip, ("POST", "/api/files/save"): r_file_save,
    ("GET", "/api/export"): r_export, ("POST", "/api/import"): r_import,
    ("POST", "/api/chat"): r_chat, ("POST", "/api/abort"): r_abort, ("POST", "/api/agent/decision"): r_decision,
}


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def _watchdog(interval=15):
    """Auto-lock after idle time, and keep the database in step with files deleted outside NYXOR."""
    n = 0
    while True:
        time.sleep(interval)
        n += 1
        if not db.is_unlocked():
            continue
        try:
            mins = int(db.get_setting("auto_lock_minutes") or 0)
            if mins and time.time() - LAST[0] > mins * 60 and not TASKS and not videogen.active():
                lock_now()
            elif n % 4 == 0:
                db.reconcile_files()
        except Exception:
            pass


privacy.install()
budget.install()


def make_server(port, watch=15):
    threading.Thread(target=_watchdog, args=(watch,), daemon=True).start()
    return Server((HOST, port), Handler)


def open_window(url):
    """Open NYXOR in its own app-style window (no tabs or address bar) using Microsoft Edge or Chrome when installed (both are
    Chromium based); otherwise the default browser. A separate profile folder keeps it apart from the user's normal browsing.
    Best effort: this was written for Windows but could not be tested on Windows."""
    import shutil
    import subprocess
    cands = []
    if os.name == "nt":
        for env in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
            base = os.environ.get(env)
            if base:
                cands += [os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"), os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")]
    else:
        cands = [shutil.which(n) for n in ("microsoft-edge", "google-chrome", "chromium", "chromium-browser")]
    exe = next((c for c in cands if c and os.path.isfile(c)), None)
    if exe and not os.environ.get("NYXOR_BROWSER_TAB"):
        try:
            prof = os.path.join(assets.DATA_DIR, "window-profile")
            os.makedirs(prof, exist_ok=True)
            subprocess.Popen([exe, "--app=" + url, "--user-data-dir=" + prof, "--no-first-run", "--no-default-browser-check"])
            return
        except OSError:
            pass
    webbrowser.open(url)


def main():
    ap = argparse.ArgumentParser(description="Run NYXOR v2 locally.")
    ap.add_argument("--port", type=int, default=8777)
    ap.add_argument("--no-open", action="store_true")
    ap.add_argument("--selftest", action="store_true", help="run the built-in test suite against a mock model server")
    a = ap.parse_args()
    if a.selftest:
        try:
            import selftest
        except ImportError:
            sys.exit("The self-test is not part of the single-file build. Run it from the project folder: python server.py --selftest")
        sys.exit(selftest.run(sys.modules[__name__]))
    srv = make_server(a.port)
    url = "http://localhost:%d" % a.port
    print("\n%s  N Y X O R%s  %s// v%s \u00b7 local AI workspace%s\n" % (RED, RST, DIM, VERSION, RST))
    print("  interface   %s   (bound to %s only)" % (url, HOST))
    print("  database    %s   (encrypted)\n  workspace   %s%s" % (db.DB_PATH, db.WORKSPACE, "\n  build       Store build (stricter privacy and safety limits)" if privacy.store() else ""))
    pw = os.environ.get("NYXOR_PASSPHRASE")
    if pw:
        try:
            db.unlock(pw) if db.status()["exists"] and not db.status()["legacy"] else print("  recovery    %s" % db.create_vault(pw))
            _new_session()
            print("  vault       unlocked from NYXOR_PASSPHRASE (the browser will still ask once)")
        except Exception as e:
            print("  vault       could not unlock from NYXOR_PASSPHRASE: %s" % e)
    st = db.status()
    print("  vault       %s" % ("legacy plaintext database found: it will be encrypted when you set a passphrase" if st["legacy"]
                               else "locked: unlock it in the browser" if st["exists"] else "new: choose a passphrase in the browser"))
    print("\n  %sCtrl+C to stop%s\n" % (DIM, RST))
    atexit.register(lambda: db.is_unlocked() and db.flush(force=True))
    try:
        signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    except (ValueError, AttributeError):
        pass
    if not a.no_open:
        threading.Timer(0.8, lambda: open_window(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  saving and sealing the vault\u2026")
        if db.is_unlocked():
            db.flush(force=True)
        print("  stopped.")


if __name__ == "__main__":
    main()
