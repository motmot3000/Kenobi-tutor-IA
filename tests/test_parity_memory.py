"""Parity of browser/memory.js with memory.py, the reference: same validation messages, same plan synchronisation
(result, changes and errors on hand-written states), same MEMORY.md byte for byte."""
import copy
import json
import random
import unittest
from pathlib import Path

import memory
from parity_helper import NODE, JsError, js_many
from test_memory import former_python_state, sample_state

ROOT = Path(__file__).resolve().parent.parent
DEFAULT = json.loads((ROOT / "data" / "default-state.json").read_text(encoding="utf-8"))
DEMO = json.loads((ROOT / "tests" / "fixtures" / "demo-state.json").read_text(encoding="utf-8"))
DELETE = object()
# Values put in place of every node of a state; "\x1c" and "\x85" are spaces for Python, "\ufeff" is not.
REPLACEMENTS = (None, True, False, 0, 1, 2.5, -3, "", " ", "\x1c", "\x85", "\u00a0", "\ufeff", "x", "done", "Python",
                [], ["x"], [1], [None], {}, {"a": 1})


def rich_state():
    """sample_state with every optional field, and a second course."""
    state = sample_state()
    course = state["courses"][0]
    course["shortTitle"] = "Py"
    course["goals"] = ["créer un dépôt", ""]
    course["tools"] = {"terminal": True, "language": "shell", "file": "exercice.sh"}
    module = course["modules"][0]
    module["brief"] = "Dépôt, index, commit."
    module["source"] = "Module 1"
    module["lessons"][0]["objectives"] = ["initialiser un dépôt", "lire l'état"]
    course["memory"]["facts"] = ["veut versionner ses cours"]
    course["memory"]["reviews"] = [{"item": "Boucle while", "step": 7, "due": "2026-11-15"},
                                   {"item": "Indentation", "due": "2026-11-09"}]
    second = copy.deepcopy(course)
    second.update({"id": "git", "title": "Git"})
    second["memory"]["lastSessionId"] = None
    state["courses"].append(second)
    return state


def paths(value, prefix=()):
    yield prefix
    if isinstance(value, dict):
        for key, item in value.items():
            yield from paths(item, prefix + (key,))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from paths(item, prefix + (index,))


def mutated(state, path, replacement):
    if not path:
        return copy.deepcopy(replacement)
    clone = copy.deepcopy(state)
    target = clone
    for key in path[:-1]:
        target = target[key]
    if replacement is DELETE:
        del target[path[-1]]
    else:
        target[path[-1]] = copy.deepcopy(replacement)
    return clone


def with_course(change, state=None):
    state = copy.deepcopy(state or sample_state())
    change(state, state["courses"][0])
    return state


def targeted_states():
    states = []
    for course_id in ("", " ", "\x1c", "\x85", "\ufeff", "../python", "Python", "python_1", "python 1", "a\n", "é",
                      "python-3", "0", "-", 3, None):
        def change(state, course, course_id=course_id):
            course["id"] = course_id
            state["activeCourseId"] = course_id if isinstance(course_id, str) else "python"
        states.append(with_course(change))
    for version in (1, 2, "1", True, 2.5, None, 0, DELETE):
        states.append(mutated(sample_state(), ("version",), version))
    duplicate = sample_state()
    duplicate["courses"].append(copy.deepcopy(duplicate["courses"][0]))
    states.append(duplicate)
    for active in ("", "git", "absent", None, 1):
        states.append(mutated(sample_state(), ("activeCourseId",), active))
        states.append({"version": 1, "activeCourseId": active, "courses": []})
    for status in ("done", "current", "todo", "review", "finished", "Done", " done", "", None):
        states.append(mutated(sample_state(), ("courses", 0, "modules", 1, "lessons", 1, "status"), status))
    for value in (20, 2.5, -1, "20 min", "", True, False, None, [], {}, DELETE):
        states.append(mutated(sample_state(), ("courses", 0, "modules", 0, "lessons", 1, "duration"), value))
        states.append(mutated(sample_state(), ("courses", 0, "sessions", 1, "duration"), value))
    for value in (DELETE, None, "session-new", "", 1, [], True):
        states.append(mutated(sample_state(), ("courses", 0, "memory", "lastSessionId"), value))
    for reviews in ("texte", [], [{}], [{"item": " ", "due": "x"}], [{"item": "a", "due": ""}], [{"item": "a"}],
                    ["x"], [{"item": "a", "due": "2026-11-09", "step": "x"}], [{"item": "\x85", "due": "d"}],
                    [{"item": "a", "due": "\ufeff"}], [None], {"item": "a", "due": "d"}):
        states.append(mutated(sample_state(), ("courses", 0, "memory", "reviews"), reviews))
    for key in ("acquired", "toReview", "facts", "goals", "objectives"):
        for value in ([], ["a", "b"], ["a", 1], "a", None, [""], [["a"]]):
            if key in ("facts",):
                states.append(mutated(sample_state(), ("courses", 0, "memory", key), value))
            elif key == "goals":
                states.append(mutated(sample_state(), ("courses", 0, "goals"), value))
            elif key == "objectives":
                states.append(mutated(sample_state(), ("courses", 0, "modules", 0, "lessons", 0, "objectives"), value))
            else:
                states.append(mutated(sample_state(), ("courses", 0, "memory", key), value))
    for tools in ({}, {"language": "python"}, {"language": 1}, {"file": None}, {"terminal": "oui"}, "python", None, []):
        states.append(mutated(sample_state(), ("courses", 0, "tools"), tools))
    for updated in ("2026-11-03", "", 5, None):
        states.append(mutated(sample_state(), ("updatedAt",), updated))
        states.append(mutated(sample_state(), ("courses", 0, "updatedAt"), updated))
    messages = sample_state()
    messages["courses"][0]["sessions"][1]["messages"].append({"id": "m2", "role": "tutor", "createdAt": "", "content": ""})
    states.append(messages)
    states.append(copy.deepcopy(DEFAULT))
    states.append(copy.deepcopy(DEMO))
    states.append(former_python_state())
    return states


def validation_states():
    states = targeted_states()
    for base, replacements in ((rich_state(), REPLACEMENTS + (DELETE,)), (DEMO, (None, "", " ", 1, [], {}, DELETE))):
        for path in paths(base):
            for replacement in replacements:
                if path or replacement is not DELETE:
                    states.append(mutated(base, path, replacement))
    return states


def py_validate(state):
    try:
        memory.validate_state(copy.deepcopy(state))
        return ("ok", None)
    except memory.StateValidationError as error:
        return ("StateValidationError", str(error))


def js_validate(value, state):
    if isinstance(value, JsError):
        return (value.name, value.message)
    return ("ok", None) if value == state else ("changed", value)


def py_sync(state, default):
    state = copy.deepcopy(state)
    try:
        return {"changed": memory.sync_builtin_plan(state, copy.deepcopy(default)), "state": state}
    except Exception as error:  # the JS port throws the same Python error names
        return {"error": [type(error).__name__, str(error)], "state": state}


def py_pipeline(state, default):
    state = copy.deepcopy(state)
    try:
        memory.validate_state(state)
        changed = memory.sync_builtin_plan(state, copy.deepcopy(default))
        memory.validate_state(state)
        return {"changed": changed, "state": state,
                "memories": [memory.render_course_memory(state, course) for course in state["courses"]]}
    except Exception as error:
        return {"error": [type(error).__name__, str(error)]}


def known_course(**extra):
    """A course that sync_builtin_plan recognises (it has a lesson of the shipped plan)."""
    course = {"id": "python", "modules": [{"lessons": [{"id": "m0-types", "status": "done"}]}], "sessions": []}
    course.update(extra)
    return course


def malformed_sync_cases():
    tiny_default = {"courses": [{"id": "python", "title": "Plan", "modules": [
        {"id": "m", "title": "M", "lessons": [{"id": "a", "title": "A"}, {"id": "b", "title": "B"}]}]}]}
    lesson_a = {"id": "python", "modules": [{"lessons": [{"id": "a", "status": "done"}]}]}
    cases = [
        ({"courses": "ab"}, DEFAULT),
        ({"courses": 5}, DEFAULT),
        ({"courses": {"python": 1}}, DEFAULT),
        ({"courses": [None]}, DEFAULT),
        ({"courses": [3]}, DEFAULT),
        ({"courses": [{"id": ["python"]}]}, DEFAULT),
        ({"courses": [{"id": {"a": 1}}]}, DEFAULT),
        ({"courses": [{"id": "python"}]}, DEFAULT),
        ({"courses": [{"id": "python", "modules": None}]}, DEFAULT),
        ({"courses": [{"id": "python", "modules": [{"lessons": [{"id": "m0-types"}]}, 5]}]}, DEFAULT),
        ({"courses": [{"id": "python", "modules": ["abc"]}]}, DEFAULT),
        ({"courses": [{"id": "python", "modules": [[1]]}]}, DEFAULT),
        ({"courses": [{"id": "python", "modules": [{"lessons": ["m0-types"]}]}]}, DEFAULT),
        ({"courses": [{"id": "python", "modules": [{"lessons": [{"id": ["x"]}]}]}]}, DEFAULT),
        ({"courses": [{"id": "python", "modules": [{"lessons": [{"id": "m0-types", "status": "done"}, 5]}]}]}, DEFAULT),
        ({"courses": [{"id": "python", "modules": [{"lessons": [{"id": "mimo-basics", "status": "done"},
                                                                {"id": ["x"], "status": "done"}]}]}]}, DEFAULT),
        ({"courses": [known_course(sessions=5)]}, DEFAULT),
        ({"courses": [known_course(sessions=[None])]}, DEFAULT),
        ({"courses": [known_course(sessions=[{"lessonId": ["x"]}])]}, DEFAULT),
        ({"courses": [known_course(sessions=[{"lessonId": "mimo-basics", "title": "x"}, None])]}, DEFAULT),
        ({"courses": [known_course(sessions="ab")]}, DEFAULT),
        ({"courses": [known_course(sessions={"lessonId": "mimo-loops"})]}, DEFAULT),
        ({"courses": [former_python_state()["courses"][0], None]}, DEFAULT),
        ({"courses": [known_course()]}, None),
        ({"courses": [known_course()]}, {}),
        ({"courses": [known_course()]}, []),
        ({"courses": [known_course()]}, ["x"]),
        ({"courses": [known_course()]}, {"courses": None}),
        ({"courses": [known_course()]}, {"courses": "abc"}),
        ({"courses": [known_course()]}, {"courses": [1, "x", None, {"title": "sans id"}]}),
        ({"courses": [known_course()]}, {"courses": [{"id": ["l"]}]}),
        ({"courses": [{"id": "python", "modules": []}]}, {"courses": [{"id": "python"}]}),
        ({"courses": [lesson_a]}, {"courses": [{"id": "python", "modules": [{"lessons": [{"id": "a"}]}]}]}),
        ({"courses": [lesson_a]}, {"courses": [{"id": "python", "modules": [{"lessons": [{"title": "A"}]}]}]}),
        ({"courses": [lesson_a]}, tiny_default),
        ({"courses": [dict(lesson_a, id=True)]}, {"courses": [dict(tiny_default["courses"][0], id=1)]}),
        ({"courses": [dict(lesson_a, id=1.5)]}, {"courses": [dict(tiny_default["courses"][0], id=1.5)]}),
        ({"courses": [dict(lesson_a, title=None)]},
         {"courses": [dict(tiny_default["courses"][0], title=None, goals=[1, True], level="x")]}),
        ({"courses": [{"id": "python", "title": "Plan", "modules": [
            {"id": "m", "title": "M", "lessons": [{"id": "a", "title": "A", "status": "done"},
                                                  {"id": "b", "title": "B", "status": "current"}]}]}]}, tiny_default),
        ({"courses": [{"id": "python", "title": "Plan", "modules": [
            {"id": "m", "title": "M", "lessons": [{"id": "a", "title": "A", "status": True},
                                                  {"id": "b", "title": "B", "status": "current"}]}]}]}, tiny_default),
        ({"courses": [lesson_a, {"id": "python", "modules": [{"lessons": [{"id": "b", "status": "todo"}]}]}]},
         tiny_default),
        # Python keys: "None" is not None, "n:1" is not 1; an empty reference is skipped.
        ({"courses": [dict(lesson_a, id="None")]}, {"courses": [{k: v for k, v in tiny_default["courses"][0].items()
                                                                 if k != "id"}]}),
        ({"courses": [dict(lesson_a, id="n:1")]}, {"courses": [dict(tiny_default["courses"][0], id=1)]}),
        ({"courses": [{"modules": []}]}, {"courses": [{}]}),
        # Python's == : True == 1, so an up-to-date course is left as it is.
        ({"courses": [{"id": "python", "title": "Plan", "goals": [True, 1], "modules": [
            {"id": "m", "title": "M", "lessons": [{"id": "a", "title": "A", "duration": True, "status": "done"},
                                                  {"id": "b", "title": "B", "duration": 2, "status": "current"}]}]}]},
         {"courses": [{"id": "python", "title": "Plan", "goals": [1, True], "modules": [
             {"id": "m", "title": "M", "lessons": [{"id": "a", "title": "A", "duration": 1},
                                                   {"id": "b", "title": "B", "duration": 2.0}]}]}]}),
        ({"courses": []}, DEFAULT),
        ({"courses": None}, DEFAULT),
        ({}, DEFAULT),
    ]
    return cases


def fuzz_state(rng):
    """The shipped course as a learner may have it: progress, former lesson ids, sessions, stale fields."""
    state = copy.deepcopy(DEFAULT)
    course = state["courses"][0]
    former = {new: old for old, new in memory.FORMER_LESSON_IDS.items()}
    lesson_ids = []
    for module in course["modules"]:
        for lesson in module["lessons"]:
            lesson["status"] = rng.choice(["done", "done", "todo", "current", "review"])
            if lesson["id"] in former and rng.random() < 0.5:
                lesson["id"] = former[lesson["id"]]
            if rng.random() < 0.1:
                lesson["title"] = "Ancien titre"
            if rng.random() < 0.1:
                lesson["duration"] = rng.choice([45, "40 min", 20.5])
            lesson_ids.append(lesson["id"])
    if rng.random() < 0.3:
        course["modules"].pop(rng.randrange(len(course["modules"])))
    if rng.random() < 0.2:
        rng.shuffle(course["modules"])
    candidates = lesson_ids + list(memory.FORMER_LESSON_IDS) + ["inconnue", ""]
    course["sessions"] = []
    for index in range(rng.randint(0, 6)):
        course["sessions"].append({
            "id": f"s{index}", "title": f"Séance {index}", "startedAt": f"2026-11-{10 + index}T10:00:00Z",
            "updatedAt": rng.choice([f"2026-11-{10 + index}T10:30:00Z", "2026-11-12T10:30:00Z"]), "duration": index * 5,
            "status": rng.choice(["active", "completed"]), "lessonId": rng.choice(candidates),
            "summary": rng.choice(["", "Bilan.", "Bilan avec espace final \u3000"]),
            "messages": [{"id": f"s{index}-m", "role": "tutor", "createdAt": "", "content": "Cours."}]})
    if rng.random() < 0.3:
        course["title"] = "Ancien titre"
    if rng.random() < 0.2:
        del course["goals"]
    if rng.random() < 0.2:
        course["level"] = "Autre"
    if rng.random() < 0.1:
        course["id"] = "autre"
        state["activeCourseId"] = "autre"
    if rng.random() < 0.2:
        course["memory"]["reviews"] = [{"item": "range", "step": rng.choice([1, 3, 7]), "due": rng.choice(["2026-11-13", "2026-11-09"])},
                                       {"item": "listes", "due": "2026-11-09"}]
    if rng.random() < 0.2:
        imported = copy.deepcopy(DEMO["courses"][0])
        imported["id"] = "python-2"
        state["courses"].append(imported)
    return state


def render_states():
    """(state, course index) pairs covering every branch of render_course_memory."""
    cases = [(copy.deepcopy(DEMO), 0), (copy.deepcopy(DEFAULT), 0), (sample_state(), 0), (rich_state(), 0),
             (rich_state(), 1), (former_python_state(), 0)]

    def lessons(done, total, current=True):
        statuses = ["done"] * done + ["todo"] * (total - done)
        if current and done < total:
            statuses[done] = "current"
        return [{"id": f"l{index}", "title": f"Leçon {index}", "status": status, "duration": 20}
                for index, status in enumerate(statuses)]

    def state(course_changes=None, state_changes=None, done=1, total=4, current=True):
        value = sample_state()
        course = value["courses"][0]
        course["modules"] = [{"id": "m", "title": "M", "description": "", "lessons": lessons(done, total, current)}]
        course.update(course_changes or {})
        value.update(state_changes or {})
        return value

    for done, total in ((0, 0), (1, 8), (3, 8), (5, 8), (7, 8), (1, 3), (2, 3), (1, 200), (3, 200), (199, 200),
                        (1, 40), (3, 40), (1, 6), (5, 6), (8, 8), (0, 5)):
        cases.append((state(done=done, total=total), 0))
    cases.append((state(done=1, total=4, current=False), 0))
    cases.append((state(done=4, total=4), 0))
    review = lambda due, item, **extra: dict({"item": item, "due": due}, **extra)  # noqa: E731
    memories = [
        {"reviews": [review("2026-11-15", "b", step=7), review("2026-11-09", "a"), review("2026-11-09", "c", step="3"),
                     review("2026-11-09", "d", step=None), review("2026-11-10", "e", step=2.5),
                     review("2026-11-10", "f", step=True), review("2026-11-11", "g", step=[1]),
                     review("2026-11-11", "h", step={"a": "b"}), review("\uff01", "i"), review("\U0001F600", "j"),
                     review("2026-11-09", "k", step=1e-05), review("2026-11-09", "l", step=2 ** 53),
                     review("2026-11-09", "m", step=10 ** 20), review("2026-11-09", "n", step=1.5e300),
                     review("2026-11-09", "o", step=1e22), review("2026-11-09", "p", step=-0.25)]},
        {"facts": ["aime les jeux", "préfère l'écrit"], "nextFocus": "", "notes": ""},
        {"facts": [], "acquired": [], "toReview": [], "reviews": []},
        {"nextFocus": "Faire\nune boucle", "notes": "Note finale avec espaces \x85\x1c\u3000 "},
        {"notes": "Note finale avec BOM \ufeff"},
    ]
    for changes in memories:
        value = sample_state()
        value["courses"][0]["memory"].update(changes)
        cases.append((value, 0))
    title = 'Cours "spécial" \\ \n\t\x01\x1f\x7f\u2028 é 😀 \u200b'
    cases.append((state({"title": title, "goals": ["", "Objectif 1", "Objectif 2"]}), 0))
    cases.append((state({"goals": []}), 0))
    without = state()
    del without["courses"][0]["updatedAt"]
    cases.append((without, 0))
    neither = state()
    del neither["courses"][0]["updatedAt"]
    del neither["updatedAt"]
    cases.append((neither, 0))
    empty_updated = state({"updatedAt": ""}, {"updatedAt": "2026-11-04T08:00:00Z"})
    cases.append((empty_updated, 0))
    sessions = state()
    base_session = sessions["courses"][0]["sessions"][0]
    sessions["courses"][0]["sessions"] = [
        dict(base_session, id=f"s{index}", title=f"Séance {index}", updatedAt=updated, summary=summary)
        for index, (updated, summary) in enumerate([
            ("2026-11-02T09:00:00Z", "A"), ("2026-11-03T09:00:00Z", ""), ("2026-11-02T09:00:00Z", "C"),
            ("\U0001F600", "D"), ("\uff01", "E"), ("2026-11-03T09:00:00Z", "F\u3000"), ("", "G")])]
    cases.append((sessions, 0))
    no_sessions = state()
    no_sessions["courses"][0]["sessions"] = []
    cases.append((no_sessions, 0))
    # The text ends with the last summary: Python's rstrip removes "\x85" and "\x1c", not "\ufeff".
    for ending in ("\x85", "\x1c\x1d", "\ufeff", " \ufeff ", "\u3000\u2028"):
        last = state()
        last["courses"][0]["sessions"] = [dict(base_session, summary=f"Dernier résumé{ending}")]
        cases.append((last, 0))
    return cases


@unittest.skipUnless(NODE, "node absent")
class ValidationParityTests(unittest.TestCase):
    def test_same_verdict_and_message_on_valid_and_broken_states(self):
        states = validation_states()
        answers = js_many([("memory", "validateState", [state]) for state in states], timeout=300)
        failures = 0
        for state, answer in zip(states, answers):
            expected, actual = py_validate(state), js_validate(answer, state)
            if expected != actual:
                failures += 1
                if failures <= 5:
                    with self.subTest(state=json.dumps(state, ensure_ascii=False)[:300]):
                        self.assertEqual(actual, expected)
        self.assertEqual(failures, 0, f"{failures} différences sur {len(states)} états")
        verdicts = [py_validate(state)[0] for state in states]
        self.assertGreater(verdicts.count("ok"), 50)
        self.assertGreater(verdicts.count("StateValidationError"), 1000)

    def test_the_error_is_a_state_validation_error(self):
        [answer] = js_many([("memory", "validateState", [{"version": 2}])])
        self.assertIsInstance(answer, JsError)
        self.assertEqual((answer.name, answer.message), ("StateValidationError", "state.version doit être égal à 1."))


@unittest.skipUnless(NODE, "node absent")
class SyncParityTests(unittest.TestCase):
    def check(self, cases):
        answers = js_many([("test:memory_harness", "syncPlan", [state, default]) for state, default in cases],
                          timeout=300)
        for (state, default), answer in zip(cases, answers):
            with self.subTest(state=json.dumps(state, ensure_ascii=False)[:200], default=str(default)[:80]):
                self.assertNotIsInstance(answer, JsError)
                self.assertEqual(answer, py_sync(state, default))

    def test_fixtures_and_renamed_lessons(self):
        cases = [(former_python_state(), DEFAULT), (copy.deepcopy(DEFAULT), DEFAULT), (sample_state(), DEFAULT),
                 (copy.deepcopy(DEMO), DEFAULT), ({"version": 1, "activeCourseId": "", "courses": []}, DEFAULT)]
        self.check(cases)
        self.assertTrue(py_sync(former_python_state(), DEFAULT)["changed"])
        self.assertFalse(py_sync(copy.deepcopy(DEFAULT), DEFAULT)["changed"])

    def test_hand_written_states_fail_at_the_same_point(self):
        cases = malformed_sync_cases()
        self.check(cases)
        errors = [py_sync(state, default).get("error", [None])[0] for state, default in cases]
        self.assertTrue({"KeyError", "TypeError", "AttributeError"} <= set(errors))

    def test_random_progress_on_the_shipped_course(self):
        rng = random.Random(20261124)
        self.check([(fuzz_state(rng), DEFAULT) for _ in range(150)])

    def test_server_pipeline_validate_sync_validate_render(self):
        rng = random.Random(14)
        states = [fuzz_state(rng) for _ in range(60)] + [former_python_state(), copy.deepcopy(DEMO), rich_state()]
        answers = js_many([("test:memory_harness", "pipeline", [state, DEFAULT]) for state in states], timeout=300)
        for state, answer in zip(states, answers):
            with self.subTest(state=json.dumps(state, ensure_ascii=False)[:200]):
                self.assertEqual(answer, py_pipeline(state, DEFAULT))
        self.assertGreater(sum("memories" in py_pipeline(state, DEFAULT) for state in states), 40)


@unittest.skipUnless(NODE, "node absent")
class RenderParityTests(unittest.TestCase):
    def test_memory_markdown_is_identical(self):
        cases = render_states()
        answers = js_many([("memory", "renderCourseMemory", [state, state["courses"][index]]) for state, index in cases])
        for (state, index), answer in zip(cases, answers):
            with self.subTest(course=state["courses"][index]["title"][:40]):
                self.assertNotIsInstance(answer, JsError)
                self.assertEqual(answer, memory.render_course_memory(state, state["courses"][index]))


if __name__ == "__main__":
    unittest.main()
