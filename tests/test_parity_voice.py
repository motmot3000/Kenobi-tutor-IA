"""browser/voice.js hears and speaks like voice.py: same WAV checks, same requests and errors, same call turn.

Python reads prepared answers through a fake opener; the JavaScript version through a fake fetch. No network.
"""
import base64
import contextlib
import io
import json
import re
import struct
import tempfile
import types
import unittest
import urllib.error
import wave

import voice

try:
    from parity_helper import NODE, js
except ImportError:  # run as tests.test_parity_voice
    from tests.parity_helper import NODE, js

HARNESS = "test:voice_harness"
PCM_GUID = b"\x01\x00\x00\x00\x00\x00\x10\x00\x80\x00\x00\xaa\x008\x9bq"
FLOAT_GUID = b"\x03\x00\x00\x00\x00\x00\x10\x00\x80\x00\x00\xaa\x008\x9bq"


def b64(data):
    return base64.b64encode(data).decode("ascii")


def wav_bytes(seconds=1.0, rate=16000, channels=1, width=2):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(width)
        output.setframerate(rate)
        output.writeframes(b"\x01" * width * channels * int(seconds * rate))
    return buffer.getvalue()


def chunk(name, payload, size=None, pad=True):
    data = name + struct.pack("<I", len(payload) if size is None else size) + payload
    return data + (b"\x00" if pad and len(payload) % 2 else b"")


def fmt(tag=1, channels=1, rate=16000, bits=16, extra=b""):
    block = channels * ((bits + 7) // 8)
    return struct.pack("<HHIIHH", tag, channels, rate, rate * block, block, bits) + extra


def extensible(guid, bits=16):
    return fmt(0xFFFE, 1, 16000, bits, struct.pack("<HHI", 22, bits, 4) + guid)


def riff(*chunks, size=None):
    body = b"WAVE" + b"".join(chunks)
    return b"RIFF" + struct.pack("<I", len(body) if size is None else size) + body


RECORDINGS = [
    wav_bytes(1.5), wav_bytes(2, channels=2), wav_bytes(1, width=1), wav_bytes(1, width=3), wav_bytes(0.1, channels=3),
    wav_bytes(60, rate=8000), wav_bytes(61, rate=8000), b"", b"pas un wav", b"RIFF", wav_bytes(1)[:20], wav_bytes(1)[:43],
    riff(chunk(b"fmt ", fmt(bits=12)), chunk(b"data", b"\x00" * 3200)),
    riff(chunk(b"fmt ", fmt(channels=0)), chunk(b"data", b"\x00" * 100)),
    riff(chunk(b"fmt ", fmt(bits=0)), chunk(b"data", b"\x00" * 100)),
    riff(chunk(b"fmt ", fmt(tag=3, bits=32)), chunk(b"data", b"\x00" * 100)),
    riff(chunk(b"fmt ", extensible(PCM_GUID)), chunk(b"data", b"\x00" * 3200)),
    riff(chunk(b"fmt ", extensible(FLOAT_GUID)), chunk(b"data", b"\x00" * 3200)),
    riff(chunk(b"fmt ", fmt(0xFFFE) + struct.pack("<H", 22)), chunk(b"data", b"\x00" * 100)),
    riff(chunk(b"fmt ", fmt()[:12]), chunk(b"data", b"\x00" * 100)),
    riff(chunk(b"data", b"\x00" * 100), chunk(b"fmt ", fmt())),
    riff(chunk(b"fmt ", fmt())),
    riff(chunk(b"LIST", b"INFOabcd"), chunk(b"fmt ", fmt()), chunk(b"data", b"\x00" * 32000)),
    riff(chunk(b"junk", b"abc"), chunk(b"fmt ", fmt()), chunk(b"data", b"\x00" * 32000)),
    riff(chunk(b"junk", b"abc", pad=False), chunk(b"fmt ", fmt()), chunk(b"data", b"\x00" * 32000)),
    riff(chunk(b"fmt ", fmt() + b"\x00"), chunk(b"data", b"\x00" * 100)),
    riff(chunk(b"fmt ", fmt()), chunk(b"data", b"\x00" * 100), size=4),
    riff(chunk(b"fmt ", fmt()), chunk(b"data", b"\x00" * 100), size=0xFFFFFFFF),
    riff(chunk(b"fmt ", fmt()), chunk(b"data", b"", size=0xFFFFFFFF)),
    riff(chunk(b"fmt ", fmt()), chunk(b"data", b"")),
    riff(chunk(b"fmt ", fmt(rate=0)), chunk(b"data", b"\x00" * 20)),
    riff(chunk(b"LIST", b"x" * 10, size=100000), chunk(b"fmt ", fmt()), chunk(b"data", b"\x00" * 100)),
    b"RIFX" + wav_bytes(1)[4:], wav_bytes(1)[:8] + b"WAVX" + wav_bytes(1)[12:],
    riff(chunk(b"fmt ", fmt(channels=2)), chunk(b"data", b"\x00" * 7)),
    riff(chunk(b"fmt ", fmt()), chunk(b"fmt ", fmt(bits=8)), chunk(b"data", b"\x00" * 100)),
]

NOISES = [
    "Sous-titres réalisés par la communauté d'Amara.org", "...", "Merci d'avoir regardé cette vidéo !", "[Musique]",
    "  SOUS-TITRAGE ST' 501  ", "(rires)", "…", "Abonnez-vous !", "MERCI D'AVOIR REGARDÉ", "sous-tıtres", "SOUS-TİTRES",
    "Merci, j'ai compris la boucle.", "Bonjour...", "[x] puis y", "abonnez vous", "", "....", ". .", "!!!", "Sous-titres\n",
    "Sous-titres\n\n", "[Musique]\n", "[Musique]\nencore", "(applaudissements) merci", "—Sous-titres—", "Amara.org",
    "amara.orgue", "« Merci d'avoir regardé »", "Sous_titres", "sous-titr", "Sous-titresé", "… …", "Merci d’avoir regardé",
    "\u0345sous-titres", "ſous-titres", "...\n", "x...", "[a]\n[b]", "(a\nb)",
]


def multipart_fields(request):
    boundary = request.get_header("Content-type").split("boundary=")[1].encode("ascii")
    fields = []
    for part in request.data.split(b"--" + boundary):
        head, separator, value = part.partition(b"\r\n\r\n")
        if not separator or b"filename=" in head:
            continue
        fields.append([re.search(rb'name="([^"]*)"', head).group(1).decode("utf-8"), value[:-2].decode("utf-8")])
    return fields


class FakeOpener:
    def __init__(self, replies, requests):
        self.replies, self.requests, self.index = replies, requests, 0

    def open(self, request, timeout=None):
        content_type = request.get_header("Content-type") or ""
        fields = multipart_fields(request) if content_type.startswith("multipart/") else None
        headers = {name.lower(): value for name, value in request.header_items()}
        if fields is not None:
            headers.pop("content-type")  # the browser writes it itself for a FormData
        self.requests.append({"url": request.full_url, "method": request.get_method(), "headers": headers, "fields": fields,
                              "json": json.loads(request.data) if content_type == "application/json" else None})
        reply = self.replies[min(self.index, len(self.replies) - 1)]
        self.index += 1
        if reply.get("network"):
            raise urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))
        data = base64.b64decode(reply.get("base64", ""))
        if reply.get("status", 200) != 200:
            raise urllib.error.HTTPError(request.full_url, reply["status"], "error", None, io.BytesIO(data))
        return io.BytesIO(data)


@contextlib.contextmanager
def replaying(replies, requests):
    saved = voice._OPENER
    voice._OPENER = FakeOpener(replies, requests)
    try:
        yield
    finally:
        voice._OPENER = saved


def describe(error):
    if isinstance(error, voice.VoiceError):
        return {"error": {"status": error.status, "message": error.message}}
    return {"crash": True}


def answer(body=b"", status=200):
    data = body if isinstance(body, bytes) else (body.encode("utf-8") if isinstance(body, str) else json.dumps(body).encode("utf-8"))
    return {"status": status, "base64": b64(data)}


def python_calls(cases):
    results = []
    for item in cases:
        requests = []
        if item["engine"] == "elevenlabs":
            engine = voice.ElevenLabsVoice(item["key"], item["voice"], item["model"])
        else:
            engine = voice.MistralVoice(item["key"], item["voice"])
        args = [base64.b64decode(item["args"][0]), item["args"][1]] if item["method"] == "transcribe" else item["args"]
        with replaying(item["replies"], requests):
            try:
                value = getattr(engine, item["method"])(*args)
                result = {"audio": b64(value[0]), "mime": value[1]} if item["method"] == "speak" else {"value": value}
            except Exception as error:  # noqa: BLE001 - the kind of failure is compared
                result = describe(error)
        results.append({"result": result, "requests": requests})
    return results


def engine_cases():
    sound = b64(wav_bytes(0.2))
    wav_answer = answer({"audio_data": b64(b"RIFF" + b"\x00" * 60)})
    mistral = {"engine": "mistral", "key": "key-not-real", "voice": "voice_589", "model": None}
    eleven = {"engine": "elevenlabs", "key": "key-not-real", "voice": "voice-id", "model": "eleven_flash_v2_5"}
    many = ["Kenobi", "boucle for", "print", "a", "console.log", "l’élève", "x" * 41, "é" * 40, "é" * 41, "a-b.c'd", "a\nb",
            "mot\n", "_x"] + [f"mot{index}" for index in range(30)]
    cases = []
    for terms in (many, [], ["Python"], "Python", ["Kenobi", "Camille"]):
        cases.append({**mistral, "method": "transcribe", "args": [sound, terms], "replies": [answer({"text": "  bonjour  "})]})
    for body in ({"text": " x "}, {"text": 5}, {"text": None}, {}, [], "\"str\"", b"not json", b"\xef\xbb\xbf{\"text\": \"bom\"}",
                 b'{"text": "caf\xff"}', '{"text": NaN}', {"text": "\u00a0espaces\u3000"}):
        cases.append({**mistral, "method": "transcribe", "args": [sound, []], "replies": [answer(body)]})
        cases.append({**eleven, "method": "transcribe", "args": [sound, ["Python"]], "replies": [answer(body)]})
    for status in (401, 403, 404, 429, 500, 503):
        cases.append({**mistral, "method": "transcribe", "args": [sound, []], "replies": [answer(b"{}", status)]})
        cases.append({**eleven, "method": "speak", "args": ["Salut"], "replies": [answer(b"{}", status)]})
    cases.append({**mistral, "method": "transcribe", "args": [sound, []], "replies": [{"network": True}]})
    cases.append({**mistral, "method": "transcribe", "args": [sound, []], "replies": [answer(b"x" * 65537)]})
    cases.append({**mistral, "method": "transcribe", "args": [sound, []], "replies": [answer(b"{\"text\": \"" + b"y" * 65520 + b"\"}")]})
    for body in ({"audio_data": b64(b"RIFF" + b"\x00" * 60)}, {"audio_data": "!!"}, {"audio_data": b64(b"RIFF" + b"\x00" * 61).rstrip("=")},
                 {"audio_data": None}, {}, {"audio_data": b64(b"ID3" + b"\x00" * 60)}, {"audio_data": b64(b"RIFF" + b"\x00" * 10)},
                 {"audio_data": 5}, [], b"not json", {"audio_data": " " + b64(b"RIFF" + b"\x00" * 60)},
                 {"audio_data": b64(b"RIFF" + b"\x00" * 60) + "\n"}, {"audio_data": "QQ=="}, {"audio_data": "QQ="},
                 {"audio_data": "=QQ="}, {"audio_data": "QUJD="}, {"audio_data": "QQ==QQ=="}, {"audio_data": "é"}):
        cases.append({**mistral, "method": "speak", "args": ["Salut"], "replies": [answer(body)]})
    cases.append({**mistral, "method": "speak", "args": ["é" * 5000], "replies": [wav_answer]})
    cases.append({**mistral, "method": "speak", "args": ["😀" * 4100, "voice_592"], "replies": [wav_answer]})
    cases.append({**mistral, "voice": "", "method": "speak", "args": ["Salut", None], "replies": [wav_answer]})
    for voice_id in ("voix d'été/1", "abc", "a b!*'()~_.-", "😀"):
        for model in ("eleven_flash_v2_5", "eleven_multilingual_v2", "eleven_turbo_v2", "FLASH"):
            cases.append({**eleven, "voice": voice_id, "model": model, "method": "speak", "args": ["Bonjour " * 600],
                          "replies": [answer(b"\xff" * 300)]})
    cases.append({**eleven, "method": "speak", "args": ["Salut", "autre voix"], "replies": [answer(b"\xff" * 199)]})
    voices = [
        {"voice_id": "b", "name": "Zoé", "labels": {"language": "fr", "gender": "female"}, "category": "premade"},
        {"voice_id": "a", "name": "adam", "labels": {}}, {"voice_id": "c", "name": "Céline", "verified_languages": [
            {"language": "en"}, {"language": "fr"}, "x", {"language": None}]},
        {"voice_id": "d", "name": "Bob", "labels": {"language": "en-US", "gender": "male" * 5}}, {"voice_id": 7, "name": None},
        {"name": "sans identifiant"}, {"voice_id": "", "name": "vide"}, "pas un objet", {"voice_id": "e", "name": "Émile " * 20,
        "labels": {"language": "french-canadian-x"}, "category": "professional-cloned-voice"}, {"voice_id": "f", "name": "ÉLISE",
        "labels": "pas un objet", "verified_languages": {"language": "fr"}}, {"voice_id": "g", "name": "élise"},
        {"voice_id": "h", "name": "𝒜", "labels": {"language": "fr-CA"}}, {"voice_id": "i", "name": "\uffff", "labels": {"language": "fr"}},
    ]
    for replies in ([answer({"voices": voices})], [answer(b"{}", 404), answer({"voices": voices})], [answer(b"{}", 401)],
                    [answer(b"{}", 500), answer(b"{}", 500)], [{"network": True}, answer({"voices": voices[:3]})],
                    [answer({"voices": [{"voice_id": f"v{index}", "name": f"Voix {index:03d}"} for index in range(250)]})],
                    [answer({"voices": None})], [answer({"voices": {"a": 1}})], [answer({"voices": 5})], [answer(b"[]")],
                    [answer(b"pas du JSON")]):
        cases.append({**eleven, "method": "voices", "args": [], "replies": replies})
    return cases


class FakeEngine:
    name = "fake"
    label = "Voix d'essai"

    def __init__(self, heard="c'est quoi une boucle", fail_on=None, problem=None):
        self.heard, self.fail_on, self._problem = heard, fail_on, problem
        self.spoken = []
        self.terms = None

    def problem(self):
        return self._problem

    def transcribe(self, audio, terms=()):
        self.terms = list(terms)
        return self.heard

    def speak(self, text, voice_name=None):
        if self.fail_on and self.fail_on in text:
            raise voice.VoiceError("Voix en panne.")
        self.spoken.append(text)
        return b"RIFF" + text.encode("utf-8"), "audio/wav"


class ChatError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


def manager(swiss=False, fillers=True):
    settings = types.SimpleNamespace(voice=lambda: {"engine": "fake", "local": {"voice": "pierre"}, "swissNumbers": swiss,
                                                    "fillers": fillers})
    return voice.VoiceManager(settings, tempfile.gettempdir())


PHRASES = [
    {"name": name, "swiss": swiss, "fillers": fillers}
    for name in ("Camille", "", "<b>Camille</b> !", "  Jean-Pierre O'Neil  ", "x" * 50, "Zoé 😀", "Léa\nNouvelle", "  ", "a_b",
                 "ı İ", "\u0345x", None)
    for swiss, fillers in ((False, True), (True, False))
] + [{"name": "Léa", "swiss": False, "fillers": True, "failOn": "Voyons"}]


def python_phrases(cases):
    results = []
    for item in cases:
        engine = FakeEngine(fail_on=item.get("failOn"))
        voices = manager(item["swiss"], item["fillers"])
        voices.engine = lambda config=None, engine=engine: (engine, "voix")
        value = voices.phrases(item["name"])
        results.append({"value": value, "spoken": sorted(engine.spoken)})
    return results


def turn_scenarios():
    sound = b64(wav_bytes(1))
    long_reply = ("Une boucle for parcourt une séquence, élément par élément, et exécute son bloc pour chacun d'eux, "
                  "ce qui évite de répéter le même code. ") * 6 + "Tu veux un exemple ?"
    base = {"audio": None, "text": None, "carry": "", "terms": [], "heard": "c'est quoi une boucle", "failOn": None,
            "problem": None, "swiss": False, "raiseAt": None, "bug": False, "errorStatus": None, "errorMessage": None}
    scenarios = [
        {"audio": sound, "chunks": ["Une boucle répète. ", "Par exemple avec for. ", "Tu vois ?"], "carry": "Kenobi,", "terms": ["Python"]},
        {"audio": sound, "heard": "Sous-titres réalisés par la communauté d'Amara.org", "chunks": ["x"]},
        {"audio": sound, "heard": "", "chunks": ["x"]},
        {"audio": sound, "heard": "  oui  ", "chunks": ["D'accord."], "carry": "  avant  "},
        {"text": "[Exécution — tentative n° 1 …]", "chunks": ["Presque. ", "Regarde la ligne 2."]},
        {"text": "   ", "chunks": ["x"]},
        {"chunks": ["x"]},
        {"audio": sound, "failOn": "ligne", "chunks": ["Presque. ", "Regarde la ligne 2. ", "Courage."]},
        {"audio": sound, "problem": "La voix locale n'est pas branchée.", "chunks": ["x"]},
        {"text": "Run", "problem": "Pas de voix.", "chunks": ["Bien. ", "Continue."]},
        {"audio": b64(b"pas un wav"), "chunks": ["x"]},
        # No finished sentence before the failure: whether Python's speaker thread says it first is a race.
        {"text": "Salut", "chunks": ["Un", " deux "], "raiseAt": 1, "errorStatus": 409, "errorMessage": "Aucun modèle d'IA n'est branché."},
        {"text": "Salut", "chunks": ["Un. "], "raiseAt": 0, "bug": True},
        {"text": "Combien ?", "chunks": ["Il y a 75 lignes ", "et 91 fichiers à 14h30. ", "Voilà."], "swiss": True},
        {"text": "Code", "chunks": ["Voici le code.\n\n```python\nprint(1)\n```\n\nTu vois ?\n\n```kenobi-boutons\nbouton: Oui\n```"]},
        {"audio": sound, "chunks": [long_reply[index:index + 9] for index in range(0, len(long_reply), 9)], "carry": "Et"},
    ]
    return [{**base, **scenario} for scenario in scenarios]


def python_turns(scenarios):
    results = []
    for scenario in scenarios:
        engine = FakeEngine(scenario["heard"], scenario["failOn"], scenario["problem"])
        voices = manager(scenario["swiss"])
        voices.engine = lambda config=None, engine=engine: (engine, "voix")

        def start_reply(text, cancel, scenario=scenario):
            for index, piece in enumerate(scenario["chunks"]):
                if scenario["raiseAt"] == index:
                    if scenario["bug"]:
                        raise TypeError("x is not a function")
                    raise ChatError(scenario["errorStatus"], scenario["errorMessage"])
                yield "delta", {"text": piece}
            yield "done", {"elapsed": 0.1, "firstToken": 0.05, "heardText": text}

        audio = base64.b64decode(scenario["audio"]) if scenario["audio"] is not None else None
        events = [[kind, data] for kind, data in voices.turn(start_reply, audio=audio, text=scenario["text"],
                                                              carry=scenario["carry"], terms=scenario["terms"])]
        results.append({"events": events, "spoken": engine.spoken, "terms": engine.terms})
    return results


def split_events(events):
    """Events without what depends on time; the pieces of speech apart (Python says them from another thread)."""
    others, segments = [], []
    for kind, data in events:
        data = dict(data)
        if kind == "segment":
            segments.append(data)
            continue
        if "sttMs" in data:
            data["sttMs"] = None if data["sttMs"] is None else "ms"
        if kind == "done":
            data.pop("total")
            data["firstAudio"] = None if data["firstAudio"] is None else "s"
        others.append([kind, data])
    return others, segments


@unittest.skipUnless(NODE, "node absent")
class VoiceParityTests(unittest.TestCase):
    maxDiff = 6000

    def test_recordings_are_checked_the_same(self):
        expected = []
        for data in RECORDINGS:
            try:
                expected.append({"seconds": voice.check_wav(data)})
            except Exception as error:  # noqa: BLE001
                expected.append(describe(error))
        results = js(HARNESS, "checkWav", [b64(data) for data in RECORDINGS])
        for index, (result, wanted) in enumerate(zip(results, expected)):
            self.assertEqual(result, wanted, f"recording {index}")
        self.assertIn({"crash": True}, expected)  # the RuntimeError of Python's wave module, kept as a fault

    def test_noise_transcripts_are_recognised_the_same(self):
        self.assertEqual(js(HARNESS, "noise", NOISES), [bool(voice.NOISE_TRANSCRIPTS.match(text)) for text in NOISES])

    def test_engines_send_the_same_requests_and_read_the_same_answers(self):
        cases = engine_cases()
        expected = python_calls(cases)
        for item, result, wanted in zip(cases, js(HARNESS, "calls", cases, timeout=120), expected):
            with self.subTest(engine=item["engine"], method=item["method"], args=str(item["args"])[:80], replies=str(item["replies"])[:80]):
                self.assertEqual(result, wanted)

    def test_the_local_prompt_is_the_same(self):
        lists = [[], ["Kenobi"], ["Kenobi", "Camille", "Python", "boucle for"], [f"terme{index}" for index in range(60)],
                 ["é😀" * 100]]
        expected = []
        for terms in lists:
            local = voice.LocalVoice(tempfile.gettempdir())
            seen = {}

            def request(payload, timeout=90, seen=seen):
                seen["prompt"] = payload["prompt"]
                return {"ok": True, "text": ""}

            local._request = request
            local.transcribe(b"", terms)
            expected.append(seen["prompt"])
        self.assertEqual(js(HARNESS, "localPrompts", lists), expected)

    def test_phrases_are_built_the_same(self):
        results = js(HARNESS, "phrases", PHRASES)
        for item, result, wanted in zip(PHRASES, results, python_phrases(PHRASES)):
            if item.get("failOn"):
                # After a failure, how many sentences Python's thread pool had already said depends on timing.
                self.assertEqual(result["value"], wanted["value"], item)
            else:
                self.assertEqual(result, wanted, item)

    def test_call_turns_give_the_same_events(self):
        scenarios = turn_scenarios()
        results = js(HARNESS, "turns", scenarios)
        for index, (result, wanted) in enumerate(zip(results, python_turns(scenarios))):
            with self.subTest(index=index):
                self.assertEqual(split_events(result["events"]), split_events(wanted["events"]))
                self.assertEqual((result["spoken"], result["terms"]), (wanted["spoken"], wanted["terms"]))


if __name__ == "__main__":
    unittest.main()
