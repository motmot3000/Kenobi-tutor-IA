import asyncio
import base64
import contextlib
import http.client
import io
import json
import os
import re
import select
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import terminal_bridge as bridge_module
from terminal_bridge import (
    BridgeError,
    CommandTracker,
    TerminalBridge,
    clean_output,
    encode_frame,
    read_token,
    unescape_mark,
    unmask,
    websocket_accept,
    write_token,
)

ROOT = Path(__file__).resolve().parent.parent
ORIGIN = "http://127.0.0.1:8193"


def mark(payload):
    return b"\x1b]633;" + payload.encode("utf-8") + b"\x07"


class HelperTests(unittest.TestCase):
    def test_websocket_accept_matches_rfc_6455_example(self):
        self.assertEqual(websocket_accept("dGhlIHNhbXBsZSBub25jZQ=="), "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")

    def test_masked_frames_round_trip(self):
        payload = "é" * 70000
        frame = encode_frame(0x1, payload.encode("utf-8"), mask=b"\x01\x02\x03\x04")
        self.assertEqual(frame[1] & 0x7F, 127)
        self.assertEqual(unmask(frame[14:], b"\x01\x02\x03\x04").decode("utf-8"), payload)
        self.assertEqual(unmask(b"", b"abcd"), b"")

    def test_clean_output_removes_colors_and_applies_carriage_returns(self):
        raw = b"\x1b[31mErreur\x1b[0m\r\nprogress 10%\rprogress 99%\r\nab\x08c\n/home/thomas/x.py\n\x1b(B\x1b=ok\x1b>\n\n"
        self.assertEqual(
            clean_output(raw, home="/home/thomas"),
            "Erreur\nprogress 99%\nac\n~/x.py\nok",
        )

    def test_mark_escapes_are_decoded(self):
        self.assertEqual(unescape_mark(r"a\\b\nc"), "a\\b\nc")

    def test_token_file_must_be_private(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sub" / "token"
            write_token(path, "a" * 48)
            self.assertEqual(oct(path.stat().st_mode & 0o777), "0o600")
            self.assertEqual(read_token(path), "a" * 48)
            os.chmod(path, 0o644)
            self.assertIsNone(read_token(path))
            self.assertIsNone(read_token(Path(folder) / "absent"))

    def test_local_origins_are_this_computer_the_lan_or_the_tailnet(self):
        local = (
            "http://127.0.0.1:8192", "http://localhost:8192", "http://[::1]:8192", "http://192.168.1.220:8192",
            "http://10.0.0.5:8192", "http://172.20.1.2:8192", "http://100.120.165.95:8192", "http://169.254.3.4",
            "http://minix-de-tom:8192", "http://minix-de-tom.local:8192", "http://minix-de-tom.tail1234.ts.net:8192",
            "http://kenobi.lan", "http://kenobi.home.arpa", "http://[fd7a:115c:a1e0::1]:8192", "http://[fe80::1]",
        )
        public = (
            "http://evil.test", "https://kenobi.example.com", "http://8.8.8.8:8192", "http://100.128.0.1",
            "http://[2001:4860::8888]", "http://ts.net.evil.test", "javascript:alert(1)", "", None,
        )
        for origin in local:
            with self.subTest(origin=origin):
                self.assertTrue(bridge_module.local_origin(origin))
        for origin in public:
            with self.subTest(origin=origin):
                self.assertFalse(bridge_module.local_origin(origin))

    def test_exit_code_works_without_waitstatus_to_exitcode(self):
        status = 3 << 8  # exit(3) selon waitpid
        self.assertEqual(bridge_module.exit_code(status), 3)
        saved = getattr(os, "waitstatus_to_exitcode", None)
        if saved:
            del os.waitstatus_to_exitcode  # comme Python 3.8
        try:
            self.assertEqual(bridge_module.exit_code(status), 3)
            self.assertEqual(bridge_module.exit_code(9), -9)  # tué par SIGKILL
        finally:
            if saved:
                os.waitstatus_to_exitcode = saved

    def test_kenobi_opens_in_firefox_when_it_is_there(self):
        with mock.patch.object(bridge_module, "_firefox_command", return_value=[sys.executable, "-c", "pass"]), \
                mock.patch.object(bridge_module.webbrowser, "open") as default:
            self.assertEqual(bridge_module._open_browser("http://192.168.1.220:8192/#terminal=8194-x"), "Firefox")
            default.assert_not_called()
        with mock.patch.object(bridge_module, "_firefox_command", return_value=None), \
                mock.patch.object(bridge_module.webbrowser, "open", return_value=True) as default:
            self.assertEqual(bridge_module._open_browser("http://kenobi/"), "ton navigateur")
            default.assert_called_once_with("http://kenobi/")
        with mock.patch.object(bridge_module, "_firefox_command", return_value=None), \
                mock.patch.object(bridge_module.webbrowser, "open", side_effect=RuntimeError):
            self.assertEqual(bridge_module._open_browser("http://kenobi/"), "")

    def test_a_refusal_is_written_once_however_often_a_tab_insists(self):
        with tempfile.TemporaryDirectory() as folder:
            bridge = TerminalBridge(port=0, workspace=folder, origins=ORIGIN, token_file=Path(folder) / "token",
                                    announce=True)
            written = io.StringIO()
            with contextlib.redirect_stdout(written):
                for _ in range(5):
                    bridge._refusal("token", "Refusé : ancien code.", "  8194-…")
                bridge._refusal("origin", "Refusé : adresse.")
            self.assertEqual(written.getvalue().splitlines(), ["Refusé : ancien code.", "  8194-…", "Refusé : adresse."])

    def test_a_token_renewed_by_another_bridge_is_picked_up(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "token"
            bridge = TerminalBridge(port=0, workspace=folder, origins=ORIGIN, token_file=path)
            first = bridge.load_or_create_token()
            self.assertEqual(bridge.current_token(), first)
            time.sleep(0.01)
            write_token(path, "c" * 48)
            self.assertEqual(bridge.current_token(), "c" * 48)
            os.chmod(path, 0o644)  # fichier devenu lisible par d'autres : ignoré, le jeton en mémoire reste
            os.utime(path, None)
            self.assertEqual(bridge.current_token(), "c" * 48)


class TrackerTests(unittest.TestCase):
    def test_command_is_captured_even_when_marks_are_split(self):
        tracker = CommandTracker(home="/home/thomas")
        stream = (
            mark("P;Cwd=/home/thomas/Kenobi") + mark("A") + b"$ "
            + mark("E;python3 exercice.py") + mark("C")
            + b"Traceback (most recent call last):\r\n  File \"/home/thomas/Kenobi/exercice.py\", line 2\r\n"
            + b"NameError: name 'x' is not defined\r\n"
            + mark("D;1") + mark("A") + b"$ "
        )
        shown = bytearray()
        events = []
        for index in range(0, len(stream), 7):
            visible, found = tracker.feed(stream[index:index + 7])
            shown += visible
            events += found
        self.assertNotIn(b"633", bytes(shown))
        self.assertIn(b"NameError", bytes(shown))
        kinds = [event["t"] for event in events]
        self.assertEqual(kinds, ["cwd", "state", "command-start", "command-done", "state"])
        done = events[3]
        self.assertEqual(done["cmd"], "python3 exercice.py")
        self.assertEqual(done["exit"], 1)
        self.assertEqual(done["cwd"], "~/Kenobi")
        self.assertTrue(done["output"].startswith("Traceback"))
        self.assertIn('File "~/Kenobi/exercice.py", line 2', done["output"])
        self.assertTrue(tracker.idle)

    def test_long_output_keeps_head_and_tail(self):
        tracker = CommandTracker(home="/home/thomas")
        tracker.feed(mark("E;python3 boucle.py") + mark("C"))
        tracker.feed(b"debut\n" + b"x" * (bridge_module.CAPTURE_HEAD + bridge_module.CAPTURE_TAIL) + b"\nfin\n")
        _, events = tracker.feed(mark("D;0"))
        self.assertTrue(events[0]["truncated"])
        self.assertTrue(events[0]["output"].startswith("debut"))
        self.assertTrue(events[0]["output"].endswith("fin"))
        self.assertIn("[...]", events[0]["output"])


class FileTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.bridge = TerminalBridge(port=0, workspace=self.folder.name, origins=ORIGIN,
                                     token_file=Path(self.folder.name) / "token")

    def tearDown(self):
        self.folder.cleanup()

    def test_files_stay_inside_the_lesson_folder(self):
        saved = self.bridge.save_file("mimo-basics", "exercice.py", "print('ok')\n")
        self.assertTrue(saved["path"].endswith("mimo-basics/exercice.py"))
        self.assertEqual(self.bridge.open_file("mimo-basics", "exercice.py")["code"], "print('ok')\n")
        self.assertFalse(self.bridge.open_file("mimo-basics", "autre.py")["exists"])
        self.assertEqual([item["name"] for item in self.bridge.list_files("mimo-basics")["files"]], ["exercice.py"])
        for lesson, name in (("../x", "a.py"), ("mimo", "../a.py"), ("mimo", ".cache.py"), ("mimo", "a.exe"), ("Mimo", "a.py")):
            with self.subTest(lesson=lesson, name=name), self.assertRaises(BridgeError):
                self.bridge.file_path(lesson, name)

    def test_each_course_has_its_own_folder_next_to_the_python_one(self):
        root = Path(self.folder.name)
        python_home = root / "Kenobi" / "python"
        bridge = TerminalBridge(port=0, workspace=python_home, origins=ORIGIN, token_file=root / "token")
        # Le parcours Python d'origine garde son dossier ; un cours importé a le sien, à côté.
        self.assertEqual(bridge.lesson_dir("mimo-basics"), python_home.resolve() / "mimo-basics")
        self.assertEqual(bridge.lesson_dir("mimo-basics", "python"), python_home.resolve() / "mimo-basics")
        self.assertEqual(bridge.lesson_dir("git-0101-depot", "git"), (root / "Kenobi" / "git").resolve() / "git-0101-depot")
        saved = bridge.save_file("git-0101-depot", "exercice.sh", "git status\n", "git")
        self.assertTrue(saved["path"].endswith("Kenobi/git/git-0101-depot/exercice.sh"))
        self.assertEqual(bridge.open_file("git-0101-depot", "exercice.sh", "git")["code"], "git status\n")
        self.assertEqual([item["name"] for item in bridge.list_files("git-0101-depot", "git")["files"]], ["exercice.sh"])
        self.assertFalse(bridge.open_file("git-0101-depot", "exercice.sh")["exists"])  # pas dans le dossier Python
        for course in ("../x", "Git", "a/b", 3):
            with self.subTest(course=course), self.assertRaises(BridgeError):
                bridge.lesson_dir("lecon", course)
        # Un dossier choisi à la main qui ne s'appelle pas « python » accueille les autres cours dedans.
        self.assertEqual(self.bridge.lesson_dir("l1", "git"), Path(self.folder.name).resolve() / "git" / "l1")

    def test_run_picks_the_interpreter_from_the_file(self):
        python = self.bridge.python_cmd or "python3"
        for name, runner in (("exercice.py", python), ("app.js", "node"), ("script.sh", "bash"), ("main.go", "go run")):
            with self.subTest(name=name):
                self.assertTrue(self.bridge.run_command("lecon", name, "cours").endswith(f"&& {runner} {name}"))
        with self.assertRaises(BridgeError):
            self.bridge.run_command("lecon", "notes.txt")
        with self.assertRaises(BridgeError):
            self.bridge.start_run("lecon", "donnees.csv", "a,b\n")
        self.assertFalse((Path(self.folder.name) / "lecon" / "donnees.csv").exists())  # rien d'écrit pour un Run refusé

    def test_run_command_changes_folder_only_when_needed(self):
        command = self.bridge.run_command("mimo-basics", "exercice.py")
        python = self.bridge.python_cmd or "python3"
        self.assertTrue(command.startswith("cd "))
        self.assertTrue(command.endswith(f"&& {python} exercice.py"))

    def test_powershell_and_cmd_get_their_own_syntax(self):
        target = Path(self.folder.name) / "mimo-basics"
        python = self.bridge.python_cmd or "python3"
        self.bridge.shell_kind = "pwsh"
        self.assertEqual(self.bridge.run_command("mimo-basics", "exercice.py"),
                         f"cd {bridge_module.powershell_path(target.resolve())}; {python} exercice.py")
        self.bridge.shell_kind = "cmd"
        self.assertEqual(self.bridge.run_command("mimo-basics", "exercice.py"),
                         f'cd /d "{target.resolve()}" && {python} exercice.py')
        self.assertEqual(bridge_module.powershell_path("/home/t/Kenobi/python", home="/home/t"), "~/Kenobi/python")
        self.assertEqual(bridge_module.powershell_path("/srv/it's here", home="/home/t"), "'/srv/it''s here'")

    def test_powershell_receives_its_integration_without_a_script_file(self):
        self.bridge.shell, self.bridge.shell_kind = "/usr/bin/pwsh", "pwsh"
        argv, _ = self.bridge.shell_command()
        self.assertEqual(argv[:4], ["/usr/bin/pwsh", "-NoLogo", "-NoExit", "-EncodedCommand"])
        script = base64.b64decode(argv[4]).decode("utf-16-le")
        self.assertIn("633;E;", script)
        self.assertIn("function Global:prompt", script)

    def test_origins_are_strict(self):
        self.assertEqual(bridge_module.normalize_origin("HTTP://192.168.1.220:8192/"), "http://192.168.1.220:8192")
        for value in ("http://evil.test/path", "javascript:alert(1)", "http://a b", "", None):
            with self.subTest(value=value):
                self.assertIsNone(bridge_module.normalize_origin(value))


def bridge_environment(root, shell=None, extra=None):
    home = root / "home"
    home.mkdir(exist_ok=True)
    env = dict(os.environ)
    env.update(
        HOME=str(home),
        KENOBI_TERMINAL_HOME=str(root / "config"),
        KENOBI_WORKSPACE=str(root / "Kenobi"),
    )
    if shell:
        env["KENOBI_TERMINAL_SHELL"] = shell
    env.pop("ZDOTDIR", None)
    env.update(extra or {})
    return env


class BridgeProcess:
    """Pont lancé comme le service du MINIX, dans un processus à part, avec un dossier et un jeton temporaires."""

    def __init__(self, shell):
        self.folder = tempfile.TemporaryDirectory()
        root = Path(self.folder.name)
        self.workspace = root / "Kenobi"
        self.token_file = root / "run" / "token"
        env = bridge_environment(root, shell, {
            "KENOBI_TERMINAL_PORT": "0",
            "KENOBI_TERMINAL_TOKEN_FILE": str(self.token_file),
            "KENOBI_TERMINAL_ORIGINS": ORIGIN,
        })
        self.process = subprocess.Popen(
            [sys.executable, str(ROOT / "terminal_bridge.py"), "--service"],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        line = self.process.stdout.readline()
        match = re.search(r"127\.0\.0\.1:(\d+)", line)
        if not match:
            self.stop()
            raise RuntimeError(f"Pont non démarré : {line!r} {self.process.stderr.read()}")
        self.port = int(match.group(1))
        self.token = read_token(self.token_file)

    def stop(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.process.stdout.close()
        self.process.stderr.close()
        self.folder.cleanup()


class Client:
    def __init__(self, port, *, origin=ORIGIN, token="", host=None, protocol="kenobi-terminal.v1"):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=15)
        key = base64.b64encode(os.urandom(16)).decode()
        offered = ", ".join(item for item in (protocol, f"token.{token}") if item)
        request = (
            "GET /terminal HTTP/1.1\r\n"
            f"Host: {host or f'127.0.0.1:{port}'}\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Version: 13\r\n"
            f"Sec-WebSocket-Key: {key}\r\nOrigin: {origin}\r\n"
            f"Sec-WebSocket-Protocol: {offered}\r\n\r\n"
        )
        self.sock.sendall(request.encode())
        self.buffer = b""
        head = self._read_until(b"\r\n\r\n")
        self.status = int(head.split(b" ")[1])
        self.output = bytearray()
        self.events = []

    def _read_until(self, marker):
        while marker not in self.buffer:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("connexion fermée")
            self.buffer += chunk
        head, _, self.buffer = self.buffer.partition(marker)
        return head

    def _read(self, size):
        while len(self.buffer) < size:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("connexion fermée")
            self.buffer += chunk
        data, self.buffer = self.buffer[:size], self.buffer[size:]
        return data

    def send(self, value):
        self.sock.sendall(encode_frame(0x1, json.dumps(value).encode("utf-8"), mask=os.urandom(4)))

    def frame(self):
        header = self._read(2)
        size = header[1] & 0x7F
        if size == 126:
            size = struct.unpack("!H", self._read(2))[0]
        elif size == 127:
            size = struct.unpack("!Q", self._read(8))[0]
        return header[0] & 0x0F, self._read(size)

    def denial(self):
        """Refus expliqué par le pont : (message « denied », code de fermeture)."""
        event = None
        while True:
            opcode, payload = self.frame()
            if opcode == 0x1:
                event = json.loads(payload)
            elif opcode == 0x8:
                return event, struct.unpack("!H", payload[:2])[0]

    def wait_for(self, predicate, timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            opcode, payload = self.frame()
            if opcode == 0x2:
                self.output += payload
                # Like xterm.js, answer the cursor position requests (PowerShell waits for them).
                for _ in range(payload.count(b"\x1b[6n")):
                    self.send({"t": "input", "d": "\x1b[1;1R"})
            elif opcode == 0x1:
                event = json.loads(payload)
                self.events.append(event)
                if predicate(event):
                    return event
            elif opcode == 0x8:
                raise ConnectionError("fermeture par le pont")
        raise TimeoutError("évènement attendu absent")

    def close(self):
        self.sock.close()


class LiveBridgeMixin:
    shell = None
    echo = "echo kenobi-$((40+2))"

    def setUp(self):
        if not shutil.which(self.shell):
            self.skipTest(f"{self.shell} absent")
        self.bridge = BridgeProcess(shutil.which(self.shell))

    def tearDown(self):
        self.bridge.stop()

    def connect(self):
        client = Client(self.bridge.port, token=self.bridge.token)
        self.assertEqual(client.status, 101)
        ready = client.wait_for(lambda event: event["t"] == "ready")
        self.assertEqual(ready["shell"], self.shell)
        client.wait_for(lambda event: event["t"] == "state" and event.get("integration"), timeout=30)
        return client

    def test_commands_runs_and_files_are_reported(self):
        client = self.connect()
        client.send({"t": "input", "d": f"{self.echo}\r"})
        done = client.wait_for(lambda event: event["t"] == "command-done", timeout=30)
        self.assertEqual(done["cmd"], self.echo)
        self.assertEqual(done["exit"], 0)
        self.assertEqual(done["output"], "kenobi-42")
        self.assertEqual(done["source"], "shell")

        code = "nom = 'Tom'\nprint(nom.uper())\n"
        client.send({"t": "run", "id": "r1", "lesson": "mimo-types", "file": "exercice.py", "code": code})
        started = client.wait_for(lambda event: event.get("id") == "r1")
        self.assertEqual(started["t"], "run-started")
        done = client.wait_for(lambda event: event["t"] == "command-done")
        self.assertEqual(done["source"], "run")
        self.assertEqual(done["runId"], started["runId"])
        self.assertEqual(done["code"], code)
        self.assertNotEqual(done["exit"], 0)
        self.assertIn("AttributeError", done["output"])
        self.assertEqual((self.bridge.workspace / "mimo-types" / "exercice.py").read_text(), code)

        # Second Run from the lesson folder: no cd any more.
        client.send({"t": "run", "id": "r2", "lesson": "mimo-types", "file": "exercice.py", "code": "print('ok')\n"})
        started = client.wait_for(lambda event: event.get("id") == "r2")
        self.assertFalse(started["command"].startswith("cd "))
        done = client.wait_for(lambda event: event["t"] == "command-done")
        self.assertEqual((done["exit"], done["output"]), (0, "ok"))

        client.send({"t": "input", "d": "python3 exercice.py\r"})
        done = client.wait_for(lambda event: event["t"] == "command-done")
        self.assertEqual(done["source"], "shell")
        self.assertEqual((done["file"], done["code"]), ("exercice.py", "print('ok')\n"))

        client.send({"t": "list", "id": "l1", "lesson": "mimo-types"})
        files = client.wait_for(lambda event: event.get("id") == "l1")
        self.assertEqual([item["name"] for item in files["files"]], ["exercice.py"])
        client.send({"t": "open", "id": "o1", "lesson": "../etc", "file": "passwd.py"})
        refused = client.wait_for(lambda event: event.get("id") == "o1")
        self.assertEqual(refused["t"], "error")
        client.close()

    def test_busy_terminal_refuses_run_and_reconnection_replays(self):
        client = self.connect()
        client.send({"t": "input", "d": "python3 -c 'input()'\r"})
        client.wait_for(lambda event: event["t"] == "command-start")
        client.send({"t": "run", "id": "r1", "lesson": "mimo-types", "file": "exercice.py", "code": "print(1)\n"})
        refused = client.wait_for(lambda event: event.get("id") == "r1")
        self.assertEqual((refused["t"], refused["reason"]), ("run-refused", "busy"))
        client.send({"t": "interrupt"})
        done = client.wait_for(lambda event: event["t"] == "command-done")
        self.assertNotEqual(done["exit"], 0)
        client.send({"t": "input", "d": "echo replay-marker\r"})
        client.wait_for(lambda event: event["t"] == "command-done")
        client.close()

        again = Client(self.bridge.port, token=self.bridge.token)
        again.wait_for(lambda event: event["t"] == "ready")
        deadline = time.monotonic() + 5
        while b"replay-marker" not in again.output and time.monotonic() < deadline:
            again.sock.settimeout(1)
            try:
                opcode, payload = again.frame()
            except socket.timeout:
                break
            if opcode == 0x2:
                again.output += payload
        self.assertIn(b"replay-marker", bytes(again.output))
        again.close()

    def test_wrong_origin_token_or_host_are_refused(self):
        # Sans explication : hôte qui n'est pas la boucle locale (rebond DNS), client qui n'est pas Kenobi,
        # page d'Internet sans le code.
        attempts = (
            {"token": self.bridge.token, "host": f"evil.test:{self.bridge.port}"},
            {"token": self.bridge.token, "protocol": ""},
            {"token": "0" * 48, "origin": "http://evil.test"},
            {"token": "", "origin": "http://evil.test"},
        )
        for options in attempts:
            with self.subTest(**options):
                client = Client(self.bridge.port, **options)
                self.assertEqual(client.status, 403)
                client.close()
        # Expliqué : code faux depuis une page locale, page d'Internet avec le bon code.
        for options, reason, code in (
            ({"token": "0" * 48}, "token", 4401),
            ({"token": self.bridge.token, "origin": "https://kenobi.example.com"}, "origin", 4403),
        ):
            with self.subTest(reason=reason):
                client = Client(self.bridge.port, **options)
                self.assertEqual(client.status, 101)
                event, closed = client.denial()
                self.assertEqual((event["t"], event["reason"], closed), ("denied", reason, code))
                self.assertNotIn(self.bridge.token, json.dumps(event))
                client.close()

    def test_any_address_of_kenobi_on_this_network_enters_with_the_code(self):
        # Le pont n'a été réglé que pour ORIGIN : les autres adresses locales de Kenobi entrent avec le jeton.
        for origin in ("http://192.168.1.220:8192", "http://100.120.165.95:8192", "http://minix-de-tom:8192",
                       "http://minix-de-tom.tail1234.ts.net:8192"):
            with self.subTest(origin=origin):
                client = Client(self.bridge.port, token=self.bridge.token, origin=origin)
                self.assertEqual(client.status, 101)
                self.assertEqual(client.wait_for(lambda event: event["t"] == "ready")["protocol"], 2)
                client.close()

    def test_health_is_readable_only_by_pages_allowed_in(self):
        def health(method="GET", origin=None, host=None):
            connection = http.client.HTTPConnection("127.0.0.1", self.bridge.port, timeout=5)
            headers = {"Host": host or f"127.0.0.1:{self.bridge.port}"}
            if origin:
                headers["Origin"] = origin
            connection.request(method, "/health", headers=headers)
            response = connection.getresponse()
            result = response.status, {k.lower(): v for k, v in response.getheaders()}, response.read()
            connection.close()
            return result

        status, headers, body = health(origin="http://192.168.1.220:8192")
        value = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(headers["access-control-allow-origin"], "http://192.168.1.220:8192")
        self.assertEqual((value["app"], value["protocol"], value["service"]), ("kenobi-terminal", 2, True))
        self.assertNotIn(self.bridge.token, body.decode())
        for options in ({"origin": "http://evil.test"}, {"origin": ORIGIN, "host": "evil.test"}, {}):
            with self.subTest(**options):
                self.assertNotIn("access-control-allow-origin", health(**options)[1])
        status, headers, _ = health("OPTIONS", origin=ORIGIN)
        self.assertEqual((status, headers["access-control-allow-origin"]), (204, ORIGIN))
        self.assertEqual(headers["access-control-allow-private-network"], "true")


class ZshBridgeTests(LiveBridgeMixin, unittest.TestCase):
    shell = "zsh"


class BashBridgeTests(LiveBridgeMixin, unittest.TestCase):
    shell = "bash"


class PwshBridgeTests(LiveBridgeMixin, unittest.TestCase):
    """PowerShell 7 : même intégration que sous Windows (prompt et PSConsoleHostReadLine)."""

    shell = "pwsh"
    echo = 'echo "kenobi-$(40+2)"'


def read_line_matching(stream, pattern, timeout=15):
    """Lit la sortie du pont ligne à ligne, sans tampon Python (select voit alors chaque ligne)."""
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


class OtherComputerTests(unittest.TestCase):
    """Le pont lancé à la main sur un autre ordinateur : il retient Kenobi, garde son jeton et affiche un code."""

    kenobi = "http://127.0.0.2:8193"

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.env = bridge_environment(self.root, shutil.which("bash") or "/bin/sh", {"KENOBI_TERMINAL_PORT": "0"})

    def tearDown(self):
        self.folder.cleanup()

    def start(self, *arguments):
        process = subprocess.Popen(
            [sys.executable, str(ROOT / "terminal_bridge.py"), *arguments],
            env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0,
        )
        self.addCleanup(lambda: (process.poll() is None and process.terminate(), process.wait(5),
                                 process.stdout.close(), process.stderr.close()))
        return process

    def launch(self, *arguments):
        process = self.start(*arguments, "--sans-navigateur")
        match, lines = read_line_matching(process.stdout, r"^\s+(\d+)-([0-9a-f]{48})\s*$")
        return process, int(match.group(1)), match.group(2), "\n".join(lines)

    def test_the_service_leaves_the_remembered_addresses_alone(self):
        settings = self.root / "config" / "settings.json"
        bridge_module.write_private(settings, json.dumps({"origins": [self.kenobi]}))
        process = self.start("--service", "--kenobi", "http://127.0.0.3:8193")
        read_line_matching(process.stdout, r"sur 127\.0\.0\.1:\d+")
        self.assertEqual(json.loads(settings.read_text())["origins"], [self.kenobi])

    def test_pairing_code_origin_and_token_survive_a_restart(self):
        process, port, token, text = self.launch("--kenobi", f"{self.kenobi}/")
        self.assertIn(f"Pont prêt sur 127.0.0.1:{port}", text)
        self.assertIn(f"{self.kenobi}/#terminal={port}-{token}", text)
        settings = json.loads((self.root / "config" / "settings.json").read_text())
        self.assertEqual(settings["origins"], [self.kenobi])
        self.assertEqual(read_token(self.root / "config" / "token"), token)

        # L'adresse retenue, puis une autre adresse locale de Kenobi : toutes deux entrent avec le code.
        for origin in (self.kenobi, "http://127.0.0.3:8193"):
            client = Client(port, token=token, origin=origin)
            self.assertEqual(client.status, 101)
            self.assertEqual(client.wait_for(lambda event: event["t"] == "ready")["t"], "ready")
            client.close()
        match, _ = read_line_matching(process.stdout, r"Kenobi est branché \((http://127\.0\.0\.3:8193)\)")
        self.assertEqual(match.group(1), "http://127.0.0.3:8193")

        # Un ancien code : refus expliqué à la page, et la fenêtre du pont redonne le bon code.
        refused = Client(port, token="0" * 48, origin=self.kenobi)
        self.assertEqual(refused.denial()[1], 4401)
        refused.close()
        match, _ = read_line_matching(process.stdout, r"^\s+(\d+-[0-9a-f]{48})\s*$")
        self.assertEqual(match.group(1), f"{port}-{token}")
        process.terminate()
        process.wait(5)

        _, _, again, _ = self.launch()
        self.assertEqual(again, token)
        _, _, renewed, _ = self.launch("--nouveau-jeton")
        self.assertNotEqual(renewed, token)

    def test_an_older_copy_points_to_the_newer_one(self):
        downloads = self.root / "Téléchargements"
        downloads.mkdir()
        old = downloads / "kenobi-terminal.py"
        new = downloads / "kenobi-terminal(1).py"
        source = (ROOT / "terminal_bridge.py").read_text(encoding="utf-8")
        old.write_text(source, encoding="utf-8")
        new.write_text(source, encoding="utf-8")
        os.utime(old, (time.time() - 3600, time.time() - 3600))
        process = subprocess.Popen([sys.executable, str(old), "--sans-navigateur"], env=self.env,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        self.addCleanup(lambda: (process.poll() is None and process.terminate(), process.wait(5),
                                 process.stdout.close(), process.stderr.close()))
        match, _ = read_line_matching(process.stdout, r"copie plus récente du pont est à côté de celle-ci \((.+)\)")
        self.assertEqual(match.group(1), "kenobi-terminal(1).py")

    def test_a_running_bridge_is_recognised_and_its_port_left_alone(self):
        _, port, _, _ = self.launch()
        found = bridge_module._bridge_on(port)
        self.assertEqual((found["app"], found["version"], found["service"]), ("kenobi-terminal", bridge_module.VERSION, False))
        blocker = socket.socket()
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)
        self.addCleanup(blocker.close)
        self.assertIsNone(bridge_module._bridge_on(blocker.getsockname()[1]))

        env = dict(self.env)
        env.pop("KENOBI_TERMINAL_PORT")
        second = subprocess.Popen(
            [sys.executable, str(ROOT / "terminal_bridge.py"), "--sans-navigateur", "--port", str(port)],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        self.addCleanup(lambda: (second.poll() is None and second.terminate(), second.wait(5),
                                 second.stdout.close(), second.stderr.close()))
        second.wait(10)
        self.assertEqual(second.returncode, 1)  # --port imposé et occupé : pas de port de secours
        self.assertIn("n'a pas pu démarrer", second.stdout.read().decode("utf-8"))

    def test_a_busy_port_falls_back_to_a_spare_one(self):
        blocker = socket.socket()
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)
        self.addCleanup(blocker.close)
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        free = probe.getsockname()[1]
        probe.close()
        bridge = TerminalBridge(port=blocker.getsockname()[1], workspace=self.root / "ws", origins=ORIGIN,
                                token_file=self.root / "token", spare_ports=True)

        async def scenario():
            await bridge.start()
            port = bridge.port
            await bridge.close()
            return port

        with mock.patch.object(bridge_module, "SPARE_PORTS", [free]):
            self.assertEqual(asyncio.run(scenario()), free)


class KenobiPageTests(unittest.TestCase):
    """L'adresse de Kenobi inscrite au téléchargement peut porter un chemin (version web dans un sous-dossier)."""

    def test_origin_and_page(self):
        cases = {
            "http://127.0.0.1:8192": ("http://127.0.0.1:8192", "http://127.0.0.1:8192/"),
            "http://127.0.0.1:8192/": ("http://127.0.0.1:8192", "http://127.0.0.1:8192/"),
            "https://Exemple.github.io/Kenobi-tutor-IA/": ("https://exemple.github.io",
                                                           "https://exemple.github.io/Kenobi-tutor-IA/"),
            "https://exemple.github.io/Kenobi-tutor-IA": ("https://exemple.github.io",
                                                          "https://exemple.github.io/Kenobi-tutor-IA/"),
            "https://exemple.github.io/Kenobi-tutor-IA/index.html": ("https://exemple.github.io",
                                                                     "https://exemple.github.io/Kenobi-tutor-IA/"),
        }
        for value, expected in cases.items():
            self.assertEqual(bridge_module.kenobi_page(value), expected, value)
        for value in ("", "ftp://exemple.org", "https://exemple.org/?x=1", "https://exemple.org/#a",
                      "https://moi:secret@exemple.org/", "pas une adresse", "https://exemple.org/<script>"):
            self.assertEqual(bridge_module.kenobi_page(value), (None, None), value)

    def test_embedded_marker_stays_unique(self):
        source = Path(bridge_module.__file__).read_text(encoding="utf-8")
        self.assertEqual(source.count('EMBEDDED_KENOBI_URL = ""'), 1)


if __name__ == "__main__":
    unittest.main()
