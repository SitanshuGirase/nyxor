# NYXOR: handoff prompt for a new Claude conversation or account

Paste the prompt below into a fresh conversation, then upload `nyxor-v2.3-store-ready.zip` (and `nyxor.pyz` if you want the built single file) in the same message.

---

You are taking over a project I built with other Claude sessions: **NYXOR v2.3**, a local, private AI workspace for Windows, macOS and Linux, branded as *my own terminal, codebase and private agent, with security at the top*. The name is **NYXOR** everywhere (I decided; never rename). I attached the whole project. **First unzip it, read `README.md`, `CHANGELOG.md`, `docs/MICROSOFT-STORE-PLAN.md` and every file you will touch, and run both test suites before changing anything. Never rely on memory of earlier work: the files are the truth.**

## What it is
Plain HTML/CSS/JS front end plus a Python **standard-library-only** backend (`python server.py` opens http://localhost:8777, bound to 127.0.0.1). It can also be built as ONE file: `python build_single_file.py` writes `dist/nyxor.pyz`.
Modules: `server.py` (HTTP, SSE, routes), `vault.py` + `db.py` (encrypted vault, settings, chats), `catalog.py` + `providers.py` (any API key, OpenAI-compatible adapter + native Anthropic), `modes.py` (DIRECT), `router.py` (model choice + fallback + learned records), `council.py`, `agent.py` (local-only tools, approvals with diffs), `terminal.py` (opt-in terminal panel), `research.py` + `websearch.py`, `attachments.py`, `workspace.py`, `videogen.py` (Colab / Replicate / fal), `tts.py`, `privacy.py` (consent flows, Store-build limits), `telemetry.py` (real measurements + real event feed), `assets.py` (works from a folder or from the .pyz; per-user data folder), `smoke.js` (headless game tester), `index.html` `style.css` `core.js` `app.js`, `privacy.html`, `fonts/` (Inter, JetBrains Mono, OFL), `selftest.py`, `tests/`.

## State of the work (done and tested)
- Router + automatic fallback (DIRECT mode): Manual (default) or Auto; privacy rule (chats holding agent output stay local); fallback only before the first token; local-to-cloud fallback needs permission.
- Interface redesigned in a clean IDE style: graphite + one blue accent, no red, no sphere, Inter / JetBrains Mono bundled. Security bar on top (real vault, privacy, agent, terminal, workspace state), conversation in the middle, terminal drawer, activity/files column, status bar, Ctrl+K command palette, Ctrl+` terminal.
- Real data only: telemetry.py measures CPU, memory, disk, model speed, uptime; the event stream lists only real events. All simulated telemetry, fake events and pseudo-hacking text were removed on purpose (Microsoft Store policy 10.1.1). **Never reintroduce simulation.**
- Privacy: first-run consent screen (nothing pre-ticked); backend refuses cloud models, web research, page reader, cloud voice, cloud video until allowed; microphone gated in the UI; REPORT button on every answer (Store policy 11.16); privacy policy page `privacy.html`.
- Store build (marker file `STORE_BUILD` or `NYXOR_STORE=1`): no keyless search scraping, agent never AUTO and no ALLOW ALL, safety block in the system prompt.
- Terminal panel: off by default; commands run as the user inside the allowed folders only; no NYXOR_* env vars in the child; timeout, output cap, Stop button; one-shot commands only (no stdin).
- Agent file-write approvals show a unified diff.
- Single file build; per-user data folder; council game test runs under Node's permission model.

## Hard rules (the user's, keep them)
- Backend: Python standard library only (3.11+). No pip. No frontend build step. **No localStorage** (settings live in the encrypted DB). Bind 127.0.0.1 only. Fonts are bundled; the interface makes no third-party requests.
- Deliver **complete files**, never snippets or diffs. Say plainly what you could not verify.
- Cloud models never see local files or command output; agent mode is local models only (backend returns 403 for cloud).
- Keys never reach the browser (masked); a key is only sent to the provider it belongs to (ambiguous keys are not probed). API keys are OPTIONAL: local models need none.
- Research stays inside the app (sources open in the built-in reader).
- Quality and "do not break what works" matter more than features. The UI must stay **clean**.

## How to verify
- `python server.py --selftest` must print `24/24 passed` (about 1.5 minutes, mock servers, no network).
- `node tests/ui_harness.js` runs the real `core.js` + `app.js` against the real backend with a stubbed DOM: expect no FAIL lines (about 45 ok).
- Node.js on PATH is needed for the council's runtime game test.
- Optional real-browser checks (need Playwright + Chromium): `node tests/browser/layout.js`, `scroll.js`, `store_ui.js`, `ide.js`. Use them for every UI change: take screenshots and look at them.

## Known gaps / unverified (be honest about these)
- Nothing has been run on Windows (window launcher through Edge `--app`, CPU/memory readings via ctypes, MSIX packaging, the certification kit). Nothing has been run with a real microphone, real GPU, real cloud APIs, Replicate/fal, search scraping or the Colab notebook.
- The vault is home-built from scrypt + BLAKE2b + HMAC-SHA256: standard primitives, unaudited. Replacing it (e.g. Windows CNG via ctypes) was deliberately NOT done because it cannot be tested without Windows and a bug would destroy user data.
- Node's permission model does not restrict the network; real isolation of model-written code would need an OS sandbox.
- Council quality with real models is untested; expect to tune prompts in `council.py`.

## Pitfalls learned (do not repeat)
- Patching `server.py`: anchor on `^ROUTES = {` (regex, MULTILINE); `str.replace("ROUTES = {")` also matches `OPEN_ROUTES = {`. Handler `q` is a flat dict of strings (not lists).
- `/api/telemetry` and `/api/terminal/output` are in `IDLE_EXEMPT`; polling must never reset the auto-lock timer.
- Tests: grant consent after the vault is created (`/api/consent`); a 5xx marks a whole provider "down" for 60 s (`P._status`), which the router respects; reset `research.ALLOW_PRIVATE` / `research._CACHE` after SSRF tests; mock servers wrap SSE writes in try/except OSError. Store mode is tested by monkeypatching `privacy.store`.
- UI harness: handlers set with `.onclick =` must be called directly; `fire()` only runs `addEventListener` handlers. Top-level `const`s in `app.js` (e.g. `S`) are not on `window`, so `page.evaluate` cannot see them. The settings tabs own `data-tab`; other chips must use another attribute (`data-open`).
- CSS sentence-case trick: labels are stored in capitals and shown with `text-transform: lowercase` + `::first-letter` uppercase. It breaks acronyms and does not work on flex containers.
- `assets.DATA_DIR` must be created at startup (a fresh PC has no folder): the single-file test caught this once.
- HTMLParser-based text extraction counts `SKIP` tags; unmatched tags can desync it. Keep fallbacks (JSON-LD, reader).

## Next jobs (work in stages; run both suites after each; report what I must install or do)
1. **Budgets** (upgrade stage 3): per-provider spend caps (tokens x configurable price), a usage meter in the UI (real data), block/ask when exceeded. Providers already stream usage in some paths; the Anthropic path does not.
2. **Council debate + judge** for non-build questions: independent answers, cross-challenge round, separate judge model, final answer listing contradictions and uncertainty; opt-in, show estimated cost and latency first.
3. **Memory**: relevance retrieval (keyword first, no embeddings), per-memory permissions (never send to cloud / local only); export and delete already exist.
4. **Hardware awareness**: detect GPU/CPU/RAM and recommend Ollama / llama.cpp models (NYXOR does not drive GPUs itself).
5. **Persona**: cold, concise, anticipatory voice, never claims about what the software did.
6. **Antigravity / Codex-style features not built yet**: plan-first agent with an approvable plan artifact, task list, multi-agent manager view, review comments, per-folder command allow/deny lists, editor with code intelligence.
7. **Microsoft Store**: see `docs/MICROSOFT-STORE-PLAN.md`, `docs/store/`. Open items are mine to decide or Windows-only: account type, trademark search (a live US NYXOR registration exists in Class 11), support email, hosting `privacy.html`, MSIX packaging and test on a clean Windows VM, certification kit, screenshots, age rating.
