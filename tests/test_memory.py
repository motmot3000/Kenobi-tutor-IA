import copy
import json
import unittest
from pathlib import Path

from memory import FORMER_LESSON_IDS, StateValidationError, render_course_memory, sync_builtin_plan, validate_state

DATA = Path(__file__).resolve().parent.parent / "data"


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
                "updatedAt": "2026-11-03T09:30:00Z",
                "modules": [
                    {
                        "id": "bases",
                        "title": "Les bases",
                        "description": "Fondamentaux",
                        "lessons": [
                            {
                                "id": "variables",
                                "title": "Variables",
                                "status": "done",
                                "duration": "20 min",
                            },
                            {
                                "id": "conditions",
                                "title": "Conditions",
                                "status": "current",
                                "duration": "25 min",
                            },
                        ],
                    },
                    {
                        "id": "structures",
                        "title": "Structures",
                        "description": "Aller plus loin",
                        "lessons": [
                            {
                                "id": "boucles",
                                "title": "Boucles",
                                "status": "todo",
                                "duration": "30 min",
                            },
                            {
                                "id": "fonctions",
                                "title": "Fonctions",
                                "status": "review",
                                "duration": "35 min",
                            },
                        ],
                    },
                ],
                "sessions": [
                    {
                        "id": "session-old",
                        "title": "Session précédente",
                        "startedAt": "2026-11-01T09:00:00Z",
                        "updatedAt": "2026-11-01T09:30:00Z",
                        "duration": "30 min",
                        "status": "done",
                        "lessonId": "variables",
                        "summary": "Résumé ancien.",
                        "messages": [],
                    },
                    {
                        "id": "session-new",
                        "title": "Session récente",
                        "startedAt": "2026-11-02T09:00:00Z",
                        "updatedAt": "2026-11-02T09:45:00Z",
                        "duration": "45 min",
                        "status": "done",
                        "lessonId": "conditions",
                        "summary": "Résumé récent.",
                        "messages": [
                            {
                                "id": "message-1",
                                "role": "user",
                                "createdAt": "2026-11-02T09:01:00Z",
                                "content": "MESSAGE_COMPLET_A_NE_PAS_EXPORTER",
                            }
                        ],
                    },
                ],
                "memory": {
                    "lastSessionId": "session-new",
                    "acquired": ["Affecter une variable", "Lire une condition"],
                    "toReview": ["Indentation"],
                    "nextFocus": "Écrire trois conditions.",
                    "notes": "Reprendre avec un exemple court.",
                },
            }
        ],
    }


class MemoryTests(unittest.TestCase):
    def test_markdown_progress_sections_and_session_order(self):
        state = sample_state()
        markdown = render_course_memory(state, state["courses"][0])

        self.assertIn("type: apprentissage", markdown)
        self.assertIn('cours: "Python"', markdown)
        self.assertIn('mis_a_jour: "2026-11-03T09:30:00Z"', markdown)
        self.assertIn("1 / 4 leçons terminées (25 %).", markdown)
        self.assertIn("## Prochaine leçon\n\nConditions", markdown)
        self.assertIn("- Affecter une variable", markdown)
        self.assertIn("- Indentation", markdown)
        self.assertIn("Écrire trois conditions.", markdown)
        self.assertIn("Reprendre avec un exemple court.", markdown)
        self.assertLess(markdown.index("Résumé récent."), markdown.index("Résumé ancien."))
        self.assertNotIn("MESSAGE_COMPLET_A_NE_PAS_EXPORTER", markdown)

    def test_first_todo_is_used_without_current_lesson(self):
        state = sample_state()
        course = state["courses"][0]
        course["modules"][0]["lessons"][1]["status"] = "review"
        del course["updatedAt"]

        markdown = render_course_memory(state, course)

        self.assertIn("## Prochaine leçon\n\nBoucles", markdown)
        self.assertIn('mis_a_jour: "2026-11-03T10:00:00Z"', markdown)

    def test_course_id_validation(self):
        for invalid_id in ("", "../python", "Python", "python_1", "python 1"):
            with self.subTest(invalid_id=invalid_id):
                state = sample_state()
                state["activeCourseId"] = invalid_id
                state["courses"][0]["id"] = invalid_id
                with self.assertRaises(StateValidationError):
                    validate_state(state)

        state = sample_state()
        state["activeCourseId"] = "python-3"
        state["courses"][0]["id"] = "python-3"
        self.assertIs(validate_state(state), state)

    def test_numeric_durations_are_accepted(self):
        state = sample_state()
        state["courses"][0]["modules"][0]["lessons"][0]["duration"] = 20
        state["courses"][0]["sessions"][0]["duration"] = 30
        self.assertIs(validate_state(state), state)

    def test_invalid_lesson_status_is_rejected(self):
        state = sample_state()
        state["courses"][0]["modules"][0]["lessons"][0]["status"] = "finished"
        with self.assertRaisesRegex(StateValidationError, "status"):
            validate_state(state)

    def test_memory_arrays_must_contain_strings(self):
        state = sample_state()
        state["courses"][0]["memory"]["acquired"] = ["Variables", 3]
        with self.assertRaisesRegex(StateValidationError, "acquired"):
            validate_state(state)

    def test_reviews_are_validated_and_listed_by_due_date(self):
        state = sample_state()
        memory = state["courses"][0]["memory"]
        memory["reviews"] = [
            {"item": "Boucle while", "step": 7, "due": "2026-11-15"},
            {"item": "Indentation", "step": 1, "due": "2026-11-09"},
        ]
        self.assertIs(validate_state(state), state)
        markdown = render_course_memory(state, state["courses"][0])
        self.assertIn("## Révisions prévues", markdown)
        self.assertLess(
            markdown.index("2026-11-09 — Indentation (J+1)"),
            markdown.index("2026-11-15 — Boucle while (J+7)"),
        )

        memory["reviews"] = [{"item": "", "due": "2026-11-09"}]
        with self.assertRaisesRegex(StateValidationError, "reviews"):
            validate_state(state)
        memory["reviews"] = "pas une liste"
        with self.assertRaisesRegex(StateValidationError, "reviews"):
            validate_state(state)

    def test_imported_course_fields_are_checked_and_written_to_the_memory(self):
        state = sample_state()
        course = state["courses"][0]
        course["goals"] = ["créer un dépôt"]
        course["tools"] = {"terminal": True, "language": "shell", "file": "exercice.sh"}
        course["modules"][0]["brief"] = "Dépôt, index, commit."
        course["modules"][0]["lessons"][0]["objectives"] = ["initialiser un dépôt"]
        course["memory"]["facts"] = ["veut versionner ses cours"]
        self.assertIs(validate_state(state), state)
        markdown = render_course_memory(state, course)
        self.assertIn("## Objectifs du cours\n\n- créer un dépôt", markdown)
        self.assertIn("## Ce que Kenobi a retenu\n\n- veut versionner ses cours", markdown)
        for path, value, word in ((("goals",), "un texte", "goals"), (("memory", "facts"), [1], "facts"),
                                  (("tools",), "python", "tools"), (("modules", 0, "lessons", 0, "objectives"), [None], "objectives")):
            with self.subTest(word=word):
                broken = copy.deepcopy(state)
                target = broken["courses"][0]
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                with self.assertRaisesRegex(StateValidationError, word):
                    validate_state(broken)

    def test_validation_does_not_mutate_state(self):
        state = sample_state()
        original = copy.deepcopy(state)
        validate_state(state)
        self.assertEqual(state, original)


def former_python_state():
    """L'état d'un apprenant d'avant le 2026-11-14 : le bloc calqué sur Mimo, cinq leçons validées."""
    lesson = lambda id, title, status, duration=40: {"id": id, "title": title, "duration": duration, "status": status}
    session = lambda id, lesson_id, title: {"id": id, "title": title, "startedAt": "2026-11-12T10:00:00Z",
                                            "updatedAt": "2026-11-12T10:20:00Z", "duration": 0, "status": "completed",
                                            "lessonId": lesson_id, "summary": "Bilan.",
                                            "messages": [{"id": f"{id}-m", "role": "tutor", "createdAt": "", "content": "Cours."}]}
    return {
        "version": 1, "activeCourseId": "python",
        "courses": [{
            "id": "python", "title": "Parcours Python", "description": "Ancien plan.",
            "modules": [
                {"id": "bloc-0-mimo", "title": "Bloc 0 — Mimo : immersion dans la syntaxe", "description": "Mimo.",
                 "lessons": [lesson("mimo-basics", "Python Basics", "done", 45), lesson("mimo-types", "Types et comparaisons", "done"),
                             lesson("mimo-conditions", "Instructions conditionnelles", "done"), lesson("mimo-loops", "Boucles", "done", 45),
                             lesson("mimo-lists", "Listes", "done"), lesson("mimo-list-ops", "Opérations sur les listes", "current"),
                             lesson("mimo-strings", "Opérations sur les chaînes", "todo")]},
                {"id": "module-8-api-bdd", "title": "Module 8", "description": "",
                 "lessons": [lesson("m8-api-rest", "Construire une petite API", "todo", 55)]},
            ],
            "sessions": [session("s1", "mimo-basics", "Python Basics"), session("s2", "mimo-loops", "Boucles"),
                         {**session("s3", "mimo-list-ops", "Opérations sur les listes"), "status": "active"}],
            "memory": {"lastSessionId": "s3", "acquired": ["écrire une boucle for"], "toReview": [],
                       "nextFocus": "leçon « Opérations sur les listes »", "notes": "",
                       "reviews": [{"item": "range", "step": 1, "due": "2026-11-13"}]},
        }],
    }


class BuiltinPlanTests(unittest.TestCase):
    def setUp(self):
        self.default = json.loads((DATA / "default-state.json").read_text(encoding="utf-8"))

    def test_the_former_plan_takes_the_new_one_and_keeps_the_progress(self):
        state = former_python_state()
        before = copy.deepcopy(state["courses"][0])
        self.assertTrue(sync_builtin_plan(state, self.default))
        validate_state(state)
        course = state["courses"][0]
        reference = self.default["courses"][0]
        self.assertEqual([module["id"] for module in course["modules"]], [module["id"] for module in reference["modules"]])
        lessons = [lesson for module in course["modules"] for lesson in module["lessons"]]
        self.assertEqual([lesson["id"] for lesson in lessons][:7], ["m0-premiers-pas", "m0-types", "m0-conditions", "m0-boucles",
                                                                    "m0-listes", "m0-listes-operations", "m0-saisie"])
        self.assertEqual([lesson["status"] for lesson in lessons][:7], ["done"] * 5 + ["current", "todo"])
        self.assertEqual(sum(lesson["status"] == "current" for lesson in lessons), 1)
        self.assertEqual((lessons[0]["duration"], lessons[0]["objectives"][0]), (20, "afficher du texte et des valeurs avec print()"))
        self.assertEqual(course["title"], reference["title"])
        self.assertEqual(course["goals"], reference["goals"])
        self.assertEqual([(item["lessonId"], item["title"]) for item in course["sessions"]], [
            ("m0-premiers-pas", "Premiers pas : afficher, variables, lancer un script"),
            ("m0-boucles", "Boucles for et range"),
            ("m0-listes-operations", "Opérations sur les listes")])
        self.assertEqual([item["messages"] for item in course["sessions"]], [item["messages"] for item in before["sessions"]])
        self.assertEqual(course["memory"], before["memory"])
        self.assertNotIn("mimo", json.dumps(course["modules"]).lower())
        self.assertFalse(sync_builtin_plan(state, self.default))

    def test_an_imported_course_with_the_same_id_is_left_alone(self):
        state = sample_state()
        original = copy.deepcopy(state)
        self.assertFalse(sync_builtin_plan(state, self.default))
        self.assertEqual(state, original)
        removed = {"version": 1, "activeCourseId": "", "courses": []}
        self.assertFalse(sync_builtin_plan(removed, self.default))
        self.assertEqual(removed["courses"], [])

    def test_the_course_shipped_with_the_application_is_complete(self):
        validate_state(self.default)
        text = (DATA / "default-state.json").read_text(encoding="utf-8") + (DATA / "plan-formation-python.md").read_text(encoding="utf-8")
        self.assertNotIn("mimo", text.lower())
        lessons = [(module["id"], lesson) for module in self.default["courses"][0]["modules"] for lesson in module["lessons"]]
        self.assertEqual(len({lesson["id"] for _, lesson in lessons}), len(lessons))
        for module_id, lesson in lessons:
            with self.subTest(lesson=lesson["id"]):
                self.assertTrue(2 <= len(lesson["objectives"]) <= 3)
                project = "projet" in lesson["id"] or module_id == "module-13-portfolio"
                self.assertTrue(15 <= lesson["duration"] <= (90 if project else 35))
                if module_id == "module-0-bases" and not project:
                    self.assertLessEqual(lesson["duration"], 25)
        ids = {lesson["id"] for _, lesson in lessons}
        self.assertEqual(ids & set(FORMER_LESSON_IDS), set())
        self.assertLessEqual(set(FORMER_LESSON_IDS.values()), ids)


if __name__ == "__main__":
    unittest.main()
