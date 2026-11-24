# Self-hosting

Two ways to run your own Kenobi:

- **The web version, from your own static hosting** — the same files as the public site. Everything runs in each
  visitor's browser; you host nothing else. Any static server works:

  ```bash
  python3 -m http.server 8000            # http://localhost:8000
  ```

  To publish it, copy the repository's files (or the GitHub Pages artifact, see `.github/workflows/pages.yml`) to
  your web server. Serve it over **HTTPS**: browsers only open the microphone on secure pages (`localhost` excepted).

- **The self-hosted server** — `server.py` serves the interface **and** answers the API. Keys and progress live on
  that computer; several devices of your network can use the same courses.

  ```bash
  python3 server.py                      # http://127.0.0.1:8192
  ```

The rest of this page is about `server.py`.

## Requirements

Python 3.8 or later, standard library only. The local voice installs its own packages in a separate environment
(`python3 scripts/installer_voix.py`, about 1.1 GB).

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `KENOBI_HOST` | `127.0.0.1` | interface to listen on (`0.0.0.0` for your network) |
| `KENOBI_PORT` | `8192` | port |
| `KENOBI_DATA_DIR` | `data` | data folder: `default-state.json`, and `runtime/` (progress, course documents, trash) |
| `KENOBI_CONFIG_DIR` | `~/.config/kenobi` (Linux), `~/Library/Application Support/kenobi` (macOS), `%APPDATA%\kenobi` (Windows) | settings and keys (`settings.json`, 0600) |
| `KENOBI_VOICE_DIR` | `~/.local/share/kenobi/voice` (Linux) | local voice (venv, Piper voice, Whisper model) |
| `KENOBI_REMOTE_SETTINGS` | unset | `1` lets pages opened from other computers change the settings (keys!). Only on a trusted network. |
| `KENOBI_GATEWAY_TOKEN_FILE` | `<config>/gateway.token` | token of the voice gateway |
| `MISTRAL_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY`, `DEEPSEEK_API_KEY`, `GROQ_API_KEY`, `ELEVENLABS_API_KEY` | unset | used when no key is saved for that provider |

## Connect a model

Open `http://127.0.0.1:8192` **on the computer that runs the server** (settings are read-only elsewhere), then
Réglages › Modèle d'IA: provider, key, « Charger la liste », model, « Enregistrer et tester ». A key stays tied to
the address it was given for: changing the provider or the address forgets it. The page never receives a key back.

## Run it as a service (Linux, systemd user units)

`deploy/` holds two examples. Adapt the paths, then:

```bash
mkdir -p ~/.config/systemd/user
cp deploy/kenobi-interface.service deploy/kenobi-terminal.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now kenobi-interface.service kenobi-terminal.service
journalctl --user -u kenobi-interface.service -n 50
```

`kenobi-terminal.service` runs `terminal_bridge.py --service`: the page opened on that computer plugs into it
without a code.

## Use it from other devices

With `KENOBI_HOST=0.0.0.0`, other devices of your network open `http://<server>:8192`. Everything works except two
things that browsers reserve for secure pages: the **microphone** (voice call) and, in recent Chrome and Edge, the
connection to a terminal bridge from a plain-HTTP page. Solutions:

- serve Kenobi over HTTPS with a reverse proxy (Caddy, nginx) or `tailscale serve` on a tailnet;
- or use the public web version on those devices.

Other computers plug their own terminal with the downloaded bridge ([terminal.md](terminal.md)).

## Voice gateway

`gateway.py` lets an outside voice-call application running on the same computer (a phone line, for example) talk
to Kenobi like to an agent, through an OpenAI-compatible endpoint. Kenobi answers in the latest session of the
active course with phone rules (the caller has no screen: no blocks, no multiple-choice cards, no code read aloud)
and the turns join the session's discussion, which open pages merge.

```bash
python3 gateway.py --creer-jeton     # creates <config>/gateway.token (0600) and prints its path, never the token
python3 gateway.py --etat            # open or closed
```

The call application then uses:

- `GET http://127.0.0.1:8192/health` → `{"status": "ok", "app": "kenobi", "gateway": true}`;
- `POST http://127.0.0.1:8192/v1/chat/completions` with `Authorization: Bearer <token>`, `"model": "kenobi"`,
  `"stream": true` and the caller's sentence as the last user message. Only loopback requests are served. Leading
  bracketed instruction lines that a call application may add (`[Mode appel vocal …]`) are dropped: Kenobi has its
  own phone rules.

Delete the token file to close the gateway.

## Backups

Progress lives in `data/runtime/` (`state.json`, one folder per course with `MEMORY.md` and the imported
`COURSE.md`). Back it up like any folder. `~/.config/kenobi/settings.json` holds keys: never put it in a shared
backup.

## Tests

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_*.py'
node --test tests/*.test.js
```

To try changes without touching your progress, run a second instance with its own folders:

```bash
KENOBI_PORT=8293 KENOBI_DATA_DIR=/tmp/kenobi/data KENOBI_CONFIG_DIR=/tmp/kenobi/config python3 server.py
```

(copy `data/default-state.json` and `data/plan-formation-python.md` into `/tmp/kenobi/data` first).
