import base64
import io
import tempfile
import time
import unittest
import wave
from pathlib import Path

import speech_text
import voice
from settings import Settings


def wav_bytes(seconds=1.0, rate=16000):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(b"\x01\x00" * int(seconds * rate))
    return buffer.getvalue()


class FakeEngine:
    name = "fake"
    label = "Voix d'essai"

    def __init__(self, heard="c'est quoi une boucle", fail_on=None, delay=0.0):
        self.heard, self.fail_on, self.delay = heard, fail_on, delay
        self.spoken = []
        self.terms = None

    def problem(self):
        return None

    def transcribe(self, audio, terms=()):
        self.terms = list(terms)
        return self.heard

    def speak(self, text, voice_name=None):
        if self.fail_on and self.fail_on in text:
            raise voice.VoiceError("Voix en panne.")
        time.sleep(self.delay)
        self.spoken.append(text)
        return b"RIFF" + text.encode("utf-8"), "audio/wav"


class SpeechTextTests(unittest.TestCase):
    def test_what_is_said_has_no_markup_code_or_interface_block(self):
        text = ("**Presque** : la boucle est bonne. Regarde la `ligne 3`.\n\n```python\nfor i in range(3):\n    print(i)\n```\n\n"
                "Qu'affiche ce code ?\n\n```kenobi-exercice\nfichier: exercice.py\nattendu: 0 à 2\n```")
        self.assertEqual(speech_text.normalize(text), "Presque: la boucle est bonne. Regarde la ligne 3. Qu'affiche ce code?")

    def test_stream_releases_sentences_as_they_end_and_never_a_block(self):
        text = "Voyons. Une boucle répète des instructions.\n\n```kenobi-lecon\nobjectif: Lire\n```"
        segmenter = speech_text.StreamSegmenter()
        released = []
        for index in range(0, len(text), 5):
            released += segmenter.feed(text[index:index + 5])
            if index == 10:
                self.assertEqual(released, ["Voyons."])  # la première phrase part sans attendre la suite
        released += segmenter.finish()
        self.assertEqual(released, ["Voyons.", "Une boucle répète des instructions."])

    def test_swiss_numbers_are_optional_wording(self):
        self.assertEqual(speech_text.spoken_fr("75 lignes et 91 fichiers à 14h30"),
                         "septante-cinq lignes et nonante et un fichiers à quatorze heures trente")


class WavTests(unittest.TestCase):
    def test_recordings_are_checked(self):
        self.assertAlmostEqual(voice.check_wav(wav_bytes(1.5)), 1.5, places=2)
        for bad in (b"", b"pas un wav", wav_bytes(voice.MAX_SECONDS + 1)):
            with self.subTest(size=len(bad)):
                with self.assertRaises(voice.VoiceError):
                    voice.check_wav(bad)

    def test_noise_transcripts_are_recognised(self):
        for text in ("Sous-titres réalisés par la communauté d'Amara.org", "...", "Merci d'avoir regardé cette vidéo !", "[Musique]"):
            self.assertTrue(voice.NOISE_TRANSCRIPTS.match(text), text)
        self.assertIsNone(voice.NOISE_TRANSCRIPTS.match("Merci, j'ai compris la boucle."))


class TurnTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.manager = voice.VoiceManager(Settings(Path(self.directory.name) / "config"), Path(self.directory.name) / "voix")

    def tearDown(self):
        self.manager.close()
        self.directory.cleanup()

    def run_turn(self, engine, reply, **options):
        self.manager.engine = lambda config=None: (engine, "voix")
        seen = {}

        def start_reply(text, cancel):
            seen["text"] = text
            for chunk in reply:
                yield "delta", {"text": chunk}
            yield "done", {"elapsed": 0.1, "firstToken": 0.05}

        return list(self.manager.turn(start_reply, **options)), seen

    def test_a_spoken_turn_is_transcribed_answered_and_said_in_order(self):
        engine = FakeEngine()
        events, seen = self.run_turn(engine, ["Une boucle répète. ", "Par exemple avec for. ", "Tu vois ?"],
                                     audio=wav_bytes(), carry="Kenobi,", terms=["Python"])
        kinds = [kind for kind, _ in events]
        self.assertEqual(kinds[:3], ["stage", "transcript", "stage"])
        self.assertEqual(kinds[-1], "done")
        transcript = next(data for kind, data in events if kind == "transcript")
        # Les mots restés sans réponse au tour précédent ouvrent celui-ci.
        self.assertEqual(transcript["text"], "Kenobi, c'est quoi une boucle")
        self.assertEqual(seen["text"], "Kenobi, c'est quoi une boucle")
        self.assertEqual(engine.terms, ["Python"])
        segments = [data for kind, data in events if kind == "segment"]
        self.assertEqual([segment["index"] for segment in segments], list(range(len(segments))))
        self.assertEqual(" ".join(segment["text"] for segment in segments), "Une boucle répète. Par exemple avec for. Tu vois?")
        self.assertEqual(base64.b64decode(segments[0]["audio"]), b"RIFF" + segments[0]["text"].encode("utf-8"))
        done = events[-1][1]
        self.assertEqual(done["reply"], "Une boucle répète. Par exemple avec for. Tu vois ?")
        self.assertEqual(done["segments"], len(segments))

    def test_noise_or_silence_asks_nothing_of_the_tutor(self):
        events, seen = self.run_turn(FakeEngine(heard="Sous-titres réalisés par la communauté d'Amara.org"), ["x"], audio=wav_bytes())
        self.assertEqual([kind for kind, _ in events], ["stage", "empty"])
        self.assertEqual(seen, {})

    def test_a_written_turn_during_the_call_is_spoken_too(self):
        engine = FakeEngine()
        events, seen = self.run_turn(engine, ["Presque. ", "Regarde la ligne 2."], text="[Exécution — tentative n° 1 …]")
        self.assertEqual(seen["text"], "[Exécution — tentative n° 1 …]")
        self.assertEqual(engine.spoken, ["Presque.", "Regarde la ligne 2."])

    def test_when_the_voice_breaks_the_text_still_arrives(self):
        engine = FakeEngine(fail_on="ligne")
        events, _ = self.run_turn(engine, ["Presque. ", "Regarde la ligne 2. ", "Courage."], audio=wav_bytes())
        done = events[-1][1]
        self.assertEqual(events[-1][0], "done")
        self.assertEqual(done["reply"], "Presque. Regarde la ligne 2. Courage.")
        self.assertEqual(done["voiceError"], "Voix en panne.")
        # Après la panne, plus rien n'est dit : ni trou, ni phrase dans le désordre.
        self.assertEqual(engine.spoken, ["Presque."])

    def test_closing_the_turn_stops_the_model_and_the_voice(self):
        engine = FakeEngine(delay=0.05)
        self.manager.engine = lambda config=None: (engine, "voix")
        state = {}

        def start_reply(text, cancel):
            state["cancel"] = cancel
            for index in range(200):
                if cancel.is_set():
                    state["stopped_at"] = index
                    return
                yield "delta", {"text": f"Phrase numéro {index}. "}
                time.sleep(0.01)
            yield "done", {}

        turn = self.manager.turn(start_reply, audio=wav_bytes())
        for kind, _ in turn:
            if kind == "segment":
                break
        turn.close()
        deadline = time.time() + 3
        while "stopped_at" not in state and time.time() < deadline:
            time.sleep(0.02)
        self.assertTrue(state["cancel"].is_set())
        self.assertLess(state.get("stopped_at", 999), 200)
        spoken = len(engine.spoken)
        time.sleep(0.3)
        self.assertLessEqual(len(engine.spoken), spoken + 1)

    def test_phrases_are_said_once_in_the_chosen_voice_and_kept(self):
        engine = FakeEngine()
        self.manager.engine = lambda config=None: (engine, "voix")
        first = self.manager.phrases("Camille")
        self.assertEqual([item["text"] for item in first["greetings"]][0], "Oui, Camille ?")
        self.assertEqual(len(first["fillers"]), len(voice.FILLERS))
        count = len(engine.spoken)
        self.manager.phrases("Camille")
        self.assertEqual(len(engine.spoken), count)
        self.assertEqual(self.manager.phrases("")["greetings"][0]["text"], "Oui ?")

    def test_local_voice_says_when_it_is_not_installed(self):
        status = self.manager.status()
        self.assertEqual((status["engine"], status["ready"], status["local"]["installed"]), ("local", False, False))
        self.assertIn("n'est pas installée", status["problem"])
        with self.assertRaises(voice.VoiceError):
            self.manager.test()


if __name__ == "__main__":
    unittest.main()
