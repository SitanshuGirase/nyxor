# Notes for certification (paste into the "Notes for certification" box)

**What it is.** NYXOR is a local AI workspace (packaged desktop app). It starts a small local server on 127.0.0.1 and shows its interface in an app window. There is no publisher server, account or sign-in.

**API keys are optional.** NYXOR works with a local model and no key at all; a key is only needed to use a cloud AI service. The key below is offered only so reviewers do not have to install a local model.

**How to test (about 5 minutes).**
1. Start NYXOR. Choose a passphrase (8+ characters) and press CREATE. Save the recovery code it shows, tick the box, press ENTER.
2. The privacy screen appears with nothing ticked. Tick "cloud AI provider" and press SAVE MY CHOICES.
3. Open SYSTEMS → PROVIDERS. In the box at the top paste this test key: **[TEST KEY]** (a rate-limited key created for certification and revoked after approval). It is recognised automatically. Press DETECT if asked.
4. Select that model in the model picker (top of the chat) and ask any question. The answer streams in.
5. Press REPORT under an answer: a dialog opens; SAVE ONLY stores the report on the PC. SYSTEMS → PRIVACY lists it.
6. Optional: SYSTEMS → ROUTER → Auto, then ask two different questions to see the model choice and reasons.
Without a key or a local model the app still starts and says plainly that no model is connected.

**Capabilities.**
- `runFullTrust` (restricted): NYXOR is a packaged desktop (Win32) application that runs a local Python process and a local web server bound to 127.0.0.1 only. It cannot be built as a sandboxed app without that.
- `internetClient`: outbound calls only to services the user connects and allows (cloud AI provider, search service, text-to-speech, video service).

**Generative AI (policy 11.16).** The app uses live generative AI from the model the user connects. Disclosed in the listing. Every answer has a REPORT button that saves a report on the PC and can prepare an email to [support address]. Reports are reviewed and acted on by changing safety rules or features. The system prompt contains safety rules in this build.

**Privacy (policy 10.5).** Privacy policy URL: [URL]. Nothing leaves the PC for cloud AI, web research, the page reader, cloud voice or video until the user allows that kind of flow on the first-run screen (revocable in SYSTEMS → PRIVACY). The browser's own speech recognition (microphone) is blocked until allowed.

**Dependencies (policy 10.2.4).** A model is required: a local model server installed by the user, or a cloud API key. Disclosed at the start of the description.

**Terminal panel.** Off by default. If the user enables it (SYSTEMS → Agent, with a plain warning), it runs the commands the user types as their own account inside the allowed folders; it cannot `cd` out of them and gives the child process none of NYXOR's environment variables. It is a convenience like any terminal app, not a remote-code feature.

**Dynamic code (policy 10.2.2).** AI-written code is only run (a) in a sandboxed preview the user starts, with no network and no access to chats or keys, and (b) in a restricted test process during council builds. The AGENT can run commands only after the user approves each one (no auto mode in this build).
