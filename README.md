# NYXOR v2.3 — local private AI workspace

Dark Ultron-style interface. Plain HTML/CSS/JS plus a Python backend that uses **only the standard library** (Python **3.11+**). Runs on `127.0.0.1` only.

## Start
| System | Command |
| --- | --- |
| Windows | double-click `start-nyxor.bat` |
| macOS / Linux | `./start-nyxor.sh` |
| Any | `python server.py` → opens http://localhost:8777 |

First run: choose a **passphrase** and save the **recovery code** (shown once). Later runs: unlock in the browser. Self-test: `python server.py --selftest`.

## The encrypted database
`nyxor.db` is an opaque encrypted blob (no tables or text visible). The real database lives only in memory while unlocked. Chats, API keys, memories and tool logs are inside it. Forget the passphrase and lose the recovery code and the data is gone. Backup: SYSTEMS → SECURITY → *Download encrypted backup*.
- Deleting a chat / file / memory erases it from the database; files you delete from disk are removed from the database automatically (including text extracted from uploads).
- **Honest limits:** built from standard primitives (scrypt, BLAKE2b, HMAC-SHA256) because Python's standard library has no AES; not independently audited. It protects the file at rest, not a PC with malware while unlocked. Files in `nyxor-workspace/` (generated/uploaded files, videos) are ordinary files so you can open them. JSON exports are not encrypted. An old plaintext `nyxor.db` is encrypted automatically on first run.
- Headless: `NYXOR_PASSPHRASE=... python server.py` unlocks at start (the passphrase is then in your environment — use with care).

## Paste any API key
SYSTEMS → PROVIDERS → the box at the top. NYXOR recognises the provider from the key (`sk-ant-`, `gsk_`, `nvapi-`, `AIza`, `tvly-`, `r8_`, …), connects, tests, loads models. If a shape fits several providers (e.g. plain `sk-…`) the key is **not sent anywhere** until you choose. Unknown format: give the OpenAI-compatible base URL. All calls go through the backend; the browser only ever sees a masked key.

Get keys: OpenAI platform.openai.com/api-keys · Anthropic console.anthropic.com · xAI console.x.ai · Groq console.groq.com/keys (free tier) · Gemini aistudio.google.com/apikey (free tier) · OpenRouter openrouter.ai/keys (`:free` filter) · NVIDIA build.nvidia.com · Mistral console.mistral.ai · DeepSeek platform.deepseek.com · Together, Fireworks, Cerebras, SambaNova, Cohere, Perplexity, Hugging Face, GitHub Models (token with Models access) · Tavily app.tavily.com (free tier) · Brave brave.com/search/api · Serper serper.dev · Exa dashboard.exa.ai · Replicate replicate.com/account/api-tokens · fal.ai fal.ai/dashboard/keys · ElevenLabs elevenlabs.io.

## Local Qwen (Ollama)
`ollama pull qwen3:8b` → NYXOR detects it. For web research or long chats raise Ollama's context (`num_ctx`, default 4096 is small).

## Web research (in the tab, never leaving)
Ask "what is happening in the Strait of Hormuz" (typed or spoken). NYXOR plans the search, reads the pages itself, and your model writes a dated, cited briefing; sources open in the built-in reader. Modes AUTO/ON/OFF on the globe button; SYSTEMS → WEB sets depth:
- **Snippets** — search results only. **Read** — open the top 5 pages. **Deep** — several search angles (written by your model when it can), up to 10 pages, merged, de-duplicated, at most 3 per site, fresh news first.
- Pages are read by the local backend: menus/ads stripped, publish dates found, JSON-LD article bodies used when the visible page is empty, a 10-minute cache, two connections per site. JavaScript-only pages can use a reader fallback (sends only that page's URL to r.jina.ai; switch it off in WEB).
- **Paste a link** in your message and NYXOR reads it (not when web research is OFF).
- With no search key: Bing News RSS for news questions and DuckDuckGo otherwise (best effort, can be rate-limited; they fall back to each other). A Tavily / Brave / Serper key is far more reliable.
- Pages are untrusted text: private-network addresses are blocked, models are told not to follow instructions in them. Ollama's default 4096-token context is small for long research: raise `num_ctx`.

## Voice
- **LISTEN** button / `/listen`: say "Nyxor, …" (Chrome or Edge). Commands: stop · new chat · direct/council/agent mode · web search on/off/auto · open systems/video/files · generate a video of … · read that again · lock the vault. Anything else is sent as a message. It ignores its own voice.
- **Deep voice**: SYSTEMS → VOICE → *PRESET: DEEP*, or a cloud voice (OpenAI-compatible TTS / ElevenLabs) with the Ultron effect chain. NYXOR cannot clone a film actor's voice; use a voice you have rights to.

## Interface (new in 2.3)
A security bar across the top shows the real vault, privacy, agent and terminal state. The conversation sits in the middle, system readings and chats on the left, activity and files on the right. **Ctrl+K** opens the command palette; **Ctrl+`** opens the terminal panel (off until you enable it in SYSTEMS → AGENT; it runs commands as you inside the allowed folders). The agent's file writes show a diff before you approve. Fonts (Inter, JetBrains Mono) are bundled, so the interface makes no outside requests.

## Privacy, real telemetry and the single file (new in 2.3)
The dashboard shows only measured data (CPU, memory, disk, model speed, real events); a value that cannot be read shows n/a. On first run NYXOR asks which kinds of data may leave this PC (cloud models, web research, the page reader, cloud voice, microphone, cloud video); nothing is pre-ticked and local models never need permission. Change it in SYSTEMS → PRIVACY, where saved reports about AI answers are listed. The policy is `privacy.html`. `python build_single_file.py` builds **one file**, `dist/nyxor.pyz` (run with `python nyxor.pyz`); its data lives in `%LOCALAPPDATA%\NYXOR`. Publishing: `docs/MICROSOFT-STORE-PLAN.md`.

## Router and fallback (new in 2.3)
SYSTEMS → ROUTER. **Manual** (default): the model you selected answers, exactly as before. **Auto**: for every DIRECT message NYXOR ranks the connected models and shows its choice and reasons in the chat. Inputs: task size (light / standard / heavy, from length, code, a few keywords, attachments), privacy, availability, speed, cost, and a learned record of earlier answers. A chat that holds AGENT output is **local-only**: cloud models are excluded in both modes. Capability and cost tiers are **guesses from model names** ("mini" / 8B = small, "sonnet" / 70B = large, local and `:free` = free); the record corrects them over time. The preview box shows the ranking for any message without calling a model.
**Fallback** (on by default): if a model fails *before it starts answering* (bad key, rate limit, server down, model missing, disabled) the next-best model is tried, at most twice, with a visible notice. Once the answer has started it is never retried. A failing local model falls back to local models only, unless you tick *allow local → cloud fallback* (your message then leaves this PC). COUNCIL and AGENT are not routed yet. Prices, spend caps and a usage meter: see Budgets below.

## Budgets (new in 2.3)
SYSTEMS → BUDGETS: a usage meter and spend caps per provider. **Tokens** are the provider's own numbers when it reports them, otherwise an estimate (about 4 characters per token; such calls are marked "estimated"). **Dollars** are tokens × the price **you enter** per million input and output tokens (per provider, optionally per model): there is no built-in price list, because prices change and cannot be checked offline. Local models are free. A cloud call without a price adds tokens but no dollars, and the tab says so; a dollar cap cannot be saved without a price (use a token cap instead).
**Caps:** dollars or tokens, per day or per month (0 = none; the clock is this PC's). A request is refused **before it is sent** when the spend so far, calls already running and the size of this request's input would reach a cap. The answer cannot be predicted, so one call can overshoot a cap; treat caps as a safety net, and the provider's own bill as the truth. **When a cap is reached:** *Block* (the provider refuses until the next day / month, or until you press OVERRIDE TODAY) or *Ask* (the chat shows the refusal with an ALLOW TODAY button). With fallback on, a capped provider hands over to the next-best model; in AUTO routing it is not a candidate. Voice, video and search APIs are not counted. The "Spend today" meter in the system panel shows dollars (or tokens) and fills toward the tightest cap.

## Council (rebuilt in 2.2)
Orchestrator = any model, local or cloud. Members = any mix, with optional roles; **Auto-pool** picks the best-performing enabled models when none are ticked. Strategy (SYSTEMS → COUNCIL): *Auto* uses **candidates** for build requests (games, apps, pages) and **merge** for everything else; *split* (each member owns files) is still available.
Candidates pipeline: the orchestrator writes a short build brief → every member writes a **complete single-file** version in parallel → NYXOR **runs each one headlessly** (syntax checks, then `smoke.js` plays ~4 s of frames with key presses and clicks in a fake browser; needs **Node.js**) → the best *valid* one wins, slow members are cancelled once a good candidate exists → the other members review it → the orchestrator repairs with minimal fixes, and **a repair is accepted only if it scores at least as well and does not break a working file** → the final report says what was tested. Efficiency: one request at a time per local model server (one GPU), three per cloud API, per-member timeout, and members that fail 3 times in a row are skipped until you reset their record.
Press **▶ PREVIEW** on the resulting HTML file to play it inside NYXOR: it runs in a sandbox with no network and no access to your chats or keys (the only thing it loses is localStorage persistence). A playable reference game ships in `examples/infinite_runner.html`.
Cloud members receive the task, never local files or command output.

## Agent mode (local models only)
Enforced in the backend (HTTP 403 for cloud). Tools: list_dir, read_file, write_file, find_files, run_command, open_item, web_search, system_info. Allowed folders default to `nyxor-workspace`; every path is `realpath`-checked. Writes, commands, opens and searches ask first (ALLOW / DENY / ALLOW ALL FOR THIS TASK); read-only and auto (red warning) modes exist. 15 steps max, 120 s command timeout, ABORT kills the process tree. A shell can touch anything, so commands always ask in the default mode. Everything is logged in `tool_runs`.

## Video (free Colab GPU)
VIDEO → download `nyxor_colab_video.ipynb` → upload to colab.research.google.com → T4 GPU → run 3 cells → paste the printed connect string. Default model CogVideoX-2b (fits a free T4; expect minutes per short clip). Replicate and fal.ai work with just a key + model id. *Colab/Replicate/fal paths were checked against local stand-ins, not the live services.*

## Files
Models create files with fenced blocks `file:path/name.ext`; they are saved per chat with DOWNLOAD / VIEW / DELETE, DOWNLOAD ALL (.zip), SAVE AS FILE on plain code blocks. Attach images (vision models) and text/.docx/.pdf files with the paperclip, drag-and-drop or paste.

## Files in this folder
`smoke.js` (headless game/app tester) · `examples/` · `tests/ui_harness.js` · `server.py` (HTTP + SSE) · `vault.py` `db.py` · `providers.py` `catalog.py` · `modes.py` `router.py` `budget.py` `privacy.py` `telemetry.py` `terminal.py` `assets.py` `build_single_file.py` `council.py` `agent.py` · `research.py` `websearch.py` `attachments.py` · `videogen.py` `tts.py` `workspace.py` · `selftest.py` · `index.html` `style.css` `core.js` `app.js` · `nyxor_colab_video.ipynb`

## What you need to install
1. **Python 3.11+** (required). 2. **Node.js** on PATH (strongly recommended: it powers the headless game test; without it the council still works but cannot catch runtime crashes). 3. A model: **Ollama** + `ollama pull qwen3:8b`, and/or API keys pasted into SYSTEMS → PROVIDERS. 4. For voice control and dictation: **Chrome or Edge** and microphone permission. 5. Video: a Google account for Colab (free T4). 6. Optional: a Tavily/Brave/Serper key for reliable web research.
## Tests
Optional real-browser checks (layout overlaps at six screen sizes, scroll-while-streaming): `node tests/browser/layout.js` and `node tests/browser/scroll.js` (need Playwright + Chromium; not part of the two suites below).
`python server.py --selftest` (25 groups against mock servers, no network) and `node tests/ui_harness.js` (the real UI code against the real backend with a stubbed page).
