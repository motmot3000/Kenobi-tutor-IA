# Architecture

Kenobi is one interface with two interchangeable backends. This page explains how a message travels, where the
pedagogy lives, and how the two versions stay identical.

## One interface, two backends

The interface (`index.html`, `styles.css`, `app.js`, `core.js`, `settings.js`, `call.js`, `terminal.js`,
`editor.js`, `tour.js`) only talks to an HTTP API under `api/…` (relative paths, so Kenobi can live in a sub-folder).
`config.js` decides who answers:

| | Web version | Self-hosted version |
|---|---|---|
| Served by | any static hosting (GitHub Pages, `python3 -m http.server`…) | `python3 server.py` |
| `config.js` | the file itself: `window.KENOBI_BACKEND = "browser"` | answered by the server: `"server"` |
| API answered by | `browser/backend.js`, inside the page (it wraps `fetch`) | `server.py` |
| Progress | IndexedDB (`browser/store.js`) | `data/runtime/state.json`, one `MEMORY.md` per course |
| Keys | encrypted in the browser (`browser/vault.js`) | `~/.config/kenobi/settings.json` (0600) |
| AI and voice calls | from the browser to the provider (CORS) | from the server to the provider |
| Local voice | `kenobi-voice.py` bridge (Whisper + Piper) | `voice_worker.py`, installed by `scripts/installer_voix.py` |
| Terminal | `kenobi-terminal.py` bridge, paired by code | same bridge, as a service on the server's computer |

Every route exists on both sides with the same contract: `api/state` (with a version tag, `ETag` / `If-Match`),
`api/chat/stream` (server-sent events), `api/settings…`, `api/courses/import`, `api/voice/…`, `api/call/turn`,
`api/terminal…`, `api/memory`. The web version adds `api/backup` (export and import of everything it keeps) and
`api/settings/vault` (how keys are kept). The full list: [server-api.md](server-api.md).

## The trip of a message

1. The learner writes (or clicks an answer, or runs code). `app.js` adds the message to the session and **saves the
   whole state** (`POST api/state`).
2. `app.js` calls `POST api/chat/stream` with the session id, the message and the terminal state.
3. The backend rebuilds the context from the **saved state** (`tutor.py` / `browser/tutor.js`):
   - a **system prompt**, stable during a session (good for prompt caching): Kenobi's persona and method
     (`PERSONA`), the session context (date, learner, course, module, lesson and its goals, lesson contract, terminal,
     memory of the course, reviews due, last sessions, excerpt of the course document for the module), and the
     protocol of the interface blocks (`PROTOCOL_*`);
   - the **history** of the session, bounded (60 messages, 90 000 characters);
   - the **message**, followed by a reminder for this turn only (`turn_note`): open the lesson with a course, judge
     an attempt without correcting it, ask a multiple-choice question after two replies without one, etc.
4. The provider client (`providers.py` / `browser/providers.js`) streams the answer (OpenAI-compatible
   `/chat/completions` or Anthropic `/messages`), and learns which optional parameters a model refuses
   (`reasoning_effort`, `temperature`, `max_tokens`) to stop sending them.
5. `app.js` shows the answer as it arrives, then reads the **blocks** at its end.

## The blocks

Kenobi ends some answers with fenced blocks that the interface turns into cards and buttons (`core.js`):

| Block | Becomes |
|---|---|
| `kenobi-lecon` | the lesson contract: goal and success criteria |
| `kenobi-pret` | « C'est parti ! » / « J'ai une question » after the opening course |
| `kenobi-qcm` | a three-choice question, judged on click, with the explanation |
| `kenobi-boutons` | one-click answers |
| `kenobi-exercice` | an open instruction: file and expected result |
| `kenobi-verdict` | « Réussi », « Presque » or « Pas encore » after an attempt |
| `kenobi-bilan` | the recap: proof, skills, what to review, spaced reviews, next step |
| `kenobi-revision` | a review done from memory |
| `kenobi-memoire` | a fact to remember about the learner's learning |

An instruction stays open until a « réussi » verdict given in answer to an attempt. `tutor.has_open_task` and
`core.js` (`sessionTasks`) read a session the same way.

## The voice call

`call.js` reads the microphone with an AudioWorklet (`vad-worklet.js`), detects speech on the sound level (`vad.js`),
and sends each sentence as a 16 kHz WAV to `api/call/turn`. The backend transcribes it, streams the tutor's answer
with voice rules (short sentences, no Markdown, code shown and not read), cuts it into sentences
(`speech_text.py` / `browser/speech.js`) and synthesizes them in order while the answer is still being written.
Speaking over Kenobi lowers then cuts its voice; a short filler (« Voyons voir. ») is played when the answer takes
more than 1.1 s. Engines: local (Piper + Whisper), Mistral Voxtral, ElevenLabs. See [voice.md](voice.md).

## The terminal

`terminal_bridge.py` starts the computer's default shell in a pseudo-terminal and serves it over a WebSocket on
`127.0.0.1`. Its shell integration marks each command (OSC 633), so the bridge knows the command, its output and its
exit code. The Run button saves the editor's file and types the real command. See [terminal.md](terminal.md).

## Memory and progress

The state holds the courses, their modules and lessons (with status), the sessions (messages, contract, summary) and
each course's memory (acquired skills, points to review, spaced reviews J+1 → J+7 → J+21, facts, next focus).
`memory.py` / `browser/memory.js` validate it, align the built-in Python course with `data/default-state.json`
(titles, durations, goals; renamed lessons keep their progress) and render the course memory as Markdown.

Several pages can share a state (two tabs, or a call through the voice gateway): each save carries the version it
started from; a page that saved on an old version gets a conflict, merges the messages it did not have, and saves
again.

## Keeping the two versions identical

- Texts and constants come from Python: `python3 scripts/build_browser.py` writes `browser/shared.js`.
- Logic is ported by hand, module by module, and **parity tests** (`tests/test_parity_*.py`) run the same inputs
  through both: same system prompt, same history, same blocks reading, same memory Markdown, same course import,
  same spoken segments.

## The voice gateway (self-hosted, optional)

`gateway.py` lets an outside voice-call application on the same computer (a phone line, for example) talk to Kenobi
through an OpenAI-compatible endpoint, `POST /v1/chat/completions` (model `kenobi`, streaming, bearer token from a
0600 file). Kenobi answers in the latest session of the active course, with phone rules (no screen: no blocks, no
code read aloud), and the turns join the session's discussion. See [self-hosting.md](self-hosting.md#voice-gateway).
