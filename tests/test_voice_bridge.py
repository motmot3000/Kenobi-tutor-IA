import argparse
import ast
import contextlib
import http.client
import importlib.util
import io
import json
import os
import platform
import re
import select
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest
import wave
from pathlib import Path
from unittest import mock

import voice
import voice_bridge as vb

ROOT = Path(__file__).resolve().parent.parent
BRIDGE = ROOT / "voice_bridge.py"
MARKER = 'EMBEDDED_KENOBI_URL = ""'
ORIGIN = "https://example.github.io"
PAGE = "https://motmot3000.github.io/Kenobi-tutor-IA/"
WINDOWS = os.name == "nt"

try:
    import numpy  # noqa: F401 - the worker role needs it, like the voice's environment

    HAVE_NUMPY = True
except ImportError:
    HAVE_NUMPY = False


def load_installer():
    spec = importlib.util.spec_from_file_location("installer_voix", ROOT / "scripts" / "installer_voix.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def wav_bytes(seconds=1.0, rate=16000, channels=1, width=2):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(width)
        output.setframerate(rate)
        sample = struct.pack("<h", 1000) if width == 2 else b"\x90"
        output.writeframes(sample * int(seconds * rate) * channels)
    return buffer.getvalue()


SPOKEN = wav_bytes(0.2, 22050)


def multipart(fields, boundary="----WebKitFormBoundary7MA4YWxkTrZu0gW"):
    """A body made like a browser's FormData: [(name, text or bytes)], bytes being a file."""
    body = b""
    for name, value in fields:
        if isinstance(value, bytes):
            body += (f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="voix.wav"\r\n'
                     "Content-Type: audio/wav\r\n\r\n").encode() + value + b"\r\n"
        else:
            body += f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
    return body + f"--{boundary}--\r\n".encode()


class FakeEngine:
    def __init__(self):
        self.calls = []
        self.error = None
        self.closed = False

    def info(self):
        return {"ready": True, "voice": vb.VOICE, "speakers": list(vb.SPEAKERS), "stt": "small"}

    def transcribe(self, audio, language, prompt):
        self.calls.append(("stt", audio, language, prompt))
        if self.error:
            raise self.error
        return "c'est quoi une boucle"

    def speak(self, text, voice_name):
        self.calls.append(("tts", text, voice_name))
        if self.error:
            raise self.error
        return SPOKEN

    def close(self):
        self.closed = True


def read_line_matching(stream, pattern, timeout=20):
    """Read the bridge's output line by line, without Python's buffer (select then sees every line)."""
    deadline = time.monotonic() + timeout
    buffer = b""
    lines = []
    while time.monotonic() < deadline:
        ready, _, _ = select.select([stream], [], [], max(0.1, deadline - time.monotonic()))
        if not ready:
            break
        chunk = os.read(stream.fileno(), 4096)
        if not chunk:
            break
        buffer += chunk
        while b"\n" in buffer:
            raw, buffer = buffer.split(b"\n", 1)
            line = raw.decode("utf-8", "replace")
            lines.append(line)
            match = re.search(pattern, line)
            if match:
                return match, lines
    raise TimeoutError(f"ligne absente : {lines!r}")


def get(port, path, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        connection.request("GET", path, headers=headers or {})
        response = connection.getresponse()
        return response.status, {k.lower(): v for k, v in response.getheaders()}, response.read()
    finally:
        connection.close()


def post(port, path, body, headers):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    try:
        connection.request("POST", path, body=body, headers=headers)
        response = connection.getresponse()
        return response.status, {k.lower(): v for k, v in response.getheaders()}, response.read()
    finally:
        connection.close()


# ── Addresses, origins and the download marker ─────────────────────────────


class AddressTests(unittest.TestCase):
    def test_the_page_keeps_its_path_and_the_origin_is_what_browsers_send(self):
        app = ("https://motmot3000.github.io", PAGE)
        cases = {
            "http://127.0.0.1:8192": ("http://127.0.0.1:8192", "http://127.0.0.1:8192/"),  # bare origin
            "https://example.github.io": (ORIGIN, ORIGIN + "/"),
            PAGE: app,  # origin with a path
            "https://motmot3000.github.io/Kenobi-tutor-IA": app,  # no trailing slash
            "https://motmot3000.github.io/Kenobi-tutor-IA/index.html": app,  # file name dropped
            "https://motmot3000.github.io/Kenobi-tutor-IA/INDEX.HTM": app,
            "https://MotMot3000.GitHub.IO/Kenobi-tutor-IA/": app,  # host lower-cased, path kept
            "HTTPS://motmot3000.github.io:443/Kenobi-tutor-IA/": app,  # default port dropped
            "  https://motmot3000.github.io/Kenobi-tutor-IA/  ": app,
            "http://localhost:8192/index.html": ("http://localhost:8192", "http://localhost:8192/"),
            "http://[::1]:8192/kenobi": ("http://[::1]:8192", "http://[::1]:8192/kenobi/"),
            "https://example.github.io/kenobi.app": (ORIGIN, ORIGIN + "/kenobi.app/"),  # a folder with a dot
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(vb.kenobi_page(value), expected)

    def test_anything_else_is_refused(self):
        invalid = (
            "", None, "   ", "motmot3000.github.io/Kenobi-tutor-IA/", "//motmot3000.github.io/Kenobi-tutor-IA/",
            "ftp://example.github.io/", "javascript:alert(1)", "file:///home/thomas/index.html", "data:text/html,x",
            "https://user:secret@example.github.io/", "https://user@example.github.io/",
            "https://example.github.io/?voix=1", "https://example.github.io/?",
            "https://example.github.io/#voix=8196-ab", "https://example.github.io/#", "https:///Kenobi/", "https://",
            "https://example.github.io:99999/", "https://example.github.io:port/", "https://exa_mple.github.io/",
            "https://example.github.io/a b/",
            'https://example.github.io/a"b/', "https://example.github.io/a\\b/", "https://example.github.io/\nx",
            "https://example.github.io/Kénobi/", "https://[::1/",
        )
        for value in invalid:
            with self.subTest(value=value):
                self.assertIsNone(vb.kenobi_page(value))

    def test_allowed_origins_come_from_kenobi_the_download_the_option_and_the_environment(self):
        environment = {"KENOBI_VOICE_ORIGINS": " http://kenobi.lan:8192 ,https://Autre.example/app/index.html,pas une "
                                               "adresse,https://x.test/?a"}
        with mock.patch.object(vb, "EMBEDDED_KENOBI_URL", "https://MotMot3000.github.io/Kenobi-tutor-IA/index.html"), \
                mock.patch.dict(os.environ, environment):
            self.assertEqual(vb.allowed_origins("http://127.0.0.2:8193/kenobi/"), [
                "http://127.0.0.1:8192", "http://localhost:8192", "https://motmot3000.github.io",
                "http://127.0.0.2:8193", "http://kenobi.lan:8192", "https://autre.example"])
        with mock.patch.object(vb, "EMBEDDED_KENOBI_URL", "pas une adresse"), \
                mock.patch.dict(os.environ, {"KENOBI_VOICE_ORIGINS": ""}):
            self.assertEqual(vb.allowed_origins(), list(vb.DEFAULT_ORIGINS))


class DownloadTests(unittest.TestCase):
    def test_the_marker_appears_exactly_once(self):
        self.assertEqual(BRIDGE.read_text(encoding="utf-8").count(MARKER), 1)

    def test_a_downloaded_copy_knows_its_page(self):
        # What the web app does: the page's address as a JSON string (a valid Python string).
        literal = json.dumps(PAGE + "index.html")
        source = BRIDGE.read_text(encoding="utf-8").replace(MARKER, f"EMBEDDED_KENOBI_URL = {literal}")
        namespace = {"__name__": "kenobi_voice_copy", "__file__": "kenobi-voice.py"}
        exec(compile(source, "kenobi-voice.py", "exec"), namespace)  # noqa: S102 - our own file
        with mock.patch.dict(os.environ, {"KENOBI_VOICE_ORIGINS": ""}):
            self.assertIn("https://motmot3000.github.io", namespace["allowed_origins"]())
        self.assertEqual(namespace["kenobi_page"](namespace["EMBEDDED_KENOBI_URL"]),
                         ("https://motmot3000.github.io", PAGE))


class FolderTests(unittest.TestCase):
    def test_the_voice_folder_is_the_one_of_voice_py_and_of_the_installer(self):
        installer = load_installer()
        base = {key: value for key, value in os.environ.items() if key not in ("KENOBI_VOICE_DIR", "XDG_DATA_HOME")}
        for extra in ({"KENOBI_VOICE_DIR": "~/ailleurs/voix"}, {"XDG_DATA_HOME": "/srv/donnees"}, {}):
            with self.subTest(**extra), mock.patch.dict(os.environ, dict(base, **extra), clear=True):
                self.assertEqual(vb.voice_dir(), voice.voice_dir())
                self.assertEqual(vb.voice_dir(), installer.default_dir())

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux folders")
    def test_the_token_lives_in_the_bridge_config_folder(self):
        with mock.patch.dict(os.environ, {"HOME": "/home/camille"}):
            os.environ.pop("XDG_CONFIG_HOME", None)
            os.environ.pop("KENOBI_VOICE_HOME", None)
            self.assertEqual(vb.token_path(), Path("/home/camille/.config/kenobi-voice/token"))
            os.environ["KENOBI_VOICE_HOME"] = "~/autre"
            self.assertEqual(vb.token_path(), Path("/home/camille/autre/token"))


class TokenTests(unittest.TestCase):
    def test_the_token_is_private_kept_and_renewed(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config" / "token"
            first = vb.VoiceBridge(FakeEngine(), port=0, token_file=path)
            token = first.load_or_create_token()
            self.assertTrue(first.token_created)
            self.assertRegex(token, r"^[0-9a-f]{48}$")
            if not WINDOWS:
                self.assertEqual(oct(path.stat().st_mode & 0o777), "0o600")
            second = vb.VoiceBridge(FakeEngine(), port=0, token_file=path)
            self.assertEqual(second.load_or_create_token(), token)
            self.assertFalse(second.token_created)
            time.sleep(0.02)
            renewed = second.load_or_create_token(renew=True)
            self.assertNotEqual(renewed, token)
            self.assertTrue(second.token_created)
            # A bridge already running picks up the code renewed by another one.
            self.assertEqual(first.current_token(), renewed)
            if not WINDOWS:
                os.chmod(path, 0o644)  # readable by others: not trusted any more
                self.assertIsNone(vb.read_token(path))
                third = vb.VoiceBridge(FakeEngine(), port=0, token_file=path)
                self.assertNotEqual(third.load_or_create_token(), renewed)
                self.assertEqual(oct(path.stat().st_mode & 0o777), "0o600")


class RequestHelperTests(unittest.TestCase):
    def test_recordings_are_checked(self):
        self.assertAlmostEqual(vb.check_wav(wav_bytes(1.5)), 1.5, places=2)
        self.assertAlmostEqual(vb.check_wav(wav_bytes(2, 44100, channels=2)), 2.0, places=2)
        for audio, status in ((b"", 400), (b"RIFF pas un wav", 400), (wav_bytes(1, width=1), 400),
                              (wav_bytes(1, rate=4000), 400), (wav_bytes(0), 400), (wav_bytes(61, 8000), 413),
                              (b"\x00" * (vb.MAX_AUDIO + 1), 413)):
            with self.subTest(size=len(audio)):
                with self.assertRaises(vb.VoiceBridgeError) as caught:
                    vb.check_wav(audio)
                self.assertEqual(caught.exception.status, status)

    def test_languages(self):
        for value, expected in (("", "fr"), (None, "fr"), ("fr", "fr"), (" EN ", "en"), ("fr-CH", "fr"),
                                ("pt_BR", "pt"), ("haw", "haw"), ("auto", "auto")):
            self.assertEqual(vb.parse_language(value), expected)
        for value in ("français", "f", "12", "fr;en"):
            with self.subTest(value=value), self.assertRaises(vb.VoiceBridgeError):
                vb.parse_language(value)

    def test_the_limits_are_those_of_voice_py(self):
        self.assertEqual((vb.MAX_AUDIO, vb.MAX_SECONDS, vb.MAX_TEXT, vb.IDLE_SECONDS),
                         (voice.MAX_AUDIO, voice.MAX_SECONDS, voice.MAX_SPOKEN, voice.IDLE_SECONDS))
        self.assertEqual((vb.DEFAULT_PORT, list(vb.SPARE_PORTS)), (8196, [8197, 8198, 8199]))

    def test_an_installation_step_that_fails_shows_its_last_lines(self):
        vb.run([sys.executable, "-c", "print('ok')"], "Échec.")
        with self.assertRaises(vb.Failure) as caught:
            vb.run([sys.executable, "-c", "import sys; print('a'); print('b'); print('ERROR: no space'); sys.exit(1)"],
                   "Installation des paquets impossible.")
        self.assertEqual(str(caught.exception), "Installation des paquets impossible. b ERROR: no space")
        with self.assertRaises(vb.Failure):
            vb.run([str(Path(tempfile.gettempdir()) / "programme-absent")], "Programme introuvable.")


# ── HTTP server with a fake engine ──────────────────────────────────────────


class BridgeCase(unittest.TestCase):
    announce = False

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.engine = FakeEngine()
        self.bridge = vb.VoiceBridge(self.engine, port=0, origins=[ORIGIN, "http://127.0.0.1:8192"],
                                     token_file=Path(self.folder.name) / "token", announce=self.announce)
        self.bridge.start()
        self.port = self.bridge.port
        self.token = self.bridge.token

    def tearDown(self):
        self.bridge.close()
        self.folder.cleanup()
        self.assertTrue(self.engine.closed)

    def call(self, method, path, body=None, headers=None, token=None, origin=None, host=None):
        sent = {"Host": host or f"127.0.0.1:{self.port}"}
        if token:
            sent["Authorization"] = f"Bearer {token}"
        if origin:
            sent["Origin"] = origin
        sent.update(headers or {})
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            connection.request(method, path, body=body, headers=sent)
            response = connection.getresponse()
            return response.status, {k.lower(): v for k, v in response.getheaders()}, response.read()
        finally:
            connection.close()

    def raw(self, request, pause=None):
        """Bytes sent as they are (requests http.client will not make): (status, head, body)."""
        with socket.create_connection(("127.0.0.1", self.port), timeout=10) as sock:
            sock.sendall(request)
            data = b""
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                data += chunk
        head, _, body = data.partition(b"\r\n\r\n")
        return int(head.split(b" ")[1]), head.decode("latin-1"), body

    def speech(self, payload, **options):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        return self.call("POST", "/v1/audio/speech", body=body, token=options.pop("token", self.token),
                         headers={"Content-Type": "application/json"}, **options)

    def transcription(self, body, content_type, **options):
        return self.call("POST", "/v1/audio/transcriptions", body=body, token=options.pop("token", self.token),
                         headers={"Content-Type": content_type}, **options)


class SecurityTests(BridgeCase):
    def test_health_needs_no_token_and_tells_what_is_ready(self):
        status, headers, body = self.call("GET", "/health")
        self.assertEqual(status, 200)
        self.assertEqual(headers["content-type"], "application/json; charset=utf-8")
        self.assertEqual(json.loads(body), {"ok": True, "app": "kenobi-voice", "version": "1.0.0", "protocol": 1,
                                            "ready": True, "voice": "fr_FR-upmc-medium",
                                            "speakers": ["pierre", "jessica"], "stt": "small"})
        self.assertNotIn(self.token, body.decode())
        self.assertNotIn("access-control-allow-origin", headers)
        self.assertEqual((headers["cache-control"], headers["x-content-type-options"]), ("no-store", "nosniff"))
        status, headers, _ = self.call("GET", "/health", origin=ORIGIN)
        self.assertEqual((status, headers["access-control-allow-origin"], headers["vary"]), (200, ORIGIN, "Origin"))

    def test_only_this_computer_is_answered(self):
        for host in ("evil.test", f"evil.test:{self.port}", "127.0.0.1", f"127.0.0.1:{self.port + 1}",
                     f"192.168.1.20:{self.port}", f"127.0.0.1.nip.io:{self.port}"):
            with self.subTest(host=host):
                status, headers, body = self.call("GET", "/health", host=host, origin=ORIGIN)
                self.assertEqual(status, 403)
                self.assertNotIn("access-control-allow-origin", headers)
                self.assertEqual(json.loads(body)["error"]["type"], "forbidden")
        # DNS rebinding: even with the code, another host name is refused.
        self.assertEqual(self.call("GET", "/v1/models", host=f"evil.test:{self.port}", token=self.token)[0], 403)
        for host in (f"localhost:{self.port}", f"LOCALHOST:{self.port}", f"[::1]:{self.port}"):
            with self.subTest(host=host):
                self.assertEqual(self.call("GET", "/health", host=host)[0], 200)
        self.assertEqual(self.raw(b"GET /health HTTP/1.0\r\n\r\n")[0], 403)  # no Host at all

    def test_the_v1_routes_need_the_code(self):
        status, headers, body = self.call("GET", "/v1/models")
        self.assertEqual(status, 401)
        self.assertEqual(json.loads(body)["error"]["type"], "auth")
        self.assertEqual(headers["www-authenticate"], 'Bearer realm="kenobi-voice"')
        for authorization in ("Bearer " + "0" * 48, "Bearer", "Bearer ", "Basic " + self.token, self.token,
                              "Bearer " + self.token + "0", "Bearer " + self.token[:-1],
                              "Bearer " + self.token.upper()):
            with self.subTest(authorization=authorization):
                self.assertEqual(self.call("GET", "/v1/models", headers={"Authorization": authorization})[0], 401)
        status, _, body = self.call("GET", "/v1/models", headers={"Authorization": "bearer  " + self.token})
        self.assertEqual(status, 200)
        listing = json.loads(body)
        self.assertEqual((listing["object"], [model["id"] for model in listing["data"]]),
                         ("list", ["whisper", "piper"]))
        self.assertEqual(self.call("GET", "/v1/nothing")[0], 401)  # nothing is said about /v1/ without the code
        self.assertEqual(self.engine.calls, [])

    def test_kenobi_reads_the_answers_and_other_pages_read_nothing(self):
        # A refused code is readable by Kenobi's page: it can ask for the new one.
        status, headers, _ = self.speech({"input": "Bonjour"}, token="0" * 48, origin=ORIGIN)
        self.assertEqual((status, headers["access-control-allow-origin"]), (401, ORIGIN))
        for origin in ("https://evil.test", "null", "https://example.github.io.evil.test", "http://example.github.io",
                       "https://example.github.io:8443", "https://example.github.io/kenobi"):
            with self.subTest(origin=origin):
                status, headers, _ = self.speech({"input": "Bonjour"}, origin=origin)
                self.assertEqual(status, 403)
                self.assertFalse([name for name in headers if name.startswith("access-control-")])
        self.assertEqual(self.engine.calls, [])
        status, headers, _ = self.speech({"input": "Bonjour"}, origin=ORIGIN)
        self.assertEqual((status, headers["access-control-allow-origin"], headers["vary"]), (200, ORIGIN, "Origin"))
        status, headers, _ = self.speech({"input": "Bonjour"}, origin="http://127.0.0.1:8192")
        self.assertEqual((status, headers["access-control-allow-origin"]), (200, "http://127.0.0.1:8192"))
        # Programs of this computer send no Origin: the code is enough.
        status, headers, _ = self.speech({"input": "Bonjour"})
        self.assertEqual(status, 200)
        self.assertNotIn("access-control-allow-origin", headers)

    def test_preflight_lets_kenobi_in_from_the_internet(self):
        asked = {"Access-Control-Request-Method": "POST",
                 "Access-Control-Request-Headers": "authorization, content-type",
                 "Access-Control-Request-Private-Network": "true"}
        status, headers, body = self.call("OPTIONS", "/v1/audio/speech", origin=ORIGIN, headers=asked)
        self.assertEqual((status, body), (204, b""))
        self.assertEqual(headers["access-control-allow-origin"], ORIGIN)
        self.assertEqual(headers["access-control-allow-methods"], "GET, POST, OPTIONS")
        self.assertEqual(headers["access-control-allow-headers"], "authorization, content-type")
        self.assertEqual(headers["access-control-max-age"], "600")
        self.assertEqual(headers["access-control-allow-private-network"], "true")
        self.assertEqual(headers["vary"], "Origin")
        status, headers, _ = self.call("OPTIONS", "/v1/audio/transcriptions", origin="http://127.0.0.1:8192",
                                       headers={"Access-Control-Request-Method": "POST"})
        self.assertEqual((status, headers["access-control-allow-origin"]), (204, "http://127.0.0.1:8192"))
        self.assertNotIn("access-control-allow-private-network", headers)
        status, headers, _ = self.call("OPTIONS", "/v1/audio/speech", origin="https://evil.test", headers=asked)
        self.assertEqual(status, 403)
        self.assertFalse([name for name in headers if name.startswith("access-control-")])

    def test_unknown_routes_and_methods(self):
        self.assertEqual(self.call("GET", "/")[0], 404)
        self.assertEqual(self.call("GET", "/v1/nothing", token=self.token)[0], 404)
        self.assertEqual(self.call("GET", "/v1/audio/speech/", token=self.token)[0], 404)
        status, headers, body = self.call("GET", "/v1/audio/speech", token=self.token)
        self.assertEqual((status, headers["allow"]), (405, "POST, OPTIONS"))
        self.assertEqual(json.loads(body)["error"]["type"], "invalid_request_error")
        self.assertEqual(self.call("POST", "/health", body=b"")[0], 405)
        self.assertEqual(self.call("GET", "/v1/models?x=1", token=self.token)[0], 200)


class TranscriptionTests(BridgeCase):
    def test_a_recording_is_transcribed(self):
        audio = wav_bytes(1.5)
        body = multipart([("model", "whisper-1"), ("file", audio), ("language", "fr-CH"),
                          ("prompt", "Séance de tutorat en informatique. Vocabulaire : boucle, liste.")])
        status, headers, data = self.transcription(
            body, "multipart/form-data; boundary=----WebKitFormBoundary7MA4YWxkTrZu0gW", origin=ORIGIN)
        self.assertEqual(status, 200)
        self.assertEqual(headers["content-type"], "application/json; charset=utf-8")
        self.assertEqual(headers["access-control-allow-origin"], ORIGIN)
        self.assertEqual(json.loads(data), {"text": "c'est quoi une boucle"})
        self.assertEqual(self.engine.calls,
                         [("stt", audio, "fr", "Séance de tutorat en informatique. Vocabulaire : boucle, liste.")])
        # French by default, no prompt; plain text on request; the prompt is cut for Whisper.
        body = multipart([("file", audio), ("response_format", "text"), ("prompt", "x" * 1000)], "abc")
        status, headers, data = self.transcription(body, "multipart/form-data; boundary=abc")
        self.assertEqual((status, headers["content-type"], data), (200, "text/plain; charset=utf-8",
                                                                   "c'est quoi une boucle".encode()))
        self.assertEqual(self.engine.calls[-1], ("stt", audio, "fr", "x" * vb.MAX_PROMPT))

    def test_the_boundaries_of_every_kind_of_client(self):
        audio = wav_bytes(0.5, 8000)
        cases = (
            ("----WebKitFormBoundary7MA4YWxkTrZu0gW", "multipart/form-data; boundary={}"),  # Chrome, Safari
            ("---------------------------735323031399963166993862150", "multipart/form-data; boundary={}"),  # Firefox
            ("------------------------d74496d66958873e", "multipart/form-data; boundary={}"),  # curl
            ("3f2a9c1e5b7d4f6a8c0e2b4d6f8a0c1e", "multipart/form-data; boundary={}"),  # Python (httpx, requests)
            ("a'()+_,-./:=? b", 'multipart/form-data; boundary="{}"'),  # quoted, every character RFC 2046 allows
            ("xYz", "Multipart/Form-Data; charset=utf-8; BOUNDARY={}"),  # case, parameter order
            ("x" * 70, "multipart/form-data; boundary={}"),  # longest
        )
        for boundary, content_type in cases:
            with self.subTest(boundary=boundary):
                self.engine.calls.clear()
                body = multipart([("model", "whisper-1"), ("file", audio), ("language", "en")], boundary)
                status, _, data = self.transcription(body, content_type.format(boundary))
                self.assertEqual(status, 200, data)
                self.assertEqual(self.engine.calls, [("stt", audio, "en", "")])

    def test_binary_data_preamble_and_padding_survive(self):
        boundary = "frontiere"
        audio = wav_bytes(0.5, 8000)
        # Recorded sound may hold anything: lines, "--", the boundary inside a line or followed by other bytes.
        tricky = audio + b"\r\n--\r\nx--frontiere\r\n\r\n--frontiere-pas-une-limite\r\n--frontierexx\r\n"
        body = (b"Un preambule que les clients n'envoient pas.\r\n--frontiere  \t\r\n"
                b'Content-Disposition: form-data; name="file"; filename="a.wav"\r\nContent-Type: audio/wav\r\n\r\n'
                + tricky + b"\r\n--frontiere\r\nContent-Disposition: form-data; name=language\r\n\r\nde\r\n"
                b"--frontiere\r\nContent-Disposition: form-data; name=\"file\"\r\n\r\nseconde copie\r\n"
                b"--frontiere--\r\nUn epilogue.")
        with mock.patch.object(vb, "check_wav", return_value=1.0):
            status, _, data = self.transcription(body, "multipart/form-data; boundary=frontiere")
        self.assertEqual(status, 200, data)
        self.assertEqual(self.engine.calls, [("stt", tricky, "de", "")])
        parts = vb.parse_multipart(body, "multipart/form-data; boundary=frontiere")
        self.assertEqual(parts["file"], ("a.wav", tricky))
        self.assertEqual(parts["language"], (None, b"de"))

    def test_bad_uploads_are_refused_with_their_reason(self):
        content_type = "multipart/form-data; boundary=b0undary"
        audio = wav_bytes(1)

        def body(*fields):
            return multipart(fields, "b0undary")

        cases = (
            (body(("model", "whisper-1")), content_type, 400, "« file »"),
            (body(("file", audio)), "application/octet-stream", 400, "multipart/form-data"),
            (body(("file", audio)), "multipart/form-data", 400, "boundary"),
            (body(("file", audio)), 'multipart/form-data; boundary=""', 400, "boundary"),
            (body(("file", audio))[:-20], content_type, 400, "multipart illisible"),
            (b"", content_type, 400, "multipart illisible"),
            (body(*[("f%d" % index, "x") for index in range(vb.MAX_PARTS + 1)]), content_type, 400, "Trop de champs"),
            (body(("file", b"")), content_type, 400, "vide"),
            (body(("file", b"RIFF pas un wav")), content_type, 400, "illisible"),
            (body(("file", wav_bytes(1, width=1))), content_type, 400, "16 bits"),
            (body(("file", wav_bytes(61, 8000))), content_type, 413, "60 secondes"),
            (body(("file", b"\x00" * (vb.MAX_AUDIO + 1))), content_type, 413, "8 Mio"),
            (body(("file", audio), ("language", "français")), content_type, 400, "Langue"),
            (body(("file", audio), ("response_format", "srt")), content_type, 400, "json ou text"),
        )
        for data, kind, status, words in cases:
            with self.subTest(words=words, kind=kind):
                got, headers, answer = self.transcription(data, kind, origin=ORIGIN)
                self.assertEqual(got, status, answer)
                self.assertEqual(headers["content-type"], "application/json; charset=utf-8")
                self.assertEqual(headers["access-control-allow-origin"], ORIGIN)
                error = json.loads(answer)["error"]
                self.assertEqual(error["type"], "invalid_request_error")
                self.assertIn(words, error["message"])
        self.assertEqual(self.engine.calls, [])

    def test_too_big_a_body_gets_an_answer_not_a_reset(self):
        status, _, data = self.transcription(b"\x00" * (vb.MAX_BODY + 1), "multipart/form-data; boundary=b")
        self.assertEqual(status, 413)
        self.assertIn("8 Mio", json.loads(data)["error"]["message"])
        # Refused for a wrong code: the body is read all the same, the answer arrives.
        status, _, _ = self.transcription(multipart([("file", wav_bytes(30))], "b"), "multipart/form-data; boundary=b",
                                          token="0" * 48)
        self.assertEqual(status, 401)

    def test_a_body_needs_its_length_and_must_not_linger(self):
        head = (f"POST /v1/audio/speech HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n"
                f"Authorization: Bearer {self.token}\r\nContent-Type: application/json\r\n")
        self.assertEqual(self.raw((head + "\r\n").encode())[0], 411)
        self.assertEqual(self.raw((head + "Transfer-Encoding: chunked\r\n\r\n0\r\n\r\n").encode())[0], 411)
        self.assertEqual(self.raw((head + "Content-Length: -5\r\n\r\n").encode())[0], 400)
        with mock.patch.object(vb._Handler, "timeout", 0.3):
            begin = time.monotonic()
            status, _, body = self.raw((head + 'Content-Length: 100\r\n\r\n{"input": ').encode())
            self.assertEqual(status, 408)
            self.assertLess(time.monotonic() - begin, 5)
        self.assertEqual(self.engine.calls, [])

    def test_curl_waiting_for_100_continue_is_served(self):
        audio = wav_bytes(1)
        body = multipart([("file", audio)], "b")
        head = (f"POST /v1/audio/transcriptions HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n"
                f"Authorization: Bearer {self.token}\r\nContent-Type: multipart/form-data; boundary=b\r\n"
                f"Content-Length: {len(body)}\r\nExpect: 100-continue\r\n\r\n").encode()
        with socket.create_connection(("127.0.0.1", self.port), timeout=10) as sock:
            sock.sendall(head)
            self.assertTrue(sock.recv(1024).startswith(b"HTTP/1.1 100"))
            sock.sendall(body)
            answer = b""
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                answer += chunk
        self.assertTrue(answer.startswith(b"HTTP/1.1 200"), answer[:80])
        self.assertEqual(self.engine.calls, [("stt", audio, "fr", "")])


class SpeechTests(BridgeCase):
    def test_text_is_said_in_the_chosen_voice(self):
        status, headers, data = self.speech({"model": "tts-1", "input": "  Une boucle\n répète.  ", "voice": "Jessica",
                                             "response_format": "wav"})
        self.assertEqual((status, headers["content-type"], data), (200, "audio/wav", SPOKEN))
        self.assertEqual(headers["content-length"], str(len(SPOKEN)))
        self.assertEqual(self.engine.calls, [("tts", "Une boucle répète.", "jessica")])
        self.assertEqual(self.speech({"input": "Salut"})[0], 200)  # Pierre by default, wav by default
        self.assertEqual(self.engine.calls[-1], ("tts", "Salut", "pierre"))
        self.assertEqual(self.speech({"input": "é" * vb.MAX_TEXT, "voice": None})[0], 200)

    def test_bad_requests_are_refused_with_their_reason(self):
        cases = (
            ({"input": ""}, "vide"),
            ({"input": " \n\t "}, "vide"),
            ({"voice": "pierre"}, "« input »"),
            ({"input": 42}, "« input »"),
            ({"input": "x" * (vb.MAX_TEXT + 1)}, "trop long"),
            ({"input": "Salut", "voice": "alloy"}, "Voix inconnue"),
            ({"input": "Salut", "voice": 3}, "Voix inconnue"),
            ({"input": "Salut", "voice": ""}, "Voix inconnue"),
            ({"input": "Salut", "response_format": "mp3"}, "wav seulement"),
            ({"input": "Salut", "response_format": 1}, "wav seulement"),
            (b"{pas du json", "JSON"),
            (b"\xff\xfe", "JSON"),
            (b"[1, 2]", "objet JSON"),
        )
        for payload, words in cases:
            with self.subTest(words=words, payload=str(payload)[:40]):
                status, headers, data = self.speech(payload)
                self.assertEqual(status, 400)
                self.assertEqual(headers["content-type"], "application/json; charset=utf-8")
                error = json.loads(data)["error"]
                self.assertEqual(error["type"], "invalid_request_error")
                self.assertIn(words, error["message"])
        status, _, data = self.speech(b" " * (vb.MAX_JSON + 1))
        self.assertEqual(status, 413)
        self.assertEqual(self.engine.calls, [])

    def test_engine_failures_become_clear_errors(self):
        self.engine.error = vb.unavailable("La voix locale s'est arrêtée : elle redémarre à la prochaine demande.")
        status, headers, data = self.speech({"input": "Salut"}, origin=ORIGIN)
        self.assertEqual((status, headers["access-control-allow-origin"]), (503, ORIGIN))
        self.assertEqual(json.loads(data)["error"], {"message": self.engine.error.message, "type": "unavailable"})
        self.engine.error = RuntimeError("détail interne")
        status, _, data = self.speech({"input": "Salut"})
        self.assertEqual(status, 500)
        self.assertNotIn("détail interne", data.decode())


class QuietWindowTests(BridgeCase):
    announce = True

    def test_the_window_shows_refusals_and_use_never_the_content(self):
        written = io.StringIO()
        with contextlib.redirect_stdout(written), contextlib.redirect_stderr(written):
            self.speech({"input": "Phrase confidentielle de Camille"}, origin=ORIGIN)
            self.speech({"input": "Phrase confidentielle de Camille"}, origin=ORIGIN)
            body = multipart([("file", wav_bytes(1)), ("prompt", "Vocabulaire confidentiel")], "b")
            self.transcription(body, "multipart/form-data; boundary=b")
            for _ in range(3):
                self.speech({"input": "Phrase confidentielle"}, token="0" * 48, origin=ORIGIN)
                self.speech({"input": "Phrase confidentielle"}, origin="https://evil.test")
        lines = written.getvalue().splitlines()
        self.assertNotIn("confidentiel", written.getvalue())
        self.assertEqual(lines, [
            "Kenobi est branché (https://example.github.io).",
            "Un programme de cet ordinateur utilise la voix.",
            "Refusé : une demande avec un code faux ou ancien. Le code à coller dans Kenobi est :",
            f"  {self.bridge.pairing_code}",
            "Refusé : la page https://evil.test n'est pas Kenobi. Si c'en est bien un, relance ce pont avec : "
            "--kenobi https://evil.test",
        ])


class PortTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.blocker = socket.socket()
        self.blocker.bind(("127.0.0.1", 0))
        self.blocker.listen(1)
        self.addCleanup(self.blocker.close)
        self.busy = self.blocker.getsockname()[1]

    def bridge(self, **options):
        return vb.VoiceBridge(FakeEngine(), port=self.busy, token_file=Path(self.folder.name) / "token", **options)

    def test_a_busy_port_falls_back_to_the_next_free_one(self):
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        free = probe.getsockname()[1]
        probe.close()
        bridge = self.bridge(spare_ports=True)
        with mock.patch.object(vb, "SPARE_PORTS", [free]):
            bridge.start()
        try:
            self.assertEqual((bridge.port, bridge.busy_ports), (free, [self.busy]))
            found = vb._bridge_on(free)
            self.assertEqual((found["app"], found["version"]), ("kenobi-voice", vb.VERSION))
            self.assertIsNone(vb._bridge_on(self.busy))  # something that does not answer like a bridge
        finally:
            bridge.close()

    def test_a_forced_port_or_a_full_range_is_an_error(self):
        with self.assertRaises(OSError) as caught:
            self.bridge().start()
        self.assertIn(f"le port {self.busy} est pris", caught.exception.strerror)
        with mock.patch.object(vb, "SPARE_PORTS", [self.busy]), self.assertRaises(OSError) as caught:
            self.bridge(spare_ports=True).start()
        self.assertIn("sont tous pris", caught.exception.strerror)


# ── Installation compatible with scripts/installer_voix.py ──────────────────


def shape(value):
    if isinstance(value, dict):
        return {key: shape(item) for key, item in value.items()}
    if isinstance(value, list):
        return [shape(item) for item in value]
    return type(value).__name__


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name) / "voice"
        self.installer = load_installer()
        self.result = {"speakers": ["jessica", "pierre"], "tts_seconds": 0.066, "stt_seconds": 0.394,
                       "heard": "Bonjour"}

    def run_installer(self, model="small"):
        """scripts/installer_voix.py from start to end, with its downloads replaced by stand-ins."""
        python = vb.venv_python(self.root)

        def create_venv(root, report):
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_text("")
            return python

        with mock.patch.object(self.installer, "create_venv", create_venv), \
                mock.patch.object(self.installer, "install_packages", lambda *args: None), \
                mock.patch.object(self.installer, "download", lambda *args: None), \
                mock.patch.object(self.installer, "download_whisper", lambda *args: None), \
                mock.patch.object(self.installer, "smoke_test", lambda *args: dict(self.result)), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.installer.main(["--dir", str(self.root), "--whisper", model]), 0)
        return json.loads((self.root / "manifest.json").read_text(encoding="utf-8")), python

    def test_the_install_is_the_one_of_the_installer(self):
        for name in ("PACKAGES", "VOICE", "VOICE_URL", "VOICE_FILES", "WHISPER_MODELS", "TEST_SENTENCE"):
            with self.subTest(name=name):
                self.assertEqual(getattr(vb, name), getattr(self.installer, name))
        self.assertEqual(vb.venv_python(self.root), self.installer.venv_python(self.root))

    def test_the_worker_role_has_the_values_of_voice_worker(self):
        tree = ast.parse((ROOT / "voice_worker.py").read_text(encoding="utf-8"))
        constants = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
                with contextlib.suppress(ValueError):
                    constants[node.targets[0].id] = ast.literal_eval(node.value)
        self.assertEqual(constants, {"MAX_TEXT": vb.MAX_TEXT, "MAX_SECONDS": vb.WORKER_MAX_SECONDS,
                                     "EDGE_MS": vb.EDGE_MS, "SILENCE_FRACTION": vb.SILENCE_FRACTION,
                                     "PEAK_TARGET": vb.PEAK_TARGET, "STT_RATE": vb.STT_RATE})

    def test_the_manifest_is_written_field_for_field_like_the_installer(self):
        theirs, python = self.run_installer("medium")
        ours = vb.build_manifest(python, "medium", self.result)
        self.assertEqual(shape(ours), shape(theirs))
        stamp = r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?Z$"
        self.assertRegex(theirs.pop("installedAt"), stamp)
        self.assertRegex(ours.pop("installedAt"), stamp)
        self.assertEqual(ours, theirs)

    def test_an_installation_made_by_either_one_is_used_by_the_other(self):
        _manifest, python = self.run_installer()
        voice_file = self.root / "models" / "piper" / f"{vb.VOICE}.onnx"
        voice_file.write_bytes(b"onnx")
        loaded, problem = vb.load_manifest(self.root)
        self.assertIsNone(problem)
        self.assertEqual((loaded["_python"], loaded["_voice"], loaded["_model"]), (python, voice_file, "small"))
        engine = vb.LocalEngine(self.root)
        self.assertEqual(engine.info(), {"ready": True, "voice": vb.VOICE, "speakers": ["pierre", "jessica"],
                                         "stt": "small"})
        self.assertEqual(engine._command(), [str(python), str(BRIDGE), "--worker", "--voice", str(voice_file),
                                             "--whisper", "small",
                                             "--whisper-root", str(self.root / "models" / "whisper")])
        # The other way round: what the bridge writes, voice.py reads.
        ours = vb.build_manifest(python, "base", self.result)
        (self.root / "manifest.json").write_text(json.dumps(ours, ensure_ascii=False, indent=2) + "\n",
                                                 encoding="utf-8")
        local = voice.LocalVoice(self.root).manifest()
        self.assertEqual((local["_python"], local["_voice"], local["whisper"]["model"]), (python, voice_file, "base"))

    def test_what_is_missing_is_said(self):
        self.assertIn("pas encore installée", vb.load_manifest(self.root)[1])
        self.root.mkdir(parents=True)
        (self.root / "manifest.json").write_text("{pas du json", encoding="utf-8")
        self.assertIn("illisible", vb.load_manifest(self.root)[1])
        self.assertIsNone(vb.installed_model(self.root))
        _manifest, python = self.run_installer()
        self.assertIn("La voix de Piper manque", vb.load_manifest(self.root)[1])
        (self.root / "models" / "piper" / f"{vb.VOICE}.onnx").write_bytes(b"onnx")
        self.assertIsNone(vb.load_manifest(self.root)[1])
        shutil.rmtree(self.root / "models" / "whisper")
        self.assertIn("Le modèle de Whisper manque", vb.load_manifest(self.root)[1])
        python.unlink()
        self.assertIn("L'environnement Python de la voix manque", vb.load_manifest(self.root)[1])
        self.assertEqual(vb.installed_model(self.root), "small")  # kept for the repair


# ── Worker process ──────────────────────────────────────────────────────────

FAKE_WORKER = r'''
import json
import sys
import wave

with open(sys.argv[1], "a") as log:
    log.write("start\n")
if sys.argv[2:] == ["fail"]:
    print("Traceback (most recent call last):", file=sys.stderr)
    print("ModuleNotFoundError: No module named 'piper'", file=sys.stderr)
    sys.exit(1)
print("une bibliothèque qui parle sur la sortie standard", flush=True)
print(json.dumps({"ready": True, "speakers": {"jessica": 0, "pierre": 1}}), flush=True)
for line in sys.stdin:
    request = json.loads(line)
    if request["op"] == "tts":
        if request["text"] == "plante":
            sys.exit(3)
        with wave.open(request["path"], "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(22050)
            out.writeframes(b"\x01\x00" * (2205 if request["speaker"] == "pierre" else 1000))
        reply = {"ok": True, "took": 0.01}
    elif request["op"] == "stt":
        with wave.open(request["path"], "rb") as source:
            frames = source.getnframes()
        reply = {"ok": True, "text": " %d %s %s " % (frames, request["language"], request["prompt"])}
    else:
        reply = {"ok": False, "error": "unknown_op"}
    print(json.dumps(reply), flush=True)
'''


class LocalEngineTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.dir = Path(self.folder.name)
        self.script = self.dir / "fake_worker.py"
        self.script.write_text(FAKE_WORKER, encoding="utf-8")
        self.log = self.dir / "starts.log"
        self.messages = []

    def engine(self, *extra, idle=60):
        engine = vb.LocalEngine(self.dir / "voice", command=[sys.executable, str(self.script), str(self.log), *extra],
                                idle_seconds=idle, say=self.messages.append)
        self.addCleanup(engine.close)
        return engine

    def starts(self):
        return self.log.read_text().count("start") if self.log.exists() else 0

    def test_the_worker_starts_with_the_first_request_and_stays(self):
        engine = self.engine()
        self.assertEqual(self.starts(), 0)
        self.assertTrue(engine.info()["ready"])
        with wave.open(io.BytesIO(engine.speak("Bonjour", "pierre"))) as source:
            self.assertEqual((source.getframerate(), source.getnframes()), (22050, 2205))
        with wave.open(io.BytesIO(engine.speak("Bonjour", "jessica"))) as source:
            self.assertEqual(source.getnframes(), 1000)
        self.assertEqual(engine.transcribe(wav_bytes(1.0, 8000), "fr", "Vocabulaire : boucle"),
                         "8000 fr Vocabulaire : boucle")
        self.assertEqual(self.starts(), 1)
        self.assertEqual(self.messages[0], "Démarrage de Piper et Whisper…")
        self.assertRegex(self.messages[1], r"^Voix prête en \d+,\d s\.$")
        self.assertFalse(list(self.dir.glob("kenobi-voix-*")))

    def test_a_crashed_worker_starts_again_with_the_next_request(self):
        engine = self.engine()
        engine.speak("Bonjour", "pierre")
        with self.assertRaises(vb.VoiceBridgeError) as caught:
            engine.speak("plante", "pierre")
        self.assertEqual((caught.exception.status, caught.exception.kind), (503, "unavailable"))
        self.assertIn("redémarre", caught.exception.message)
        self.assertTrue(engine.speak("Encore", "pierre").startswith(b"RIFF"))
        self.assertEqual(self.starts(), 2)
        self.assertIn("La voix s'est arrêtée pendant une demande (code 3) : elle redémarrera à la prochaine.",
                      self.messages)
        self.assertFalse([message for message in self.messages if "plante" in message or "Bonjour" in message])

    def test_an_idle_worker_is_stopped_then_started_again(self):
        engine = self.engine(idle=0.3)
        engine.speak("Bonjour", "pierre")
        process = engine.worker.process
        deadline = time.monotonic() + 5
        while engine.worker is not None and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertIsNone(engine.worker)
        self.assertIsNotNone(process.wait(timeout=5))  # the idle watcher is terminating it
        self.assertTrue(any(message.startswith("Voix au repos") for message in self.messages))
        engine.speak("Re", "pierre")
        self.assertEqual(self.starts(), 2)

    def test_a_worker_that_cannot_start_says_why(self):
        engine = self.engine("fail")
        with self.assertRaises(vb.VoiceBridgeError) as caught:
            engine.speak("Bonjour", "pierre")
        self.assertEqual(caught.exception.status, 503)
        self.assertIn("ModuleNotFoundError: No module named 'piper'", caught.exception.message)
        self.assertIn("--installer", caught.exception.message)

    def test_a_missing_installation_or_program_is_explained(self):
        engine = vb.LocalEngine(self.dir / "voice")
        self.assertEqual(engine.info(), {"ready": False, "voice": vb.VOICE, "speakers": ["pierre", "jessica"],
                                         "stt": ""})
        with self.assertRaises(vb.VoiceBridgeError) as caught:
            engine.speak("Bonjour", "pierre")
        self.assertIn("pas encore installée", caught.exception.message)
        self.assertIn("Relance kenobi-voice.py", caught.exception.message)
        engine = vb.LocalEngine(self.dir, command=[str(self.dir / "python-absent")])
        with self.assertRaises(vb.VoiceBridgeError) as caught:
            engine.transcribe(wav_bytes(), "fr", "")
        self.assertIn("Impossible de lancer la voix", caught.exception.message)

    def test_closing_stops_the_worker_for_good(self):
        engine = self.engine()
        engine.speak("Bonjour", "pierre")
        process = engine.worker.process
        engine.close()
        self.assertIsNotNone(process.poll())
        with self.assertRaises(vb.VoiceBridgeError):
            engine.speak("Encore", "pierre")
        self.assertEqual(self.starts(), 1)


FAKE_PIPER = r'''
import os
import struct


class SynthesisConfig:
    def __init__(self, **options):
        self.__dict__.update(options)


class _Config:
    speaker_id_map = {"jessica": 0, "pierre": 1}
    sample_rate = 22050


class _Chunk:
    sample_rate = 22050

    def __init__(self, samples):
        self.audio_int16_bytes = struct.pack("<%dh" % len(samples), *samples)


class PiperVoice:
    config = _Config()

    @classmethod
    def load(cls, path):
        with open(path, "rb"):
            pass
        if os.environ.get("FAKE_PIPER_PID"):
            with open(os.environ["FAKE_PIPER_PID"], "w") as handle:
                handle.write(str(os.getpid()))
        return cls()

    def synthesize(self, text, syn_config=None):
        speaker = getattr(syn_config, "speaker_id", 0) or 0
        for word in text.split():
            tone = [4000 if index % 2 else -4000 for index in range(1000 * (1 + speaker) + 10 * len(word))]
            yield _Chunk([0] * 3000 + [7] * 20 + tone + [0] * 3000)
'''

FAKE_WHISPER = r'''
class _Segment:
    def __init__(self, text, no_speech_prob, avg_logprob):
        self.text, self.no_speech_prob, self.avg_logprob = text, no_speech_prob, avg_logprob


class WhisperModel:
    def __init__(self, model, device="cpu", compute_type="int8", cpu_threads=0, download_root=None,
                 local_files_only=False):
        if not local_files_only:
            raise RuntimeError("the worker must never download")
        self.model = model

    def transcribe(self, audio, language=None, initial_prompt=None, **options):
        text = " %s %s %d %s %s " % (self.model, language, len(audio), audio.dtype, initial_prompt)
        return iter([_Segment(text, 0.1, -0.2), _Segment(" Sous-titres ", 0.9, -1.5)]), None
'''


def write_fakes(folder):
    """Stand-ins for Piper and faster-whisper, with their interfaces: the worker role runs without models."""
    for package, source in (("piper", FAKE_PIPER), ("faster_whisper", FAKE_WHISPER)):
        (folder / package).mkdir(parents=True)
        (folder / package / "__init__.py").write_text(source, encoding="utf-8")
    return folder


@unittest.skipUnless(HAVE_NUMPY, "numpy absent")
class WorkerRoleTests(unittest.TestCase):
    """The --worker role of the bridge, and voice_worker.py, with stand-ins for Piper and faster-whisper."""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.dir = Path(self.folder.name)
        fakes = write_fakes(self.dir / "fakes")
        self.voice_file = self.dir / "voix.onnx"
        self.voice_file.write_bytes(b"onnx")
        (self.dir / "whisper").mkdir()
        patcher = mock.patch.dict(os.environ, {"PYTHONPATH": str(fakes)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def engine(self, *script):
        command = [sys.executable, *map(str, script), "--voice", str(self.voice_file), "--whisper", "small",
                   "--whisper-root", str(self.dir / "whisper")]
        engine = vb.LocalEngine(self.dir, command=command)
        self.addCleanup(engine.close)
        return engine

    def test_the_worker_role_speaks_and_hears(self):
        engine = self.engine(BRIDGE, "--worker")
        text = "Une boucle répète"
        audio = engine.speak(text, "pierre")
        with wave.open(io.BytesIO(audio)) as source:
            self.assertEqual((source.getnchannels(), source.getsampwidth(), source.getframerate()), (1, 2, 22050))
            frames = source.readframes(source.getnframes())
        samples = struct.unpack("<%dh" % (len(frames) // 2), frames)
        self.assertEqual(max(abs(value) for value in samples), vb.PEAK_TARGET)  # normalised once
        # Each word keeps its sound and 50 ms of silence on each side (instead of 136 ms).
        margin = 22050 * vb.EDGE_MS // 1000
        self.assertEqual(len(samples), sum(2 * margin + 2000 + 10 * len(word) for word in text.split()))
        jessica = engine.speak(text, "jessica")
        self.assertLess(len(jessica), len(audio))
        self.assertEqual(engine.speak(text, "inconnue"), jessica)  # the first voice
        self.assertEqual(engine.transcribe(wav_bytes(1.0, 8000, channels=2), "fr", "Vocabulaire : boucle"),
                         "small fr 16000 float32 Vocabulaire : boucle")  # mono, 16 kHz, float32, the noise left out
        self.assertEqual(engine.transcribe(wav_bytes(0.5), "auto", ""), "small None 8000 float32 None")
        with self.assertRaises(vb.VoiceBridgeError) as caught:
            engine.transcribe(wav_bytes(1.0, width=1), "fr", "")
        self.assertEqual(caught.exception.status, 400)
        with self.assertRaises(vb.VoiceBridgeError) as caught:
            engine.speak("   ", "pierre")
        self.assertEqual(caught.exception.status, 400)

    def test_the_worker_role_answers_exactly_like_voice_worker_py(self):
        ours = self.engine(BRIDGE, "--worker")
        theirs = self.engine(ROOT / "voice_worker.py")
        for voice_name in ("pierre", "jessica"):
            with self.subTest(voice=voice_name):
                self.assertEqual(ours.speak("Une boucle répète. Tu vois ?", voice_name),
                                 theirs.speak("Une boucle répète. Tu vois ?", voice_name))
        recording = wav_bytes(1.2, 11025, channels=2)
        self.assertEqual(ours.transcribe(recording, "fr", "Vocabulaire : liste"),
                         theirs.transcribe(recording, "fr", "Vocabulaire : liste"))


# ── Command line ────────────────────────────────────────────────────────────


@unittest.skipIf(WINDOWS, "signals and select on pipes")
class CommandLineTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.dir = Path(self.folder.name)
        self.voice = self.dir / "voice"
        (self.dir / "home").mkdir()
        self.env = {key: value for key, value in os.environ.items()
                    if key not in ("KENOBI_VOICE_ORIGINS", "KENOBI_VOICE_DIR", "DISPLAY", "WAYLAND_DISPLAY")}
        self.env.update(KENOBI_VOICE_HOME=str(self.dir / "config"), HOME=str(self.dir / "home"))

    def install_fake_voice(self, python=sys.executable):
        """A complete installation for the manifest; the worker would only start with a request."""
        (self.voice / "models" / "piper").mkdir(parents=True)
        (self.voice / "models" / "whisper").mkdir()
        (self.voice / "models" / "piper" / f"{vb.VOICE}.onnx").write_bytes(b"onnx")
        manifest = vb.build_manifest(python, "small", {"speakers": ["jessica", "pierre"], "tts_seconds": 0.1,
                                                       "stt_seconds": 0.2})
        (self.voice / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    def start(self, *arguments, script=BRIDGE, python=sys.executable):
        process = subprocess.Popen([python, str(script), "--dossier", str(self.voice), *arguments], env=self.env,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   bufsize=0)

        def cleanup():
            if process.poll() is None:
                process.terminate()
            process.wait(10)
            process.stdout.close()

        self.addCleanup(cleanup)
        return process

    def launch(self, *arguments, **options):
        process = self.start("--sans-navigateur", "--port", "0", *arguments, **options)
        match, lines = read_line_matching(process.stdout, r"^\s+(\d+)-([0-9a-f]{48})\s*$")
        return process, int(match.group(1)), match.group(2), "\n".join(lines)

    def stop(self, process):
        process.send_signal(signal.SIGINT)  # Ctrl+C
        self.assertEqual(process.wait(10), 0)
        return process.stdout.read().decode("utf-8")

    def test_the_code_comes_last_and_kenobi_opens_on_its_own_page(self):
        self.install_fake_voice()
        process, port, token, text = self.launch("--kenobi", "https://MotMot3000.github.io/Kenobi-tutor-IA/index.html")
        lines = text.splitlines()
        self.assertEqual(lines[0], "Kenobi · voix locale 1.0.0")
        self.assertIn(f"Ordinateur : {socket.gethostname()}", text)
        self.assertIn(f"Python {platform.python_version()}", text)
        self.assertIn(f"Voix : {self.voice} · Piper fr_FR-upmc-medium (Pierre, Jessica) · Whisper small", text)
        self.assertIn(f"Pont de la voix prêt sur 127.0.0.1:{port}", text)
        self.assertIn(f"Lien qui ouvre Kenobi déjà branché : {PAGE}#voix={port}-{token}", text)
        self.assertNotIn("github.io/#voix", text)
        self.assertEqual(lines[-2], "Code à coller dans Kenobi (réglages de la voix), si la page le demande :")
        token_file = self.dir / "config" / "token"
        self.assertEqual((vb.read_token(token_file), oct(token_file.stat().st_mode & 0o777)), (token, "0o600"))
        status, headers, body = get(port, "/health", {"Origin": "https://motmot3000.github.io"})
        self.assertEqual((status, headers["access-control-allow-origin"]), (200, "https://motmot3000.github.io"))
        self.assertEqual((json.loads(body)["ready"], json.loads(body)["stt"]), (True, "small"))
        self.assertEqual(get(port, "/health", {"Origin": "https://evil.test"})[0], 403)
        self.assertIn("Pont de la voix arrêté", self.stop(process))
        # The code survives a restart; --nouveau-jeton changes it.
        process, _, again, _ = self.launch()
        self.assertEqual(again, token)
        self.stop(process)
        process, _, renewed, _ = self.launch("--nouveau-jeton")
        self.assertNotEqual(renewed, token)

    def test_a_downloaded_copy_opens_the_page_that_downloaded_it(self):
        self.install_fake_voice()
        copy = self.dir / "Téléchargements" / "kenobi-voice.py"
        copy.parent.mkdir()
        copy.write_text(BRIDGE.read_text(encoding="utf-8").replace(MARKER, f"EMBEDDED_KENOBI_URL = {json.dumps(PAGE)}"),
                        encoding="utf-8")
        process, port, token, text = self.launch(script=copy)
        self.assertIn(f"Kenobi : {PAGE}", text)
        self.assertIn(f"{PAGE}#voix={port}-{token}", text)
        status, headers, _ = get(port, "/health", {"Origin": "https://motmot3000.github.io"})
        self.assertEqual((status, headers["access-control-allow-origin"]), (200, "https://motmot3000.github.io"))

    def test_without_the_voice_it_explains_and_asks_first(self):
        process = self.start("--port", "0")  # no terminal: no question can be asked
        self.assertEqual(process.wait(20), 1)
        text = process.stdout.read().decode("utf-8")
        self.assertIn("La voix locale n'est pas encore installée", text)
        self.assertIn(f"Au total, environ 1,1 Go, dans {self.voice}", text)
        self.assertIn("--oui", text)
        self.assertFalse(self.voice.exists())  # nothing downloaded, nothing created
        process = self.start("--port", "0", "--whisper", "medium")
        process.wait(20)
        self.assertIn("Whisper « medium » : environ 1530 Mo", process.stdout.read().decode("utf-8"))
        self.install_fake_voice()
        process = self.start("--port", "0", "--whisper", "base")
        self.assertEqual(process.wait(20), 1)
        self.assertIn("« small » devient « base »", process.stdout.read().decode("utf-8"))

    def test_a_missing_voice_python_is_said(self):
        self.install_fake_voice(python=self.dir / "venv-disparu" / "bin" / "python")
        process = self.start("--port", "0")
        self.assertEqual(process.wait(20), 1)
        self.assertIn("L'environnement Python de la voix manque", process.stdout.read().decode("utf-8"))

    def test_a_forced_busy_port_is_an_error(self):
        self.install_fake_voice()
        blocker = socket.socket()
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)
        self.addCleanup(blocker.close)
        port = blocker.getsockname()[1]
        process = self.start("--sans-navigateur", "--port", str(port))
        self.assertEqual(process.wait(20), 1)
        text = process.stdout.read().decode("utf-8")
        self.assertIn(f"Le pont de la voix n'a pas pu démarrer : le port {port} est pris", text)

    def test_bad_addresses_are_refused(self):
        self.install_fake_voice()
        process = self.start("--port", "0", "--kenobi", "https://example.github.io/?x=1")
        self.assertEqual(process.wait(20), 2)
        self.assertIn("Adresse de Kenobi illisible", process.stdout.read().decode("utf-8"))

    @unittest.skipUnless(HAVE_NUMPY, "numpy absent")
    def test_a_call_goes_through_the_worker_role_and_ctrl_c_stops_it(self):
        self.install_fake_voice()  # this Python plays the voice's Python; the worker is the bridge file itself
        self.env.update(PYTHONPATH=str(write_fakes(self.dir / "fakes")), FAKE_PIPER_PID=str(self.dir / "worker.pid"))
        process, port, token, _ = self.launch()
        self.assertFalse((self.dir / "worker.pid").exists())  # nothing loaded before the first request
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        status, answer, audio = post(port, "/v1/audio/speech", json.dumps({"input": "Une phrase privée"}), headers)
        self.assertEqual((status, answer["content-type"], audio[:4]), (200, "audio/wav", b"RIFF"))
        body = multipart([("file", wav_bytes(1.0, 8000))], "b")
        headers["Content-Type"] = "multipart/form-data; boundary=b"
        status, _, data = post(port, "/v1/audio/transcriptions", body, headers)
        self.assertEqual((status, json.loads(data)), (200, {"text": "small fr 16000 float32 None"}))
        worker = int((self.dir / "worker.pid").read_text())
        os.kill(worker, 0)
        text = self.stop(process)
        with self.assertRaises(ProcessLookupError):
            os.kill(worker, 0)
        self.assertIn("Démarrage de Piper et Whisper…", text)
        self.assertIn("Un programme de cet ordinateur utilise la voix.", text)
        self.assertNotIn("privée", text)

    def test_python_3_8_runs_the_server(self):
        python = old_python("3.8")
        if not python:
            self.skipTest("Python 3.8 absent")
        self.install_fake_voice()
        process, port, _, _ = self.launch(python=python)
        status, _, body = get(port, "/health")
        self.assertEqual((status, json.loads(body)["app"]), (200, "kenobi-voice"))
        self.stop(process)


class AnnounceTests(unittest.TestCase):
    def test_kenobi_opens_already_paired_only_when_it_should(self):
        token = "a" * 48
        bridge = vb.VoiceBridge(FakeEngine(), port=8196, token_file=Path(tempfile.gettempdir()) / "absent" / "token")
        bridge.port, bridge.token = 8196, token
        manifest = {"piper": {"voice": vb.VOICE}, "_model": "small"}
        page = vb.kenobi_page("https://motmot3000.github.io/Kenobi-tutor-IA/index.html")
        link = f"{PAGE}#voix=8196-{token}"
        cases = (
            ({"created": True}, True),
            ({"created": False}, False),
            ({"created": False, "brancher": True}, True),
            ({"created": True, "sans": True}, False),
            ({"created": True, "display": False}, False),
        )
        for options, opened in cases:
            with self.subTest(**options):
                bridge.token_created = options["created"]
                args = argparse.Namespace(brancher=options.get("brancher", False),
                                          sans_navigateur=options.get("sans", False))
                urls = []
                written = io.StringIO()
                with mock.patch.object(vb, "_open_browser", side_effect=lambda url: urls.append(url) or True), \
                        mock.patch.object(vb, "_can_open_browser", return_value=options.get("display", True)), \
                        contextlib.redirect_stdout(written):
                    vb._announce(bridge, manifest, Path("/voix"), page, args)
                self.assertEqual(urls, [link] if opened else [])
                text = written.getvalue()
                self.assertIn("Kenobi s'ouvre dans ton navigateur, déjà branché." if opened
                              else f"Lien qui ouvre Kenobi déjà branché : {link}", text)
                self.assertEqual(text.splitlines()[-1], f"  8196-{token}")

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux")
    def test_no_browser_without_a_screen(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("DISPLAY", None)
            os.environ.pop("WAYLAND_DISPLAY", None)
            self.assertFalse(vb._can_open_browser())
            os.environ["WAYLAND_DISPLAY"] = "wayland-0"
            self.assertTrue(vb._can_open_browser())


def old_python(version):
    """An installed Python `version` (on the PATH, or found by uv without downloading), or None."""
    python = shutil.which(f"python{version}")
    if python or not shutil.which("uv"):
        return python
    try:
        result = subprocess.run(["uv", "python", "find", version], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                text=True, timeout=20,
                                env=dict(os.environ, UV_PYTHON_DOWNLOADS="never", UV_OFFLINE="1"))
    except (OSError, subprocess.SubprocessError):
        return None
    path = result.stdout.strip()
    return path if result.returncode == 0 and path and os.path.isfile(path) else None


class CompatibilityTests(unittest.TestCase):
    def test_the_bridge_is_python_3_8(self):
        source = BRIDGE.read_text(encoding="utf-8")
        ast.parse(source, feature_version=(3, 8))  # no match statement, no newer grammar
        for newer in (".removeprefix(", ".removesuffix(", "functools.cache", "strict=True", "math.lcm"):
            self.assertNotIn(newer, source)

    def test_python_3_8_and_3_9_compile_the_bridge(self):
        found = [python for python in (old_python("3.8"), old_python("3.9")) if python]
        if not found:
            self.skipTest("ni Python 3.8 ni 3.9 ici")
        for python in found:
            with self.subTest(python=python):
                check = "import sys; compile(open(sys.argv[1], encoding='utf-8').read(), sys.argv[1], 'exec')"
                result = subprocess.run([python, "-c", check, str(BRIDGE)], stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout)


if __name__ == "__main__":
    unittest.main()
