"""Pont du terminal de Kenobi : branche le terminal habituel de l'ordinateur sur l'interface.

Le pont lance le shell par défaut de l'ordinateur (zsh ou bash sous Linux et macOS, PowerShell sous Windows) dans
un pseudo-terminal et le relie au panneau « Terminal » de Kenobi par WebSocket.

- Sur l'ordinateur qui fait tourner server.py, il peut tourner en service (`kenobi-terminal.service`, option
  --service) : l'interface lit son jeton toute seule.
- Sur un autre ordinateur, on le télécharge depuis Kenobi (Terminal › Brancher) puis on le lance :
  `python3 kenobi-terminal.py` (Linux, macOS) ou `py kenobi-terminal.py` / double-clic (Windows). Au premier
  lancement, il ouvre Kenobi déjà branché ; sinon il affiche un code à coller dans Kenobi (Terminal › Brancher).

Sécurité : il n'écoute que la boucle locale, refuse tout hôte autre qu'elle (rebond DNS) et toute connexion sans le
jeton. Le jeton est la preuve du jumelage : une page de cet ordinateur, du réseau local ou du tailnet qui le présente
est acceptée, quelle que soit l'adresse par laquelle Kenobi a été ouvert ; une page d'Internet ne l'est que si elle a
téléchargé ce pont ou si on l'a donnée avec --kenobi. L'intégration du shell, incluse dans ce fichier, marque chaque
commande (séquences OSC 633) : le pont en tire la commande, sa sortie et son code de retour. Kenobi ne tape jamais rien.

Un seul fichier, bibliothèque standard, Python 3.8 ou plus. Windows : module `pywinpty` en plus (proposé au premier
lancement).
"""
import argparse
import asyncio
import base64
import hashlib
import hmac
import ipaddress
import json
import os
import platform
import re
import secrets
import shlex
import shutil
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser
from pathlib import Path
from urllib.parse import urlsplit

WINDOWS = os.name == "nt"
if not WINDOWS:
    import fcntl
    import pty
    import pwd
    import termios

VERSION = "1.3.0"
# 2 : un refus est expliqué dans la WebSocket (code faux, adresse refusée) ; toute page locale entre avec le jeton.
PROTOCOL = 2
# Remplie par Kenobi au téléchargement (Terminal › Brancher) : l'adresse de la page qui l'a téléchargé.
EMBEDDED_KENOBI_URL = ""
HOST = "127.0.0.1"
DEFAULT_PORT = 8194
SPARE_PORTS = range(8195, 8200)
DEFAULT_ORIGINS = ("http://127.0.0.1:8192", "http://localhost:8192")
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
SUBPROTOCOL = "kenobi-terminal.v1"
TOKEN_PREFIX = "token."
TOKEN_RE = re.compile(r"[0-9a-f]{48}")
ORIGIN_RE = re.compile(r"https?://(?:[a-z0-9.-]+|\[[0-9a-f:]+\])(?::\d{1,5})?", re.IGNORECASE)
# Adresses qui ne sont pas sur Internet : réseau local, tailnet (100.64.0.0/10, *.ts.net), noms sans domaine.
CGNAT = ipaddress.ip_network("100.64.0.0/10")
LOCAL_SUFFIXES = (".localhost", ".local", ".lan", ".home", ".home.arpa", ".internal", ".ts.net")
CLOSE_BAD_CODE = 4401
CLOSE_BAD_ORIGIN = 4403
MAX_FRAME = 2 * 1024 * 1024
MAX_INPUT = 64 * 1024
MAX_FILE = 512 * 1024
MAX_SCRIPT = 200 * 1024
MAX_PENDING_WRITE = 1024 * 1024
REPLAY_LIMIT = 256 * 1024
CAPTURE_HEAD = 64 * 1024
CAPTURE_TAIL = 64 * 1024
HIGH_WATER = 1024 * 1024
MAX_UNTERMINATED_MARK = 16 * 1024
LESSON_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
FILE_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}")
TEXT_SUFFIXES = {
    ".py", ".txt", ".csv", ".json", ".md", ".toml", ".cfg", ".ini", ".xml",
    ".html", ".css", ".js", ".sql", ".yaml", ".yml",
    ".sh", ".mjs", ".ts", ".c", ".h", ".cpp", ".java", ".rs", ".go", ".rb", ".php",
}
# Ce que Run sait lancer d'un clic : un interpréteur par extension. Le reste se tape dans le terminal.
RUNNERS = {".js": "node", ".mjs": "node", ".sh": "bash", ".rb": "ruby", ".php": "php", ".go": "go run", ".java": "java"}
SCRIPT_PROGRAM_RE = re.compile(r"(?:node|nodejs|bash|sh|zsh|ruby|php|java)(?:\.exe)?$", re.IGNORECASE)
PYTHON_PROGRAM_RE = re.compile(r"(?:python(?:3(?:\.\d+)?)?|py)(?:\.exe)?$", re.IGNORECASE)
POWERSHELL_KINDS = {"pwsh", "powershell"}
SHELL_LABELS = {
    "zsh": "zsh", "bash": "bash", "fish": "fish", "sh": "sh", "dash": "sh",
    "pwsh": "PowerShell 7", "powershell": "Windows PowerShell", "cmd": "Invite de commandes",
}
OS_LABELS = {"Darwin": "macOS", "Windows": "Windows", "Linux": "Linux"}

_ANSI_RE = re.compile(
    r"\x1b\[[0-?]*[ -/]*[@-~]"          # CSI : couleurs, curseur
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC : titre de fenêtre…
    r"|\x1b[PX^_][^\x1b]*\x1b\\"        # DCS, SOS, PM, APC
    r"|\x1b[ -/]+[0-~]"                  # jeux de caractères (ESC ( B)
    r"|\x1b[0-~]"                        # séquences courtes (ESC =, ESC 7, ESC c…)
    r"|\x1b"
)
_CONTROL_RE = re.compile(r"[\x00-\x07\x0b\x0c\x0e-\x1f\x7f]")
_BACKSPACE_RE = re.compile(r"[^\n\x08]\x08")


class BridgeError(Exception):
    """Demande refusée : le message est montré tel quel dans l'interface."""


class ProtocolError(Exception):
    """Trame WebSocket non conforme : la connexion est fermée."""


# ── Intégration des shells (marques OSC 633) ────────────────────────────────

ZSH_ENV = r"""# Kenobi : ce dossier sert de ZDOTDIR au démarrage du terminal intégré.
# On charge le .zshenv habituel, puis zsh lit le .zshrc voisin.
__kenobi_zdotdir="$ZDOTDIR"
ZDOTDIR="${KENOBI_USER_ZDOTDIR:-$HOME}"
if [[ -f "$ZDOTDIR/.zshenv" ]]; then
  source "$ZDOTDIR/.zshenv"
fi
KENOBI_USER_ZDOTDIR="$ZDOTDIR"
ZDOTDIR="$__kenobi_zdotdir"
unset __kenobi_zdotdir
"""

ZSH_RC = r"""# Kenobi : configuration habituelle de zsh, puis repérage des commandes (marques OSC 633).
ZDOTDIR="${KENOBI_USER_ZDOTDIR:-$HOME}"
unset KENOBI_USER_ZDOTDIR
if [[ -f "$ZDOTDIR/.zshrc" ]]; then
  source "$ZDOTDIR/.zshrc"
fi

__kenobi_escape() {
  __kenobi_reply="${1//\\/\\\\}"
  __kenobi_reply="${__kenobi_reply//$'\n'/\\n}"
  __kenobi_reply="${__kenobi_reply//$'\a'/}"
  __kenobi_reply="${__kenobi_reply//$'\e'/}"
}

__kenobi_precmd() {
  local kenobi_status=$?
  if [[ -n "${__kenobi_running-}" ]]; then
    builtin printf '\e]633;D;%s\a' "$kenobi_status"
    __kenobi_running=
  fi
  __kenobi_escape "$PWD"
  builtin printf '\e]633;P;Cwd=%s\a' "$__kenobi_reply"
  builtin printf '\e]633;A\a'
}

__kenobi_preexec() {
  __kenobi_running=1
  __kenobi_escape "$1"
  builtin printf '\e]633;E;%s\a' "$__kenobi_reply"
  builtin printf '\e]633;C\a'
}

autoload -Uz add-zsh-hook
precmd_functions=(__kenobi_precmd ${precmd_functions[@]})
add-zsh-hook preexec __kenobi_preexec
"""

BASH_RC = r"""# Kenobi : configuration habituelle de bash, puis repérage des commandes (marques OSC 633).
if [ -f "$HOME/.bashrc" ]; then
  . "$HOME/.bashrc"
fi

__kenobi_escape() {
  __kenobi_reply="${1//\\/\\\\}"
  __kenobi_reply="${__kenobi_reply//$'\n'/\\n}"
  __kenobi_reply="${__kenobi_reply//$'\a'/}"
  __kenobi_reply="${__kenobi_reply//$'\e'/}"
}

__kenobi_running=""
__kenobi_ready=""
__kenobi_last=0

__kenobi_status() {
  __kenobi_last=$?
}

__kenobi_precmd() {
  if [ -n "$__kenobi_running" ]; then
    printf '\e]633;D;%s\a' "$__kenobi_last"
    __kenobi_running=""
  fi
  __kenobi_escape "$PWD"
  printf '\e]633;P;Cwd=%s\a' "$__kenobi_reply"
  printf '\e]633;A\a'
  __kenobi_ready=1
}

__kenobi_preexec() {
  [ -n "$__kenobi_ready" ] || return
  [ -n "${COMP_LINE-}" ] && return
  __kenobi_ready=""
  local line
  line=$(HISTTIMEFORMAT= builtin history 1)
  if [[ $line =~ ^[[:space:]]*[0-9]+[*]?[[:space:]]+(.*)$ ]]; then
    line="${BASH_REMATCH[1]}"
  else
    line="$BASH_COMMAND"
  fi
  __kenobi_escape "$line"
  printf '\e]633;E;%s\a' "$__kenobi_reply"
  printf '\e]633;C\a'
  __kenobi_running=1
}

trap '__kenobi_preexec' DEBUG
PROMPT_COMMAND="__kenobi_status${PROMPT_COMMAND:+; $PROMPT_COMMAND}; __kenobi_precmd"
"""

# PowerShell 5.1 et 7 : le prompt habituel (profil compris) est gardé, on l'entoure des marques.
POWERSHELL_INTEGRATION = r"""
if (-not $Global:__KenobiTerminal) {
  $Global:__KenobiTerminal = $true
  $Global:__KenobiRunning = $false
  $Global:__KenobiPrompt = $function:prompt

  function Global:__KenobiEscape([string] $value) {
    if ($null -eq $value) { return '' }
    $value = $value.Replace('\', '\\').Replace("`n", '\n').Replace("`r", '')
    return $value.Replace([string][char]7, '').Replace([string][char]27, '')
  }

  function Global:prompt {
    $kenobiOk = $global:?
    $kenobiCode = $global:LASTEXITCODE
    $e = [char]27
    $b = [char]7
    $marks = ''
    if ($Global:__KenobiRunning) {
      $Global:__KenobiRunning = $false
      if ($kenobiOk) { $code = 0 }
      elseif ($kenobiCode -is [int] -and $kenobiCode -ne 0) { $code = $kenobiCode }
      else { $code = 1 }
      $marks += "$e]633;D;$code$b"
    }
    $marks += "$e]633;P;Cwd=$(__KenobiEscape $PWD.ProviderPath)$b$e]633;A$b"
    if ($Global:__KenobiPrompt) { $text = & $Global:__KenobiPrompt } else { $text = "PS $($PWD.ProviderPath)> " }
    $global:LASTEXITCODE = $kenobiCode
    return $marks + $text
  }

  if (Get-Command PSConsoleHostReadLine -ErrorAction SilentlyContinue) {
    $Global:__KenobiReadLine = $function:PSConsoleHostReadLine
    function Global:PSConsoleHostReadLine {
      $line = & $Global:__KenobiReadLine
      if ($line -and $line.Trim()) {
        $Global:__KenobiRunning = $true
        [Console]::Write("$([char]27)]633;E;$(__KenobiEscape $line)$([char]7)$([char]27)]633;C$([char]7)")
      }
      return $line
    }
  }
}
"""


# ── Emplacements et configuration (partagés avec server.py) ─────────────────


def configured_port():
    try:
        return int(os.environ.get("KENOBI_TERMINAL_PORT", DEFAULT_PORT))
    except ValueError:
        return DEFAULT_PORT


def config_dir():
    explicit = os.environ.get("KENOBI_TERMINAL_HOME")
    if explicit:
        return Path(os.path.expanduser(explicit))
    if WINDOWS:
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "kenobi-terminal"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "kenobi-terminal"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "kenobi-terminal"


def token_path():
    explicit = os.environ.get("KENOBI_TERMINAL_TOKEN_FILE")
    return Path(os.path.expanduser(explicit)) if explicit else config_dir() / "token"


def _private_file(path):
    try:
        info = os.lstat(path)
    except OSError:
        return False
    if not path.is_file() or path.is_symlink():
        return False
    return WINDOWS or (info.st_uid == os.getuid() and not info.st_mode & 0o077)


def read_token(path=None):
    """Jeton du pont, ou None si le fichier manque ou n'est pas privé (0600, même propriétaire)."""
    path = Path(path or token_path())
    if not _private_file(path):
        return None
    try:
        value = path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return value if TOKEN_RE.fullmatch(value) else None


def write_private(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    temporary = path.with_name(f".{path.name}.{os.getpid()}")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        os.write(descriptor, text.encode("utf-8"))
    finally:
        os.close(descriptor)
    os.replace(temporary, path)


def write_token(path, token):
    write_private(path, token)


def load_settings():
    try:
        value = json.loads((config_dir() / "settings.json").read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(settings):
    write_private(config_dir() / "settings.json", json.dumps(settings, ensure_ascii=False, indent=2))


def normalize_origin(value):
    text = str(value or "").strip().rstrip("/")
    return text.lower() if ORIGIN_RE.fullmatch(text) else None


def kenobi_page(value):
    """Adresse de Kenobi → (origine, page). La version web peut vivre dans un sous-dossier
    (https://exemple.github.io/Kenobi-tutor-IA/) : l'origine sert aux contrôles, la page au lien qui l'ouvre."""
    text = str(value or "").strip()
    origin = normalize_origin(text)
    if origin:
        return origin, origin + "/"
    parts = urlsplit(text)
    if parts.query or parts.fragment or parts.username or parts.password:
        return None, None
    origin = normalize_origin(f"{parts.scheme}://{parts.netloc}") if parts.scheme and parts.netloc else None
    if not origin or not re.fullmatch(r"/[A-Za-z0-9._~%/-]*", parts.path or "/"):
        return None, None
    path = parts.path or "/"
    if not path.endswith("/"):
        path = path.rsplit("/", 1)[0] + "/" if "." in path.rsplit("/", 1)[-1] else path + "/"
    return origin, origin + path


def local_origin(value):
    """Vrai pour une page servie par cet ordinateur, le réseau local ou le tailnet : jamais par Internet."""
    origin = normalize_origin(value)
    if not origin:
        return False
    host = urlsplit(origin).hostname or ""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return "." not in host or host.endswith(LOCAL_SUFFIXES)
    if getattr(address, "ipv4_mapped", None):
        address = address.ipv4_mapped
    return address.is_loopback or address.is_private or address.is_link_local or address in CGNAT


def exit_code(status):
    """Code de retour d'un processus attendu par waitpid (os.waitstatus_to_exitcode n'existe qu'à partir de 3.9)."""
    if hasattr(os, "waitstatus_to_exitcode"):
        return os.waitstatus_to_exitcode(status)
    return os.WEXITSTATUS(status) if os.WIFEXITED(status) else -os.WTERMSIG(status)


# ── Utilitaires ─────────────────────────────────────────────────────────────


def pretty_path(path, home=None):
    text = str(path)
    home = home or str(Path.home())
    if text == home:
        return "~"
    if text.startswith(home + os.sep):
        return "~" + os.sep + text[len(home) + 1:]
    return text


def shell_path(path, home=None):
    """Chemin à taper dans un shell POSIX : ~/… sans guillemets quand c'est possible."""
    home = home or str(Path.home())
    text = str(path)
    if text.startswith(home + os.sep):
        return "~/" + shlex.quote(text[len(home) + 1:])
    return shlex.quote(text)


def powershell_path(path, home=None):
    home = home or str(Path.home())
    text = str(path)
    if text.startswith(home + os.sep) and re.fullmatch(r"[A-Za-z0-9_.\\/-]+", text[len(home) + 1:]):
        return "~" + os.sep + text[len(home) + 1:]
    return "'" + text.replace("'", "''") + "'"


def shell_kind(path):
    name = Path(str(path)).name.lower()
    return name[:-4] if name.endswith(".exe") else name


def detect_shell():
    explicit = os.environ.get("KENOBI_TERMINAL_SHELL")
    if WINDOWS:
        root = os.environ.get("SystemRoot", r"C:\Windows")
        candidates = [
            explicit,
            shutil.which("pwsh"),
            shutil.which("powershell") or os.path.join(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe"),
            os.environ.get("ComSpec") or os.path.join(root, "System32", "cmd.exe"),
        ]
        for candidate in candidates:
            if candidate and os.path.isfile(candidate):
                return candidate
        return "cmd.exe"
    candidates = [explicit, os.environ.get("SHELL")]
    try:
        candidates.append(pwd.getpwuid(os.getuid()).pw_shell)
    except KeyError:
        pass
    candidates += ["/bin/zsh", "/bin/bash", "/bin/sh"]
    for candidate in candidates:
        if candidate and os.path.isabs(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return "/bin/sh"


def detect_python():
    """Commande à taper pour lancer Python dans le terminal, et sa version."""
    if WINDOWS:
        python = shutil.which("python")
        names = ["python"] if python and "windowsapps" not in python.lower() else []
        names += ["py"] if shutil.which("py") else []
    else:
        names = [name for name in ("python3", "python") if shutil.which(name)]
    for name in names:
        try:
            result = subprocess.run([shutil.which(name), "--version"], capture_output=True, text=True, timeout=5)
            version = (result.stdout or result.stderr).strip()
        except (OSError, subprocess.SubprocessError):
            continue
        if version.lower().startswith("python 3"):
            return name, version
    return None, ""


def unescape_mark(value):
    return re.sub(r"\\(\\|n)", lambda match: "\\" if match.group(1) == "\\" else "\n", value)


def _overwrite_line(line):
    if "\r" not in line:
        return line
    result = ""
    for part in line.split("\r"):
        result = part + result[len(part):]
    return result


def clean_output(raw, home=None):
    """Texte lisible d'une sortie de terminal : sans couleurs, retours chariot appliqués, ~ pour le dossier personnel."""
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
    text = _ANSI_RE.sub("", text)
    previous = None
    while previous != text:
        previous = text
        text = _BACKSPACE_RE.sub("", text)
    text = text.replace("\x08", "").replace("\r\n", "\n")
    text = _CONTROL_RE.sub("", text)
    lines = [_overwrite_line(line).rstrip() for line in text.split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    while lines and not lines[0]:
        lines.pop(0)
    text = "\n".join(lines)
    home = home or str(Path.home())
    return text.replace(home + "/", "~/").replace(home + "\\", "~\\")


def _partial_suffix(data, marker):
    """Longueur de la fin de `data` qui commence `marker` (marque coupée entre deux lectures)."""
    for size in range(min(len(marker) - 1, len(data)), 0, -1):
        if marker.startswith(data[-size:]):
            return size
    return 0


def websocket_accept(key):
    digest = hashlib.sha1((key + WS_GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def unmask(payload, mask):
    if not payload:
        return payload
    size = len(payload)
    key = (mask * (size // 4 + 1))[:size]
    return (int.from_bytes(payload, "little") ^ int.from_bytes(key, "little")).to_bytes(size, "little")


def encode_frame(opcode, payload=b"", mask=None):
    """Trame WebSocket ; `mask` (4 octets) seulement côté client, utile aux tests."""
    header = bytearray([0x80 | opcode])
    size = len(payload)
    flag = 0x80 if mask else 0
    if size < 126:
        header.append(flag | size)
    elif size < 65536:
        header.append(flag | 126)
        header += struct.pack("!H", size)
    else:
        header.append(flag | 127)
        header += struct.pack("!Q", size)
    if mask:
        header += mask
        payload = unmask(payload, mask)
    return bytes(header) + payload


# ── Repérage des commandes ──────────────────────────────────────────────────


class CommandTracker:
    """Lit les marques OSC 633 de l'intégration du shell et capture chaque commande avec sa sortie.

    A : invite affichée (le shell attend) · P;Cwd=… : dossier courant · E;… : ligne de commande ·
    C : début de l'exécution · D;n : fin, code de retour n.
    """

    MARK = b"\x1b]633;"

    def __init__(self, home=None):
        self.home = home or str(Path.home())
        self.pending = b""
        self.seen = False
        self.idle = False
        self.cwd = None
        self.command = None
        self.capturing = False
        self.started = 0.0
        self._head = bytearray()
        self._tail = bytearray()
        self._truncated = False

    def feed(self, data):
        """Renvoie (octets à afficher, évènements) ; les marques sont retirées du flux affiché."""
        data = self.pending + data
        self.pending = b""
        out = bytearray()
        events = []
        position = 0
        while True:
            start = data.find(self.MARK, position)
            if start == -1:
                rest = data[position:]
                keep = _partial_suffix(rest, self.MARK)
                self._visible(rest[: len(rest) - keep], out)
                self.pending = rest[len(rest) - keep:] if keep else b""
                break
            self._visible(data[position:start], out)
            ends = [index for index in (data.find(b"\x07", start), data.find(b"\x1b\\", start)) if index != -1]
            if not ends:
                if len(data) - start > MAX_UNTERMINATED_MARK:
                    self._visible(data[start:], out)
                else:
                    self.pending = data[start:]
                break
            end = min(ends)
            payload = data[start + len(self.MARK):end].decode("utf-8", "replace")
            event = self._mark(payload)
            if event:
                events.append(event)
            position = end + (1 if data[end:end + 1] == b"\x07" else 2)
        return bytes(out), events

    def _visible(self, chunk, out):
        if not chunk:
            return
        out += chunk
        if not self.capturing:
            return
        room = CAPTURE_HEAD - len(self._head)
        if room > 0:
            self._head += chunk[:room]
            chunk = chunk[room:]
        if chunk:
            self._tail += chunk
            if len(self._tail) > CAPTURE_TAIL:
                del self._tail[: len(self._tail) - CAPTURE_TAIL]
                self._truncated = True

    def _mark(self, payload):
        kind, _, value = payload.partition(";")
        self.seen = True
        if kind == "A":
            was_idle = self.idle
            self.idle = True
            return None if was_idle else {"t": "state", "busy": False, "integration": True}
        if kind == "P" and value.startswith("Cwd="):
            self.cwd = unescape_mark(value[4:])
            return {"t": "cwd", "cwd": pretty_path(self.cwd, self.home)}
        if kind == "E":
            self.command = unescape_mark(value)
            return None
        if kind == "C":
            self.idle = False
            self.capturing = True
            self.started = time.monotonic()
            self._head = bytearray()
            self._tail = bytearray()
            self._truncated = False
            return {"t": "command-start", "cmd": self.command or ""}
        if kind == "D":
            if not self.capturing:
                return None
            self.capturing = False
            try:
                code = int(value.split(";", 1)[0]) if value else None
            except ValueError:
                code = None
            raw = bytes(self._head) + (b"\n[...]\n" if self._truncated else b"") + bytes(self._tail)
            event = {
                "t": "command-done",
                "cmd": self.command or "",
                "exit": code,
                "output": clean_output(raw, self.home),
                "truncated": self._truncated,
                "durationMs": int((time.monotonic() - self.started) * 1000),
                "cwd": pretty_path(self.cwd, self.home) if self.cwd else None,
                "cwdPath": self.cwd,
            }
            self.command = None
            return event
        return None


# ── WebSocket ───────────────────────────────────────────────────────────────


class WebSocket:
    def __init__(self, reader, writer):
        self.reader = reader
        self.writer = writer
        self.closed = False

    async def receive(self):
        """Message complet (opcode, données) ; répond aux pings, renvoie (8, b"") à la fermeture."""
        fragments = []
        first = None
        total = 0
        while True:
            header = await self.reader.readexactly(2)
            fin = header[0] & 0x80
            opcode = header[0] & 0x0F
            if header[0] & 0x70:
                raise ProtocolError("bits réservés")
            if not header[1] & 0x80:
                raise ProtocolError("trame client non masquée")
            size = header[1] & 0x7F
            if size == 126:
                size = struct.unpack("!H", await self.reader.readexactly(2))[0]
            elif size == 127:
                size = struct.unpack("!Q", await self.reader.readexactly(8))[0]
            if size > MAX_FRAME or total + size > MAX_FRAME:
                raise ProtocolError("message trop long")
            mask = await self.reader.readexactly(4)
            payload = unmask(await self.reader.readexactly(size), mask)
            if opcode >= 0x8:
                if not fin or size > 125:
                    raise ProtocolError("trame de contrôle invalide")
                if opcode == 0x8:
                    return 0x8, payload
                if opcode == 0x9:
                    self.send_frame(0xA, payload)
                continue
            if opcode == 0x0:
                if first is None:
                    raise ProtocolError("continuation sans début")
            elif opcode in (0x1, 0x2):
                if first is not None:
                    raise ProtocolError("message entrelacé")
                first = opcode
            else:
                raise ProtocolError("opcode inconnu")
            fragments.append(payload)
            total += size
            if fin:
                return first, b"".join(fragments)

    def send_frame(self, opcode, payload=b""):
        if self.closed or self.writer.is_closing():
            return
        self.writer.write(encode_frame(opcode, payload))

    def send_json(self, value):
        self.send_frame(0x1, json.dumps(value, ensure_ascii=False).encode("utf-8"))

    def send_binary(self, data):
        if data:
            self.send_frame(0x2, data)

    def buffered(self):
        transport = self.writer.transport
        return transport.get_write_buffer_size() if transport else 0

    def close(self, code=1000, reason=""):
        if self.closed:
            return
        try:
            self.send_frame(0x8, struct.pack("!H", code) + reason.encode("utf-8")[:120])
        except Exception:
            pass
        self.closed = True
        try:
            self.writer.close()
        except Exception:
            pass


# ── Pseudo-terminaux ────────────────────────────────────────────────────────


class PosixPty:
    """Linux et macOS : pty.fork, lecture sans blocage dans la boucle asyncio."""

    can_pause = True

    def __init__(self, loop, on_data, on_exit):
        self.loop, self.on_data, self.on_exit = loop, on_data, on_exit
        self.pid = None
        self.fd = None
        self.reading = False
        self.write_buffer = bytearray()

    def spawn(self, argv, env, cwd, cols, rows):
        pid, fd = pty.fork()
        if pid == 0:  # pragma: no cover - processus enfant
            try:
                os.chdir(cwd)
                os.execvpe(argv[0], argv, env)
            finally:
                os._exit(127)
        self.pid, self.fd = pid, fd
        os.set_blocking(fd, False)
        self.resize(cols, rows)
        self.resume()

    def resume(self):
        if self.fd is not None and not self.reading:
            self.loop.add_reader(self.fd, self._readable)
            self.reading = True

    def pause(self):
        if self.fd is not None and self.reading:
            self.loop.remove_reader(self.fd)
            self.reading = False

    def _readable(self):
        try:
            data = os.read(self.fd, 65536)
        except BlockingIOError:
            return
        except OSError:
            data = b""
        if data:
            self.on_data(data)
            return
        self._close_fd()
        code = None
        try:
            pid, status = os.waitpid(self.pid, os.WNOHANG)
            if pid:
                code = exit_code(status)
            else:
                self.loop.call_later(1.0, self._reap)
        except ChildProcessError:
            pass
        self.on_exit(code)

    def write(self, data):
        if self.fd is None or not data:
            return
        if len(self.write_buffer) + len(data) > MAX_PENDING_WRITE:
            raise BridgeError("Le terminal ne lit plus son entrée : arrête le programme (Ctrl+C).")
        self.write_buffer += data
        self._flush()

    def _flush(self):
        while self.write_buffer and self.fd is not None:
            try:
                written = os.write(self.fd, self.write_buffer)
            except BlockingIOError:
                self.loop.add_writer(self.fd, self._flush)
                return
            except OSError:
                self.write_buffer.clear()
                break
            del self.write_buffer[:written]
        if self.fd is not None:
            self.loop.remove_writer(self.fd)

    def resize(self, cols, rows):
        if self.fd is not None:
            try:
                fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
            except OSError:
                pass

    def _close_fd(self):
        if self.fd is None:
            return
        self.pause()
        self.loop.remove_writer(self.fd)
        try:
            os.close(self.fd)
        except OSError:
            pass
        self.fd = None

    def _reap(self, attempts=10):
        try:
            pid, _ = os.waitpid(self.pid, os.WNOHANG)
        except ChildProcessError:
            return
        if pid:
            return
        if attempts <= 5:
            try:
                os.killpg(self.pid, signal.SIGKILL)
            except OSError:
                pass
        if attempts > 0:
            self.loop.call_later(1.0, self._reap, attempts - 1)

    def terminate(self):
        self._close_fd()
        if self.pid:
            try:
                os.killpg(self.pid, signal.SIGHUP)
            except OSError:
                pass
            self.loop.call_later(0.2, self._reap)


class WindowsPty:
    """Windows : ConPTY par pywinpty, lu dans un fil d'exécution qui renvoie vers la boucle asyncio."""

    can_pause = False

    def __init__(self, loop, on_data, on_exit):
        self.loop, self.on_data, self.on_exit = loop, on_data, on_exit
        self.process = None
        self.stopped = False

    def spawn(self, argv, env, cwd, cols, rows):
        from winpty import PtyProcess  # noqa: PLC0415 - module propre à Windows

        self.process = PtyProcess.spawn(argv, cwd=cwd, env=env, dimensions=(rows, cols))
        threading.Thread(target=self._read, name="kenobi-pty", daemon=True).start()

    def _read(self):
        while not self.stopped:
            try:
                data = self.process.read(65536)
            except (EOFError, OSError, ValueError):
                break
            if data:
                self.loop.call_soon_threadsafe(self.on_data, data.encode("utf-8", "replace"))
        code = None
        try:
            if not self.process.isalive():
                code = self.process.exitstatus
        except Exception:
            pass
        if not self.stopped:
            self.loop.call_soon_threadsafe(self.on_exit, code)

    def write(self, data):
        if self.process is None or not data:
            return
        try:
            self.process.write(data.decode("utf-8", "replace"))
        except (EOFError, OSError):
            pass

    def resize(self, cols, rows):
        if self.process is not None:
            try:
                self.process.setwinsize(rows, cols)
            except Exception:
                pass

    def pause(self):
        pass

    def resume(self):
        pass

    def terminate(self):
        self.stopped = True
        if self.process is not None:
            try:
                self.process.terminate(force=True)
            except Exception:
                pass


class ShellSession:
    def __init__(self, bridge):
        self.bridge = bridge
        self.cols = 100
        self.rows = 30
        self.tracker = CommandTracker(bridge.home)
        self.replay = bytearray()
        self.alive = False
        backend = WindowsPty if WINDOWS else PosixPty
        self.pty = backend(bridge.loop, self._on_data, self._on_exit)

    def spawn(self):
        argv, env = self.bridge.shell_command()
        self.pty.spawn(argv, env, str(self.bridge.workspace), self.cols, self.rows)
        self.alive = True

    def _on_data(self, data):
        if not self.alive:
            return
        visible, events = self.tracker.feed(data)
        if visible:
            self.replay += visible
            if len(self.replay) > REPLAY_LIMIT:
                cut = self.replay.find(b"\n", len(self.replay) - REPLAY_LIMIT)
                del self.replay[: cut + 1 if cut != -1 else len(self.replay) - REPLAY_LIMIT]
        self.bridge.on_shell_output(self, visible, events)

    def _on_exit(self, code):
        if not self.alive:
            return
        self.alive = False
        self.bridge.on_shell_exit(self, code)

    def write(self, data):
        if self.alive:
            self.pty.write(data)

    def resize(self, cols, rows):
        self.cols = max(10, min(500, int(cols)))
        self.rows = max(3, min(200, int(rows)))
        self.pty.resize(self.cols, self.rows)

    def pause_reading(self):
        self.pty.pause()

    def resume_reading(self):
        self.pty.resume()

    def terminate(self):
        """Ferme le terminal et tout ce qui y tourne."""
        self.alive = False
        self.pty.terminate()


# ── Pont ────────────────────────────────────────────────────────────────────


class TerminalBridge:
    def __init__(self, *, port=None, workspace=None, origins=None, token_file=None, shell=None, host=HOST,
                 spare_ports=False, service=False, announce=False):
        self.host = host
        self.requested_port = configured_port() if port is None else port
        self.spare_ports = spare_ports
        self.service = service
        # Fenêtre ouverte à la main : chaque branchement, débranchement ou refus y est écrit.
        self.announce = announce
        self.port = None
        self.home = str(Path.home())
        workspace = workspace or os.environ.get("KENOBI_WORKSPACE") or os.path.join("~", "Kenobi", "python")
        self.workspace = Path(os.path.expanduser(str(workspace))).resolve()
        # Le parcours Python d'origine garde son dossier ; chaque autre cours a le sien, à côté (~/Kenobi/<cours>).
        self.root = self.workspace.parent if self.workspace.name == "python" else self.workspace
        if origins is None:
            origins = list(DEFAULT_ORIGINS) + os.environ.get("KENOBI_TERMINAL_ORIGINS", "").split(",")
        elif isinstance(origins, str):
            origins = origins.split(",")
        self.origins = {origin for origin in (normalize_origin(item) for item in origins) if origin}
        self.token_file = Path(token_file) if token_file else token_path()
        self.shell = shell or detect_shell()
        self.shell_kind = shell_kind(self.shell)
        self.python_cmd, self.python_version = detect_python()
        self.hostname = socket.gethostname()
        self.token = None
        self.token_created = False
        self._token_stamp = None
        self._refusals = {}
        self.busy_ports = []
        self.loop = None
        self.server = None
        self.session = None
        self.client = None
        self.pending_run = None
        self._draining = False

    # Démarrage et arrêt

    def load_or_create_token(self, renew=False):
        token = None if renew else read_token(self.token_file)
        if not token:
            token = secrets.token_hex(24)
            write_token(self.token_file, token)
            self.token_created = True
        self.token = token
        self._token_stamp = self._stamp()
        return token

    def _stamp(self):
        try:
            return self.token_file.stat().st_mtime_ns
        except OSError:
            return None

    def current_token(self):
        """Jeton en vigueur, relu s'il a changé : un autre pont de cet ordinateur partage ce fichier et peut le renouveler."""
        stamp = self._stamp()
        if stamp is not None and stamp != self._token_stamp:
            self._token_stamp = stamp
            value = read_token(self.token_file)
            if value:
                self.token = value
        return self.token

    def origin_allowed(self, origin):
        return bool(origin) and (origin in self.origins or local_origin(origin))

    async def start(self):
        self.loop = asyncio.get_running_loop()
        self.workspace.mkdir(parents=True, exist_ok=True)
        if not self.token:
            self.load_or_create_token()
        ports = [self.requested_port] + (list(SPARE_PORTS) if self.spare_ports and self.requested_port else [])
        listener = None
        for port in ports:
            candidate = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            if WINDOWS:
                candidate.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                candidate.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                candidate.bind((self.host, port))
            except OSError as error:
                candidate.close()
                if port == ports[-1]:
                    if len(ports) > 1:
                        raise OSError(error.errno, f"ports {ports[0]} à {ports[-1]} tous occupés") from error
                    raise
                self.busy_ports.append(port)
                continue
            listener = candidate
            break
        listener.listen(16)
        listener.setblocking(False)
        self.port = listener.getsockname()[1]
        self.server = await asyncio.start_server(self._handle, sock=listener)

    async def close(self):
        if self.client:
            self.client.close(1001, "Pont arrêté")
        if self.session:
            self.session.terminate()
            self.session = None
        if self.server:
            self.server.close()
            await self.server.wait_closed()

    @property
    def pairing_code(self):
        return f"{self.port}-{self.current_token()}"

    def integration_dir(self):
        folder = config_dir() / "shell"
        for relative, content in ((("zsh", ".zshenv"), ZSH_ENV), (("zsh", ".zshrc"), ZSH_RC), (("bash", "bashrc"), BASH_RC)):
            path = folder.joinpath(*relative)
            try:
                if not path.is_file() or path.read_text(encoding="utf-8") != content:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(content, encoding="utf-8")
            except OSError:
                pass
        return folder

    def shell_command(self):
        env = dict(os.environ)
        for name in ("INVOCATION_ID", "JOURNAL_STREAM", "NOTIFY_SOCKET", "MANAGERPID", "SYSTEMD_EXEC_PID"):
            env.pop(name, None)
        env.update(COLORTERM="truecolor", TERM_PROGRAM="kenobi", KENOBI_TERMINAL="1")
        kind = self.shell_kind
        if kind in POWERSHELL_KINDS:
            encoded = base64.b64encode(POWERSHELL_INTEGRATION.encode("utf-16-le")).decode("ascii")
            return [self.shell, "-NoLogo", "-NoExit", "-EncodedCommand", encoded], env
        if WINDOWS:
            return [self.shell], env
        env["TERM"] = "xterm-256color"
        if kind == "zsh":
            folder = self.integration_dir()
            env["KENOBI_USER_ZDOTDIR"] = env.get("ZDOTDIR") or env.get("HOME") or self.home
            env["ZDOTDIR"] = str(folder / "zsh")
            return [self.shell, "-i"], env
        if kind == "bash":
            return [self.shell, "--rcfile", str(self.integration_dir() / "bash" / "bashrc"), "-i"], env
        return [self.shell, "-i"], env

    def ensure_session(self):
        if self.session is None or not self.session.alive:
            self.session = ShellSession(self)
            self.session.spawn()
        return self.session

    def restart(self):
        if self.session:
            self.session.terminate()
        self.session = None
        self.pending_run = None
        return self.ensure_session()

    # Évènements du shell

    def on_shell_output(self, session, visible, events):
        if session is not self.session:
            return
        client = self.client
        if client and visible:
            client.send_binary(visible)
            if session.pty.can_pause and client.buffered() > HIGH_WATER and not self._draining:
                session.pause_reading()
                self._draining = True
                self.loop.create_task(self._drain(client, session))
        for event in events:
            if event["t"] == "command-done":
                event = self._describe_command(event)
            if client:
                client.send_json(event)

    async def _drain(self, client, session):
        try:
            await client.writer.drain()
        except Exception:
            pass
        finally:
            self._draining = False
            if session is self.session and session.alive:
                session.resume_reading()

    def on_shell_exit(self, session, code):
        if session is not self.session:
            return
        self.pending_run = None
        if self.client:
            self.client.send_json({"t": "exit", "code": code})

    def _describe_command(self, event):
        cwd = event.pop("cwdPath", None)
        run = self.pending_run
        if run and event["cmd"].strip() == run["command"]:
            self.pending_run = None
            event.update(source="run", runId=run["runId"], file=run["file"], lesson=run["lesson"],
                         course=run.get("course", ""), code=run["code"])
            return event
        event["source"] = "shell"
        script = self._script_of(event["cmd"], cwd)
        if script:
            event.update(file=script[0], code=script[1])
        return event

    def _script_of(self, command, cwd):
        """Fichier lancé par une commande tapée à la main (python, node, bash…), s'il est lisible et court."""
        try:
            words = shlex.split(command, posix=not WINDOWS)
        except ValueError:
            return None
        while words and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", words[0]):
            words.pop(0)
        program = os.path.basename(words[0].strip("\"'")) if words else ""
        if PYTHON_PROGRAM_RE.fullmatch(program):
            suffixes = (".py",)
        elif SCRIPT_PROGRAM_RE.fullmatch(program):
            suffixes = tuple(RUNNERS)
        else:
            return None
        script = next((word.strip("\"'") for word in words[1:] if word.strip("\"'").lower().endswith(suffixes)
                       and not word.startswith("-")), None)
        if not script:
            return None
        path = Path(os.path.expanduser(script))
        if not path.is_absolute():
            path = Path(cwd or self.workspace) / path
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_SCRIPT:
                return None
            return path.name, path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

    # Fichiers de la leçon

    def course_dir(self, course=None):
        if course in (None, "", "python"):
            return self.workspace
        if not isinstance(course, str) or not LESSON_RE.fullmatch(course):
            raise BridgeError("Cours inconnu pour le dossier de travail.")
        return self.root / course

    def lesson_dir(self, lesson, course=None):
        if not isinstance(lesson, str) or not LESSON_RE.fullmatch(lesson):
            raise BridgeError("Leçon inconnue pour le dossier de travail.")
        return self.course_dir(course) / lesson

    def file_path(self, lesson, name, course=None):
        if not isinstance(name, str) or not FILE_RE.fullmatch(name):
            raise BridgeError("Nom de fichier refusé : lettres sans accent, chiffres, point, tiret ou souligné.")
        if Path(name).suffix.lower() not in TEXT_SUFFIXES:
            raise BridgeError("L'éditeur ouvre les fichiers texte : .py, .js, .sh, .txt, .csv, .json, .md…")
        path = self.lesson_dir(lesson, course) / name
        if path.is_symlink():
            raise BridgeError("Ce fichier est un lien : ouvre-le dans le terminal.")
        return path

    def open_file(self, lesson, name, course=None):
        path = self.file_path(lesson, name, course)
        if not path.exists():
            return {"exists": False, "code": "", "path": pretty_path(path, self.home)}
        if not path.is_file():
            raise BridgeError("Ce nom désigne un dossier, pas un fichier.")
        if path.stat().st_size > MAX_FILE:
            raise BridgeError("Fichier trop grand pour l'éditeur (512 Kio au plus).")
        try:
            code = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raise BridgeError("Ce fichier n'est pas du texte UTF-8.") from None
        return {"exists": True, "code": code, "path": pretty_path(path, self.home), "mtime": path.stat().st_mtime}

    def save_file(self, lesson, name, code, course=None):
        if not isinstance(code, str):
            raise BridgeError("Contenu de fichier invalide.")
        data = code.encode("utf-8")
        if len(data) > MAX_FILE:
            raise BridgeError("Fichier trop grand pour l'éditeur (512 Kio au plus).")
        path = self.file_path(lesson, name, course)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.kenobi-{os.getpid()}")
        temporary.write_bytes(data)
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
        return {"path": pretty_path(path, self.home), "mtime": path.stat().st_mtime}

    def list_files(self, lesson, course=None):
        folder = self.lesson_dir(lesson, course)
        files = []
        if folder.is_dir():
            for entry in sorted(folder.iterdir(), key=lambda item: item.name.lower()):
                if entry.is_symlink() or not entry.is_file() or not FILE_RE.fullmatch(entry.name):
                    continue
                if entry.suffix.lower() not in TEXT_SUFFIXES:
                    continue
                stat_result = entry.stat()
                files.append({"name": entry.name, "size": stat_result.st_size, "mtime": stat_result.st_mtime})
                if len(files) >= 100:
                    break
        return {"files": files, "path": pretty_path(folder, self.home)}

    def run_command(self, lesson, name, course=None):
        """La vraie commande à taper : se placer dans le dossier de la leçon si besoin, puis lancer le fichier."""
        target = self.lesson_dir(lesson, course).resolve()
        kind = self.shell_kind
        suffix = Path(name).suffix.lower()
        if suffix == ".py":
            runner = self.python_cmd or ("python" if WINDOWS else "python3")
        elif suffix in RUNNERS:
            runner = RUNNERS[suffix]
        else:
            raise BridgeError("Run lance les fichiers .py, .js, .sh, .rb, .php, .go et .java : pour les autres, "
                              "tape la commande dans le terminal.")
        command = f"{runner} {name}"
        cwd = self.session.tracker.cwd if self.session else None
        try:
            here = Path(cwd).resolve() if cwd else None
        except OSError:
            here = None
        if here == target:
            return command
        if kind in POWERSHELL_KINDS:
            return f"cd {powershell_path(target, self.home)}; {command}"
        if kind == "cmd":
            return f'cd /d "{target}" && {command}'
        return f"cd {shell_path(target, self.home)} && {command}"

    def start_run(self, lesson, name, code, course=None):
        command = self.run_command(lesson, name, course)
        if code is not None:
            self.save_file(lesson, name, code, course)
        elif not self.file_path(lesson, name, course).is_file():
            raise BridgeError("Ce fichier n'existe pas encore : écris ton code, puis Run.")
        session = self.ensure_session()
        if session.tracker.seen and not session.tracker.idle:
            return {"t": "run-refused", "reason": "busy",
                    "message": "Un programme tourne encore dans le terminal : arrête-le (Ctrl+C), puis relance."}
        command = self.run_command(lesson, name, course)
        run_id = secrets.token_hex(6)
        self.pending_run = {"runId": run_id, "command": command, "lesson": lesson, "file": name, "course": course or "",
                            "code": code if code is not None else self.open_file(lesson, name, course)["code"]}
        if self.shell_kind in POWERSHELL_KINDS:
            prefix = b""
        elif self.shell_kind == "cmd":
            prefix = b"\x1b"  # Échap efface la saisie en cours.
        else:
            prefix = b"\x05\x15"  # Ctrl+E puis Ctrl+U : efface une saisie en cours.
        session.write(prefix + command.encode("utf-8") + b"\r")
        return {"t": "run-started", "runId": run_id, "command": command, "captured": session.tracker.seen}

    # Connexions

    def ready_payload(self):
        tracker = self.session.tracker if self.session else None
        system = platform.system()
        return {
            "t": "ready",
            "version": VERSION,
            "protocol": PROTOCOL,
            "shell": self.shell_kind,
            "shellLabel": SHELL_LABELS.get(self.shell_kind, self.shell_kind),
            "host": self.hostname,
            "os": system,
            "osLabel": OS_LABELS.get(system, system),
            "sep": os.sep,
            "python": self.python_version,
            "pythonCmd": self.python_cmd,
            "workspace": pretty_path(self.workspace, self.home),
            "root": pretty_path(self.root, self.home),
            "courses": True,
            "cwd": pretty_path(tracker.cwd, self.home) if tracker and tracker.cwd else None,
            "integration": bool(tracker and tracker.seen),
            "busy": bool(tracker and tracker.seen and not tracker.idle),
        }

    def _allowed_host(self, host):
        return (host or "").lower() in {f"127.0.0.1:{self.port}", f"localhost:{self.port}", f"[::1]:{self.port}"}

    def health_payload(self):
        return {"ok": True, "app": "kenobi-terminal", "version": VERSION, "protocol": PROTOCOL,
                "shell": self.shell_kind, "host": self.hostname, "service": self.service,
                "attached": self.client is not None}

    async def _handle(self, reader, writer):
        try:
            try:
                head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=10)
            except (asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
                return
            method, path, headers = _parse_request(head)
            origin = normalize_origin(headers.get("origin")) or ""
            if path == "/health" and method in ("GET", "OPTIONS"):
                # Seule une page qui a le droit d'entrer lit la réponse (CORS) ; un autre site n'y voit rien.
                extra = {"Vary": "Origin"}
                if self.origin_allowed(origin) and self._allowed_host(headers.get("host")):
                    extra.update({"Access-Control-Allow-Origin": origin, "Access-Control-Allow-Private-Network": "true"})
                if method == "OPTIONS":
                    extra.update({"Access-Control-Allow-Methods": "GET", "Access-Control-Max-Age": "600"})
                    _respond(writer, 204, "No Content", extra=extra)
                else:
                    _respond(writer, 200, "OK", json.dumps(self.health_payload()).encode("utf-8"), extra=extra)
                return
            if path != "/terminal" or method != "GET":
                _respond(writer, 404, "Not Found", b'{"error":"Route inconnue."}')
                return
            refusal = self._check_request(headers)
            if refusal:
                self._refusal("request", f"Connexion refusée : {refusal}.", always=True)
                _respond(writer, 403, "Forbidden", b'{"error":"Connexion refusee."}')
                return
            token_ok = self._token_ok(headers)
            origin_ok = self.origin_allowed(origin)
            if not token_ok and not origin_ok:
                # Une page d'Internet sans le code : rien à lui expliquer.
                self._refusal("page", f"Connexion refusée : page {origin or 'sans origine'}, sans le bon code.", always=True)
                _respond(writer, 403, "Forbidden", b'{"error":"Connexion refusee."}')
                return
            writer.write((
                "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {websocket_accept(headers['sec-websocket-key'])}\r\n"
                f"Sec-WebSocket-Protocol: {SUBPROTOCOL}\r\n\r\n"
            ).encode("ascii"))
            socket_ = WebSocket(reader, writer)
            if not token_ok:
                await self._deny(socket_, "token", CLOSE_BAD_CODE,
                                 "Ce code n'est pas celui du pont : colle celui qu'affiche sa fenêtre.")
                self._refusal("token", "Refusé : ce navigateur a un ancien code. Colle dans Kenobi le code ci-dessous :",
                              f"  {self.pairing_code}")
                return
            if not origin_ok:
                await self._deny(socket_, "origin", CLOSE_BAD_ORIGIN,
                                 f"Ce pont n'accepte pas l'adresse {origin}. Relance-le avec : --kenobi {origin}")
                self._refusal("origin", f"Refusé : la page {origin} n'est pas sur ce réseau. Si c'est bien ton Kenobi, "
                              f"relance le pont avec --kenobi {origin}", always=True)
                return
            await self._serve(socket_, origin)
        finally:
            if not writer.is_closing():
                writer.close()

    def _check_request(self, headers):
        """Refus sans explication : pas une WebSocket de Kenobi, ou un hôte qui n'est pas cet ordinateur."""
        if headers.get("upgrade", "").lower() != "websocket" or "upgrade" not in headers.get("connection", "").lower():
            return "pas une demande WebSocket"
        if headers.get("sec-websocket-version") != "13" or not headers.get("sec-websocket-key"):
            return "version WebSocket non prise en charge"
        if not self._allowed_host(headers.get("host")):
            return f"hôte inattendu ({headers.get('host') or 'absent'})"
        if SUBPROTOCOL not in self._offered(headers):
            return "client qui n'est pas Kenobi"
        return None

    @staticmethod
    def _offered(headers):
        return [item.strip() for item in headers.get("sec-websocket-protocol", "").split(",")]

    def _token_ok(self, headers):
        token = next((item[len(TOKEN_PREFIX):] for item in self._offered(headers) if item.startswith(TOKEN_PREFIX)), "")
        expected = self.current_token() or ""
        return bool(expected) and hmac.compare_digest(token.encode(), expected.encode())

    async def _deny(self, socket_, reason, code, message):
        """Refus expliqué à la page : elle sait alors s'il faut un nouveau code ou une autre adresse."""
        socket_.send_json({"t": "denied", "reason": reason, "message": message, "version": VERSION})
        socket_.send_frame(0x8, struct.pack("!H", code) + (b"Code faux" if reason == "token" else b"Adresse refusee"))

        async def closing_reply():
            while (await socket_.receive())[0] != 0x8:
                pass

        try:
            # Le navigateur ne donne le code de fermeture à la page qu'après avoir répondu à la fermeture.
            await asyncio.wait_for(closing_reply(), timeout=2)
        except Exception:
            pass
        socket_.closed = True
        socket_.writer.close()

    def _say(self, message, always=False):
        """Écrit dans la fenêtre du pont (ou le journal du service, pour les refus seulement)."""
        if self.announce:
            print(message, flush=True)
        elif always:
            _log(message)

    def _refusal(self, reason, *lines, always=False):
        """Un refus est écrit au plus une fois toutes les 20 s par motif : un onglet resté sur une ancienne version de
        Kenobi, ou un site qui insiste, réessaie en boucle."""
        now = time.monotonic()
        if now - self._refusals.get(reason, -60.0) < 20:
            return
        self._refusals[reason] = now
        for line in lines:
            self._say(line, always=always)

    async def _serve(self, socket_, origin=""):
        previous = self.client
        self.client = socket_
        if previous:
            previous.close(4000, "Terminal ouvert ailleurs")
        try:
            session = self.ensure_session()
        except Exception as error:
            socket_.send_json({"t": "fatal", "message": f"Impossible de lancer le terminal : {error}"})
            socket_.close(1011, "Terminal indisponible")
            self.client = None
            return
        socket_.send_json(self.ready_payload())
        if session.replay:
            socket_.send_binary(bytes(session.replay))
        self._say(f"Kenobi est branché ({origin or 'navigateur'}).")
        try:
            while True:
                opcode, payload = await socket_.receive()
                if opcode == 0x8:
                    break
                if opcode == 0x2:
                    self.ensure_session().write(payload[:MAX_INPUT])
                    continue
                try:
                    message = json.loads(payload.decode("utf-8"))
                except (UnicodeDecodeError, ValueError):
                    continue
                if isinstance(message, dict):
                    self._dispatch(message, socket_)
        except (asyncio.IncompleteReadError, ConnectionError, ProtocolError):
            pass
        finally:
            if self.client is socket_:
                self.client = None
                self._say("Kenobi s'est débranché (terminal fermé dans la page). Le shell attend la prochaine ouverture.")
            socket_.close()

    def _dispatch(self, message, socket_):
        kind = message.get("t")
        request_id = message.get("id")

        def reply(value):
            if request_id is not None:
                value["id"] = request_id
            socket_.send_json(value)

        try:
            if kind == "input":
                session = self.session
                if session and session.alive:
                    session.write(str(message.get("d", ""))[:MAX_INPUT].encode("utf-8"))
            elif kind == "resize":
                session = self.session
                if session:
                    session.resize(message.get("cols", 100), message.get("rows", 30))
            elif kind == "interrupt":
                if self.session and self.session.alive:
                    self.session.write(b"\x03")
            elif kind == "restart":
                session = self.restart()
                reply({"t": "restarted"})
                socket_.send_json(self.ready_payload())
                if session.replay:
                    socket_.send_binary(bytes(session.replay))
            elif kind == "open":
                reply({"t": "file", "lesson": message.get("lesson"), "file": message.get("file"),
                       **self.open_file(message.get("lesson"), message.get("file"), message.get("course"))})
            elif kind == "save":
                reply({"t": "saved", "file": message.get("file"),
                       **self.save_file(message.get("lesson"), message.get("file"), message.get("code"),
                                        message.get("course"))})
            elif kind == "list":
                reply({"t": "files", **self.list_files(message.get("lesson"), message.get("course"))})
            elif kind == "run":
                code = message.get("code")
                reply(self.start_run(message.get("lesson"), message.get("file"),
                                     code if isinstance(code, str) else None, message.get("course")))
            elif kind == "ping":
                reply({"t": "pong"})
        except BridgeError as error:
            reply({"t": "error", "message": str(error)})
        except (OSError, ValueError, TypeError):
            reply({"t": "error", "message": "Le pont du terminal n'a pas pu traiter la demande."})


def _parse_request(head):
    lines = head.decode("latin-1").split("\r\n")
    parts = lines[0].split(" ")
    method = parts[0] if parts else ""
    target = parts[1] if len(parts) > 1 else ""
    path = target.split("?", 1)[0]
    headers = {}
    for line in lines[1:]:
        if not line:
            continue
        name, _, value = line.partition(":")
        key = name.strip().lower()
        value = value.strip()
        headers[key] = f"{headers[key]}, {value}" if key in headers else value
    return method, path, headers


def _respond(writer, status, reason, body=b"", extra=None):
    lines = [
        f"HTTP/1.1 {status} {reason}",
        "Content-Type: application/json; charset=utf-8",
        f"Content-Length: {len(body)}",
        "Cache-Control: no-store",
        "X-Content-Type-Options: nosniff",
        "Connection: close",
    ]
    lines += [f"{name}: {value}" for name, value in (extra or {}).items()]
    writer.write(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body)


def _log(message):
    print(message, file=sys.stderr, flush=True)


# ── Lancement ───────────────────────────────────────────────────────────────


def _ensure_windows_pty():
    """Sous Windows, le vrai terminal passe par pywinpty : on propose de l'installer s'il manque."""
    try:
        import winpty  # noqa: F401, PLC0415

        return True
    except ImportError:
        pass
    print("Pour brancher un vrai terminal sous Windows, le pont a besoin du module pywinpty")
    print("(PyPI, licence MIT, environ 1 Mo).")
    if not sys.stdin or not sys.stdin.isatty():
        print(f"Installe-le avec : {sys.executable} -m pip install --user pywinpty")
        return False
    answer = input("L'installer maintenant pour ton compte ? [O/n] ").strip().lower()
    if answer not in ("", "o", "oui", "y", "yes"):
        return False
    result = subprocess.run([sys.executable, "-m", "pip", "install", "--user", "pywinpty"])
    if result.returncode != 0:
        print("L'installation a échoué. Vérifie ta connexion, puis relance le pont.")
        return False
    import importlib  # noqa: PLC0415
    import site  # noqa: PLC0415

    user_site = site.getusersitepackages()
    if user_site not in sys.path:
        sys.path.append(user_site)
    importlib.invalidate_caches()
    try:
        import winpty  # noqa: F401, PLC0415

        return True
    except ImportError:
        print("pywinpty est installé : relance le pont pour l'utiliser.")
        return False


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="kenobi-terminal",
        description="Branche le terminal de cet ordinateur sur Kenobi.",
    )
    parser.add_argument("--kenobi", metavar="ADRESSE", help="adresse de Kenobi, par exemple http://192.168.1.20:8192 "
                        "ou https://exemple.github.io/Kenobi-tutor-IA/")
    parser.add_argument("--port", type=int, help=f"port local du pont ({DEFAULT_PORT} par défaut)")
    parser.add_argument("--shell", help="terminal à brancher (sinon celui de l'ordinateur)")
    parser.add_argument("--dossier", help="dossier des fichiers du parcours Python (~/Kenobi/python par défaut ; les autres cours vont à côté)")
    parser.add_argument("--sans-navigateur", action="store_true", help="ne pas ouvrir Kenobi")
    parser.add_argument("--brancher", action="store_true", help="ouvrir Kenobi pour brancher ce navigateur")
    parser.add_argument("--nouveau-jeton", action="store_true", help="oublier les navigateurs déjà branchés")
    parser.add_argument("--service", action="store_true", help="mode service (ordinateur qui fait tourner server.py) : sans message ni navigateur")
    return parser.parse_args(argv)


def _replace_unprintable():
    """Une console qui ne sait pas écrire un caractère le remplace au lieu d'arrêter le pont."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


def _pause_on_windows():
    """Lancé d'un double-clic, Windows fermerait la fenêtre avant qu'on ait pu lire le message."""
    if WINDOWS and sys.stdin and sys.stdin.isatty():
        try:
            input("Appuie sur Entrée pour fermer…")
        except (EOFError, KeyboardInterrupt):
            pass


def _can_open_browser():
    # Sans écran (connexion SSH), Python ouvrirait un navigateur en mode texte dans cette fenêtre.
    return WINDOWS or sys.platform == "darwin" or bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _firefox_command():
    """Commande qui ouvre Firefox, s'il est installé (sinon None)."""
    if WINDOWS:
        for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), os.environ.get("LOCALAPPDATA")):
            path = Path(base) / "Mozilla Firefox" / "firefox.exe" if base else None
            if path and path.is_file():
                return [str(path)]
        return None
    if sys.platform == "darwin":
        apps = (Path("/Applications/Firefox.app"), Path.home() / "Applications" / "Firefox.app")
        return ["open", "-a", "Firefox"] if any(app.is_dir() for app in apps) else None
    path = shutil.which("firefox") or shutil.which("firefox-esr")
    return [path] if path else None


def _open_browser(url):
    """Ouvre Kenobi dans Firefox s'il est là (Chrome et Edge refusent qu'une page du réseau joigne le pont), sinon
    dans le navigateur par défaut. Renvoie le nom de ce qui a été ouvert, ou une chaîne vide."""
    command = _firefox_command()
    if command:
        try:
            options = {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)} if WINDOWS else {"start_new_session": True}
            subprocess.Popen(command + [url], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             **options)
            return "Firefox"
        except OSError:
            pass
    try:
        return "ton navigateur" if webbrowser.open(url) else ""
    except Exception:
        return ""


def _newer_copy():
    """Copie plus récente du pont téléchargée à côté de celle-ci (« kenobi-terminal(1).py »…), ou None."""
    try:
        here = Path(__file__).resolve()
        if not here.name.startswith("kenobi-terminal"):
            return None
        mine = here.stat().st_mtime
        copies = [(path.stat().st_mtime, path) for path in here.parent.glob("kenobi-terminal*.py")
                  if path != here and path.is_file()]
    except (NameError, OSError):
        return None
    newer = [item for item in copies if item[0] > mine + 1]
    return max(newer)[1] if newer else None


def _bridge_on(port):
    """Ce que répond le pont Kenobi qui occupe déjà ce port de cet ordinateur, ou None."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}/health", timeout=1) as response:
            data = json.loads(response.read(4096).decode("utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) and data.get("ok") and "shell" in data else None


def main(argv=None):
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    _replace_unprintable()
    if args.shell:
        os.environ["KENOBI_TERMINAL_SHELL"] = args.shell
    if WINDOWS and not _ensure_windows_pty():
        _pause_on_windows()
        return 1

    settings = {} if args.service else load_settings()
    origins = list(DEFAULT_ORIGINS) + os.environ.get("KENOBI_TERMINAL_ORIGINS", "").split(",")
    remembered = [origin for origin in settings.get("origins", []) if normalize_origin(origin)]
    kenobi, page = kenobi_page(args.kenobi) if args.kenobi else kenobi_page(EMBEDDED_KENOBI_URL)
    if args.kenobi and not kenobi:
        print(f"Adresse de Kenobi illisible : {args.kenobi}")
        return 2
    new_origin = bool(kenobi and kenobi not in remembered)
    if new_origin:
        remembered.append(kenobi)
        settings["origins"] = remembered
        if not args.service:  # Le service ne lit pas les réglages : il ne doit pas les écraser.
            try:
                save_settings(settings)
            except OSError as error:
                print(f"Adresse de Kenobi non retenue ({error}) : le pont marche quand même.")
    if kenobi and page != kenobi + "/" and not args.service:
        settings.setdefault("pages", {})[kenobi] = page
        try:
            save_settings(settings)
        except OSError:
            pass
    kenobi = kenobi or (remembered[-1] if remembered else None)
    page = page or (settings.get("pages", {}).get(kenobi) if kenobi else None) or (kenobi + "/" if kenobi else None)

    port = args.port if args.port is not None else configured_port()
    bridge = TerminalBridge(
        port=port,
        workspace=args.dossier,
        origins=origins + remembered,
        spare_ports=not args.service and args.port is None and "KENOBI_TERMINAL_PORT" not in os.environ,
        service=args.service,
        announce=not args.service,
    )
    try:
        bridge.load_or_create_token(renew=args.nouveau_jeton)
    except OSError as error:
        print(f"Le pont n'a pas pu enregistrer son code dans {pretty_path(bridge.token_file)} : {error}")
        _pause_on_windows()
        return 1

    async def run():
        await bridge.start()
        if args.service:
            print(f"Pont du terminal Kenobi {VERSION} prêt sur {bridge.host}:{bridge.port} · shell {bridge.shell} · "
                  f"dossier {pretty_path(bridge.workspace, bridge.home)}", flush=True)
        else:
            _announce(bridge, kenobi, args, open_browser=bridge.token_created or new_origin or args.brancher, page=page)
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(signum, stop.set)
            except (NotImplementedError, RuntimeError):
                pass
        try:
            await stop.wait()
        finally:
            await bridge.close()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
    except OSError as error:
        print(f"Le pont n'a pas pu démarrer : {error}")
        if bridge.busy_ports:
            print("Un pont tourne sans doute déjà dans une autre fenêtre : utilise-la, ou ferme-la puis relance celui-ci.")
        _pause_on_windows()
        return 1
    if not args.service:
        print("Pont arrêté : le terminal est débranché.")
    return 0


def _announce(bridge, kenobi, args, open_browser, page=None):
    system = platform.system()
    python = bridge.python_version or "Python introuvable dans le terminal"
    print(f"Kenobi · pont du terminal {VERSION}", flush=True)
    print(f"  Ordinateur : {bridge.hostname} ({OS_LABELS.get(system, system)}) · "
          f"terminal : {SHELL_LABELS.get(bridge.shell_kind, bridge.shell_kind)} · {python}")
    print(f"  Dossier de travail : {pretty_path(bridge.workspace, bridge.home)}")
    if kenobi:
        print(f"  Kenobi : {page or kenobi}")
    newer = _newer_copy()
    if newer:
        print()
        print(f"Attention : une copie plus récente du pont est à côté de celle-ci ({newer.name}).")
        print("  Ferme cette fenêtre (Ctrl+C) et lance la plus récente : la commande donnée par Kenobi la choisit seule.")
    for port in bridge.busy_ports:
        other = _bridge_on(port)
        if other:
            where = "le service de cet ordinateur" if other.get("service") else "une autre fenêtre"
            print(f"  Un autre pont Kenobi ({other.get('version', '?')}) occupe déjà le port {port} ({where}) : "
                  f"celui-ci prend le port {bridge.port}.")
    print()
    print(f"Pont prêt sur {bridge.host}:{bridge.port} : il n'écoute que cet ordinateur.")
    print("Garde cette fenêtre ouverte pendant que tu codes ; ferme-la (ou Ctrl+C) pour débrancher le terminal.")
    link = f"{page or kenobi + '/'}#terminal={bridge.pairing_code}" if kenobi else ""
    opened = _open_browser(link) if link and open_browser and not args.sans_navigateur and _can_open_browser() else ""
    if opened:
        print(f"Kenobi s'ouvre dans {opened}, déjà branché.")
    elif link:
        print(f"Lien qui ouvre Kenobi déjà branché : {link}")
    print()
    print("Code à coller dans Kenobi (Terminal › Brancher), si la page le demande :")
    print(f"  {bridge.pairing_code}", flush=True)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception:  # noqa: BLE001 - dernier filet : le message doit rester lisible
        traceback.print_exc()
        _pause_on_windows()
        sys.exit(1)
