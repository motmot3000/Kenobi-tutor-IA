"""Petit backend local pour l'interface Kenobi, sans dépendance externe."""

import hashlib
import json
import mimetypes
import os
import re
import shutil
import socket
import tempfile
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from memory import StateValidationError, render_course_memory, sync_builtin_plan, validate_state
import courses
import gateway
import providers
import settings as settings_module
import terminal_bridge
import tutor
import voice as voice_module


HOST = os.environ.get("KENOBI_HOST", "127.0.0.1")
DEFAULT_PORT = 8192
MAX_STATE_BYTES = 16 * 1024 * 1024  # la conversation garde les fichiers joints aux messages
PROJECT_ROOT = Path(__file__).resolve().parent
BRIDGE_PATH = PROJECT_ROOT / "terminal_bridge.py"
COURSE_ID_RE = re.compile(r"[a-z0-9-]+")
VERSION = "0.8.0"
LOOPBACK_CLIENTS = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
STATIC_FILES = {
    "/": "index.html",
    "/index.html": "index.html",
    "/styles.css": "styles.css",
    "/app.js": "app.js",
    "/core.js": "core.js",
    "/editor.js": "editor.js",
    "/terminal.js": "terminal.js",
    "/settings.js": "settings.js",
    "/call.js": "call.js",
    "/vad.js": "vad.js",
    "/vad-worklet.js": "vad-worklet.js",
    "/tour.js": "tour.js",
}
# Mots que la transcription doit écrire juste pendant un appel, selon le langage du cours.
VOICE_TERMS = {
    "python": ("Python", "boucle for", "boucle while", "print", "input", "variable", "if", "elif", "else", "def",
               "return", "liste", "dictionnaire", "tuple", "range", "import", "str", "int", "float"),
    "javascript": ("JavaScript", "console.log", "const", "let", "function", "return", "tableau", "objet", "Node"),
    "shell": ("terminal", "commande", "cd", "ls", "git", "commit", "branche", "dossier", "bash"),
}


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


class StateConflict(Exception):
    """L'état a changé depuis que la page l'a lu (un appel par la passerelle vocale, un autre onglet) : elle doit le relire."""

    def __init__(self, tag):
        super().__init__("conflict")
        self.tag = tag


def state_tag(data):
    """Étiquette d'une version de l'état : l'empreinte des octets enregistrés."""
    return "k-" + hashlib.sha256(data).hexdigest()[:20]


def _atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as temporary:
            temporary_name = temporary.name
            temporary.write(data)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
    finally:
        if temporary_name and os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _reject_json_constant(value):
    raise ValueError(f"Valeur JSON non standard : {value}")


class KenobiServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, server_address, data_dir, static_dir, config_dir=None, voice_dir=None):
        super().__init__(server_address, KenobiRequestHandler)
        self.data_dir = Path(data_dir)
        self.runtime_dir = self.data_dir / "runtime"
        self.default_state_path = self.data_dir / "default-state.json"
        self.state_path = self.runtime_dir / "state.json"
        self.static_dir = Path(static_dir).resolve()
        self.settings = settings_module.Settings(config_dir)
        self.voice = voice_module.VoiceManager(self.settings, voice_dir)
        self.state_lock = threading.Lock()
        self._course_ids = None
        self._plan = (None, None)
        self._tag_cache = (None, None)

    def default_state(self):
        """Le plan livré avec l'application (data/default-state.json), relu seulement s'il a changé."""
        try:
            mtime = self.default_state_path.stat().st_mtime
            if self._plan[0] != mtime:
                self._plan = (mtime, json.loads(self.default_state_path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return None
        return self._plan[1]

    def load_state(self):
        return self.load_state_with_tag()[0]

    def load_state_with_tag(self):
        """L'état enregistré et son étiquette de version (empreinte du fichier)."""
        if not self.state_path.exists():
            try:
                default_data = self.default_state_path.read_bytes()
            except FileNotFoundError as error:
                raise ApiError(500, "Le fichier default-state.json est introuvable.") from error
            _atomic_write(self.state_path, default_data)
        raw = self.state_path.read_bytes()
        try:
            state = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ApiError(500, "Le fichier d'état ne contient pas un JSON valide.") from error
        if self._course_ids is None and isinstance(state, dict):
            self._course_ids = {course.get("id") for course in state.get("courses") or [] if isinstance(course, dict)}
        if isinstance(state, dict):
            try:
                sync_builtin_plan(state, self.default_state())
            except (KeyError, TypeError, AttributeError):
                pass  # état écrit à la main et incomplet : il est servi tel quel, la validation le dira
        return state, state_tag(raw)

    def current_tag(self):
        try:
            return state_tag(self.state_path.read_bytes())
        except OSError:
            return None

    def cached_tag(self):
        """L'étiquette, recalculée seulement si le fichier a changé : la page la demande toutes les 5 secondes."""
        try:
            info = self.state_path.stat()
        except OSError:
            return None
        key = (info.st_mtime_ns, info.st_size)
        if self._tag_cache[0] != key:
            self._tag_cache = (key, self.current_tag())
        return self._tag_cache[1]

    def _serialize(self, state):
        validate_state(state)
        sync_builtin_plan(state, self.default_state())
        validate_state(state)
        return (json.dumps(state, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")

    def save_state(self, state, expected_tag=None):
        """Enregistre l'état de la page. Avec `expected_tag`, refuse (StateConflict) si l'état a changé entre-temps."""
        data = self._serialize(state)
        with self.state_lock:
            if expected_tag:
                current = self.current_tag()
                if current and current != expected_tag:
                    raise StateConflict(current)
            self._write_locked(state, data)
        return state_tag(data)

    def update_state(self, change):
        """Modifie l'état enregistré sous verrou : `change(state)` le modifie en place, ou renvoie False pour rien."""
        with self.state_lock:
            state = self.load_state()
            if change(state) is False:
                return None
            data = self._serialize(state)
            self._write_locked(state, data)
        return state_tag(data)

    def _write_locked(self, state, data):
        kept = {course["id"] for course in state["courses"]}
        if self._course_ids is None and self.state_path.exists():
            self.load_state()
        removed = (self._course_ids or set()) - kept
        if removed:
            self._trash_courses(removed)
        _atomic_write(self.state_path, data)
        self._course_ids = kept
        for course in state["courses"]:
            markdown = render_course_memory(state, course).encode("utf-8")
            _atomic_write(self.runtime_dir / course["id"] / "MEMORY.md", markdown)

    def _trash_courses(self, removed):
        """Un cours retiré de l'état part à la corbeille avec ses séances, sa mémoire et son document."""
        try:
            previous = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            previous = {}
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        for course in previous.get("courses") or []:
            course_id = course.get("id") if isinstance(course, dict) else None
            if course_id not in removed or not COURSE_ID_RE.fullmatch(str(course_id)):
                continue
            target = self.runtime_dir / "corbeille" / f"{course_id}-{stamp}"
            target.mkdir(parents=True, exist_ok=True)
            _atomic_write(target / "course.json", (json.dumps(course, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
            folder = self.runtime_dir / course_id
            if folder.is_dir():
                for entry in folder.iterdir():
                    if entry.is_file():
                        shutil.move(str(entry), str(target / entry.name))
                try:
                    folder.rmdir()
                except OSError:
                    pass

    def server_close(self):
        super().server_close()
        self.voice.close()


class KenobiRequestHandler(BaseHTTPRequestHandler):
    server_version = f"Kenobi/{VERSION}"
    sys_version = ""

    def log_message(self, format, *args):
        # Ni journal HTTP, ni contenu de message, ni clé : rien n'est écrit.
        pass

    def _send_bytes(self, status, body, content_type, headers=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status, value, headers=None):
        body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._send_bytes(status, body, "application/json; charset=utf-8", headers)

    def _send_error_json(self, status, message):
        self._send_json(status, {"error": message})

    def _handle_errors(self, action):
        try:
            action()
        except ApiError as error:
            self._send_error_json(error.status, error.message)
        except StateConflict as error:
            self._send_json(409, {"error": "L'état a changé ailleurs (appel, autre onglet) : la page le relit.",
                                  "conflict": True, "tag": error.tag})
        except gateway.GatewayError as error:
            self._send_json(error.status, {"error": {"message": error.message, "type": error.kind}})
        except StateValidationError as error:
            self._send_error_json(400, str(error))
        except settings_module.SettingsError as error:
            self._send_error_json(400, str(error))
        except courses.CourseError as error:
            self._send_error_json(400, str(error))
        except tutor.ChatError as error:
            self._send_error_json(error.status, error.message)
        except providers.ProviderError as error:
            self._send_error_json(error.status, error.message)
        except voice_module.VoiceError as error:
            self._send_error_json(error.status, error.message)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self._send_error_json(500, "Erreur interne du serveur.")

    def do_GET(self):
        self._handle_errors(self._get)

    def do_POST(self):
        self._handle_errors(self._post)

    def do_PUT(self):
        self._handle_errors(lambda: self._method_not_allowed())

    def do_PATCH(self):
        self._handle_errors(lambda: self._method_not_allowed())

    def do_DELETE(self):
        self._handle_errors(lambda: self._method_not_allowed())

    def _method_not_allowed(self):
        self._send_error_json(405, "Méthode HTTP non autorisée.")

    # ── Qui demande ─────────────────────────────────────────────────────────

    def _host_name(self):
        host = (self.headers.get("Host") or "").strip().lower()
        return host.split("]", 1)[0] + "]" if host.startswith("[") else host.rsplit(":", 1)[0]

    def _local_page(self):
        """Vrai si la page est ouverte sur cet ordinateur (client et en-tête Host en boucle locale)."""
        return self.client_address[0] in LOOPBACK_CLIENTS and self._host_name() in LOOPBACK_HOSTS

    def _same_computer(self):
        """Page ouverte sur cet ordinateur, par la boucle locale ou par une de ses propres adresses (réseau, tailnet).

        Le client et le serveur ont alors la même adresse. L'en-tête Host doit nommer cet ordinateur (boucle locale,
        adresse reçue, nom de la machine) : un nom de domaine qui pointerait ici par rebond DNS est refusé.
        """
        if self._local_page():
            return True
        try:
            local = self.connection.getsockname()[0]
        except OSError:
            return False
        client = self.client_address[0]
        if client != local and client not in LOOPBACK_CLIENTS:
            return False
        hostname = self._host_name().strip("[]")
        machine = socket.gethostname().lower()
        return hostname in {local, machine, f"{machine}.local"} or (
            hostname.startswith(f"{machine}.") and hostname.endswith(".ts.net"))

    def _same_site(self):
        fetch_site = self.headers.get("Sec-Fetch-Site")
        return not fetch_site or fetch_site in {"same-origin", "none"}

    def _can_edit_settings(self):
        """Les réglages portent des clés : seule une page de cet ordinateur les modifie, sauf choix contraire."""
        return self._same_site() and (self._local_page() or os.environ.get("KENOBI_REMOTE_SETTINGS") == "1")

    def _require_settings_access(self):
        if not self._can_edit_settings():
            raise ApiError(403, "Les réglages se modifient depuis l'ordinateur qui héberge Kenobi (http://127.0.0.1).")

    # ── Routes ──────────────────────────────────────────────────────────────

    def _get(self):
        path = urlsplit(self.path).path
        if path == "/api/health":
            self._send_json(200, {
                "status": "ok",
                "version": VERSION,
                "mode": "server",
                "agent": tutor.status(self.server.settings),
            })
            return
        if path == "/api/state":
            state, tag = self.server.load_state_with_tag()
            self._send_json(200, state, {"ETag": f'"{tag}"', "Cache-Control": "no-store"})
            return
        if path == "/api/state/tag":
            self._send_json(200, {"tag": self.server.cached_tag()}, {"Cache-Control": "no-store"})
            return
        if path == "/health":
            # Sonde des applications d'appel (passerelle vocale) : rien de privé.
            self._send_json(200, {"status": "ok", "app": "kenobi", "version": VERSION,
                                  "gateway": bool(gateway.read_token())})
            return
        if path == "/config.js":
            # Servie par ce serveur : l'interface utilise son API. Publiée seule (GitHub Pages), config.js dit
            # « browser » et tout tourne dans le navigateur.
            self._send_bytes(200, b'window.KENOBI_BACKEND = "server";\n', "text/javascript; charset=utf-8",
                             {"Cache-Control": "no-cache"})
            return
        if path == "/api/memory":
            self._get_memory()
            return
        if path == "/api/settings":
            value = self.server.settings.public()
            value["editable"] = self._can_edit_settings()
            value["mode"] = "server"
            self._send_json(200, value, {"Cache-Control": "no-store"})
            return
        if path == "/api/voice/status":
            self._send_json(200, self.server.voice.status(), {"Cache-Control": "no-store"})
            return
        if path == "/api/courses/template":
            self._send_bytes(200, courses.TEMPLATE.encode("utf-8"), "text/markdown; charset=utf-8", {
                "Content-Disposition": 'attachment; filename="modele-de-cours.md"',
            })
            return
        if path == "/api/courses/source":
            self._get_course_source()
            return
        if path == "/api/terminal":
            self._get_terminal()
            return
        if path == "/api/terminal/pont":
            self._get_bridge_download()
            return
        if path == "/api" or path.startswith("/api/"):
            raise ApiError(404, "Route API inconnue.")
        self._get_static()

    def _post(self):
        path = urlsplit(self.path).path
        routes = {
            "/api/state": self._post_state,
            "/api/chat/stream": self._post_chat_stream,
            "/api/chat": self._post_chat,
            "/api/settings": self._post_settings,
            "/api/settings/models": self._post_settings_models,
            "/api/settings/test": self._post_settings_test,
            "/api/courses/import": self._post_course_import,
            "/api/courses/write": self._post_course_write,
            "/api/voice/install": self._post_voice_install,
            "/api/voice/voices": self._post_voice_voices,
            "/api/voice/test": self._post_voice_test,
            "/api/call/phrases": self._post_call_phrases,
            "/api/call/turn": self._post_call_turn,
            "/v1/chat/completions": self._post_gateway,
        }
        handler = routes.get(path)
        if handler is None:
            if path == "/api" or path.startswith("/api/"):
                raise ApiError(404, "Route API inconnue.")
            raise ApiError(404, "Route inconnue.")
        handler()

    def _post_state(self):
        expected = (self.headers.get("If-Match") or "").strip().strip('"') or None
        tag = self.server.save_state(self._read_json_body(), expected)
        self._send_json(200, {"ok": True, "tag": tag}, {"ETag": f'"{tag}"'})

    # ── Passerelle vocale (lignes d'appel extérieures) ──────────────────────

    def _post_gateway(self):
        """Un tour d'une ligne d'appel extérieure, protocole compatible OpenAI (gateway.py)."""
        if not self._local_page():
            raise gateway.GatewayError(403, "Passerelle réservée aux applications de cet ordinateur.", "permission_error")
        gateway.check_token(self.headers.get("Authorization"))
        payload = self._read_json_body()
        gateway.heard_text(payload)  # une demande invalide reçoit son code d'erreur avant le flux
        if not payload.get("stream"):
            self._send_json(200, gateway.complete(self.server, payload))
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

        def write(data):
            self.wfile.write(data)
            self.wfile.flush()

        try:
            gateway.stream(self.server, payload, write, cancel=gateway.new_cancel())
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ── Discussion ──────────────────────────────────────────────────────────

    def _reply_options(self):
        return {"settings": self.server.settings, "data_dir": self.server.data_dir,
                "runtime_dir": self.server.runtime_dir}

    def _post_chat(self):
        payload = self._read_json_body()
        self._send_json(200, tutor.chat(self.server.load_state(), payload, **self._reply_options()))

    def _start_stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

        def emit(kind, data):
            message = f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
            self.wfile.write(message.encode("utf-8"))
            self.wfile.flush()

        return emit

    def _relay(self, events, emit):
        """Relaie un générateur d'évènements en SSE ; une erreur devient un évènement, une coupure arrête tout."""
        try:
            for kind, data in events:
                emit(kind, data)
        except (tutor.ChatError, providers.ProviderError, voice_module.VoiceError) as error:
            self._emit_quietly(emit, {"message": error.message, "status": error.status})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self._emit_quietly(emit, {"message": "Erreur interne de Kenobi.", "status": 500})
        finally:
            events.close()

    def _post_chat_stream(self):
        """Réponse de Kenobi en flux SSE : delta, done ou error."""
        payload = self._read_json_body()
        events = tutor.stream_reply(self.server.load_state(), payload, **self._reply_options())
        self._relay(events, self._start_stream())

    @staticmethod
    def _emit_quietly(emit, data):
        try:
            emit("error", data)
        except OSError:
            pass

    # ── Réglages ────────────────────────────────────────────────────────────

    def _post_settings(self):
        self._require_settings_access()
        data = self.server.settings.update(self._read_json_body())
        value = self.server.settings.public(data)
        value["editable"] = True
        value["mode"] = "server"
        self._send_json(200, value, {"Cache-Control": "no-store"})

    def _post_settings_models(self):
        """Modèles proposés par un fournisseur, avec la clé saisie ou celle déjà enregistrée pour lui."""
        self._require_settings_access()
        payload = self._read_json_body()
        wanted = payload.get("ai") if isinstance(payload.get("ai"), dict) else {}
        preset = settings_module.PROVIDER_IDS.get(str(wanted.get("provider") or ""))
        if not preset:
            raise ApiError(400, "Fournisseur inconnu.")
        saved = self.server.settings.ai()
        if preset.get("editableUrl"):
            base = settings_module.normalize_base_url(wanted.get("baseUrl")) or preset["baseUrl"]
        else:
            base = preset["baseUrl"]
        key = str(wanted.get("apiKey") or "").strip()
        if not key and saved["provider"] == preset["id"] and saved["baseUrl"] == base:
            key = saved["apiKey"]
        if not key and preset.get("env"):
            key = os.environ.get(preset["env"], "").strip()
        if not base:
            raise ApiError(400, "Adresse de l'API manquante.")
        if not key and not (preset.get("keyless") or preset.get("keyOptional")):
            raise ApiError(400, "Colle d'abord la clé d'API.")
        config = {"kind": preset["kind"], "label": preset["label"], "baseUrl": base, "apiKey": key, "model": ""}
        self._send_json(200, {"models": providers.list_models(config)}, {"Cache-Control": "no-store"})

    def _post_settings_test(self):
        self._require_settings_access()
        self._read_json_body()
        self._send_json(200, tutor.test_connection(self.server.settings), {"Cache-Control": "no-store"})

    # ── Cours ───────────────────────────────────────────────────────────────

    def _post_course_import(self):
        """Importe un cours Markdown : flux SSE progress, puis done {course, notice} ou error."""
        payload = self._read_json_body()
        filename = str(payload.get("filename") or "cours.md")[:120]
        text = courses.check_document(payload.get("markdown"))
        state = self.server.load_state()
        existing = {course.get("id") for course in state.get("courses") or []}
        trash = self.server.runtime_dir / "corbeille"
        if trash.is_dir():
            existing |= {entry.name.rsplit("-", 2)[0] for entry in trash.iterdir()}
        ai = self.server.settings.ai() if payload.get("useAi", True) else None
        emit = self._start_stream()
        last = [0.0]

        def on_progress(received):
            if time.monotonic() - last[0] > 0.4:
                last[0] = time.monotonic()
                emit("progress", {"stage": "analysis", "received": received})

        try:
            analysed = bool(ai and ai.get("configured"))
            emit("progress", {"stage": "analysis" if analysed else "outline", "chars": len(text),
                              "model": ai["model"] if analysed else ""})
            course, notice = courses.import_course(text, filename, ai=ai, existing_ids=existing, on_progress=on_progress)
            _atomic_write(self.server.runtime_dir / course["id"] / "COURSE.md", text.encode("utf-8"))
            emit("done", {"course": course, "notice": notice})
        except (courses.CourseError, providers.ProviderError) as error:
            self._emit_quietly(emit, {"message": getattr(error, "message", str(error)), "status": 400})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self._emit_quietly(emit, {"message": "Import interrompu par une erreur interne.", "status": 500})

    def _post_course_write(self):
        """Écrit le document d'un cours avec le modèle branché : flux SSE delta, puis done {markdown} ou error."""
        payload = self._read_json_body()
        wish = courses.check_wish(payload.get("wish"))
        ai = self.server.settings.ai()
        if not ai.get("configured"):
            raise ApiError(409, "Branche d'abord un modèle d'IA dans les réglages : c'est lui qui écrit le cours.")
        cancel = providers.Cancel()
        events = courses.write_course(wish, ai, cancel=cancel)
        try:
            self._relay(events, self._start_stream())
        finally:
            cancel.set()

    def _course_id_param(self):
        query = parse_qs(urlsplit(self.path).query)
        course_id = query.get("courseId", [""])[0]
        if not course_id:
            raise ApiError(400, "Le paramètre courseId est requis.")
        if not COURSE_ID_RE.fullmatch(course_id):
            raise ApiError(400, "courseId ne peut contenir que a-z, 0-9 et -.")
        return course_id, query

    def _get_course_source(self):
        course_id, query = self._course_id_param()
        path = tutor.course_document({"id": course_id}, self.server.data_dir, self.server.runtime_dir)
        if not path:
            raise ApiError(404, "Ce cours n'a pas de document Markdown.")
        headers = {}
        if query.get("download") == ["1"]:
            headers["Content-Disposition"] = f'attachment; filename="{course_id}.md"'
        self._send_bytes(200, path.read_bytes(), "text/markdown; charset=utf-8", headers)

    def _get_memory(self):
        course_id, query = self._course_id_param()
        memory_path = self.server.runtime_dir / course_id / "MEMORY.md"
        try:
            content = memory_path.read_bytes()
        except FileNotFoundError as error:
            raise ApiError(404, "MEMORY.md introuvable pour ce cours.") from error
        headers = {}
        if query.get("download") == ["1"]:
            headers["Content-Disposition"] = (
                f'attachment; filename="{course_id}-MEMORY.md"'
            )
        self._send_bytes(200, content, "text/markdown; charset=utf-8", headers)

    # ── Voix et appel ───────────────────────────────────────────────────────

    def _post_voice_install(self):
        self._require_settings_access()
        self._read_json_body()
        self.server.voice.local.install()
        self._send_json(202, self.server.voice.status(), {"Cache-Control": "no-store"})

    def _post_voice_voices(self):
        self._require_settings_access()
        payload = self._read_json_body()
        key = str(payload.get("apiKey") or "").strip()[:512]
        self._send_json(200, {"voices": self.server.voice.voices(key or None)}, {"Cache-Control": "no-store"})

    def _post_voice_test(self):
        self._read_json_body()
        self._send_json(200, self.server.voice.test(), {"Cache-Control": "no-store"})

    def _post_call_phrases(self):
        self._read_json_body()
        status = self.server.voice.status()
        phrases = self.server.voice.phrases(self.server.settings.learner_name()) if status["ready"] else {
            "greetings": [], "fillers": []}
        self._send_json(200, {**phrases, "delayMs": status["fillerDelayMs"], "ready": status["ready"],
                              "problem": status["problem"], "engine": status["label"]}, {"Cache-Control": "no-store"})

    def _voice_terms(self, state, session_id):
        course, _session = tutor.find_session(state, session_id)
        terms = ["Kenobi"]
        name = self.server.settings.learner_name()
        if name:
            terms.append(name.split()[0])
        if course:
            terms += VOICE_TERMS.get(tutor.course_tools(course)["language"], ())
        return terms

    def _post_call_turn(self):
        """Un tour d'appel : une phrase enregistrée (WAV) ou un texte, la réponse dite morceau par morceau (SSE)."""
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        audio, text = None, None
        if content_type == "application/json":
            payload = self._read_json_body()
            session_id = str(payload.get("sessionId") or "")
            terminal = payload.get("terminal")
            carry = ""
            text = str(payload.get("text") or "").strip()
            if not text:
                raise ApiError(400, "Message vide.")
        elif content_type in ("audio/wav", "audio/x-wav", "audio/wave"):
            audio = self._read_body(voice_module.MAX_AUDIO)
            session_id = self.headers.get("X-Session-Id", "")
            terminal = self.headers.get("X-Terminal")
            try:
                carry = unquote(self.headers.get("X-Carry", ""), errors="strict")[:2000]
            except (UnicodeDecodeError, ValueError):
                carry = ""
        else:
            raise ApiError(415, "Content-Type audio/wav ou application/json requis.")
        state = self.server.load_state()
        if not tutor.find_session(state, session_id)[1]:
            raise ApiError(404, "Séance introuvable : recharge la page.")
        options = self._reply_options()
        spoken = audio is not None

        def start_reply(heard, cancel):
            message = f"[Appel vocal] {heard}" if spoken else heard
            return tutor.stream_reply(state, {"sessionId": session_id, "message": message, "terminal": terminal},
                                      voice=True, cancel=cancel, **options)

        events = self.server.voice.turn(start_reply, audio=audio, text=text, carry=carry,
                                        terms=self._voice_terms(state, session_id))
        self._relay(events, self._start_stream())

    # ── Corps des requêtes ──────────────────────────────────────────────────

    def _read_body(self, limit):
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise ApiError(411, "Content-Length requis.")
        try:
            length = int(raw_length)
        except ValueError as error:
            raise ApiError(400, "Content-Length invalide.") from error
        if length < 0:
            raise ApiError(400, "Content-Length invalide.")
        if length > limit:
            self.close_connection = True
            raise ApiError(413, f"Le corps de la requête ne doit pas dépasser {limit // (1024 * 1024)} Mio.")
        body = self.rfile.read(length)
        if len(body) != length:
            raise ApiError(400, "Corps de requête incomplet.")
        return body

    def _read_json_body(self):
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise ApiError(415, "Content-Type application/json requis.")
        body = self._read_body(MAX_STATE_BYTES)
        try:
            text = body.decode("utf-8")
            return json.loads(text, parse_constant=_reject_json_constant)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            raise ApiError(400, "Corps JSON invalide.") from error

    # ── Terminal ────────────────────────────────────────────────────────────

    def _get_terminal(self):
        """Adresse et jeton du pont du terminal, donnés seulement à une page ouverte sur cet ordinateur."""
        if not self._same_site():
            raise ApiError(403, "Requête refusée.")
        if not self._same_computer():
            # Un autre ordinateur : son propre pont, jumelé une fois par un code ; ce jeton-ci ne sort jamais.
            value = {
                "available": False,
                "reason": "remote",
                "pairing": True,
                "download": "api/terminal/pont",
                "message": "Branche le terminal de cet ordinateur : télécharge le pont et lance-le.",
            }
        else:
            token = terminal_bridge.read_token()
            if token:
                value = {
                    "available": True,
                    "url": f"ws://127.0.0.1:{terminal_bridge.configured_port()}/terminal",
                    "token": token,
                }
            else:
                value = {
                    "available": False,
                    "reason": "bridge",
                    "message": "Le pont du terminal ne tourne pas (service kenobi-terminal.service).",
                }
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self._send_bytes(200, body, "application/json; charset=utf-8", {"Cache-Control": "no-store"})

    def _get_bridge_download(self):
        """Le pont en un fichier, réglé pour la page qui le demande (son adresse doit être celle de ce serveur).

        Sans paramètre (téléchargement par une commande), l'adresse est celle par laquelle on a joint Kenobi.
        """
        query = parse_qs(urlsplit(self.path).query)
        host = (self.headers.get("Host") or "").strip().lower()
        origin = terminal_bridge.normalize_origin(query.get("kenobi", [f"http://{host}"])[0])
        if not origin or origin.split("://", 1)[1] != host:
            raise ApiError(400, "Adresse de Kenobi invalide pour le pont.")
        source = BRIDGE_PATH.read_text(encoding="utf-8")
        marker = 'EMBEDDED_KENOBI_URL = ""'
        if source.count(marker) != 1:
            raise ApiError(500, "Pont du terminal introuvable.")
        body = source.replace(marker, f'EMBEDDED_KENOBI_URL = "{origin}"').encode("utf-8")
        self._send_bytes(200, body, "text/x-python; charset=utf-8", {
            "Content-Disposition": 'attachment; filename="kenobi-terminal.py"',
            "Cache-Control": "no-store",
        })

    # ── Fichiers de l'interface ─────────────────────────────────────────────

    def _get_static(self):
        raw_path = urlsplit(self.path).path
        path = unquote(raw_path)
        if path in STATIC_FILES:
            file_path = (self.server.static_dir / STATIC_FILES[path]).resolve()
            try:
                file_path.relative_to(self.server.static_dir)
            except ValueError as error:
                raise ApiError(404, "Fichier statique introuvable.") from error
        elif path.startswith("/assets/") and len(path) > len("/assets/"):
            assets_root = (self.server.static_dir / "assets").resolve()
            file_path = (assets_root / path[len("/assets/") :]).resolve()
            try:
                file_path.relative_to(assets_root)
            except ValueError as error:
                raise ApiError(404, "Fichier statique introuvable.") from error
        elif re.fullmatch(r"/browser/[a-z0-9-]+\.js", path):
            # Modules de la version web : chargés aussi ici, ils restent inactifs quand ce serveur répond.
            file_path = (self.server.static_dir / path.lstrip("/")).resolve()
        else:
            raise ApiError(404, "Fichier statique introuvable.")

        try:
            if not file_path.is_file():
                raise ApiError(404, "Fichier statique introuvable.")
            content = file_path.read_bytes()
        except (OSError, ValueError) as error:
            raise ApiError(404, "Fichier statique introuvable.") from error
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or file_path.suffix in {".js", ".svg"}:
            content_type += "; charset=utf-8"
        # L'interface change avec Kenobi : le navigateur ne doit jamais faire tourner un app.js périmé.
        self._send_bytes(200, content, content_type, {"Cache-Control": "no-cache"})


def create_server(port=None, data_dir=None, static_dir=None, config_dir=None, voice_dir=None):
    """Crée un serveur loopback ; port=0 est disponible pour les tests."""
    if port is None:
        port = int(os.environ.get("KENOBI_PORT", DEFAULT_PORT))
    if data_dir is None:
        data_dir = os.environ.get("KENOBI_DATA_DIR", PROJECT_ROOT / "data")
    if static_dir is None:
        static_dir = PROJECT_ROOT
    return KenobiServer((HOST, port), data_dir, static_dir, config_dir, voice_dir)


def main():
    server = create_server()
    host, port = server.server_address
    print(f"Kenobi disponible sur http://{host}:{port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
