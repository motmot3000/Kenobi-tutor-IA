# The terminal

Kenobi's terminal is **your computer's real terminal**, shown in the page, next to an editor with a Run button.
Kenobi sees what you run (if you let it) and answers each attempt; it never types anything itself.

A web page cannot open a terminal on its own: a small program, the **terminal bridge**, does it on your computer.

## Plug it in (web version, or any computer other than the server's)

1. Open the « Terminal » bar at the bottom of a lesson (or press Ctrl+J), then **« Brancher ce terminal »**.
2. **Download** `kenobi-terminal.py`. The file carries the address of the Kenobi page you downloaded it from, so the
   bridge knows which page it may trust.
3. **Run it** with Python 3.8 or later. The panel gives the exact command for your system; it picks the latest
   downloaded copy:
   - Linux: `python3 "$(ls -t "$(xdg-user-dir DOWNLOAD)"/kenobi-terminal*.py | head -n 1)"`
   - macOS: `python3 "$(ls -t ~/Downloads/kenobi-terminal*.py | head -n 1)"`
   - Windows (PowerShell): `py "$((ls $HOME\Downloads\kenobi-terminal*.py | sort LastWriteTime | select -Last 1).FullName)"`,
     or a double-click. On first launch it offers to install `pywinpty` (needed for a real Windows console).
4. On its first launch it **opens Kenobi already paired**. Otherwise, paste in the panel the code printed under
   « Code à coller dans Kenobi » (`8194-…`).

Keep the bridge's window open while you code; close it (or Ctrl+C) to unplug the terminal.

## What you get

- The default shell of your computer: zsh or bash on Linux and macOS, PowerShell 7 or Windows PowerShell on Windows
  (cmd as a last resort, without command tracking).
- Files in `~/Kenobi/<course>/<lesson>/` (`~/Kenobi/python/<lesson>/` for the Python course).
- **Run** saves the editor's file and types the real command: `python3 exercice.py`, `node exercice.js`,
  `bash exercice.sh`, `ruby`, `php`, `go run`, `java`.
- What Kenobi sees, in the bar: **Coach** (each run of an open instruction, and errors), **Silencieux** (runs join
  your next message), **Ne regarde pas** (nothing). Closed drawer = Kenobi sees nothing.
- « Je passe sur mon terminal »: you code elsewhere (VS Code…) and paste your code and output, or attach files.

## Options

```text
python3 kenobi-terminal.py [--port N] [--shell PATH] [--dossier DIR] [--kenobi ADDRESS]
                           [--sans-navigateur] [--brancher] [--nouveau-jeton]
```

- `--kenobi ADDRESS`: allow another Kenobi page, e.g. `https://example.org/Kenobi-tutor-IA/`.
- `--nouveau-jeton`: new pairing code; every browser has to paste it again.
- The bridge takes port 8194, or the next free one up to 8199.

## Security

The bridge listens on `127.0.0.1` only, refuses foreign `Host` headers, and requires the pairing code. Pages from
your computer, your local network or your tailnet are accepted with the code; a page from the Internet only if it is
the page you downloaded the bridge from, or one you allowed with `--kenobi`. Refusals are explained in the page and
in the bridge's window. See [SECURITY.md](../SECURITY.md).

## Browsers

Firefox and Chrome both work from the web version (HTTPS). Chrome asks once for permission to reach apps on your
device (Local Network Access): accept it. From a page served over plain HTTP on your network (self-hosted version
opened from another computer), recent Chrome and Edge block the connection to `127.0.0.1`: use Firefox, or serve
Kenobi over HTTPS.

## Self-hosted version

On the computer that runs `server.py`, start the bridge as a service: `python3 terminal_bridge.py --service`
(a systemd unit example is in `deploy/`). The page then gets the token from the server and plugs itself in.
