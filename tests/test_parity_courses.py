"""Parity of browser/courses.js with courses.py, the reference: same outline, same parsed model answer, same course,
same notices and errors, for the same document and the same (stubbed) model answer. No network."""
import json
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

import courses
import providers
from parity_helper import NODE, JsError, js_many
from test_courses import ANALYSIS, DOCUMENT

ROOT = Path(__file__).resolve().parent.parent
PLAN = (ROOT / "data" / "plan-formation-python.md").read_text(encoding="utf-8")
NOW = "2026-11-24T09:30:00+00:00"
AI = {"provider": "stub", "label": "Stub", "kind": "openai", "baseUrl": "http://127.0.0.1:9", "model": "stub-model",
      "apiKey": "", "keySource": "", "reasoning": "none", "configured": True, "keyless": True}


def long_document():
    parts = ["# Formation longue\n\nUn cours très long pour éprouver les limites.\n"]
    for module in range(1, 61):
        parts.append(f"\n## Module {module} — Thème {module}\n\nDescription du module {module}.\n")
        for lesson in range(1, 7):
            parts.append(f"\n### Leçon {lesson} — Notion {module}.{lesson}\n\n- Objectif : savoir {lesson}\n"
                         "- Notions : a, b\n- Exercice\n- Bonus\n")
    text = "".join(parts) + "\n## Annexe\n\n"
    filler = "Paragraphe de remplissage avec des mots variés et des accents éèà.\n"
    return text + filler * ((250_000 - len(text)) // len(filler))


DOCUMENTS = {
    "plan": PLAN,
    "template": courses.TEMPLATE,
    "git": DOCUMENT,
    "long": long_document(),
    "lists-only": (
        "Un cours sans aucun titre, seulement des listes.\n\n- variables\n- boucles\n1. fonctions\n2) classes\n"
        "* [x] tests\n+ [ ] modules\n   - indenté de trois\n    - indenté de quatre\n٣. chiffre arabe\n"
        "１) chiffre pleine chasse\n1234. quatre chiffres\n-collé\n- **gras** et `code`\n- ### dièses\n-  \n"
        "\x1c- espace Python\n\u00a0- espace insécable\n\ufeff- BOM\n"),
    "paragraph-only": "Juste un paragraphe, assez long pour être un document, sans titre ni liste.\n",
    "single-h1": "# Python pour tous\n\nUne introduction\nsur deux lignes.\n\nSecond paragraphe.\n\nRien d'autre ici.\n",
    "multi-h1": "# Partie A\n\n## Chapitre 1\n\nTexte.\n\n# Partie B\n\n## Chapitre 2\n- point\n\n# Partie C\n",
    "numbered": (
        "# Cours numéroté\n\n## Module 1 — Variables\n\n### Leçon 1 — Affecter\n\n- Objectif : affecter\n"
        "### Séance 2) Types\n\n## Chapitre 2 : Boucles\n\n### Étape 3. for\n### unİté 4 – while\n"
        "## Partie 3. Fonctions\n\n- def\n- return\n- portée\n## Bloc ٤ : Projet\n"),
    "goals": (
        "# Objectifs partout\n\nIntro.\n\n## Objectifs du cours\n\n- savoir a\n- savoir b\n\n"
        "## Compétences visées\n\n1. compétence c\n\n## But du cours\n\n- but d\n\n## Finalités\n\n- finalité e\n"
        "## À la fin du cours\n\n- fin f\n\n## Acquis visés\n\n- acquis g\n\n## Module 1\n\n### Objectifs\n\n"
        "- objectif de leçon ignoré\n\n### Leçon 1\n\n- Objectif : ok\n"),
    "not-modules": (
        "# Cours\n\n## Prérequis\n\n- bases\n\n## Pré-requis techniques\n\n## Ressources utiles\n\n"
        "## Bibliographie\n\n## Annexe A\n\n## Glossaire\n\n## Licence\n\n## Sommaire\n\n## Table des matières\n\n"
        "## À propos\n\n## Références\n\n## Mode d'emploi\n\n## Introduction\n\n## Introduction générale\n\n"
        "## Introduction à Python\n\n### Installer\n\n### Prérequis de la leçon\n\n## Présentation du cours\n\n"
        "## Conclusion de la formation\n\n## Évaluation\n\n## Organisation\n\n## Fonctionnement général\n\n"
        "## Évaluation finale\n\n- projet\n"),
    "fences": (
        "# Code\n\n## Module 1\n\n```python\n# pas un titre\n- pas un item\n```\n\n### Leçon 1\n\n```js\n## non\n```\n"
        "- vrai objectif\n\n## Module 2\n\n  ```bash\n# toujours du code\n  ```\n\n- item du module 2\n"),
    "fence-languages": "# Langages\n\n## M\n\n```Python3\nx\n```\n\n```c++\ny\n```\n\n``` JavaScript\nz\n```\n",
    "fence-newline": "# Langage après un saut de ligne\n\n## M\n\n```\npython\nprint(1)\n```\n",
    "fence-sql": "# Base de données\n\n## Requêtes\n\n```sql\nSELECT 1;\n```\n\n```pythoné\nx\n```\n",
    "fence-unicode-case": "# Casse Unicode\n\n## M\n\n```ſh\nls\n```\n\n```javascrİpt\nx\n```\n\n```JAVASCRıPT\ny\n```\n",
    "fence-none": "# Pas de langage reconnu\n\n## M\n\n```pythonic\nx\n```\n\n```text\ny\n```\n\n```gopher\nz\n```\n",
    "fence-unclosed": "# Bloc ouvert\n\n## Module A\n\n```python\n## Module caché\n- item caché\n",
    "headings-edge": (
        "# Titres limites ##\n\n##   Espacé   \n\n####### sept dièses\n\n#SansEspace\n\n## C#\n\n## Titre #pas fin\n\n"
        "##\tTabulation\n\n## \x1cespace Python\n\n##\u00a0insécable\n\n##    \n\n## **Gras** et `code`\n\n### Leçon ###\n"
        "## \ufeffBOM\n"),
    "deep": "### Niveau trois\n\nTexte.\n\n#### Niveau quatre\n\n- a\n\n##### Niveau cinq\n\n### Autre trois\n\n- b\n",
    "crlf": "# Titre Windows\r\n\r\nIntro du cours.\r\n\r\n## Module\r\n\r\n### Leçon\r\n\r\n- item\r\n",
    "emoji": "# Cours 😀 émoji\n\n## Module 🚀 fusée\n\n### Leçon 🐍 serpent\n\n- objectif 😀😀\n\n" + "😀" * 200 + "\n",
    "items-overflow": ("# Beaucoup\n\n## Module\n\n" + "".join(f"- point {i}\n" for i in range(20)) +
                       "\n## Module 2\n\n### Leçon\n\n" + "".join(f"- objectif {i}\n" for i in range(6))),
    "first-paragraph": (
        "# Titre\n\n| tableau |\n> citation\n- liste\n* liste\n---\n```\ncode\n```\nVrai paragraphe.\n\n"
        "## Module\n\n#### sous-titre\nTexte du module\nsuite.\n\nAutre.\n"),
    "long-lines": ("# " + "T" * 300 + "\n\n" + "Description " * 100 + "\n\n## " + "Module " * 50 + "\n\n- " +
                   "Objectif " * 40 + "\n- " + "é😀" * 120 + "\n"),
}
FILENAMES = ("cours.md", "Cours.MARKDOWN", "notes.txt", "x.mdx", "", None, "cours.md\n", "Un nom long " * 15 + ".md")
ANALYSIS_EMOJI = {**ANALYSIS, "title": "Git 😀 : les bases", "notes": "Évaluation 🚀 sur un projet neuf."}


def fixed_datetime(value):
    moment = datetime.fromisoformat(value)

    class Fixed(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment
    return Fixed


def outcome(run):
    try:
        return ("ok", run())
    except Exception as error:  # the JS port throws the same names and messages
        return ("error", type(error).__name__, str(error))


def js_outcome(answer):
    return ("error", answer.name, answer.message) if isinstance(answer, JsError) else ("ok", answer)


def py_import(markdown, filename, script, ai=AI, existing_ids=(), now=NOW):
    calls, progress = [], []

    def stream_chat(config, system, messages, *, max_tokens=4000, temperature=0.4, timeout=180, cancel=None):
        calls.append({"config": config, "system": system, "messages": messages, "maxTokens": max_tokens,
                      "temperature": temperature, "timeout": timeout * 1000})
        for chunk in script.get("chunks", []):
            yield "delta", chunk
        if script.get("runtime"):
            raise RuntimeError(script["runtime"])
        if script.get("error"):
            error = script["error"]
            raise providers.ProviderError(error["status"], error["message"], error.get("code", ""))
        yield "done", {"elapsed": 0.1, "firstToken": 0.05, "truncated": False, "model": "stub"}

    with mock.patch.object(courses.providers, "stream_chat", stream_chat), \
            mock.patch.object(courses, "datetime", fixed_datetime(now)):
        try:
            course, notice = courses.import_course(markdown, filename, ai=ai, existing_ids=existing_ids,
                                                   on_progress=progress.append)
            return {"course": course, "notice": notice, "calls": calls, "progress": progress}
        except Exception as error:
            return {"error": [type(error).__name__, str(error)], "calls": calls, "progress": progress}


def py_build(raw, options):
    return outcome(lambda: courses.build_course(
        raw, existing_ids=options.get("existingIds", ()), filename=options.get("filename", ""),
        chars=options.get("chars", 0), analysis=options.get("analysis", "ia"), headings=options.get("headings"),
        now=datetime.fromisoformat(options.get("now", NOW))))


def build_cases():
    opts = {"existingIds": [], "filename": "cours.md", "chars": 1234, "analysis": "ia", "headings": None, "now": NOW}
    one = {"title": "M", "lessons": ["L"]}
    cases = [
        (ANALYSIS, dict(opts, existingIds=["git"], filename="git.md", chars=900,
                        headings=courses.document_headings(DOCUMENT))),
        (ANALYSIS, opts), (ANALYSIS_EMOJI, dict(opts, headings=[])), (ANALYSIS, dict(opts, analysis="titres")),
        (courses.outline(PLAN, "plan.md"), dict(opts, headings=courses.document_headings(PLAN), analysis="titres")),
        (courses.outline(courses.TEMPLATE, "modele.md"), dict(opts, headings=courses.document_headings(courses.TEMPLATE))),
    ]
    titles = [
        ("Module 3 — Les boucles", None), ("Chapitre 2 : Fonctions", ""), ("PARTIE 1. Intro", None),
        ("Leçon 4) Classes", None), ("Séance 12 – Révisions", None), ("unİté 5 — Turc", None), ("UNıTE 6 - x", None),
        ("ſéance 7 : s long", None), ("Étape ٣ : chiffres arabes", None), ("Bloc １２ – pleine chasse", None),
        ("Module 1", None), ("Module1:Collé", None), ("😀 Émoji 🚀 et accents àéîõü", None), ("😀🚀", None), ("", None),
        (None, "Court"), ("A" * 200, None), ("Un titre très long qui dépasse largement vingt-quatre caractères", None),
        ("abcdefghijklmnopqrstuvw-xyz", None), ("**Gras** et `code` # pas un titre", None),
        ("### Titre avec dièses", None), (["liste", "titre"], None), ({"a": 1, "b": [None, True]}, None), (42, None),
        (2.5, None), (True, None), (0, None), ("  \t\n espaces  \x85\u3000", None), ("İstanbul ﬁ ß Æ ø Ł", None),
        ("Ωμέγα", None), ("x\u0301\u0302", None), ("Titre", "Un nom court beaucoup trop long pour la limite"),
        ("Titre", "**court**"), ("Titre", "😀" * 30), ("Titre", ["a"]), ("Titre", 0),
    ]
    for title, short in titles:
        raw = {"title": title, "modules": [one]}
        if short is not None:
            raw["shortTitle"] = short
        cases.append((raw, opts))
    for existing, title in ((["git"], "Git"), (["git", "git-2", "git-3"], "Git"), (["cours"], ""),
                            (["git"] + [f"git-{n}" for n in range(2, 12)], "Git"),
                            (["abcdefghijklmnopqrstuvwx"], "abcdefghijklmnopqrstuvwxyz"),
                            (["abcdefghijklmnopqrstu-wx"], "abcdefghijklmnopqrstu-wxyz"), ([None, 3, "x"], "x"),
                            ("git", "Git"), (["cours-importe"], None)):
        cases.append(({"title": title, "modules": [one]}, dict(opts, existingIds=existing)))
    lessons = ["L1", 5, None, ["x"], {"title": ""}, {"title": "  "}, {"title": "**L2**"}, {"duration": 30},
               {"title": "Leçon 3 — Numérotée", "duration": 45}, {"title": "### L4", "objectives": "une seule"}]
    module_variants = [
        "x", {}, None, [], ["stop"], [one, "stop", {"title": "B", "lessons": ["L2"]}],
        [{"title": "", "lessons": ["L"]}, {"title": "  ", "lessons": ["L"]}, {"title": "###", "lessons": ["L"]}, one],
        [{"title": "Sans leçon"}, {"title": "Leçons vides", "lessons": []}, {"title": "Leçons texte", "lessons": "L"}, one],
        [{"title": "Mélange", "lessons": lessons}],
        [{"title": f"Module {index}", "lessons": [f"Leçon {index}"]} for index in range(45)],
        [{"title": "Un seul", "lessons": [f"Leçon {index}" for index in range(35)]}],
        [{"title": f"M{index}", "lessons": [f"Leçon {index}.{n}" for n in range(30)]} for index in range(12)],
        [{"title": "Même titre", "lessons": ["Même leçon", "Même leçon", "Même leçon"]},
         {"title": "Même titre", "lessons": ["Même leçon"]}],
        [{"title": "Texte long " * 30, "description": "D " * 400, "brief": "B " * 500, "source": "S " * 150,
          "lessons": [{"title": "T " * 100, "objectives": ["O " * 200, "a", "a", "b", "c", "d"]}]}],
    ]
    for modules in module_variants:
        cases.append(({"title": "Variantes", "modules": modules}, opts))
    durations = [None, True, False, 0, 5, 10, 15.9, 120, 121, -5, 1e300, -1e300, "20", " 25 ", "1_5", "_15", "15_",
                 "1__5", "٣٠", "１２", "abc", "", " ", "nan", "NaN", "-nan", "1e3", "1E-3", ".5", "5.", "+7", "-7",
                 "0x10", "20 min", [], {}, [20], {"a": 1}, 2.5, -0.5, "\u00a020\u2003", "\x1c20", "20\x7f", "1e1_0",
                 "١_٢", "inf", "-Infinity", "+INF"]
    for value in durations:
        cases.append(({"title": "Durées", "modules": [{"title": "M", "lessons": [{"title": "L", "duration": value}]}]},
                      opts))
    for objectives in ("une seule", ["a", "a", "b", "c", "d", "e"], [1, None, "", "x"], {"a": 1}, ["**a**", "`a`", "a"],
                       ["x" * 300], [" ", "\x85"], 7):
        cases.append(({"title": "Objectifs", "modules": [{"title": "M", "lessons": [
            {"title": "L", "objectives": objectives}]}]}, opts))
    tools = [None, "python", [], {}, {"terminal": True}, {"terminal": "false"}, {"terminal": 0}, {"terminal": []},
             {"terminal": 1, "language": "PYTHON "}, {"terminal": True, "language": "bash", "file": "../../etc/passwd"},
             {"terminal": True, "language": "zsh", "file": "run.SH"}, {"terminal": True, "language": "git"},
             {"terminal": True, "language": "node", "file": "app.mjs"}, {"terminal": True, "language": "py", "file": "exo.PY"},
             {"terminal": True, "language": "Rust", "file": "main.rs"}, {"terminal": True, "language": "", "file": "notes.md"},
             {"terminal": True, "language": None, "file": ".hidden.py"}, {"terminal": True, "file": "_ok.yml"},
             {"terminal": True, "file": "a b.py"}, {"terminal": True, "file": "x" * 61 + ".py"},
             {"terminal": True, "file": "x" * 62 + ".py"}, {"terminal": True, "language": "js", "file": " spaced.js "},
             {"terminal": True, "file": "noext"}, {"terminal": True, "file": "data.CSV"},
             {"terminal": True, "file": "archive.tar.gz"}, {"terminal": True, "file": "é.py"},
             {"terminal": True, "language": 5, "file": 7}, {"terminal": True, "language": " JavaScript\x85", "file": "x.json"},
             {"terminal": False, "language": "python", "file": "x.py"},
             {"terminal": True, "language": ["python"], "file": ["x.py"]}]
    for value in tools:
        cases.append(({"title": "Outils", "tools": value, "modules": [one]}, opts))
    headings = ["Module 1 — Premiers commits", "Les branches"]
    for source, known in (("Module 1 — Premiers commits", headings), ("module 1 premiers commits", headings),
                          ("MODULE 1 : PREMIERS COMMITS !", headings), ("Absent", headings), ("Absent", []),
                          ("Absent", None), ("", headings), ("**Les branches**", headings), (5, ["5"]),
                          ("Les branches", "Les branches")):
        cases.append(({"title": "Sources", "modules": [{"title": "M", "source": source, "lessons": ["L"]}]},
                      dict(opts, headings=known)))
    extras = [{"goals": "un seul objectif"}, {"goals": [f"objectif {n}" for n in range(10)] + ["objectif 1"]},
              {"goals": [1, None, True, "  "]}, {"notes": ["consigne 1", "consigne 2"]}, {"notes": "N " * 600},
              {"level": "  "}, {"level": "L" * 80}, {"description": "D " * 300}, {"description": {"k": "v"}},
              {"informatique": False}]
    for extra in extras:
        cases.append(({"title": "Extras", "modules": [one], **extra}, opts))
    for filename in ("", None, "x" * 200, "**cours**.md", "  cours.md  ", "# cours.md", "cours\n.md"):
        cases.append(({"title": "Fichier", "modules": [one]}, dict(opts, filename=filename)))
    for chars in (0, 12345, "12", " 7 ", "1_000", 3.9, True, "x", None, "1.5", "٣"):
        cases.append(({"title": "Taille", "modules": [one]}, dict(opts, chars=chars)))
    for now in ("2026-11-24T09:30:00Z", "2026-11-24T09:30:00.123456Z", "2026-11-24T09:30:00.5+02:00", "2026-11-24",
                "2026-11-24T09:30", "2026-11-24T09:30:00-05:30", "2026-11-24 09:30:00-00:00", "2026-11-24T09:30:00.1234567Z",
                "2026-02-28T23:59:59.000001+00:00", "2026-11-24T09:30:00+05:45:30"):
        cases.append(({"title": "Date", "modules": [one]}, dict(opts, now=now)))
    for raw in ("texte", None, [], 5, {}, {"modules": []}, {"modules": [{"title": "", "lessons": [{"title": "x"}]}]}):
        cases.append((raw, opts))
    return cases


SCRIPT_TEXT = json.dumps(ANALYSIS_EMOJI, ensure_ascii=False)
SCRIPTS = {
    "ok": {"chunks": [SCRIPT_TEXT[:100], SCRIPT_TEXT[100:350], SCRIPT_TEXT[350:]]},
    "ok-fenced": {"chunks": ["Voici :\n```json\n", SCRIPT_TEXT, "\n```\nBonne séance 😀."]},
    "not-computing": {"chunks": [json.dumps({**ANALYSIS, "informatique": False})]},
    "computing-zero": {"chunks": [json.dumps({**ANALYSIS, "informatique": 0})]},
    "unreadable": {"chunks": ["Je ne sais pas ", "lire ce document."]},
    "empty": {"chunks": []},
    "refused-key": {"error": {"status": 401, "message": "Clé refusée par le fournisseur.", "code": "auth"}},
    "too-long-midway": {"chunks": ['{"title": '], "error": {"status": 502, "message": "Réponse du modèle trop longue.",
                                                            "code": "too_long"}},
    "cancelled": {"chunks": ["{"], "error": {"status": 499, "message": "Réponse interrompue.", "code": "cancelled"}},
    "runtime": {"chunks": ["{"], "runtime": "boom"},
    "no-modules": {"chunks": [json.dumps({"title": "Vide", "modules": []})]},
    "infinite-duration": {"chunks": ['{"title": "T", "modules": [{"title": "M", "lessons": [{"title": "L", '
                                     '"duration": Infinity}]}]}']},
    "nan-duration": {"chunks": ['{"title": "T", "modules": [{"title": "M", "lessons": [{"title": "L", "duration": NaN}]}]}']},
    "big-integers": {"chunks": ['{"title": 12345678901234567890123, "modules": [{"title": "M", "lessons": [{"title": "L", '
                                '"duration": 12345678901234567890}, {"title": "L2", "duration": -98765432109876543210}]}]}']},
    "huge-duration": {"chunks": ['{"title": "T", "modules": [{"title": "M", "lessons": [{"title": "L", "duration": 1'
                                 + "0" * 400 + '}]}]}']},
    "array": {"chunks": ["[1, 2]"]},
    "lists-in-text-fields": {"chunks": [json.dumps({**ANALYSIS, "notes": ["consigne 1", "consigne 2"],
                                                    "goals": "un seul objectif", "description": ["a", 1.5]})]},
}

ANSWERS = [
    json.dumps(ANALYSIS), json.dumps(ANALYSIS, ensure_ascii=False, indent=2),
    "Voici :\n```json\n" + json.dumps(ANALYSIS) + "\n```\nBonne séance.", "```\n" + json.dumps(ANALYSIS) + "\n```",
    '```JSON\n{"title": "maj"}\n```', 'texte {"title": "a"} milieu {"title": "b"} fin', '{"a": {"b": "}"}}',
    "{'title': 'guillemets simples'}", '{"title": "virgule",}', "[1, 2, 3]", '[{"title": "x"}]',
    '{"a": NaN, "b": Infinity, "c": -Infinity, "d": [1.5, -0.25, 1e-7, 2e+30, 1E-2]}', '{"n": ' + "1" * 5000 + "}",
    '{"n": ' + "1" * 4300 + "}", '{"s": "\\ud83d\\ude00 \\ud800 \\u00e9 \\udc00x"}', '{"s": "tab\there"}',
    '{"s": "ok\\/slash\\b\\f\\n\\r\\t\\"\\\\"}', '{"s": "bad \\x escape"}', '{"__proto__": {"x": 1}, "title": "proto"}',
    '{"b": 1, "2": 2, "a": {"1": [], "0": {}}, "10": null}', '{"dup": 1, "other": 0, "dup": 2}', "", None,
    "désolé, je ne peux pas", "}{",
    '```json\n{"title": "bloc invalide",}\n```\n{"title": "objet externe"}',
    'Réponse : {"title": "t", "modules": []} merci', '```json{"title":"collé"}```',
    '{"deep": ' + "[" * 50 + "]" * 50 + "}", '  \n {"title": "espaces"} \n ',
    '{"t": true, "f": false, "n": null, "neg": -0, "frac": 0.5, "big": 12345678901234567890123}',
    '{"a": 01}', '{"a": 1.}', '{"a": .5}', '{"a": -}', '{"a": tru}', '{"a": "\\u12"}', '{"a" 1}', '{"a": 1 "b": 2}',
    '{"a": [1 2]}', '{"a": [1,]}', '{"a": "\u2028 ligne \u2029"}', '{"a": "\x7f del"}', '{"a": 1}\x00',
    '{"a": "\x1f"}', '{"a": -Infinity, "b": -NaN}', '{"a": infinity}', '{"title": "😀"}',
    "```json\n{\"title\": \"premier\"}\n```\n```json\n{\"title\": \"second\"}\n```",
    "\t```json  \n\n {\"title\": \"blancs\"} \n```", 12, ["liste"],
]

SLUG_VALUES = ["Git : les bases", "Écrire une fonction", "İstanbul ﬁ ß Æ ø Ł", "Ωμέγα", "😀 Émoji 🚀", "--déjà--",
               "a—b–c", "MAJUSCULES et minuscules", "x\u0301\u0302", "123 456", "", None, 0, True, 2.5, ["a", "b"],
               "Leçon 1 — Créer un dépôt", "ǅ dz", "ẞ capitale", "K kelvin", "Å angström", "ﬀ ligature", "Ⅻ ①②",
               "ʼn", "  espaces  ", "a" * 60, "é" * 60, "a-" * 30]
TEXT_VALUES = [None, False, True, 0, 1, -7, 2.5, -0.25, 1e-05, 1e22, 10 ** 16, "", " ", "abc", "  a \t b \n c  ",
               "\x1c\x85a\u2028b\u3000", "\ufeffx\ufeff", "**gras** `code`", "### titre", "#titre", "  ## titre ##",
               "a" * 200, "😀" * 200, "é" * 199 + "😀" * 5, "a" * 23 + "😀" + "b", ["a", "b"],
               ["it's", 'say "hi"', "both ' \""], {"k": [1, None, True]}, [], {}, "x\x00y", "\x7f",
               "ligne\u200bzéro", ["\x00\x85\xa0\u200b😀\t\n\\"], "fin avec espaces    \t"]
PLAIN_VALUES = ["Module 1 — Premiers commits", "MODULE 1 : PREMIERS COMMITS !", "Évaluation", "İ", "😀 émoji",
                "Ωμέγα 12", None, 5, ["a"], "", "  ", "À la fin du cours", "Pré-requis", "a_b-c.d"]


@unittest.skipUnless(NODE, "node absent")
class DocumentParityTests(unittest.TestCase):
    def test_outline_and_headings(self):
        calls, expected = [], []
        for document in DOCUMENTS.values():
            for filename in FILENAMES:
                calls.append(("courses", "outline", [document, filename]))
                expected.append(courses.outline(document, filename))
            calls.append(("courses", "documentHeadings", [document]))
            expected.append(courses.document_headings(document))
        answers = js_many(calls, timeout=300)
        for call, answer, value in zip(calls, answers, expected):
            with self.subTest(document=call[2][0][:50], call=call[1], filename=call[2][1:]):
                self.assertNotIsInstance(answer, JsError)
                self.assertEqual(answer, value)

    def test_check_document(self):
        values = [None, 5, ["x"], "court", "a\x00b" * 30, "x" * 300_000, "x" * 300_001, "\ufeff\ufeff" + "x" * 50,
                  "\ufeff" + " " * 50, "😀" * 39 + " \n", "😀" * 40, "😀" * 150_000, "😀" * 150_001,
                  "\r\n" * 10 + "y" * 39, "a\rb\r\nc" * 20, "\x1c\x1d\x1e\x1f\x85" * 20 + "z", "\ufeff" * 45,
                  " " * 39 + "\ufeffx", "\u00a0" * 50 + "y" * 39, DOCUMENT]
        answers = js_many([("courses", "checkDocument", [value]) for value in values], timeout=300)
        for value, answer in zip(values, answers):
            with self.subTest(value=repr(value)[:40]):
                self.assertEqual(js_outcome(answer), outcome(lambda: courses.check_document(value)))

    def test_import_without_model_takes_the_headings(self):
        calls, expected = [], []
        for name, document in DOCUMENTS.items():
            for filename, existing in (("cours.md", []), (None, ["git", "cours"]), ("git.md", ["git-2"])):
                calls.append(("courses", "importCourse", [document, filename, {"existingIds": existing, "now": NOW}]))
                expected.append(outcome(lambda: py_import(document, filename, {}, ai=None, existing_ids=existing)))
        answers = js_many(calls, timeout=300)
        for call, answer, value in zip(calls, answers, expected):
            with self.subTest(document=call[2][0][:50], filename=call[2][1]):
                result = value[1]
                if "error" in result:
                    self.assertEqual(js_outcome(answer), ("error", *result["error"]))
                else:
                    self.assertEqual(answer, [result["course"], result["notice"]])
                    self.assertEqual(result["calls"], [])


@unittest.skipUnless(NODE, "node absent")
class AnalysisParityTests(unittest.TestCase):
    def test_model_answers_are_read_the_same_way(self):
        answers = js_many([("test:courses_harness", "parseRepr", [text]) for text in ANSWERS], timeout=300)
        for text, answer in zip(ANSWERS, answers):
            with self.subTest(answer=repr(text)[:60]):
                expected = outcome(lambda: repr(courses.parse_analysis(text)))
                if expected[0] == "ok":
                    self.assertEqual(answer, {"repr": expected[1]})
                else:
                    self.assertEqual(answer, {"error": list(expected[1:])})

    def test_json_reader_accepts_what_json_loads_accepts(self):
        texts = [text for text in ANSWERS if isinstance(text, str)] + [
            "1", "-0", "1.5e3x", "[]", "{}", " null ", "true", "\ufeff{}", '"\\uD834\\uDD1E"', "[1e400, -1e400]",
            '{"a": [1, {"b": [true, false, null]}]}', "NaN", "-Infinity", "Infinityx", "[" * 30 + "]" * 30, "",
            '"unterminated', "[1,,2]", '{"a":1,}', '{,}', "01", "-", "--1", "1e", "1e+", ".1", '"\\u00zz"']
        answers = js_many([("test:courses_harness", "loadRepr", [text]) for text in texts], timeout=300)
        for text, answer in zip(texts, answers):
            with self.subTest(text=repr(text)[:60]):
                try:
                    expected = {"repr": repr(json.loads(text))}
                except ValueError:
                    expected = {"error": "ValueError"}
                self.assertEqual(answer, expected)

    def test_analyse_sends_the_same_request(self):
        for name in ("ok", "ok-fenced", "unreadable", "refused-key", "array"):
            with self.subTest(script=name):
                script = SCRIPTS[name]
                [answer] = js_many([("test:courses_harness", "analyse", [DOCUMENT, "git.md", AI, script])])
                calls, progress = [], []

                def stream_chat(config, system, messages, *, max_tokens=4000, temperature=0.4, timeout=180, cancel=None):
                    calls.append({"config": config, "system": system, "messages": messages, "maxTokens": max_tokens,
                                  "temperature": temperature, "timeout": timeout * 1000})
                    for chunk in script.get("chunks", []):
                        yield "delta", chunk
                    if script.get("error"):
                        error = script["error"]
                        raise providers.ProviderError(error["status"], error["message"], error.get("code", ""))
                    yield "done", {}

                with mock.patch.object(courses.providers, "stream_chat", stream_chat):
                    result = outcome(lambda: repr(courses.analyse(DOCUMENT, "git.md", AI, on_progress=progress.append)))
                expected = {"calls": calls, "progress": progress}
                expected.update({"repr": result[1]} if result[0] == "ok" else {"error": list(result[1:])})
                self.assertEqual(answer, expected)
                self.assertEqual(calls[0]["system"], courses.ANALYSIS_SYSTEM)
                self.assertEqual((calls[0]["maxTokens"], calls[0]["temperature"], calls[0]["timeout"]),
                                 (16000, 0.2, 300000))


@unittest.skipUnless(NODE, "node absent")
class BuildParityTests(unittest.TestCase):
    def test_build_course(self):
        cases = build_cases()
        answers = js_many([("courses", "buildCourse", [raw, options]) for raw, options in cases], timeout=300)
        for (raw, options), answer in zip(cases, answers):
            with self.subTest(raw=json.dumps(raw, ensure_ascii=False)[:100], options=json.dumps(options)[:120]):
                self.assertEqual(js_outcome(answer), py_build(raw, options))
        verdicts = [py_build(raw, options)[0] for raw, options in cases]
        self.assertGreater(verdicts.count("ok"), 150)
        self.assertGreater(verdicts.count("error"), 10)

    def test_private_helpers(self):
        calls, expected = [], []
        for value in TEXT_VALUES:
            for limit in (0, 1, 5, 24, 60, 120, 160):
                calls.append(("courses", "_internal.text", [value, limit]))
                expected.append(outcome(lambda: courses._text(value, limit)))
        for value in SLUG_VALUES:
            for limit in (40, 24, 26, 5, 1, 0, -3, 100):
                calls.append(("courses", "slugify", [value, limit]))
                expected.append(outcome(lambda: courses.slugify(value, limit)))
            calls.append(("courses", "slugify", [value]))
            expected.append(outcome(lambda: courses.slugify(value)))
        for value in PLAIN_VALUES + SLUG_VALUES:
            calls.append(("courses", "_internal.plain", [value]))
            expected.append(outcome(lambda: courses._plain(value)))
        for values, limit, count in (("abc", 10, 2), (["a", "a", "b"], 10, 4), (["x" * 30], 5, 1),
                                     ([1, None, "", "y"], 10, 4), ({"a": 1}, 10, 2), (None, 10, 2),
                                     (["a", "b", "c", "d", "e"], 10, 4), (["**a**", "`a`", "a"], 10, 4),
                                     ([" ", "\x85"], 10, 2), (["😀" * 10], 3, 1)):
            calls.append(("courses", "_internal.strings", [values, limit, count]))
            expected.append(outcome(lambda: courses._strings(values, limit, count)))
        for value in [None, True, False, 0, 5, 10, 15.9, 120, 121, -5, 1e300, "20", " 25 ", "1_5", "٣٠", "abc", "nan",
                      "inf", "-inf", "1e3", [], {}, "20 min", "\u00a020\u2003", "\x1c20", "1e1_0", "Infinity"]:
            calls.append(("courses", "_internal.duration", [value]))
            expected.append(outcome(lambda: courses._duration(value)))
        answers = js_many(calls, timeout=300)
        for call, answer, value in zip(calls, answers, expected):
            with self.subTest(call=call[1], args=repr(call[2])[:80]):
                self.assertEqual(js_outcome(answer), value)


@unittest.skipUnless(NODE, "node absent")
class ImportParityTests(unittest.TestCase):
    def test_import_with_a_stub_model(self):
        documents = [(DOCUMENT, "git.md"), (PLAN, "plan-formation-python.md"), (courses.TEMPLATE, None)]
        cases = []
        for name, script in SCRIPTS.items():
            for document, filename in documents:
                cases.append((document, filename, script, AI, ["git"]))
        for ai in (None, {}, {"configured": False}, {"configured": "oui", "model": "m"}):
            cases.append((DOCUMENT, "git.md", SCRIPTS["ok"], ai, []))
        cases.append(("court", "x.md", SCRIPTS["ok"], AI, []))
        cases.append((None, "x.md", SCRIPTS["ok"], AI, []))
        cases.append((DOCUMENT, "git.md", SCRIPTS["ok"], AI, ["git", "git-2", "git-3"]))
        answers = js_many([("test:courses_harness", "importCourse",
                            [document, filename, script, {"ai": ai, "existingIds": existing, "now": NOW}])
                           for document, filename, script, ai, existing in cases], timeout=300)
        notices = set()
        for (document, filename, script, ai, existing), answer in zip(cases, answers):
            with self.subTest(document=str(document)[:30], script=json.dumps(script)[:60], ai=str(ai)[:40]):
                expected = py_import(document, filename, script, ai=ai, existing_ids=existing)
                self.assertEqual(answer, expected)
                notices.add(expected.get("notice", "error"))
        self.assertGreaterEqual(len(notices), 6)


if __name__ == "__main__":
    unittest.main()
