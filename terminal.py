#!/usr/bin/env python3
"""
NYXOR v2.3 - the terminal panel: run a command on this PC from the interface and watch its output. Standard library only.

Safety rules (this is the part that matters):
  - OFF by default. It works only after the user switches it on in SYSTEMS -> AGENT, with a plain warning that commands run as them.
  - Every command runs in a folder inside the allowed folders (the same list the agent uses; default: the NYXOR workspace).
    `cd` is handled here and refuses to leave them. A command itself can still touch anything the user account can: it is a shell.
  - Needs the unlocked vault's session cookie like every other API route, plus the Host/Origin checks.
  - The child process gets NO NYXOR_* environment variables (no passphrase, no paths to the vault).
  - One-shot commands with merged output, a timeout (default 120 s), an output cap, and a KILL button that ends the whole process tree.
  - Honest limit: this is not a terminal emulator. There is no stdin, so full-screen or interactive programs (vim, a REPL, a password prompt) will not work.
"""
import os
import subprocess
import threading
import time
import uuid

import agent
import db
import telemetry

OUT_CAP = 400_000
MAX_RUNS = 20
_lock = threading.Lock()
RUNS = {}
_cwd = [None]


def enabled():
    return bool(db.get_setting("terminal_enabled"))


def roots():
    return agent.allowed_dirs() or [os.path.realpath(db.WORKSPACE)]


def _inside_any(full):
    return any(agent._inside(r, full) for r in roots())


def cwd():
    c = _cwd[0]
    if not c or not os.path.isdir(c) or not _inside_any(c):
        os.makedirs(db.WORKSPACE, exist_ok=True)
        base = os.path.realpath(db.WORKSPACE)
        c = base if _inside_any(base) else roots()[0]
        os.makedirs(c, exist_ok=True)
        _cwd[0] = c
    return c


def child_env():
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("NYXOR_")}
    env.setdefault("TERM", "dumb")
    return env


def _cd(arg):
    arg = arg.strip().strip('"\'')
    if arg.lower().startswith("/d "):
        arg = arg[3:].strip().strip('"\'')
    target = os.path.realpath(os.path.join(cwd(), os.path.expanduser(arg))) if arg else os.path.realpath(db.WORKSPACE)
    if not os.path.isdir(target):
        return "cd: no such folder: %s\n" % arg, 1
    if not _inside_any(target):
        return "cd: blocked. The terminal stays inside the allowed folders (SYSTEMS \u2192 AGENT):\n  %s\n" % "\n  ".join(roots()), 1
    _cwd[0] = target
    return "", 0


def start(command):
    """Begin a command. Returns {id, cwd} or, for the built-in cd, {id: None, cwd, output, code}."""
    if not enabled():
        raise PermissionError("The terminal is switched off. Enable it in SYSTEMS \u2192 AGENT.")
    command = (command or "").strip()
    if not command or len(command) > 4000:
        raise ValueError("Type a command (up to 4000 characters).")
    word, _, rest = command.partition(" ")
    if word.lower() == "cd":
        out, code = _cd(rest)
        return {"id": None, "cwd": cwd(), "output": out, "code": code}
    timeout = max(5, min(int(db.get_setting("terminal_timeout") or 120), 600))
    argv = ["powershell", "-NoProfile", "-NonInteractive", "-Command", command] if os.name == "nt" else ["/bin/sh", "-c", command]
    kw = dict(stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, cwd=cwd(), env=child_env())
    if os.name == "nt":
        kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        kw["start_new_session"] = True
    proc = subprocess.Popen(argv, **kw)
    run = {"id": uuid.uuid4().hex[:10], "out": "", "done": False, "code": None, "proc": proc, "t0": time.time(), "killed": "", "cwd": cwd()}
    with _lock:
        RUNS[run["id"]] = run
        for old in sorted(RUNS, key=lambda k: RUNS[k]["t0"])[:-MAX_RUNS]:
            if RUNS[old]["done"]:
                RUNS.pop(old, None)
    threading.Thread(target=_pump, args=(run, timeout), daemon=True).start()
    return {"id": run["id"], "cwd": run["cwd"]}


def _pump(run, timeout):
    proc = run["proc"]

    def on_timeout():
        run["killed"] = "timeout"
        agent._kill_tree(proc)
    timer = threading.Timer(timeout, on_timeout)
    timer.start()
    try:
        while True:
            chunk = os.read(proc.stdout.fileno(), 4096)
            if not chunk:
                break
            if len(run["out"]) < OUT_CAP:
                run["out"] += chunk.decode("utf-8", "replace")
        run["code"] = proc.wait(timeout=10)
    except Exception:
        run["code"] = run["code"] if run["code"] is not None else -1
    finally:
        timer.cancel()
        try:
            proc.stdout.close()
        except Exception:
            pass
        if run["killed"] == "timeout":
            run["out"] += "\n[killed: exceeded %d s timeout]\n" % timeout
        elif run["killed"] == "user":
            run["out"] += "\n[stopped]\n"
        run["done"] = True
        secs = time.time() - run["t0"]
        bad = run["code"] not in (0, None)
        telemetry.add_event("terminal command finished (exit %s \u00b7 %.1f s)" % (run["code"], secs), "hot" if bad else "")


def poll(rid, since=0):
    run = RUNS.get(rid)
    if not run:
        return {"output": "", "n": since, "done": True, "code": None, "cwd": cwd()}
    out = run["out"]
    return {"output": out[since:], "n": len(out), "done": run["done"], "code": run["code"], "cwd": cwd()}


def kill(rid):
    run = RUNS.get(rid)
    if run and not run["done"]:
        run["killed"] = run["killed"] or "user"
        agent._kill_tree(run["proc"])
        return True
    return False
