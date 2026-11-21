"""Entry point for the packaged Windows app (PyInstaller turns this into NYXOR.exe).

What the user sees: one normal app window (WebView2, the Edge engine Windows already ships), no browser, no console.
What happens inside: the stdlib backend runs on 127.0.0.1 on a free port in a background thread, only for this window.
Closing the window seals the vault and ends the process. A second launch just exits (single instance).
Fallback if pywebview/WebView2 is missing: the old Edge/Chrome --app window, with the same shutdown rule."""
import os
import sys
import threading
import time

FROZEN = getattr(sys, "frozen", False)
if FROZEN:
    base = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "NYXOR")
    os.environ.setdefault("NYXOR_HOME", base)          # install folder is read-only in an MSIX: keep data per user
    os.makedirs(base, exist_ok=True)
    if sys.stdout is None or sys.stderr is None:        # windowed exe: send output to a log instead of crashing
        log = open(os.path.join(base, "nyxor.log"), "a", buffering=1, encoding="utf-8", errors="replace")
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log
    sys.path.insert(0, getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable))))

import server  # noqa: E402
import db      # noqa: E402


def single_instance():
    """True if this is the only NYXOR running (Windows named mutex). Elsewhere always True."""
    if os.name != "nt":
        return True
    try:
        import ctypes
        h = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\NYXOR.SingleInstance")
        single_instance.h = h                           # keep the handle alive for the life of the process
        return ctypes.windll.kernel32.GetLastError() != 183      # ERROR_ALREADY_EXISTS
    except Exception:
        return True


def shutdown(code=0):
    try:
        if db.is_unlocked():
            db.flush(force=True)                        # seal the vault before the process goes
    finally:
        os._exit(code)


def main():
    if not single_instance():
        return
    srv = server.make_server(0)                         # port 0: the OS picks a free one, so no clash with other software
    port = srv.server_address[1]
    url = "http://localhost:%d" % port
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        import webview
    except ImportError:
        webview = None
    if webview:
        try:
            webview.settings["ALLOW_DOWNLOADS"] = True   # SYSTEMS > export saves a file
            webview.create_window("NYXOR", url, width=1360, height=860, min_size=(960, 600), background_color="#111114")
            webview.start(storage_path=os.path.join(server.assets.DATA_DIR, "webview"))   # blocks until the window closes
            shutdown(0)
        except Exception as e:                           # WebView2 runtime missing or failed: fall back, say why in the log
            print("webview failed (%s); falling back to an Edge/Chrome app window" % e)
    server.open_window(url)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        shutdown(0)


if __name__ == "__main__":
    main()
