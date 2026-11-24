"""Passerelle vocale (gateway.py) : une ligne d'appel extérieure parle à Kenobi comme à un agent."""
import http.client
import json
import os
import stat
import tempfile
import threading
import unittest
from pathlib import Path

import gateway
import providers
from server import HOST, create_server

try:
    from stub_llm import StubLLM
    from test_server import sample_state
except ImportError:
    from tests.stub_llm import StubLLM
    from tests.test_server import sample_state

CALL_APP_PREFIX = ("[Mode appel vocal : Alex est au téléphone, il t'entend et ne lit rien. Réponds comme au "
                   "téléphone.]\n[Système de tâches : cette demande est une tâche.]\n")


def openai_chunks(raw):
    """Les objets JSON d'un flux SSE compatible OpenAI, et s'il s'est terminé par [DONE]."""
    chunks, done = [], False
    for block in raw.decode("utf-8").split("\n\n"):
        for line in block.split("\n"):
            if line.startswith("data: "):
                data = line[6:]
                if data == "[DONE]":
                    done = True
                else:
                    chunks.append(json.loads(data))
    return chunks, done


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.data_dir = self.root / "data"
        self.data_dir.mkdir()
        state = sample_state()
        state["courses"][0]["sessions"].append({
            "id": "session-2", "title": "Variables", "startedAt": "2026-11-03T10:00:00Z",
            "updatedAt": "2026-11-03T10:05:00Z", "duration": "20 min", "status": "active", "lessonId": "variables",
            "summary": "", "messages": [], "contract": {"objective": "Créer une variable", "criteria": ["nommer"]}})
        (self.data_dir / "default-state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        self.token_file = self.root / "config" / "gateway.token"
        self.saved_env = {name: os.environ.get(name) for name in ("KENOBI_GATEWAY_TOKEN_FILE",
                                                                   "KENOBI_TERMINAL_TOKEN_FILE")}
        os.environ["KENOBI_GATEWAY_TOKEN_FILE"] = str(self.token_file)
        os.environ["KENOBI_TERMINAL_TOKEN_FILE"] = str(self.root / "run" / "token")
        self.server = create_server(port=0, data_dir=self.data_dir, static_dir=self.root, config_dir=self.root / "config",
                                    voice_dir=self.root / "voix")
        self.port = self.server.server_address[1]
        providers._quirks.clear()
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True)
        self.thread.start()
        self.stub = StubLLM()
        self.server.settings.update({"learner": {"name": "Camille"},
                                     "ai": {"provider": "custom", "baseUrl": self.stub.base_url,
                                            "apiKey": "clef-de-test", "model": "stub-fast"}})

    def tearDown(self):
        self.stub.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        for name, value in self.saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        self.temporary_directory.cleanup()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection(HOST, self.port, timeout=10)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        result = response.status, {k.lower(): v for k, v in response.getheaders()}, response.read()
        connection.close()
        return result

    def call(self, text, token=None, stream=True, model="kenobi", host=None):
        token = token if token is not None else gateway.read_token(self.token_file)
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}",
                   "X-Hermes-Session-Id": "voice-line-kenobi"}
        if host:
            headers["Host"] = host
        body = {"model": model, "stream": stream, "messages": [{"role": "user", "content": text}]}
        return self.request("POST", "/v1/chat/completions", json.dumps(body, ensure_ascii=False).encode(), headers)

    def session(self, session_id="session-2"):
        state = json.loads(self.request("GET", "/api/state")[2])
        return next(item for item in state["courses"][0]["sessions"] if item["id"] == session_id)

    def test_closed_until_a_token_exists(self):
        status, _, body = self.call("Bonjour", token="x" * 40)
        self.assertEqual(status, 404)
        self.assertIn("fermée", json.loads(body)["error"]["message"])
        self.assertFalse(json.loads(self.request("GET", "/health")[2])["gateway"])

    def test_token_file_is_private_and_kept(self):
        path = gateway.create_token(self.token_file)
        token = gateway.read_token(path)
        self.assertTrue(token and len(token) >= 40)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(gateway.create_token(self.token_file), path)
        self.assertEqual(gateway.read_token(path), token)
        os.chmod(path, 0o644)
        self.assertIsNone(gateway.read_token(path), "un jeton lisible par d'autres comptes est ignoré")

    def test_refuses_bad_token_foreign_host_and_unknown_model(self):
        gateway.create_token(self.token_file)
        self.assertEqual(self.call("Bonjour", token="mauvais-jeton-0123456789abcdef")[0], 401)
        self.assertEqual(self.call("Bonjour", host="kenobi.example.org")[0], 403)
        self.assertEqual(self.call("Bonjour", model="gpt-6")[0], 404)
        status, _, body = self.call("[Mode appel vocal : rien]\n   ")
        self.assertEqual(status, 400)
        self.assertEqual(self.stub.chat_requests(), [], "rien n'est demandé au modèle")

    def test_spoken_turn_is_answered_without_blocks_and_recorded(self):
        gateway.create_token(self.token_file)
        self.assertTrue(json.loads(self.request("GET", "/health")[2])["gateway"])
        tag_before = json.loads(self.request("GET", "/api/state/tag")[2])["tag"]
        self.stub.say("Une variable garde une valeur. ", "**Par exemple**, age vaut 12.\n\n```kenobi-boutons\n",
                      "bouton: On continue\n```")
        status, headers, raw = self.call(CALL_APP_PREFIX + "C'est quoi une variable ?")
        self.assertEqual(status, 200)
        self.assertIn("text/event-stream", headers["content-type"])
        chunks, done = openai_chunks(raw)
        self.assertTrue(done)
        self.assertTrue(all(chunk["model"] == "kenobi" for chunk in chunks))
        spoken = "".join(choice["delta"].get("content", "") for chunk in chunks for choice in chunk["choices"])
        self.assertIn("Une variable garde une valeur.", spoken)
        self.assertIn("Par exemple, age vaut 12.", spoken)
        self.assertNotIn("kenobi", spoken)
        self.assertNotIn("**", spoken)
        self.assertEqual(chunks[-1]["choices"][0]["finish_reason"], "stop")

        sent = self.stub.chat_requests()[-1]["body"]["messages"]
        last = sent[-1]["content"]
        self.assertIn("Appel téléphonique", last)
        self.assertIn("[Appel vocal] C'est quoi une variable ?", last)
        self.assertNotIn("Système de tâches", last, "les consignes de l'application d'appel ne vont pas au modèle")
        self.assertNotIn("Alex est au téléphone", last)

        messages = self.session()["messages"]
        self.assertEqual([item["role"] for item in messages], ["user", "tutor"])
        self.assertEqual(messages[0]["content"], "[Appel vocal] C'est quoi une variable ?")
        self.assertEqual((messages[0]["voice"], messages[0]["via"]), (True, "gateway"))
        self.assertIn("```kenobi-boutons", messages[1]["content"], "la discussion garde la réponse entière")
        self.assertNotEqual(json.loads(self.request("GET", "/api/state/tag")[2])["tag"], tag_before)

    def test_history_of_previous_calls_reaches_the_model(self):
        gateway.create_token(self.token_file)
        self.stub.say("Première réponse.")
        self.call("Première question")
        self.stub.say("Deuxième réponse.")
        self.call("Deuxième question")
        sent = self.stub.chat_requests()[-1]["body"]["messages"]
        contents = [item["content"] for item in sent]
        self.assertTrue(any("Première question" in content for content in contents))
        self.assertTrue(any("Première réponse." in content for content in contents))
        self.assertEqual(sum("Deuxième question" in content for content in contents), 1, "la question n'arrive qu'une fois")

    def test_model_error_removes_the_unanswered_question(self):
        gateway.create_token(self.token_file)
        self.stub.fail(401)
        status, _, raw = self.call("Une question")
        self.assertEqual(status, 200)
        chunks, done = openai_chunks(raw)
        self.assertFalse(done)
        self.assertIn("Clé refusée", chunks[-1]["error"]["message"])
        self.assertEqual(self.session()["messages"], [], "rien ne reste d'un tour sans réponse")

    def test_no_session_says_so_and_records_nothing(self):
        gateway.create_token(self.token_file)
        state = json.loads(self.request("GET", "/api/state")[2])
        state["courses"][0]["sessions"] = []
        self.assertEqual(self.request("POST", "/api/state", json.dumps(state).encode(),
                                      {"Content-Type": "application/json"})[0], 200)
        chunks, done = openai_chunks(self.call("Bonjour")[2])
        self.assertTrue(done)
        spoken = "".join(choice["delta"].get("content", "") for chunk in chunks for choice in chunk["choices"])
        self.assertIn("Ouvre d'abord une leçon", spoken)
        self.assertEqual(self.stub.chat_requests(), [])

    def test_non_streaming_request(self):
        gateway.create_token(self.token_file)
        self.stub.say("Réponse entière.")
        status, _, body = self.call("Question", stream=False)
        self.assertEqual(status, 200)
        value = json.loads(body)
        self.assertEqual(value["model"], "kenobi")
        self.assertEqual(value["choices"][0]["message"]["content"], "Réponse entière.")


class StateVersionTests(unittest.TestCase):
    """L'étiquette de version de l'état : une page qui enregistre un état périmé est refusée (409) et relit."""

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        (root / "data").mkdir()
        (root / "data" / "default-state.json").write_text(json.dumps(sample_state()), encoding="utf-8")
        self.saved = os.environ.get("KENOBI_TERMINAL_TOKEN_FILE")
        os.environ["KENOBI_TERMINAL_TOKEN_FILE"] = str(root / "run" / "token")
        self.server = create_server(port=0, data_dir=root / "data", static_dir=root, config_dir=root / "config",
                                    voice_dir=root / "voix")
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        if self.saved is None:
            os.environ.pop("KENOBI_TERMINAL_TOKEN_FILE", None)
        else:
            os.environ["KENOBI_TERMINAL_TOKEN_FILE"] = self.saved
        self.temporary_directory.cleanup()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection(HOST, self.port, timeout=5)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        result = response.status, {k.lower(): v for k, v in response.getheaders()}, response.read()
        connection.close()
        return result

    def save(self, state, tag=None):
        headers = {"Content-Type": "application/json"}
        if tag:
            headers["If-Match"] = f'"{tag}"'
        return self.request("POST", "/api/state", json.dumps(state).encode(), headers)

    def test_tags_follow_saves_and_refuse_stale_pages(self):
        status, headers, body = self.request("GET", "/api/state")
        tag = headers["etag"].strip('"')
        self.assertEqual(json.loads(self.request("GET", "/api/state/tag")[2])["tag"], tag)
        state = json.loads(body)
        state["courses"][0]["memory"]["notes"] = "Page A"
        status, _, body = self.save(state, tag)
        self.assertEqual(status, 200)
        new_tag = json.loads(body)["tag"]
        self.assertNotEqual(new_tag, tag)

        state["courses"][0]["memory"]["notes"] = "Page B, restée sur l'ancienne version"
        status, _, body = self.save(state, tag)
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(body)["tag"], new_tag)
        self.assertEqual(json.loads(self.request("GET", "/api/state")[2])["courses"][0]["memory"]["notes"], "Page A")

        status, _, _ = self.save(state)  # une page d'avant les étiquettes enregistre comme avant
        self.assertEqual(status, 200)

    def test_update_state_changes_the_tag(self):
        tag = json.loads(self.request("GET", "/api/state/tag")[2])["tag"]

        def change(state):
            state["courses"][0]["memory"]["notes"] = "Modifié par la passerelle"

        new_tag = self.server.update_state(change)
        self.assertNotEqual(new_tag, tag)
        self.assertEqual(self.server.current_tag(), new_tag)
        self.assertIsNone(self.server.update_state(lambda state: False))
        self.assertEqual(self.server.current_tag(), new_tag)


if __name__ == "__main__":
    unittest.main()
