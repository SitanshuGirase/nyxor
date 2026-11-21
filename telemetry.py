#!/usr/bin/env python3
"""
NYXOR v2.3 - real telemetry. Everything the dashboard shows comes from here and is measured, never simulated:
  system   CPU load, memory, disk of the data drive, this process's memory and uptime (standard library only;
           Windows uses ctypes calls to kernel32, Linux reads /proc). A value that cannot be read is None, and the
           interface shows "n/a" instead of inventing a number.
  app      chats, memories, files, tool runs, errors in the last hour, vault auto-lock countdown
  models   connected models and provider reachability, tokens per second from real answers (router records)
  events   a ring buffer of things that really happened (unlock, answers, failures, fallbacks, research, video, reports)
"""
import ctypes
import os
import shutil
import sys
import threading
import time

import budget
import db
import providers as P

START = time.time()
_lock = threading.Lock()
_events = []              # [{id, t, text, cls}]
_next_id = [1]
_cpu_prev = [None]


# ------------------------------------------------------------------ events
def add_event(text, cls=""):
    """Record something that really happened. cls: '' | 'ok' | 'hot' (hot = a problem or something to look at)."""
    with _lock:
        _events.append({"id": _next_id[0], "t": time.time(), "text": str(text)[:200], "cls": cls})
        _next_id[0] += 1
        del _events[:-200]


def events_since(since=0):
    with _lock:
        return [e for e in _events if e["id"] > since]


def clear_events():
    with _lock:
        del _events[:]


def errors_last_hour():
    cut = time.time() - 3600
    with _lock:
        return sum(1 for e in _events if e["cls"] == "hot" and e["t"] >= cut)


# ------------------------------------------------------------------ system readings
def _cpu_percent():
    """Whole-machine CPU use since the previous call (needs two samples), or None."""
    try:
        if os.name == "nt":
            class FT(ctypes.Structure):
                _fields_ = [("lo", ctypes.c_uint32), ("hi", ctypes.c_uint32)]
            idle, kern, user = FT(), FT(), FT()
            if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user)):
                return None
            f = lambda x: (x.hi << 32) | x.lo
            cur = (f(idle), f(kern) + f(user))        # kernel time includes idle time
        else:
            with open("/proc/stat") as fh:
                parts = [int(x) for x in fh.readline().split()[1:]]
            cur = (parts[3] + (parts[4] if len(parts) > 4 else 0), sum(parts[:8]))
        prev, _cpu_prev[0] = _cpu_prev[0], cur
        if not prev or cur[1] <= prev[1]:
            return None
        busy = 1 - (cur[0] - prev[0]) / (cur[1] - prev[1])
        return round(max(0.0, min(1.0, busy)) * 100)
    except Exception:
        return None


def _memory():
    """(used percent, total bytes, used bytes) of physical memory, or (None, None, None)."""
    try:
        if os.name == "nt":
            class MS(ctypes.Structure):
                _fields_ = [("len", ctypes.c_uint32), ("load", ctypes.c_uint32), ("total", ctypes.c_uint64), ("avail", ctypes.c_uint64),
                            ("tpf", ctypes.c_uint64), ("apf", ctypes.c_uint64), ("tv", ctypes.c_uint64), ("av", ctypes.c_uint64), ("ave", ctypes.c_uint64)]
            m = MS()
            m.len = ctypes.sizeof(MS)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
                return None, None, None
            return int(m.load), int(m.total), int(m.total - m.avail)
        info = {}
        with open("/proc/meminfo") as fh:
            for ln in fh:
                k, _, v = ln.partition(":")
                info[k] = int(v.split()[0]) * 1024
        total, avail = info["MemTotal"], info.get("MemAvailable", info.get("MemFree", 0))
        return round((total - avail) / total * 100), total, total - avail
    except Exception:
        return None, None, None


def _disk():
    try:
        base = db.WORKSPACE if os.path.isdir(db.WORKSPACE) else os.path.dirname(db.DB_PATH) or "."
        u = shutil.disk_usage(base)
        return round(u.used / u.total * 100), u.total, u.free
    except Exception:
        return None, None, None


def _process_rss():
    try:
        if os.name == "nt":
            class PM(ctypes.Structure):
                _fields_ = [("cb", ctypes.c_uint32), ("pf", ctypes.c_uint32), ("peak", ctypes.c_size_t), ("ws", ctypes.c_size_t)] + \
                           [(n, ctypes.c_size_t) for n in ("qpp", "pp", "qnpp", "np", "pfu", "ppfu")]
            pm = PM()
            pm.cb = ctypes.sizeof(PM)
            h = ctypes.windll.kernel32.GetCurrentProcess()
            if ctypes.windll.psapi.GetProcessMemoryInfo(h, ctypes.byref(pm), pm.cb):
                return int(pm.ws)
            return None
        with open("/proc/self/statm") as fh:
            return int(fh.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except Exception:
        return None


# ------------------------------------------------------------------ snapshot
def _count(sql):
    try:
        r = db._row(sql)
        return int(list(r.values())[0]) if r else 0
    except Exception:
        return 0


def snapshot(idle_since=None, auto_lock_minutes=0, busy=False, since=0):
    """One reading for the dashboard. Safe to call while the vault is unlocked."""
    cpu = _cpu_percent()
    mem_pct, mem_total, mem_used = _memory()
    disk_pct, disk_total, disk_free = _disk()
    stats = db.get_setting("router_stats") or {}
    tps = [r.get("tps", 0) for r in stats.values() if r.get("tps")]
    models, online = 0, 0
    for row in db.list_providers():
        p = db.get_provider(row["id"])
        if p and p.get("kind", "llm") == "llm" and p["enabled"] and (p["is_local"] or p["api_key"]) and p["model"]:
            models += 1
            with P._slock:
                st = P._status.get(p["id"], ("ok", 0))
            if not (st[0] == "down" and time.time() - st[1] < 60):
                online += 1
    lock_left = None
    if auto_lock_minutes and idle_since:
        lock_left = max(0, round(auto_lock_minutes * 60 - (time.time() - idle_since)))
    return {
        "t": time.time(), "uptime": int(time.time() - START), "os": sys.platform, "busy": bool(busy),
        "cpu": cpu, "mem": mem_pct, "mem_total": mem_total, "mem_used": mem_used, "disk": disk_pct, "disk_total": disk_total, "disk_free": disk_free,
        "process_rss": _process_rss(),
        "chats": _count("SELECT COUNT(*) FROM conversations"), "memories": _count("SELECT COUNT(*) FROM memories"),
        "files": _count("SELECT COUNT(*) FROM files"), "tool_runs": _count("SELECT COUNT(*) FROM tool_runs"),
        "models": models, "models_online": online, "tps_best": max(tps) if tps else None, "tps_last": tps[-1] if tps else None,
        "usage": budget.today_meter(),
        "alerts": errors_last_hour(), "lock_left": lock_left, "auto_lock_minutes": auto_lock_minutes,
        "events": events_since(since),
    }
