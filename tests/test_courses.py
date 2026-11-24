import json
import unittest

import courses

try:
    from stub_llm import StubLLM
except ImportError:
    from tests.stub_llm import StubLLM

DOCUMENT = """# Git : les bases

Un cours court pour versionner son code.

## Objectifs du cours

- créer un dépôt et enregistrer des versions ;
- lire un historique.

## Module 1 — Premiers commits

Créer un dépôt et enregistrer.

### Leçon 1 — Créer un dépôt

- Objectif : initialiser avec `git init`.

### Leçon 2 — Lire l'état

## Module 2 — Les branches

- Créer une branche
- Fusionner une branche

## Évaluation

Un petit projet neuf.
"""

ANALYSIS = {
    "title": "Git : les bases", "shortTitle": "Git", "description": "Versionner son code.", "level": "débutant",
    "goals": ["créer un dépôt", "lire un historique"], "informatique": True,
    "tools": {"terminal": True, "language": "bash", "file": "../../etc/passwd"},
    "notes": "Évaluation sur un projet neuf.",
    "modules": [
        {"title": "Premiers commits", "description": "Créer et enregistrer.", "brief": "Dépôt, index, commit.",
         "source": "Module 1 — Premiers commits",
         "lessons": [{"title": "Créer un dépôt", "duration": 25, "objectives": ["initialiser un dépôt"]},
                     {"title": "Lire l'état", "duration": 900, "objectives": "interpréter git status"}]},
        {"title": "Les branches", "description": "", "source": "Un titre absent du document",
         "lessons": ["Créer une branche", {"title": ""}, {"title": "Fusionner une branche"}]},
        {"title": "Module vide", "lessons": []},
    ],
}


class OutlineTests(unittest.TestCase):
    def test_headings_become_modules_and_lessons(self):
        raw = courses.outline(DOCUMENT, "git.md")
        self.assertEqual(raw["title"], "Git : les bases")
        self.assertEqual(raw["goals"], ["créer un dépôt et enregistrer des versions ;", "lire un historique."])
        self.assertEqual([module["title"] for module in raw["modules"]], ["Module 1 — Premiers commits", "Module 2 — Les branches"])
        self.assertEqual([lesson["title"] for lesson in raw["modules"][0]["lessons"]], ["Leçon 1 — Créer un dépôt", "Leçon 2 — Lire l'état"])
        # Sans sous-titres, les éléments de liste deviennent les leçons.
        self.assertEqual([lesson["title"] for lesson in raw["modules"][1]["lessons"]], ["Créer une branche", "Fusionner une branche"])

    def test_a_real_first_chapter_called_introduction_is_kept(self):
        raw = courses.outline("# Cours\n\n## Introduction à Python\n\n### Installer\n\n## Introduction\n\nBla.\n", "c.md")
        self.assertEqual([module["title"] for module in raw["modules"]], ["Introduction à Python"])

    def test_documents_are_checked_before_anything(self):
        for bad in (None, "court", "a\x00b" * 30, "x" * (courses.MAX_DOCUMENT + 1)):
            with self.subTest(bad=str(bad)[:12]):
                with self.assertRaises(courses.CourseError):
                    courses.check_document(bad)


class BuildTests(unittest.TestCase):
    def test_course_is_safe_bounded_and_ready(self):
        course = courses.build_course(ANALYSIS, existing_ids={"git"}, filename="git.md", chars=900,
                                      headings=courses.document_headings(DOCUMENT))
        self.assertEqual(course["id"], "git-2")  # « git » est déjà pris
        self.assertEqual(course["tools"], {"terminal": True, "language": "shell", "file": "exercice.sh"})
        self.assertEqual(len(course["modules"]), 2)  # le module sans leçon est écarté
        first = course["modules"][0]
        self.assertEqual(first["source"], "Module 1 — Premiers commits")
        self.assertNotIn("source", course["modules"][1])  # titre absent du document
        lessons = [lesson for module in course["modules"] for lesson in module["lessons"]]
        self.assertEqual([lesson["status"] for lesson in lessons], ["current", "todo", "todo", "todo"])
        self.assertEqual(lessons[1]["duration"], 120)
        self.assertEqual(lessons[1]["objectives"], ["interpréter git status"])
        for lesson in lessons:
            self.assertRegex(lesson["id"], r"^[a-z0-9][a-z0-9-]{0,63}$")
            self.assertTrue(lesson["id"].startswith("git-2-"))
        self.assertEqual(len({lesson["id"] for lesson in lessons}), 4)
        self.assertEqual(course["memory"]["notes"], "Évaluation sur un projet neuf.")
        self.assertEqual(course["memory"]["facts"], [])
        self.assertEqual(course["source"]["analysis"], "ia")

    def test_the_state_validator_accepts_an_imported_course(self):
        import memory
        course = courses.build_course(ANALYSIS, filename="git.md")
        state = {"version": 1, "activeCourseId": course["id"], "courses": [course]}
        memory.validate_state(state)
        self.assertIn("## Objectifs du cours", memory.render_course_memory(state, course))

    def test_nothing_usable_is_refused(self):
        for raw in ({}, {"modules": []}, {"modules": [{"title": "", "lessons": [{"title": "x"}]}]}, "texte"):
            with self.subTest(raw=str(raw)[:30]):
                with self.assertRaises(courses.CourseError):
                    courses.build_course(raw)

    def test_analysis_is_read_even_wrapped_in_text(self):
        wrapped = "Voici le cours :\n```json\n" + json.dumps(ANALYSIS) + "\n```\nBonne séance."
        self.assertEqual(courses.parse_analysis(wrapped)["title"], "Git : les bases")
        self.assertEqual(courses.parse_analysis("{\"title\": \"x\"}")["title"], "x")
        with self.assertRaises(courses.CourseError):
            courses.parse_analysis("désolé, je ne peux pas")


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.stub = StubLLM()

    def tearDown(self):
        self.stub.close()

    def test_the_plugged_model_reads_the_document(self):
        text = json.dumps(ANALYSIS)
        self.stub.say(text[:200], text[200:])
        seen = []
        course, notice = courses.import_course(DOCUMENT, "git.md", ai=self.stub.config(), on_progress=seen.append)
        self.assertEqual((course["source"]["analysis"], notice), ("ia", ""))
        self.assertEqual(course["shortTitle"], "Git")
        self.assertTrue(seen and seen[-1] == len(text))
        request = self.stub.chat_requests()[0]["body"]
        self.assertIn("Kenobi", request["messages"][0]["content"])
        self.assertIn("## Module 1 — Premiers commits", request["messages"][1]["content"])

    def test_without_model_or_with_a_bad_answer_the_headings_are_used(self):
        course, notice = courses.import_course(DOCUMENT, "git.md", ai=None)
        self.assertEqual(course["source"]["analysis"], "titres")
        self.assertIn("Aucun modèle", notice)
        self.stub.say("Je ne sais pas lire ce document.")
        course, notice = courses.import_course(DOCUMENT, "git.md", ai=self.stub.config())
        self.assertEqual(course["source"]["analysis"], "titres")
        self.assertIn("titres du document", notice)
        self.stub.fail(401)
        course, notice = courses.import_course(DOCUMENT, "git.md", ai=self.stub.config())
        self.assertEqual(course["source"]["analysis"], "titres")
        self.assertIn("Clé refusée", notice)

    def test_a_course_outside_computing_is_imported_with_a_warning(self):
        self.stub.say(json.dumps({**ANALYSIS, "informatique": False}))
        course, notice = courses.import_course(DOCUMENT, "git.md", ai=self.stub.config())
        self.assertEqual(course["source"]["analysis"], "ia")
        self.assertIn("informatique", notice)


if __name__ == "__main__":
    unittest.main()
