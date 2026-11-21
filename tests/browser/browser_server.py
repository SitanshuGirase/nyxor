import os, sys, json, tempfile, threading, time
t = tempfile.mkdtemp(prefix="nyxs-")
os.environ.update(NYXOR_DB=t+"/n.db", NYXOR_WORKSPACE=t+"/ws", NYXOR_KDF_FAST="1")
sys.path.insert(0, os.environ.get("NYXOR_SRC") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import server, selftest, research, providers as P, videogen
from http.server import ThreadingHTTPServer
research.ALLOW_PRIVATE = True
m = ThreadingHTTPServer(("127.0.0.1", 0), selftest.Mock); threading.Thread(target=m.serve_forever, daemon=True).start()
s = server.make_server(0); threading.Thread(target=s.serve_forever, daemon=True).start()
print(json.dumps({"app": s.server_address[1], "mock": m.server_address[1]}), flush=True)
time.sleep(900)
