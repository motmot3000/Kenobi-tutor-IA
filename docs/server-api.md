# The API

The interface talks to this API with relative paths (`api/…`). `server.py` answers it in the self-hosted version;
`browser/backend.js` answers it inside the page in the web version. Both follow this contract; differences are
marked **server** or **web**.

Errors are JSON `{"error": "message in French"}` with the HTTP status; streamed routes send an `error` event
`{message, status}` instead once the stream has started.

## Health and state

- `GET api/health` → `{status, version, mode: "server"|"browser", agent}`. `agent`: `{configured, model, provider,
  providerId, problem, detail}` — is a model plugged, and the last error seen. No network call.
- `GET api/state` → the whole state (courses, modules, lessons, sessions, messages, memories), header `ETag` with the
  version tag. On first read the built-in course (`data/default-state.json`) is copied.
- `GET api/state/tag` → `{tag}`: cheap check used by open pages every few seconds.
- `POST api/state` (JSON, ≤ 16 MiB): saves the whole state. With `If-Match: "<tag>"`, refused with **409**
  `{error, conflict: true, tag}` if the state changed since that version; the page then merges the messages it does
  not have and saves again. Answer: `{ok: true, tag}`. Validation errors: 400. A course removed from the state goes to
  the trash with its sessions, memory and document.
- `GET api/memory?courseId=<id>[&download=1]` → the course memory, Markdown.

## Conversation

- `POST api/chat/stream` `{sessionId, message, terminal}` → server-sent events: `delta {text}`… then
  `done {mode, elapsed, firstToken, truncated, model}` or `error`. The backend reads the **saved** state to build the
  context: the page saves before calling. `terminal` is `coach`, `silent`, `off` or `closed`. Message ≤ 128 000
  characters (attached files included). No model: `error` with status 409.
- `POST api/chat` → the same without streaming: `{reply, …}`.

## Settings

- `GET api/settings` → public settings: `learner`, `ai` (`provider, label, baseUrl, model, reasoning, hasKey,
  keySource, configured`), `voice`, `providers`, `mistralVoices`, `localVoices`, `editable`, `mode`. **Never a key.**
  **web**: also `vault {mode, locked}` and `voice.local {url, paired}`.
- `POST api/settings` `{ai?, voice?, learner?}` (partial). `ai.apiKey` sets the key, `ai.clearKey` forgets it; a key
  is forgotten when the provider or the address changes. **server**: only from the server's computer (loopback client
  and `Host`, `Sec-Fetch-Site` same-origin or none), else 403. **web**: `voice.local.code` pairs the voice bridge
  (`<port>-<token>`), `voice.local.url` (+ `token`) sets any OpenAI-compatible speech server, `voice.local.clear`
  unpairs.
- `POST api/settings/models` `{ai: {provider, baseUrl?, apiKey?}}` → `{models: [{id, label}]}` from the provider
  (non-chat models filtered out).
- `POST api/settings/test` → a tiny question to the saved model: `{ok, model, provider, sample, firstToken, elapsed}`.
- **web** `POST api/settings/vault` `{action}`: `unlock {passphrase}`, `lock`, `mode {mode: device|passphrase|session,
  passphrase?}`, `forget` → public settings.

## Courses

- `POST api/courses/import` `{filename, markdown, useAi?}` (≤ 300 000 characters) → events `progress {stage,
  received?}`, then `done {course, notice}` or `error`. The document is kept; the course enters the state when the
  page saves it.
- `POST api/courses/write` `{wish}` (10 to 4 000 characters: what the learner wants to learn) → events `delta {text}`
  (the course document as the plugged model writes it), then `done {markdown, model, elapsed, truncated}` or `error`.
  No model plugged: 409. The prompt is `courses.AUTHOR_SYSTEM`, also offered in the interface to copy into any AI.
- `GET api/courses/source?courseId=<id>[&download=1]` → the course document (Markdown).
- `GET api/courses/template` → the template document.

## Voice and call

- `GET api/voice/status` → `{engine, label, ready, problem, local, fillers, fillerDelayMs}`.
- `POST api/voice/test` → `{audio (base64), mime, text}`: a test sentence in the chosen voice.
- `POST api/voice/voices` `{apiKey?}` → `{voices}` of the ElevenLabs account, French first.
- **server** `POST api/voice/install` → starts `scripts/installer_voix.py` in the background (202); follow it with
  `api/voice/status`. **web**: 501, the local voice is the voice bridge.
- **web** `GET api/voice/pont` → `kenobi-voice.py`, with this page's address written in it.
- `POST api/call/phrases` → greetings and fillers already spoken in the chosen voice, `{greetings, fillers, delayMs,
  ready, problem, engine}`.
- `POST api/call/turn`: one turn of the call. Body `audio/wav` (≤ 60 s; headers `X-Session-Id`, `X-Terminal`,
  `X-Carry` for the words of a replaced turn) or JSON `{sessionId, text, terminal}` (a run sent during the call).
  Events: `stage`, `transcript {text, heard, sttMs}`, `delta {text}`, `segment {index, text, audio, mime}` (one
  sentence, in order), then `done`, `empty` (nothing intelligible) or `error`. Closing the request stops the model and
  the voice.

## Terminal

- `GET api/terminal` → how this page plugs its terminal. **server**, page opened on the server's computer:
  `{available, url, token}` of the bridge service. Elsewhere (and **web**): `{available: false, reason: "remote",
  pairing: true, download}`, never a token.
- `GET api/terminal/pont` → `kenobi-terminal.py`, with this page's address written in it.

## Backup (web)

- `GET api/backup` → a JSON file with the state, the course documents and the settings (never the keys).
- `POST api/backup` (that file) → replaces what this browser keeps: `{ok, tag, courses}`.

## Voice gateway (server, optional)

- `GET /health` → `{status, app: "kenobi", version, gateway}`.
- `POST /v1/chat/completions` — OpenAI-compatible, for an outside voice-call application on the same computer. See
  [self-hosting.md](self-hosting.md#voice-gateway).

## Static files (server)

`server.py` serves only `index.html`, `config.js` (answered with `server`), `styles.css`, the interface scripts,
`browser/*.js` and `assets/`. Everything is sent with `Cache-Control: no-cache`.
