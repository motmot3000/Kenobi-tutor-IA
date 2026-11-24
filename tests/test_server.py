import base64
import http.client
import io
import json
import os
import socket
import tempfile
import threading
import unittest
import wave
from pathlib import Path
from unittest import mock
from urllib.parse import quote

import providers
import server
import terminal_bridge
import voice
from server import HOST, MAX_STATE_BYTES, VERSION, create_server

try:
    from stub_llm import StubLLM
except ImportError:
    from tests.stub_llm import StubLLM

COURSE_DOCUMENT = """# Git : les bases

Un cours court pour versionner son code.

## Module 1 — Premiers commits

### Leçon 1 — Créer un dépôt

### Leçon 2 — Lire l'état

## Module 2 — Les branches

- Créer une branche
- Fusionner une branche
"""


class FakeEngine:
    name = "fake"
    label = "Voix d'essai"

    def problem(self):
        return None

    def transcribe(self, audio, terms=()):
        return "c'est quoi une variable"

    def speak(self, text, voice_name=None):
        return b"RIFF" + text.encode("utf-8"), "audio/wav"


def sse_events(raw):
    events = []
    for block in raw.decode("utf-8").split("\n\n"):
        kind, data = None, None
        for line in block.split("\n"):
            if line.startswith("event:"):
                kind = line[6:].strip()
            elif line.startswith("data:"):
                data = json.loads(line[5:])
        if kind:
            events.append((kind, data))
    return events


def sample_state():
    return {
        "version": 1,
        "activeCourseId": "python",
        "updatedAt": "2026-11-03T10:00:00Z",
        "courses": [
            {
                "id": "python",
                "title": "Python",
                "description": "Premier parcours",
                "modules": [
                    {
                        "id": "bases",
                        "title": "Les bases",
                        "description": "Fondamentaux",
                        "lessons": [
                            {
                                "id": "variables",
                                "title": "Variables",
                                "status": "current",
                                "duration": "20 min",
                            }
                        ],
                    }
                ],
                "sessions": [
                    {
                        "id": "session-1",
                        "title": "Première session",
                        "startedAt": "2026-11-03T09:00:00Z",
                        "updatedAt": "2026-11-03T09:20:00Z",
                        "duration": "20 min",
                        "status": "done",
                        "lessonId": "variables",
                        "summary": "Les affectations sont comprises.",
                        "messages": [
                            {
                                "id": "message-1",
                                "role": "assistant",
                                "createdAt": "2026-11-03T09:01:00Z",
                                "content": "CONTENU_DE_MESSAGE_A_NE_PAS_EXPORTER",
                            }
                        ],
                    }
                ],
                "memory": {
                    "lastSessionId": "session-1",
                    "acquired": ["Affectation"],
                    "toReview": ["Types"],
                    "nextFocus": "Créer une variable.",
                    "notes": "Note initiale.",
                },
            }
        ],
    }


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.data_dir = self.root / "data"
        self.data_dir.mkdir()
        self.default_state = sample_state()
        (self.data_dir / "default-state.json").write_text(
            json.dumps(self.default_state, ensure_ascii=False), encoding="utf-8"
        )

        self.static_dir = self.root / "static"
        (self.static_dir / "assets").mkdir(parents=True)
        (self.static_dir / "index.html").write_text("<h1>Kenobi test</h1>", encoding="utf-8")
        (self.static_dir / "styles.css").write_text("body { color: black; }", encoding="utf-8")
        (self.static_dir / "app.js").write_text("const app = true;", encoding="utf-8")
        (self.static_dir / "core.js").write_text("const core = true;", encoding="utf-8")
        (self.static_dir / "editor.js").write_text("const editor = true;", encoding="utf-8")
        (self.static_dir / "terminal.js").write_text("const terminal = true;", encoding="utf-8")
        for name in ("settings.js", "call.js", "vad.js", "vad-worklet.js"):
            (self.static_dir / name).write_text(f"// {name}", encoding="utf-8")
        (self.static_dir / "assets" / "test.txt").write_text("asset-ok", encoding="utf-8")

        self.previous_data_dir = os.environ.get("KENOBI_DATA_DIR")
        os.environ["KENOBI_DATA_DIR"] = str(self.data_dir)
        self.token_file = self.root / "run" / "token"
        self.previous_token_file = os.environ.get("KENOBI_TERMINAL_TOKEN_FILE")
        os.environ["KENOBI_TERMINAL_TOKEN_FILE"] = str(self.token_file)
        self.server = create_server(port=0, static_dir=self.static_dir, config_dir=self.root / "config",
                                    voice_dir=self.root / "voix")
        self.port = self.server.server_address[1]
        self.stub = None
        providers._quirks.clear()
        self.thread = threading.Thread(
            target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True
        )
        self.thread.start()

    def tearDown(self):
        if self.stub:
            self.stub.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        if self.previous_data_dir is None:
            os.environ.pop("KENOBI_DATA_DIR", None)
        else:
            os.environ["KENOBI_DATA_DIR"] = self.previous_data_dir
        if self.previous_token_file is None:
            os.environ.pop("KENOBI_TERMINAL_TOKEN_FILE", None)
        else:
            os.environ["KENOBI_TERMINAL_TOKEN_FILE"] = self.previous_token_file
        self.temporary_directory.cleanup()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection(HOST, self.port, timeout=5)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        result = (
            response.status,
            {name.lower(): value for name, value in response.getheaders()},
            response.read(),
        )
        connection.close()
        return result

    def post_json(self, state):
        return self.request(
            "POST",
            "/api/state",
            body=json.dumps(state, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )

    def post(self, path, payload, headers=None):
        status, response_headers, body = self.request(
            "POST", path, body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", **(headers or {})})
        return status, response_headers, body

    def plug_model(self):
        """Branche le serveur sur un faux fournisseur, comme le ferait le dialogue des réglages."""
        self.stub = StubLLM()
        status, _, body = self.post("/api/settings", {
            "learner": {"name": "Camille"},
            "ai": {"provider": "custom", "baseUrl": self.stub.base_url, "apiKey": "clef-de-test-serveur", "model": "stub-fast"},
        })
        self.assertEqual(status, 200, body)
        return json.loads(body)

    def test_health_and_unknown_api_route(self):
        status, headers, body = self.request("GET", "/api/health")
        self.assertEqual(status, 200)
        value = json.loads(body)
        self.assertEqual((value["status"], value["version"]), ("ok", VERSION))
        self.assertEqual((value["agent"]["connected"], value["agent"]["name"]), (False, "Kenobi"))
        self.assertIn("Aucun modèle", value["agent"]["detail"])
        self.assertIn("application/json", headers["content-type"])

        self.plug_model()
        agent = json.loads(self.request("GET", "/api/health")[2])["agent"]
        self.assertEqual((agent["connected"], agent["model"]), (True, "stub-fast"))

        status, _, body = self.request("GET", "/api/inconnue")
        self.assertEqual(status, 404)
        self.assertIn("error", json.loads(body))

    def test_state_is_initialized_from_default_state(self):
        runtime_state = self.data_dir / "runtime" / "state.json"
        self.assertFalse(runtime_state.exists())

        status, _, body = self.request("GET", "/api/state")

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), self.default_state)
        self.assertTrue(runtime_state.is_file())
        self.assertEqual(json.loads(runtime_state.read_text(encoding="utf-8")), self.default_state)

    def test_post_persists_state_and_generates_downloadable_memory(self):
        new_state = sample_state()
        new_state["courses"][0]["memory"]["notes"] = "Note sauvegardée."

        status, _, body = self.post_json(new_state)
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["ok"])

        status, _, body = self.request("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), new_state)
        memory_path = self.data_dir / "runtime" / "python" / "MEMORY.md"
        self.assertTrue(memory_path.is_file())
        self.assertIn("Note sauvegardée.", memory_path.read_text(encoding="utf-8"))

        status, headers, body = self.request("GET", "/api/memory?courseId=python")
        self.assertEqual(status, 200)
        self.assertIn("text/markdown", headers["content-type"])
        self.assertIn("Les affectations sont comprises.", body.decode("utf-8"))
        self.assertNotIn("CONTENU_DE_MESSAGE_A_NE_PAS_EXPORTER", body.decode("utf-8"))

        status, headers, _ = self.request(
            "GET", "/api/memory?courseId=python&download=1"
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            headers["content-disposition"], 'attachment; filename="python-MEMORY.md"'
        )

    def test_a_page_left_on_the_former_plan_cannot_save_it_back(self):
        plan = sample_state()
        plan["courses"][0]["modules"][0]["lessons"] = [
            {"id": "m0-types", "title": "Types et comparaisons", "duration": 20, "status": "current",
             "objectives": ["reconnaître int, float, str et bool"]},
            {"id": "m0-boucles", "title": "Boucles for et range", "duration": 20, "status": "todo"},
        ]
        (self.data_dir / "default-state.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        stale = sample_state()
        stale["courses"][0]["modules"][0]["lessons"] = [
            {"id": "mimo-types", "title": "Types et comparaisons", "duration": 40, "status": "done"},
            {"id": "mimo-loops", "title": "Boucles", "duration": 45, "status": "current"},
        ]
        stale["courses"][0]["sessions"][0]["lessonId"] = "mimo-loops"

        status, _, body = self.post_json(stale)
        self.assertEqual(status, 200, body)

        saved = json.loads((self.data_dir / "runtime" / "state.json").read_text(encoding="utf-8"))
        served = json.loads(self.request("GET", "/api/state")[2])
        for state in (saved, served):
            course = state["courses"][0]
            self.assertEqual([(lesson["id"], lesson["status"], lesson["duration"]) for lesson in course["modules"][0]["lessons"]],
                             [("m0-types", "done", 20), ("m0-boucles", "current", 20)])
            self.assertEqual((course["sessions"][0]["lessonId"], course["sessions"][0]["title"]), ("m0-boucles", "Boucles for et range"))
            self.assertEqual(course["memory"]["notes"], "Note initiale.")

    def test_dangerous_course_id_is_rejected_without_persistence(self):
        state = sample_state()
        state["activeCourseId"] = "../python"
        state["courses"][0]["id"] = "../python"

        status, _, body = self.post_json(state)

        self.assertEqual(status, 400)
        self.assertIn("error", json.loads(body))
        self.assertFalse((self.data_dir / "runtime" / "state.json").exists())

    def test_invalid_json_is_rejected(self):
        status, _, body = self.request(
            "POST",
            "/api/state",
            body=b'{"version": 1,',
            headers={"Content-Type": "application/json"},
        )

        self.assertEqual(status, 400)
        self.assertIn("error", json.loads(body))
        self.assertFalse((self.data_dir / "runtime" / "state.json").exists())

    def test_body_over_two_mebibytes_is_rejected(self):
        status, _, body = self.request(
            "POST",
            "/api/state",
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(MAX_STATE_BYTES + 1),
            },
        )

        self.assertEqual(status, 413)
        self.assertIn("error", json.loads(body))

    def test_only_expected_static_files_are_served(self):
        expected = {
            "/": b"<h1>Kenobi test</h1>",
            "/index.html": b"<h1>Kenobi test</h1>",
            "/styles.css": b"body { color: black; }",
            "/app.js": b"const app = true;",
            "/core.js": b"const core = true;",
            "/editor.js": b"const editor = true;",
            "/terminal.js": b"const terminal = true;",
            "/settings.js": b"// settings.js",
            "/call.js": b"// call.js",
            "/vad.js": b"// vad.js",
            "/vad-worklet.js": b"// vad-worklet.js",
            "/assets/test.txt": b"asset-ok",
        }
        for path, expected_body in expected.items():
            with self.subTest(path=path):
                status, headers, body = self.request("GET", path)
                self.assertEqual(status, 200)
                self.assertEqual(body, expected_body)
                self.assertEqual(headers["cache-control"], "no-cache")

    def test_chat_stream_answers_with_the_plugged_model_then_reports_errors(self):
        body = {"sessionId": "session-1", "message": "Salut", "terminal": "coach"}
        status, _, raw = self.post("/api/chat/stream", body)
        kind, data = sse_events(raw)[-1]  # aucun modèle branché : la page le dit et renvoie aux réglages
        self.assertEqual((status, kind, data["status"]), (200, "error", 409))
        self.assertIn("Aucun modèle", data["message"])

        self.plug_model()
        self.stub.say("Bon", "jour")
        status, response_headers, raw = self.post("/api/chat/stream", body)
        self.assertEqual(status, 200)
        self.assertIn("text/event-stream", response_headers["content-type"])
        events = sse_events(raw)
        self.assertEqual([data["text"] for kind, data in events if kind == "delta"], ["Bon", "jour"])
        self.assertEqual(events[-1][0], "done")
        sent = self.stub.chat_requests()[0]["body"]
        self.assertIn("Tu es Kenobi", sent["messages"][0]["content"])
        self.assertIn("Camille", sent["messages"][0]["content"])
        self.assertTrue(sent["messages"][-1]["content"].startswith("Salut"))

        self.stub.fail(401)
        status, _, raw = self.post("/api/chat/stream", body)
        self.assertEqual(status, 200)
        kind, data = sse_events(raw)[-1]
        self.assertEqual(kind, "error")
        self.assertIn("Clé refusée", data["message"])
        self.assertNotIn(b"clef-de-test-serveur", raw)

        self.stub.say("Bien", ".")
        status, _, raw = self.post("/api/chat", body)
        self.assertEqual((status, json.loads(raw)["reply"]), (200, "Bien."))

    def test_settings_keep_the_key_on_the_server(self):
        public = self.plug_model()
        self.assertEqual((public["ai"]["hasKey"], public["ai"]["configured"], public["editable"]), (True, True, True))
        status, headers, raw = self.request("GET", "/api/settings")
        self.assertEqual(status, 200)
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertNotIn(b"clef-de-test-serveur", raw)
        self.assertEqual(json.loads(raw)["learner"]["name"], "Camille")
        stored = self.root / "config" / "settings.json"
        self.assertEqual(oct(stored.stat().st_mode & 0o777), "0o600")

        status, _, raw = self.post("/api/settings/test", {})
        self.assertEqual((status, json.loads(raw)["ok"]), (200, True))
        self.assertEqual(self.stub.chat_requests()[-1]["headers"]["authorization"], "Bearer clef-de-test-serveur")

        # Changer d'adresse sans redonner la clé : elle n'est pas envoyée ailleurs.
        other = StubLLM()
        try:
            status, _, raw = self.post("/api/settings", {"ai": {"baseUrl": other.base_url}})
            self.assertEqual((status, json.loads(raw)["ai"]["hasKey"]), (200, False))
            self.post("/api/settings/test", {})
            self.assertTrue(other.requests)
            for request in other.requests:
                self.assertNotIn("authorization", request["headers"])
                self.assertNotIn("clef-de-test-serveur", json.dumps(request))
        finally:
            other.close()

        status, _, raw = self.post("/api/settings", {"ai": {"provider": "inconnu"}})
        self.assertEqual(status, 400)

    def test_settings_change_only_from_this_computer(self):
        lan = {"Host": f"192.168.1.220:{self.port}"}
        status, _, raw = self.request("GET", "/api/settings", headers=lan)
        self.assertEqual((status, json.loads(raw)["editable"]), (200, False))
        for path, payload in (
            ("/api/settings", {"ai": {"provider": "custom", "baseUrl": "http://evil.test/v1", "model": "m"}}),
            ("/api/settings/models", {"ai": {"provider": "custom", "baseUrl": "http://127.0.0.1:1/v1"}}),
            ("/api/settings/test", {}),
            ("/api/voice/install", {}),
            ("/api/voice/voices", {"apiKey": "x"}),
        ):
            with self.subTest(path=path):
                status, _, raw = self.post(path, payload, lan)
                self.assertEqual(status, 403)
        status, _, _ = self.post("/api/settings", {"learner": {"name": "x"}}, {"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(status, 403)
        self.assertFalse((self.root / "config" / "settings.json").exists())

    def test_models_come_from_the_provider(self):
        self.stub = StubLLM()
        status, _, raw = self.post("/api/settings/models", {"ai": {"provider": "custom", "baseUrl": self.stub.base_url, "apiKey": "k"}})
        self.assertEqual(status, 200)
        self.assertEqual([item["id"] for item in json.loads(raw)["models"]], ["stub-fast", "stub-large"])
        status, _, raw = self.post("/api/settings/models", {"ai": {"provider": "mistral"}})
        self.assertEqual(status, 400)  # pas de clé : rien n'est demandé au fournisseur

    def test_course_import_from_headings_then_with_the_model(self):
        status, _, raw = self.post("/api/courses/import", {"filename": "git.md", "markdown": COURSE_DOCUMENT})
        self.assertEqual(status, 200)
        events = sse_events(raw)
        self.assertEqual(events[0], ("progress", {"stage": "outline", "chars": len(COURSE_DOCUMENT), "model": ""}))
        kind, data = events[-1]
        self.assertEqual(kind, "done")
        course = data["course"]
        self.assertEqual((course["source"]["analysis"], len(course["modules"])), ("titres", 2))
        self.assertIn("Aucun modèle", data["notice"])
        document = self.data_dir / "runtime" / course["id"] / "COURSE.md"
        self.assertEqual(document.read_text(encoding="utf-8"), COURSE_DOCUMENT)
        status, headers, raw = self.request("GET", f"/api/courses/source?courseId={course['id']}")
        self.assertEqual((status, raw.decode("utf-8")), (200, COURSE_DOCUMENT))

        # Le cours rejoint l'état, sa mémoire est créée ; une séance y trouve son document.
        state = sample_state()
        state["courses"].append(course)
        self.assertEqual(self.post_json(state)[0], 200)
        self.assertTrue((self.data_dir / "runtime" / course["id"] / "MEMORY.md").is_file())

        self.plug_model()
        analysis = {"title": "Git avancé", "shortTitle": "Git", "tools": {"terminal": True, "language": "shell"},
                    "modules": [{"title": "Rebase", "source": "Module 1 — Premiers commits", "lessons": [{"title": "Rebaser"}]}]}
        self.stub.say(json.dumps(analysis))
        status, _, raw = self.post("/api/courses/import", {"filename": "git.md", "markdown": COURSE_DOCUMENT})
        kind, data = sse_events(raw)[-1]
        self.assertEqual((kind, data["course"]["source"]["analysis"], data["notice"]), ("done", "ia", ""))
        self.assertNotEqual(data["course"]["id"], course["id"])  # identifiant déjà pris par le premier import

        status, _, raw = self.post("/api/courses/import", {"filename": "x.md", "markdown": "trop court"})
        self.assertEqual(status, 400)
        status, headers, raw = self.request("GET", "/api/courses/template")
        self.assertEqual(status, 200)
        self.assertIn("## Objectifs du cours", raw.decode("utf-8"))

    def test_a_removed_course_goes_to_the_trash_with_its_memory(self):
        state = sample_state()
        extra = json.loads(json.dumps(state["courses"][0]))
        extra["id"], extra["title"] = "git", "Git"
        state["courses"].append(extra)
        self.assertEqual(self.post_json(state)[0], 200)
        self.assertTrue((self.data_dir / "runtime" / "git" / "MEMORY.md").is_file())

        state["courses"] = state["courses"][:1]
        self.assertEqual(self.post_json(state)[0], 200)
        self.assertFalse((self.data_dir / "runtime" / "git").exists())
        trash = list((self.data_dir / "runtime" / "corbeille").iterdir())
        self.assertEqual(len(trash), 1)
        self.assertTrue(trash[0].name.startswith("git-"))
        self.assertEqual(json.loads((trash[0] / "course.json").read_text(encoding="utf-8"))["title"], "Git")
        self.assertTrue((trash[0] / "MEMORY.md").is_file())
        self.assertTrue((self.data_dir / "runtime" / "python" / "MEMORY.md").is_file())

    def test_call_turn_hears_answers_and_speaks_in_order(self):
        self.request("GET", "/api/state")
        status, _, raw = self.request("GET", "/api/voice/status")
        self.assertEqual((status, json.loads(raw)["ready"]), (200, False))  # voix locale pas installée ici

        self.plug_model()
        self.server.voice.engine = lambda config=None: (FakeEngine(), "voix")
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(16000)
            output.writeframes(b"\x01\x00" * 16000)
        self.stub.say("Une boîte qui garde une valeur. ", "Tu en crées une avec le signe égal.")
        status, headers, raw = self.request("POST", "/api/call/turn", body=buffer.getvalue(), headers={
            "Content-Type": "audio/wav", "X-Session-Id": "session-1", "X-Terminal": "coach", "X-Carry": quote("Dis-moi,")})
        self.assertEqual(status, 200)
        events = sse_events(raw)
        transcript = next(data for kind, data in events if kind == "transcript")
        self.assertEqual(transcript["text"], "Dis-moi, c'est quoi une variable")
        segments = [data for kind, data in events if kind == "segment"]
        self.assertEqual([item["index"] for item in segments], [0, 1])
        self.assertEqual(base64.b64decode(segments[0]["audio"]), b"RIFF" + "Une boîte qui garde une valeur.".encode("utf-8"))
        self.assertEqual(events[-1][0], "done")
        sent = self.stub.chat_requests()[-1]["body"]["messages"][-1]["content"]
        self.assertIn("[Appel vocal] Dis-moi, c'est quoi une variable", sent)
        self.assertTrue(sent.startswith("[Appel vocal : Camille te parle au micro"))

        # Un message écrit pendant l'appel (un Run) : pas de transcription, la réponse est dite aussi.
        self.stub.say("Presque.")
        status, _, raw = self.post("/api/call/turn", {"sessionId": "session-1", "text": "[Exécution — tentative n° 1.]"})
        kinds = [kind for kind, _ in sse_events(raw)]
        self.assertEqual((kinds[0], kinds[-1], kinds.count("segment")), ("transcript", "done", 1))

        for body, headers, expected in (
            (b"x", {"Content-Type": "text/plain"}, 415),
            (buffer.getvalue(), {"Content-Type": "audio/wav", "X-Session-Id": "inconnue"}, 404),
            (b"{}", {"Content-Type": "application/json"}, 400),
        ):
            with self.subTest(expected=expected):
                status, _, _ = self.request("POST", "/api/call/turn", body=body, headers=headers)
                self.assertEqual(status, expected)

        status, _, raw = self.post("/api/call/phrases", {})
        phrases = json.loads(raw)
        self.assertEqual((status, phrases["ready"], phrases["greetings"][0]["text"]), (200, True, "Oui, Camille ?"))

    def test_terminal_token_is_only_given_to_a_page_on_this_computer(self):
        status, headers, body = self.request("GET", "/api/terminal", headers={"Host": f"127.0.0.1:{self.port}"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["reason"], "bridge")

        terminal_bridge.write_token(self.token_file, "b" * 48)
        status, headers, body = self.request("GET", "/api/terminal", headers={"Host": f"127.0.0.1:{self.port}"})
        value = json.loads(body)
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertTrue(value["available"])
        self.assertEqual(value["token"], "b" * 48)
        self.assertTrue(value["url"].startswith("ws://127.0.0.1:"))

        # A DNS name pointing at the loopback (rebinding) or a LAN address never gets the token.
        for host in ("evil.test", f"192.168.1.220:{self.port}"):
            with self.subTest(host=host):
                _, _, body = self.request("GET", "/api/terminal", headers={"Host": host})
                value = json.loads(body)
                self.assertEqual((value["available"], value["reason"]), (False, "remote"))
                self.assertEqual(value["download"], "api/terminal/pont")
                self.assertNotIn("token", value)

        status, _, body = self.request(
            "GET", "/api/terminal", headers={"Host": f"127.0.0.1:{self.port}", "Sec-Fetch-Site": "cross-site"}
        )
        self.assertEqual(status, 403)
        self.assertNotIn(b"b" * 48, body)

        # Le nom de la machine, depuis la machine elle-même : c'est encore cet ordinateur.
        _, _, body = self.request("GET", "/api/terminal", headers={"Host": f"{socket.gethostname()}:{self.port}"})
        self.assertEqual(json.loads(body).get("token"), "b" * 48)

    def test_this_computer_is_recognised_by_its_own_network_addresses(self):
        def page(client, local, host):
            handler = server.KenobiRequestHandler.__new__(server.KenobiRequestHandler)
            handler.headers = {"Host": host}
            handler.client_address = (client, 50000)
            handler.connection = mock.Mock(getsockname=lambda: (local, 8192))
            return handler._same_computer()

        machine = socket.gethostname().lower()
        # Le MINIX qui s'ouvre par son adresse du réseau local ou du tailnet, ou par son nom.
        self.assertTrue(page("192.168.1.220", "192.168.1.220", "192.168.1.220:8192"))
        self.assertTrue(page("100.120.165.95", "100.120.165.95", "100.120.165.95:8192"))
        self.assertTrue(page("127.0.0.1", "127.0.1.1", f"{machine}:8192"))
        self.assertTrue(page("100.120.165.95", "100.120.165.95", f"{machine}.tail1234.ts.net:8192"))
        # Un autre ordinateur, ou un nom de domaine qui pointerait ici (rebond DNS).
        self.assertFalse(page("192.168.1.50", "192.168.1.220", "192.168.1.220:8192"))
        self.assertFalse(page("192.168.1.220", "192.168.1.220", "evil.test:8192"))
        self.assertFalse(page("127.0.0.1", "127.0.0.1", "evil.test:8192"))
        self.assertFalse(page("127.0.0.1", "127.0.0.1", "192.168.1.220:8192"))
        self.assertFalse(page("192.168.1.220", "192.168.1.220", f"{machine}.evil.test:8192"))

    def test_bridge_download_is_set_for_the_page_that_asks(self):
        host = f"192.168.1.220:{self.port}"
        status, headers, body = self.request(
            "GET", f"/api/terminal/pont?kenobi=http%3A%2F%2F{host}", headers={"Host": host}
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers["content-disposition"], 'attachment; filename="kenobi-terminal.py"')
        source = body.decode("utf-8")
        self.assertIn(f'EMBEDDED_KENOBI_URL = "http://{host}"', source)
        self.assertNotIn('EMBEDDED_KENOBI_URL = ""', source)
        compile(source, "kenobi-terminal.py", "exec")
        self.assertNotIn(b"b" * 48, body)

        # Téléchargé par une commande, sans paramètre : l'adresse est celle par laquelle on a joint Kenobi.
        status, _, body = self.request("GET", "/api/terminal/pont", headers={"Host": host})
        self.assertEqual(status, 200)
        self.assertIn(f'EMBEDDED_KENOBI_URL = "http://{host}"', body.decode("utf-8"))

        for origin, sent_host in (
            ("http://evil.test", host),
            (f"http://{host}/x", host),
            ('http://a"+__import__("os")+"', host),
            (f"http://{host}", "other.test"),
        ):
            with self.subTest(origin=origin, host=sent_host):
                status, _, _ = self.request(
                    "GET", f"/api/terminal/pont?kenobi={quote(origin, safe='')}", headers={"Host": sent_host}
                )
                self.assertEqual(status, 400)

    def test_data_project_files_and_traversal_are_not_exposed(self):
        paths = (
            "/data/default-state.json",
            "/PRODUCT.md",
            "/server.py",
            "/assets/../data/default-state.json",
            "/assets/%2e%2e/data/default-state.json",
        )
        for path in paths:
            with self.subTest(path=path):
                status, _, body = self.request("GET", path)
                self.assertEqual(status, 404)
                self.assertIn("error", json.loads(body))


if __name__ == "__main__":
    unittest.main()
