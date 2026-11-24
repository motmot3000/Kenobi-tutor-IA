import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

import providers
import tutor
from settings import Settings

try:
    from stub_llm import StubLLM
except ImportError:
    from tests.stub_llm import StubLLM

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
LEARNER = {"name": "Camille", "about": "Débute en programmation."}


def real_state():
    state = json.loads((DATA / "default-state.json").read_text(encoding="utf-8"))
    course = state["courses"][0]
    course["sessions"] = [
        {
            "id": "session-a",
            "title": "Premiers pas : afficher, variables, lancer un script",
            "startedAt": "2026-11-08T10:00:00Z",
            "updatedAt": "2026-11-08T10:05:00Z",
            "duration": 0,
            "status": "active",
            "lessonId": "m0-premiers-pas",
            "summary": "Session consacrée à « Premiers pas ».",
            "messages": [
                {"id": "m1", "role": "user", "createdAt": "2026-11-08T10:01:00Z", "content": "On commence ?"},
                {
                    "id": "m2",
                    "role": "tutor",
                    "createdAt": "2026-11-08T10:01:05Z",
                    "content": "Oui.\n\n```kenobi-lecon\nobjectif: Lire un script\ncritere: Prédire\n```",
                },
                {"id": "m3", "role": "user", "createdAt": "2026-11-08T10:02:00Z", "content": "Voici ma réponse"},
            ],
        }
    ]
    course["memory"]["acquired"] = ["affecte une variable"]
    course["memory"]["facts"] = ["veut créer des outils pour sa classe"]
    course["memory"]["reviews"] = [
        {"item": "print et return", "step": 1, "due": "2026-11-08"},
        {"item": "indentation", "step": 7, "due": "2026-11-20"},
    ]
    return state


def imported_course():
    return {
        "id": "git", "title": "Git : les bases", "shortTitle": "Git", "description": "", "level": "débutant",
        "goals": ["créer un dépôt"], "tools": {"terminal": True, "language": "shell", "file": "exercice.sh"},
        "modules": [{"id": "git-m01", "title": "Premiers commits", "description": "", "brief": "Dépôt, index, commit.",
                     "source": "Module 1 — Premiers commits",
                     "lessons": [{"id": "git-0101-creer", "title": "Créer un dépôt", "duration": 30, "status": "current",
                                  "objectives": ["initialiser un dépôt avec git init"]}]}],
        "sessions": [{"id": "session-git", "title": "Créer un dépôt", "startedAt": "", "updatedAt": "", "duration": 0,
                      "status": "active", "lessonId": "git-0101-creer", "summary": "", "messages": []}],
        "memory": {"lastSessionId": None, "acquired": [], "toReview": [], "nextFocus": "", "notes": ""},
    }


class ContextTests(unittest.TestCase):
    def test_context_carries_learner_lesson_memory_reviews_and_plan(self):
        context = tutor.build_context(real_state(), "session-a", learner=LEARNER, data_dir=DATA, today=date(2026, 11, 8),
                                      terminal="coach")
        for expected in (
            "dimanche 8 novembre 2026",
            "Apprenant : Camille — Débute en programmation.",
            "0 leçon(s) validée(s) sur 86",
            "Leçon en cours : « Premiers pas : afficher, variables, lancer un script » (1/16 du module)",
            "Ce que la leçon doit lui apprendre :",
            "afficher du texte et des valeurs avec print()",
            "Durée prévue : environ 20 min, cours compris",
            "Leçon suivante du plan : « Types et comparaisons ».",
            "affecte une variable",
            "print et return (J+1)",
            "indentation le 2026-11-20",
            "veut créer des outils pour sa classe",
            "Module 0 — Les bases de Python",
            "Mini-projet — Le nombre mystère",
            "mode Coach — tu reçois ce qu'il lance (commandes Python)",
            "Contrat de la leçon : pas encore posé",
            "Séance : en cours.",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, context)

    def test_system_prompt_names_the_learner_the_course_and_the_blocks(self):
        system = tutor.build_system(real_state(), "session-a", learner=LEARNER, data_dir=DATA, terminal="coach")
        self.assertIn("le tuteur personnel de\nCamille", system)
        self.assertIn("spécialisé en informatique et en programmation", system)
        for block in ("kenobi-lecon", "kenobi-pret", "kenobi-qcm", "kenobi-boutons", "kenobi-bilan", "kenobi-revision",
                      "kenobi-memoire", "kenobi-exercice", "kenobi-verdict"):
            self.assertIn(block, system)
        for rule in ("LE COURS — chaque leçon commence par un vrai cours", "**À quoi ça sert**", "RÉPONDRE EN UN CLIC",
                     "Question fermée (oui ou non", "bouton: Non, on continue",
                     "Révisions dues aujourd'hui (de mémoire, pendant la pratique, après le cours)"):
            self.assertIn(rule, system)
        self.assertIn("l'interface t'envoie chacun de ses\nlancements".replace("\n", " "), system.replace("\n", " "))
        self.assertNotIn("{name}", system)
        self.assertNotIn("{file}", system)
        self.assertNotIn("{delivery}", system)
        self.assertNotIn("Alex", system, "aucun prénom écrit en dur : seul celui des réglages")

    def test_system_prompt_is_stable_between_two_turns_of_a_session(self):
        state = real_state()
        first = tutor.build_system(state, "session-a", learner=LEARNER, data_dir=DATA, today=date(2026, 11, 8), terminal="coach")
        state["courses"][0]["sessions"][0]["messages"].append(
            {"id": "m4", "role": "tutor", "createdAt": "2026-11-08T10:03:00Z", "content": "Bien."})
        second = tutor.build_system(state, "session-a", learner=LEARNER, data_dir=DATA, today=date(2026, 11, 8), terminal="coach")
        self.assertEqual(first, second)

    def test_an_imported_course_brings_its_goals_objectives_and_document(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = Path(folder)
            (runtime / "git").mkdir()
            (runtime / "git" / "COURSE.md").write_text(
                "# Git\n\n## Module 1 — Premiers commits\n\nLes trois zones de Git.\n\n### Leçon 1\n\nDétail.\n\n## Module 2\n\nAutre chose.\n",
                encoding="utf-8")
            state = {"version": 1, "activeCourseId": "git", "courses": [imported_course()]}
            system = tutor.build_system(state, "session-git", learner={"name": "", "about": ""}, runtime_dir=runtime,
                                        terminal="coach")
        for expected in ("Objectifs du cours", "créer un dépôt", "Dépôt, index, commit.", "initialiser un dépôt avec git init",
                         "Les trois zones de Git.", "toutes ses commandes", "il clique sur « Vérifier »", "ton élève",
                         "exercice.sh"):
            with self.subTest(expected=expected):
                self.assertIn(expected, system)
        self.assertNotIn("Autre chose.", system)

    def test_a_course_without_terminal_has_no_exercise_block(self):
        course = imported_course()
        course["tools"] = {"terminal": False, "language": "", "file": ""}
        state = {"version": 1, "activeCourseId": "git", "courses": [course]}
        system = tutor.build_system(state, "session-git", terminal="closed")
        self.assertIn("sans éditeur ni terminal", system)
        self.assertNotIn("kenobi-verdict", system)
        self.assertNotIn("Terminal : fermé", system)

    def test_a_session_is_found_in_any_course(self):
        state = real_state()
        state["courses"].append(imported_course())
        course, session = tutor.find_session(state, "session-git")
        self.assertEqual((course["id"], session["id"]), ("git", "session-git"))
        with self.assertRaises(tutor.ChatError) as caught:
            tutor.build_context(state, "inconnue")
        self.assertEqual(caught.exception.status, 404)


class HistoryTests(unittest.TestCase):
    def test_history_replays_the_session_and_ends_with_the_new_message_once(self):
        session = real_state()["courses"][0]["sessions"][0]
        history = tutor.build_history(session, "Voici ma réponse")
        self.assertEqual([item["role"] for item in history], ["user", "assistant", "user"])
        self.assertEqual(history[-1]["content"], "Voici ma réponse")
        self.assertIn("kenobi-lecon", history[1]["content"])

    def test_old_long_messages_are_clipped_and_errors_left_out(self):
        session = {"messages": [{"id": f"m{index}", "role": "user" if index % 2 == 0 else "tutor", "createdAt": "",
                                 "content": f"message {index} " + "x" * 9000} for index in range(20)]}
        session["messages"].append({"id": "err", "role": "tutor", "createdAt": "", "content": "Kenobi n'a pas pu répondre", "error": True})
        history = tutor.build_history(session, "suite")
        self.assertNotIn("pas pu répondre", json.dumps(history, ensure_ascii=False))
        self.assertLess(len(history[0]["content"]), 5000)
        self.assertIn("passage coupé", history[0]["content"])
        self.assertEqual(history[-1], {"role": "user", "content": "suite"})

    def test_turn_note_reminds_the_right_block(self):
        tools = {"terminal": True, "language": "python", "file": "exercice.py"}
        opening = tutor.turn_note({"messages": []}, "On commence", tools)
        for expected in ("c'est le cours", "sans exercice ni question de rappel", "même s'il dit connaître déjà",
                         "kenobi-lecon puis ```kenobi-pret", "(10 à 20 lignes hors code)"):
            self.assertIn(expected, opening)
        self.assertIn("cinq à huit phrases", tutor.turn_note({"messages": []}, "[Appel vocal] on y va", tools, voice=True))
        session = {"contract": {"objective": "Lire", "criteria": []}}
        free = tutor.turn_note(session, "Une question", tools)
        self.assertIn("kenobi-exercice", free)
        self.assertIn("kenobi-boutons", free)
        attempt = tutor.turn_note(session, "[Exécution — tentative n° 2 pour la consigne en cours.]", tools)
        self.assertIn("kenobi-verdict", attempt)
        self.assertNotIn("kenobi-exercice", attempt)
        theory = tutor.turn_note(session, "Une question", {"terminal": False, "language": "", "file": ""})
        self.assertNotIn("kenobi-exercice", theory)
        self.assertIn("kenobi-boutons", theory)

    def test_what_can_be_clicked_is_not_asked_in_writing(self):
        tools = {"terminal": True, "language": "python", "file": "exercice.py"}
        session = {"contract": {"objective": "Lire", "criteria": []}, "messages": []}
        free = tutor.turn_note(session, "Explique autrement.", tools)
        for expected in ("se pose en QCM", "se termine par le bloc ```kenobi-boutons", "il n'écrit que pour expliquer",
                         "des boutons pour la suite"):
            self.assertIn(expected, free)
        wrong = tutor.turn_note(session, "[QCM — il a choisi « 1 » : ce n'est pas la bonne réponse, qui était « 2 ».]", tools)
        self.assertIn("puis un QCM proche", wrong)
        session["messages"] = [{"role": "tutor", "content": "**Écris une boucle**.\n\n```kenobi-exercice\nattendu: 1, 2 et 3\n```"}]
        self.assertIn("une question fermée, par le bloc ```kenobi-boutons", tutor.turn_note(session, "Je bloque", tools))

    def test_the_planned_duration_reaches_kenobi(self):
        self.assertEqual([tutor._minutes(value) for value in (20, 25.0, "20 min", "environ 45 minutes", 0, "", None, True)],
                         [20, 25, 20, 45, None, None, None, None])
        state = real_state()
        state["courses"][0]["modules"][0]["lessons"][0]["duration"] = ""
        context = tutor.build_context(state, "session-a", data_dir=DATA, today=date(2026, 11, 8))
        self.assertNotIn("Durée prévue", context)

    def test_after_the_course_c_est_parti_starts_the_practice(self):
        tools = {"terminal": True, "language": "python", "file": "exercice.py"}
        course = ("**Ce qu'on va faire** : une liste.\n\n```kenobi-lecon\nobjectif: Lire une liste\n```\n\n"
                  "```kenobi-pret\nbouton: C'est parti !\n```")
        session = {"contract": {"objective": "Lire une liste", "criteria": []},
                   "messages": [{"role": "user", "content": "Commençons la leçon « Listes »."},
                                {"role": "tutor", "content": course}]}
        self.assertTrue(tutor.awaits_start(session))
        note = tutor.turn_note(session, "C’est parti !", tools)
        self.assertIn("tu viens de lui demander s'il est prêt", note)
        self.assertIn("le premier exercice, terminé par son bloc ```kenobi-exercice", note)
        self.assertIn("termine par le bloc ```kenobi-pret", note)
        self.assertIn("dans la discussion", tutor.turn_note(session, "oui", {"terminal": False, "language": "", "file": ""}))
        # Une réponse en échec ne compte pas ; la pratique commencée, le départ n'est plus attendu.
        session["messages"].append({"role": "tutor", "content": "Kenobi n'a pas pu répondre", "error": True})
        self.assertTrue(tutor.awaits_start(session))
        session["messages"].append({"role": "tutor", "content": "**Écris une liste**.\n\n```kenobi-exercice\nattendu: 3\n```"})
        self.assertFalse(tutor.awaits_start(session))
        self.assertNotIn("s'il est prêt", tutor.turn_note(session, "Une question", tools))

    def test_a_quiz_answer_is_not_judged_twice_and_quizzes_come_back(self):
        tools = {"terminal": True, "language": "python", "file": "exercice.py"}
        quiz = "Qu'affiche ce code ?\n\n```kenobi-qcm\nchoix: 1\nchoix: 2\nchoix: 3\nreponse: 2\n```"
        session = {"contract": {"objective": "Lire", "criteria": []},
                   "messages": [{"role": "tutor", "content": "Cours."}, {"role": "tutor", "content": quiz}]}
        self.assertEqual(tutor.replies_since_quiz(session), 0)
        note = tutor.turn_note(session, "[QCM — il a choisi « 1 » : ce n'est pas la bonne réponse, qui était « 2 ».]", tools)
        self.assertIn("ne les répète pas", note)
        self.assertIn("ce qui a pu le tromper", note)
        self.assertNotIn("peut être un QCM", tutor.turn_note(session, "Et ensuite ?", tools))
        session["messages"] += [{"role": "tutor", "content": "Bien."} for _ in range(tutor.QUIZ_EVERY)]
        self.assertEqual(tutor.replies_since_quiz(session), tutor.QUIZ_EVERY)
        self.assertIn("peut être un QCM (bloc ```kenobi-qcm)", tutor.turn_note(session, "Et ensuite ?", tools))
        self.assertIn("la suite peut être un QCM", tutor.turn_note(session, "[Exécution — tentative n° 1 pour la consigne en cours.]", tools))
        self.assertEqual(tutor.replies_since_quiz({"messages": []}), 0)

    def test_an_open_instruction_is_never_solved_unasked(self):
        tools = {"terminal": True, "language": "python", "file": "exercice.py"}
        block = "```kenobi-exercice\nfichier: exercice.py\nattendu: 1, 2 et 3\n```"
        tutor_says = lambda text: {"role": "tutor", "content": text}
        verdict = lambda word, more="": tutor_says(f"Voilà.{more}\n\n```kenobi-verdict\nrésultat: {word}\n```")
        attempt = {"role": "user", "content": "[Exécution — tentative n° 1 …]", "run": {"status": "ok", "attempt": 1}, "reason": "exercise"}
        session = {"contract": {"objective": "Lire", "criteria": []}, "messages": [tutor_says("**Écris une boucle**.\n\n" + block)]}
        self.assertTrue(tutor.has_open_task(session))
        note = tutor.turn_note(session, "C'est quoi range ?", tools)
        self.assertIn("sans écrire le code attendu", note)
        self.assertIn("seulement s'il le demande explicitement", note)
        self.assertIn("sans écrire le code attendu", tutor.turn_note(session, "[Appel vocal] je bloque", tools, voice=True))
        # Une tentative garde son propre rappel.
        self.assertNotIn("sans écrire le code attendu", tutor.turn_note(session, "[Exécution — tentative n° 1 pour la consigne en cours.]", tools))
        # Un verdict que rien ne demandait ne clôt rien ; « presque » non plus ; le bloc redit n'ouvre rien.
        session["messages"] += [{"role": "user", "content": "Donne-moi la solution."}, verdict("reussi", "\n\n" + block)]
        self.assertTrue(tutor.has_open_task(session))
        session["messages"] += [attempt, verdict("presque")]
        self.assertTrue(tutor.has_open_task(session))
        # « Réussi » en réponse à une tentative la clôt, même si le bloc est redit dans la même réponse.
        session["messages"] += [attempt, verdict("Réussi", "\n\n" + block.replace("1, 2 et 3", "1, 2 ET 3"))]
        self.assertFalse(tutor.has_open_task(session))
        self.assertNotIn("sans écrire le code attendu", tutor.turn_note(session, "Et ensuite ?", tools))
        # Une autre consigne dans la réponse qui valide : la suivante est ouverte.
        session["messages"][-1] = verdict("reussi", "\n\n" + block.replace("1, 2 et 3", "3, 2 et 1"))
        self.assertTrue(tutor.has_open_task(session))
        self.assertFalse(tutor.has_open_task({"messages": [{"role": "tutor", "content": block, "error": True}]}))
        # Séance d'avant les verdicts : un lancement réussi rendait l'exercice.
        legacy = {"messages": [tutor_says(block), {"role": "user", "content": "…", "run": {"status": "ok"}, "reason": "exercise"}]}
        self.assertFalse(tutor.has_open_task(legacy))


class ReplyTests(unittest.TestCase):
    def setUp(self):
        self.stub = StubLLM()
        self.directory = tempfile.TemporaryDirectory()
        self.settings = Settings(Path(self.directory.name))
        providers._quirks.clear()
        self.settings.update({"learner": LEARNER, "ai": {"provider": "custom", "baseUrl": self.stub.base_url,
                                                          "apiKey": "clef-de-test", "model": "stub-fast"}})

    def tearDown(self):
        self.stub.close()
        self.directory.cleanup()

    def test_reply_streams_from_the_plugged_model_with_the_whole_context(self):
        self.stub.say("Bien", " vu.")
        events = list(tutor.stream_reply(real_state(), {"sessionId": "session-a", "message": "Voici ma réponse", "terminal": "coach"},
                                         settings=self.settings, data_dir=DATA))
        self.assertEqual("".join(data["text"] for kind, data in events if kind == "delta"), "Bien vu.")
        self.assertEqual(events[-1][0], "done")
        body = self.stub.chat_requests()[0]["body"]
        self.assertEqual(body["model"], "stub-fast")
        self.assertIn("Tu es Kenobi", body["messages"][0]["content"])
        self.assertIn("Camille", body["messages"][0]["content"])
        self.assertEqual([item["role"] for item in body["messages"]], ["system", "user", "assistant", "user"])
        self.assertTrue(body["messages"][-1]["content"].startswith("Voici ma réponse"))
        self.assertIn("[Interface — rappels", body["messages"][-1]["content"])
        self.assertTrue(tutor.status(self.settings)["connected"])

    def test_voice_turn_adds_the_spoken_rules_for_this_turn_only(self):
        self.stub.say("Oui.")
        list(tutor.stream_reply(real_state(), {"sessionId": "session-a", "message": "[Appel vocal] c'est quoi une variable"},
                                settings=self.settings, data_dir=DATA, voice=True))
        last = self.stub.chat_requests()[0]["body"]["messages"][-1]["content"]
        self.assertTrue(last.startswith("[Appel vocal : Camille te parle au micro"))
        self.assertIn("[Appel vocal] c'est quoi une variable", last)

    def test_errors_are_reported_and_remembered_for_the_status(self):
        self.stub.fail(401)
        with self.assertRaises(tutor.ChatError) as caught:
            list(tutor.stream_reply(real_state(), {"sessionId": "session-a", "message": "Salut"}, settings=self.settings))
        self.assertIn("Clé refusée", caught.exception.message)
        self.assertIn("Clé refusée", tutor.status(self.settings)["problem"])
        self.stub.say("ok")
        self.assertEqual(tutor.test_connection(self.settings)["sample"], "ok")
        self.assertEqual(tutor.status(self.settings)["problem"], "")

    def test_bad_requests_are_refused_before_any_call(self):
        cases = (({"sessionId": "session-a", "message": "  "}, 400), ({"sessionId": "x", "message": "a"}, 400),
                 ({"sessionId": "inconnue", "message": "a"}, 404),
                 ({"sessionId": "session-a", "message": "x" * (tutor.MAX_MESSAGE + 1)}, 413))
        for payload, status in cases:
            with self.subTest(status=status, session=payload["sessionId"]):
                with self.assertRaises(tutor.ChatError) as caught:
                    list(tutor.stream_reply(real_state(), payload, settings=self.settings))
                self.assertEqual(caught.exception.status, status)
        self.assertEqual(self.stub.chat_requests(), [])

    def test_without_model_the_person_is_sent_to_the_settings(self):
        empty = Settings(Path(self.directory.name) / "vide")
        with self.assertRaises(tutor.ChatError) as caught:
            list(tutor.stream_reply(real_state(), {"sessionId": "session-a", "message": "Salut"}, settings=empty))
        self.assertEqual(caught.exception.status, 409)
        self.assertFalse(tutor.status(empty)["connected"])


if __name__ == "__main__":
    unittest.main()
