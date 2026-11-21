#!/usr/bin/env python3
"""
NYXOR v2 - encrypted vault for nyxor.db (Python standard library only).

On disk the database is NOT a SQLite file: it is an opaque blob that looks like random bytes. The real SQLite
database only ever exists in memory, after you unlock the vault in the browser.

  random 64-byte master key  (32 encryption + 32 MAC)
  key slots (like LUKS): the master key wrapped by scrypt(passphrase) and by scrypt(recovery code)
  body cipher : BLAKE2b keyed PRF in counter mode (fresh random nonce on every save)
  integrity   : HMAC-SHA256 over header + nonce + ciphertext (encrypt-then-MAC); tampering or a wrong key is detected
  file layout : MAGIC | header length | header JSON (salts, KDF costs, wrapped keys) | nonce | ciphertext | MAC
  writes      : temp file -> fsync -> atomic replace, previous version kept as nyxor.db.bak

Honest limits: this is a careful construction from standard primitives (scrypt, BLAKE2b, HMAC-SHA256), but it is
home-made because the standard library has no AES. It is not independently audited. It protects the file at rest
(someone copying or opening nyxor.db); it cannot protect against malware on this PC while the vault is unlocked.
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import struct
import threading

MAGIC = b"\x93NYXVLT1"
SQLITE_HEADER = b"SQLite format 3\x00"
ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"


class VaultError(Exception):
    pass


class BadSecret(VaultError):
    pass


class Corrupt(VaultError):
    pass


def _kdf_params():
    if os.environ.get("NYXOR_KDF_FAST") == "1":      # self-test only
        return {"kdf": "scrypt", "n": 2 ** 8, "r": 8, "p": 1}
    if hasattr(hashlib, "scrypt"):
        return {"kdf": "scrypt", "n": 2 ** 15, "r": 8, "p": 1}
    return {"kdf": "pbkdf2", "iter": 600000}


def _derive(secret, salt, prm):
    b = secret.encode("utf-8")
    if prm["kdf"] == "scrypt":
        return hashlib.scrypt(b, salt=salt, n=prm["n"], r=prm["r"], p=prm["p"], maxmem=128 * 2 ** 20, dklen=64)
    return hashlib.pbkdf2_hmac("sha256", b, salt, prm["iter"], 64)


def _stream(key, nonce, n):
    blocks = (n + 63) // 64
    ks = b"".join(hashlib.blake2b(nonce + i.to_bytes(8, "big"), key=key, digest_size=64).digest() for i in range(blocks))
    return ks[:n]


def _xor(a, b):
    return (int.from_bytes(a, "big") ^ int.from_bytes(b, "big")).to_bytes(len(a), "big") if a else b""


def _mac(key, *parts):
    h = hmac.new(key, digestmod="sha256")
    for p in parts:
        h.update(p)
    return h.digest()


def norm_recovery(s):
    return re.sub(r"[^A-Z2-7]", "", (s or "").upper())


def new_recovery_code():
    raw = "".join(secrets.choice(ALPHABET) for _ in range(24))
    return "-".join(raw[i:i + 4] for i in range(0, 24, 4))


def _make_slot(master, secret, typ):
    prm, salt, nonce = _kdf_params(), os.urandom(16), os.urandom(16)
    kek = _derive(secret, salt, prm)
    ct = _xor(master, _stream(kek[:32], nonce, len(master)))
    return dict(prm, t=typ, salt=salt.hex(), nonce=nonce.hex(), ct=ct.hex(), mac=_mac(kek[32:], b"slot", nonce, ct).hex())


def _open_slot(slot, secret):
    kek = _derive(secret, bytes.fromhex(slot["salt"]), slot)
    nonce, ct = bytes.fromhex(slot["nonce"]), bytes.fromhex(slot["ct"])
    if not hmac.compare_digest(_mac(kek[32:], b"slot", nonce, ct), bytes.fromhex(slot["mac"])):
        return None
    return _xor(ct, _stream(kek[:32], nonce, len(ct)))


class Vault:
    def __init__(self, path):
        self.path = path
        self.master = None
        self.header = None
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- state
    def exists(self):
        return os.path.exists(self.path) or os.path.exists(self.path + ".bak")

    def is_legacy(self):
        """A plaintext SQLite file from an older build."""
        try:
            with open(self.path, "rb") as f:
                return f.read(16) == SQLITE_HEADER
        except OSError:
            return False

    @property
    def unlocked(self):
        return self.master is not None

    def lock(self):
        self.master = None

    # ---------------------------------------------------------------- file io
    def _candidates(self):
        out = []
        for p in (self.path, self.path + ".bak"):
            try:
                with open(p, "rb") as f:
                    raw = f.read()
            except OSError:
                continue
            if not raw.startswith(MAGIC) or len(raw) < len(MAGIC) + 4 + 16 + 32:
                continue
            try:
                (hl,) = struct.unpack(">I", raw[8:12])
                header = json.loads(raw[12:12 + hl])
                rest = raw[12 + hl:]
                out.append((header, raw[:12 + hl], rest[:16], rest[16:-32], rest[-32:]))
                break      # the .bak is only a fallback when the main file is unreadable, never a second chance for old secrets
            except (struct.error, ValueError):
                continue
        return out

    def create(self, passphrase, image=b""):
        """New vault. Returns the one-time RECOVERY CODE."""
        master, recovery = os.urandom(64), new_recovery_code()
        self.header = {"v": 1, "slots": [_make_slot(master, passphrase, "pass"), _make_slot(master, norm_recovery(recovery), "recovery")]}
        self.master = master
        self.save(image)
        return recovery

    def unlock(self, secret):
        """Returns the decrypted database image. Accepts the passphrase or the recovery code."""
        cands = self._candidates()
        if not cands:
            raise Corrupt("The database file is missing or not a NYXOR vault.")
        found = False
        for header, prefix, nonce, ct, mac in cands:
            master = None
            for slot in header.get("slots", []):
                master = _open_slot(slot, norm_recovery(secret) if slot["t"] == "recovery" else secret)
                if master:
                    break
            if not master:
                continue
            found = True
            if hmac.compare_digest(_mac(master[32:], prefix, nonce, ct), mac):
                self.master, self.header = master, header
                return _xor(ct, _stream(master[:32], nonce, len(ct)))
        if found:
            raise Corrupt("The database failed its integrity check (corrupt or modified). The previous version is kept next to it as nyxor.db.bak: "
                          "close NYXOR, rename nyxor.db.bak to nyxor.db and unlock again to restore it.")
        raise BadSecret("Wrong passphrase or recovery code.")

    def save(self, image, purge=False):
        """purge=True saves twice so the .bak no longer holds the previous image (after deletions or secret changes)."""
        if not self.master:
            raise VaultError("vault is locked")
        for _ in range(2 if purge else 1):
            self._save_once(image)

    def _save_once(self, image):
        with self._lock:
            nonce = os.urandom(16)
            ct = _xor(image, _stream(self.master[:32], nonce, len(image)))
            hdr = json.dumps(self.header, separators=(",", ":")).encode()
            prefix = MAGIC + struct.pack(">I", len(hdr)) + hdr
            data = prefix + nonce + ct + _mac(self.master[32:], prefix, nonce, ct)
            tmp = self.path + ".tmp"
            with open(tmp, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            if os.path.exists(self.path):
                os.replace(self.path, self.path + ".bak")
            os.replace(tmp, self.path)

    # ---------------------------------------------------------------- secrets
    def check(self, secret):
        return any(_open_slot(s, norm_recovery(secret) if s["t"] == "recovery" else secret) for s in (self.header or {}).get("slots", []))

    def replace_slot(self, typ, secret):
        """Replace the passphrase or recovery slot (caller must re-save the vault)."""
        if not self.master:
            raise VaultError("vault is locked")
        slots = [s for s in self.header["slots"] if s["t"] != typ]
        slots.append(_make_slot(self.master, norm_recovery(secret) if typ == "recovery" else secret, typ))
        self.header = {"v": 1, "slots": slots}
