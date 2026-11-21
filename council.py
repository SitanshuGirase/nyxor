#!/usr/bin/env python3
"""
NYXOR v2 - COUNCIL mode, rebuilt so groups of models can actually ship working projects (games, apps).

  1 PLAN       the orchestrator (ANY model, local or cloud) writes a shared SPEC (file names, element ids, function
               names, data shapes) and gives every member its own files. Strict JSON; if parsing fails every member
               gets the whole task.
  2 BUILD      members run in parallel, each streaming; each returns COMPLETE files for its assignment.
  3 ASSEMBLE   the backend writes the members' files into the chat workspace and VALIDATES them (Python/JS syntax,
               JSON, HTML references, placeholder text).
  4 INTEGRATE  the orchestrator reads all files plus the validation report, writes any missing or corrected files,
               then the backend validates again; up to two repair rounds.
Tasks with no files (writing, analysis) skip steps 3-4 and the orchestrator simply merges the members' answers.
A failed member (bad key, rate limit) is marked failed and the rest continue.
"""
import json
import os
import posixpath
import re
import shutil
import subprocess
import tempfile
import threading

import time

import assets
import db
import modes as M
import providers as P
import workspace as W

PLAN_RULES = (
    "You are the orchestrator of a council of AI models. Split the operator's task into exactly %d assignments, one per member, "
    "so they can work IN PARALLEL and their output fits together. Reply with strict JSON only (no prose, no code fences):\n"
    '{"spec": "<shared contract every member must follow>", "subtasks": [{"title": "...", "files": ["relative/path.ext"], "instructions": "..."}]}\n'
    "For software and games the spec MUST fix exact file names, HTML element ids, function names, data shapes and controls so "
    "separately written files work together. Prefer one self-contained HTML file or very few files; no external dependencies and no "
    "build step (games: HTML5 canvas + vanilla JavaScript, keyboard controls, score, game over and restart). Each file is owned by "
    "exactly one member. For non-code tasks leave files empty.")
HERE = os.path.dirname(os.path.abspath(__file__))
PLACEHOLDER = re.compile(r"(rest of (the )?(code|file|game|script)|your code here|implement (this|me|here)|\.\.\.\s*(existing|omitted|truncated|unchanged)|TODO: implement)", re.I)


def _normalise(j, n, text):
    spec, subs = str(j.get("spec") or "").strip(), []
    for s in j["subtasks"]:
        if isinstance(s, str):
            s = {"instructions": s}
        if not isinstance(s, dict):
            continue
        files = []
        for f in s.get("files") or []:
            try:
                files.append(W.safe_rel(str(f)))
            except ValueError:
                pass
        ins = str(s.get("instructions") or s.get("task") or s.get("description") or "").strip()
        if ins or files:
            subs.append({"title": str(s.get("title") or s.get("role") or "").strip(), "files": files, "instructions": ins or text})
    if not subs:
        raise ValueError("empty plan")
    while len(subs) < n:
        subs.append({"title": "", "files": [], "instructions": text})
    return spec, subs[:n]


def _plan(task, lead, lmodel, text, members):
    n = len(members)
    roles = "; ".join("member %d is %s" % (m["idx"] + 1, m["role"]) for m in members if m["role"])
    sysmsg = PLAN_RULES % n + ((" Member roles: " + roles + ".") if roles else "")
    try:
        out = P.complete(lead, lmodel, M.apply_think([{"role": "system", "content": sysmsg}, {"role": "user", "content": text}], lmodel), 0.2, task)
        spec, subs = _normalise(json.loads(out[out.index("{"):out.rindex("}") + 1]), n, text)
        return spec, subs, False
    except P.Cancelled:
        raise
    except Exception:
        return "", [{"title": "", "files": [], "instructions": text}] * n, True


def _join(base, rel):
    b = os.path.realpath(base)
    full = os.path.realpath(os.path.join(b, rel))
    if os.path.commonpath([b, full]) != b:
        raise ValueError("path escapes the folder")
    return full


def validate(base, paths):
    """Static checks on generated files. Returns [{path, problem}]."""
    issues, node = [], shutil.which("node")

    def js_check(src, label):
        if not node:
            return
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "x.mjs")
            with open(f, "w", encoding="utf-8") as fh:
                fh.write(src)
            r = subprocess.run([node, "--check", f], capture_output=True, text=True, timeout=20)
            if r.returncode:
                lines = [l for l in r.stderr.splitlines() if l.strip() and not l.startswith("Node.js")]
                issues.append({"path": label, "problem": "JavaScript syntax error: " + " | ".join(lines[:4])[:300]})

    for rel in paths:
        try:
            src = open(_join(base, rel), encoding="utf-8", errors="replace").read()
        except (OSError, ValueError):
            continue
        ext = os.path.splitext(rel)[1].lower()
        if not src.strip():
            issues.append({"path": rel, "problem": "file is empty"})
            continue
        if PLACEHOLDER.search(src):
            issues.append({"path": rel, "problem": "contains placeholder text instead of real code"})
        if ext == ".py":
            try:
                compile(src, rel, "exec")
            except SyntaxError as e:
                issues.append({"path": rel, "problem": "Python syntax error on line %s: %s" % (e.lineno, e.msg)})
        elif ext in (".js", ".mjs"):
            js_check(src, rel)
        elif ext == ".json":
            try:
                json.loads(src)
            except ValueError as e:
                issues.append({"path": rel, "problem": "invalid JSON: %s" % e})
        elif ext in (".html", ".htm"):
            for m in re.finditer(r"""<(?:script[^>]*\ssrc|link[^>]*\shref)\s*=\s*["']([^"']+)["']""", src, re.I):
                ref = m.group(1)
                if re.match(r"(https?:)?//|data:|#|mailto:", ref):
                    continue
                target = posixpath.normpath(posixpath.join(posixpath.dirname(rel), ref.split("?")[0].split("#")[0]))
                try:
                    ok = os.path.isfile(_join(base, target))
                except ValueError:
                    ok = False
                if not ok:
                    issues.append({"path": rel, "problem": "references '%s' but that file does not exist" % ref})
            for k, m in enumerate(re.finditer(r"<script(?![^>]*\ssrc)[^>]*>(.*?)</script>", src, re.I | re.S)):
                if m.group(1).strip():
                    js_check(m.group(1), "%s (inline script %d)" % (rel, k + 1))
    return issues


def _read_files(cid, paths, cap=80000):
    out, used = [], 0
    for rel in sorted(paths):
        try:
            src = open(W.resolve(cid, rel), encoding="utf-8", errors="replace").read()
        except (OSError, ValueError):
            continue
        room = max(2000, min(20000, cap - used))
        note = "\n[... file continues, %d more chars ...]" % (len(src) - room) if len(src) > room else ""
        out.append("=== %s ===\n````\n%s%s\n````" % (rel, src[:room], note))
        used += min(len(src), room)
    return "\n\n".join(out)


def run_council(task, emit, cid, text, lead_ref, member_refs, attachments=None, web=None):
    lead, lmodel = M.resolve_ref(lead_ref)
    M.check_usable(lead, lmodel)
    member_refs = list(member_refs or [])
    if not member_refs and db.get_setting("council_auto"):
        member_refs = auto_members()
        if member_refs:
            emit({"type": "notice", "text": "Auto-pool picked: " + ", ".join(member_refs) + "  (change in SYSTEMS \u2192 COUNCIL)"})
    if not member_refs:
        raise P.ProviderError("No council members available. Enable a provider or choose members in SYSTEMS \u2192 COUNCIL.")
    roles = db.get_setting("council_roles") or {}
    members = []
    for i, ref in enumerate(member_refs):
        err, p, m = None, None, ref
        try:
            p, m = M.resolve_ref(ref)
            M.check_usable(p, m)
        except P.ProviderError as e:
            err = str(e)
        members.append({"idx": i, "ref": ref, "p": p, "model": m, "err": err, "out": "", "status": "pending",
                        "role": str(roles.get(ref, "")).strip(), "sub": text, "files": [], "title": "", "wrote": []})
    ctx = db.recent_context(cid, include_local=P.is_local_provider(lead))
    try:
        inp = M.prepare(emit, cid, text, attachments, web, task, P.is_local_provider(lead), (lead, lmodel))
    except P.Cancelled:
        emit({"type": "aborted"})
        return
    db.add_message(cid, "user", text or "[attachment]", meta=dict(inp["meta"], mode="council"))
    if not P.is_local_provider(lead) or any(m["p"] and not P.is_local_provider(m["p"]) for m in members):
        emit({"type": "notice", "text": "Cloud members receive this task."})
    strategy = db.get_setting("council_strategy") or "auto"
    if strategy == "auto":
        strategy = "candidates" if looks_build(text) else "merge"
    emit({"type": "council_start", "lead": lead_ref, "strategy": strategy, "members": [
        {"idx": m["idx"], "ref": m["ref"], "role": m["role"], "local": bool(m["p"] and P.is_local_provider(m["p"]))} for m in members]})
    if strategy == "candidates":
        return _candidates(task, emit, cid, text, inp, lead, lmodel, lead_ref, members, ctx)
    return _legacy(task, emit, cid, text, inp, lead, lmodel, lead_ref, members, ctx)


def _legacy(task, emit, cid, text, inp, lead, lmodel, lead_ref, members, ctx):
    """strategy 'split' (each member owns files) and 'merge' (text answers merged by the orchestrator)."""
    base = os.path.realpath(W.conv_dir(cid))
    try:
        spec, subs, fallback = _plan(task, lead, lmodel, inp["text"], members)
    except P.Cancelled:
        emit({"type": "aborted"})
        return
    for m, s in zip(members, subs):
        m["sub"], m["files"], m["title"] = s["instructions"], s["files"], s["title"]
    emit({"type": "council_plan", "subtasks": [m["sub"] for m in members], "titles": [m["title"] for m in members],
          "files": [m["files"] for m in members], "spec": spec, "fallback": fallback})
    elock = threading.Lock()

    def sem(ev):
        with elock:
            emit(ev)

    def work(m):
        if m["err"]:
            m["status"] = "failed"
            return sem({"type": "member_status", "idx": m["idx"], "status": "failed", "error": m["err"]})
        sem({"type": "member_status", "idx": m["idx"], "status": "running"})
        sysmsg = (db.get_setting("system_prompt") or db.DEFAULT_SYSTEM) + (
            "\n\nYou are a council member%s. Do ONLY your assignment, thoroughly, and follow the shared spec exactly. "
            "If you create files, output each COMPLETE file as a fenced block with info string file:relative/path.ext \u2014 "
            "never truncate, no placeholders, no TODO stubs. Do not address the other members." % ((" (%s)" % m["role"]) if m["role"] else ""))
        body = "OVERALL TASK:\n%s\n\nSHARED SPEC:\n%s\n\nYOUR ASSIGNMENT%s:\n%s" % (
            inp["text"], spec or "(none)", (" (%s)" % m["title"]) if m["title"] else "", m["sub"])
        if m["files"]:
            body += "\n\nYOU OWN THESE FILES (write each completely): " + ", ".join(m["files"])
        msgs = [{"role": "system", "content": sysmsg}, {"role": "user", "content": M.build_user(inp, body)}]
        try:
            r = M.stream(task, sem, m["p"], m["model"], msgs, db.get_setting("temperature", 0.7), member=m["idx"])
            m["out"], m["status"] = r["content"].strip(), "done"
            sem({"type": "member_status", "idx": m["idx"], "status": "done"})
        except P.Cancelled:
            m["status"] = "aborted"
        except Exception as e:
            m["status"], m["err"] = "failed", str(e)
            sem({"type": "member_status", "idx": m["idx"], "status": "failed", "error": str(e)})

    threads = [threading.Thread(target=work, args=(m,), daemon=True) for m in members]
    for t in threads:
        t.start()
    for t in threads:
        while t.is_alive():
            t.join(0.2)
    if task.cancel.is_set():
        emit({"type": "aborted"})
        return

    # ---- collect the files the members wrote
    drafts = {}
    for m in members:
        if m["status"] != "done":
            continue
        for b in W.parse_blocks(m["out"]):
            raw = W.file_path_of(b["info"])
            if not raw:
                continue
            try:
                rel = W.safe_rel(raw)
            except ValueError as e:
                emit({"type": "notice", "text": "Member %d wrote a blocked path '%s': %s" % (m["idx"] + 1, raw, e)})
                continue
            if not b["closed"]:
                emit({"type": "notice", "text": "Member %d's file '%s' was cut off and was not used." % (m["idx"] + 1, rel)})
                continue
            drafts[rel] = b["content"] + "\n"
            m["wrote"].append(rel)
    summary = lambda: [{"ref": m["ref"], "role": m["role"], "sub": m["sub"], "files": m["wrote"] or m["files"], "status": m["status"],
                        "error": m["err"] or "", "out": m["out"][:20000]} for m in members]
    temp = db.get_setting("temperature", 0.7)
    paths, texts, validations = set(), [], []

    def finish(final, meta_extra):
        mid = db.add_message(cid, "assistant", final, "", lead["id"], lmodel, dict(meta_extra, model=lmodel))
        W.save_blocks(cid, final, mid)
        files = []
        for rel in sorted(paths):
            try:
                size = os.path.getsize(W.resolve(cid, rel))
            except (OSError, ValueError):
                continue
            db.add_file(cid, rel, size, mid)
            files.append({"path": rel, "size": size})
        if files:
            meta = dict(meta_extra, model=lmodel, files=files)
            db.update_message(mid, meta=meta)
            emit({"type": "files", "files": files, "conv_id": cid})
        return mid

    if drafts:  # ---- ASSEMBLE + INTEGRATE
        for rel, c in drafts.items():
            W.write_file(cid, rel, c)
            paths.add(rel)
        issues = validate(base, sorted(paths))
        validations.append(issues)
        emit({"type": "council_validation", "round": 0, "issues": issues})
        missing = [m for m in members if m["status"] != "done"]
        r = {"toks": 0, "secs": 1}
        rounds, cap = 0, max(0, min(int(db.get_setting("council_fix_rounds", 2) or 0), 3))
        while True:
            sysmsg = M.build_system(
                "You are the council orchestrator. Your job is to make the project WORK: check that the files fit together (element ids, "
                "function names, file references, game loop, controls) and that nothing is missing. If anything is missing, broken or "
                "inconsistent, output the corrected COMPLETE files as file: blocks (only files that change; never truncate; no placeholders). "
                "Do not repeat unchanged files. Finish with a short summary and how to run it.")
            umsg = "OPERATOR TASK:\n%s\n\nSHARED SPEC:\n%s\n\nFILES PRODUCED SO FAR:\n%s\n\nAUTOMATIC VALIDATION REPORT:\n%s" % (
                inp["text"], spec or "(none)", _read_files(cid, paths),
                "\n".join("- %s: %s" % (i["path"], i["problem"]) for i in issues) or "no problems found by the automatic checks")
            if missing:
                umsg += "\n\nThese members FAILED, so their files may be missing: " + "; ".join(
                    "%s (files: %s)" % (m["ref"], ", ".join(m["files"]) or "unspecified") for m in missing)
            emit({"type": "council_lead", "status": "integrating" if rounds == 0 else "fixing"})
            try:
                r = M.stream(task, emit, lead, lmodel, [{"role": "system", "content": sysmsg}] + (ctx if rounds == 0 else []) + [{"role": "user", "content": umsg}], temp)
            except P.Cancelled:
                emit({"type": "aborted"})
                return
            except P.ProviderError as e:
                emit({"type": "error", "message": "Orchestrator failed: %s The members' draft files were still saved." % e})
                texts.append("[orchestrator failed: %s]\nDraft files from the members were saved as they were." % e)
                break
            out = r["content"].strip()
            texts.append(out if rounds == 0 else "\n\n---\nFix round %d\n\n%s" % (rounds, out))
            saved, skipped = W.save_blocks(cid, out)
            paths |= {s["path"] for s in saved}
            for path, why in skipped:
                emit({"type": "notice", "text": "File '%s' not saved: %s" % (path, why)})
            issues = validate(base, sorted(paths))
            validations.append(issues)
            emit({"type": "council_validation", "round": rounds + 1, "issues": issues})
            if not issues or rounds >= cap:
                break
            rounds += 1
            emit({"type": "notice", "text": "Validation found %d problem(s); the orchestrator is repairing them (round %d)." % (len(issues), rounds)})
            emit({"type": "token", "text": "\n\n\u2014 fix round %d \u2014\n\n" % rounds})
        meta = _m(lmodel, r, council={"lead": lead_ref, "fallback": fallback, "spec": spec, "members": summary(), "validation": validations[-1]})
        finish("".join(texts), meta)
        emit({"type": "done", "meta": meta})
        return

    # ---- no files: plain merge
    ok = [m for m in members if m["status"] == "done"]
    blocks = ["### Member %d (%s) \u2014 subtask: %s\n%s" % (m["idx"] + 1, m["ref"], m["sub"], m["out"] if m["status"] == "done" else "FAILED: " + (m["err"] or "no output")) for m in members]
    sysmsg = M.build_system("You are the council lead. Combine the members' work into ONE final answer for the operator. Resolve conflicts, "
                            "remove duplication, mention failed members only briefly.")
    umsg = "OPERATOR TASK:\n%s\n\nCOUNCIL RESULTS:\n%s" % (inp["text"], "\n\n".join(blocks)) + ("" if ok else "\n\nEvery member failed. Answer the task directly yourself.")
    emit({"type": "council_lead", "status": "merging"})
    try:
        r = M.stream(task, emit, lead, lmodel, [{"role": "system", "content": sysmsg}] + ctx + [{"role": "user", "content": umsg}], temp)
        final = r["content"].strip()
    except P.Cancelled:
        emit({"type": "aborted"})
        return
    except P.ProviderError as e:
        final, r = "[lead failed: %s]\n\n%s" % (e, "\n\n".join(blocks)), {"toks": 0, "secs": 1}
        emit({"type": "error", "message": "Lead model failed: %s Showing the raw member results." % e})
        emit({"type": "token", "text": final})
    meta = _m(lmodel, r, council={"lead": lead_ref, "fallback": fallback, "spec": spec, "members": summary()})
    finish(final, meta)
    emit({"type": "done", "meta": meta})


def _m(model, r, **extra):
    return M._meta(model, r, **extra)


# =====================================================================================================
#  CANDIDATES strategy: best-of-N, every candidate actually tested, repairs that can never make it worse
# =====================================================================================================
GAME = re.compile(r"\b(game|snake|runner|platformer|tetris|pong|flappy|shooter|arcade|breakout|asteroids|invaders|racing|dodge)\b", re.I)
BUILD = re.compile(r"\b(build|make|create|write|code|develop|generate|design|program|implement)\b.{0,80}\b(game|app|application|website|web ?site|web ?page|page|tool|calculator|script|program|simulator|clone|dashboard|bot|extension|visuali[sz]er|widget|timer|todo|quiz|runner|platformer|tetris|snake|pong|flappy)\b", re.I | re.S)
RECIPES = {
    "game": {
        "rules": ["Use one <canvas> (2D context) with a fixed logical size, scaled to fit the window (also on resize).",
                  "Drive everything from requestAnimationFrame with a clamped delta time (never a setInterval game loop).",
                  "Three states: start screen, playing, game over. Restart with Space, Enter, click or tap.",
                  "Keyboard AND mouse/touch controls; call preventDefault() on the game keys so the page never scrolls.",
                  "Difficulty ramps over time; obstacles/enemies spawn at randomised but always fair intervals.",
                  "Collision detection, a score, a best score (a variable is fine) and pause on P.",
                  "Draw with shapes and gradients only: no images, fonts, libraries or network.",
                  "Declare every variable and function before use. No console errors, no TODOs, no placeholders, no truncated code."],
        "checks": [r"requestAnimationFrame", r"addEventListener\(\s*['\"]key", r"getContext\(\s*['\"]2d", r"game ?over|state\s*=\s*['\"]over", r"score", r"restart|reset", r"touch|mouse|pointer|click"]},
    "web": {
        "rules": ["One self-contained file: semantic HTML, inline CSS, inline JavaScript. No external libraries, fonts or network.",
                  "Responsive (meta viewport), keyboard friendly, labelled controls, clear empty and error states.",
                  "Keep state in JavaScript variables; if you use localStorage wrap it in try/catch.",
                  "Declare every variable and function before use. No console errors, no TODOs, no placeholders, no truncated code."],
        "checks": [r"<!doctype html", r"addEventListener", r"<style", r'name="viewport"', r"<script"]},
}


def looks_build(text):
    t = text or ""
    return bool(BUILD.search(t) or (GAME.search(t) and re.search(r"\b(make|build|create|write|code|generate|develop)\b", t, re.I)))


def recipe_for(text):
    return "game" if GAME.search(text or "") else "web"


def smoke(base, entry):
    """Run the page headlessly in Node (smoke.js) and report runtime errors. None if Node is not installed."""
    node, runner = shutil.which("node"), assets.path("smoke.js")
    if not node or not os.path.isfile(runner):
        return None
    try:
        # Model-written code is executed here, so Node runs with its permission model when it has one (Node 22.13+): read-only access to
        # the page folder and smoke.js only, no writes, no child processes, no workers. Node's permission model does not restrict the
        # network, and node:vm is not a security boundary by itself; the real limits are those listed here plus a fake browser with no fetch.
        base_abs, runner_dir = os.path.realpath(base), os.path.dirname(os.path.realpath(runner))
        args = [node, "--permission", "--allow-fs-read=" + base_abs, "--allow-fs-read=" + runner_dir, runner, base, entry]
        r = subprocess.run(args, capture_output=True, text=True, timeout=40)
        if r.returncode != 0 and not r.stdout.strip() and re.search(r"bad option|--permission|not allowed", r.stderr or "", re.I):
            r = subprocess.run([node, runner, base, entry], capture_output=True, text=True, timeout=40)     # an older Node without the flag
        return json.loads(r.stdout.strip().splitlines()[-1])
    except subprocess.TimeoutExpired:
        return {"ok": False, "errors": ["the page froze the test (infinite loop?)"], "warnings": [], "frames": 0, "draws": 0, "raf": 0}
    except (ValueError, IndexError, OSError):
        return None


def quality(base, paths, recipe=None):
    """Score a set of files 0-100 and say whether they are 'valid' (no syntax problems, no runtime errors in the smoke test)."""
    paths = sorted(paths)
    issues = validate(base, paths)
    entry = next((p for p in paths if p.lower() == "index.html"), next((p for p in paths if p.lower().endswith((".html", ".htm"))), None))
    sm = smoke(base, entry) if entry else None
    texts = []
    for p in paths:
        try:
            texts.append(open(_join(base, p), encoding="utf-8", errors="replace").read())
        except (OSError, ValueError):
            pass
    blob, total = "\n".join(texts), sum(len(t) for t in texts)
    syntax_bad = any(re.search(r"syntax error|invalid JSON", i["problem"]) for i in issues)
    checks = (recipe or {}).get("checks", [])
    s = (30 if total > 400 else 0) + (0 if syntax_bad else 30) + (10 if sm is None else 30 if sm["ok"] else 0)
    s += round(10 * sum(1 for rx in checks if re.search(rx, blob, re.I)) / (len(checks) or 1))
    s -= 10 * sum(1 for i in issues if "placeholder" in i["problem"] or "does not exist" in i["problem"])
    return {"score": max(0, min(100, s)), "valid": not issues and (sm is None or sm["ok"]) and total > 400, "issues": issues, "smoke": sm, "size": total}


def _brief_line(q):
    bits = ["score %d" % q["score"], "valid" if q["valid"] else "NOT valid"]
    if q["smoke"]:
        sm = q["smoke"]
        bits.append("ran %d frames, %d draw calls, %d error(s)" % (sm.get("frames", 0), sm.get("draws", 0), len(sm.get("errors", []))))
    return " · ".join(bits)


def _problems(q, limit=4):
    out = ["%s: %s" % (i["path"], i["problem"]) for i in q["issues"]]
    out += ["runtime: %s" % e for e in (q["smoke"] or {}).get("errors", [])]
    return out[:limit]


_stat_lock = threading.Lock()


def _stats():
    return db.get_setting("council_stats") or {}


def _record(ref, ok, secs, valid=False):
    with _stat_lock:
        st = _stats()
        r = st.setdefault(ref, {"runs": 0, "ok": 0, "fail": 0, "valid": 0, "secs": 0.0, "streak": 0})
        r["runs"] += 1
        r["secs"] = round(r["secs"] + secs, 1)
        if ok:
            r["ok"] += 1
            r["streak"] = 0
        else:
            r["fail"] += 1
            r["streak"] += 1
        r["valid"] += 1 if valid else 0
        db.set_settings({"council_stats": st})


def reset_stats():
    db.set_settings({"council_stats": {}})


def auto_members(n=3):
    """Pick up to n models from the enabled providers, best recent record first (valid rate, then speed)."""
    stats, cands = _stats(), []
    for r in db.list_providers():
        if r["kind"] != "llm" or not r["enabled"]:
            continue
        p = db.get_provider(r["id"])
        if (not p["is_local"] and not p["api_key"]) or not p["model"]:
            continue
        ref = "%s:%s" % (p["id"], p["model"])
        st = stats.get(ref, {})
        runs = st.get("runs", 0)
        cands.append((-((st.get("valid", 0) + 1) / (runs + 2)), (st.get("secs", 0) / runs) if runs else 120, ref))
    return [c[2] for c in sorted(cands)[:n]]


def _brief(task, lead, lmodel, text, recipe_name):
    sysmsg = ("You are the lead designer of a build council. Write a concise BUILD BRIEF (max 250 words, plain text, no code) for the request: the exact "
              "deliverable (one self-contained index.html unless impossible), the concrete mechanics or features, controls, visual style, key states, "
              "and three pitfalls to avoid. " + ("It is a browser game: specify game feel and difficulty curve." if recipe_name == "game" else ""))
    try:
        with P.slot(lead, task):
            out = P.complete(lead, lmodel, M.apply_think([{"role": "system", "content": sysmsg}, {"role": "user", "content": text}], lmodel), 0.4, task)
        return out.strip()[:2500]
    except P.Cancelled:
        raise
    except Exception:
        return ""


def _candidate_msgs(inp, brief, recipe):
    sysmsg = ((db.get_setting("system_prompt") or db.DEFAULT_SYSTEM) +
              "\n\nYou are a member of a build council. Write the COMPLETE deliverable as ONE self-contained file: a single fenced block with info string "
              "file:index.html containing inline CSS and JavaScript, no external libraries, no network access, no placeholders, no truncation. It must run "
              "correctly when opened by double-clicking. At most two sentences before the file. Checklist:\n- " + "\n- ".join(recipe["rules"]))
    body = "TASK:\n%s\n\nBUILD BRIEF:\n%s" % (inp["text"], brief or "(none: use your best judgement)")
    return [{"role": "system", "content": sysmsg}, {"role": "user", "content": M.build_user(inp, body)}]


def _extract_files(out):
    """Closed file: blocks from a model answer; a bare HTML block counts as index.html."""
    files, bare = {}, None
    for b in W.parse_blocks(out):
        if not b["closed"]:
            continue
        raw = W.file_path_of(b["info"])
        if raw:
            try:
                files[W.safe_rel(raw)] = b["content"] + "\n"
            except ValueError:
                pass
        elif bare is None and re.search(r"<!doctype html|<html|<canvas", b["content"], re.I):
            bare = b["content"] + "\n"
    if not files and bare:
        files["index.html"] = bare
    return files


def _eval_files(files, recipe):
    tmp = tempfile.mkdtemp(prefix="nyxor-cand-")
    try:
        for rel, c in files.items():
            full = _join(tmp, rel)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8") as f:
                f.write(c)
        return quality(tmp, list(files), recipe)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _candidates(task, emit, cid, text, inp, lead, lmodel, lead_ref, members, ctx):
    rname = recipe_for(text)
    recipe = RECIPES[rname]
    temp = db.get_setting("temperature", 0.7)
    emit({"type": "council_strategy", "strategy": "candidates", "recipe": rname})
    stats = _stats()
    live = [m for m in members if not m["err"] and stats.get(m["ref"], {}).get("streak", 0) < 3]
    if live:
        for m in members:
            if m not in live and not m["err"]:
                m["err"], m["status"] = "skipped: failed 3 times in a row (reset the record in SYSTEMS \u2192 COUNCIL)", "skipped"
    emit({"type": "council_lead", "status": "designing"})
    try:
        brief = _brief(task, lead, lmodel, inp["text"], rname)
    except P.Cancelled:
        emit({"type": "aborted"})
        return
    for m in members:
        m["sub"], m["files"] = "Write the complete, self-contained index.html", ["index.html"]
    emit({"type": "council_plan", "subtasks": [m["sub"] for m in members], "titles": ["candidate"] * len(members),
          "files": [m["files"] for m in members], "spec": brief, "fallback": not brief})
    elock = threading.Lock()

    def sem(ev):
        with elock:
            emit(ev)

    def work(m):
        if m["err"]:
            m["status"] = m["status"] if m["status"] == "skipped" else "failed"
            return sem({"type": "member_status", "idx": m["idx"], "status": m["status"], "error": m["err"]})
        ct = task.child()
        m["ct"], m["t0"] = ct, time.time()
        sem({"type": "member_status", "idx": m["idx"], "status": "running"})
        try:
            with P.slot(m["p"], ct):
                m["t0"] = time.time()
                r = M.stream(ct, sem, m["p"], m["model"], _candidate_msgs(inp, brief, recipe), temp, member=m["idx"])
            m["out"] = r["content"].strip()
            m["files_out"] = _extract_files(m["out"])
            if not m["files_out"]:
                raise P.ProviderError("no complete file came back (the answer had no closed file block; it may have been cut off)")
            m["q"] = _eval_files(m["files_out"], recipe)
            m["status"] = "done"
            _record(m["ref"], True, time.time() - m["t0"], m["q"]["valid"])
            sem({"type": "council_candidate", "idx": m["idx"], "score": m["q"]["score"], "valid": m["q"]["valid"], "problems": _problems(m["q"]), "line": _brief_line(m["q"])})
            sem({"type": "member_status", "idx": m["idx"], "status": "done"})
        except P.Cancelled:
            if m.get("timeout"):
                m["status"], m["err"] = "failed", "timed out after %ds" % int(db.get_setting("council_member_timeout") or 300)
                _record(m["ref"], False, time.time() - m["t0"])
            elif m.get("early"):
                m["status"], m["err"] = "cancelled", "not needed: a good candidate was already finished"
            else:
                m["status"] = "aborted"
            sem({"type": "member_status", "idx": m["idx"], "status": m["status"], "error": m["err"]})
        except Exception as e:
            m["status"], m["err"] = "failed", str(e)
            _record(m["ref"], False, time.time() - m.get("t0", time.time()))
            sem({"type": "member_status", "idx": m["idx"], "status": "failed", "error": str(e)})

    threads = [threading.Thread(target=work, args=(m,), daemon=True) for m in members]
    for t in threads:
        t.start()
    limit = int(db.get_setting("council_member_timeout") or 300)
    grace = int(db.get_setting("council_grace") if db.get_setting("council_grace") is not None else 15)
    first_good = None
    while any(t.is_alive() for t in threads):
        time.sleep(0.2)
        if task.cancel.is_set():
            break
        now = time.time()
        for m in members:
            if m.get("ct") and m["status"] == "pending" and now - m["t0"] > limit and m["t0"]:
                m["timeout"] = True
                m["ct"].abort()
        if first_good is None and any(m.get("q") and m["q"]["valid"] and m["q"]["score"] >= 85 for m in members):
            first_good = now
        if first_good is not None and len(members) > 1 and now - first_good > grace:
            for m in members:
                if m.get("ct") and m["status"] == "pending" and m["out"] == "" and "q" not in m:
                    m["early"] = True
                    m["ct"].abort()
            first_good = float("inf")
    for t in threads:
        t.join(2)
    if task.cancel.is_set():
        emit({"type": "aborted"})
        return
    for m in members:
        if m["status"] == "pending":
            m["status"] = "failed"

    done = [m for m in members if m.get("q")]
    rejected = []
    if not done:           # nobody delivered: the orchestrator builds it alone
        emit({"type": "notice", "text": "No member delivered a usable file; the orchestrator is building it alone."})
        emit({"type": "council_lead", "status": "building alone"})
        try:
            with P.slot(lead, task):
                r = M.stream(task, lambda ev: None, lead, lmodel, _candidate_msgs(inp, brief, recipe), temp)
            files = _extract_files(r["content"])
        except P.Cancelled:
            emit({"type": "aborted"})
            return
        except P.ProviderError as e:
            emit({"type": "error", "message": "The orchestrator could not build it either: %s" % e})
            return
        if not files:
            emit({"type": "error", "message": "The orchestrator's answer had no complete file (cut off?). Try a model with a larger output limit."})
            return
        winner = {"idx": None, "ref": lead_ref, "files_out": files, "q": _eval_files(files, recipe), "out": r["content"], "role": "orchestrator", "sub": "", "err": "", "status": "done"}
    else:
        winner = max(done, key=lambda m: (m["q"]["valid"], m["q"]["score"], -(m.get("t0") or 0)))
    emit({"type": "council_winner", "idx": winner["idx"], "ref": winner["ref"], "line": _brief_line(winner["q"])})
    rejected = [m for m in done if m is not winner]

    paths = set()
    for rel, c in winner["files_out"].items():
        W.write_file(cid, rel, c)
        paths.add(rel)
    base = os.path.realpath(W.conv_dir(cid))
    cur = winner["q"]
    emit({"type": "council_validation", "round": 0, "issues": [{"path": i["path"], "problem": i["problem"]} for i in cur["issues"]] +
          [{"path": "runtime", "problem": e} for e in (cur["smoke"] or {}).get("errors", [])]})

    # ---- challenge: the other members review the winner (short, parallel, never blocks long)
    reviews = []
    reviewers = [m for m in done if m is not winner and m["p"]][:2]
    if db.get_setting("council_review") and reviewers:
        emit({"type": "council_lead", "status": "reviewing"})
        rlock = threading.Lock()
        winfile = "\n\n".join("=== %s ===\n````\n%s````" % (rel, c[:30000]) for rel, c in winner["files_out"].items())

        def review(m):
            ct = task.child()
            sysm = "You are a meticulous code reviewer on a build council. Reply with at most 6 terse bullets: concrete defects or missing features that would hurt the result or cause errors. No code, no praise."
            body = "TASK:\n%s\n\nBRIEF:\n%s\n\nWINNING CANDIDATE:\n%s\n\nAUTOMATIC CHECKS: %s\n%s" % (inp["text"][:2000], brief[:1200], winfile, _brief_line(cur), "\n".join(_problems(cur)))
            try:
                with P.slot(m["p"], ct):
                    out = P.complete(m["p"], m["model"], M.apply_think([{"role": "system", "content": sysm}, {"role": "user", "content": body}], m["model"]), 0.3, ct)
                with rlock:
                    reviews.append(out.strip()[:1500])
                sem({"type": "council_review", "idx": m["idx"], "text": out.strip()[:600]})
            except Exception:
                pass
        rts = [threading.Thread(target=review, args=(m,), daemon=True) for m in reviewers]
        for t in rts:
            t.start()
        t0 = time.time()
        while any(t.is_alive() for t in rts) and time.time() - t0 < 90 and not task.cancel.is_set():
            time.sleep(0.2)
        if task.cancel.is_set():
            emit({"type": "aborted"})
            return

    # ---- repair / polish: guarded, so a repair can never make things worse
    cap = max(0, min(int(db.get_setting("council_fix_rounds") or 0), 3))
    snapshot = {rel: c for rel, c in winner["files_out"].items()}
    repairs = []
    for rnd in range(1, cap + 1):
        need = bool(cur["issues"] or (cur["smoke"] and not cur["smoke"]["ok"]) or (rnd == 1 and reviews))
        if not need:
            break
        emit({"type": "council_lead", "status": "repairing (round %d)" % rnd})
        sysmsg = M.build_system("You are the council orchestrator. Improve the winning candidate with MINIMAL, targeted fixes. Never rewrite it from scratch and never remove working features. "
                                "Output the COMPLETE corrected file as a single fenced block with info string file:index.html (same file name), no truncation, no placeholders.")
        umsg = "TASK:\n%s\n\nBUILD BRIEF:\n%s\n\nWINNING CANDIDATE:\n%s\n\nAUTOMATIC CHECKS (%s):\n%s\n\nREVIEWERS' NOTES:\n%s" % (
            inp["text"][:2000], brief[:1200], "\n\n".join("=== %s ===\n````\n%s````" % (r_, c_[:40000]) for r_, c_ in snapshot.items()), _brief_line(cur),
            "\n".join(_problems(cur, 8)) or "all automatic checks passed", "\n---\n".join(reviews) or "none")
        try:
            with P.slot(lead, task):
                r = M.stream(task, lambda ev: None, lead, lmodel, [{"role": "system", "content": sysmsg}, {"role": "user", "content": umsg}], temp)
        except P.Cancelled:
            emit({"type": "aborted"})
            return
        except P.ProviderError as e:
            emit({"type": "notice", "text": "Repair round %d failed: %s. Keeping the current version." % (rnd, e)})
            break
        newfiles = _extract_files(r["content"])
        if not newfiles:
            repairs.append("round %d: no usable file returned, kept the current version" % rnd)
            emit({"type": "council_repair", "round": rnd, "accepted": False, "reason": "the answer had no complete file", "before": cur["score"], "after": cur["score"]})
            break
        merged = dict(snapshot, **newfiles)
        nq = _eval_files(merged, recipe)
        worse = (cur["valid"] and not nq["valid"]) or nq["score"] < cur["score"]
        emit({"type": "council_repair", "round": rnd, "accepted": not worse, "before": cur["score"], "after": nq["score"],
              "reason": ("it would have broken it: " + "; ".join(_problems(nq, 2))) if worse else _brief_line(nq)})
        if worse:
            repairs.append("round %d rejected (%d \u2192 %d): kept the previous version" % (rnd, cur["score"], nq["score"]))
            break
        snapshot, cur = merged, nq
        for rel, c in newfiles.items():
            W.write_file(cid, rel, c)
            paths.add(rel)
        repairs.append("round %d accepted (%d \u2192 %d)" % (rnd, winner["q"]["score"] if rnd == 1 else cur["score"], nq["score"]))
        if nq["valid"] and not reviews:
            break
        reviews = []          # polish notes only apply once

    emit({"type": "council_validation", "round": len(repairs) + 1, "issues": [{"path": i["path"], "problem": i["problem"]} for i in cur["issues"]] +
          [{"path": "runtime", "problem": e} for e in (cur["smoke"] or {}).get("errors", [])]})
    files_meta, size = [], 0
    mid = None
    entry = "index.html" if "index.html" in paths else sorted(paths)[0]
    lines = ["Built **%s** with a council of %d model%s." % (entry, len(members), "" if len(members) == 1 else "s"),
             "Winner: %s (%s)." % (winner["ref"], _brief_line(winner["q"])),
             "Final check: %s." % _brief_line(cur)]
    if rejected:
        lines.append("Other candidates: " + "; ".join("%s \u2014 %s" % (m["ref"], "ok, %d" % m["q"]["score"] if m["q"]["valid"] else "rejected (" + (_problems(m["q"], 1) or ["low score"])[0] + ")") for m in rejected) + ".")
    for m in members:
        if m["status"] in ("failed", "skipped", "cancelled") and m["err"]:
            lines.append("%s: %s." % (m["ref"], m["err"]))
    if repairs:
        lines.append("Repairs: " + "; ".join(repairs) + ".")
    if not cur["valid"]:
        lines.append("It still has problems: " + "; ".join(_problems(cur, 3)) + ". Ask for another round or try a stronger orchestrator.")
    lines.append("Press PREVIEW on the file below to play it here, or download it and open it in a browser.")
    final = "\n".join(lines)
    summary = [{"ref": m["ref"], "role": m["role"], "sub": m["sub"], "files": m.get("files", []), "status": m["status"], "error": m["err"] or "",
                "out": (m.get("out") or "")[:20000], "score": (m.get("q") or {}).get("score"), "valid": (m.get("q") or {}).get("valid")} for m in members]
    meta = {"model": lmodel, "toks": 0, "tps": 0, "council": {"lead": lead_ref, "strategy": "candidates", "spec": brief, "members": summary,
            "winner": winner["ref"], "validation": _problems(cur, 6)}}
    mid = db.add_message(cid, "assistant", final, "", lead["id"], lmodel, meta)
    for rel in sorted(paths):
        try:
            sz = os.path.getsize(_join(base, rel))
        except (OSError, ValueError):
            continue
        db.add_file(cid, rel, sz, mid)
        files_meta.append({"path": rel, "size": sz})
    meta["files"] = files_meta
    db.update_message(mid, meta=meta)
    emit({"type": "token", "text": final})
    emit({"type": "files", "files": files_meta, "conv_id": cid})
    emit({"type": "done", "meta": meta})
