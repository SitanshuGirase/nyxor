#!/usr/bin/env python3
"""NYXOR v2 - downloadable files: fenced `file:path` blocks, safe paths, zip (Stage 4)."""
import io
import os
import re
import zipfile

import db

MAX_FILE = 5 * 1024 * 1024


def conv_dir(cid):
    return os.path.join(db.WORKSPACE, "conv_%d" % int(cid))


def safe_rel(p):
    """Validate a model-supplied relative path. Blocks .., absolute paths, drive letters, odd characters."""
    if not isinstance(p, str):
        raise ValueError("path must be text")
    p = p.strip().strip("`\"'").replace("\\", "/")
    if not p or "\x00" in p or len(p) > 200:
        raise ValueError("empty or invalid path")
    if p.startswith(("/", "~")) or re.match(r"^[A-Za-z]:", p):
        raise ValueError("absolute paths and drive letters are blocked")
    parts = [x for x in p.split("/") if x not in ("", ".")]
    if not parts or any(x == ".." for x in parts):
        raise ValueError("path traversal is blocked")
    if any(re.search(r'[<>:"|?*\x00-\x1f]', x) for x in parts):
        raise ValueError("illegal characters in path")
    return "/".join(parts)


def _inside(base, full):
    try:
        return os.path.commonpath([base, full]) == base
    except ValueError:
        return False


def resolve(cid, rel):
    """Absolute path of rel inside the conversation folder (realpath-checked)."""
    rel = safe_rel(rel)
    base = os.path.realpath(conv_dir(cid))
    full = os.path.realpath(os.path.join(base, rel))
    if not _inside(base, full):
        raise ValueError("path escapes the conversation folder")
    return full


def parse_blocks(text):
    """Line-based fence parser: a closing fence needs at least as many backticks as the opener,
    so a file may contain ``` if its own fence is longer."""
    lines, out, i = text.split("\n"), [], 0
    while i < len(lines):
        m = re.match(r"^\s{0,3}(`{3,})([^`]*)$", lines[i])
        if not m:
            i += 1
            continue
        n, info, body, j, closed = len(m.group(1)), m.group(2).strip(), [], i + 1, False
        close = re.compile(r"^\s{0,3}`{%d,}\s*$" % n)
        while j < len(lines):
            if close.match(lines[j]):
                closed = True
                break
            body.append(lines[j])
            j += 1
        out.append({"info": info, "content": "\n".join(body), "closed": closed})
        i = j + 1
    return out


def file_path_of(info):
    m = re.search(r"(?:^|\s)file:(.+)$", info or "")
    return m.group(1).strip() if m else None


def write_file(cid, rel, content, message_id=None):
    full = resolve(cid, rel)
    data = content.encode("utf-8")
    if len(data) > MAX_FILE:
        raise ValueError("file too large")
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "wb") as f:
        f.write(data)
    rel = safe_rel(rel)
    db.add_file(cid, rel, len(data), message_id)
    return {"path": rel, "size": len(data)}


def save_blocks(cid, text, message_id=None):
    """Save every closed file:... block. Returns (saved, skipped[(path, reason)])."""
    saved, skipped = [], []
    for b in parse_blocks(text):
        raw = file_path_of(b["info"])
        if raw is None:
            continue
        if not b["closed"]:
            skipped.append((raw, "block was never closed (output cut off), not saved"))
            continue
        try:
            saved.append(write_file(cid, raw, b["content"] + "\n" if b["content"] else "", message_id))
        except (ValueError, OSError) as e:
            skipped.append((raw, str(e)))
    return saved, skipped


def read_file(cid, rel):
    full = resolve(cid, rel)
    if not os.path.isfile(full):
        raise FileNotFoundError(rel)
    with open(full, "rb") as f:
        return f.read()


def zip_conv(cid):
    base = os.path.realpath(conv_dir(cid))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _, names in os.walk(base):
            for n in sorted(names):
                full = os.path.join(root, n)
                if os.path.islink(full) or not _inside(base, os.path.realpath(full)):
                    continue
                z.write(full, os.path.relpath(full, base).replace("\\", "/"))
    return buf.getvalue()


def delete_file(cid, rel):
    """Delete a generated/uploaded file from disk AND from the database (row, chips, extracted text)."""
    full = resolve(cid, rel)
    rel = safe_rel(rel)
    try:
        os.remove(full)
    except FileNotFoundError:
        pass
    db.remove_file_record(cid, rel)
    db.flush(force=True, purge=True)
    # tidy empty folders inside the chat folder
    base = os.path.realpath(conv_dir(cid))
    d = os.path.dirname(full)
    while d != base and _inside(base, d):
        try:
            os.rmdir(d)
        except OSError:
            break
        d = os.path.dirname(d)
