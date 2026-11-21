#!/usr/bin/env python3
"""NYXOR v2 - text-to-speech through the backend, so the browser never sees a key.
OpenAI-compatible /audio/speech (OpenAI and compatible hosts) and ElevenLabs. The browser then plays the audio
through the Ultron effect chain (pitch down, low-end boost, metallic double, light distortion)."""
import json
import socket
import urllib.error
import urllib.request

import providers as P
from providers import ProviderError

DEFAULTS = {"openai": ("tts-1", "onyx"), "elevenlabs": ("eleven_multilingual_v2", "pNInz6obpgDQGcFmaJgB")}


def synth(p, text, voice="", model=""):
    """Returns (audio bytes, mime)."""
    P.consent("cloud_voice")
    text = (text or "").strip()[:1500]
    if not text:
        raise ProviderError("Nothing to say.")
    if not p.get("api_key"):
        raise ProviderError("%s has no API key." % p["name"])
    base = p["base_url"].rstrip("/")
    if p["protocol"] == "elevenlabs":
        m, v = model or DEFAULTS["elevenlabs"][0], voice or DEFAULTS["elevenlabs"][1]
        url, hdr, body = "%s/text-to-speech/%s?output_format=mp3_44100_128" % (base, v), {"xi-api-key": p["api_key"]}, {"text": text, "model_id": m}
    else:
        m, v = model or DEFAULTS["openai"][0], voice or DEFAULTS["openai"][1]
        url, hdr, body = base + "/audio/speech", {"Authorization": "Bearer " + p["api_key"]}, {"model": m, "input": text, "voice": v, "response_format": "mp3"}
    hdr.update({"Content-Type": "application/json", "User-Agent": "NYXOR/2"})
    try:
        with urllib.request.urlopen(urllib.request.Request(url, json.dumps(body).encode(), hdr, method="POST"), timeout=60) as r:
            data = r.read()
            return data, r.headers.get("Content-Type", "audio/mpeg")
    except urllib.error.HTTPError as e:
        raise ProviderError("%s voice request failed (HTTP %d). %s" % (p["name"], e.code, "Check the key, model and voice id." if e.code in (400, 401, 403, 404) else ""), e.code)
    except (urllib.error.URLError, socket.timeout, OSError) as e:
        raise ProviderError("Cannot reach %s: %s" % (p["name"], getattr(e, "reason", e)))
