import unittest

import providers

try:
    from stub_llm import StubLLM
except ImportError:  # lancé comme tests.test_providers
    from tests.stub_llm import StubLLM


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.stub = StubLLM()
        providers._quirks.clear()

    def tearDown(self):
        self.stub.close()

    def test_openai_compatible_stream_gives_deltas_then_done(self):
        self.stub.say("Bon", "jour", " !")
        events = list(providers.stream_chat(self.stub.config(), "Système.", [{"role": "user", "content": "Salut"}]))
        self.assertEqual([value for kind, value in events if kind == "delta"], ["Bon", "jour", " !"])
        kind, done = events[-1]
        self.assertEqual(kind, "done")
        self.assertFalse(done["truncated"])
        self.assertEqual(done["model"], "stub-fast")
        request = self.stub.chat_requests()[0]
        self.assertEqual(request["path"], "/v1/chat/completions")
        self.assertEqual(request["headers"]["authorization"], "Bearer test-key-not-real")
        self.assertEqual(request["body"]["messages"][0], {"role": "system", "content": "Système."})
        self.assertEqual(request["body"]["reasoning_effort"], "none")
        self.assertTrue(request["body"]["stream"])

    def test_anthropic_protocol_uses_its_own_headers_and_shape(self):
        self.stub.say("Oui", ".", stop="max_tokens")
        text, done = providers.complete(self.stub.config("anthropic"), "Système.", [{"role": "user", "content": "Salut"}])
        self.assertEqual(text, "Oui.")
        self.assertTrue(done["truncated"])
        request = self.stub.chat_requests()[0]
        self.assertEqual(request["path"], "/v1/messages")
        self.assertEqual(request["headers"]["x-api-key"], "test-key-not-real")
        self.assertNotIn("authorization", request["headers"])
        self.assertEqual(request["body"]["system"][0]["text"], "Système.")
        self.assertNotIn("reasoning_effort", request["body"])
        self.assertIn("max_tokens", request["body"])

    def test_a_refused_optional_setting_is_dropped_and_remembered(self):
        self.stub.rejected_params.add("reasoning_effort")
        self.stub.say("un")
        self.stub.say("deux")
        config = self.stub.config()
        self.assertEqual(providers.complete(config, "", [{"role": "user", "content": "a"}])[0], "un")
        self.assertEqual(providers.complete(config, "", [{"role": "user", "content": "b"}])[0], "deux")
        bodies = [item["body"] for item in self.stub.chat_requests()]
        # Refusé une fois, puis jamais renvoyé pour ce modèle à cette adresse.
        self.assertEqual(["reasoning_effort" in body for body in bodies], [True, False, False])

    def test_max_tokens_switches_to_the_name_the_model_wants(self):
        self.stub.fail(400, "Unsupported parameter: 'max_tokens'. Use 'max_completion_tokens' instead.")
        self.stub.say("ok")
        providers.complete(self.stub.config(), "", [{"role": "user", "content": "a"}], max_tokens=50)
        bodies = [item["body"] for item in self.stub.chat_requests()]
        self.assertIn("max_tokens", bodies[0])
        self.assertEqual(bodies[1].get("max_completion_tokens"), 50)
        self.assertNotIn("max_tokens", bodies[1])

    def test_errors_are_named_for_the_person_and_never_show_the_key(self):
        cases = ((401, "auth", "Clé refusée"), (404, "not_found", "introuvable"), (429, "rate", "Limite"),
                 (500, "upstream", "erreur 500"))
        for status, code, words in cases:
            with self.subTest(status=status):
                self.stub.fail(status, "boom")
                with self.assertRaises(providers.ProviderError) as caught:
                    providers.complete(self.stub.config(), "", [{"role": "user", "content": "a"}])
                self.assertEqual(caught.exception.code, code)
                self.assertIn(words, caught.exception.message)
                self.assertNotIn("test-key-not-real", caught.exception.message)

    def test_a_bad_key_answered_with_400_is_still_a_key_problem(self):
        self.stub.fail(400, "API key not valid. Please pass a valid API key.")
        with self.assertRaises(providers.ProviderError) as caught:
            providers.complete(self.stub.config(), "", [{"role": "user", "content": "a"}])
        self.assertEqual(caught.exception.code, "auth")
        self.assertIn("Clé refusée", caught.exception.message)
        # Refusée une fois : pas de nouvel essai, et aucun réglage du modèle n'est appris de travers.
        self.assertEqual(len(self.stub.chat_requests()), 1)
        self.assertEqual(providers._quirks, {})

    def test_a_refusal_that_no_setting_explains_teaches_nothing(self):
        # Une demande trop longue, par exemple : retirer les réglages facultatifs n'y change rien.
        self.stub.fail(400, "This model's maximum context length is 8192 tokens.")
        self.stub.fail(400, "This model's maximum context length is 8192 tokens.")
        config = self.stub.config()
        with self.assertRaises(providers.ProviderError) as caught:
            providers.complete(config, "", [{"role": "user", "content": "a"}])
        self.assertEqual(caught.exception.code, "bad_request")
        self.assertIn("maximum context length", caught.exception.message)
        self.assertEqual(providers._quirks, {})
        # La demande suivante part avec tous ses réglages, raisonnement coupé compris.
        self.stub.say("ok")
        providers.complete(config, "", [{"role": "user", "content": "b"}])
        body = self.stub.chat_requests()[-1]["body"]
        self.assertEqual((body["reasoning_effort"], "temperature" in body, "max_tokens" in body), ("none", True, True))

    def test_unreachable_and_unconfigured_are_told_apart(self):
        config = self.stub.config(baseUrl="http://127.0.0.1:9/v1")
        with self.assertRaises(providers.ProviderError) as caught:
            providers.complete(config, "", [{"role": "user", "content": "a"}])
        self.assertEqual(caught.exception.code, "unreachable")
        with self.assertRaises(providers.ProviderError) as caught:
            providers.complete({**self.stub.config(), "configured": False}, "", [{"role": "user", "content": "a"}])
        self.assertEqual(caught.exception.code, "unconfigured")

    def test_messages_alternate_and_start_with_the_user(self):
        messages = providers.normalize_messages([
            {"role": "assistant", "content": "Avant tout."},
            {"role": "user", "content": "Un"},
            {"role": "user", "content": "Deux"},
            {"role": "assistant", "content": ""},
            {"role": "assistant", "content": "Trois"},
        ])
        self.assertEqual(messages, [{"role": "user", "content": "Un\n\nDeux"}, {"role": "assistant", "content": "Trois"}])

    def test_models_are_listed_without_the_ones_that_do_not_chat(self):
        models = providers.list_models(self.stub.config())
        self.assertEqual([item["id"] for item in models], ["stub-fast", "stub-large"])
        self.assertEqual(models[1]["label"], "Stub Large")

    def test_cancel_stops_a_reply_in_progress(self):
        cancel = providers.Cancel()
        self.stub.say("un", "deux", "trois")
        seen = []
        with self.assertRaises(providers.ProviderError) as caught:
            for kind, value in providers.stream_chat(self.stub.config(), "", [{"role": "user", "content": "a"}], cancel=cancel):
                seen.append(value)
                cancel.set()
        self.assertEqual(caught.exception.code, "cancelled")
        self.assertEqual(seen, ["un"])


if __name__ == "__main__":
    unittest.main()
