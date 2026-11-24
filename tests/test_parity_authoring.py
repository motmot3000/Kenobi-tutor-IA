"""Écrire un cours avec le modèle branché : courses.py (serveur) et browser/courses.js (version web) font pareil."""
import unittest
from unittest import mock

import courses
import providers

try:
    from parity_helper import NODE, js_many
except ImportError:
    from tests.parity_helper import NODE, js_many

AI = {"provider": "custom", "label": "Stub", "kind": "openai", "baseUrl": "http://127.0.0.1:9/v1", "model": "stub-fast",
      "apiKey": "", "reasoning": "none", "configured": True, "keyless": True}

WISHES = [
    "Je suis débutant, je veux apprendre Python pour automatiser mes tableaux, 20 minutes par jour.",
    "  Apprendre Git\r\npour travailler en équipe, niveau intermédiaire.  ",
    "court",
    "",
    "x" * 4001,
    "é" * 4000,
    "🙂" * 9 + "!",
    "Un cours sur les bases de données SQL avec des exercices sur une base de films 🎬.",
]

SCRIPTS = [
    {"chunks": ["# Python au bureau\n\n", "Un cours.\n\n## Module 1 — Bases\n\n### Leçon 1 — Afficher\n"]},
    {"chunks": ["```markdown\n# Git\n\n## Module 1 — Dépôt\n### Leçon 1 — init\n```"]},
    {"chunks": ["```md\n# SQL\n```\n"]},
    {"chunks": ["Voici le cours :\n```markdown\n# Pas un bloc entier\n```"]},
    {"chunks": ["# Coupé"], "truncated": True},
    {"chunks": ["# Début"], "error": {"status": 503, "message": "Limite ou crédit atteint chez Stub.", "code": "rate"}},
]


def python_write(wish, ai, script):
    calls, events = [], []

    def stream_chat(config, system, messages, **options):
        calls.append({"system": system, "messages": messages, "maxTokens": options.get("max_tokens"),
                      "temperature": options.get("temperature"),
                      "timeout": options.get("timeout") * 1000})  # seconds in Python, milliseconds in JS
        for chunk in script.get("chunks", []):
            yield "delta", chunk
        if script.get("error"):
            error = script["error"]
            raise providers.ProviderError(error["status"], error["message"], error.get("code", ""))
        yield "done", {"elapsed": 0.5, "firstToken": 0.1, "truncated": bool(script.get("truncated")), "model": "stub"}

    with mock.patch.object(courses.providers, "stream_chat", stream_chat):
        try:
            for kind, data in courses.write_course(wish, ai):
                events.append([kind, data])
        except (courses.CourseError, providers.ProviderError) as error:
            name = "CourseError" if isinstance(error, courses.CourseError) else "ProviderError"
            return {"error": [name, getattr(error, "message", str(error))], "events": events, "calls": calls}
    return {"events": events, "calls": calls}


def python_check(wish):
    try:
        return {"value": courses.check_wish(wish)}
    except courses.CourseError as error:
        return {"error": ["CourseError", str(error)]}


@unittest.skipUnless(NODE, "node absent")
class AuthoringParityTests(unittest.TestCase):
    def test_wish_checks(self):
        results = js_many([("test:author_harness", "checkWish", [wish]) for wish in WISHES])
        for wish, value in zip(WISHES, results):
            self.assertEqual(value, python_check(wish), repr(wish[:40]))

    def test_messages_and_cleaning(self):
        texts = ["# Titre\n", "```markdown\n# Titre\n```", "```MD\n# T\n\n## M\n```", "  ```\n# T\n```  ",
                 "Avant\n```markdown\n# T\n```", "", "   ", "# T\n```python\nprint(1)\n```"]
        calls = [("courses", "cleanDocument", [text]) for text in texts]
        calls += [("courses", "authorMessages", [wish]) for wish in WISHES[:3]]
        results = js_many(calls)
        for text, value in zip(texts, results):
            self.assertEqual(value, courses.clean_document(text), repr(text))
        for wish, value in zip(WISHES[:3], results[len(texts):]):
            self.assertEqual(value, courses.author_messages(wish))

    def test_write_course_with_a_stub_model(self):
        cases = [(wish, script) for wish in (WISHES[0], WISHES[1]) for script in SCRIPTS]
        cases.append((WISHES[0], SCRIPTS[0]))
        results = js_many([("test:author_harness", "writeCourse", [wish, AI, script]) for wish, script in cases])
        for (wish, script), value in zip(cases, results):
            expected = python_write(wish, AI, script)
            self.assertEqual(value, expected, (wish[:30], script))
        unplugged = js_many([("test:author_harness", "writeCourse", [WISHES[0], {**AI, "configured": False}, SCRIPTS[0]])])[0]
        self.assertEqual(unplugged, python_write(WISHES[0], {**AI, "configured": False}, SCRIPTS[0]))
        self.assertEqual(unplugged["error"][0], "CourseError")

    def test_the_prompt_asks_for_the_import_format(self):
        for marker in ("# Titre du cours", "## Module 1 — Titre du module", "### Leçon 1 — Titre de la leçon",
                       "**Niveau :**", "**Langage et outils :**", "## Évaluation"):
            self.assertIn(marker, courses.AUTHOR_SYSTEM)
        document = courses.clean_document("```markdown\n" + courses.TEMPLATE + "```")
        self.assertEqual(courses.document_headings(document), courses.document_headings(courses.TEMPLATE))


if __name__ == "__main__":
    unittest.main()


class WriteRouteTests(unittest.TestCase):
    """POST /api/courses/write du serveur : le document s'écrit en flux, sans bloc de code autour."""

    def setUp(self):
        import json
        import os
        import tempfile
        import threading
        from pathlib import Path

        import server
        try:
            from stub_llm import StubLLM
        except ImportError:
            from tests.stub_llm import StubLLM
        self.json = json
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        (root / "data").mkdir()
        (root / "data" / "default-state.json").write_text(
            (Path(__file__).resolve().parent.parent / "data" / "default-state.json").read_text(encoding="utf-8"),
            encoding="utf-8")
        self.saved = os.environ.get("KENOBI_TERMINAL_TOKEN_FILE")
        os.environ["KENOBI_TERMINAL_TOKEN_FILE"] = str(root / "pas-de-jeton")
        self.server = server.create_server(port=0, data_dir=root / "data", static_dir=root, config_dir=root / "config",
                                           voice_dir=root / "voix")
        self.port = self.server.server_address[1]
        threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True).start()
        self.stub = StubLLM()

    def tearDown(self):
        import os
        self.stub.close()
        self.server.shutdown()
        self.server.server_close()
        if self.saved is None:
            os.environ.pop("KENOBI_TERMINAL_TOKEN_FILE", None)
        else:
            os.environ["KENOBI_TERMINAL_TOKEN_FILE"] = self.saved
        self.temporary.cleanup()

    def post(self, path, payload):
        import http.client
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        connection.request("POST", path, self.json.dumps(payload).encode(), {"Content-Type": "application/json"})
        response = connection.getresponse()
        result = response.status, response.read().decode("utf-8")
        connection.close()
        return result

    def test_write_route(self):
        status, body = self.post("/api/courses/write", {"wish": "Je veux apprendre Git pour mes projets de classe."})
        self.assertEqual(status, 409, "sans modèle branché")
        self.assertIn("Branche d'abord un modèle", body)
        self.server.settings.update({"ai": {"provider": "custom", "baseUrl": self.stub.base_url, "model": "stub-fast"}})
        self.assertEqual(self.post("/api/courses/write", {"wish": "court"})[0], 400)
        self.stub.say("```markdown\n# Git en classe\n\n", "## Module 1 — Dépôt\n\n### Leçon 1 — init\n```")
        status, body = self.post("/api/courses/write", {"wish": "Je veux apprendre Git pour mes projets de classe."})
        self.assertEqual(status, 200)
        events = [block for block in body.split("\n\n") if block.strip()]
        self.assertTrue(events[0].startswith("event: delta"))
        done = self.json.loads(events[-1].split("data: ", 1)[1])
        self.assertTrue(events[-1].startswith("event: done"))
        self.assertEqual(done["markdown"], "# Git en classe\n\n## Module 1 — Dépôt\n\n### Leçon 1 — init\n")
        request = self.stub.chat_requests()[-1]["body"]
        self.assertEqual(request["messages"][0]["content"], courses.AUTHOR_SYSTEM)
        self.assertIn("Je veux apprendre Git", request["messages"][1]["content"])
