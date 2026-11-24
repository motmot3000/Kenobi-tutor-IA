"""browser/speech.js says the same thing as speech_text.py: same text, same pieces, same numbers."""
import hashlib
import random
import re
import unicodedata
import unittest

import speech_text

try:
    from parity_helper import NODE, js
except ImportError:  # run as tests.test_parity_speech
    from tests.parity_helper import NODE, js

HARNESS = "test:speech_harness"

FRAGMENTS = [
    "Bonjour !", "Salut, ça va ?", "Une boucle répète des instructions.", "Regarde la ligne 3.", "Tu vois ?",
    "C'est **important** : retiens-le.", "Le mot *clé* est `for`.", "__souligné__ et ~~barré~~ ici.",
    "Voici un lien : https://example.com/page?x=1&y=2 et www.python.org.", "[la doc](https://docs.python.org) explique.",
    "![schéma](img.png) montre la boucle.", "# Titre\n", "## Sous-titre\nTexte.\n", "- premier point\n- second point\n",
    "* étoile\n+ plus\n• puce\n", "1. un\n2) deux\n10. dix\n", "> une citation\n>suite\n", "| a | b |\n|---|---|\n| 1 | 2 |\n",
    "---\n", "===\n", "* * *\n", "```python\nfor i in range(3):\n    print(i)\n```\n", "```kenobi-qcm\nchoix: a\nchoix: b\nreponse: 1\n```",
    "```kenobi_boutons\nbouton: Oui\n```\n", "``` kenobi-lecon\nobjectif: x\n```", "```\ncode sans langage\n",
    "```kenobi-exercice\nfichier: exercice.py\nattendu: 1, 2 et 3\n```\nEt après ?", "Ok.```python\nx=1\n```Suite.",
    "```kenobi-pret\nbouton: C'est parti !\n", "Le code `print(\"x\")` affiche x.", "Il fait 20°C dehors, 5 °C la nuit, 3°Cé.",
    "Ça coûte 5 € ou 10€.", "50 % des élèves, 3% du temps.", "A -> B, C → D, E => F.", "≈ 3 secondes.", "Tom & Jerry, etc.",
    "Etc. ETC. etc. etcetera.", "😀 Bravo ! 👍🏽 👨‍👩‍👧 ❤️", "Rendez-vous à 14h30, puis à 9 h 05, 0h00, 1h, 23h59 et 12 h.",
    "J'ai 3 pommes, 17 poires, 71 kiwis, 80 noix, 91 figues, 101 dattes, 999 999 grains et 1000000 d'atomes.",
    "Version 3.11, page 42-43, x2 et 2x, 007.", "Quoi ?! Vraiment !!! Hmm…… ok...", "Il a dit « bonjour. » Puis il est parti.",
    "(Voir plus haut.) Ensuite.", "[Note.] Fin.", "Non\u00a0! Oui\u202f? Peut-être\u2009: oui.", "Tab\tici\x0bet là\x0c.",
    "Ligne\r\nWindows\rMac\u2028sép\u2029para\x85NEL\x1cFS\x1dGS\x1eRS\x1fUS.", "Première phrase très courte. ",
    "Fin sans point", "Question?Réponse!Exclamation.Point", "Un. Deux.  Trois.\tQuatre.\nCinq.", "M. Dupont est là.",
    "Les nombres ٣ et ۴۵ et 𝟏𝟐 et ١٤h٣٠.", "Ünïcödé ÀÉÎÕÜ œ æ ß.", "Emoji au milieu😀du mot.", "\ufeffBOM au début.",
    "Zero\u200dwidth\ufe0fjoiner.", "snake_case_name et _privé_ et *gras* et 2*3*4.", "Le ` seul.", "```", "````", "**", "*a*b*",
    " ", "", "\n\n\n", "…", "Fin.»\n", "Ah!» Bon.", "Liste:\n- a\n- b", "Code inline `a\nb` coupé.", "x" * 330 + ".",
    "Mot " * 90 + "fin.", "Un, deux; trois: quatre, " * 20, "Et\u3000puis\u2003encore.", "Point.\u00a0Suite.",
]


def long_sentence(rng):
    words = ["boucle", "variable", "fonction", "liste", "élève", "programme", "valeur", "indice", "condition", "résultat"]
    parts = []
    for _ in range(rng.randint(30, 120)):
        parts.append(rng.choice(words) + rng.choice(["", "", "", ",", ";", ":"]))
    return " ".join(parts) + rng.choice([".", "", "!", " ?"])


def corpus():
    rng = random.Random(20261124)
    texts = list(FRAGMENTS)
    for _ in range(20):
        texts.append(long_sentence(rng))
    for _ in range(450):
        pieces = [rng.choice(FRAGMENTS) if rng.random() < 0.85 else long_sentence(rng) for _ in range(rng.randint(1, 7))]
        texts.append("".join(piece + rng.choice(["", " ", "\n", "\n\n", "\r\n"]) for piece in pieces))
    return texts


def chunkings(text, rng):
    result = [list(text), [text]]
    for _ in range(3):
        chunks, index = [], 0
        while index < len(text):
            size = rng.randint(1, 15)
            chunks.append(text[index:index + size])
            index += size
        result.append(chunks)
    return result


def python_all(text):
    return {
        "strip": speech_text.strip_interface_blocks(text),
        "normalize": speech_text.normalize(text),
        "sentences": speech_text.sentences(text),
        "segments": speech_text.split_segments(text),
        "segmentsWide": speech_text.split_segments(text, speech_text.SEGMENT_MAX),
        "segmentsSmall": speech_text.split_segments(text, 30, 60),
        "cut": speech_text._cut_long(text, 50),
        "spoken": speech_text.spoken_fr(text),
        "spokenNormal": speech_text.spoken_fr(speech_text.normalize(text)),
    }


def python_stream(chunks):
    segmenter = speech_text.StreamSegmenter()
    released = [segmenter.feed(chunk) for chunk in chunks]
    released.append(segmenter.finish())
    return released


def numbers_text(start, end):
    return "\n".join(str(n) for n in range(start, end))


@unittest.skipUnless(NODE, "node absent")
class SpeechParityTests(unittest.TestCase):
    maxDiff = 4000

    def test_every_function_gives_the_same_text(self):
        texts = corpus()
        results = js(HARNESS, "all", texts)
        self.assertEqual(len(results), len(texts))
        for text, result in zip(texts, results):
            self.assertEqual(result, python_all(text), repr(text[:200]))

    def test_streaming_the_same_chunks_releases_the_same_pieces(self):
        rng = random.Random(7)
        cases = [chunks for text in corpus()[:260] for chunks in chunkings(text, rng)]
        results = js(HARNESS, "stream", cases)
        for chunks, result in zip(cases, results):
            self.assertEqual(result, python_stream(chunks), repr("".join(chunks)[:200]))

    def test_numbers_from_0_to_999999(self):
        expected = hashlib.sha256(speech_text.spoken_fr(numbers_text(0, 1_000_000)).encode("utf-8")).hexdigest()
        if js(HARNESS, "numbersDigest", 0, 1_000_000) == expected:
            return
        for start in range(0, 1_000_000, 50_000):  # find the first difference, to report it
            spoken = js(HARNESS, "numbers", start, start + 50_000)
            self.assertEqual(spoken, speech_text.spoken_fr(numbers_text(start, start + 50_000)).split("\n"))
        self.fail("digests differ")

    def test_hours_and_numbers_in_context(self):
        forms = []
        for hours in range(100):
            for minutes in range(0, 100, 7):
                forms += [f"{hours}h{minutes:02d}", f"{hours} h {minutes:02d}", f"à {hours}h", f"{hours:02d}h{minutes % 10}",
                          f"{hours}h{minutes:02d}x", f"x{hours}h", f"{hours}H{minutes:02d}", f"{hours}h{minutes:03d}"]
        text = " ; ".join(forms)
        self.assertEqual(js(HARNESS, "all", [text])[0]["spoken"], speech_text.spoken_fr(text))

    def test_unicode_characters_are_classed_like_python(self):
        skip = [(0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xA000, 0xA4CF), (0xAC00, 0xD7AF), (0xD800, 0xDFFF), (0xE010, 0xF8FF),
                (0x17000, 0x18CFF), (0x1B170, 0x1B2FF), (0x20000, 0x2FFFF)]
        codes = [code for code in range(0x30000) if not any(low <= code <= high for low, high in skip)
                 and unicodedata.category(chr(code)) != "Cn"]
        results = js(HARNESS, "probes", codes, timeout=120)
        space, word, digit = re.compile(r"\s"), re.compile(r"\w"), re.compile(r"\d")
        newer = []
        for code, result in zip(codes, results):
            char = chr(code)
            classes = [bool(space.match(char)), bool(word.match(char)), bool(digit.match(char)),
                       unicodedata.category(char) in ("So", "Sk", "Co")]
            if result["classes"] != classes:
                newer.append(code)  # another Unicode version in Node: not a difference of the port
                continue
            expected = [speech_text.normalize("a" + char + "b"), speech_text.normalize(char + "*x* y"),
                        speech_text.spoken_fr("1" + char + "2"), speech_text.sentences("Fin." + char + "Suite")]
            self.assertEqual(result["out"], expected, f"U+{code:04X}")
        self.assertLess(len(newer), 100, [f"U+{code:04X}" for code in newer[:20]])


if __name__ == "__main__":
    unittest.main()
