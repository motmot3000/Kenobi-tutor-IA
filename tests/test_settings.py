import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import settings as settings_module
from settings import Settings, SettingsError


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.settings = Settings(Path(self.directory.name) / "config")
        self.environment = mock.patch.dict(os.environ, {}, clear=False)
        self.environment.start()
        for name in ("MISTRAL_API_KEY", "OPENAI_API_KEY", "ELEVENLABS_API_KEY"):
            os.environ.pop(name, None)

    def tearDown(self):
        self.environment.stop()
        self.directory.cleanup()

    def test_nothing_is_plugged_at_first(self):
        ai = self.settings.ai()
        self.assertFalse(ai["configured"])
        self.assertEqual(self.settings.public()["ai"]["hasKey"], False)
        self.assertFalse(self.settings.path.exists())

    def test_the_file_is_private_and_the_key_never_leaves_through_the_public_view(self):
        self.settings.update({"ai": {"provider": "mistral", "apiKey": "clef-secrete-de-test", "model": "modele-rapide"}})
        mode = stat.S_IMODE(os.stat(self.settings.path).st_mode)
        self.assertEqual(mode, 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(self.settings.path.parent).st_mode), 0o700)
        public = self.settings.public()
        self.assertNotIn("clef-secrete-de-test", json.dumps(public))
        self.assertEqual((public["ai"]["hasKey"], public["ai"]["keySource"], public["ai"]["configured"]), (True, "saved", True))
        ai = self.settings.ai()
        self.assertEqual((ai["baseUrl"], ai["apiKey"], ai["kind"]), ("https://api.mistral.ai/v1", "clef-secrete-de-test", "openai"))

    def test_a_key_stays_bound_to_the_address_it_was_given_for(self):
        self.settings.update({"ai": {"provider": "custom", "baseUrl": "http://127.0.0.1:1234/v1/", "apiKey": "clef-a", "model": "m"}})
        self.assertEqual(self.settings.ai()["baseUrl"], "http://127.0.0.1:1234/v1")
        # Même adresse, autre modèle : la clé reste.
        self.settings.update({"ai": {"model": "m2"}})
        self.assertEqual(self.settings.ai()["apiKey"], "clef-a")
        # Autre adresse sans redonner la clé : elle est effacée, jamais envoyée ailleurs.
        self.settings.update({"ai": {"baseUrl": "http://ailleurs.test/v1"}})
        self.assertEqual(self.settings.ai()["apiKey"], "")
        # Autre fournisseur : idem, et le modèle de l'ancien ne suit pas.
        self.settings.update({"ai": {"provider": "custom", "baseUrl": "http://127.0.0.1:1234/v1", "apiKey": "clef-b", "model": "m"}})
        self.settings.update({"ai": {"provider": "openai"}})
        ai = self.settings.ai()
        self.assertEqual((ai["apiKey"], ai["model"], ai["configured"]), ("", "", False))

    def test_environment_key_is_used_when_none_is_saved(self):
        os.environ["MISTRAL_API_KEY"] = "clef-env"
        self.settings.update({"ai": {"provider": "mistral", "model": "m"}})
        ai = self.settings.ai()
        self.assertEqual((ai["apiKey"], ai["keySource"], ai["configured"]), ("clef-env", "env", True))
        self.assertNotIn("clef-env", self.settings.path.read_text(encoding="utf-8"))

    def test_local_services_need_no_key(self):
        self.settings.update({"ai": {"provider": "ollama", "model": "petit-modele"}})
        ai = self.settings.ai()
        self.assertEqual((ai["baseUrl"], ai["configured"]), ("http://127.0.0.1:11434/v1", True))

    def test_bad_values_are_refused(self):
        for patch in (
            {"ai": {"provider": "inconnu"}},
            {"ai": {"provider": "custom", "baseUrl": "ftp://x"}},
            {"ai": {"provider": "custom", "baseUrl": "http://user:pass@x/v1"}},
            {"ai": {"provider": "mistral", "apiKey": "avec espace"}},
            {"ai": {"provider": "mistral", "model": "nom avec espace"}},
            {"ai": {"reasoning": "énorme"}},
            {"voice": {"engine": "gramophone"}},
            {"voice": {"elevenlabs": {"voice": "../x"}}},
            "pas un objet",
        ):
            with self.subTest(patch=patch):
                with self.assertRaises(SettingsError):
                    self.settings.update(patch)

    def test_voice_can_borrow_the_mistral_key_of_the_model(self):
        self.settings.update({"ai": {"provider": "mistral", "apiKey": "clef-mistral", "model": "m"}, "voice": {"engine": "mistral"}})
        voice = self.settings.voice()
        self.assertEqual((voice["engine"], voice["mistral"]["apiKey"], voice["mistral"]["voice"]),
                         ("mistral", "clef-mistral", settings_module.MISTRAL_VOICES[0][0]))
        public = self.settings.public()["voice"]["mistral"]
        self.assertEqual((public["hasKey"], public["ownKey"]), (True, False))

    def test_learner_profile_is_bounded_and_clean(self):
        self.settings.update({"learner": {"name": "  Alex\x00\n ", "about": "x" * 2000}})
        learner = self.settings.load()["learner"]
        self.assertEqual(learner["name"], "Alex")
        self.assertEqual(len(learner["about"]), 600)

    def test_a_file_readable_by_others_is_ignored(self):
        self.settings.update({"ai": {"provider": "mistral", "apiKey": "clef", "model": "m"}})
        os.chmod(self.settings.path, 0o644)
        self.assertEqual(self.settings.ai()["apiKey"], "")


if __name__ == "__main__":
    unittest.main()
