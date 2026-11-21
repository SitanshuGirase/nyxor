#!/usr/bin/env python3
"""
NYXOR v2 - generic OpenAI-compatible provider client (Stage 2).

One client talks to every provider (Ollama, LM Studio, llama.cpp, Grok, Groq, Gemini, OpenRouter,
Mistral, GitHub Models, Cerebras, custom). All calls happen here, in the backend; the browser never
sees an API key and never calls a model directly. Standard library only.
"""
import contextlib
import ipaddress
import json
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import catalog
import db

RETRY_DELAY = 1.0          # seconds before the single retry on 429 / 5xx (selftest lowers it)
_status = {}               # provider id -> (state, timestamp): ok | rate | down
_slock = threading.Lock()
CONSENT = {"check": None, "ok": None}   # installed by privacy.py: check(flow) raises, ok(flow) returns bool
BUDGET = {"gate": None, "record": None}  # installed by budget.py: gate(p, model, messages) raises at a cap, record(...) books the usage
_NO_USAGE_OPTS = set()                  # provider ids whose server rejected stream_options (we then stop asking for token usage)


def consent(flow):
    """Raise ProviderError(kind=consent) unless the user allowed this kind of data flow (no-op when privacy.py is not installed)."""
    if CONSENT["check"]:
        CONSENT["check"](flow)


def consent_ok(flow):
    return CONSENT["ok"](flow) if CONSENT["ok"] else True


class ProviderError(Exception):
    def __init__(self, message, status=0, kind="error"):
        super().__init__(message)
        self.status, self.kind = status, kind


class Cancelled(Exception):
    pass


class Task:
    """One running request. abort() stops streams, council members and running commands."""

    def __init__(self, tid):
        self.id = tid
        self.cancel = threading.Event()
        self.conv_id = None
        self._lock = threading.Lock()
        self._closers = []
        self.events = {}        # run_id -> threading.Event (approval waits)
        self.decisions = {}     # run_id -> allow | deny | allow_all
        self.allow_all = False

    def add_closer(self, fn):
        with self._lock:
            self._closers.append(fn)
        if self.cancel.is_set():
            try:
                fn()
            except Exception:
                pass

    def remove_closer(self, fn):
        with self._lock:
            if fn in self._closers:
                self._closers.remove(fn)

    def child(self):
        """A sub-task that can be cancelled on its own; aborting the parent aborts it too."""
        c = Task(self.id + ".c")
        self.add_closer(c.abort)
        return c

    def abort(self):
        self.cancel.set()
        with self._lock:
            cs = list(self._closers)
        for c in cs:
            try:
                c()
            except Exception:
                pass
        for ev in list(self.events.values()):
            ev.set()


_sems, _slock2 = {}, threading.Lock()


@contextlib.contextmanager
def slot(p, task=None):
    """Concurrency limit per provider: ONE request at a time for a local model server (one GPU), three for a cloud API.
    Waits politely, and gives up at once if the task is cancelled."""
    with _slock2:
        sem = _sems.setdefault(p["id"], threading.Semaphore(1 if is_local_provider(p) else 3))
    while not sem.acquire(timeout=0.2):
        if task and task.cancel.is_set():
            raise Cancelled()
    try:
        if task and task.cancel.is_set():
            raise Cancelled()
        yield
    finally:
        sem.release()


def _note(pid, state):
    with _slock:
        _status[pid] = (state, time.time())


def _redact(text, p):
    k = p.get("api_key") or ""
    return str(text).replace(k, db.mask_key(k)) if len(k) > 6 else str(text)


def is_local_provider(p):
    """True only for built-in local slots whose URL really points at this machine (loopback).
    Agent mode is allowed for these and nothing else."""
    if not p or not p.get("is_local"):
        return False
    host = urllib.parse.urlparse(p["base_url"]).hostname or ""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def split_ref(ref):
    """'ollama:qwen3:8b' -> ('ollama', 'qwen3:8b')"""
    pid, _, model = (ref or "").partition(":")
    return pid, model


def _friendly(p, status, text):
    name = p["name"]
    snippet = _redact((text or "").strip().replace("\n", " "), p)[:280]
    try:
        j = json.loads(text)
        m = (j.get("error") or {}) if isinstance(j, dict) else {}
        snippet = _redact((m.get("message") if isinstance(m, dict) else m) or snippet, p)[:280]
    except Exception:
        pass
    if status in (401, 403):
        return "%s rejected the API key (HTTP %d). Check it in SYSTEMS \u2192 PROVIDERS. %s" % (name, status, snippet), "auth"
    if status == 404:
        return "%s: model or endpoint not found (HTTP 404). Use DETECT to pick a valid model. %s" % (name, snippet), "notfound"
    if status == 429:
        return "%s is rate-limiting you (HTTP 429, retried once). Wait a moment or pick another model. %s" % (name, snippet), "rate"
    if status >= 500:
        return "%s server error (HTTP %d, retried once). %s" % (name, status, snippet), "server"
    return "%s error (HTTP %d). %s" % (name, status, snippet), "error"


def _headers(p):
    h = {"Content-Type": "application/json", "User-Agent": "NYXOR/2"}
    key = p.get("api_key")
    if p.get("protocol") == "anthropic":
        h["anthropic-version"] = "2023-06-01"
        if key:
            h["x-api-key"] = key
    elif key:
        h["Authorization"] = "Bearer " + key
    return h


def _urlopen(req, timeout, task):
    """urlopen that ABORT can interrupt even while the server is still silent (a big local model loading can take a minute
    before the first byte). The connection finishes in the background and is closed as soon as it arrives."""
    if task is None:
        return urllib.request.urlopen(req, timeout=timeout)
    box = {}

    def go():
        try:
            box["r"] = urllib.request.urlopen(req, timeout=timeout)
        except BaseException as e:
            box["e"] = e
    t = threading.Thread(target=go, daemon=True)
    t.start()
    while t.is_alive():
        t.join(0.1)
        if task.cancel.is_set():
            def late():
                t.join()
                if "r" in box:
                    try:
                        box["r"].close()
                    except Exception:
                        pass
            threading.Thread(target=late, daemon=True).start()
            raise Cancelled()
    if "e" in box:
        raise box["e"]
    return box["r"]


def _open(p, path, body=None, url=None, timeout=600, task=None):
    """HTTP request with ONE retry on 429/5xx. Returns the open response."""
    url = url or p["base_url"].rstrip("/") + path
    data = json.dumps(body).encode() if body is not None else None
    headers = _headers(p)
    for attempt in (0, 1):
        if task and task.cancel.is_set():
            raise Cancelled()
        req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
        try:
            r = _urlopen(req, timeout, task)
            _note(p["id"], "ok")
            return r
        except urllib.error.HTTPError as e:
            try:
                text = e.read().decode("utf-8", "replace")
            except Exception:
                text = ""
            if (e.code == 429 or e.code >= 500) and attempt == 0:
                _note(p["id"], "rate" if e.code == 429 else "down")
                try:
                    delay = min(float(e.headers.get("Retry-After", "")), 5.0)
                except (TypeError, ValueError):
                    delay = RETRY_DELAY
                if task:
                    if task.cancel.wait(delay):
                        raise Cancelled()
                else:
                    time.sleep(delay)
                continue
            msg, kind = _friendly(p, e.code, text)
            _note(p["id"], "rate" if e.code == 429 else "down" if e.code >= 500 else "ok")
            raise ProviderError(msg, e.code, kind)
        except (urllib.error.URLError, socket.timeout, OSError) as e:
            if task and task.cancel.is_set():
                raise Cancelled()
            _note(p["id"], "down")
            reason = getattr(e, "reason", e)
            raise ProviderError("Cannot reach %s at %s: %s" % (p["name"], url, _redact(reason, p)), 0, "network")


def _shutdown(r):
    # wake a reader blocked in readline(); the reading thread closes the response itself
    try:
        r.fp.raw._sock.shutdown(socket.SHUT_RDWR)
    except Exception:
        pass


def _partial(s, tag):
    for n in range(min(len(tag) - 1, len(s)), 0, -1):
        if tag.startswith(s[-n:]):
            return n
    return 0


class ThinkSplitter:
    """Splits <think>...</think> out of streamed content into ('reasoning', ...) events."""

    def __init__(self):
        self.pend, self.inthink = "", False

    def feed(self, c):
        self.pend += c
        while self.pend:
            tag = "</think>" if self.inthink else "<think>"
            kind = "reasoning" if self.inthink else "content"
            i = self.pend.find(tag)
            if i < 0:
                k = _partial(self.pend, tag)
                out, self.pend = self.pend[:len(self.pend) - k], self.pend[len(self.pend) - k:]
                if out:
                    yield kind, out
                return
            out, self.pend = self.pend[:i], self.pend[i + len(tag):]
            if out:
                yield kind, out
            self.inthink = not self.inthink

    def flush(self):
        if self.pend:
            yield ("reasoning" if self.inthink else "content"), self.pend
            self.pend = ""


def _blocks(c):
    if isinstance(c, str):
        return [{"type": "text", "text": c or " "}]
    out = []
    for part in c or []:
        if part.get("type") == "text":
            out.append({"type": "text", "text": part.get("text") or " "})
        elif part.get("type") == "image_url":
            url = (part.get("image_url") or {}).get("url", "")
            if url.startswith("data:") and ";base64," in url:
                head, data = url.split(",", 1)
                out.append({"type": "image", "source": {"type": "base64", "media_type": head[5:].split(";")[0], "data": data}})
    return out or [{"type": "text", "text": " "}]


def _to_anthropic(messages):
    system, out = [], []
    for m in messages:
        if m["role"] == "system":
            c = m.get("content")
            system.append(c if isinstance(c, str) else "".join(x.get("text", "") for x in c if x.get("type") == "text"))
        elif m["role"] in ("user", "assistant"):
            out.append({"role": m["role"], "content": _blocks(m.get("content"))})
    if out and out[0]["role"] != "user":
        out.insert(0, {"role": "user", "content": [{"type": "text", "text": "(conversation start)"}]})
    return "\n\n".join(system), out


def _stream_anthropic(p, model, messages, temperature, task, max_tokens):
    system, msgs = _to_anthropic(messages)
    body = {"model": model, "messages": msgs, "stream": True, "max_tokens": max_tokens or 8192, "temperature": max(0.0, min(float(temperature), 1.0))}
    if system:
        body["system"] = system
    u_in = None
    r = _open(p, "/messages", body, task=task)
    closer = lambda: _shutdown(r)
    if task:
        task.add_closer(closer)
    try:
        while True:
            line = r.readline()
            if task and task.cancel.is_set():
                raise Cancelled()
            if not line:
                break
            line = line.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            try:
                j = json.loads(line[5:].strip())
            except ValueError:
                continue
            t = j.get("type")
            if t == "error":
                raise ProviderError("%s: %s" % (p["name"], _redact((j.get("error") or {}).get("message", "stream error"), p)), 0, "error")
            if t == "message_start":
                u_in = ((j.get("message") or {}).get("usage") or {}).get("input_tokens")
            elif t == "message_delta" and isinstance(j.get("usage"), dict):
                yield "usage", {"prompt_tokens": u_in, "completion_tokens": j["usage"].get("output_tokens")}
            if t == "content_block_delta":
                d = j.get("delta") or {}
                if d.get("type") == "text_delta" and d.get("text"):
                    yield "content", d["text"]
                elif d.get("type") == "thinking_delta" and d.get("thinking"):
                    yield "reasoning", d["thinking"]
            elif t == "message_stop":
                break
    except (OSError, ValueError) as e:
        if task and task.cancel.is_set():
            raise Cancelled()
        raise ProviderError("Stream from %s broke: %s" % (p["name"], _redact(e, p)), 0, "network")
    finally:
        if task:
            task.remove_closer(closer)
        try:
            r.close()
        except Exception:
            pass


def stream_chat(p, model, messages, temperature=0.7, task=None, tools=None, max_tokens=None):
    """Generator of ('content'|'reasoning', text), ('usage', dict) and finally ('tool_calls', [..]).
    Every model call in NYXOR passes through here, so this is where budget caps are enforced (before the request is sent)
    and where usage is booked (when it ends, even if it was cancelled or broke half way)."""
    if not is_local_provider(p):
        consent("cloud_models")
    tok = BUDGET["gate"](p, model, messages) if BUDGET["gate"] else None
    usage, out_chars = {}, 0
    try:
        for kind, val in _stream_chat(p, model, messages, temperature, task, tools, max_tokens):
            if kind in ("content", "reasoning"):
                out_chars += len(val)
            elif kind == "usage" and isinstance(val, dict):
                usage = dict(usage, **{k: v for k, v in val.items() if v is not None})
            elif kind == "tool_calls":
                out_chars += len(json.dumps(val))
            yield kind, val
    finally:
        if tok is not None and BUDGET["record"]:
            BUDGET["record"](tok, p, model, usage, out_chars)


def _open_stream(p, body, task):
    """Open the chat stream. NYXOR asks for token usage (stream_options); a server that rejects that option gets the request again without it."""
    if p["id"] not in _NO_USAGE_OPTS:
        body["stream_options"] = {"include_usage": True}
        try:
            return _open(p, "/chat/completions", body, task=task)
        except ProviderError as e:
            if e.status not in (400, 422):
                raise
            body.pop("stream_options", None)
            r = _open(p, "/chat/completions", body, task=task)
            _NO_USAGE_OPTS.add(p["id"])
            return r
    return _open(p, "/chat/completions", body, task=task)


def _stream_chat(p, model, messages, temperature, task, tools, max_tokens):
    if p.get("protocol") == "anthropic":
        yield from _stream_anthropic(p, model, messages, temperature, task, max_tokens)
        return
    body = {"model": model, "messages": messages, "stream": True, "temperature": float(temperature)}
    if tools:
        body["tools"] = tools
    if max_tokens:
        body["max_tokens"] = max_tokens
    r = _open_stream(p, body, task)
    closer = lambda: _shutdown(r)
    if task:
        task.add_closer(closer)
    split, calls = ThinkSplitter(), {}
    try:
        while True:
            line = r.readline()
            if task and task.cancel.is_set():
                raise Cancelled()
            if not line:
                break
            line = line.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                j = json.loads(data)
            except ValueError:
                continue
            if isinstance(j, dict) and j.get("error"):
                e = j["error"]
                raise ProviderError("%s: %s" % (p["name"], _redact(e.get("message") if isinstance(e, dict) else e, p)), 0, "error")
            ch = (j.get("choices") or [{}])[0]
            d = ch.get("delta") or ch.get("message") or {}
            rc = d.get("reasoning_content") or d.get("reasoning") or ""
            if rc:
                yield "reasoning", rc
            if d.get("content"):
                yield from split.feed(d["content"])
            for tc in d.get("tool_calls") or []:
                slot = calls.setdefault(tc.get("index", len(calls)), {"id": "", "name": "", "arguments": ""})
                slot["id"] = tc.get("id") or slot["id"]
                f = tc.get("function") or {}
                if f.get("name"):
                    slot["name"] = f["name"]
                a = f.get("arguments")
                if a:
                    slot["arguments"] += a if isinstance(a, str) else json.dumps(a)
            if j.get("usage"):
                yield "usage", j["usage"]
        yield from split.flush()
        if calls:
            yield "tool_calls", [calls[k] for k in sorted(calls)]
    except (OSError, ValueError) as e:
        if task and task.cancel.is_set():
            raise Cancelled()
        raise ProviderError("Stream from %s broke: %s" % (p["name"], _redact(e, p)), 0, "network")
    finally:
        if task:
            task.remove_closer(closer)
        try:
            r.close()
        except Exception:
            pass


def complete(p, model, messages, temperature=0.3, task=None, max_tokens=None):
    """Non-interactive helper: returns the full answer text."""
    return "".join(v for k, v in stream_chat(p, model, messages, temperature, task, None, max_tokens) if k == "content")


def detect(p, free_only=False):
    """Load model ids from /models. OpenRouter can be filtered to ids ending in ':free'."""
    url = None
    if (urllib.parse.urlparse(p["base_url"]).hostname or "") == "models.github.ai":
        url = p["base_url"].replace("/inference", "") + "/catalog/models"
    try:
        r = _open(p, "/models", url=url, timeout=20)
    except ProviderError as e:
        static = catalog.BY_ID.get(p["id"], {}).get("static_models")
        if static and e.status in (404, 405):
            return list(static)
        raise
    try:
        j = json.loads(r.read().decode("utf-8", "replace"))
    except ValueError:
        raise ProviderError("%s returned something that is not a model list." % p["name"])
    finally:
        r.close()
    items = (j.get("data") or j.get("models") or []) if isinstance(j, dict) else j
    ids = set()
    for m in items:
        i = (m.get("id") or m.get("name")) if isinstance(m, dict) else str(m)
        if i:
            ids.add(i[7:] if i.startswith("models/") else i)
    if free_only and p["id"] == "openrouter":
        ids = {i for i in ids if i.endswith(":free")}
    return sorted(ids)


def test(p):
    """Latency of one tiny real request (first token if a model is set, else /models), or the error."""
    t0 = time.time()
    try:
        if p.get("model"):
            for kind, _ in stream_chat(p, p["model"], [{"role": "user", "content": "Reply with OK."}], 0, None, None, 8):
                if kind in ("content", "reasoning"):
                    break
            what = "model " + p["model"]
        else:
            n = len(detect(p))
            what = "%d model(s) visible, no model selected yet" % n
        return {"ok": True, "latency_ms": int((time.time() - t0) * 1000), "message": what}
    except ProviderError as e:
        return {"ok": False, "error": str(e)}


def matrix():
    """Per-provider state for the PROVIDER MATRIX: ONLINE / NO KEY / OFFLINE / RATE-LIMITED."""
    provs = [db.get_provider(r["id"]) for r in db.list_providers()]

    def one(p):
        local, kind = is_local_provider(p), p.get("kind", "llm")
        if kind == "llm":
            if not p["is_local"] and not p["api_key"]:
                st = "NO KEY"
            elif p["is_local"]:
                try:
                    urllib.request.urlopen(urllib.request.Request(p["base_url"].rstrip("/") + "/models"), timeout=1.5).close()
                    st = "ONLINE"
                except Exception:
                    st = "OFFLINE"
            else:
                state, ts = _status.get(p["id"], ("ok", 0))
                recent = time.time() - ts < 90
                st = "RATE-LIMITED" if state == "rate" and recent else "OFFLINE" if state == "down" and recent else "ONLINE"
        elif p.get("protocol") == "colab":
            if not p["base_url"]:
                st = "NO KEY"
            else:
                try:
                    h = {"Authorization": "Bearer " + p["api_key"]} if p["api_key"] else {}
                    urllib.request.urlopen(urllib.request.Request(p["base_url"].rstrip("/") + "/health", headers=h), timeout=3).close()
                    st = "ONLINE"
                except Exception:
                    st = "OFFLINE"
        else:
            st = "ONLINE" if p["api_key"] else "NO KEY"
        return {"id": p["id"], "name": p["name"], "status": st, "enabled": bool(p["enabled"]), "local": local,
                "kind": kind, "has_key": bool(p["api_key"])}

    with ThreadPoolExecutor(max_workers=max(1, len(provs))) as ex:
        return list(ex.map(one, provs))
