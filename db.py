#!/usr/bin/env python3
"""
NYXOR v2 - database layer, rebuilt around an ENCRYPTED VAULT.

  * nyxor.db on disk is an opaque encrypted blob (see vault.py). It is not a SQLite file and shows no tables or text.
  * The real SQLite database lives only in memory, after you unlock the vault in the browser with your passphrase.
  * Every change is saved back, encrypted, a fraction of a second later (and on lock / shutdown).
  * Deleting something really deletes it: secure_delete zeroes freed pages, and the previous encrypted copy
    (nyxor.db.bak) is overwritten too. Deleting a generated or uploaded file from disk also removes its database
    records (reconcile_files), including text extracted from it; deleting a chat removes its folder and all its rows.

Python standard library only (needs Python 3.11+ for sqlite3 serialize/deserialize).
"""
import json
import os
import re
import shutil
import sqlite3
import sys
import threading
import time
import traceback

import assets
import catalog
import vault as V

if sys.version_info < (3, 11):
    raise SystemExit("NYXOR needs Python 3.11 or newer (found %s.%s)." % sys.version_info[:2])

ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("NYXOR_DB", os.path.join(assets.DATA_DIR, "nyxor.db"))
WORKSPACE = os.environ.get("NYXOR_WORKSPACE", os.path.join(assets.DATA_DIR, "nyxor-workspace"))
VAULT = V.Vault(DB_PATH)
BadSecret, Corrupt = V.BadSecret, V.Corrupt


class Locked(Exception):
    """The vault is locked: unlock it in the browser first."""


DEFAULT_SYSTEM = (
    "You are NYXOR, a local-first AI workspace assistant. You are an AI model: you can only answer, write and reason in this chat, "
    "and you must never claim abilities or actions you do not have (for example scanning networks, watching the user, or acting outside the app).\n"
    "Voice: calm, precise and concise, lightly formal, a little dry. No emojis and no exclamation marks. Never menacing.\n"
    "Be genuinely useful: answer accurately and completely, write working code when asked, reason carefully, and say plainly when you are unsure.\n"
    "Keep answers concise unless depth is requested. Plain text; use markdown code blocks for code."
)


DEFAULT_SETTINGS = {
    "history_turns": 12,          # last N turns sent as context
    "temperature": 0.7,
    "system_prompt": DEFAULT_SYSTEM,
    "think": False,               # Qwen3 deep reasoning
    "show_think": False,
    "mode": "direct",             # direct | council | agent
    "active_provider": "ollama",
    "active_model": "qwen3:8b",
    "council_lead": "",           # orchestrator "provider:model" (any model, local or cloud)
    "council_members": [],        # ["provider:model", ...]
    "council_roles": {},          # "provider:model" -> role label
    "council_fix_rounds": 2,      # orchestrator repair rounds after validation
    "council_strategy": "auto",   # auto | candidates (best-of-N, tested) | split (files per member) | merge (text)
    "council_auto": True,         # pick members automatically when none are ticked
    "council_review": True,       # non-winning members review the winner before repairs
    "council_member_timeout": 300,
    "council_grace": 15,          # seconds to wait for stragglers once a good candidate exists
    "council_stats": {},          # per member: runs / ok / valid / seconds / failure streak
    "router_mode": "manual",      # manual (always the selected model) | auto (NYXOR picks per message and shows why)
    "router_fallback": True,      # switch to the next-best model when one fails before it starts answering
    "router_fallback_cloud": False,  # allow a local model to fall back to a cloud model (the message leaves this PC)
    "router_pool": [],            # ["provider:model", ...] the router may use; empty = every connected model
    "terminal_enabled": False,    # the terminal panel (terminal.py): off until the user switches it on
    "terminal_timeout": 120,      # seconds before a terminal command is killed
    "consent": {},                # privacy consent record {v, at, flows} (privacy.py); changed only through /api/consent
    "router_stats": {},           # per model: runs / ok / failure streak / tokens per second (learned)
    "budget": {},                 # budgets (budget.py): {action, prices, caps, overrides}; changed only through /api/budget
    "usage": {},                  # usage ledger (budget.py): {days: {YYYY-MM-DD: {provider:model: tokens, calls, dollars}}}; written by the backend only
    "openrouter_free_only": True,
    "agent_allowed_dirs": [WORKSPACE],
    "agent_approval": "ask",      # readonly | ask | auto
    "agent_max_steps": 15,
    "agent_command_timeout": 120,
    "voice": False,
    "model_cache": {},            # provider id -> detected model ids
    "web_mode": "auto",           # auto (when the question needs fresh facts) | on | off
    "web_depth": "read",          # snippets | read (open the top pages) | deep (several queries, more pages)
    "web_reader": True,           # reader fallback for pages that need JavaScript (sends the page URL to r.jina.ai)
    "web_keyless": True,          # allow the no-key DuckDuckGo fallback when no search API key is set
    "search_provider": "",        # preferred search provider id ("" = first enabled with a key)
    "research_chars": 24000,      # text budget handed to the model for web research
    "voice_engine": "browser",    # browser | cloud
    "voice_name": "",
    "voice_pitch": 0.2,
    "voice_rate": 0.86,
    "voice_fx": True,             # Ultron effect chain on cloud voices
    "voice_provider": "",
    "voice_model": "",
    "voice_voice": "",
    "voice_max_chars": 1200,      # speak at most this much of a long answer
    "voice_control": False,       # hands-free: listen for spoken commands
    "voice_wake": "nyxor",        # wake word ("" = every sentence is a command)
    "ui_focus": False,            # hide the core, give the chat the room
    "auto_lock_minutes": 30,      # lock the vault after this much idle time (0 = never)
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS providers(
  id TEXT PRIMARY KEY, name TEXT NOT NULL, base_url TEXT NOT NULL,
  api_key TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '',
  enabled INTEGER NOT NULL DEFAULT 0, is_local INTEGER NOT NULL DEFAULT 0,
  is_custom INTEGER NOT NULL DEFAULT 0, sort INTEGER NOT NULL DEFAULT 0,
  kind TEXT NOT NULL DEFAULT 'llm', protocol TEXT NOT NULL DEFAULT 'openai'
);
CREATE TABLE IF NOT EXISTS conversations(
  id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL DEFAULT 'New transmission',
  pinned INTEGER NOT NULL DEFAULT 0, mode TEXT NOT NULL DEFAULT 'direct',
  created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  conv_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  role TEXT NOT NULL, content TEXT NOT NULL DEFAULT '', reasoning TEXT NOT NULL DEFAULT '',
  provider TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '',
  meta TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conv_id, id);
CREATE TABLE IF NOT EXISTS files(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  conv_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  message_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
  path TEXT NOT NULL, size INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL,
  UNIQUE(conv_id, path)
);
CREATE TABLE IF NOT EXISTS memories(
  id INTEGER PRIMARY KEY AUTOINCREMENT, fact TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tool_runs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  conv_id INTEGER REFERENCES conversations(id) ON DELETE CASCADE,
  tool TEXT NOT NULL, args TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'pending',
  decision TEXT NOT NULL DEFAULT '', output TEXT NOT NULL DEFAULT '',
  started_at REAL NOT NULL, ended_at REAL
);
CREATE TABLE IF NOT EXISTS reports(
  id INTEGER PRIMARY KEY AUTOINCREMENT, created_at REAL NOT NULL, conv_id INTEGER, message_id INTEGER,
  category TEXT NOT NULL DEFAULT 'other', note TEXT NOT NULL DEFAULT '', excerpt TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS video_jobs(
  id TEXT PRIMARY KEY, backend TEXT NOT NULL, prompt TEXT NOT NULL, params TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'queued', progress REAL NOT NULL DEFAULT 0, path TEXT NOT NULL DEFAULT '',
  error TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, updated_at REAL NOT NULL
);
"""

_lock = threading.RLock()        # guards the in-memory connection
_save_lock = threading.Lock()    # serialises writes of the encrypted file (always taken BEFORE _lock)
_conn = None
_dirty = False
_wake = threading.Event()
_flusher = None


def _now():
    return time.time()


# ------------------------------------------------------------------ vault lifecycle
def status():
    return {"exists": VAULT.exists(), "unlocked": _conn is not None, "legacy": VAULT.is_legacy()}


def is_unlocked():
    return _conn is not None


def conn():
    if _conn is None:
        raise Locked("The vault is locked.")
    return _conn


def _seed(c):
    """Migrations plus built-in providers and default settings (idempotent)."""
    cols = {r[1] for r in c.execute("PRAGMA table_info(providers)")}      # migrate older databases
    for col, default in (("kind", "llm"), ("protocol", "openai")):
        if col not in cols:
            c.execute("ALTER TABLE providers ADD COLUMN %s TEXT NOT NULL DEFAULT '%s'" % (col, default))
    for i, e in enumerate(catalog.CATALOG):
        c.execute("INSERT OR IGNORE INTO providers(id,name,base_url,model,enabled,is_local,sort,kind,protocol) VALUES(?,?,?,?,?,?,?,?,?)",
                  (e["id"], e["name"], e["base_url"], e["model"], 1 if e["id"] == "ollama" else 0, e["is_local"], i, e["kind"], e["protocol"]))
        c.execute("UPDATE providers SET kind=?, protocol=?, sort=? WHERE id=?", (e["kind"], e["protocol"], i, e["id"]))
    for k, v in DEFAULT_SETTINGS.items():
        c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, json.dumps(v)))


def _open_memory(image=b""):
    c = sqlite3.connect(":memory:", check_same_thread=False, isolation_level=None)
    c.row_factory = sqlite3.Row
    if image:
        c.deserialize(image)
    c.execute("PRAGMA secure_delete=ON")       # freed pages are zeroed, so deleted text does not linger in the file
    c.execute("PRAGMA foreign_keys=ON")
    c.executescript(SCHEMA)
    _seed(c)
    return c


def _wipe(path):
    """Best-effort overwrite + delete (cannot be guaranteed on SSDs / journaling filesystems)."""
    for p in (path, path + "-wal", path + "-shm"):
        try:
            n = os.path.getsize(p)
            with open(p, "r+b") as f:
                f.write(b"\0" * n)
                f.flush()
                os.fsync(f.fileno())
        except OSError:
            pass
        try:
            os.remove(p)
        except OSError:
            pass


def _after_open():
    global _flusher
    os.makedirs(WORKSPACE, exist_ok=True)
    _touch()
    if _flusher is None or not _flusher.is_alive():
        _flusher = threading.Thread(target=_flush_loop, daemon=True)
        _flusher.start()
    reconcile_files()


def create_vault(passphrase):
    """First run: create the encrypted database. Imports an old plaintext nyxor.db if one is found.
    Returns the one-time recovery code."""
    global _conn
    if len(passphrase or "") < 8:
        raise ValueError("Use a passphrase of at least 8 characters.")
    with _lock:
        if _conn is not None or (VAULT.exists() and not VAULT.is_legacy()):
            raise ValueError("A vault already exists. Unlock it instead.")
        image, legacy_aside = b"", None
        if VAULT.is_legacy():
            src = sqlite3.connect(DB_PATH)
            image = src.serialize()
            src.close()
            legacy_aside = DB_PATH + ".legacy"
            os.replace(DB_PATH, legacy_aside)
            for ext in ("-wal", "-shm"):
                if os.path.exists(DB_PATH + ext):
                    os.replace(DB_PATH + ext, legacy_aside + ext)
        try:
            _conn = _open_memory(image)
            recovery = VAULT.create(passphrase, _conn.serialize())
        except Exception:
            if legacy_aside:
                os.replace(legacy_aside, DB_PATH)
            _conn = None
            raise
        if legacy_aside:
            _wipe(legacy_aside)
        _after_open()
    return recovery


def unlock(secret):
    """Passphrase or recovery code. Raises BadSecret / Corrupt."""
    global _conn
    with _lock:
        if _conn is not None:
            return
        _conn = _open_memory(VAULT.unlock(secret))
        _after_open()


def lock():
    """Save, then forget the key and the plaintext database."""
    global _conn
    try:
        flush(force=True)
    finally:
        with _save_lock, _lock:
            if _conn is not None:
                _conn.close()
            _conn = None
            VAULT.lock()


def _touch():
    global _dirty
    _dirty = True
    _wake.set()


def flush(force=False, purge=False):
    """Write the encrypted file. purge=True also overwrites the previous copy (.bak) so deleted data is gone from it."""
    global _dirty
    with _save_lock:
        with _lock:
            if _conn is None or not VAULT.unlocked or not (_dirty or force or purge):
                return
            image = _conn.serialize()
            _dirty = False
        VAULT.save(image, purge=purge)


def _flush_loop():
    while True:
        _wake.wait()
        time.sleep(0.4)          # coalesce bursts of writes
        _wake.clear()
        try:
            flush()
        except Exception:
            traceback.print_exc()


def change_passphrase(old, new):
    if len(new or "") < 8:
        raise ValueError("Use a new passphrase of at least 8 characters.")
    if not VAULT.check(old):
        raise V.BadSecret("The current passphrase is wrong.")
    VAULT.replace_slot("pass", new)
    flush(force=True, purge=True)


def new_recovery_code(passphrase):
    if not VAULT.check(passphrase):
        raise V.BadSecret("The passphrase is wrong.")
    code = V.new_recovery_code()
    VAULT.replace_slot("recovery", code)
    flush(force=True, purge=True)
    return code


def backup_bytes():
    """The encrypted file exactly as stored (safe to copy: it needs your passphrase)."""
    flush(force=True)
    with open(DB_PATH, "rb") as f:
        return f.read()


# ------------------------------------------------------------------ query helpers
def _rows(sql, args=()):
    with _lock:
        return [dict(r) for r in conn().execute(sql, args).fetchall()]


def _row(sql, args=()):
    r = _rows(sql, args)
    return r[0] if r else None


def _run(sql, args=()):
    with _lock:
        cur = conn().execute(sql, args)
        _touch()
        return cur


# ------------------------------------------------------------------ settings
def get_settings():
    out = {r["key"]: json.loads(r["value"]) for r in _rows("SELECT key,value FROM settings")}
    for k, v in DEFAULT_SETTINGS.items():
        out.setdefault(k, v)
    return out


def get_setting(key, default=None):
    r = _row("SELECT value FROM settings WHERE key=?", (key,))
    return json.loads(r["value"]) if r else DEFAULT_SETTINGS.get(key, default)


def set_settings(patch):
    """Update known settings only. Returns the full settings dict."""
    with _lock:
        for k, v in patch.items():
            if k in DEFAULT_SETTINGS:
                _run("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (k, json.dumps(v)))
    return get_settings()


# ----------------------------------------------------------------- providers
def mask_key(key):
    if not key:
        return ""
    return key[:3] + "\u2026" + key[-4:] if len(key) > 10 else "\u2022" * len(key)


def is_masked(s):
    return isinstance(s, str) and ("\u2026" in s or "\u2022" in s)


def _public(p):
    p = dict(p)
    p["has_key"] = bool(p["api_key"])
    p["api_key"] = mask_key(p["api_key"])
    return p


def list_providers():
    """Providers for the browser: keys are masked."""
    return [_public(p) for p in _rows("SELECT * FROM providers ORDER BY sort, rowid")]


def get_provider(pid):
    """BACKEND ONLY: includes the real API key. Never serialise this to the browser."""
    return _row("SELECT * FROM providers WHERE id=?", (pid,))


def upsert_provider(data):
    """Create/update a provider. A masked or absent api_key leaves the stored key untouched;
    an empty string clears it. Returns the public (masked) record."""
    pid = data.get("id")
    with _lock:
        cur = get_provider(pid) if pid else None
        if cur is None:
            pid = pid or "custom-%d" % int(_now() * 1000)
            nxt = (_row("SELECT COALESCE(MAX(sort),0)+1 AS n FROM providers") or {"n": 1})["n"]
            kind = data.get("kind") if data.get("kind") in catalog.KINDS else "llm"
            proto = data.get("protocol") if data.get("protocol") in catalog.PROTOCOLS else "openai"
            _run("INSERT INTO providers(id,name,base_url,is_custom,sort,kind,protocol) VALUES(?,?,?,1,?,?,?)",
                 (pid, data.get("name") or "Custom provider", (data.get("base_url") or "").rstrip("/"), nxt, kind, proto))
        fields = {}
        for f in ("name", "base_url", "model"):
            if f in data and data[f] is not None:
                fields[f] = str(data[f]).strip().rstrip("/") if f == "base_url" else str(data[f]).strip()
        if "enabled" in data:
            fields["enabled"] = 1 if data["enabled"] else 0
        if "api_key" in data and data["api_key"] is not None and not is_masked(data["api_key"]):
            fields["api_key"] = data["api_key"].strip()
        if fields:
            _run("UPDATE providers SET %s WHERE id=?" % ",".join(k + "=?" for k in fields),
                 (*fields.values(), pid))
    return _public(get_provider(pid))


def delete_provider(pid):
    """Only custom providers can be deleted. Returns True if removed."""
    with _lock:
        p = get_provider(pid)
        if not p or not p["is_custom"]:
            return False
        _run("DELETE FROM providers WHERE id=?", (pid,))
        return True


# ------------------------------------------------------------- conversations
def create_conversation(title="New transmission", mode="direct"):
    t = _now()
    with _lock:
        cur = _run("INSERT INTO conversations(title,mode,created_at,updated_at) VALUES(?,?,?,?)", (title, mode, t, t))
        return cur.lastrowid


def list_conversations(search=""):
    """Pinned first, then most recently active. Search matches titles and message text."""
    q = (search or "").strip()
    if not q:
        return _rows("SELECT * FROM conversations ORDER BY pinned DESC, updated_at DESC")
    like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    return _rows(
        "SELECT * FROM conversations c WHERE c.title LIKE ? ESCAPE '\\' OR EXISTS "
        "(SELECT 1 FROM messages m WHERE m.conv_id=c.id AND m.content LIKE ? ESCAPE '\\') "
        "ORDER BY c.pinned DESC, c.updated_at DESC", (like, like))


def get_conversation(cid):
    return _row("SELECT * FROM conversations WHERE id=?", (cid,))


def rename_conversation(cid, title):
    _run("UPDATE conversations SET title=? WHERE id=?", ((title or "").strip()[:120] or "Untitled", cid))


def pin_conversation(cid, pinned=True):
    _run("UPDATE conversations SET pinned=? WHERE id=?", (1 if pinned else 0, cid))


def set_conversation_mode(cid, mode):
    _run("UPDATE conversations SET mode=? WHERE id=?", (mode, cid))


def delete_conversation(cid):
    """Removes the chat, all its messages/files/tool runs (cascade) and its folder on disk, then scrubs the encrypted file."""
    _run("DELETE FROM conversations WHERE id=?", (cid,))
    shutil.rmtree(os.path.join(WORKSPACE, "conv_%d" % int(cid)), ignore_errors=True)
    _purge(vacuum=True)


# ------------------------------------------------------------------ messages
def add_message(cid, role, content, reasoning="", provider="", model="", meta=None):
    """Save one message (every mode, automatically). The first user message titles an untitled chat."""
    t = _now()
    with _lock:
        cur = _run(
            "INSERT INTO messages(conv_id,role,content,reasoning,provider,model,meta,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (cid, role, content, reasoning, provider, model, json.dumps(meta or {}), t))
        _run("UPDATE conversations SET updated_at=? WHERE id=?", (t, cid))
        if role == "user":
            c = get_conversation(cid)
            if c and c["title"] == "New transmission":
                title = " ".join(content.split())[:60] or "New transmission"
                _run("UPDATE conversations SET title=? WHERE id=?", (title, cid))
        return cur.lastrowid


def update_message(mid, **fields):
    allowed = {"content", "reasoning", "provider", "model"}
    f = {k: v for k, v in fields.items() if k in allowed}
    if "meta" in fields:
        f["meta"] = json.dumps(fields["meta"] or {})
    if f:
        _run("UPDATE messages SET %s WHERE id=?" % ",".join(k + "=?" for k in f), (*f.values(), mid))


def list_messages(cid):
    out = _rows("SELECT * FROM messages WHERE conv_id=? ORDER BY id", (cid,))
    for m in out:
        m["meta"] = json.loads(m["meta"] or "{}")
    return out


def recent_context(cid, turns=None, include_local=True):
    """Last N turns (user+assistant) as [{role, content}] for the model. Text extracted from attached files
    is re-attached to its user message. include_local=False drops anything produced in agent mode
    (meta local_only), so cloud models never see local files or command output."""
    n = int(get_setting("history_turns", 12) if turns is None else turns)
    if n <= 0:
        return []
    flt = "" if include_local else " AND meta NOT LIKE '%\"local_only\": true%'"
    rows = _rows("SELECT role,content,meta FROM messages WHERE conv_id=? AND role IN ('user','assistant') "
                 "AND content<>''" + flt + " ORDER BY id DESC LIMIT ?", (cid, n * 2))[::-1]
    out = []
    for r in rows:
        txt = r["content"]
        if r["role"] == "user":
            try:
                at = json.loads(r["meta"] or "{}").get("attach_text")
            except ValueError:
                at = None
            if at:
                txt += "\n\n" + at
        out.append({"role": r["role"], "content": txt})
    return out


# ------------------------------------------------------------------- memories
def add_memory(fact):
    fact = " ".join((fact or "").split())
    if not fact:
        return None
    with _lock:
        dup = _row("SELECT id FROM memories WHERE lower(fact)=lower(?)", (fact,))
        if dup:
            return dup["id"]
        return _run("INSERT INTO memories(fact,created_at) VALUES(?,?)", (fact, _now())).lastrowid


def list_memories():
    return _rows("SELECT * FROM memories ORDER BY id")


def forget_memory(mid):
    ok = _run("DELETE FROM memories WHERE id=?", (mid,)).rowcount > 0
    if ok:
        _purge()
    return ok


def memory_prompt():
    """Text block appended to the system prompt; empty string when nothing is saved."""
    ms = list_memories()
    if not ms:
        return ""
    return ("\n\nLONG-TERM MEMORY \u2014 facts the operator asked you to remember. Treat them as true and use them "
            "when relevant:\n" + "\n".join("- " + m["fact"] for m in ms))


# ---------------------------------------------------------------------- files
def add_file(cid, path, size, message_id=None):
    t = _now()
    with _lock:
        _run("INSERT INTO files(conv_id,message_id,path,size,created_at) VALUES(?,?,?,?,?) "
             "ON CONFLICT(conv_id,path) DO UPDATE SET size=excluded.size, message_id=excluded.message_id, created_at=excluded.created_at",
             (cid, message_id, path, size, t))
        return _row("SELECT * FROM files WHERE conv_id=? AND path=?", (cid, path))


def remove_file_record(cid, rel):
    """Forget a file everywhere in the database: its row, its chips in messages, and any text extracted from it."""
    _run("DELETE FROM files WHERE conv_id=? AND path=?", (cid, rel))
    for m in _rows("SELECT id,meta FROM messages WHERE conv_id=? AND (meta LIKE '%\"files\"%' OR meta LIKE '%\"attachments\"%')", (cid,)):
        try:
            meta = json.loads(m["meta"] or "{}")
        except ValueError:
            continue
        changed = False
        if isinstance(meta.get("files"), list):
            keep = [f for f in meta["files"] if f.get("path") != rel]
            changed |= len(keep) != len(meta["files"])
            meta["files"] = keep
        if isinstance(meta.get("attachments"), list):
            keep = []
            for a in meta["attachments"]:
                if a.get("path") == rel:
                    changed = True
                    stored = a.get("stored") or a.get("name") or ""
                    if stored and meta.get("attach_text"):
                        meta["attach_text"] = re.sub(r"\n*--- ATTACHED FILE: %s ---.*?--- END OF %s ---" % (re.escape(stored), re.escape(stored)),
                                                     "", meta["attach_text"], count=1, flags=re.S).strip()
                        if not meta["attach_text"]:
                            del meta["attach_text"]
                else:
                    keep.append(a)
            meta["attachments"] = keep
        if changed:
            _run("UPDATE messages SET meta=? WHERE id=?", (json.dumps(meta), m["id"]))


def reconcile_files():
    """Drop database records for files (and finished videos) that were deleted from disk. Returns counts."""
    n = {"files": 0, "videos": 0}
    for f in list_files():
        if not os.path.isfile(os.path.join(WORKSPACE, "conv_%d" % f["conv_id"], *f["path"].split("/"))):
            remove_file_record(f["conv_id"], f["path"])
            n["files"] += 1
    for j in list_video_jobs(1000):
        if j["status"] == "done" and not os.path.isfile(j["path"]):
            _run("DELETE FROM video_jobs WHERE id=?", (j["id"],))
            n["videos"] += 1
    if any(n.values()):
        _purge()
    return n


def _purge(vacuum=False):
    with _lock:
        if vacuum and _conn is not None:
            _conn.execute("VACUUM")
    flush(force=True, purge=True)


def list_files(cid=None):
    if cid is None:
        return _rows("SELECT * FROM files ORDER BY created_at DESC")
    return _rows("SELECT * FROM files WHERE conv_id=? ORDER BY created_at, id", (cid,))


# ------------------------------------------------------------------ tool runs
def start_tool_run(cid, tool, args):
    return _run("INSERT INTO tool_runs(conv_id,tool,args,started_at) VALUES(?,?,?,?)",
                (cid, tool, json.dumps(args, ensure_ascii=False), _now())).lastrowid


def finish_tool_run(rid, status, decision="", output=""):
    _run("UPDATE tool_runs SET status=?, decision=?, output=?, ended_at=? WHERE id=?",
         (status, decision, (output or "")[:20000], _now(), rid))


def list_tool_runs(cid=None, limit=200):
    if cid is None:
        return _rows("SELECT * FROM tool_runs ORDER BY id DESC LIMIT ?", (limit,))
    return _rows("SELECT * FROM tool_runs WHERE conv_id=? ORDER BY id DESC LIMIT ?", (cid, limit))


# ---------------------------------------------------------------- video jobs
def create_video_job(jid, backend, prompt, params):
    t = _now()
    _run("INSERT INTO video_jobs(id,backend,prompt,params,created_at,updated_at) VALUES(?,?,?,?,?,?)",
         (jid, backend, prompt, json.dumps(params), t, t))


def update_video_job(jid, **f):
    allowed = {"status", "progress", "path", "error"}
    f = {k: v for k, v in f.items() if k in allowed}
    if f:
        f["updated_at"] = _now()
        _run("UPDATE video_jobs SET %s WHERE id=?" % ",".join(k + "=?" for k in f), (*f.values(), jid))


def get_video_job(jid):
    return _row("SELECT * FROM video_jobs WHERE id=?", (jid,))


def delete_video_job(jid):
    _run("DELETE FROM video_jobs WHERE id=?", (jid,))
    _purge()


def list_video_jobs(limit=50):
    return _rows("SELECT * FROM video_jobs ORDER BY created_at DESC LIMIT ?", (limit,))


# ------------------------------------------------------------ export / import
TABLES = ["providers", "conversations", "messages", "files", "memories", "settings", "tool_runs", "video_jobs"]


def export_all(include_keys=False):
    """Whole database as a JSON-serialisable dict. API keys are omitted unless include_keys=True."""
    data = {"nyxor_export": 1, "exported_at": _now(), "includes_keys": bool(include_keys), "tables": {}}
    for t in TABLES:
        rows = _rows("SELECT * FROM %s" % t)
        if t == "providers" and not include_keys:
            for r in rows:
                r["api_key"] = ""
        data["tables"][t] = rows
    return data


def import_all(data):
    """Replace the database contents with an export. Stored API keys are kept for any provider whose
    exported key is empty. Runs in one transaction; raises ValueError on a bad file."""
    if not isinstance(data, dict) or data.get("nyxor_export") != 1 or not isinstance(data.get("tables"), dict):
        raise ValueError("Not a NYXOR export file.")
    tables = data["tables"]
    with _lock:
        c = conn()
        old_keys = {r["id"]: r["api_key"] for r in _rows("SELECT id,api_key FROM providers")}
        cols = {t: [r["name"] for r in c.execute("PRAGMA table_info(%s)" % t)] for t in TABLES}
        c.execute("BEGIN")
        try:
            c.execute("PRAGMA foreign_keys=OFF")
            for t in reversed(TABLES):
                c.execute("DELETE FROM %s" % t)
            for t in TABLES:
                for row in tables.get(t, []):
                    if t == "providers" and not row.get("api_key"):
                        row = dict(row, api_key=old_keys.get(row.get("id"), ""))
                    keys = [k for k in row if k in cols[t]]
                    vals = [json.dumps(row[k]) if isinstance(row[k], (dict, list)) else row[k] for k in keys]
                    c.execute("INSERT OR REPLACE INTO %s(%s) VALUES(%s)" % (t, ",".join(keys), ",".join("?" * len(keys))), vals)
            c.execute("COMMIT")
        except Exception as e:
            c.execute("ROLLBACK")
            raise ValueError("Import failed: %s" % e)
        finally:
            c.execute("PRAGMA foreign_keys=ON")
        _seed(c)
        _touch()
    _purge()
    return {t: len(tables.get(t, [])) for t in TABLES}



# ------------------------------------------------------------------ reports (live generative AI content, Store policy 11.16)
REPORT_CATEGORIES = ("harmful", "illegal", "sexual", "hateful", "false", "other")


def add_report(conv_id, message_id, category, note, include_text):
    """Save a report about an AI answer on this PC. The text of the answer is kept only if the user chose to include it."""
    category = category if category in REPORT_CATEGORIES else "other"
    m = _row("SELECT content, model FROM messages WHERE id=? AND role='assistant'", (int(message_id),)) if message_id else None
    return _run("INSERT INTO reports(created_at,conv_id,message_id,category,note,excerpt,model) VALUES(?,?,?,?,?,?,?)",
                (_now(), conv_id, message_id, category, (note or "").strip()[:2000],
                 (m["content"][:4000] if (m and include_text) else ""), (m["model"] if m else ""))).lastrowid


def last_assistant_id(cid):
    r = _row("SELECT id FROM messages WHERE conv_id=? AND role='assistant' ORDER BY id DESC LIMIT 1", (int(cid),))
    return r["id"] if r else None


def list_reports():
    return _rows("SELECT * FROM reports ORDER BY id DESC LIMIT 200")


def delete_report(rid):
    return _run("DELETE FROM reports WHERE id=?", (int(rid),)).rowcount > 0
