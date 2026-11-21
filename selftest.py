#!/usr/bin/env python3
"""NYXOR v2 selftest: python server.py --selftest. Everything runs against mock servers on localhost (no real APIs)."""
import http.client
import http.cookiejar
import io
import json
import os
import re
import shutil
import threading
import time
import urllib.error
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

GOOD_KEY, BAD_KEY, GSK_KEY = "sk-mock-SECRET-KEY-0001", "sk-bad-key-000000", "gsk_testkey_0123456789abcdef"
ANT_KEY, TAV_KEY, COLAB_TOKEN = "sk-ant-api03-mockmockmockmock", "tvly-dev-mockkey123", "colabtoken123"
PASS = "correct horse battery"
HERE = os.path.dirname(os.path.abspath(__file__))
GAME = open(os.path.join(HERE, "examples", "infinite_runner.html"), encoding="utf-8").read()
GAME_SYNTAX = GAME.replace("function jump() {", "function jump( {")
GAME_RUNTIME = GAME.replace("g.fillRect(player.x, player.y, player.w, player.h);", "g.fillRect(player.x, player.y, player.w, player.hh.size);")
GAME_POLISHED = GAME.replace("</html>", "<!-- polished --></html>")
MARKER = "SECRET-LOCAL-DATA-8841"
LOG = []
COUNT = {"429": 0}
SLEEP = "sleep 60" if os.name != "nt" else "Start-Sleep 60"
JOBS = {}
PAGES = {
    "4": '<html><head><title>JSON-LD story</title><script type="application/ld+json">{"@type":"NewsArticle","headline":"JSON-LD story","datePublished":"2026-11-09T07:00:00Z","articleBody":"JSONLD-BODY ' + "the full article text lives only inside the structured data block of this page. " * 5 + '"}</script></head><body><div id=app></div></body></html>',
    "1": "<html><head><title>Hormuz shipping disrupted</title></head><body><nav>SUBSCRIBE-NOISE menu</nav><script>var x='SCRIPT-NOISE'</script><article>%s</article><footer>FOOTER-NOISE</footer></body></html>"
         % ("<p>HORMUZ-FACT-ALPHA: tanker traffic through the strait fell sharply on Monday according to shipping data and insurers.</p>" * 6),
    "2": "<html><head><title>Diplomats meet</title><meta property=\"article:published_time\" content=\"2026-11-09T09:30:00Z\"></head><body><main>%s</main></body></html>"
         % ("<p>HORMUZ-FACT-BETA: regional diplomats met to discuss a ceasefire and the reopening of shipping lanes in the gulf region.</p>" * 6),
    "3": "<html><body><div id=app></div><script>render()</script></body></html>",
}


class Mock(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _j(self, code, obj, extra=None):
        d = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(d)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(d)

    def _raw(self, code, data, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _auth_ok(self):
        a = self.headers
        p = self.path
        if p.startswith("/cloud"):
            return a.get("Authorization") in ("Bearer " + GOOD_KEY, "Bearer " + GSK_KEY)
        if p.startswith("/anth"):
            return a.get("x-api-key") == ANT_KEY and a.get("anthropic-version")
        if p.startswith("/tavily"):
            return a.get("Authorization") == "Bearer " + TAV_KEY
        if p.startswith("/colab"):
            return a.get("Authorization") == "Bearer " + COLAB_TOKEN
        return True

    def do_GET(self):
        LOG.append((self.path, self.headers.get("Authorization"), None))
        port = self.server.server_address[1]
        if self.path.startswith("/page/"):
            return self._raw(200, PAGES.get(self.path[6:], "").encode(), "text/html; charset=utf-8")
        if self.path.startswith("/jina/"):
            return self._raw(200, ("Title: Reader page\n\nURL Source: x\n\nMarkdown Content:\n" + ("READER-TEXT the reader service rendered this page for us and returned clean text. " * 8)).encode(), "text/plain")
        if self.path.startswith("/bing/news/search"):
            from email.utils import formatdate
            import urllib.parse as up
            items = "".join("<item><title>%s</title><link>http://www.bing.com/news/apiclick.aspx?ref=FexRss&amp;url=%s&amp;c=1</link><description>%s</description><pubDate>%s</pubDate></item>"
                            % (t, up.quote("http://127.0.0.1:%d/page/%s" % (port, n), safe=""), d, formatdate()) for t, n, d in (("Bing one", "1", "bing snippet one"), ("Bing two", "2", "bing snippet two")))
            return self._raw(200, ("<?xml version='1.0'?><rss><channel>%s</channel></rss>" % items).encode(), "application/rss+xml")
        if self.path.startswith("/ddg/html/"):
            import urllib.parse as up
            rows = "".join('<a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=%s&rut=x">DDG %s</a><a class="result__snippet" href="x">ddg snippet %s</a>' % (up.quote("http://127.0.0.1:%d/page/%s" % (port, n), safe=""), n, n) for n in ("1", "2"))
            return self._raw(200, ("<html><body>%s</body></html>" % rows).encode(), "text/html")
        if not self._auth_ok():
            return self._j(401, {"error": {"message": "invalid api key"}})
        if self.path.endswith("/models"):
            return self._j(200, {"data": [{"id": i} for i in ("mock-ok", "mock-member", "x/free-model:free", "x/paid")]})
        if self.path == "/colab/health":
            return self._j(200, {"ok": True, "model": "mock-cogvideox", "gpu": "Mock T4"})
        m = re.match(r"^/colab/job/(\w+)$", self.path)
        if m:
            j = JOBS[m.group(1)]
            if j["status"] == "running" and time.time() - j["t"] > 1.0:
                j["status"] = "done"
            return self._j(200, {"status": j["status"], "progress": 0.5 if j["status"] == "running" else 1.0, "error": ""})
        if re.match(r"^/colab/video/\w+\.mp4$", self.path):
            return self._raw(200, b"\x00\x00\x00\x18ftypmp42FAKEVIDEOBYTES0123456789", "video/mp4")
        self._j(404, {})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        LOG.append((self.path, self.headers.get("Authorization") or self.headers.get("x-api-key"), body))
        port = self.server.server_address[1]
        if not self._auth_ok():
            return self._j(401, {"error": {"message": "invalid api key"}})
        if self.path == "/tavily/search":
            return self._j(200, {"results": [{"title": "Hormuz shipping", "url": "http://127.0.0.1:%d/page/1" % port, "content": "snip one", "published_date": "2026-11-08"},
                                             {"title": "Diplomats", "url": "http://127.0.0.1:%d/page/2" % port, "content": "snip two"},
                                             {"title": "JS only", "url": "http://127.0.0.1:%d/page/3" % port, "content": "snip three"}]})
        if self.path == "/colab/generate":
            jid = "job%d" % (len(JOBS) + 1)
            JOBS[jid] = {"status": "running", "t": time.time(), "prompt": body.get("prompt")}
            return self._j(200, {"job": jid})
        m = re.match(r"^/colab/cancel/(\w+)$", self.path)
        if m:
            JOBS[m.group(1)]["status"] = "cancelled"
            return self._j(200, {"ok": True})
        if self.path.endswith("/audio/speech"):
            return self._raw(200, b"ID3FAKEAUDIO", "audio/mpeg")
        if self.path == "/anth/v1/messages":
            txt = "Claude mock reply to: " + "".join(b.get("text", "") for b in body["messages"][-1]["content"] if b["type"] == "text")[:40]
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            try:
                self.wfile.write(b"event: message_start\ndata: " + json.dumps({"type": "message_start", "message": {"usage": {"input_tokens": 200, "output_tokens": 1}}}).encode() + b"\n\n")
                for w in txt.split(" "):
                    self.wfile.write(b"event: content_block_delta\ndata: " + json.dumps({"type": "content_block_delta", "delta": {"type": "text_delta", "text": w + " "}}).encode() + b"\n\n")
                self.wfile.write(b"event: message_delta\ndata: " + json.dumps({"type": "message_delta", "usage": {"output_tokens": 50}}).encode() + b"\n\n")
                self.wfile.write(b'event: message_stop\ndata: {"type":"message_stop"}\n\n')
            except OSError:
                pass      # NYXOR's TEST call hangs up after the first token
            return
        model, msgs = body.get("model", ""), body.get("messages", [])
        if model == "mock-429":
            COUNT["429"] += 1
            if COUNT["429"] == 1:
                return self._j(429, {"error": {"message": "slow down"}})
        if model == "mock-500":
            return self._j(500, {"error": {"message": "boom"}})
        if model == "mock-noopts" and "stream_options" in body:
            return self._j(400, {"error": {"message": "Unknown parameter: stream_options"}})
        sysm = next((m_["content"] for m_ in msgs if m_["role"] == "system"), "")
        lastc = next((m_["content"] for m_ in reversed(msgs) if m_["role"] == "user"), "")
        parts = lastc if isinstance(lastc, list) else [{"type": "text", "text": lastc}]
        last = "".join(x.get("text", "") for x in parts if x.get("type") == "text").replace(" /no_think", "")
        nimg = sum(1 for x in parts if x.get("type") == "image_url")
        has_tool = any(m_["role"] == "tool" for m_ in msgs)
        calls, words, delay = None, [], 0.01
        if "3 different web search queries" in sysm:
            words = "hormuz shipping latest\nhormuz diplomacy talks\nhormuz insurance rates"
        elif "code reviewer" in sysm:
            words = "- the jump feels floaty\n- add a pause key"
        elif "lead designer" in sysm:
            words = "BRIEF: an infinite runner. One index.html with a canvas 800x300, jump with Space, obstacles that speed up, score and best score, restart after game over."
        elif model == "mock-lead-goodfix" and "Improve the winning candidate" in sysm:
            words, delay = "Polished.\n```file:index.html\n" + GAME_POLISHED + "```", 0.0003
        elif model == "mock-lead-badfix" and "Improve the winning candidate" in sysm:
            words, delay = "Fixed.\n```file:index.html\n" + GAME_SYNTAX + "```", 0.0003
        elif model in ("mock-cand-good", "mock-cand-slow", "mock-cand-syntax", "mock-cand-runtime"):
            if model == "mock-cand-slow":
                time.sleep(8)
            words, delay = "Here is the game.\n```file:index.html\n" + {"mock-cand-syntax": GAME_SYNTAX, "mock-cand-runtime": GAME_RUNTIME}.get(model, GAME) + "```", 0.0003
        elif model in ("mock-ok", "mock-429", "mock-usage", "mock-noopts"):
            if "my name" in last.lower():
                fact = [l for l in sysm.splitlines() if l.startswith("- My name is ")]
                words = ("Your name is " + fact[0][13:] + ".") if fact else "I do not know your name."
            else:
                words = "Mock reply to: " + last[:60]
        elif model == "mock-echo":
            words = "images=%d file_hello=%s file_docx=%s file_pdf=%s" % (nimg, "hello from the text file" in last, "hello docx" in last, "Hello PDF world" in last)
        elif model == "mock-research":
            words = "I read %d sources. alpha=%s beta=%s noise=%s [1] [2]" % (len(re.findall(r"^\[\d\] ", last, re.M)), "HORMUZ-FACT-ALPHA" in last, "HORMUZ-FACT-BETA" in last,
                                                                           any(n in last for n in ("SUBSCRIBE-NOISE", "SCRIPT-NOISE", "FOOTER-NOISE")))
        elif model == "mock-slow":
            words, delay = "slow " * 300, 0.04
        elif model == "mock-member":
            time.sleep(0.6)
            words = "member answer"
        elif model == "mock-lead":
            words = '{"subtasks": ["sub A", "sub B", "sub C"]}' if "strict JSON" in sysm else "FINAL merged: " + last[-300:].replace("\n", " ")
        elif model == "mock-lead-badjson":
            words = "I refuse to output json" if "strict JSON" in sysm else "FINAL merged (fallback)"
        elif model == "mock-lead-build":
            if "strict JSON" in sysm:
                words = json.dumps({"spec": "canvas id=game; global startGame()", "subtasks": [
                    {"title": "markup", "files": ["index.html"], "instructions": "write the page"},
                    {"title": "logic", "files": ["game.js"], "instructions": "write the game"}]})
            elif "FILES PRODUCED SO FAR" in last and "syntax error" in last:
                words = "Fixed the script.\n```file:game.js\nfunction startGame() { return 1; }\n```\nOpen index.html."
            else:
                words = "Everything fits. Open index.html."
        elif model == "mock-builder":
            if "game.js" in last.split("YOU OWN THESE FILES")[-1]:
                words = "```file:game.js\nfunction startGame( {\n```"
            else:
                words = "```file:index.html\n<canvas id=game></canvas><script src=\"game.js\"></script>\n```"
        elif model == "mock-files":
            words = ("Here is your app.\n```file:index.html\n<h1>Todo</h1>\n```\n```file:style.css\nbody{color:red}\n```\n"
                     "```file:app.js\nconsole.log('todo')\n```\n```file:../evil.txt\nnope\n```\nDone.")
        elif model.startswith("mock-tools"):
            if has_tool and model != "mock-tools-loop":
                words = "Task complete. Tool said: " + next(m_["content"] for m_ in reversed(msgs) if m_["role"] == "tool")[:200]
            else:
                spec = {"mock-tools": ("write_file", {"path": "notes/hello.txt", "content": MARKER}),
                        "mock-tools-escape": ("read_file", {"path": "../../../../../../etc/passwd"}),
                        "mock-tools-cmd": ("run_command", {"command": SLEEP}),
                        "mock-tools-search": ("web_search", {"query": "private plans"}),
                        "mock-tools-loop": ("list_dir", {"path": "."})}[model]
                calls = [{"index": 0, "id": "call_1", "type": "function", "function": {"name": spec[0], "arguments": json.dumps(spec[1])}}]
        else:
            words = "unknown mock model"
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            if calls:
                self.wfile.write(b"data: " + json.dumps({"choices": [{"delta": {"tool_calls": calls}}]}).encode() + b"\n\n")
            else:
                pieces = words.split(" ") if isinstance(words, str) else words
                for i, w in enumerate(pieces):
                    self.wfile.write(b"data: " + json.dumps({"choices": [{"delta": {"content": w + (" " if i < len(pieces) - 1 else "")}}]}).encode() + b"\n\n")
                    self.wfile.flush()
                    time.sleep(delay)
            if model == "mock-usage" and (body.get("stream_options") or {}).get("include_usage"):
                self.wfile.write(b"data: " + json.dumps({"choices": [], "usage": {"prompt_tokens": 1000, "completion_tokens": 500}}).encode() + b"\n\n")
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except OSError:
            pass


class Client:
    def __init__(self, port, cookies=True):
        self.base = "http://127.0.0.1:%d" % port
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar)) if cookies else urllib.request.build_opener()

    def _open(self, req, timeout=60):
        return self.op.open(req, timeout=timeout)

    def get(self, path, headers=None):
        with self._open(urllib.request.Request(self.base + path, headers=headers or {})) as r:
            return r.read()

    def jget(self, path):
        return json.loads(self.get(path))

    def post(self, path, body):
        with self._open(urllib.request.Request(self.base + path, json.dumps(body).encode(), {"Content-Type": "application/json"})) as r:
            return json.loads(r.read())

    def chat(self, body, on_event=None):
        events = []
        with self._open(urllib.request.Request(self.base + "/api/chat", json.dumps(body).encode(), {"Content-Type": "application/json"})) as r:
            for raw in r:
                line = raw.decode().strip()
                if line.startswith("data:"):
                    ev = json.loads(line[5:])
                    events.append(ev)
                    if on_event:
                        on_event(ev)
        return events


def text_of(events):
    return "".join(e.get("text", "") for e in events if e["type"] == "token")


def code_of(fn):
    try:
        fn()
        return 200
    except urllib.error.HTTPError as e:
        return e.code


def run(srv_mod):
    import db
    import providers as P
    import research
    import vault
    import videogen
    import workspace as W
    P.RETRY_DELAY = 0.05
    videogen.POLL = 0.2
    research.ALLOW_PRIVATE = True
    mock = ThreadingHTTPServer(("127.0.0.1", 0), Mock)
    threading.Thread(target=mock.serve_forever, daemon=True).start()
    mport = mock.server_address[1]
    srv = srv_mod.make_server(0, watch=0.3)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    c = Client(srv.server_address[1])
    anon = Client(srv.server_address[1], cookies=False)
    base = "http://127.0.0.1:%d" % mport
    local, cloud = base + "/local/v1", base + "/cloud/v1"
    results = []

    def test(name):
        def deco(fn):
            t0 = time.time()
            try:
                fn()
                results.append((name, True, "%.1fs" % (time.time() - t0)))
            except Exception as e:
                import traceback
                traceback.print_exc()
                results.append((name, False, "%s: %s" % (type(e).__name__, e)))
            return fn
        return deco

    def cfg(**s):
        c.post("/api/settings", s)

    @test("1  vault: first run is empty; API is locked (423) until a passphrase is set; file on disk is not SQLite")
    def _():
        st = anon.jget("/api/vault/status")
        assert not st["exists"] and not st["unlocked"] and not os.path.exists(db.DB_PATH)
        assert code_of(lambda: anon.get("/api/state")) == 423
        assert code_of(lambda: anon.post("/api/vault/create", {"passphrase": "short"})) == 400
        r = c.post("/api/vault/create", {"passphrase": PASS})
        assert re.match(r"^([A-Z2-7]{4}-){5}[A-Z2-7]{4}$", r["recovery_code"])
        c.recovery = r["recovery_code"]
        import privacy
        assert c.jget("/api/state")["consent"]["needs_prompt"] and not any(c.jget("/api/state")["consent"]["flows"].values()), "nothing may be allowed before the user decides"
        c.post("/api/consent", {"flows": {k: True for k in privacy.FLOWS}})        # the tests below use mock cloud services, so allow every flow
        raw = open(db.DB_PATH, "rb").read()
        assert not raw.startswith(b"SQLite") and b"conversations" not in raw and b"providers" not in raw
        assert len(c.jget("/api/state")["providers"]) == 31 and os.path.isdir(db.WORKSPACE)
        assert code_of(lambda: anon.get("/api/state")) == 423, "another process without the session cookie can read the data"
        c.post("/api/providers", {"id": "ollama", "base_url": local, "enabled": True, "model": "mock-ok"})
        c.post("/api/providers", {"id": "xai", "base_url": cloud, "enabled": True, "api_key": GOOD_KEY, "model": "mock-ok"})
        c.post("/api/providers", {"id": "openai", "base_url": cloud, "enabled": True, "api_key": BAD_KEY, "model": "mock-member"})
        cfg(active_provider="ollama", active_model="mock-ok", web_mode="off")

    @test("2  chats are encrypted at rest, survive lock/unlock; wrong passphrase refused; recovery code works; /api/vault/lock locks")
    def _():
        ev = c.chat({"text": "hello there SECRETWORD-77", "mode": "direct", "model_ref": "ollama:mock-ok"})
        assert text_of(ev).startswith("Mock reply to: hello there"), text_of(ev)
        cid = ev[0]["conv_id"]
        time.sleep(1.0)
        for p in (db.DB_PATH, db.DB_PATH + ".bak"):
            if os.path.exists(p):
                assert b"SECRETWORD-77" not in open(p, "rb").read(), "plaintext found in " + p
        c.post("/api/vault/lock", {})
        assert code_of(lambda: c.get("/api/state")) == 423 and not db.is_unlocked()
        assert code_of(lambda: c.post("/api/vault/unlock", {"secret": "wrong passphrase!"})) == 401
        c.post("/api/vault/unlock", {"secret": PASS})
        msgs = c.jget("/api/conversation?id=%d" % cid)["messages"]
        assert [m["role"] for m in msgs] == ["user", "assistant"], msgs
        c.post("/api/vault/lock", {})
        c.post("/api/vault/unlock", {"secret": c.recovery.lower()})
        assert c.jget("/api/state")["conversations"]
        ev = c.chat({"text": "x", "mode": "direct", "model_ref": "ollama:mock-429"})
        assert any(e["type"] == "done" for e in ev) and COUNT["429"] == 2, "retry did not happen"
        err = [e for e in c.chat({"text": "x", "mode": "direct", "model_ref": "ollama:mock-500"}) if e["type"] == "error"]
        assert err and "HTTP 500" in err[0]["message"] and sum(1 for p, _, b in LOG if b and b.get("model") == "mock-500") == 2

    @test("3  paste-a-key: detection, ambiguity never leaks the key, auto-verify, key never reaches the browser, CSP + static whitelist")
    def _():
        d = c.post("/api/keys/detect", {"key": "tvly-dev-abc"})["candidates"]
        assert d[0]["id"] == "tavily" and c.post("/api/keys/detect", {"key": "https://x-y.trycloudflare.com?token=t"})["candidates"][0]["id"] == "colab"
        n = len(LOG)
        amb = c.post("/api/keys/add", {"key": "sk-" + "a1" * 16})
        assert amb["ambiguous"] and {x["id"] for x in amb["candidates"]} >= {"openai", "deepseek"} and len(LOG) == n, "ambiguous key was sent somewhere"
        c.post("/api/providers", {"id": "groq", "base_url": cloud})
        r = c.post("/api/keys/add", {"key": GSK_KEY})
        assert r["ok"] and r["provider"] == "groq" and r["models"] >= 3 and r["test"]["ok"], r
        assert GSK_KEY not in json.dumps(r)
        t = c.post("/api/providers/test", {"id": "xai"})
        assert t["ok"] and "latency_ms" in t
        bad = c.post("/api/providers/test", {"id": "openai"})
        assert not bad["ok"] and "rejected the API key" in bad["error"] and BAD_KEY not in bad["error"]
        blob = c.get("/api/state").decode() + c.get("/api/export").decode() + c.get("/api/conversations").decode()
        assert all(k not in blob for k in (GOOD_KEY, BAD_KEY, GSK_KEY))
        ev = c.chat({"text": "cloud hi", "mode": "direct", "model_ref": "xai:mock-ok"})
        assert text_of(ev).startswith("Mock reply to: cloud hi")
        assert any(p.startswith("/cloud") and a == "Bearer " + GOOD_KEY and b and b.get("model") == "mock-ok" for p, a, b in LOG), "backend did not send the key upstream"
        c.post("/api/providers", {"id": "openrouter", "base_url": cloud, "api_key": GOOD_KEY, "enabled": True})
        cfg(openrouter_free_only=True)
        assert c.post("/api/providers/models", {"id": "openrouter"})["models"] == ["x/free-model:free"]
        for bad_path in ("/nyxor.db", "/server.py", "/vault.py", "/nyxor-workspace/"):
            assert code_of(lambda: anon.get(bad_path)) == 404, bad_path
        h = http.client.HTTPConnection("127.0.0.1", srv.server_address[1])
        h.request("GET", "/")
        assert "default-src 'self'" in h.getresponse().getheader("Content-Security-Policy")
        # Anthropic native adapter
        c.post("/api/providers", {"id": "anthropic", "base_url": base + "/anth/v1"})
        r = c.post("/api/keys/add", {"key": ANT_KEY})
        assert r["provider"] == "anthropic", r
        c.post("/api/providers", {"id": "anthropic", "model": "claude-mock"})
        ev = c.chat({"text": "hi claude", "mode": "direct", "model_ref": "anthropic:claude-mock"})
        assert text_of(ev).startswith("Claude mock reply to: hi claude"), text_of(ev)

    @test("4  files: 3 downloadable files + zip; unsafe paths blocked; deleting a file also deletes it in the database")
    def _():
        ev = c.chat({"text": "Build a todo app with index.html, style.css, app.js", "mode": "direct", "model_ref": "ollama:mock-files"})
        saved = [f for e in ev if e["type"] == "files" for f in e["files"]]
        assert sorted(f["path"] for f in saved) == ["app.js", "index.html", "style.css"], saved
        assert any(e["type"] == "notice" and "evil.txt" in e["text"] for e in ev)
        cid = ev[0]["conv_id"]
        assert c.get("/api/file?conv=%d&path=index.html" % cid).strip() == b"<h1>Todo</h1>"
        z = zipfile.ZipFile(io.BytesIO(c.get("/api/zip?conv=%d" % cid)))
        assert sorted(z.namelist()) == ["app.js", "index.html", "style.css"]
        for bad in ("../x", "/etc/passwd", "C:\\win", "a/../../b", "~/x", ""):
            try:
                W.safe_rel(bad)
                raise AssertionError("allowed " + repr(bad))
            except ValueError:
                pass
        assert code_of(lambda: c.get("/api/file?conv=%d&path=../../nyxor.db" % cid)) == 404
        assert c.post("/api/files/save", {"conv_id": cid, "path": "snippet.py", "content": "print(1)"})["file"]["path"] == "snippet.py"
        left = c.post("/api/file/delete", {"conv": cid, "path": "style.css"})["files"]
        assert "style.css" not in [f["path"] for f in left] and not os.path.exists(os.path.join(W.conv_dir(cid), "style.css"))
        assert "style.css" not in json.dumps([m["meta"] for m in c.jget("/api/conversation?id=%d" % cid)["messages"]])
        os.remove(os.path.join(W.conv_dir(cid), "app.js"))        # deleted behind NYXOR's back
        assert "app.js" not in [f["path"] for f in c.jget("/api/files?conv=%d" % cid)["files"]], "stale DB record kept"
        # deleting a chat removes it from disk AND from both encrypted copies
        ev = c.chat({"text": "remember ZZ-DELETE-ME-4417 please", "mode": "direct", "model_ref": "ollama:mock-ok"})
        gone = ev[0]["conv_id"]
        c.post("/api/conversation/delete", {"id": gone})
        assert not os.path.exists(W.conv_dir(gone)) and gone not in [x["id"] for x in c.jget("/api/conversations")["conversations"]]
        for p in (db.DB_PATH, db.DB_PATH + ".bak"):
            tmp = p + ".chk"
            shutil.copy(p, tmp)
            image = vault.Vault(tmp).unlock(PASS)
            os.remove(tmp)
            assert b"ZZ-DELETE-ME-4417" not in image, "deleted chat still inside " + p

    @test("5  COUNCIL (text): parallel members, merge, one fails, cloud notice; bad JSON plan falls back")
    def _():
        cfg(council_lead="ollama:mock-lead", council_members=["ollama:mock-member", "xai:mock-member", "openai:mock-member"])
        t0 = time.time()
        ev = c.chat({"text": "plan a launch", "mode": "council", "model_ref": "ollama:mock-lead"})
        dt = time.time() - t0
        plan = [e for e in ev if e["type"] == "council_plan"][0]
        assert plan["subtasks"] == ["sub A", "sub B", "sub C"] and not plan["fallback"]
        st = {(e["idx"], e["status"]) for e in ev if e["type"] == "member_status"}
        assert (0, "done") in st and (1, "done") in st and (2, "failed") in st, st
        assert any(e["type"] == "notice" and "Cloud members receive this task" in e["text"] for e in ev)
        assert dt < 2.5, "members did not run in parallel (%.1fs)" % dt
        final = text_of(ev)
        assert final.startswith("FINAL merged") and "member answer" in final and "FAILED" in final, final
        cfg(council_lead="ollama:mock-lead-badjson")
        plan = [e for e in c.chat({"text": "whole task", "mode": "council", "model_ref": "ollama:mock-ok"}) if e["type"] == "council_plan"][0]
        assert plan["fallback"] and plan["subtasks"] == ["whole task"] * 3

    @test("5b COUNCIL (build): orchestrator spec -> members write their own files -> validation -> orchestrator repairs -> working files")
    def _():
        c.post("/api/providers", {"id": "ollama", "model": "mock-ok"})
        cfg(council_strategy="split", council_lead="xai:mock-lead-build", council_members=["ollama:mock-builder", "ollama:mock-builder"], council_roles={"ollama:mock-builder": "game developer"}, council_fix_rounds=2)
        ev = c.chat({"text": "make a simple snake game", "mode": "council", "model_ref": "ollama:mock-ok"})
        plan = [e for e in ev if e["type"] == "council_plan"][0]
        assert plan["files"] == [["index.html"], ["game.js"]] and "canvas" in plan["spec"], plan
        val = [e for e in ev if e["type"] == "council_validation"]
        have_node = bool(shutil.which("node"))
        if have_node:
            assert val[0]["round"] == 0 and any("game.js" in i["path"] and "syntax error" in i["problem"] for i in val[0]["issues"]), val
            assert val[-1]["issues"] == [], "orchestrator did not repair the broken file"
        files = [f for e in ev if e["type"] == "files" for f in e["files"]]
        assert sorted(f["path"] for f in files) == ["game.js", "index.html"], files
        cid = ev[0]["conv_id"]
        assert open(os.path.join(W.conv_dir(cid), "game.js")).read().strip() == ("function startGame() { return 1; }" if have_node else "function startGame( {")
        assert [e for e in ev if e["type"] == "done"]
        assert not [e for e in ev if e["type"] == "error"]
        cfg(council_strategy="auto")

    @test("6  AGENT: cloud refused (403); asks before writing; deny/allow; outside paths blocked; read-only; step cap; web_search asks; no leak to cloud")
    def _():
        assert code_of(lambda: c.chat({"text": "x", "mode": "agent", "model_ref": "xai:mock-ok"})) == 403
        cfg(agent_approval="ask")
        state = {}

        def decide(choice):
            def on(ev):
                if ev["type"] == "start":
                    state["task"] = ev["task_id"]
                if ev["type"] == "tool_request" and ev["needs_approval"]:
                    state["req"] = ev
                    state["existed"] = any(f.endswith("hello.txt") for _, _, fs in os.walk(db.WORKSPACE) for f in fs)
                    c.post("/api/agent/decision", {"task_id": state["task"], "run_id": ev["run_id"], "decision": choice})
            return on
        ev = c.chat({"text": "write a note", "mode": "agent", "model_ref": "ollama:mock-tools"}, decide("deny"))
        assert state["req"]["tool"] == "write_file" and not state["existed"]
        assert [e for e in ev if e["type"] == "tool_result"][0]["status"] == "denied"
        ev = c.chat({"text": "write a note", "mode": "agent", "model_ref": "ollama:mock-tools"}, decide("allow"))
        assert [e for e in ev if e["type"] == "tool_result"][0]["status"] == "ok"
        cid = ev[0]["conv_id"]
        assert open(os.path.join(W.conv_dir(cid), "notes", "hello.txt")).read() == MARKER
        res = [e for e in c.chat({"text": "read passwd", "mode": "agent", "model_ref": "ollama:mock-tools-escape"}) if e["type"] == "tool_result"][0]
        assert res["status"] == "blocked" and "outside the allowed folders" in res["output"], res
        ev = c.chat({"text": "search", "mode": "agent", "model_ref": "ollama:mock-tools-search"}, decide("deny"))
        assert state["req"]["tool"] == "web_search" and [e for e in ev if e["type"] == "tool_result"][0]["status"] == "denied"
        cfg(agent_approval="readonly")
        assert [e for e in c.chat({"text": "write a note", "mode": "agent", "model_ref": "ollama:mock-tools"}) if e["type"] == "tool_result"][0]["status"] == "denied"
        cfg(agent_approval="ask", agent_max_steps=3)
        ev = c.chat({"text": "loop", "mode": "agent", "model_ref": "ollama:mock-tools-loop"})
        assert len([e for e in ev if e["type"] == "tool_result"]) == 3 and any(e["type"] == "notice" and "Step limit" in e["text"] for e in ev)
        assert {"write_file", "read_file", "list_dir", "web_search"} <= {r["tool"] for r in db.list_tool_runs()}
        cfg(agent_max_steps=15)
        LOG.clear()
        c.chat({"text": "summarise our chat", "mode": "direct", "model_ref": "xai:mock-ok", "conv_id": cid})
        assert LOG and not any(MARKER in json.dumps(b) for _, _, b in LOG if b), "local data leaked to a cloud model"

    @test("7  ABORT works in DIRECT, COUNCIL and AGENT (kills the running command)")
    def _():
        def abort_after(n, box):
            cnt = {"n": 0}

            def on(ev):
                if ev["type"] == "start":
                    box["task"] = ev["task_id"]
                if ev["type"] in ("token", "member_token", "tool_request"):
                    cnt["n"] += 1
                    if cnt["n"] == n and not box.get("sent"):
                        box["sent"] = True
                        if ev["type"] == "tool_request":
                            c.post("/api/agent/decision", {"task_id": box["task"], "run_id": ev["run_id"], "decision": "allow"})
                            time.sleep(0.5)
                        c.post("/api/abort", {"task_id": box["task"]})
            return on
        t0 = time.time()
        ev = c.chat({"text": "go", "mode": "direct", "model_ref": "ollama:mock-slow"}, abort_after(3, {}))
        assert any(e["type"] == "aborted" for e in ev) and time.time() - t0 < 4
        c.post("/api/providers", {"id": "ollama", "model": "mock-slow"})
        cfg(council_lead="ollama:mock-lead", council_members=["ollama:mock-slow", "ollama:mock-slow"])
        t0 = time.time()
        ev = c.chat({"text": "go", "mode": "council", "model_ref": "ollama:mock-lead"}, abort_after(4, {}))
        assert any(e["type"] == "aborted" for e in ev) and time.time() - t0 < 5
        c.post("/api/providers", {"id": "ollama", "model": "mock-ok"})
        t0 = time.time()
        ev = c.chat({"text": "go", "mode": "agent", "model_ref": "ollama:mock-tools-cmd"}, abort_after(1, {}))
        assert any(e["type"] == "aborted" for e in ev) and time.time() - t0 < 8
        assert db.list_tool_runs(limit=1)[0]["status"] == "aborted"

    @test("8  /remember facts change later answers; /forget removes them")
    def _():
        q = {"text": "what is my name?", "mode": "direct", "model_ref": "ollama:mock-ok"}
        assert "do not know" in text_of(c.chat(q))
        mid = c.post("/api/memories", {"fact": "My name is Zorblax"})["id"]
        assert "Zorblax" in text_of(c.chat(q))
        c.post("/api/memories/delete", {"id": mid})
        assert "do not know" in text_of(c.chat(q))

    @test("9  attachments: images go to vision models; text, docx, pdf are read; deleting the upload deletes the extracted text")
    def _():
        import base64
        png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("word/document.xml", "<w:document><w:body><w:p><w:r><w:t>hello docx</w:t></w:r></w:p></w:body></w:document>")
        pdf = b"%PDF-1.4\n4 0 obj<</Length 60>>stream\nBT /F1 12 Tf 72 720 Td (Hello PDF world) Tj ET\nendstream endobj\n%%EOF"
        b64 = lambda d: base64.b64encode(d).decode()
        atts = [{"name": "pic.png", "b64": b64(png)}, {"name": "notes.txt", "b64": b64(b"hello from the text file")},
                {"name": "a.docx", "b64": b64(buf.getvalue())}, {"name": "b.pdf", "b64": b64(pdf)}, {"name": "tool.exe", "b64": b64(b"MZ\x00\x00binary")}]
        ev = c.chat({"text": "what is attached?", "mode": "direct", "model_ref": "ollama:mock-echo", "attachments": atts})
        assert "images=1 file_hello=True file_docx=True file_pdf=True" in text_of(ev), text_of(ev)
        assert any(e["type"] == "notice" and "tool.exe" in e["text"] for e in ev)
        cid = ev[0]["conv_id"]
        files = [f["path"] for f in c.jget("/api/files?conv=%d" % cid)["files"]]
        assert "uploads/pic.png" in files and "uploads/notes.txt" in files
        assert c.get("/api/file?conv=%d&path=uploads/pic.png&img=1" % cid)[:4] == b"\x89PNG"
        ev2 = c.chat({"text": "and now?", "mode": "direct", "model_ref": "ollama:mock-echo", "conv_id": cid})
        hist = [b for p, _, b in LOG if b and b.get("model") == "mock-echo"][-1]["messages"]
        assert any(m["role"] == "user" and "hello from the text file" in str(m["content"]) for m in hist[:-1]), "extracted file text was not re-attached to its turn"
        assert "hello from the text file" not in str(hist[-1]["content"])
        c.post("/api/file/delete", {"conv": cid, "path": "uploads/notes.txt"})
        metas = json.dumps([m["meta"] for m in c.jget("/api/conversation?id=%d" % cid)["messages"]])
        assert "hello from the text file" not in metas and "notes.txt" not in metas, "extracted text of a deleted upload survived"

    @test("10 web research: auto-triggers on a news question, reads pages, strips page noise, stays in-app, SSRF blocked")
    def _():
        c.post("/api/providers", {"id": "tavily", "base_url": base + "/tavily"})
        r = c.post("/api/keys/add", {"key": TAV_KEY})
        assert r["provider"] == "tavily" and r["ok"], r
        cfg(web_mode="auto", web_depth="read", web_reader=False)
        LOG.clear()
        ev = c.chat({"text": "what is happening in strait of hormuz war", "mode": "direct", "model_ref": "ollama:mock-research"})
        kinds = [e["type"] for e in ev]
        assert "research_start" in kinds and "search" in kinds and kinds.count("research_page") == 3, kinds
        pages = {e["i"]: e for e in ev if e["type"] == "research_page"}
        assert pages[0]["ok"] and pages[1]["ok"] and not pages[2]["ok"] and "JavaScript" in pages[2]["error"]
        assert "HORMUZ-FACT-ALPHA" in pages[0]["text"] and "NOISE" not in pages[0]["text"]
        assert "I read 3 sources. alpha=True beta=True noise=False" in text_of(ev), text_of(ev)
        tav = [b for p, _, b in LOG if p == "/tavily/search"][0]
        assert tav["topic"] == "news", "news question did not use the news endpoint"
        msgs = c.jget("/api/conversation?id=%d" % ev[0]["conv_id"])["messages"]
        src = msgs[0]["meta"]["sources"]
        assert len(src) == 3 and "HORMUZ-FACT-ALPHA" in src[0]["text"] and src[2]["ok"] is False
        n = len(LOG)
        ev = c.chat({"text": "write a haiku about tea", "mode": "direct", "model_ref": "ollama:mock-ok"})
        assert "research_start" not in [e["type"] for e in ev], "researched an evergreen request"
        ev = c.chat({"text": "tell me about tea", "mode": "direct", "model_ref": "ollama:mock-research", "web": "on"})
        assert "research_start" in [e["type"] for e in ev]
        research.ALLOW_PRIVATE = False
        research._CACHE.clear()
        bad = research.fetch_page("http://127.0.0.1:%d/page/1" % mport)
        assert not bad["ok"] and "private" in bad["error"], bad
        assert not research.fetch_page("file:///etc/passwd")["ok"]
        research.ALLOW_PRIVATE = True
        cfg(web_mode="off")
        ev = c.chat({"text": "latest news please", "mode": "direct", "model_ref": "ollama:mock-ok"})
        assert "research_start" not in [e["type"] for e in ev]
        cfg(web_mode="auto")

    @test("11 video studio: Colab job runs, progress, file served with Range, cancel, delete removes file and record")
    def _():
        assert c.post("/api/keys/detect", {"key": "https://abc.trycloudflare.com?token=zz"})["candidates"][0]["id"] == "colab"
        c.post("/api/providers", {"id": "colab", "base_url": base + "/colab", "api_key": COLAB_TOKEN, "enabled": True})
        t = c.post("/api/providers/test", {"id": "colab"})
        assert t["ok"] and "Mock T4" in t["message"], t
        jid = c.post("/api/video/generate", {"backend": "colab", "prompt": "a robot walking", "steps": 5})["job"]
        for _ in range(60):
            j = [x for x in c.jget("/api/video/jobs")["jobs"] if x["id"] == jid][0]
            if j["status"] in ("done", "error"):
                break
            time.sleep(0.2)
        assert j["status"] == "done" and j["url"], j
        data = c.get(j["url"])
        assert data.startswith(b"\x00\x00\x00\x18ftyp")
        req = urllib.request.Request(c.base + j["url"], headers={"Range": "bytes=4-7"})
        with c.op.open(req) as r:
            assert r.status == 206 and r.read() == b"ftyp"
        jid2 = c.post("/api/video/generate", {"backend": "colab", "prompt": "cancel me"})["job"]
        c.post("/api/video/cancel", {"id": jid2})
        time.sleep(0.8)
        assert [x for x in c.jget("/api/video/jobs")["jobs"] if x["id"] == jid2][0]["status"] == "cancelled"
        path = os.path.join(videogen.video_dir(), jid + ".mp4")
        assert os.path.exists(path)
        left = c.post("/api/video/delete", {"id": jid})["jobs"]
        assert jid not in [x["id"] for x in left] and not os.path.exists(path)
        os.makedirs(videogen.video_dir(), exist_ok=True)
        jid3 = c.post("/api/video/generate", {"backend": "colab", "prompt": "third"})["job"]
        time.sleep(1.8)
        os.remove(os.path.join(videogen.video_dir(), jid3 + ".mp4"))       # deleted outside NYXOR
        assert db.reconcile_files()["videos"] == 1
        assert db.get_video_job(jid3) is None, "stale video record kept"

    @test("12 voice: cloud TTS goes through the backend (key hidden); export/import round trip; matrix kinds")
    def _():
        c.post("/api/providers", {"id": "xai", "api_key": GOOD_KEY})
        with c.op.open(urllib.request.Request(c.base + "/api/tts", json.dumps({"provider": "xai", "text": "I am inevitable."}).encode(), {"Content-Type": "application/json"})) as r:
            body = r.read()
            assert body.startswith(b"ID3") and GOOD_KEY.encode() not in body
        assert any(p.endswith("/audio/speech") and a == "Bearer " + GOOD_KEY for p, a, b in LOG)
        ex = json.loads(c.get("/api/export"))
        assert GOOD_KEY not in json.dumps(ex)
        n = c.post("/api/import", ex)["imported"]
        assert n["conversations"] >= 4 and db.get_provider("xai")["api_key"] == GOOD_KEY
        assert code_of(lambda: c.post("/api/import", {"nope": 1})) == 400
        mx = {m["id"]: (m["status"], m["kind"]) for m in c.jget("/api/matrix")["matrix"]}
        assert mx["cerebras"] == ("NO KEY", "llm") and mx["tavily"] == ("ONLINE", "search") and mx["colab"][1] == "video"
        bk = c.get("/api/vault/backup")
        assert not bk.startswith(b"SQLite") and b"conversations" not in bk

    @test("14 COUNCIL candidates: every candidate is RUN headlessly; broken ones lose; slow ones are cancelled; reviewers + guarded repair; infinite runner ships")
    def _():
        have_node = bool(shutil.which("node"))
        c.post("/api/providers", {"id": "xai", "enabled": True, "model": "mock-ok"})
        cfg(council_strategy="auto", council_lead="ollama:mock-lead-goodfix", council_grace=1, council_review=True, council_fix_rounds=2, council_auto=True,
            council_members=["ollama:mock-cand-syntax", "ollama:mock-cand-runtime", "xai:mock-cand-good", "groq:mock-cand-slow"])
        t0 = time.time()
        ev = c.chat({"text": "make an infinite runner game", "mode": "council", "model_ref": "ollama:mock-ok"})
        dt = time.time() - t0
        k = lambda t: [e for e in ev if e["type"] == t]
        assert k("council_strategy")[0]["strategy"] == "candidates" and k("council_strategy")[0]["recipe"] == "game"
        assert not k("error"), k("error")
        cand = {e["idx"]: e for e in k("council_candidate")}
        assert cand[2]["valid"] is True and cand[2]["score"] >= 85, cand
        if have_node:
            assert not cand[0]["valid"] and any("syntax" in p for p in cand[0]["problems"]), cand[0]
            assert not cand[1]["valid"] and any("runtime" in p for p in cand[1]["problems"]), "the runtime crash was not caught: %s" % cand[1]
            assert k("council_winner")[0]["idx"] == 2
        st = {(e["idx"], e["status"]) for e in k("member_status")}
        assert (3, "cancelled") in st and dt < 7.5, "slow member was not cancelled early (%.1fs)" % dt
        assert k("council_review"), "no reviewer ran"
        rep_ev = k("council_repair")
        assert rep_ev and rep_ev[0]["accepted"] is True, rep_ev
        cid = ev[0]["conv_id"]
        final = open(os.path.join(W.conv_dir(cid), "index.html"), encoding="utf-8").read()
        assert "<!-- polished -->" in final and "requestAnimationFrame" in final
        text = text_of(ev)
        assert "Built **index.html**" in text and "Winner: xai:mock-cand-good" in text, text
        assert [f["path"] for e in k("files") for f in e["files"]] == ["index.html"]
        assert k("done")

    @test("15 COUNCIL guard: a repair that would break a working game is rejected and the working version is kept")
    def _():
        cfg(council_lead="ollama:mock-lead-badfix", council_members=["xai:mock-cand-good", "ollama:mock-cand-syntax"], council_grace=30)
        ev = c.chat({"text": "make a platformer game", "mode": "council", "model_ref": "ollama:mock-ok"})
        rep_ev = [e for e in ev if e["type"] == "council_repair"]
        assert rep_ev and rep_ev[0]["accepted"] is False, rep_ev
        cid = ev[0]["conv_id"]
        final = open(os.path.join(W.conv_dir(cid), "index.html"), encoding="utf-8").read()
        assert final.strip() == GAME.strip() and "polished" not in final
        assert "rejected" in text_of(ev)

    @test("16 pool efficiency: failure streak circuit breaker, member stats + reset, auto-pool, one local slot at a time")
    def _():
        cfg(council_lead="ollama:mock-lead-goodfix", council_members=["xai:mock-500", "xai:mock-cand-good"], council_review=False, council_fix_rounds=0, council_grace=30)
        for _ in range(3):
            c.chat({"text": "make a pong game", "mode": "council", "model_ref": "ollama:mock-ok"})
        stats = c.jget("/api/council/stats")["stats"]
        assert stats["xai:mock-500"]["streak"] >= 3 and stats["xai:mock-cand-good"]["valid"] >= 3, stats
        ev = c.chat({"text": "make a pong game", "mode": "council", "model_ref": "ollama:mock-ok"})
        assert (0, "skipped") in {(e["idx"], e["status"]) for e in ev if e["type"] == "member_status"}, "circuit breaker did not skip the failing member"
        assert c.post("/api/council/stats/reset", {})["stats"] == {} and c.jget("/api/council/stats")["stats"] == {}
        cfg(council_members=[], council_strategy="merge", council_lead="ollama:mock-lead")
        ev = c.chat({"text": "plan a launch", "mode": "council", "model_ref": "ollama:mock-ok"})
        assert any(e["type"] == "notice" and "Auto-pool picked" in e["text"] for e in ev) and any(e["type"] == "done" for e in ev), "auto-pool"
        cfg(council_strategy="auto")
        # limiter: a local provider admits one request at a time, a cloud provider three
        a, b = db.get_provider("ollama"), db.get_provider("xai")
        got = []
        with P.slot(a), P.slot(b), P.slot(b), P.slot(b):
            for p in (a, b):
                s = P._sems[p["id"]]
                got.append(s.acquire(blocking=False))
                if got[-1]:
                    s.release()
        assert got == [False, False], got

    @test("17 research+: deep multi-query, dates, JSON-LD, reader fallback, pasted links, keyless Bing/DDG chain, merge rules")
    def _():
        import websearch
        research._CACHE.clear()
        cfg(web_mode="on", web_depth="deep", web_reader=False)
        LOG.clear()
        ev = c.chat({"text": "tell me about the strait of hormuz", "mode": "direct", "model_ref": "ollama:mock-research"})
        start = [e for e in ev if e["type"] == "research_start"][0]
        qs = {b["query"] for p, _, b in LOG if p == "/tavily/search"}
        assert len(start["queries"]) >= 3 and len(qs) >= 3, (start["queries"], qs)
        pages = {e["i"]: e for e in ev if e["type"] == "research_page"}
        assert len(pages) == 3 and pages[1]["date"].startswith("2026-11-09T09:30"), "page date was not extracted: %s" % {i: (e["date"], e["ok"]) for i, e in pages.items()}
        assert "I read 3 sources" in text_of(ev)
        # JSON-LD article body + date
        j = research.fetch_page(base + "/page/4")
        assert j["ok"] and "JSONLD-BODY" in j["text"] and j["date"].startswith("2026-11-09"), j
        # reader fallback turns the JavaScript-only page into text
        research._CACHE.clear()
        research.READER_BASE = base + "/jina/"
        cfg(web_reader=True, web_depth="read")
        ev = c.chat({"text": "tell me about hormuz", "mode": "direct", "model_ref": "ollama:mock-research"})
        p3 = [e for e in ev if e["type"] == "research_page" and e["i"] == 2][0]
        assert p3["ok"] and p3["via"] == "reader" and "READER-TEXT" in p3["text"], p3
        # a link you paste is read, even for an evergreen question
        cfg(web_mode="auto", web_reader=False)
        ev = c.chat({"text": "summarise %s/page/1 for me" % base, "mode": "direct", "model_ref": "ollama:mock-research"})
        st = [e for e in ev if e["type"] == "research_start"][0]
        assert st["provider"] == "direct links" and "alpha=True" in text_of(ev), (st, text_of(ev))
        cfg(web_mode="off")
        assert "research_start" not in [e["type"] for e in c.chat({"text": "summarise %s/page/1" % base, "mode": "direct", "model_ref": "ollama:mock-ok"})]
        # keyless: no search key at all -> Bing News RSS for news, DuckDuckGo otherwise, chain on failure
        c.post("/api/providers", {"id": "tavily", "api_key": "", "enabled": False})
        cfg(search_provider="", web_mode="on", web_depth="read", web_keyless=True)
        websearch.BING["base_url"], websearch.DDG["base_url"] = base + "/bing", base + "/ddg"
        research._CACHE.clear()
        ev = c.chat({"text": "what is happening in the strait of hormuz war", "mode": "direct", "model_ref": "ollama:mock-research"})
        st = [e for e in ev if e["type"] == "research_start"][0]
        res = [e for e in ev if e["type"] == "search"][0]["results"]
        assert st["provider"] == "Bing News (no key)" and [r["url"].endswith("/page/1") for r in res][0], (st, res)
        assert "alpha=True" in text_of(ev)
        ev = c.chat({"text": "tell me about tea", "mode": "direct", "model_ref": "ollama:mock-research"})
        assert [e for e in ev if e["type"] == "research_start"][0]["provider"] == "DuckDuckGo (no key)"
        websearch.BING["base_url"] = "http://127.0.0.1:1"
        ev = c.chat({"text": "what is happening in the strait of hormuz war", "mode": "direct", "model_ref": "ollama:mock-research"})
        assert any(e["type"] == "notice" and "DuckDuckGo" in e["text"] for e in ev) and "alpha=True" in text_of(ev), "keyless chain did not fall back"
        # merge rules
        L = lambda *u: [{"title": x, "url": "https://%s" % x, "snippet": "", "date": ""} for x in u]
        m = research.merge_results([L("a.com/1", "a.com/2", "a.com/3"), L("b.com/1", "a.com/1/")], 5, False, 2)
        assert [r["url"] for r in m] == ["https://a.com/1", "https://b.com/1", "https://a.com/2"], m
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        old = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 86400 * 3))
        news = [dict(r, date=d) for r, d in zip(L("x.com/old", "y.com/new"), (old, now))]
        assert research.merge_results([news], 5, True)[0]["url"] == "https://y.com/new"
        cfg(web_mode="off", web_depth="read")
        c.post("/api/providers", {"id": "tavily", "api_key": TAV_KEY, "enabled": True})

    @test("18 in-app preview: HTML is bundled and served inside a sandbox with no network and no access to your data")
    def _():
        cid = c.post("/api/conversation/new", {})["id"]
        c.post("/api/files/save", {"conv_id": cid, "path": "game/index.html", "content": '<!doctype html><html><head><link rel="stylesheet" href="style.css"></head><body><canvas id=c></canvas><script src="main.js"></script></body></html>'})
        c.post("/api/files/save", {"conv_id": cid, "path": "game/main.js", "content": "var SENTINEL_JS = 1; try { localStorage.setItem('a','b'); } catch(e) {}"})
        c.post("/api/files/save", {"conv_id": cid, "path": "game/style.css", "content": "body{background:#123456}"})
        with c.op.open(c.base + "/api/preview?conv=%d&path=game/index.html" % cid) as r:
            csp, page = r.headers["Content-Security-Policy"], r.read().decode()
        assert "sandbox allow-scripts" in csp and "allow-same-origin" not in csp and "connect-src 'none'" in csp, csp
        assert "SENTINEL_JS" in page and "#123456" in page and 'src="main.js"' not in page and "defineProperty(window,'localStorage'" in page, page
        assert code_of(lambda: c.get("/api/preview?conv=%d&path=game/main.js" % cid)) == 404
        assert code_of(lambda: c.get("/api/preview?conv=%d&path=../../x.html" % cid)) == 404
        assert code_of(lambda: anon.get("/api/preview?conv=%d&path=game/index.html" % cid)) == 423

    @test("20 router: auto picks per message and shows why; private chats stay local; fallback only before the first token; no cloud escalation unless allowed; records and cool-down")
    def _():
        import router
        cfg(web_mode="off", router_mode="auto", router_fallback=True, router_fallback_cloud=False, router_pool=["ollama:mock-ok", "xai:mock-ok"])
        router.reset_stats()
        # name heuristics and task classification
        assert router.classify("hi")["complexity"] == "light"
        assert router.classify("Please design and implement a compiler for a small language. " * 12)["complexity"] == "heavy"
        assert router.classify("what is this?", [{"name": "a.PNG"}])["images"]
        assert router.capability("llama3.1:8b") == 1 and router.capability("gpt-4o-mini") == 1 and router.capability("claude-3-5-sonnet") == 3
        assert router.capability("llama-3.3-70b-versatile") == 3 and router.capability("mock-ok") == 2
        # a light message goes to the local model even though a cloud model is selected, and the UI is told why
        n = len(LOG)
        ev = c.chat({"text": "hello there", "mode": "direct", "model_ref": "xai:mock-ok"})
        rt = [e for e in ev if e["type"] == "route"]
        assert rt and rt[0]["chosen"] == "ollama:mock-ok" and "light" in rt[0]["line"] and rt[0]["ranked"], rt
        assert text_of(ev).startswith("Mock reply to: hello there")
        assert all(p.startswith("/local") for p, _, b in LOG[n:] if b and b.get("messages")), "a light message left the PC"
        done = [e for e in ev if e["type"] == "done"][0]
        assert done["meta"]["route"]["auto"] and done["meta"]["route"]["chosen"] == "ollama:mock-ok"
        # heavy work and images move to a stronger cloud model; the preview endpoint shows the reasons and never a key
        c.post("/api/providers", {"id": "xai", "model": "gpt-5"})
        cfg(router_pool=["ollama:mock-ok", "xai:gpt-5"])
        heavy = "Please design and implement a compiler for a small language. " * 12
        pv = c.post("/api/router/preview", {"text": heavy, "model_ref": "ollama:mock-ok"})["decision"]
        assert pv["chosen"] == "xai:gpt-5" and pv["task"]["complexity"] == "heavy" and pv["ranked"][0]["why"], pv
        assert c.post("/api/router/preview", {"text": "hello"})["decision"]["chosen"] == "ollama:mock-ok"
        c.post("/api/providers", {"id": "xai", "model": "gpt-4o"})
        cfg(router_pool=["ollama:mock-ok", "xai:gpt-4o"])
        pv = c.post("/api/router/preview", {"text": "what is in this picture", "attachments": [{"name": "cat.png"}], "model_ref": "ollama:mock-ok"})["decision"]
        assert pv["chosen"] == "xai:gpt-4o" and any("may not read images" in x for r in pv["ranked"] for x in r["why"]), pv
        assert GOOD_KEY not in json.dumps(pv)
        c.post("/api/providers", {"id": "xai", "model": "mock-ok"})
        cfg(router_pool=["ollama:mock-ok", "xai:mock-ok"])
        # a chat that holds local-only (agent) output never reaches a cloud model, even when one is selected
        cid = db.create_conversation(mode="direct")
        db.add_message(cid, "assistant", "AGENT-ONLY " + MARKER, meta={"local_only": True})
        n = len(LOG)
        ev = c.chat({"text": "summarise that", "mode": "direct", "model_ref": "xai:mock-ok", "conv_id": cid})
        rt = [e for e in ev if e["type"] == "route"][0]
        assert rt["task"]["private"] and rt["chosen"] == "ollama:mock-ok" and any("local-only" in x["why"] for x in rt["excluded"]), rt
        assert not any(p.startswith("/cloud") for p, _, b in LOG[n:]), "private chat reached a cloud model"
        # fallback: a cloud model that fails before answering hands over to the next-best one (single retry upstream, then switch)
        cfg(router_mode="manual")
        n = len(LOG)
        ev = c.chat({"text": "fallback please", "mode": "direct", "model_ref": "xai:mock-500"})
        fb = [e for e in ev if e["type"] == "fallback"]
        assert fb and fb[0]["from"] == "xai:mock-500" and fb[0]["to"] == "ollama:mock-ok" and "HTTP 500" in fb[0]["reason"], fb
        assert text_of(ev).startswith("Mock reply to: fallback please") and not [e for e in ev if e["type"] == "error"]
        assert sum(1 for p, _, b in LOG[n:] if b and b.get("model") == "mock-500") == 2, "provider was hammered"
        assert [e for e in ev if e["type"] == "done"][0]["meta"]["route"]["fallbacks"][0]["to"] == "ollama:mock-ok"
        # a failing LOCAL model does not silently send your message to the cloud; the switch needs your permission
        n = len(LOG)
        q = {"text": "stay home", "mode": "direct", "model_ref": "ollama:mock-500"}
        ev = c.chat(q)
        assert [e for e in ev if e["type"] == "error"] and not [e for e in ev if e["type"] == "fallback"]
        assert not any(p.startswith("/cloud") for p, _, b in LOG[n:]), "local failure escalated to cloud without permission"
        cfg(router_fallback_cloud=True)
        P._status.pop("xai", None)      # the mock fails one model; a real 5xx marks the whole provider down for a minute, which the router respects
        ev = c.chat(q)
        fb = [e for e in ev if e["type"] == "fallback"]
        assert fb and fb[0]["to"] == "xai:mock-ok" and text_of(ev).startswith("Mock reply to: stay home"), fb
        cfg(router_fallback_cloud=False)
        # a model that is switched off or has no key falls back too, with the reason shown
        was = [x for x in c.jget("/api/state")["providers"] if x["id"] == "groq"][0]["enabled"]
        c.post("/api/providers", {"id": "groq", "enabled": False})
        ev = c.chat({"text": "disabled one", "mode": "direct", "model_ref": "groq:mock-ok"})
        fb = [e for e in ev if e["type"] == "fallback"]
        c.post("/api/providers", {"id": "groq", "enabled": bool(was)})
        assert fb and "disabled" in fb[0]["reason"].lower() and text_of(ev).startswith("Mock reply to: disabled one"), fb
        # once tokens have appeared there is no second answer: the partial text stays and the error is shown
        orig = P.stream_chat

        def cut(p, model, messages, temperature=0.7, task=None, tools=None, max_tokens=None):
            yield "content", "partial answer "
            raise P.ProviderError("Stream from X broke: boom", 0, "network")
        P.stream_chat = cut
        try:
            ev = c.chat({"text": "cut it", "mode": "direct", "model_ref": "ollama:mock-ok"})
        finally:
            P.stream_chat = orig
        assert [e for e in ev if e["type"] == "error"] and not [e for e in ev if e["type"] == "fallback"] and "partial answer" in text_of(ev)
        # records are learned, a model that fails 3 times in a row cools down, and the browser cannot write the records
        st = c.jget("/api/router/stats")["stats"]
        assert st["xai:mock-500"]["fail"] >= 1 and st["ollama:mock-ok"]["ok"] >= 2 and st["ollama:mock-500"]["streak"] >= 1, st
        router.reset_stats()
        for _ in range(3):
            router.record("xai:mock-ok", False, kind="server")
        dec = router.decide("hello", [], "ollama:mock-ok")
        assert any(x["ref"] == "xai:mock-ok" and "cooling down" in x["why"] for x in dec["excluded"]) and all(x["ref"] != "xai:mock-ok" for x in dec["ranked"])
        c.post("/api/settings", {"router_stats": {"x": {"runs": 1}}})
        assert "x" not in c.jget("/api/router/stats")["stats"]
        assert code_of(lambda: c.post("/api/settings", {"router_mode": "bogus"})) == 400
        assert c.post("/api/router/stats/reset", {})["stats"] == {} and c.jget("/api/router/stats")["stats"] == {}
        cfg(router_mode="manual", router_pool=[], router_fallback=True)

    @test("21 telemetry: every reading is measured, events are real, nothing is simulated, polling does not keep the vault open")
    def _():
        import inspect
        import sys as _sys
        import telemetry
        t = c.jget("/api/telemetry")
        for k in ("cpu", "mem", "disk"):
            assert t[k] is None or 0 <= t[k] <= 100, (k, t[k])
        assert isinstance(t["uptime"], int) and t["uptime"] >= 0 and t["models"] >= 2 and t["models_online"] <= t["models"], t
        u = shutil.disk_usage(db.WORKSPACE)
        assert abs(t["disk"] - round(u.used / u.total * 100)) <= 1, "disk reading does not match the operating system"
        if _sys.platform.startswith("linux"):
            info = {l.split(":")[0]: int(l.split()[1]) for l in open("/proc/meminfo") if ":" in l}
            real = round((info["MemTotal"] - info["MemAvailable"]) / info["MemTotal"] * 100)
            assert abs(t["mem"] - real) <= 3, ("memory reading does not match /proc/meminfo", t["mem"], real)
        c.jget("/api/telemetry")
        time.sleep(0.3)
        assert c.jget("/api/telemetry")["cpu"] is not None or not _sys.platform.startswith("linux"), "CPU reading missing on Linux"
        last = max([e["id"] for e in t["events"]] or [0])
        c.chat({"text": "telemetry check", "mode": "direct", "model_ref": "ollama:mock-ok"})
        t2 = c.jget("/api/telemetry?since=%d" % last)
        assert any("answered by ollama:mock-ok" in e["text"] for e in t2["events"]), t2["events"]
        last2 = max(e["id"] for e in t2["events"])
        time.sleep(1.3)
        assert c.jget("/api/telemetry?since=%d" % last2)["events"] == [], "events appear while idle: something is simulated"
        before = srv_mod.LAST[0]
        c.jget("/api/telemetry")
        assert srv_mod.LAST[0] == before, "polling telemetry resets the auto-lock timer"
        assert "random" not in inspect.getsource(telemetry), "telemetry.py must not generate random data"

    @test("22 privacy: nothing leaves the PC without consent; Store build limits; reports; policy page; no third-party fonts; no simulated text left")
    def _():
        import modes as M
        import privacy
        import research
        import tts
        import videogen
        import websearch as S
        before_flows = c.jget("/api/state")["consent"]["flows"]
        c.post("/api/consent", {"flows": {}})
        cfg(router_mode="manual", router_fallback=True, router_pool=[])
        n = len(LOG)
        ev = c.chat({"text": "consent test", "mode": "direct", "model_ref": "xai:mock-ok"})
        fb = [e for e in ev if e["type"] == "fallback"]
        assert fb and "Not allowed yet" in fb[0]["reason"] and fb[0]["to"].startswith("ollama:"), fb
        assert text_of(ev).startswith("Mock reply to: consent test")
        assert not any(p.startswith("/cloud") for p, _, b in LOG[n:]), "a cloud request was made without consent"
        cfg(router_mode="auto", router_pool=["ollama:mock-ok", "xai:mock-ok"])
        pv = c.post("/api/router/preview", {"text": "hi"})["decision"]
        assert any("not allowed yet" in x["why"] for x in pv["excluded"]) and pv["chosen"] == "ollama:mock-ok", pv
        cfg(router_mode="manual", router_pool=[])
        for name, fn in (("web research", lambda: S.search(db.get_provider("tavily"), "x")), ("cloud voice", lambda: tts.synth(db.get_provider("openai"), "hello")),
                         ("cloud video", lambda: videogen.submit("replicate", "x", {}))):
            try:
                fn()
                raise AssertionError(name + " ran without consent")
            except P.ProviderError as e:
                assert "Not allowed yet" in str(e), (name, e)
        assert research.reader_fetch("http://127.0.0.1:1/", 100) is None, "the reader service was used without consent"
        st = c.jget("/api/state")
        assert st["consent"]["flows"]["voice_input"] is False and st["consent"]["v"] == privacy.CONSENT_VERSION and not st["consent"]["needs_prompt"]
        # the Store build is stricter
        real_store, kl = privacy.store, c.jget("/api/state")["settings"]["web_keyless"]
        orig_list = db.list_providers
        try:
            privacy.store = lambda: True
            assert code_of(lambda: c.post("/api/settings", {"agent_approval": "auto"})) == 400
            cfg(web_keyless=True)
            db.list_providers = lambda: []
            assert S.pick(news=False) is None and S.pick(news=True) is None, "the Store build scraped a search page"
            assert privacy.SAFETY in M.build_system()
            privacy.store = lambda: False
            assert S.pick(news=False)["id"] == "ddg", "the standard build lost its keyless fallback"
            assert privacy.SAFETY not in M.build_system()
        finally:
            privacy.store, db.list_providers = real_store, orig_list
            cfg(web_keyless=kl)
        c.post("/api/consent", {"flows": {k: True for k in privacy.FLOWS}})
        # reports (live generative AI content)
        cid = db.create_conversation(mode="direct")
        mid = db.add_message(cid, "assistant", "bad answer text", "", "ollama", "mock-ok")
        r1 = c.post("/api/report", {"conv_id": cid, "message_id": mid, "category": "harmful", "note": "n", "include_text": True})["id"]
        r2 = c.post("/api/report", {"conv_id": cid, "message_id": mid, "category": "nonsense", "include_text": False})["id"]
        rows = {x["id"]: x for x in c.jget("/api/reports")["reports"]}
        assert rows[r1]["excerpt"] == "bad answer text" and rows[r1]["category"] == "harmful" and rows[r2]["excerpt"] == "" and rows[r2]["category"] == "other", rows
        assert c.post("/api/report/delete", {"id": r1})["ok"] and r1 not in {x["id"] for x in c.jget("/api/reports")["reports"]}
        ev = c.chat({"text": "report button", "mode": "direct", "model_ref": "ollama:mock-ok"})
        assert [e for e in ev if e["type"] == "done"][0].get("message_id"), "the answer does not tell the UI its message id"
        # policy page, fonts, simulation
        page = anon.get("/privacy").decode()
        assert "privacy policy" in page.lower() and "{{" not in page and "r.jina.ai" in page
        index = anon.get("/").decode()
        assert "fontshare" not in index and "googleapis" not in index and "fontshare" not in srv_mod.CSP and "googleapis" not in srv_mod.CSP, "a third-party font service is still used"
        for f in ("core.js", "app.js", "index.html", "style.css"):
            src = open(os.path.join(srv_mod.ROOT, f), encoding="utf-8").read()
            for bad in ("randomEvent", "proxy chain rebuilt", "severing external", "origin masking", "now mine", "Override Root", "ghost protocol", "satellite uplink", "signature masked"):
                assert bad not in src, "simulated text left in %s: %s" % (f, bad)

    @test("23 single file: nyxor.pyz builds, runs from another folder, serves its own files, keeps data outside the file, applies the Store limits")
    def _():
        import socket
        import subprocess
        import sys as _sys
        import tempfile
        import build_single_file as B
        tmp = tempfile.mkdtemp(prefix="nyxpyz-")
        pyz = B.build(os.path.join(tmp, "out", "nyxor.pyz"), True, "help@example.test", "https://example.test/privacy", "Test Publisher")
        names = zipfile.ZipFile(pyz).namelist()
        assert "STORE_BUILD" in names and "app.js" in names and "smoke.js" in names and "selftest.py" not in names and "ui_harness.js" not in names
        assert "terminal.py" in names and "fonts/Inter-Regular.otf" in names and "fonts/LICENSES.txt" in names, "fonts or terminal missing from the single file"
        so = socket.socket()
        so.bind(("127.0.0.1", 0))
        port = so.getsockname()[1]
        so.close()
        home = os.path.join(tmp, "home")
        env = dict(os.environ, NYXOR_HOME=home, NYXOR_KDF_FAST="1")
        env.pop("NYXOR_DB", None)
        env.pop("NYXOR_WORKSPACE", None)
        proc = subprocess.Popen([_sys.executable, pyz, "--no-open", "--port", str(port)], cwd=tmp, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            k = Client(port)
            for _ in range(60):
                try:
                    k.jget("/api/vault/status")
                    break
                except Exception:
                    time.sleep(0.2)
            assert b"NYXOR" in k.get("/") and b"fontshare" not in k.get("/")
            assert len(k.get("/fonts/Inter-Regular.otf")) > 100000 and len(k.get("/fonts/JetBrainsMono-Regular.ttf")) > 50000, "bundled fonts are not served from the archive"
            assert code_of(lambda: k.get("/fonts/../server.py")) in (404, 400, 403)
            policy = k.get("/privacy").decode()
            assert "help@example.test" in policy and "Test Publisher" in policy and "{{" not in policy
            k.post("/api/vault/create", {"passphrase": PASS})
            st = k.jget("/api/state")
            assert st["store"]["enabled"] and st["store"]["support_email"] == "help@example.test" and st["consent"]["needs_prompt"], st["store"]
            assert os.path.exists(os.path.join(home, "nyxor.db")), "data must live in the data folder, not in or next to the archive"
            assert not os.path.exists(os.path.join(tmp, "out", "nyxor.db"))
            assert code_of(lambda: k.post("/api/settings", {"agent_approval": "auto"})) == 400, "AUTO agent approval is allowed in the Store build"
            assert k.jget("/api/telemetry")["models"] >= 0
            assert json.loads(k.get("/api/colab/notebook"))["nbformat"] == 4, "the bundled Colab notebook is not served from the archive"
            out = subprocess.run([_sys.executable, pyz, "--selftest"], cwd=tmp, env=env, capture_output=True, text=True, timeout=30)
            assert out.returncode != 0 and "project folder" in (out.stderr + out.stdout)
        finally:
            proc.terminate()
            try:
                proc.wait(10)
            except Exception:
                proc.kill()
            shutil.rmtree(tmp, ignore_errors=True)

    @test("24 terminal: off by default, confined to the allowed folders, no NYXOR secrets in the child, killable, logged; agent file writes show a diff")
    def _():
        import agent
        import tempfile
        os.environ["NYXOR_TEST_SECRET"] = "must-not-reach-the-child"

        def run(cmd, wait=8):
            r = c.post("/api/terminal/run", {"command": cmd})
            out, n = "", 0
            for _ in range(int(wait / 0.1)):
                p = c.jget("/api/terminal/output?id=%s&since=%d" % (r["id"], n))
                out, n = out + p["output"], p["n"]
                if p["done"]:
                    return out, p["code"]
                time.sleep(0.1)
            raise AssertionError("command did not finish: " + cmd)
        assert code_of(lambda: c.post("/api/terminal/run", {"command": "echo no"})) == 403, "the terminal must be off by default"
        assert c.jget("/api/state")["terminal"]["enabled"] is False
        cfg(terminal_enabled=True)
        st = c.jget("/api/state")
        assert st["terminal"]["enabled"] and os.path.realpath(st["terminal"]["cwd"]) == os.path.realpath(db.WORKSPACE), st["terminal"]
        out, rc = run("echo hello-terminal")
        assert "hello-terminal" in out and rc == 0, (out, rc)
        if os.name != "nt":
            out, _ = run("env | grep NYXOR || echo clean")
            assert "clean" in out and "must-not-reach-the-child" not in out, "a NYXOR_* variable reached the child process"
            out, _ = run("mkdir -p sub && echo ok")
            assert "ok" in out
            r = c.post("/api/terminal/run", {"command": "cd sub"})
            assert r["id"] is None and r["code"] == 0 and r["cwd"].endswith("sub"), r
            r = c.post("/api/terminal/run", {"command": "cd .."})
            assert r["code"] == 0
            r = c.post("/api/terminal/run", {"command": "cd .."})
            assert r["code"] == 1 and "blocked" in r["output"] and os.path.realpath(r["cwd"]) == os.path.realpath(db.WORKSPACE), r
            r = c.post("/api/terminal/run", {"command": "cd /"})
            assert r["code"] == 1 and "blocked" in r["output"], "the terminal left the allowed folders"
            started = c.post("/api/terminal/run", {"command": "sleep 30"})["id"]
            time.sleep(0.4)
            assert c.post("/api/terminal/kill", {"id": started})["ok"]
            t0, out = time.time(), ""
            while time.time() - t0 < 6:
                p = c.jget("/api/terminal/output?id=%s&since=0" % started)
                if p["done"]:
                    out = p["output"]
                    break
                time.sleep(0.1)
            assert "[stopped]" in out and time.time() - t0 < 5, "kill did not end the command"
        assert code_of(lambda: c.post("/api/terminal/run", {"command": "x" * 4001})) == 400
        before = srv_mod.LAST[0]
        c.jget("/api/terminal/output?id=none&since=0")
        assert srv_mod.LAST[0] == before, "polling terminal output keeps the vault unlocked"
        assert any("terminal command finished" in e["text"] for e in c.jget("/api/telemetry")["events"]), "terminal commands are not logged"
        cfg(terminal_timeout=9999)
        assert c.jget("/api/state")["settings"]["terminal_timeout"] == 600
        cfg(terminal_timeout=120, terminal_enabled=False)
        assert code_of(lambda: c.post("/api/terminal/run", {"command": "echo no"})) == 403
        # agent file writes show what will change
        d = tempfile.mkdtemp()
        f = os.path.join(d, "a.txt")
        open(f, "w").write("one\ntwo\nthree\n")
        diff = agent._diff(f, "one\nTWO\nthree\n")
        assert diff.startswith("--- a/a.txt") and "-two" in diff and "+TWO" in diff, diff
        assert agent._diff(f, "one\ntwo\nthree\n").startswith("(no change")
        assert agent._diff(os.path.join(d, "new.txt"), "hello").startswith("NEW FILE\nhello")
        shutil.rmtree(d, ignore_errors=True)

    @test("25 budgets: real token counts, prices, caps refuse before sending, override, reservations, router and fallback respect caps")
    def _():
        import budget
        import router
        cfg(web_mode="off", router_mode="manual", router_fallback=True, router_fallback_cloud=False, router_pool=[])
        c.post("/api/providers", {"id": "xai", "base_url": cloud, "enabled": True, "api_key": GOOD_KEY, "model": "mock-ok"})
        c.post("/api/budget/reset", {})
        db.set_settings({"budget": {}})
        P._NO_USAGE_OPTS.discard("xai")
        assert c.jget("/api/budget")["budget"]["totals"]["day"]["calls"] == 0
        # the browser can neither write the ledger nor the configuration through the generic settings route
        c.post("/api/settings", {"usage": {"days": {"2000-01-01": {"xai:x": {"in": 9}}}}, "budget": {"caps": {"xai": {"tok_day": 1}}}})
        assert not db.get_setting("usage").get("days") and not db.get_setting("budget").get("caps")
        # 1. a provider that reports usage: its numbers are booked as real, not estimated; no price yet -> tokens but no dollars, and it says so
        n = len(LOG)
        ev = c.chat({"text": "count my tokens", "mode": "direct", "model_ref": "xai:mock-usage"})
        assert text_of(ev).startswith("Mock reply to: count my tokens") and not [e for e in ev if e["type"] == "error"]
        assert any((b or {}).get("stream_options") == {"include_usage": True} for p, _, b in LOG[n:] if p.startswith("/cloud")), "NYXOR did not ask for token usage"
        px = [p for p in c.jget("/api/budget")["budget"]["providers"] if p["id"] == "xai"][0]
        assert px["day"]["in"] == 1000 and px["day"]["out"] == 500 and px["day"]["est"] == 0 and px["day"]["usd"] == 0 and px["day"]["nopr"] == 1, px
        assert px["models"]["mock-usage"]["day"]["calls"] == 1
        # 2. prices are the user's: validation, then dollars = tokens x price
        for bad in ({"prices": {"xai": {"in": -1, "out": 2}}}, {"prices": {"xai": {"in": "abc", "out": 2}}}, {"prices": {"nope": {"in": 1, "out": 1}}},
                    {"caps": {"xai": {"usd_day": 5}}}, {"caps": {"ghost": {"tok_day": 5}}}, {"action": "explode"}):
            assert code_of(lambda: c.post("/api/budget", bad)) == 400, bad
        r = c.post("/api/budget", {"prices": {"xai": {"in": 3, "out": 15}}})["budget"]
        assert [p for p in r["providers"] if p["id"] == "xai"][0]["price"] == {"in": 3.0, "out": 15.0}
        c.chat({"text": "second", "mode": "direct", "model_ref": "xai:mock-usage"})
        d = [p for p in c.jget("/api/budget")["budget"]["providers"] if p["id"] == "xai"][0]["day"]
        assert abs(d["usd"] - 0.0105) < 1e-9 and d["nopr"] == 1 and d["calls"] == 2, d       # the first call keeps its "no price" mark
        # 3. a provider that reports nothing: tokens are an ESTIMATE and are counted as such
        ev = c.chat({"text": "estimate me", "mode": "direct", "model_ref": "xai:mock-ok"})
        reply = text_of(ev)
        row = budget._rows(budget.today())["xai:mock-ok"]
        assert row["est"] == 1 and row["out"] == budget.estimate_text(len(reply)) and row["in"] > 0, (row, reply)
        # local servers are free and still metered; Anthropic's own counts are used
        c.chat({"text": "local please", "mode": "direct", "model_ref": "ollama:mock-ok"})
        assert budget._rows(budget.today())["ollama:mock-ok"]["usd"] == 0 and budget.price_for("ollama", "mock-ok") == (0.0, 0.0)
        c.post("/api/providers", {"id": "anthropic", "base_url": base + "/anth/v1", "api_key": ANT_KEY, "model": "claude-mock", "enabled": True})
        c.chat({"text": "hi claude", "mode": "direct", "model_ref": "anthropic:claude-mock"})
        ar = budget._rows(budget.today())["anthropic:claude-mock"]
        assert ar["in"] == 200 and ar["out"] == 50 and ar["est"] == 0, ar
        # 4. a server that rejects stream_options still works: the request is repeated without it and NYXOR stops asking that provider
        n = len(LOG)
        ev = c.chat({"text": "old server", "mode": "direct", "model_ref": "xai:mock-noopts"})
        assert text_of(ev).startswith("Mock reply to: old server") and not [e for e in ev if e["type"] == "error"]
        sent = [b for p, _, b in LOG[n:] if p.startswith("/cloud") and b and b.get("model") == "mock-noopts"]
        assert len(sent) == 2 and "stream_options" in sent[0] and "stream_options" not in sent[1] and "xai" in P._NO_USAGE_OPTS, sent
        P._NO_USAGE_OPTS.discard("xai")
        # 5. a token cap refuses BEFORE anything is sent, says why, and is not blamed on the model
        cfg(router_fallback=False)
        c.post("/api/budget", {"caps": {"xai": {"tok_day": 1}}, "action": "ask"})
        router.reset_stats()
        n = len(LOG)
        ev = c.chat({"text": "over the cap", "mode": "direct", "model_ref": "xai:mock-usage"})
        er = [e for e in ev if e["type"] == "error"]
        assert er and er[0]["kind"] == "budget" and er[0]["provider"] == "xai" and er[0]["action"] == "ask" and "OVERRIDE TODAY" in er[0]["message"] and "daily token cap" in er[0]["message"], er
        assert not any(p.startswith("/cloud") for p, _, b in LOG[n:]), "a request was sent although the cap was reached"
        assert "xai:mock-usage" not in router.stats(), "a budget refusal was recorded as a model failure"
        # the same gate protects every other caller: council members, research query writing, TEST
        try:
            P.complete(db.get_provider("xai"), "mock-ok", [{"role": "user", "content": "hi"}])
            raise AssertionError("complete() ignored the cap")
        except P.ProviderError as e:
            assert e.kind == "budget"
        # 6. with fallback on, a capped provider hands over to the next-best model and the UI is told
        cfg(router_fallback=True)
        P._status.pop("xai", None)
        n = len(LOG)
        ev = c.chat({"text": "capped then fallback", "mode": "direct", "model_ref": "xai:mock-usage"})
        fb = [e for e in ev if e["type"] == "fallback"]
        assert fb and fb[0]["from"] == "xai:mock-usage" and fb[0]["to"] == "ollama:mock-ok" and "cap" in fb[0]["reason"], fb
        assert text_of(ev).startswith("Mock reply to: capped then fallback") and not any(p.startswith("/cloud") for p, _, b in LOG[n:])
        # 7. AUTO routing does not even consider a capped provider
        cfg(router_mode="auto", router_pool=["ollama:mock-ok", "xai:mock-ok"])
        pv = c.post("/api/router/preview", {"text": "design and implement a compiler " * 30, "model_ref": "xai:mock-ok"})["decision"]
        assert pv["chosen"] == "ollama:mock-ok" and any("spend cap" in x["why"] for x in pv["excluded"]), pv
        cfg(router_mode="manual", router_pool=[])
        # 8. OVERRIDE TODAY lets requests through again, only for today
        ov = c.post("/api/budget/override", {"provider": "xai"})["budget"]
        px = [p for p in ov["providers"] if p["id"] == "xai"][0]
        assert px["override"] is True and px["capped"] is False, px
        cfg(router_fallback=False)
        ev = c.chat({"text": "override works", "mode": "direct", "model_ref": "xai:mock-ok"})
        assert text_of(ev).startswith("Mock reply to: override works") and not [e for e in ev if e["type"] == "error"]
        stale = budget.config()
        stale["overrides"] = {"xai": "2000-01-01"}
        db.set_settings({"budget": stale})
        assert budget.capped_now("xai") and budget.blocked("xai"), "an override from an earlier day must not count"
        ev = c.chat({"text": "yesterday's override", "mode": "direct", "model_ref": "xai:mock-ok"})
        assert [e for e in ev if e["type"] == "error" and e["kind"] == "budget"], "an old override still worked"
        # 9. a dollar cap: the answer cannot be predicted, so one call may overshoot, the next one is refused
        c.post("/api/budget/reset", {})
        c.post("/api/budget", {"caps": {"xai": {"usd_day": 0.005}}})
        c.post("/api/budget/override", {"provider": "xai", "on": False})
        ev = c.chat({"text": "first", "mode": "direct", "model_ref": "xai:mock-usage"})
        assert not [e for e in ev if e["type"] == "error"]
        ev = c.chat({"text": "second", "mode": "direct", "model_ref": "xai:mock-usage"})
        er = [e for e in ev if e["type"] == "error"]
        assert er and er[0]["kind"] == "budget" and "daily spend cap" in er[0]["message"] and "$" in er[0]["message"], ev
        # 10. calls running at the same moment are reserved for their input, so parallel council members cannot all slip under a cap
        c.post("/api/budget/reset", {})
        c.post("/api/budget", {"caps": {"xai": {"tok_day": 1000}}})
        big = [{"role": "user", "content": "x" * 2400}]            # about 600 tokens
        xp = db.get_provider("xai")
        t1 = budget.gate(xp, "mock-ok", big)
        try:
            budget.gate(xp, "mock-ok", big)
            raise AssertionError("two parallel 600-token calls both passed a 1000-token cap")
        except P.ProviderError as e:
            assert e.kind == "budget"
        budget.record(t1, xp, "mock-ok", {}, 0)                     # nothing came back: nothing is booked, the reservation is released
        assert budget.used("xai")["day"]["tok"] == 0
        t2 = budget.gate(xp, "mock-ok", big)
        budget.record(t2, xp, "mock-ok", {"prompt_tokens": 600, "completion_tokens": 50}, 0)
        assert budget.used("xai")["day"]["tok"] == 650
        # 11. the cap and the meter never leak a key, the status reading carries the meter, and the ledger can be cleared
        snap = c.jget("/api/telemetry")
        assert snap["usage"]["tokens"] >= 650 and snap["usage"]["cap_pct"] == 65, snap["usage"]
        assert GOOD_KEY not in json.dumps(c.jget("/api/budget")) and ANT_KEY not in json.dumps(c.jget("/api/budget"))
        c.post("/api/budget/reset", {})
        assert c.jget("/api/budget")["budget"]["totals"]["day"]["calls"] == 0
        c.post("/api/budget", {"caps": {"xai": {}}, "prices": {"xai": {}}})
        assert budget.config()["caps"] == {} and budget.config()["prices"] == {}
        cfg(router_fallback=True)

    @test("19 security: Host/Origin checks, session cookie required, change passphrase invalidates the old one, auto-lock")
    def _():
        h = http.client.HTTPConnection("127.0.0.1", srv.server_address[1])
        h.request("GET", "/api/vault/status", headers={"Host": "evil.example.com"})
        assert h.getresponse().status == 403
        h = http.client.HTTPConnection("127.0.0.1", srv.server_address[1])
        h.request("POST", "/api/settings", body="{}", headers={"Origin": "http://evil.example.com", "Content-Type": "application/json"})
        assert h.getresponse().status == 403
        assert code_of(lambda: c.post("/api/vault/passphrase", {"old": "nope nope nope", "new": "another long phrase"})) == 400
        c.post("/api/vault/passphrase", {"old": PASS, "new": "another long phrase"})
        c.post("/api/vault/lock", {})
        assert code_of(lambda: c.post("/api/vault/unlock", {"secret": PASS})) == 401
        c.post("/api/vault/unlock", {"secret": "another long phrase"})
        code = c.post("/api/vault/recovery", {"passphrase": "another long phrase"})["recovery_code"]
        assert code != c.recovery
        c.post("/api/vault/lock", {})
        assert code_of(lambda: c.post("/api/vault/unlock", {"secret": c.recovery})) == 401, "old recovery code still works"
        c.post("/api/vault/unlock", {"secret": code})
        cfg(auto_lock_minutes=1)
        srv_mod.LAST[0] -= 120
        time.sleep(1.2)
        assert not db.is_unlocked() and code_of(lambda: c.get("/api/state")) == 423, "auto-lock did not trigger"

    srv.shutdown()
    mock.shutdown()
    print("\nNYXOR selftest")
    print("=" * 100)
    for name, ok, info in results:
        print("%s  %s  %s" % ("PASS" if ok else "FAIL", name, "(%s)" % info if ok else "\n      " + info))
    bad = [r for r in results if not r[1]]
    print("=" * 100)
    print("%d/%d passed" % (len(results) - len(bad), len(results)))
    shutil.rmtree(os.path.dirname(db.DB_PATH), ignore_errors=True)
    return 1 if bad else 0
