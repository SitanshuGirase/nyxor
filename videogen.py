#!/usr/bin/env python3
"""
NYXOR v2 - video generation back-ends. Jobs run in background threads; the UI polls /api/video/jobs.

  colab      a free Google Colab GPU running nyxor_colab_video.ipynb (open model, tunnelled to a public URL;
             paste the connect string it prints). VRAM comes from Google, nothing from your PC.
  replicate  Replicate predictions API (paste an r8_ token, set a text-to-video model such as owner/model-name)
  fal        fal.ai queue API (paste your key, set a model id such as fal-ai/ltx-video)
"""
import json
import os
import socket
import threading
import time
import urllib.error
import urllib.request
import uuid

import db
import providers as P
from providers import Cancelled, ProviderError

MAX_VIDEO = 600 * 1024 * 1024
POLL = 3.0          # seconds between status checks (selftest lowers it)
_cancel = {}


def video_dir():
    d = os.path.join(db.WORKSPACE, "videos")
    os.makedirs(d, exist_ok=True)
    return d


def _req(method, url, headers=None, body=None, timeout=60):
    h = {"User-Agent": "NYXOR/2", "Accept": "application/json"}
    h.update(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data, h, method=method), timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        hint = " The key/token was rejected." if e.code in (401, 403) else ""
        raise ProviderError("Video service answered HTTP %d.%s %s" % (e.code, hint, e.read()[:200].decode("utf-8", "replace")), e.code)
    except (urllib.error.URLError, socket.timeout, OSError, ValueError) as e:
        raise ProviderError("Cannot reach the video service: %s" % getattr(e, "reason", e))


def _download(url, dest, headers, cancel):
    req = urllib.request.Request(url, headers=dict({"User-Agent": "NYXOR/2"}, **(headers or {})))
    try:
        with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
            total = 0
            while True:
                if cancel.is_set():
                    raise Cancelled()
                chunk = r.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_VIDEO:
                    raise ProviderError("Video is larger than %d MB, aborted." % (MAX_VIDEO // 2**20))
                f.write(chunk)
    except (urllib.error.URLError, socket.timeout, OSError) as e:
        raise ProviderError("Video download failed: %s" % getattr(e, "reason", e))
    if os.path.getsize(dest) < 16:
        raise ProviderError("The video service returned an empty file.")


def _wait(cancel, secs):
    if cancel.wait(secs):
        raise Cancelled()


def _find_url(o):
    if isinstance(o, str):
        return o if o.startswith("http") and (".mp4" in o or ".webm" in o or ".mov" in o) else None
    if isinstance(o, dict):
        v = o.get("video")
        if isinstance(v, dict) and isinstance(v.get("url"), str):
            return v["url"]
        for x in o.values():
            u = _find_url(x)
            if u:
                return u
    if isinstance(o, list):
        for x in o:
            u = _find_url(x)
            if u:
                return u
    return None


def _colab(p, jid, prompt, params, dest, cancel, progress):
    base = p["base_url"].rstrip("/")
    if not base:
        raise ProviderError("Colab is not connected. Run the notebook and paste its connect string in VIDEO STUDIO.")
    h = {"Authorization": "Bearer " + p["api_key"]} if p["api_key"] else {}
    rid = (_req("POST", base + "/generate", h, dict(params, prompt=prompt)).get("job"))
    if not rid:
        raise ProviderError("The Colab server did not return a job id.")
    while True:
        if cancel.is_set():
            try:
                _req("POST", "%s/cancel/%s" % (base, rid), h, {}, 10)
            except ProviderError:
                pass
            raise Cancelled()
        s = _req("GET", "%s/job/%s" % (base, rid), h)
        progress(float(s.get("progress") or 0))
        if s.get("status") == "done":
            break
        if s.get("status") in ("error", "cancelled"):
            raise ProviderError("Colab job failed: %s" % (s.get("error") or s.get("status")))
        _wait(cancel, POLL)
    _download("%s/video/%s.mp4" % (base, rid), dest, h, cancel)


def _replicate(p, jid, prompt, params, dest, cancel, progress):
    model = (p["model"] or "").strip()
    if not model:
        raise ProviderError("Set a model for Replicate in VIDEO STUDIO (owner/model-name from replicate.com/collections/text-to-video).")
    h, base = {"Authorization": "Bearer " + p["api_key"]}, p["base_url"].rstrip("/")
    if ":" in model:
        _, ver = model.split(":", 1)
        j = _req("POST", base + "/predictions", h, {"version": ver, "input": {"prompt": prompt}})
    else:
        j = _req("POST", "%s/models/%s/predictions" % (base, model), h, {"input": {"prompt": prompt}})
    get = (j.get("urls") or {}).get("get")
    if not get:
        raise ProviderError("Replicate did not return a prediction URL.")
    n = 0
    while j.get("status") not in ("succeeded", "failed", "canceled"):
        if cancel.is_set():
            try:
                _req("POST", (j.get("urls") or {}).get("cancel", ""), h, {}, 10)
            except ProviderError:
                pass
            raise Cancelled()
        _wait(cancel, POLL)
        j = _req("GET", get, h)
        n += 1
        progress(min(0.95, n / 60))
    if j["status"] != "succeeded":
        raise ProviderError("Replicate job %s: %s" % (j["status"], j.get("error") or ""))
    url = _find_url(j.get("output"))
    if not url:
        raise ProviderError("Replicate finished but returned no video URL.")
    _download(url, dest, {}, cancel)


def _fal(p, jid, prompt, params, dest, cancel, progress):
    model = (p["model"] or "fal-ai/ltx-video").strip().strip("/")
    h = {"Authorization": "Key " + p["api_key"]}
    j = _req("POST", "%s/%s" % (p["base_url"].rstrip("/"), model), h, {"prompt": prompt})
    status_url, resp_url = j.get("status_url"), j.get("response_url")
    if not (status_url and resp_url):
        raise ProviderError("fal.ai did not return queue URLs.")
    n = 0
    while True:
        if cancel.is_set():
            raise Cancelled()
        s = _req("GET", status_url, h)
        if s.get("status") == "COMPLETED":
            break
        if s.get("status") in ("FAILED", "ERROR"):
            raise ProviderError("fal.ai job failed: %s" % (s.get("error") or s.get("status")))
        n += 1
        progress(min(0.95, n / 60))
        _wait(cancel, POLL)
    url = _find_url(_req("GET", resp_url, h))
    if not url:
        raise ProviderError("fal.ai finished but returned no video URL.")
    _download(url, dest, {}, cancel)


BACKENDS = {"colab": _colab, "replicate": _replicate, "fal": _fal}


def _run(jid):
    job = db.get_video_job(jid)
    cancel = _cancel[jid]
    try:
        p = db.get_provider(job["backend"])
        db.update_video_job(jid, status="running")
        dest = os.path.join(video_dir(), jid + ".mp4")
        BACKENDS[p["protocol"]](p, jid, job["prompt"], json.loads(job["params"] or "{}"), dest, cancel, lambda x: db.update_video_job(jid, progress=x))
        db.update_video_job(jid, status="done", progress=1.0, path=dest)
    except Cancelled:
        db.update_video_job(jid, status="cancelled")
    except ProviderError as e:
        db.update_video_job(jid, status="error", error=str(e))
    except Exception as e:  # never let a thread die silently
        db.update_video_job(jid, status="error", error="Unexpected error: %s" % e)
    finally:
        _cancel.pop(jid, None)


def submit(backend_id, prompt, params):
    p = db.get_provider(backend_id)
    if not p or p.get("kind") != "video":
        raise ProviderError("Unknown video backend '%s'." % backend_id)
    P.consent("cloud_video")
    if not prompt.strip():
        raise ProviderError("Write a prompt first.")
    if p["protocol"] == "colab" and not p["base_url"]:
        raise ProviderError("Colab is not connected yet. Run the notebook and paste its connect string.")
    if p["protocol"] != "colab" and not p["api_key"]:
        raise ProviderError("%s has no API key." % p["name"])
    clean = {}
    for k, lo, hi in (("steps", 1, 100), ("frames", 9, 97), ("fps", 4, 30), ("seed", 0, 2**31 - 1)):
        if params.get(k) not in (None, ""):
            clean[k] = max(lo, min(int(params[k]), hi))
    jid = uuid.uuid4().hex[:12]
    db.create_video_job(jid, backend_id, prompt.strip()[:2000], clean)
    _cancel[jid] = threading.Event()
    threading.Thread(target=_run, args=(jid,), daemon=True).start()
    return jid


def active():
    return bool(_cancel)


def cancel_all():
    for ev in list(_cancel.values()):
        ev.set()


def cancel(jid):
    ev = _cancel.get(jid)
    if ev:
        ev.set()
    return bool(ev)


def test(p):
    t0 = time.time()
    try:
        if p["protocol"] == "colab":
            if not p["base_url"]:
                return {"ok": False, "error": "No URL yet. Run the Colab notebook and paste its connect string."}
            h = {"Authorization": "Bearer " + p["api_key"]} if p["api_key"] else {}
            j = _req("GET", p["base_url"].rstrip("/") + "/health", h, timeout=15)
            return {"ok": True, "latency_ms": int((time.time() - t0) * 1000), "message": "Colab online \u00b7 %s \u00b7 %s" % (j.get("gpu", "GPU"), j.get("model", ""))}
        if p["protocol"] == "replicate":
            _req("GET", p["base_url"].rstrip("/") + "/account", {"Authorization": "Bearer " + p["api_key"]}, timeout=15)
            return {"ok": True, "latency_ms": int((time.time() - t0) * 1000), "message": "token accepted"}
        return {"ok": bool(p["api_key"]), "latency_ms": 0, "message": "key saved (fal.ai has no free test call; it is checked when you generate)", "error": "no key"}
    except ProviderError as e:
        return {"ok": False, "error": str(e)}
