# Contributing to Kenobi

Thank you for helping! Kenobi is a small project with a clear aim: a tutor that makes people **practise** and
**understand** code, running anywhere with the AI they choose. Contributions of every size are welcome: a typo, a
bug report, a course, a translation, a feature.

## Ways to help

- **Try it and report what goes wrong**: [open an issue](../../issues/new/choose) with the steps, the browser, the
  version (web or self-hosted) and the AI provider. Never paste an API key, even partially.
- **Write a course**: see [Courses](#courses) below. This is the most useful contribution for learners.
- **Translate**: see [Translations](#translations).
- **Improve the tutor's pedagogy**: the persona and method live in `tutor.py` (`PERSONA`, `PROTOCOL_*`,
  `turn_note`). Explain the learning problem you observed and how the change addresses it.
- **Code**: fix a bug, improve accessibility, add a provider, make the call more robust… Open an issue first for
  anything large, so we can agree on the approach.

## Getting started

Requirements: Python 3.8+ and Node.js 20+ (for the tests only). Nothing to install with pip or npm.

```bash
git clone https://github.com/motmot3000/Kenobi-tutor-IA.git
cd Kenobi-tutor-IA
python3 -m http.server 8000        # the web version: http://localhost:8000
python3 server.py                  # or the self-hosted version: http://127.0.0.1:8192
```

To test without spending anything, plug a local model (Ollama, LM Studio…) in Settings › Modèle d'IA, provider
« Autre service compatible OpenAI ».

## Run the tests

```bash
python3 scripts/build_browser.py --check     # browser/shared.js is up to date
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_*.py'
node --test tests/*.test.js
for f in *.js browser/*.js; do node --check "$f"; done
```

The tests never call a real service: `tests/stub_llm.py` is a fake AI provider (OpenAI and Anthropic protocols),
and the voice is faked too. The terminal bridge tests start a real zsh and bash (and PowerShell 7 if it is in your
`PATH`). The continuous integration runs the same commands on every pull request.

## The one rule of this code base: two versions, one behaviour

The interface talks to an API (`api/…`). Two implementations answer it:

- `server.py` and its Python modules, for the self-hosted version;
- `browser/*.js`, for the web version, inside the page.

They must behave the same. So:

1. **Texts and constants have one source**: the Python modules. After changing a prompt in `tutor.py`, a provider in
   `settings.py`, a voice in `voice.py`, a limit in `courses.py`…, run `python3 scripts/build_browser.py`: it
   regenerates `browser/shared.js`. Never edit `browser/shared.js` by hand.
2. **Logic changes go to both sides**, in the same pull request: `tutor.py` ↔ `browser/tutor.js`,
   `memory.py` ↔ `browser/memory.js`, `courses.py` ↔ `browser/courses.js`, `providers.py` ↔ `browser/providers.js`,
   `speech_text.py` ↔ `browser/speech.js`, `voice.py` ↔ `browser/voice.js`, routes of `server.py` ↔
   `browser/backend.js`.
3. **Parity tests prove it**: `tests/test_parity_*.py` run the same inputs through both versions (Node runs the
   JavaScript through `tests/parity/run.js`). Add cases for what you change.

## Code style

- **No build step, no dependency.** Vanilla HTML, CSS and JavaScript (ES2020, classic scripts with the same module
  pattern as `core.js`), Python standard library only for the server and the bridges (the local voice installs its
  own packages in a separate environment).
- The bridges (`terminal_bridge.py`, `voice_bridge.py`) are single files that must run on **Python 3.8** on Linux,
  macOS and Windows.
- Keep functions small and names explicit. Comment the *why*, not the *what*.
- User-facing text is French for now (see [Translations](#translations)). Code, comments and commit messages are in
  English (some older comments are still in French).
- Accessibility matters: keyboard navigation, visible focus, labels, contrast, `prefers-reduced-motion`.
- Security matters: never log message contents or keys; never send a key anywhere else than its provider; no
  third-party script (the Content Security Policy would block it anyway). Read [SECURITY.md](SECURITY.md).

## Commits and pull requests

- [Conventional Commits](https://www.conventionalcommits.org): `type(scope): subject`, imperative, ≤ 50 characters,
  English. Examples: `fix(call): keep the line open after a voice error`, `feat(courses): import from a URL`.
- One coherent change per commit; a body only when the *why* is not obvious.
- In the pull request: what changes for the learner, how you tested it (commands, browsers), screenshots for
  interface changes.
- All tests green, `browser/shared.js` up to date.

## Courses

A course is one Markdown document. The template (also downloadable from the « Ajouter un cours » dialog):

```markdown
# Course title

One or two sentences: who it is for and what it teaches.

**Niveau :** débutant
**Langage et outils :** Python, in the editor and the terminal

## Objectifs du cours
- what the learner can do at the end…

## Module 1 — First module title
What this module brings.

### Leçon 1 — First lesson title
- Objectif : what the learner can do at the end of the lesson.
- Notions : the notions.
- Exercice type : a task that proves it is acquired.
```

To share a course: add it to [`courses/`](courses/) as `courses/<short-name>.md`, add a line to
[`courses/README.md`](courses/README.md), and open a pull request. Check it imports well in Kenobi first (with and
without an AI connected). Courses must be your own work or under a free license (say which), in the domain of
computer science and programming. Read [docs/courses.md](docs/courses.md).

## Translations

The interface strings are in the JavaScript files (`app.js`, `settings.js`, `call.js`, `terminal.js`, `tour.js`…)
and `index.html`; the tutor's persona and method are in `tutor.py`. The plan is to move strings into per-language
files and to let the learner choose the tutor's language. If you want to lead this work, open an issue: it is the
most requested feature.

## Code of conduct

Be kind, assume good faith, explain rather than judge: we are building a tool for learners. See
[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
