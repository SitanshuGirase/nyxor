#!/usr/bin/env python3
"""NYXOR v2 - attachments. Images go to vision models as image parts; text, code, .docx and (best-effort) .pdf are
turned into text. Every attachment is also saved to the chat's workspace (uploads/) so it shows in FILES."""
import base64
import binascii
import os
import re
import zipfile
import zlib
from html import unescape
from io import BytesIO

import db
import workspace as W

MAX_FILE = 10 * 1024 * 1024
MAX_COUNT = 6
MAX_TEXT_EACH, MAX_TEXT_TOTAL = 40000, 100000
IMG = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}
MAGIC = {"image/png": b"\x89PNG", "image/jpeg": b"\xff\xd8", "image/gif": b"GIF8", "image/webp": b"RIFF"}
TEXT_EXT = {".txt", ".md", ".py", ".js", ".mjs", ".ts", ".tsx", ".jsx", ".json", ".csv", ".tsv", ".html", ".htm", ".css", ".xml",
            ".yml", ".yaml", ".toml", ".ini", ".cfg", ".env", ".sh", ".bat", ".ps1", ".c", ".h", ".cpp", ".hpp", ".java", ".cs",
            ".go", ".rs", ".rb", ".php", ".sql", ".log", ".lua", ".kt", ".swift", ".vue", ".svelte", ".tex", ".rst", ".gd"}


def docx_text(data):
    with zipfile.ZipFile(BytesIO(data)) as z:
        xml = z.read("word/document.xml").decode("utf-8", "replace")
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<w:tab/>", "\t", xml)
    return unescape(re.sub(r"<[^>]+>", "", xml)).strip()


def _pdf_unescape(s):
    s = re.sub(rb"\\([nrtbf()\\])", lambda m: {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"", b"f": b""}.get(m.group(1), m.group(1)), s)
    return re.sub(rb"\\([0-7]{1,3})", lambda m: bytes([int(m.group(1), 8) & 255]), s)


def pdf_text(data):
    """Best effort: handles simple text PDFs (Flate or plain streams). Scanned, encrypted or font-encoded PDFs yield ''."""
    out = []
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", data, re.S):
        body = m.group(1)
        try:
            body = zlib.decompress(body)
        except zlib.error:
            pass
        if b"BT" not in body:
            continue
        for t in re.finditer(rb"\[((?:[^\]\\]|\\.)*)\]\s*TJ|\(((?:[^()\\]|\\.)*)\)\s*Tj", body, re.S):
            if t.group(1) is not None:
                out.append(b"".join(_pdf_unescape(x) for x in re.findall(rb"\(((?:[^()\\]|\\.)*)\)", t.group(1))))
            else:
                out.append(_pdf_unescape(t.group(2)))
            out.append(b" ")
        out.append(b"\n")
    return re.sub(r"[ \t]+", " ", b"".join(out).decode("latin-1")).strip()


def _clean_name(name):
    name = os.path.basename(str(name or "file").replace("\\", "/"))
    name = re.sub(r'[<>:"|?*\x00-\x1f]', "_", name).strip(". ") or "file"
    return name[:120]


def process(cid, items):
    """items: [{name, b64}] -> {images:[{name,mime,b64}], text, meta:[...], notices:[...]}"""
    res = {"images": [], "text": "", "meta": [], "notices": []}
    total = 0
    for it in (items or [])[:MAX_COUNT]:
        name = _clean_name(it.get("name"))
        try:
            data = base64.b64decode(it.get("b64") or "", validate=False)
        except (binascii.Error, ValueError):
            res["notices"].append("'%s' could not be decoded." % name)
            continue
        if not data or len(data) > MAX_FILE:
            res["notices"].append("'%s' is empty or larger than %d MB, skipped." % (name, MAX_FILE // 2**20))
            continue
        ext = os.path.splitext(name)[1].lower()
        base, i = "uploads/" + name, 1
        while os.path.exists(os.path.join(W.conv_dir(cid), base)):
            i += 1
            base = "uploads/%s_%d%s" % (os.path.splitext(name)[0], i, ext)
        os.makedirs(os.path.join(W.conv_dir(cid), "uploads"), exist_ok=True)
        with open(W.resolve(cid, base), "wb") as f:
            f.write(data)
        db.add_file(cid, base, len(data))
        m = {"name": name, "path": base, "stored": os.path.basename(base), "size": len(data), "kind": "file"}
        mime = IMG.get(ext)
        text = None
        if mime and data.startswith(MAGIC[mime]):
            m["kind"] = "image"
            res["images"].append({"name": name, "mime": mime, "b64": base64.b64encode(data).decode()})
        elif ext == ".docx":
            try:
                text = docx_text(data)
            except Exception:
                res["notices"].append("'%s' is not a readable .docx." % name)
        elif ext == ".pdf":
            text = pdf_text(data)
            if not text:
                res["notices"].append("No text could be extracted from '%s' (scanned or encrypted PDF). Attach it as images instead." % name)
        elif ext in TEXT_EXT or (b"\x00" not in data[:2048] and ext not in IMG):
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                text = data.decode("latin-1")
        else:
            res["notices"].append("'%s' is a binary file NYXOR cannot read; it was saved to FILES only." % name)
        if text:
            text = text[:max(0, min(MAX_TEXT_EACH, MAX_TEXT_TOTAL - total))]
            total += len(text)
            m["kind"] = "text"
            res["text"] += "\n\n--- ATTACHED FILE: %s ---\n%s\n--- END OF %s ---" % (m["stored"], text, m["stored"])
        res["meta"].append(m)
    if len(items or []) > MAX_COUNT:
        res["notices"].append("Only the first %d attachments were used." % MAX_COUNT)
    res["text"] = res["text"].strip()
    return res
