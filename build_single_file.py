#!/usr/bin/env python3
"""
Build NYXOR as ONE file: dist/nyxor.pyz (a standard Python zipapp; run it with  python nyxor.pyz ). Standard library only.

  python build_single_file.py                  Store build (adds the STORE_BUILD marker: stricter privacy and safety limits)
  python build_single_file.py --dev            same program without the marker
  python build_single_file.py --support-email you@example.com --privacy-url https://example.com/privacy --publisher "Your Name"

The single file holds the program, the interface and the privacy policy page. Your data (nyxor.db and the workspace) is NOT inside it:
it lives in a per-user data folder (Windows: %LOCALAPPDATA%\\NYXOR, override with NYXOR_HOME), so updating or replacing the file never
touches your chats. Needs Python 3.11+ on the PC that runs it; Node.js is optional (council game test).
"""
import argparse
import json
import os
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
MODULES = ["server", "assets", "privacy", "budget", "telemetry", "vault", "db", "catalog", "providers", "modes", "router", "council", "agent",
           "research", "websearch", "attachments", "workspace", "videogen", "tts", "terminal"]
DATA = ["index.html", "style.css", "core.js", "app.js", "privacy.html", "smoke.js", "nyxor_colab_video.ipynb",
        "fonts/Inter-Regular.otf", "fonts/Inter-SemiBold.otf", "fonts/JetBrainsMono-Regular.ttf", "fonts/JetBrainsMono-Bold.ttf", "fonts/LICENSES.txt"]
MAIN = "import server\nserver.main()\n"


def build(out, store=True, support_email="", privacy_url="", publisher=""):
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    cfg = {"publisher": publisher, "support_email": support_email, "privacy_url": privacy_url}
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("__main__.py", MAIN)
        for m in MODULES:
            z.write(os.path.join(HERE, m + ".py"), m + ".py")
        for d in DATA:
            z.write(os.path.join(HERE, d), d)
        z.writestr("store_config.json", json.dumps(cfg, indent=2) + "\n")
        if store:
            z.writestr("STORE_BUILD", "Store build: stricter privacy and safety limits (see privacy.py)\n")
    with open(out, "rb") as f:
        body = f.read()
    with open(out, "wb") as f:                       # shebang so ./nyxor.pyz works on macOS / Linux
        f.write(b"#!/usr/bin/env python3\n" + body)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=os.path.join(HERE, "dist", "nyxor.pyz"))
    ap.add_argument("--dev", action="store_true", help="no STORE_BUILD marker")
    ap.add_argument("--support-email", default="")
    ap.add_argument("--privacy-url", default="")
    ap.add_argument("--publisher", default="")
    a = ap.parse_args()
    p = build(a.out, not a.dev, a.support_email, a.privacy_url, a.publisher)
    print("built %s (%d KB, %s build)" % (p, os.path.getsize(p) // 1024, "dev" if a.dev else "Store"))
