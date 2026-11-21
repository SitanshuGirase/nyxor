# Changelog

## 2.3.0 (in progress: router, fallback and budgets done, plus interface changes)
**Antigravity palette** (colors sampled from a screenshot of the Antigravity app you supplied, not from an official style guide):
- Cool near-black shell `#111114` (title bar, sidebar, status bars), a darker rounded inset content panel `#070708` with a faint `#171717` edge, composer `#15151a`, raised / active rows `#262629`, borders `#222226` / `#2f2f33`, text `#ececf0` with `#bcbdc6`, `#a1a1a3` and `#88888a` for secondary and muted text. Like the reference there is no coloured accent: the primary button, meters and focus rings are white. Green, amber and red remain only for status.
- All colors are CSS variables at the top of `style.css`; the leftover blue and the crimson in `privacy.html`, the favicon and two inline styles are gone. No layout change beyond the inset panel (checked at six sizes in headless Chromium: no overlaps, no horizontal scroll).
- Not copied: the Antigravity logo, fonts and icons (Inter and JetBrains Mono stay).

**Logo and typography**
- New logo mark (an open ring around an N; drawn for NYXOR, not taken from another product) replaces the plain "N" in the header; the same mark is the favicon (and so the Edge app-window icon). A standalone copy for Store tile assets is `docs/store/logo.svg` (the Store still needs PNG sizes made from it).
- Typography (Inter 4.0, bundled in Regular and SemiBold only): larger, calmer sizes in the security bar and left column; chips, labels and meter rows use the lighter text colour; tabular numerals for every reading; the header's small labels are tracked capitals; Inter's open digits and tailed lowercase l are switched on so `l`, `I` and `1` cannot be confused in model names. The workspace chip shortens with an ellipsis on narrow windows instead of being cut off, and the right-hand note hides below 1280 px.

**Budgets** (`budget.py`; per-provider spend caps and a real usage meter):
- Every model call (DIRECT, COUNCIL, AGENT, research query writing, the TEST button) passes through `providers.stream_chat`, which now calls the budget gate before the request and books the usage when it ends, even if it was cancelled or broke half way. Nothing can bypass a cap.
- **Tokens** are the provider's own numbers when it reports them (OpenAI-compatible servers: NYXOR now asks with `stream_options.include_usage`, and repeats the request without it if a server rejects the option; Anthropic: `message_start` / `message_delta`). Otherwise they are an **estimate** (about 4 characters per token, an image about 800 tokens) and the call is counted as estimated. A request that failed before the first token is not booked.
- **Dollars** = tokens x the price you enter (per million input / output tokens, per provider, optionally per model). NYXOR ships no price list, because prices change and cannot be checked offline. A cloud call without a price adds tokens but no dollars, and the meter says "N call(s) without a price". Local loopback servers are free. A dollar cap cannot be saved for a provider without a price.
- **Caps** per provider: dollars per day / month, tokens per day / month (0 = none; days and months follow this PC's clock). A request is refused before it is sent when the spend so far, plus calls already running (reserved for their input), plus the estimated input of this request would reach a cap. The answer cannot be predicted, so one call can overshoot a cap by its output.
- **Action:** *Block* (the provider refuses requests until the next day / month or until OVERRIDE TODAY in SYSTEMS → BUDGETS) or *Ask* (same, and the chat shows the refusal with an ALLOW TODAY button). An override ends at local midnight.
- Router: a capped provider is left out of AUTO routing; in DIRECT mode with fallback on, a capped provider counts as failed before the first token and the next-best model answers (visible `FALLBACK:` line). A budget refusal is not recorded as a model failure.
- Interface: SYSTEMS → BUDGETS (totals, per provider today / month, prices, caps, override, clear history) and a "Spend today" meter in the system panel (dollars or tokens, filling toward the tightest cap). New routes: `GET/POST /api/budget`, `POST /api/budget/override`, `POST /api/budget/reset`. The browser cannot write the ledger or the configuration through `/api/settings`.
- Not counted: voice (TTS), video, search APIs. The provider's own bill is the truth. Only checked against mock servers, not against live billing.
- Tests: self-test group 25; extra UI-harness checks (cap refusal, ALLOW TODAY button, BUDGETS tab, spend meter).
**Router + fallback** (`router.py`, DIRECT mode only; COUNCIL and AGENT choose their own models):
- SYSTEMS → ROUTER. *Manual* (default, unchanged behaviour) or *Auto*: for each message NYXOR ranks the connected models from the task (light / standard / heavy, images), privacy, availability, speed, cost and a learned record, picks the best, and shows the decision in the chat (`ROUTE: model — reasons`). A preview box shows the ranking for any message without calling a model.
- Privacy: a chat that holds AGENT output (local-only) can only be answered by local models, in either mode.
- Fallback (on by default): if a model fails before it starts answering (bad key, rate limit, server down, model missing, disabled), the next-best model is tried, at most twice, with a visible `FALLBACK:` line. A failure after tokens have appeared is never retried. A failing local model falls back to local models only unless you tick *allow local → cloud fallback*. A provider that is down, rate-limiting or rejecting the key is not retried through another of its own models.
- Records per model (answers ok / runs, tokens per second, failure streak); 3 failures in a row = skipped for 5 minutes. Reset in the ROUTER tab. The browser cannot write the records.
- Capability and cost tiers are guesses from model names; the learned record corrects them. Prices, spend caps and the usage meter: see Budgets above.
- Tests: self-test group 20 (now 20 groups) and extra UI-harness checks.
**New look (clean, IDE style)**
- Redesigned from scratch in neutral graphite with one calm accent; no red, no glow, no scanlines, no animated sphere (removed). Clean monogram logo. **Inter** and **JetBrains Mono** are bundled in `fonts/` (SIL Open Font License, texts in `fonts/LICENSES.txt`), so nothing is loaded from the internet.
- **Security bar at the top, from real state:** vault status with the auto-lock countdown, "Local only" or how many data flows are allowed, agent approval mode, terminal on/off, workspace folder, Store build. Each chip opens the matching settings tab.
- **Terminal panel** (`terminal.py`, status bar or Ctrl+`): off by default; commands run as you inside the allowed folders only (`cd` cannot leave them), with no NYXOR_* environment variables in the child, a timeout, an output cap and a Stop button; logged as events. One-shot commands only: no interactive programs (no stdin).
- **Diff review:** the agent's approval card for a file write shows a coloured unified diff against the current file (or the start of a new file).
- **Command palette** (Ctrl+K): modes, settings tabs, terminal, focus mode, lock, video studio.
- Layout: title bar, sidebar (system, chats, models), conversation, terminal drawer, activity/files column, status bar.
- Tests: self-test groups 24 (terminal, diff) and extended 23 (fonts and terminal are inside the single file); `tests/browser/ide.js`.
- Not done (compared with Antigravity): no multi-agent manager, no plan/task-list artifacts, no browser-driving agent, no editor with code intelligence.

**Store readiness, real data and one file**
- **Real data instead of simulation** (`telemetry.py`): the system panel shows measured CPU, memory, disk of the data drive, last model speed and the vault auto-lock countdown; the top bar shows real uptime, models online/connected and alerts in the last hour; the event stream lists only things that really happened (unlock, answers with tokens/s, failures, fallbacks, reports, privacy changes). A reading that cannot be measured shows n/a. All random events, fake telemetry, the fake boot lines, the pseudo-hacking commands and the scripted claims were removed; the default system prompt and the offline scripted voice were rewritten to be honest (they never claim abilities the software does not have).
- **Privacy consent** (`privacy.py`): a first-run screen with nothing pre-ticked; the backend refuses cloud models, web research, the page reader, cloud voice and cloud video until that kind of flow is allowed; microphone (browser speech recognition) is blocked in the interface until allowed. New SYSTEMS → PRIVACY tab. `privacy.html` is the policy (served at `/privacy`, contact details filled from `store_config.json`).
- **Reports** (live generative AI content): REPORT button on every answer, saved locally, listed and deletable in PRIVACY, optional email draft to the support address.
- **Store build** (marker file `STORE_BUILD`, or `NYXOR_STORE=1`): no keyless search scraping, the agent never runs in AUTO and has no ALLOW ALL, a safety block is added to the system prompt.
- The interface makes no third-party requests any more (fonts are system fonts); CSP tightened.
- The council's game test now runs under Node's permission model when available (read-only, no child processes). It does not restrict the network.
- **One file:** `python build_single_file.py` builds `dist/nyxor.pyz`. Data lives in a per-user folder (`NYXOR_HOME`, default `%LOCALAPPDATA%\NYXOR`). New `assets.py`. Optional app-style window through Edge/Chrome `--app` (written for Windows, untested there).
- Tests: self-test groups 21 (telemetry), 22 (privacy, Store limits, reports, no simulation), 23 (the single file runs); UI harness and `tests/browser/store_ui.js` extended.
- Documents: `docs/MICROSOFT-STORE-PLAN.md` (policy check and status), `docs/store/` (listing, certification notes, checklist).

**Interface**
- The chat now starts at the top of the centre column; the NYXOR sphere moved to the top of the right column; the Network Lattice and Signal panels are gone (their canvases and drawing code too). Panel numbers are 01–05.
- Mode bar fixed: DIRECT / COUNCIL / AGENT, the model picker, MEMBERS and ⤢ no longer overlap the quick-command chips (a negative margin pulled the chips 4 px over the mode bar). Checked in headless Chromium at 1919x1018, 1600x900, 1366x768, 1280x720, 1100x800 and 390x800: no overlaps, no horizontal page scroll.
- The top bar showed a horizontal scrollbar at 1100 px (it already did in 2.2): buttons are icon-only below 1400 px.
- Chat scrolling while an answer streams: the transcript no longer drags you back down. It follows new text only while you are at the bottom; scrolling up (wheel, touch, scrollbar, PageUp/Home) releases it, scrolling back down or pressing **↓ LATEST** re-pins it. The cause was `scroll-behavior: smooth` plus a forced scroll on every token. Reproduced and verified in headless Chromium (`tests/browser/`, optional, needs Playwright).
- Focus mode (⤢) now hides both side columns and centres the chat.
- New settings: `router_mode`, `router_fallback`, `router_fallback_cloud`, `router_pool`, `router_stats`. New routes: `/api/router/preview`, `/api/router/stats`, `/api/router/stats/reset`.

## 2.2.0
**Council** (it could not build an infinite runner; now it can be verified to):
- New *candidates* strategy: brief → N complete single-file candidates in parallel → each **actually run** headlessly (`smoke.js`: syntax, runtime errors, wrong element ids, frozen loops, blank canvas) → best valid candidate wins → members review → guarded repair (a repair that scores lower or breaks a valid file is rejected).
- Pool efficiency: per-provider concurrency slots (1 local, 3 cloud), cancel stragglers once a good candidate exists, per-member timeout, failure-streak circuit breaker, per-member record, auto-pool.
- Aborting now works even while a model is still loading (connection setup is interruptible).
- In-app **▶ PREVIEW**: sandboxed (no network, no API access) player for generated HTML. Reference game in `examples/`.
**Web research**:
- Deep mode (multi-query, up to 10 pages, merge/de-duplicate/per-site cap, fresh-first), model-written search angles, page dates, JSON-LD article bodies, reader fallback for JavaScript pages, page cache, per-site politeness, 45 s budget.
- Pasted links are read. Keyless Bing News RSS + DuckDuckGo (with lite fallback) chain.
- Dated briefing template for news questions.

## 2.1.0
**Security**
- `nyxor.db` is now an encrypted vault (scrypt key, BLAKE2b stream cipher, HMAC-SHA256 integrity, key slots for passphrase + recovery code). On disk it is not a SQLite file; the readable database exists only in memory while unlocked in the browser.
- The API needs the browser's session cookie, so other programs on the PC cannot read your chats. Auto-lock, LOCK button, `/lock`, voice command "lock the vault".
- Deleting is real: secure-delete zeroes freed pages, the previous encrypted copy is overwritten, deleting a chat removes its folder, rows and tool log.
- Files deleted from disk are dropped from the database automatically (rows, chips, and text extracted from uploads). Delete buttons in the FILES tab.
- Old plaintext `nyxor.db` files are imported into the vault and the plain copy is wiped.
- Content-Security-Policy, Host/Origin checks, unlock rate-limiting.

**Web research**
- Questions that need fresh facts ("what is happening in the Strait of Hormuz") trigger search + page reading automatically (AUTO / ON / OFF). Pages are fetched by the backend with SSRF protection, reduced to readable text and given to any model; the answer, a progress card and a source list appear in the chat, and every source opens in NYXOR's built-in reader. Nothing sends you to another site.
- News endpoints for Tavily, Brave and Serper; keyless DuckDuckGo fallback (best effort).

**Voice**
- Voice control: wake word ("Nyxor, ..."), spoken commands (stop, new chat, modes, web on/off, video, read again, lock), ignores its own voice.
- Deep voice: DEEP / ROBOT presets, cloud TTS (OpenAI-compatible or ElevenLabs) through an Ultron-style effect chain, spoken-length cap for long answers.

**Keys and providers**
- Paste any API key: provider recognised from its shape, connected, tested, models loaded. Ambiguous keys are never sent anywhere until you choose.
- Providers: OpenAI, Anthropic (native), Grok, Gemini, Groq, OpenRouter, NVIDIA, Mistral, DeepSeek, Together, Fireworks, Cerebras, SambaNova, Cohere, Perplexity, Hugging Face, GitHub Models, local servers, custom URLs; search: Tavily, Brave, Serper, Exa, SerpAPI, Jina, Firecrawl; video: Colab, Replicate, fal; voice: ElevenLabs.

**Council**
- Orchestrator can be any model. Roster with per-member roles. New pipeline: spec + file ownership, parallel build, automatic file checks (syntax, references, placeholders), orchestrator repair rounds.

**Other**
- Attach images and files (vision input, text, .docx, best-effort .pdf), drag and drop, paste.
- Video studio with a free Google Colab GPU notebook, Replicate and fal.ai.
- Roomier chat with a proportional font, auto-growing composer, focus mode.

## 2.0.0
Providers, memory database, council, files, agent mode, self-test.
