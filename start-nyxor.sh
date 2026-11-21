#!/usr/bin/env sh
# NYXOR v2.1 launcher (needs Python 3.11+). Optional: NYXOR_PASSPHRASE="..." ./start-nyxor.sh  (headless unlock; see README)
cd "$(dirname "$0")"
PY=python3; command -v python3 >/dev/null 2>&1 || PY=python
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' || { echo "NYXOR needs Python 3.11 or newer."; exit 1; }
exec "$PY" server.py "$@"
