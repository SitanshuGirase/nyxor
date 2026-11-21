#!/usr/bin/env python3
"""
NYXOR v2.3 - where the program's own files and the user's data live. Works from a normal folder AND from a single-file
build (nyxor.pyz, a standard zipapp). Standard library only.

  read(name)    bytes of a bundled file (index.html, style.css, smoke.js, ...)
  exists(name)  is it bundled
  path(name)    a real file path for a bundled file, for programs that need one (Node runs smoke.js); extracted once when zipped
  DATA_DIR      where nyxor.db and the workspace go: NYXOR_HOME, else the program folder (folder install),
                else a per-user data folder (single-file build): %LOCALAPPDATA%\\NYXOR, ~/Library/Application Support/NYXOR, ~/.local/share/nyxor
"""
import json
import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
IN_ZIP = os.path.isfile(ROOT)                       # inside nyxor.pyz, __file__ is <archive>/assets.py
_zip = None


def _z():
    global _zip
    if _zip is None:
        _zip = zipfile.ZipFile(ROOT)
    return _zip


def _user_dir():
    if os.name == "nt":
        return os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "NYXOR")
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "Application Support", "NYXOR")
    return os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share"), "nyxor")


DATA_DIR = os.environ.get("NYXOR_HOME") or (_user_dir() if IN_ZIP else ROOT)
try:
    os.makedirs(DATA_DIR, exist_ok=True)      # first run on a fresh PC: the per-user data folder does not exist yet
except OSError:
    pass


def exists(name):
    if IN_ZIP:
        try:
            _z().getinfo(name)
            return True
        except KeyError:
            return False
    return os.path.exists(os.path.join(ROOT, name))


def read(name):
    if IN_ZIP:
        return _z().read(name)
    with open(os.path.join(ROOT, name), "rb") as f:
        return f.read()


def path(name):
    """A real file on disk for a bundled file. From a folder that is the file itself; from the archive it is extracted (once per version)."""
    if not IN_ZIP:
        return os.path.join(ROOT, name)
    data = read(name)
    d = os.path.join(DATA_DIR, "_runtime")
    os.makedirs(d, exist_ok=True)
    dest = os.path.join(d, name)
    try:
        if os.path.getsize(dest) == len(data):
            with open(dest, "rb") as f:
                if f.read() == data:
                    return dest
    except OSError:
        pass
    with open(dest, "wb") as f:
        f.write(data)
    return dest


def store_config():
    """store_config.json: the publisher's support address and privacy-policy URL for the Store build (empty until filled in)."""
    try:
        cfg = json.loads(read("store_config.json").decode("utf-8"))
    except (OSError, KeyError, ValueError):
        cfg = {}
    cfg = {k: str(cfg.get(k) or "") for k in ("support_email", "privacy_url", "publisher")}
    cfg["support_email"] = os.environ.get("NYXOR_SUPPORT_EMAIL", cfg["support_email"])
    cfg["privacy_url"] = os.environ.get("NYXOR_PRIVACY_URL", cfg["privacy_url"])
    return cfg
