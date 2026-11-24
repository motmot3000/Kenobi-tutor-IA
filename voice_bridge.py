#!/usr/bin/env python3
"""Kenobi voice bridge: Whisper to hear and Piper to speak, on this computer, for Kenobi in the browser.

Downloaded from Kenobi as kenobi-voice.py, then started with `python3 kenobi-voice.py` (`py kenobi-voice.py` on
Windows). One file, three roles:

- install (first start, or --installer): the virtual environment, pinned packages, models and manifest.json of
  scripts/installer_voix.py, in the folder voice.py uses: either one reuses the other's installation;
- server (default): a subset of the OpenAI audio API on 127.0.0.1:8196, standard library only;
- worker (--worker): started by the server with the voice's own Python; Piper and faster-whisper behind JSON lines
  on stdin and stdout, as voice_worker.py.

Security: loopback only, Host header checked (DNS rebinding), bearer token on the /v1/ routes, CORS for Kenobi's
pages only. Audio and text are never logged nor kept. Python 3.8 or later (3.9 or later to install).
"""
import argparse
import collections
import contextlib
import hmac
import io
import json
import os
import platform
import queue
import re
import secrets
import shutil
import signal
import socket
import socketserver
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.request
import wave
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

WINDOWS = os.name == "nt"
APP = "kenobi-voice"
VERSION = "1.0.0"
PROTOCOL = 1
# Set by Kenobi when the bridge is downloaded: the address of the app's page, path included
# (https://example.github.io/kenobi/), or a bare origin (http://127.0.0.1:8192).
EMBEDDED_KENOBI_URL = ""
HOST = "127.0.0.1"
DEFAULT_PORT = 8196
SPARE_PORTS = range(8197, 8200)
DEFAULT_ORIGINS = ("http://127.0.0.1:8192", "http://localhost:8192")
TOKEN_RE = re.compile(r"[0-9a-f]{48}")
ORIGIN_RE = re.compile(r"https?://(?:[a-z0-9.-]+|\[[0-9a-f:]+\])(?::\d{1,5})?", re.IGNORECASE)
PAGE_PATH_RE = re.compile(r"(?:/[A-Za-z0-9._~%!$&'()*+,;=:@/-]*)?")
OS_LABELS = {"Darwin": "macOS", "Windows": "Windows", "Linux": "Linux"}

# Installation: the same as scripts/installer_voix.py (tests/test_voice_bridge.py compares them).
PACKAGES = ("piper-tts==1.8.0", "faster-whisper==1.2.1", "av==15.1.0", "huggingface-hub==1.33.0")
VOICE = "fr_FR-upmc-medium"
VOICE_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/fr/fr_FR/upmc/medium/"
VOICE_FILES = ((VOICE + ".onnx", 76_733_615), (VOICE + ".onnx.json", None))
WHISPER_MODELS = ("base", "small", "medium", "large-v3-turbo")
DEFAULT_WHISPER = "small"
TEST_SENTENCE = "Bonjour, je suis Kenobi, ton tuteur."
SPEAKERS = ("pierre", "jessica")
# Approximate download sizes in MB, shown before installing.
PACKAGES_MB = 500
VOICE_MB = 77
WHISPER_MB = {"base": 145, "small": 484, "medium": 1530, "large-v3-turbo": 1620}
HEARTBEAT_SECONDS = 30

# HTTP API (audio limits as in voice.py).
MAX_AUDIO = 8 * 1024 * 1024
MAX_SECONDS = 60
MAX_TEXT = 4000
MAX_PROMPT = 400
MAX_BODY = MAX_AUDIO + 256 * 1024
MAX_JSON = 64 * 1024
MAX_PARTS = 16
DRAIN_LIMIT = 32 * 1024 * 1024
SOCKET_SECONDS = 30
BODY_SECONDS = 60
LANGUAGE_RE = re.compile(r"[a-z]{2,3}")
BOUNDARY_RE = re.compile(r'(?:^|;)\s*boundary\s*=\s*(?:"([^"]{1,70})"|([^\s;"]{1,70}))', re.IGNORECASE)
MODELS = ("whisper", "piper")

# Worker process, as in voice.py: started on the first request, stopped after 15 idle minutes.
IDLE_SECONDS = 15 * 60
START_SECONDS = 120
REQUEST_SECONDS = 90
BUSY_SECONDS = 120
MAX_REPLY_LINE = 65536

# Worker role: the same values as voice_worker.py.
WORKER_MAX_SECONDS = 120
EDGE_MS = 50
SILENCE_FRACTION = 0.02
PEAK_TARGET = 30000
STT_RATE = 16000


class VoiceBridgeError(Exception):
    """Refused or failed request: `message` (French) goes to the page as is, `kind` is the OpenAI error type."""

    def __init__(self, message, status=500, kind="server_error", headers=None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.kind = kind
        self.headers = headers or {}


def bad_request(message, status=400, headers=None):
    return VoiceBridgeError(message, status, "invalid_request_error", headers)


def unavailable(message, status=503):
    return VoiceBridgeError(message, status, "unavailable")


class Failure(Exception):
    """Installation step that failed; the message says what to do."""


# ── Folders, token and origins ──────────────────────────────────────────────


def voice_dir():
    """Folder of the local voice: the same as voice.py and scripts/installer_voix.py."""
    override = os.environ.get("KENOBI_VOICE_DIR")
    if override:
        return Path(os.path.expanduser(override))
    if WINDOWS:
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "kenobi" / "voice"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "kenobi" / "voice"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "kenobi" / "voice"


def config_dir():
    """Folder of the bridge's own settings: its token."""
    explicit = os.environ.get("KENOBI_VOICE_HOME")
    if explicit:
        return Path(os.path.expanduser(explicit))
    if WINDOWS:
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "kenobi-voice"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "kenobi-voice"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "kenobi-voice"


def token_path():
    return config_dir() / "token"


def _private_file(path):
    try:
        info = os.lstat(path)
    except OSError:
        return False
    if not path.is_file() or path.is_symlink():
        return False
    return WINDOWS or (info.st_uid == os.getuid() and not info.st_mode & 0o077)


def read_token(path=None):
    """The bridge's token, or None if the file is missing or not private (0600, same owner)."""
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


def normalize_origin(value):
    text = str(value or "").strip().rstrip("/")
    return text.lower() if ORIGIN_RE.fullmatch(text) else None


def kenobi_page(value):
    """(origin, page) of the address of Kenobi's page, or None if it is not a plain http(s) address.

    https://Example.github.io/Kenobi-tutor-IA/index.html gives ("https://example.github.io",
    "https://example.github.io/Kenobi-tutor-IA/"): the origin, lower-cased and without a default port, is what
    browsers send in Origin; the page keeps its path, ends with "/" and loses a file name. Credentials, query and
    fragment are refused.
    """
    text = str(value or "").strip()
    if not text or re.search(r"[\s\x00-\x1f\x7f?#\\]", text):
        return None
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https") or not parts.hostname or "@" in parts.netloc:
        return None
    host = parts.netloc.lower().rsplit(":", 1)[0] if port is not None else parts.netloc.lower()
    default = 80 if scheme == "http" else 443
    origin = normalize_origin(f"{scheme}://{host}" + (f":{port}" if port not in (None, default) else ""))
    if not origin or not PAGE_PATH_RE.fullmatch(parts.path):
        return None
    path = parts.path or "/"
    if re.search(r"\.html?$", path, re.IGNORECASE):
        path = path[:path.rfind("/") + 1]
    elif not path.endswith("/"):
        path += "/"
    return origin, origin + path


def allowed_origins(kenobi=None):
    """Pages allowed to use the bridge: Kenobi on this computer, the page that downloaded the bridge, --kenobi and
    KENOBI_VOICE_ORIGINS (comma separated)."""
    values = list(DEFAULT_ORIGINS) + [EMBEDDED_KENOBI_URL, kenobi or ""]
    values += os.environ.get("KENOBI_VOICE_ORIGINS", "").split(",")
    origins = []
    for value in values:
        page = kenobi_page(value)
        if page and page[0] not in origins:
            origins.append(page[0])
    return origins


# ── Installed voice ─────────────────────────────────────────────────────────


def venv_python(root):
    return Path(root) / "venv" / ("Scripts/python.exe" if WINDOWS else "bin/python")


def load_manifest(root):
    """(manifest, None) when the voice installed in `root` is complete, else (None, what is missing, in French)."""
    root = Path(root)
    path = root / "manifest.json"
    if not path.is_file():
        return None, f"La voix locale n'est pas encore installée sur cet ordinateur ({pretty_path(root)})."
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        python = Path(data["python"])
        voice_file = root / data["piper"]["file"]
        whisper_root = root / data["whisper"]["root"]
        model = str(data["whisper"].get("model") or DEFAULT_WHISPER)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None, "Le fichier manifest.json de la voix est illisible : la voix est à réinstaller."
    if not python.is_file():
        return None, f"L'environnement Python de la voix manque ({pretty_path(python)})."
    if not voice_file.is_file():
        return None, f"La voix de Piper manque ({pretty_path(voice_file)})."
    if not whisper_root.is_dir():
        return None, f"Le modèle de Whisper manque ({pretty_path(whisper_root)})."
    return dict(data, _python=python, _voice=voice_file, _whisper_root=whisper_root, _model=model), None


def installed_model(root):
    """Whisper model named in the manifest, even when the installation is incomplete."""
    try:
        model = json.loads((Path(root) / "manifest.json").read_text(encoding="utf-8"))["whisper"]["model"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return model if model in WHISPER_MODELS else None


def worker_command(python, voice_file, model, whisper_root):
    """The worker role: this very file, run with the voice's Python."""
    return [str(python), str(Path(__file__).resolve()), "--worker", "--voice", str(voice_file),
            "--whisper", str(model), "--whisper-root", str(whisper_root)]


def _own_group():
    """Popen options that keep Ctrl+C for the bridge, which then stops the worker itself."""
    if WINDOWS:
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}


class Worker:
    """A worker process: one JSON request per line on its stdin, one JSON reply per line on its stdout."""

    def __init__(self, command):
        self.command = [str(part) for part in command]
        self.process = None
        self.replies = queue.Queue()
        # Only what the worker says while loading: once ready, its stderr is read and dropped (no content kept).
        self.errors = collections.deque(maxlen=4)
        self.collect_errors = True
        self.ready = {}

    @property
    def alive(self):
        return self.process is not None and self.process.poll() is None

    def exit_code(self, wait=0.0):
        """Exit code of a stopped worker (waiting at most `wait` seconds for it to end), else None."""
        if self.process is None:
            return None
        try:
            return self.process.wait(timeout=wait) if wait else self.process.poll()
        except subprocess.TimeoutExpired:
            return None

    def start(self, timeout=START_SECONDS):
        try:
            self.process = subprocess.Popen(
                self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                encoding="utf-8", errors="replace", **_own_group())
        except OSError:
            raise unavailable(f"Impossible de lancer la voix ({self.command[0]}) : relance kenobi-voice.py avec "
                              "--installer pour la réparer.") from None
        process = self.process
        threading.Thread(target=self._read_replies, args=(process,), daemon=True).start()
        errors = threading.Thread(target=self._read_errors, args=(process,), daemon=True)
        errors.start()
        try:
            ready = self.receive(timeout)
        except VoiceBridgeError as error:
            if error.status == 504:
                self.stop()
                raise unavailable("La voix met trop de temps à démarrer (plus de 2 minutes).", 504) from None
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            errors.join(2)
            self.stop()
            detail = f" ({self.errors[-1][:200]})" if self.errors else ""
            raise unavailable(f"La voix n'a pas démarré{detail}. Relance kenobi-voice.py avec --installer pour la "
                              "réparer.") from None
        self.collect_errors = False
        self.errors.clear()
        if not ready.get("ready"):
            self.stop()
            raise unavailable("La voix n'a pas démarré : réponse inattendue.")
        self.ready = ready
        return ready

    def request(self, payload, timeout=REQUEST_SECONDS):
        if not self.alive:
            raise unavailable("La voix locale s'est arrêtée : elle redémarre à la prochaine demande.")
        try:
            self.process.stdin.write(json.dumps(payload) + "\n")
            self.process.stdin.flush()
        except (OSError, ValueError, AttributeError):
            raise unavailable("La voix locale s'est arrêtée : elle redémarre à la prochaine demande.") from None
        return self.receive(timeout)

    def receive(self, timeout):
        try:
            reply = self.replies.get(timeout=timeout)
        except queue.Empty:
            raise unavailable("La voix locale ne répond pas : elle redémarre à la prochaine demande.", 504) from None
        if reply is None:
            raise unavailable("La voix locale s'est arrêtée : elle redémarre à la prochaine demande.")
        return reply

    def _read_replies(self, process):
        try:
            for line in process.stdout:
                if len(line) > MAX_REPLY_LINE:
                    break
                try:
                    reply = json.loads(line)
                except ValueError:
                    continue  # a library writing on stdout: not a reply
                if isinstance(reply, dict):
                    self.replies.put(reply)
        except (OSError, ValueError):
            pass
        finally:
            self.replies.put(None)

    def _read_errors(self, process):
        try:
            for line in process.stderr:
                if self.collect_errors and line.strip():
                    self.errors.append(line.strip()[:300])
        except (OSError, ValueError):
            pass

    def stop(self):
        process, self.process = self.process, None
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    pass
        for stream in (process.stdin, process.stdout, process.stderr):
            try:
                if stream:
                    stream.close()
            except OSError:
                pass


class LocalEngine:
    """Piper and faster-whisper in a worker process: started on the first request, stopped after 15 idle
    minutes, started again on the next request after a crash.

    The server uses any engine with this interface (the tests inject a fake one): info() -> dict,
    transcribe(wav_bytes, language, prompt) -> text, speak(text, voice) -> WAV bytes, close(). Errors are
    VoiceBridgeError. `command` replaces the installed worker (tests).
    """

    def __init__(self, root=None, command=None, idle_seconds=IDLE_SECONDS, say=None):
        self.root = Path(root) if root else voice_dir()
        self.command = list(command) if command else None
        self.idle_seconds = idle_seconds
        self.say = say or (lambda message: None)
        self.lock = threading.Lock()
        self.worker = None
        self.last_used = 0.0
        self.closed = False

    def info(self):
        manifest, _problem = load_manifest(self.root)
        piper = manifest.get("piper") if manifest else None
        return {
            "ready": bool(manifest) or self.command is not None,
            "voice": (piper.get("voice") if isinstance(piper, dict) else None) or VOICE,
            "speakers": list(SPEAKERS),
            "stt": manifest["_model"] if manifest else "",
        }

    def transcribe(self, audio, language="fr", prompt=""):
        with tempfile.TemporaryDirectory(prefix="kenobi-voix-") as folder:
            path = Path(folder) / "phrase.wav"
            path.write_bytes(audio)
            reply = self._request({"op": "stt", "path": str(path), "language": language or "fr",
                                   "prompt": (prompt or "")[:MAX_PROMPT]})
        if not reply.get("ok"):
            error = str(reply.get("error") or "")
            if error == "unsupported_audio":
                raise bad_request("Format audio non pris en charge : WAV 16 bits, mono ou stéréo, 8 kHz ou plus.")
            if "language" in error:
                raise bad_request(f"Langue inconnue de Whisper : {language}.")
            raise VoiceBridgeError("La transcription a échoué.", 500)
        return str(reply.get("text") or "").strip()

    def speak(self, text, voice=SPEAKERS[0]):
        with tempfile.TemporaryDirectory(prefix="kenobi-voix-") as folder:
            path = Path(folder) / "phrase.wav"
            reply = self._request({"op": "tts", "text": text[:MAX_TEXT], "path": str(path), "speaker": voice})
            if not reply.get("ok"):
                if reply.get("error") in ("invalid_text", "empty_synthesis"):
                    raise bad_request("Rien à dire dans ce texte.")
                raise VoiceBridgeError("La synthèse de la voix a échoué.", 500)
            try:
                return path.read_bytes()
            except OSError:
                raise VoiceBridgeError("La synthèse de la voix n'a rien produit.", 500) from None

    def close(self):
        acquired = self.lock.acquire(timeout=1)
        try:
            self.closed = True
            self._stop_worker()
        finally:
            if acquired:
                self.lock.release()

    def _command(self):
        if self.command:
            return self.command
        manifest, problem = load_manifest(self.root)
        if not manifest:
            raise unavailable(f"{problem} Relance kenobi-voice.py pour l'installer.")
        return worker_command(manifest["_python"], manifest["_voice"], manifest["_model"], manifest["_whisper_root"])

    def _request(self, payload, timeout=REQUEST_SECONDS):
        if not self.lock.acquire(timeout=BUSY_SECONDS):
            raise unavailable("La voix est occupée par une autre demande : réessaie dans un instant.")
        try:
            worker = self._ensure_worker()
            self.last_used = time.monotonic()
            try:
                return worker.request(payload, timeout)
            except VoiceBridgeError as error:
                code = worker.exit_code(wait=0 if error.status == 504 else 1)
                self._stop_worker()
                if self.closed:
                    pass  # stopped by close(): not a crash
                elif error.status == 504:
                    self.say("La voix ne répondait plus : elle est arrêtée et redémarrera à la prochaine demande.")
                else:
                    detail = f" (code {code})" if code is not None else ""
                    self.say(f"La voix s'est arrêtée pendant une demande{detail} : elle redémarrera à la prochaine.")
                raise
            finally:
                self.last_used = time.monotonic()
        finally:
            self.lock.release()

    def _ensure_worker(self):
        if self.closed:
            raise unavailable("Le pont de la voix s'arrête.")
        if self.worker is not None and self.worker.alive:
            return self.worker
        if self.worker is not None:
            code = self.worker.exit_code()
            self._stop_worker()
            self.say(f"La voix s'était arrêtée (code {code}) : elle redémarre.")
        command = self._command()
        self.say("Démarrage de Piper et Whisper…")
        begin = time.monotonic()
        worker = Worker(command)
        try:
            worker.start()
        except VoiceBridgeError as error:
            self.say(error.message)
            raise
        self.worker = worker
        self.say(f"Voix prête en {_decimal(time.monotonic() - begin)} s.")
        threading.Thread(target=self._watch_idle, args=(worker,), daemon=True).start()
        return worker

    def _watch_idle(self, worker):
        interval = max(0.05, min(30.0, self.idle_seconds / 4))
        while True:
            time.sleep(interval)
            with self.lock:
                if self.worker is not worker or not worker.alive:
                    return
                if time.monotonic() - self.last_used > self.idle_seconds:
                    self.say(f"Voix au repos après {round(self.idle_seconds / 60)} min sans demande : elle redémarrera "
                             "à la prochaine.")
                    self._stop_worker()
                    return

    def _stop_worker(self):
        worker, self.worker = self.worker, None
        if worker is not None:
            worker.stop()


# ── Requests: multipart, WAV, language ──────────────────────────────────────


def _param(header, name):
    pattern = r'(?:^|;)\s*' + re.escape(name) + r'\s*=\s*(?:"((?:[^"\\]|\\.)*)"|([^\s;"]*))'
    match = re.search(pattern, header, re.IGNORECASE)
    if not match:
        return None
    if match.group(1) is not None:
        return re.sub(r"\\(.)", r"\1", match.group(1))
    return match.group(2)


def _disposition(head):
    for line in head.decode("utf-8", "replace").split("\r\n"):
        key, _, value = line.partition(":")
        if key.strip().lower() == "content-disposition":
            return _param(value, "name"), _param(value, "filename")
    return None, None


def _next_delimiter(body, delimiter, start):
    """Index of the CRLF that opens the next delimiter line, or -1. A delimiter is followed by "--" or by an end
    of line: the same bytes followed by anything else belong to the data."""
    marker = b"\r\n" + delimiter
    index = body.find(marker, start)
    while index != -1:
        after = index + len(marker)
        line_end = body.find(b"\r\n", after)
        if body.startswith(b"--", after) or (line_end != -1 and not body[after:line_end].strip(b" \t")):
            return index
        index = body.find(marker, index + 1)
    return -1


def parse_multipart(body, content_type):
    """Parts of a multipart/form-data body: {name: (filename or None, data)}; the first part of a name wins."""
    kind, _, params = str(content_type or "").partition(";")
    if kind.strip().lower() != "multipart/form-data":
        raise bad_request("Envoie l'enregistrement en multipart/form-data, dans le champ « file ».")
    match = BOUNDARY_RE.search(params)
    if not match:
        raise bad_request("Envoi multipart sans délimiteur (boundary).")
    delimiter = b"--" + (match.group(1) or match.group(2)).encode("latin-1")
    broken = bad_request("Envoi multipart illisible.")
    if body.startswith(delimiter):
        position = len(delimiter)
    else:
        start = _next_delimiter(body, delimiter, 0)
        if start == -1:
            raise broken
        position = start + 2 + len(delimiter)
    parts = {}
    count = 0
    while not body.startswith(b"--", position):
        line_end = body.find(b"\r\n", position)
        if line_end == -1 or body[position:line_end].strip(b" \t"):
            raise broken
        head_start = line_end + 2
        end = _next_delimiter(body, delimiter, head_start)
        if end == -1:
            raise broken
        if body.startswith(b"\r\n", head_start):
            head, data_start = b"", head_start + 2
        else:
            head_end = body.find(b"\r\n\r\n", head_start, end + 2)
            if head_end == -1:
                raise broken
            head, data_start = body[head_start:head_end], head_end + 4
        count += 1
        if count > MAX_PARTS:
            raise bad_request("Trop de champs dans l'envoi.")
        name, filename = _disposition(head)
        if name is not None and name not in parts:
            parts[name] = (filename, body[data_start:end])
        position = end + 2 + len(delimiter)
    return parts


def _text(parts, name):
    item = parts.get(name)
    return item[1].decode("utf-8", "replace").strip() if item else ""


def check_wav(audio):
    """Duration in seconds of a 16-bit PCM WAV recording (mono or stereo, 8 kHz or more), or VoiceBridgeError."""
    if not audio:
        raise bad_request("Enregistrement vide.")
    if len(audio) > MAX_AUDIO:
        raise bad_request("Enregistrement trop volumineux (8 Mio au plus).", 413)
    try:
        with wave.open(io.BytesIO(audio), "rb") as source:
            width, channels = source.getsampwidth(), source.getnchannels()
            rate, frames = source.getframerate(), source.getnframes()
    except Exception:  # wave raises several kinds of errors on a damaged file
        raise bad_request("Enregistrement illisible : envoie un fichier WAV.") from None
    if width != 2 or channels not in (1, 2) or rate < 8000:
        raise bad_request("Format audio non pris en charge : WAV 16 bits, mono ou stéréo, 8 kHz ou plus.")
    if frames <= 0:
        raise bad_request("Enregistrement vide.")
    seconds = frames / rate
    if seconds > MAX_SECONDS:
        raise bad_request("Phrase trop longue (60 secondes au plus).", 413)
    return seconds


def parse_language(value):
    """Whisper language code: fr by default, fr-CH gives fr, auto lets Whisper detect it."""
    text = str(value or "").strip().lower().replace("_", "-")
    if not text:
        return "fr"
    if text == "auto":
        return text
    text = text.split("-", 1)[0]
    if not LANGUAGE_RE.fullmatch(text):
        raise bad_request("Langue illisible : donne un code comme fr ou en.")
    return text


# ── HTTP server ─────────────────────────────────────────────────────────────


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    allow_reuse_address = not WINDOWS  # on Windows it would let a second program share the port

    def server_bind(self):
        if WINDOWS and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        socketserver.TCPServer.server_bind(self)  # HTTPServer's version also looks up a host name: slow, useless
        self.server_name, self.server_port = self.server_address[:2]

    def handle_error(self, request, client_address):
        pass  # a client that hangs up is not worth a traceback


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = f"{APP}/{VERSION}"
    sys_version = ""
    timeout = SOCKET_SECONDS

    def log_message(self, format, *args):  # noqa: A002 - signature of the base class
        pass  # nothing about the requests is written anywhere

    def do_GET(self):
        self._handle()

    def do_POST(self):
        self._handle()

    def do_OPTIONS(self):
        self._handle()

    def _handle(self):
        self.cors = {}
        self.body_read = self.command != "POST"
        self.close_connection = True
        try:
            self._route(self.server.bridge)
        except VoiceBridgeError as error:
            self._fail(error)
        except (ConnectionError, socket.timeout):
            pass
        except Exception:  # noqa: BLE001 - the page gets a clean error, never a traceback
            self._fail(VoiceBridgeError("Erreur inattendue du pont de la voix.", 500))

    def _route(self, bridge):
        path = urlsplit(self.path).path
        if not bridge.host_allowed(self.headers.get("Host")):
            raise VoiceBridgeError("Hôte refusé : le pont de la voix ne répond qu'à 127.0.0.1.", 403, "forbidden")
        origin = self.headers.get("Origin")
        if origin is not None:
            allowed = bridge.origin_allowed(origin)
            if not allowed:
                bridge.refused("origin", origin)
                raise VoiceBridgeError("Page refusée : le pont de la voix ne sert que Kenobi.", 403, "forbidden")
            self.cors = {"Access-Control-Allow-Origin": allowed}
        if self.command == "OPTIONS":
            return self._preflight()
        if path == "/health":
            self._only("GET")
            return self._json(200, bridge.health())
        if not path.startswith("/v1/"):
            raise bad_request("Route inconnue.", 404)
        if not bridge.token_ok(self.headers.get("Authorization")):
            bridge.refused("token")
            raise VoiceBridgeError("Code du pont absent ou faux : colle dans Kenobi le code qu'affiche la fenêtre "
                                   "de la voix.", 401, "auth", {"WWW-Authenticate": f'Bearer realm="{APP}"'})
        bridge.first_use(origin)
        if path == "/v1/models":
            self._only("GET")
            models = [{"id": name, "object": "model", "created": 0, "owned_by": APP} for name in MODELS]
            return self._json(200, {"object": "list", "data": models})
        if path == "/v1/audio/transcriptions":
            self._only("POST")
            return self._transcription(bridge.engine)
        if path == "/v1/audio/speech":
            self._only("POST")
            return self._speech(bridge.engine)
        raise bad_request("Route inconnue.", 404)

    def _only(self, method):
        if self.command != method:
            raise bad_request(f"Méthode refusée : {method} seulement.", 405, {"Allow": f"{method}, OPTIONS"})

    def _preflight(self):
        headers = {"Allow": "GET, POST, OPTIONS"}
        if self.cors:
            headers.update({"Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                            "Access-Control-Allow-Headers": "authorization, content-type",
                            "Access-Control-Max-Age": "600"})
            if self.headers.get("Access-Control-Request-Private-Network", "").strip().lower() == "true":
                headers["Access-Control-Allow-Private-Network"] = "true"
        self._send(204, b"", None, headers)

    def _transcription(self, engine):
        body = self._body(MAX_BODY, "Enregistrement trop volumineux (8 Mio au plus).")
        parts = parse_multipart(body, self.headers.get("Content-Type"))
        if "file" not in parts:
            raise bad_request("Il manque l'enregistrement : le champ « file ».")
        audio = parts["file"][1]
        check_wav(audio)
        language = parse_language(_text(parts, "language"))
        prompt = _text(parts, "prompt")[:MAX_PROMPT]
        output = _text(parts, "response_format").lower() or "json"
        if output not in ("json", "text"):
            raise bad_request("Format de réponse non pris en charge : json ou text.")
        text = engine.transcribe(audio, language, prompt)
        if output == "text":
            self._send(200, text.encode("utf-8"), "text/plain; charset=utf-8")
        else:
            self._json(200, {"text": text})

    def _speech(self, engine):
        body = self._body(MAX_JSON, "Demande trop volumineuse.")
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise bad_request("Demande illisible : il faut du JSON.") from None
        if not isinstance(payload, dict):
            raise bad_request("Demande illisible : il faut un objet JSON.")
        text = payload.get("input")
        if not isinstance(text, str):
            raise bad_request("Il manque le texte à dire : le champ « input ».")
        text = " ".join(text.split())
        if not text:
            raise bad_request("Texte vide : rien à dire.")
        if len(text) > MAX_TEXT:
            raise bad_request(f"Texte trop long ({MAX_TEXT} caractères au plus).")
        voice = payload.get("voice")
        voice = SPEAKERS[0] if voice is None else (voice.strip().lower() if isinstance(voice, str) else "")
        if voice not in SPEAKERS:
            raise bad_request("Voix inconnue : pierre ou jessica.")
        output = payload.get("response_format") or "wav"
        if not isinstance(output, str) or output.lower() != "wav":
            raise bad_request("Format audio non pris en charge : wav seulement.")
        self._send(200, engine.speak(text, voice), "audio/wav")

    def _body(self, limit, too_big):
        if self.headers.get("Transfer-Encoding"):
            raise bad_request("Envoi par morceaux non pris en charge : il faut un Content-Length.", 411)
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            raise bad_request("Il manque la taille de l'envoi (Content-Length).", 411) from None
        if length < 0:
            raise bad_request("Taille d'envoi invalide.")
        if length > limit:
            raise bad_request(too_big, 413)
        chunks, remaining = [], length
        deadline = time.monotonic() + BODY_SECONDS
        try:
            while remaining > 0:
                if time.monotonic() > deadline:
                    raise socket.timeout()
                chunk = self.rfile.read(min(remaining, 65536))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
        except OSError:
            raise bad_request("Envoi interrompu ou trop lent.", 408) from None
        finally:
            self.body_read = True
        if remaining:
            raise bad_request("Envoi incomplet.")
        return b"".join(chunks)

    def _drain(self):
        """Read an unread request body, so that the client gets the answer instead of a reset connection."""
        if self.body_read or self.headers.get("Transfer-Encoding"):
            return
        self.body_read = True
        try:
            remaining = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return
        if remaining > DRAIN_LIMIT:
            return
        deadline = time.monotonic() + 10
        try:
            while remaining > 0 and time.monotonic() < deadline:
                chunk = self.rfile.read(min(remaining, 65536))
                if not chunk:
                    return
                remaining -= len(chunk)
        except OSError:
            pass

    def _fail(self, error):
        try:
            self._drain()
            self._json(error.status, {"error": {"message": error.message, "type": error.kind}}, error.headers)
        except (OSError, ValueError):
            pass

    def _json(self, status, value, extra=None):
        self._send(status, json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8",
                   extra)

    def _send(self, status, body, content_type, extra=None):
        self.send_response(status)
        if content_type:
            self.send_header("Content-Type", content_type)
        if status != 204:
            self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Vary", "Origin")
        headers = dict(self.cors)
        headers.update(extra or {})
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header("Connection", "close")
        self.end_headers()
        if body:
            self.wfile.write(body)


class VoiceBridge:
    """The engine behind an HTTP subset of the OpenAI audio API, on the loopback interface only."""

    def __init__(self, engine, *, port=DEFAULT_PORT, origins=(), token_file=None, spare_ports=False, host=HOST,
                 announce=False):
        self.engine = engine
        self.host = host
        self.requested_port = port
        self.spare_ports = spare_ports
        # Window opened by hand: refusals and the first use of each page are written in it.
        self.announce = announce
        self.origins = {origin for origin in (normalize_origin(item) for item in origins) if origin}
        self.token_file = Path(token_file) if token_file else token_path()
        self.token = None
        self.token_created = False
        self._token_stamp = None
        self.port = None
        self.server = None
        self.thread = None
        self.busy_ports = []
        self._lock = threading.Lock()
        self._said = {}
        self._users = set()

    def load_or_create_token(self, renew=False):
        token = None if renew else read_token(self.token_file)
        if not token:
            token = secrets.token_hex(24)
            write_private(self.token_file, token)
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
        """The token in force, read again when it changed: another bridge of this computer may have renewed it."""
        stamp = self._stamp()
        if stamp is not None and stamp != self._token_stamp:
            self._token_stamp = stamp
            value = read_token(self.token_file)
            if value:
                self.token = value
        return self.token

    @property
    def pairing_code(self):
        return f"{self.port}-{self.current_token()}"

    def host_allowed(self, host):
        return (host or "").strip().lower() in {f"127.0.0.1:{self.port}", f"localhost:{self.port}",
                                                 f"[::1]:{self.port}"}

    def origin_allowed(self, origin):
        """The origin to answer to when this page may use the bridge, else None."""
        value = normalize_origin(origin)
        return value if value in self.origins else None

    def token_ok(self, authorization):
        scheme, _, value = (authorization or "").strip().partition(" ")
        expected = self.current_token() or ""
        return (scheme.lower() == "bearer" and bool(expected)
                and hmac.compare_digest(value.strip().encode("utf-8"), expected.encode("utf-8")))

    def health(self):
        info = self.engine.info()
        return {"ok": True, "app": APP, "version": VERSION, "protocol": PROTOCOL, "ready": bool(info.get("ready")),
                "voice": info.get("voice") or VOICE, "speakers": list(info.get("speakers") or SPEAKERS),
                "stt": info.get("stt") or ""}

    def refused(self, reason, origin=None):
        """Explain a refusal in the window, at most once every 20 s per reason: a page may retry in a loop."""
        if not self.announce:
            return
        now = time.monotonic()
        with self._lock:
            if now - self._said.get(reason, -60.0) < 20:
                return
            self._said[reason] = now
        if reason == "token":
            _print("Refusé : une demande avec un code faux ou ancien. Le code à coller dans Kenobi est :")
            _print(f"  {self.pairing_code}")
            return
        shown = normalize_origin(origin)
        if shown:
            _print(f"Refusé : la page {shown} n'est pas Kenobi. Si c'en est bien un, relance ce pont avec : "
                   f"--kenobi {shown}")
        else:
            _print("Refusé : une page à l'adresse illisible.")

    def first_use(self, origin):
        key = normalize_origin(origin) or ""
        with self._lock:
            if key in self._users:
                return
            self._users.add(key)
        if self.announce:
            _print(f"Kenobi est branché ({key})." if key else "Un programme de cet ordinateur utilise la voix.")

    def start(self):
        if not self.token:
            self.load_or_create_token()
        ports = [self.requested_port] + (list(SPARE_PORTS) if self.spare_ports and self.requested_port else [])
        server = None
        for port in ports:
            try:
                server = _Server((self.host, port), _Handler)
            except OSError as error:
                if port != ports[-1]:
                    self.busy_ports.append(port)
                    continue
                if len(ports) > 1:
                    raise OSError(error.errno, f"les ports {ports[0]} à {ports[-1]} sont tous pris") from error
                raise OSError(error.errno, f"le port {port} est pris ({error.strerror or error})") from error
            break
        server.bridge = self
        self.server = server
        self.port = server.server_address[1]
        self.thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.25},
                                       name="kenobi-voice-http", daemon=True)
        self.thread.start()

    def close(self):
        server, self.server = self.server, None
        if server:
            server.shutdown()
        self.engine.close()
        if server:
            server.server_close()


# ── Installation (the steps of scripts/installer_voix.py) ───────────────────


def report(label, percent=None):
    _print(f"[{percent if percent is not None else '··':>3} %] {label}")


def run(arguments, failure, timeout=3600):
    """Run an installation command quietly; a long step says it is still going; a failure shows its last lines."""
    try:
        process = subprocess.Popen([str(item) for item in arguments], stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    except OSError:
        raise Failure(failure) from None
    tail = collections.deque(maxlen=2)

    def read():
        for line in process.stdout:
            if line.strip():
                tail.append(line.strip())

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    started = time.monotonic()
    while True:
        try:
            process.wait(timeout=HEARTBEAT_SECONDS)
            break
        except subprocess.TimeoutExpired:
            elapsed = time.monotonic() - started
            if elapsed > timeout:
                process.kill()
                process.wait()
                raise Failure(failure) from None
            _print(f"      … toujours en cours ({_duration(elapsed)})")
    reader.join(5)
    process.stdout.close()
    if process.returncode != 0:
        raise Failure(f"{failure} {' '.join(tail)[-260:]}".strip())


def create_venv(root, report=report):
    python = venv_python(root)
    if python.is_file():
        return python
    report("Création de l'environnement Python de la voix", 4)
    uv = shutil.which("uv")
    try:
        run([sys.executable, "-m", "venv", str(root / "venv")], "Création de l'environnement impossible.")
    except Failure:
        # Some distributions ship Python without a complete venv module: uv does without it.
        if not uv:
            raise Failure("Création de l'environnement impossible : installe le paquet python3-venv "
                          "(Debian, Ubuntu : sudo apt install python3-venv), ou uv.") from None
        shutil.rmtree(root / "venv", ignore_errors=True)
        run([uv, "venv", str(root / "venv")], "Création de l'environnement impossible avec uv.")
    return python


def install_packages(python, report=report):
    report("Installation de Piper et de Whisper (≈ 500 Mo, quelques minutes)", 10)
    has_pip = subprocess.run([str(python), "-m", "pip", "--version"], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL).returncode == 0
    uv = shutil.which("uv")
    if has_pip:
        run([str(python), "-m", "pip", "install", "--disable-pip-version-check", *PACKAGES],
            "Installation des paquets impossible.")
    elif uv:
        run([uv, "pip", "install", "--python", str(python), *PACKAGES], "Installation des paquets impossible.")
    else:
        raise Failure("Ni pip ni uv dans l'environnement : installe python3-venv, puis relance.")


class _CertificateError(Exception):
    pass


CERTIFICATE_HELP = ("Téléchargement impossible : ce Python ne connaît pas les certificats des sites. Sous macOS, "
                    "ouvre le dossier Python des Applications, lance « Install Certificates.command », puis relance.")


def _fetch(url, target, expected, report, label, start, end, context):
    temporary = target.with_name(target.name + ".part")
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "Kenobi/1.0"})
        with urllib.request.urlopen(request, timeout=60, context=context) as response:
            total = int(response.headers.get("Content-Length") or expected or 0)
            done, last = 0, time.monotonic()
            with open(temporary, "wb") as output:
                while True:
                    block = response.read(1024 * 256)
                    if not block:
                        break
                    output.write(block)
                    done += len(block)
                    if total and time.monotonic() - last > 5:
                        last = time.monotonic()
                        report(f"{label} ({done // 1_000_000} / {total // 1_000_000} Mo)",
                               round(start + (end - start) * done / total))
    except Exception as error:
        temporary.unlink(missing_ok=True)
        if isinstance(getattr(error, "reason", error), ssl.SSLCertVerificationError):
            raise _CertificateError() from None
        raise Failure(f"Téléchargement impossible : {url}") from None
    if expected is not None and temporary.stat().st_size != expected:
        temporary.unlink(missing_ok=True)
        raise Failure("Téléchargement de la voix incomplet : relance l'installation.")
    os.replace(temporary, target)


def _certifi(python):
    """Certificates of the voice's Python (certifi comes with its packages), or None."""
    try:
        result = subprocess.run([str(python), "-c", "import certifi; print(certifi.where())"],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    path = result.stdout.strip()
    return path if result.returncode == 0 and os.path.isfile(path) else None


def download(url, target, expected, report, label, start, end, python=None):
    if target.is_file() and (expected is None or target.stat().st_size == expected):
        return
    try:
        _fetch(url, target, expected, report, label, start, end, None)
    except _CertificateError:
        # python.org's Python on macOS may come without certificates: those of the voice's Python serve instead.
        cafile = _certifi(python) if python else None
        if not cafile:
            raise Failure(CERTIFICATE_HELP) from None
        try:
            _fetch(url, target, expected, report, label, start, end, ssl.create_default_context(cafile=cafile))
        except _CertificateError:
            raise Failure(CERTIFICATE_HELP) from None


def download_whisper(python, root, model, report=report):
    report(f"Téléchargement du modèle de transcription « {model} » (≈ {WHISPER_MB.get(model, '?')} Mo)", 62)
    code = ("import sys\nfrom faster_whisper import WhisperModel\n"
            "WhisperModel(sys.argv[1], device='cpu', compute_type='int8', download_root=sys.argv[2])\n")
    run([str(python), "-c", code, model, str(root / "models" / "whisper")],
        "Téléchargement du modèle de transcription impossible.")


def smoke_test(python, root, model):
    """Say a sentence with Piper and transcribe it with Whisper: the proof that the voice works end to end."""
    wav = root / "essai.wav"
    worker = Worker(worker_command(python, root / "models" / "piper" / (VOICE + ".onnx"), model,
                                   root / "models" / "whisper"))
    try:
        ready = worker.start()
        spoken = worker.request({"op": "tts", "text": TEST_SENTENCE, "path": str(wav), "speaker": "pierre"}, 120)
        heard = worker.request({"op": "stt", "path": str(wav), "language": "fr"}, 120)
        if not spoken.get("ok") or not heard.get("ok") or not heard.get("text"):
            raise Failure("L'essai de la voix a échoué.")
        return {"speakers": sorted(ready.get("speakers") or {}), "tts_seconds": spoken.get("took"),
                "stt_seconds": heard.get("took"), "heard": heard.get("text")}
    except VoiceBridgeError as error:
        raise Failure(error.message) from None
    finally:
        worker.stop()
        wav.unlink(missing_ok=True)


def build_manifest(python, model, result, now=None):
    """manifest.json, field for field as scripts/installer_voix.py writes it."""
    return {
        "version": 1,
        "installedAt": (now or datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z"),
        "python": str(python),
        "packages": list(PACKAGES),
        "piper": {"voice": VOICE, "file": f"models/piper/{VOICE}.onnx", "speakers": result["speakers"]},
        "whisper": {"model": model, "root": "models/whisper"},
        "test": {"tts_seconds": result["tts_seconds"], "stt_seconds": result["stt_seconds"]},
    }


def install_voice(root, model, report=report):
    """Install or complete the voice in `root`, as scripts/installer_voix.py does; Failure says why it stopped."""
    if sys.version_info < (3, 9):
        raise Failure(f"il faut Python 3.9 ou plus récent (ici Python {platform.python_version()}).")
    (root / "models" / "piper").mkdir(parents=True, exist_ok=True)
    (root / "models" / "whisper").mkdir(parents=True, exist_ok=True)
    python = create_venv(root, report)
    install_packages(python, report)
    report(f"Téléchargement de la voix française ({VOICE_MB} Mo)", 48)
    for name, size in VOICE_FILES:
        download(VOICE_URL + name, root / "models" / "piper" / name, size, report, "Voix française", 48, 60, python)
    download_whisper(python, root, model, report)
    report("Essai : dire une phrase et la réécouter", 94)
    result = smoke_test(python, root, model)
    manifest = build_manifest(python, model, result)
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report(f"Voix locale prête dans {pretty_path(root)} (essai entendu : « {result['heard']} »)", 100)


def offer_install(root, model, reason, assume_yes):
    """Say what will be downloaded and where, ask, then install. True when the voice is ready."""
    _print(reason)
    if sys.version_info < (3, 9):
        _print(f"Pour installer la voix, il faut Python 3.9 ou plus récent (ici Python {platform.python_version()}) : "
               "installe-le depuis python.org, puis relance ce fichier avec.")
        return False
    total = PACKAGES_MB + VOICE_MB + WHISPER_MB[model]
    _print("Pour que Kenobi t'entende et te parle sans envoyer ta voix sur Internet, ce programme installe une fois "
           "pour toutes :")
    _print(f"  · Piper (la voix) et faster-whisper (la transcription), dans un environnement Python à part : "
           f"environ {PACKAGES_MB} Mo ;")
    _print(f"  · la voix française {VOICE} (Pierre et Jessica) : {VOICE_MB} Mo, depuis huggingface.co ;")
    _print(f"  · le modèle de transcription Whisper « {model} » : environ {WHISPER_MB[model]} Mo, "
           "depuis huggingface.co.")
    _print(f"Au total, environ {_gigabytes(total)}, dans {pretty_path(root)}")
    _print("Rien d'autre n'est modifié sur l'ordinateur, et ce qui est déjà téléchargé est gardé. Supprimer ce dossier "
           "désinstalle la voix.")
    free = _free_space(root)
    if free is not None and free < total * 1_000_000:
        _print(f"Attention : il ne reste que {_gigabytes(free / 1_000_000)} de libre sur ce disque.")
    if not assume_yes:
        if not sys.stdin or not sys.stdin.isatty():
            _print("Pour installer sans question, relance avec --oui.")
            return False
        try:
            answer = input("Installer la voix maintenant ? [o/N] ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("o", "oui", "y", "yes"):
            _print("Installation annulée : rien n'a été téléchargé.")
            return False
    _print()
    try:
        install_voice(root, model)
    except Failure as failure:
        _print(f"Installation interrompue : {failure}")
        _print("Relance kenobi-voice.py pour reprendre : ce qui est déjà téléchargé est gardé.")
        return False
    except KeyboardInterrupt:
        _print()
        _print("Installation interrompue. Relance kenobi-voice.py pour reprendre : ce qui est déjà téléchargé "
               "est gardé.")
        return False
    except OSError as error:
        _print(f"Installation interrompue par une erreur du disque : {error}")
        return False
    return True


# ── Worker role (runs with the voice's Python; numpy, Piper and faster-whisper come from there) ──────────────

_np = None


def _numpy():
    global _np
    if _np is None:
        import numpy  # noqa: PLC0415 - only in the voice's environment

        _np = numpy
    return _np


def _samples(data):
    np = _numpy()
    return np.frombuffer(data, dtype="<i2").astype(np.int32)


def _pcm(samples):
    np = _numpy()
    return np.clip(np.rint(samples), -32768, 32767).astype("<i2").tobytes()


def pcm_trim(data, rate, margin_ms=EDGE_MS):
    """Keep the useful sound and at most margin_ms of silence at each edge."""
    np = _numpy()
    samples = _samples(data)
    if not samples.size or not samples.any():
        return b""
    loud = np.abs(samples) > max(1, int(np.max(np.abs(samples)) * SILENCE_FRACTION))
    if not loud.any():
        return b""
    first, last = int(np.argmax(loud)), len(loud) - 1 - int(np.argmax(loud[::-1]))
    margin = rate * margin_ms // 1000
    return _pcm(samples[max(first - margin, 0):min(last + margin, len(samples) - 1) + 1])


def pcm_normalize(data, target=PEAK_TARGET):
    np = _numpy()
    samples = _samples(data)
    peak = int(np.max(np.abs(samples))) if samples.size else 0
    return data if peak == 0 else _pcm(samples * (target / peak))


def read_wav(path):
    """Mono float32 samples at 16 kHz, as Whisper expects them."""
    np = _numpy()
    with wave.open(str(path), "rb") as source:
        channels, width = source.getnchannels(), source.getsampwidth()
        rate, frames = source.getframerate(), source.getnframes()
        if width != 2 or channels not in (1, 2) or rate < 8000 or frames <= 0 or frames / rate > WORKER_MAX_SECONDS:
            raise ValueError("unsupported_audio")
        raw = source.readframes(frames)
    audio = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if channels == 2:
        audio = audio.reshape(-1, 2).mean(axis=1)
    if rate != STT_RATE:
        target = int(round(len(audio) * STT_RATE / rate))
        audio = np.interp(np.linspace(0, len(audio) - 1, target), np.arange(len(audio)), audio).astype(np.float32)
    return audio


def worker_main(argv):
    """Worker role: Piper and faster-whisper behind JSON lines on stdin and stdout, as voice_worker.py.

      {"op": "tts", "text": "...", "path": "/tmp/x.wav", "speaker": "pierre"}  → {"ok": true, "seconds": 1.2, ...}
      {"op": "stt", "path": "/tmp/y.wav", "language": "fr", "prompt": "..."}   → {"ok": true, "text": "..."}

    Nothing is kept nor sent elsewhere; the models are read from the disk, never downloaded here.
    """
    started = time.perf_counter()
    parser = argparse.ArgumentParser(prog="kenobi-voice --worker")
    parser.add_argument("--voice", required=True, type=Path, help="fichier .onnx de la voix Piper")
    parser.add_argument("--whisper", required=True, help="dossier ou nom du modèle faster-whisper")
    parser.add_argument("--whisper-root", type=Path, help="dossier des modèles Whisper téléchargés")
    parser.add_argument("--threads", type=int, default=max(2, min(8, (os.cpu_count() or 4) // 2)))
    args = parser.parse_args(argv)

    # Libraries sometimes write on stdout: it is kept for the replies.
    with contextlib.redirect_stdout(sys.stderr):
        import piper  # noqa: PLC0415
        from faster_whisper import WhisperModel  # noqa: PLC0415
        from piper import SynthesisConfig  # noqa: PLC0415

        voice = piper.PiperVoice.load(str(args.voice))
        speakers = dict(voice.config.speaker_id_map or {})
        whisper = WhisperModel(args.whisper, device="cpu", compute_type="int8", cpu_threads=args.threads,
                               download_root=str(args.whisper_root) if args.whisper_root else None,
                               local_files_only=True)
        for _chunk in voice.synthesize("Bonjour.", syn_config=SynthesisConfig(
                speaker_id=sorted(speakers.values())[0] if speakers else None)):
            break

    def speak(request):
        text = " ".join(str(request.get("text") or "").split())
        if not text or len(text) > MAX_TEXT:
            raise ValueError("invalid_text")
        speaker = request.get("speaker")
        speaker_id = speakers.get(speaker) if speakers else None
        if speakers and speaker_id is None:
            speaker_id = sorted(speakers.values())[0]
        scale = float(request.get("length_scale") or 1.0)
        config = SynthesisConfig(speaker_id=speaker_id, length_scale=max(0.8, min(1.25, scale)), normalize_audio=False)
        begin = time.perf_counter()
        rate, pieces = None, []
        with contextlib.redirect_stdout(sys.stderr):
            for chunk in voice.synthesize(text, syn_config=config):
                rate = chunk.sample_rate
                pieces.append(pcm_trim(chunk.audio_int16_bytes, chunk.sample_rate))
        audio = pcm_normalize(b"".join(piece for piece in pieces if piece))
        if not audio or not rate:
            raise ValueError("empty_synthesis")
        with wave.open(str(request["path"]), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(rate)
            out.writeframes(audio)
        return {"sample_rate": rate, "seconds": round(len(audio) / (2 * rate), 3),
                "took": round(time.perf_counter() - begin, 3)}

    def transcribe(request):
        begin = time.perf_counter()
        audio = read_wav(request["path"])
        language = str(request.get("language") or "fr")
        prompt = str(request.get("prompt") or "")[:MAX_PROMPT] or None
        with contextlib.redirect_stdout(sys.stderr):
            segments, _info = whisper.transcribe(
                audio, language=None if language == "auto" else language, beam_size=5, temperature=0.0,
                condition_on_previous_text=False, vad_filter=False, without_timestamps=True, initial_prompt=prompt)
            kept = []
            for segment in segments:
                # A passage the model itself judges without speech is not a sentence.
                if segment.no_speech_prob > 0.6 and segment.avg_logprob < -1.0:
                    continue
                kept.append(segment.text.strip())
        return {"text": " ".join(part for part in kept if part).strip(),
                "audio_seconds": round(len(audio) / STT_RATE, 2), "took": round(time.perf_counter() - begin, 3)}

    print(json.dumps({"ready": True, "speakers": speakers, "sample_rate": voice.config.sample_rate,
                      "threads": args.threads, "load_seconds": round(time.perf_counter() - started, 2)}), flush=True)
    for line in sys.stdin:
        try:
            request = json.loads(line)
            operation = request.get("op")
            if operation == "tts":
                reply = {"ok": True, **speak(request)}
            elif operation == "stt":
                reply = {"ok": True, **transcribe(request)}
            elif operation == "ping":
                reply = {"ok": True}
            else:
                reply = {"ok": False, "error": "unknown_op"}
        except Exception as error:  # the request is refused, the process stays alive
            reply = {"ok": False, "error": error.args[0] if isinstance(error, ValueError) and error.args else "failed"}
        print(json.dumps(reply), flush=True)
    return 0


# ── Command line ────────────────────────────────────────────────────────────


def _print(*values):
    print(*values, flush=True)


def _decimal(value):
    return f"{value:.1f}".replace(".", ",")


def _gigabytes(megabytes):
    return f"{_decimal(megabytes / 1000)} Go"


def _duration(seconds):
    minutes, rest = divmod(int(seconds), 60)
    if not minutes:
        return f"{rest} s"
    return f"{minutes} min {rest} s" if rest else f"{minutes} min"


def _free_space(path):
    path = Path(path)
    while not path.exists() and path != path.parent:
        path = path.parent
    try:
        return shutil.disk_usage(str(path)).free
    except OSError:
        return None


def pretty_path(path, home=None):
    text = str(path)
    home = home or str(Path.home())
    if text == home:
        return "~"
    if text.startswith(home + os.sep):
        return "~" + os.sep + text[len(home) + 1:]
    return text


def _replace_unprintable():
    """A console that cannot write a character replaces it instead of stopping the bridge."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


def _pause_on_windows():
    """Started by a double click, Windows would close the window before the message could be read."""
    if WINDOWS and sys.stdin and sys.stdin.isatty():
        try:
            input("Appuie sur Entrée pour fermer…")
        except (EOFError, KeyboardInterrupt):
            pass


def _can_open_browser():
    # Without a screen (SSH), Python would open a text browser in this very window.
    return WINDOWS or sys.platform == "darwin" or bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _open_browser(url):
    """Open Kenobi in the default browser; True when one was launched."""
    try:
        return bool(webbrowser.open(url))
    except Exception:  # noqa: BLE001 - no browser is not an error
        return False


def _bridge_on(port):
    """What answers /health on this port of this computer, or None."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}/health", timeout=1) as response:
            data = json.loads(response.read(4096).decode("utf-8"))
    except Exception:  # noqa: BLE001 - anything else is "another program"
        return None
    return data if isinstance(data, dict) and data.get("ok") else None


def _interrupt(signum, frame):
    raise KeyboardInterrupt


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="kenobi-voice",
        description="Voix locale de Kenobi : Whisper pour entendre, Piper pour parler, sur cet ordinateur.",
    )
    parser.add_argument("--installer", action="store_true", help="installer la voix, ou compléter et réparer son "
                        "installation")
    parser.add_argument("--oui", "--yes", dest="oui", action="store_true", help="installer sans poser de question")
    parser.add_argument("--whisper", choices=WHISPER_MODELS, help="modèle de transcription : base (léger), small "
                        "(défaut), medium ou large-v3-turbo (plus justes, plus lents)")
    parser.add_argument("--dossier", metavar="DOSSIER",
                        help=f"dossier de la voix ({pretty_path(voice_dir())} par défaut)")
    parser.add_argument("--port", type=int, help=f"port local ({DEFAULT_PORT} par défaut, sinon le suivant libre "
                        f"jusqu'à {SPARE_PORTS[-1]}) ; imposé, il n'est pas remplacé")
    parser.add_argument("--kenobi", metavar="ADRESSE", help="adresse de Kenobi, par exemple "
                        "https://exemple.github.io/kenobi/")
    parser.add_argument("--sans-navigateur", action="store_true", help="ne pas ouvrir Kenobi")
    parser.add_argument("--brancher", action="store_true", help="ouvrir Kenobi pour brancher ce navigateur")
    parser.add_argument("--nouveau-jeton", action="store_true", help="changer de code : les navigateurs déjà "
                        "branchés devront coller le nouveau")
    parser.add_argument("--version", action="version", version=f"{APP} {VERSION}")
    return parser.parse_args(argv)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if "--worker" in argv:
        argv.remove("--worker")
        return worker_main(argv)
    if sys.version_info < (3, 8):
        print("La voix de Kenobi demande Python 3.8 ou plus récent.")
        return 1
    _replace_unprintable()
    args = _parse_args(argv)
    if args.port is not None and not 0 <= args.port <= 65535:
        print(f"Port invalide : {args.port}.")
        return 2
    kenobi = kenobi_page(args.kenobi) if args.kenobi else kenobi_page(EMBEDDED_KENOBI_URL)
    if args.kenobi and not kenobi:
        print(f"Adresse de Kenobi illisible : {args.kenobi} (il faut une adresse http ou https, sans ? ni #).")
        return 2
    if EMBEDDED_KENOBI_URL and not kenobi_page(EMBEDDED_KENOBI_URL):
        print(f"Adresse de Kenobi inscrite dans ce fichier illisible, ignorée : {EMBEDDED_KENOBI_URL!r}")
    root = Path(os.path.abspath(os.path.expanduser(args.dossier))) if args.dossier else voice_dir()

    manifest, problem = load_manifest(root)
    current = manifest["_model"] if manifest else installed_model(root)
    model = args.whisper or current or DEFAULT_WHISPER
    if args.installer or not manifest or model != current:
        if not manifest:
            reason = problem
        elif model != current:
            reason = f"Changement du modèle de transcription : « {current} » devient « {model} »."
        else:
            reason = "Vérification et réparation de la voix locale."
        if not offer_install(root, model, reason, args.oui):
            _pause_on_windows()
            return 1
        manifest, problem = load_manifest(root)
        if not manifest:
            _print(problem)
            _pause_on_windows()
            return 1
        _print()

    engine = LocalEngine(root, say=_print)
    bridge = VoiceBridge(engine, port=DEFAULT_PORT if args.port is None else args.port,
                         origins=allowed_origins(args.kenobi), spare_ports=args.port is None, announce=True)
    try:
        bridge.load_or_create_token(renew=args.nouveau_jeton)
    except OSError as error:
        _print(f"Le pont n'a pas pu enregistrer son code dans {pretty_path(bridge.token_file)} : {error}")
        _pause_on_windows()
        return 1
    try:
        bridge.start()
    except OSError as error:
        _print(f"Le pont de la voix n'a pas pu démarrer : {error.strerror or error}.")
        _print("Un pont de la voix tourne sans doute déjà dans une autre fenêtre : utilise-la, ou ferme-la puis "
               "relance celui-ci.")
        _pause_on_windows()
        return 1
    _announce(bridge, manifest, root, kenobi, args)
    try:
        signal.signal(signal.SIGTERM, _interrupt)
    except (ValueError, OSError, AttributeError):
        pass
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        bridge.close()
    _print("Pont de la voix arrêté : Whisper et Piper sont fermés.")
    return 0


def _announce(bridge, manifest, root, kenobi, args):
    system = platform.system()
    _print(f"Kenobi · voix locale {VERSION}")
    _print(f"  Ordinateur : {socket.gethostname()} ({OS_LABELS.get(system, system)}) · "
           f"Python {platform.python_version()}")
    _print(f"  Voix : {pretty_path(root)} · Piper {manifest['piper'].get('voice') or VOICE} (Pierre, Jessica) · "
           f"Whisper {manifest['_model']}")
    if kenobi:
        _print(f"  Kenobi : {kenobi[1]}")
    for port in bridge.busy_ports:
        other = _bridge_on(port)
        if other and other.get("app") == APP:
            _print(f"  Un autre pont de la voix ({other.get('version', '?')}) tourne déjà sur le port {port} : "
                   f"celui-ci prend le port {bridge.port}.")
        else:
            _print(f"  Le port {port} est pris par un autre programme : celui-ci prend le port {bridge.port}.")
    _print()
    _print(f"Pont de la voix prêt sur {bridge.host}:{bridge.port} : il n'écoute que cet ordinateur.")
    _print("Garde cette fenêtre ouverte pendant les appels ; ferme-la (ou Ctrl+C) pour arrêter la voix.")
    _print("Whisper et Piper démarrent à la première phrase, puis se reposent après 15 minutes sans appel.")
    link = f"{kenobi[1]}#voix={bridge.pairing_code}" if kenobi else ""
    wanted = bool(link) and (bridge.token_created or args.brancher) and not args.sans_navigateur
    if wanted and _can_open_browser() and _open_browser(link):
        _print("Kenobi s'ouvre dans ton navigateur, déjà branché.")
    elif link:
        _print(f"Lien qui ouvre Kenobi déjà branché : {link}")
    _print()
    _print("Code à coller dans Kenobi (réglages de la voix), si la page le demande :")
    _print(f"  {bridge.pairing_code}")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception:  # noqa: BLE001 - last resort: the message must stay readable
        traceback.print_exc()
        _pause_on_windows()
        sys.exit(1)
