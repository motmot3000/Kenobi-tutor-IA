"""Parity of browser/tutor.js with tutor.py: for the same inputs, the same prompt, context, history, reminders,
events and errors. The JS side runs in Node through tests/parity/tutor_harness.js, one process per batch."""
import copy
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

import providers
import tutor

try:
    from parity_helper import NODE, js, js_many
except ImportError:
    from tests.parity_helper import NODE, js, js_many

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
HARNESS = "test:tutor_harness"
TODAY = date(2026, 11, 24)
PLAN = (DATA / "plan-formation-python.md").read_bytes().decode("utf-8")
DEMO = json.loads((FIXTURES / "demo-state.json").read_text(encoding="utf-8"))
DEFAULT = json.loads((DATA / "default-state.json").read_text(encoding="utf-8"))

CAMILLE = {"name": "Camille", "about": "Débute en programmation."}
LEARNERS = [None, {}, CAMILLE, {"name": "  ", "about": "  "}, {"name": "", "about": "Prof de maths"},
            {"name": " Léa ", "about": None}, {"name": None, "about": ""}, {"name": "A$&B {course} $1", "about": "x"}]
TERMINALS = [None, "coach", "silent", "off", "closed", "nope", "", 3, True, "constructor"]
AI = {"provider": "custom", "label": "Service de test", "kind": "openai", "baseUrl": "http://127.0.0.1:9/v1",
      "model": "stub-model", "apiKey": "cle-de-test", "keySource": "saved", "reasoning": "none", "configured": True,
      "keyless": True}
UNCONFIGURED = {"provider": "", "label": "", "kind": "openai", "baseUrl": "", "model": "", "apiKey": "", "keySource": "",
                "reasoning": "none", "configured": False, "keyless": False}
PY_OPTION_NAMES = {"max_tokens": "maxTokens", "temperature": "temperature", "cancel": "signal", "timeout": "timeout"}


def plain(value):
    return json.loads(json.dumps(value, ensure_ascii=False))


def outcome(function, *args, **kwargs):
    """Result or error, in the shape of tutor_harness.js `call`."""
    try:
        return {"ok": True, "value": plain(function(*args, **kwargs))}
    except tutor.ChatError as error:
        return {"ok": False, "name": "ChatError", "status": error.status, "message": error.message}
    except Exception as error:  # the JS side only reports the kind of error
        return {"ok": False, "name": type(error).__name__}


def failure(error):
    if isinstance(error, tutor.ChatError):
        return {"ok": False, "name": "ChatError", "status": error.status, "message": error.message}
    return {"ok": False, "name": type(error).__name__}


def call(name, *args):
    return (HARNESS, "call", [name, list(args)])


def write_document(folder, state, session_id, document):
    """Put the course document where tutor.py looks for it: <runtime>/<course id>/COURSE.md."""
    tutor._plan_cache.clear()
    if document is None:
        return
    course, _session = tutor.find_session(state, session_id)
    if course is None:
        return
    path = Path(folder) / str(course.get("id") or "") / "COURSE.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(document.encode("utf-8"))


def py_prompt(function, state, session_id, learner=None, today=TODAY, terminal=None, document=None):
    with tempfile.TemporaryDirectory() as folder:
        write_document(folder, state, session_id, document)
        return outcome(function, state, session_id, learner=learner, runtime_dir=folder, today=today,
                       terminal=terminal)


def describe(options):
    """The options given to the provider, named as in the JS interface (timeout in ms)."""
    timeout = options.get("timeout")
    return {"keys": sorted(PY_OPTION_NAMES[key] for key in options),
            "maxTokens": options.get("max_tokens"), "temperature": options.get("temperature"),
            "timeout": timeout * 1000 if timeout is not None else None}


class FakeSettings:
    def __init__(self, ai, learner=None):
        self._ai, self._learner = ai, learner if learner is not None else {"name": "", "about": ""}

    def load(self):
        return {"learner": copy.deepcopy(self._learner), "ai": {}}

    def ai(self, data=None):
        return copy.deepcopy(self._ai)


def fixed_date(iso):
    day = date.fromisoformat(iso)

    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(day.year, day.month, day.day)

    return FixedDate


def finish(config, script):
    return script.get("done") or {"elapsed": 0.5, "firstToken": 0.1, "truncated": False, "model": config["model"]}


def fake_providers(script, calls):
    def stream_chat(config, system, messages, **options):
        calls.append({"config": copy.deepcopy(config), "system": system, "messages": copy.deepcopy(messages),
                      "options": describe(options)})
        for delta in script.get("deltas", []):
            yield "delta", delta
        if script.get("error"):
            raise providers.ProviderError(*script["error"])
        yield "done", finish(config, script)

    def complete(config, system, messages, **options):
        calls.append({"config": copy.deepcopy(config), "system": system, "messages": copy.deepcopy(messages),
                      "options": describe(options)})
        if script.get("error"):
            raise providers.ProviderError(*script["error"])
        return "".join(script.get("deltas", [])), finish(config, script)

    return stream_chat, complete


def py_run(kind, state, payload, options, script):
    """tutor.stream_reply or tutor.chat with the JS-style options of tutor_harness.js."""
    calls = []
    stream_chat, complete = fake_providers(script, calls)
    settings = FakeSettings(options.get("ai"), options.get("learner"))
    with tempfile.TemporaryDirectory() as folder, \
            mock.patch.object(tutor.providers, "stream_chat", stream_chat), \
            mock.patch.object(tutor.providers, "complete", complete), \
            mock.patch.object(tutor, "date", fixed_date(options.get("today") or TODAY.isoformat())):
        write_document(folder, state, str(payload.get("sessionId") or ""), options.get("documentText"))
        flags = {"voice": options.get("voice", False), "phone": options.get("phone", False)}
        try:
            if kind == "chat":
                return plain({"result": tutor.chat(state, payload, settings=settings, runtime_dir=folder, **flags),
                              "error": None, "calls": calls})
            events = []
            error = None
            try:
                for event in tutor.stream_reply(state, payload, settings=settings, runtime_dir=folder, **flags):
                    events.append(list(event))
            except Exception as caught:
                error = failure(caught)
            return plain({"events": events, "error": error, "calls": calls})
        except Exception as caught:
            return plain({"result": None, "error": failure(caught), "calls": calls})


def py_test_connection(ai, script):
    calls = []
    _stream_chat, complete = fake_providers(script, calls)
    with mock.patch.object(tutor.providers, "complete", complete):
        try:
            return plain({"result": tutor.test_connection(FakeSettings(ai)), "error": None, "calls": calls})
        except Exception as caught:
            return plain({"result": None, "error": failure(caught), "calls": calls})


# ── States ──────────────────────────────────────────────────────────────────


def lesson(lesson_id, title, status="todo", duration=20, objectives=None):
    item = {"id": lesson_id, "title": title, "status": status, "duration": duration}
    if objectives is not None:
        item["objectives"] = objectives
    return item


def session(session_id, lesson_id, messages=None, status="active", updated="2026-11-20T10:00:00Z", summary="",
            title="Séance", **extra):
    return {"id": session_id, "title": title, "startedAt": "", "updatedAt": updated, "duration": 0, "status": status,
            "lessonId": lesson_id, "summary": summary, "messages": messages or [], **extra}


def say(text, **extra):
    return {"role": "tutor", "content": text, **extra}


def ask(text, **extra):
    return {"role": "user", "content": text, **extra}


COURSE_REPLY = ("**Ce qu'on va faire** : un commit.\n\n```kenobi-lecon\nobjectif: Faire un commit\ncritere: git commit\n```"
                "\n\n```kenobi-pret\nbouton: C'est parti !\n```")
EXERCISE = "**Écris une boucle**.\n\n```kenobi-exercice\nfichier: exercice.py\nattendu: 1, 2 et 3\n```"
QUIZ = "Qu'affiche ce code ?\n\n```kenobi-qcm\nchoix: 1\nchoix: 2\nchoix: 3\nreponse: 2\n```"
CONTRACT = {"objective": "Faire un commit", "criteria": ["lancer git commit", "lire git log"]}


def synthetic():
    """Two courses: Git (shell terminal, document by source, module-N id and title) and a theory course."""
    return {
        "version": 1,
        "activeCourseId": "git",
        "courses": [
            {
                "id": "git", "title": "Git : les bases", "shortTitle": "Git", "description": "", "level": "débutant",
                "goals": ["créer un dépôt", "  committer  ", ""],
                "tools": {"terminal": True, "language": "shell", "file": "exercice.sh"},
                "modules": [
                    {"id": "git-m01", "title": "Premiers commits", "description": "", "brief": "Dépôt, index, commit.",
                     "source": "Module 1 — Premiers commits",
                     "lessons": [lesson("git-0101", "Créer un dépôt", "done", 30, ["initialiser un dépôt avec git init"]),
                                 lesson("git-0102", "Premier commit", "current", "25 min",
                                        ["faire un commit", " ", "lire git log"])]},
                    {"id": "module-2", "title": "Branches", "description": "",
                     "lessons": [lesson("git-0201", "Créer une branche")]},
                    {"id": "git-m03", "title": "Fusionner : les conflits !", "description": "", "brief": "  ",
                     "lessons": [lesson("git-0301", "Résoudre un conflit", duration=35)]},
                ],
                "sessions": [
                    session("session-git-1", "git-0102", [ask("On commence ?"), say(COURSE_REPLY), ask("C'est parti !")]),
                    session("session-git-0", "git-0101", [say("Bonjour.")], status="completed",
                            updated="2026-11-18T10:00:00Z", summary="Sait créer un dépôt.", title="Créer un dépôt"),
                    session("session-git-2", "git-0201", [], updated="2026-11-21T10:00:00Z"),
                    session("session-git-3", "git-0301", [ask("Salut", pending=True)], status="completed",
                            updated="2026-11-19T10:00:00Z", summary="Conflits vus.", title="Conflits"),
                ],
                "memory": {"lastSessionId": None, "acquired": ["git init", "git add"], "toReview": ["git status"],
                           "nextFocus": "Faire un premier commit.", "notes": "",
                           "reviews": [{"item": "git init", "step": 1, "due": "2026-11-24"},
                                       {"item": "git add", "step": 7, "due": "2026-12-01"}],
                           "facts": ["prof de maths", " "]},
            },
            {
                "id": "theorie", "title": "Théorie des graphes", "description": "",
                "tools": {"terminal": False, "language": "", "file": ""},
                "modules": [{"id": "graphes", "title": "Graphes", "description": "",
                             "lessons": [lesson("g-01", "Sommets et arêtes", duration="environ 45 minutes")]}],
                "sessions": [session("session-theorie", "g-01", [ask("Qu'est-ce qu'un graphe ?")])],
                "memory": {"lastSessionId": None, "acquired": [], "toReview": [], "nextFocus": "", "notes": ""},
            },
        ],
    }


GIT_DOC = "\n".join([
    "# Git : les bases", "", "Intro.", "",
    "## Module 1 : Premiers  COMMITS !", "", "Les trois zones de Git.", "",
    "### Leçon 1 — Créer un dépôt", "", "Détail de la leçon.", "",
    "```bash", "## Module 2 — dans un bloc de code", "```", "",
    "#### Sous-section", "", "Texte.", "",
    "## Module 21 — Pas celui-ci", "", "Mauvais module.", "",
    "## Module 2b — Pas celui-ci non plus", "",
    "## Module 2é — Toujours pas", "",
    "##### Module 2 — cinq dièses", "",
    "##Module 2 — sans espace", "",
    "## Module 2 — Branches ##", "", "Brancher et fusionner.", "",
    "### Leçon — Créer une branche", "", "Une branche.", "",
    "## Fusionner — les conflits", "", "Conflits.", "",
    "# Annexe", "", "Fin.", "",
])


def long_doc(paragraph, count, title="## Module 2 — Long"):
    return "# Cours\n\n" + title + "\n\n" + "\n\n".join(f"{index} {paragraph}" for index in range(count)) + "\n\n# Fin\n"


def git_variant(mutate=None, session="session-git-1", **options):
    state = synthetic()
    if mutate:
        mutate(state)
    return state, session, options


def course_of(state, index=0):
    return state["courses"][index]


def first_session(state, index=0):
    return state["courses"][index]["sessions"][0]


def set_duration(value):
    def mutate(state):
        course_of(state)["modules"][0]["lessons"][1]["duration"] = value
    return mutate


def set_contract(value):
    def mutate(state):
        first_session(state)["contract"] = value
    return mutate


def set_memory(**values):
    def mutate(state):
        course_of(state)["memory"].update(values)
    return mutate


def set_tools(value):
    def mutate(state):
        if value is None:
            course_of(state).pop("tools")
        else:
            course_of(state)["tools"] = value
    return mutate


def context_cases():
    """(label, state, session id, options) for build_context / build_system."""
    cases = []
    for session_id in ("session-functions-1", "session-loops-2"):
        for terminal in TERMINALS:
            cases.append((f"demo {session_id} terminal={terminal!r}", DEMO, session_id,
                          {"learner": CAMILLE, "terminal": terminal}))
        for index, learner in enumerate(LEARNERS):
            cases.append((f"demo {session_id} learner #{index}", DEMO, session_id, {"learner": learner, "terminal": "coach"}))
        cases.append((f"demo {session_id} plan", DEMO, session_id, {"terminal": "silent", "document": PLAN}))
    cases.append(("demo unknown session", DEMO, "inconnue", {}))
    cases.append(("demo session id None", DEMO, None, {}))
    cases.append(("no course", {"version": 1, "activeCourseId": "", "courses": []}, "session-a", {}))
    cases.append(("empty state", {}, "session-a", {}))

    # The built-in Python course with its plan: modules found by their module-N id, long sections cut.
    real = copy.deepcopy(DEFAULT)
    course = real["courses"][0]
    picks = [(0, 0), (0, 3), (1, 0), (2, 1), (9, 0), (10, 0), (12, 0), (13, -1)]
    course["sessions"] = [session(f"session-{module}-{position}", course["modules"][module]["lessons"][position]["id"],
                                  [ask("On commence ?")]) for module, position in picks]
    course["memory"]["reviews"] = [{"item": "print et return", "step": 1, "due": "2026-11-08"},
                                   {"item": "indentation", "step": 7, "due": "2026-11-30"}]
    for item in course["sessions"]:
        for today in (TODAY, date(2026, 11, 8)):
            cases.append((f"default {item['id']} {today}", real, item["id"],
                          {"learner": CAMILLE, "terminal": "coach", "document": PLAN, "today": today}))

    # Synthetic Git and theory courses.
    variants = [
        ("git base", None, {}),
        ("git document", None, {"document": GIT_DOC}),
        ("git document crlf", None, {"document": GIT_DOC.replace("\n", "\r\n")}),
        ("git document bom", None, {"document": "\ufeff" + GIT_DOC}),
        ("git document cr", None, {"document": GIT_DOC.replace("\n", "\r")}),
        ("git document empty", None, {"document": ""}),
        ("git module-2 by id", None, {"session": "session-git-2", "document": GIT_DOC}),
        ("git module by title", None, {"session": "session-git-3", "document": GIT_DOC}),
        ("theory course", None, {"session": "session-theorie", "terminal": "coach", "learner": CAMILLE}),
        ("session of a non-active course", lambda s: s.update(activeCourseId="theorie"), {"session": "session-git-1"}),
        ("missing active course", lambda s: s.update(activeCourseId="absent"), {"session": "session-theorie"}),
        ("active course id None", lambda s: s.update(activeCourseId=None), {}),
        ("duplicate session id: active course wins",
         lambda s: course_of(s, 1)["sessions"].append(session("session-git-1", "g-01", [say("Autre cours.")])), {}),
        ("duplicate session id, other course active",
         lambda s: (course_of(s, 1)["sessions"].append(session("session-git-1", "g-01")),
                    s.update(activeCourseId="theorie")), {}),
        ("lesson not in the plan", lambda s: first_session(s).update(lessonId="absent"), {"document": GIT_DOC}),
        ("lesson id None matches a lesson without id",
         lambda s: (first_session(s).update(lessonId=None), course_of(s)["modules"][0]["lessons"].insert(0, {})),
         {"document": GIT_DOC}),
        ("session id True matches id 1", lambda s: first_session(s).update(id=1), {"session": True}),
        ("last lesson has no following", None, {"session": "session-git-3"}),
        ("lesson done: review session",
         lambda s: course_of(s)["modules"][0]["lessons"][1].update(status="done"), {}),
        ("goals over the limit", lambda s: course_of(s).update(goals=[f"but {n}" for n in range(12)]), {}),
        ("goals of any type", lambda s: course_of(s).update(goals=[None, 5, True, 2.5, ["a"], {"k": "v"}, " x "]), {}),
        ("objectives over the limit",
         lambda s: course_of(s)["modules"][0]["lessons"][1].update(objectives=[f"objectif {n}" for n in range(9)]), {}),
        ("objectives of any type",
         lambda s: course_of(s)["modules"][0]["lessons"][1].update(objectives=[None, 0, "", "  ok  "]), {}),
        ("module brief and title of any type",
         lambda s: course_of(s)["modules"][0].update(brief=5, title=None), {}),
        ("course title missing", lambda s: course_of(s).pop("title"), {}),
        ("course title None", lambda s: course_of(s).update(title=None), {}),
        ("course title 0", lambda s: course_of(s).update(title=0), {}),
        ("contract set", set_contract(CONTRACT), {}),
        ("contract without criteria", set_contract({"objective": "Lire", "criteria": None}), {}),
        ("contract with empty objective", set_contract({"objective": "", "criteria": ["x"]}), {}),
        ("contract empty", set_contract({}), {}),
        ("contract not a dict", set_contract("Lire"), {}),
        ("contract criteria as a string", set_contract({"objective": "Lire", "criteria": "abc"}), {}),
        ("contract criteria as a dict", set_contract({"objective": "Lire", "criteria": {"b": 1, "a": 2}}), {}),
        ("contract criteria of any type",
         set_contract({"objective": "Lire", "criteria": [None, 5, True, 2.5, 1e-7, 0.0001, 1.5e-05, -3, 2 ** 60,
                                                         "x", ["a", 1, None], {"k": "v"}, "it's", 'say "hi"',
                                                         ["it's \"both\"", "tab\tnl\n", "\x00\x7f", "\u200b\xa0é😀"]]}),
         {}),
        ("contract criteria over the limit", set_contract({"objective": "Lire", "criteria": [str(n) for n in range(9)]}), {}),
        ("contract criteria not iterable", set_contract({"objective": "Lire", "criteria": 5}), {}),
        ("contract objective as a list", set_contract({"objective": ["liste", 1], "criteria": []}), {}),
        ("contract objective as a number", set_contract({"objective": 42}), {}),
        ("terminal as a list", None, {"terminal": ["coach"]}),
        ("terminal as a dict", None, {"terminal": {"coach": 1}}),
        ("terminal as a list without terminal", None, {"session": "session-theorie", "terminal": ["coach"]}),
        ("course without tools", set_tools(None), {"terminal": "coach"}),
        ("python course without tools",
         lambda s: (course_of(s).pop("tools"), course_of(s).update(id="python"), s.update(activeCourseId="python")),
         {"terminal": "coach"}),
        ("tools empty", set_tools({}), {"terminal": "coach"}),
        ("tools javascript upper case", set_tools({"terminal": True, "language": "JavaScript", "file": ""}),
         {"terminal": "coach"}),
        ("tools unknown language with terminal", set_tools({"terminal": 1, "language": "rust", "file": None}),
         {"terminal": "silent"}),
        ("tools unknown language without terminal", set_tools({"terminal": 0, "language": "rust"}), {"terminal": "coach"}),
        ("tools language constructor", set_tools({"terminal": True, "language": "constructor"}), {"terminal": "coach"}),
        ("tools language as a number", set_tools({"terminal": True, "language": 5, "file": 7}), {"terminal": "coach"}),
        ("tools python", set_tools({"terminal": True, "language": "python", "file": "main.py"}), {"terminal": "coach"}),
        ("tools autre", set_tools({"terminal": True, "language": "autre", "file": ""}), {"terminal": "coach"}),
        ("tools not a dict", set_tools(["python"]), {"terminal": "coach"}),
        ("no messages", lambda s: first_session(s).update(messages=[]), {}),
        ("only pending and error messages",
         lambda s: first_session(s).update(messages=[ask("a", pending=True), say("b", error=True)]), {}),
        ("falsy pending and error values",
         lambda s: first_session(s).update(messages=[ask("a", pending=0, error={}), say("b", error=[], pending="")]), {}),
        ("one user message", lambda s: first_session(s).update(messages=[ask("Bonjour")]), {}),
        ("tutor message last", lambda s: first_session(s)["messages"].append(say("Vas-y.")), {}),
        ("reviews due, upcoming and odd",
         set_memory(reviews=[{"item": "a", "step": 1, "due": "2026-11-24"}, {"item": "b", "step": 21, "due": "2026-11-23"},
                             {"item": "c", "due": "2026-11-25"}, {"item": "d", "step": 2.5},
                             {"item": None, "step": None, "due": None}, {"item": "f", "due": 5},
                             {"item": "g", "due": "2026-11-24T08:00"}, "pas un dict", ["liste"],
                             {"item": "h", "due": "2027-01-01"}, {"item": "i", "due": "2026-12-24"},
                             {"item": "j", "due": "2026-12-31"}, {"item": "k", "due": "\U0001F600"}]), {}),
        ("reviews today 2026-11-08", None, {"today": date(2026, 11, 8)}),
        ("reviews leap day", None, {"today": date(2024, 2, 29)}),
        ("reviews not a list", set_memory(reviews=None), {}),
        ("facts over the limit", set_memory(facts=[f"fait {n}" for n in range(15)] + [" ", None]), {}),
        ("acquired over the limit and odd", set_memory(acquired=[f"acquis {n}" for n in range(8)], toReview=["", " "]), {}),
        ("acquired as a string", set_memory(acquired="abc", toReview=None), {}),
        ("notes and next focus", set_memory(notes="Penser au café.", nextFocus="  "), {}),
        ("notes as a list, next focus 0", set_memory(notes=["x"], nextFocus=0), {}),
        ("memory missing", lambda s: course_of(s).pop("memory"), {}),
        ("recent sessions sorted by updatedAt",
         lambda s: course_of(s)["sessions"].extend([
             session("s-a", "git-0101", status="completed", updated="2026-11-22", summary="A.", title="A"),
             session("s-b", "git-0101", status="completed", updated="2026-11-22", summary="B.", title="B"),
             session("s-c", "git-0101", status="completed", updated=None, summary="C.", title="C"),
             session("s-d", "git-0101", status="Completed", updated="2027", summary="D.", title="D"),
             session("s-e", "git-0101", status="completed", updated="2027", summary="", title="E"),
             session("s-f", "git-0101", status="completed", updated="\uFFFD", summary="F.", title=None),
             session("s-g", "git-0101", status="completed", updated="\U0001F600", summary="G.", title="G"),
         ]), {}),
        ("recent sessions: missing updatedAt",
         lambda s: [item.pop("updatedAt") for item in course_of(s)["sessions"]], {}),
        ("current session completed is not recent",
         lambda s: first_session(s).update(status="completed", summary="En cours."), {}),
    ]
    for duration in (30, 25.7, "25 min", "environ 45 minutes", "0", 0, -5, "", None, True, False, "٢٠ دقيقة", "１５分",
                     "abc", 10 ** 20, "99999999999999999999 min", 0.5, [20], {"m": 15}, "00012", "𝟒𝟓 minutes"):
        variants.append((f"duration {duration!r}", set_duration(duration), {}))
    for label, mutate, options in variants:
        state, session_id, options = git_variant(mutate, **options)
        cases.append((label, state, session_id, options))

    # Documents: matched by source, module-N id (Unicode digits too) and title; cut when too long.
    documents = [
        ("source normalized to nothing", {"source": "!!!"}, "# Cours\n\n#   \n\nVide.\n\n## ---\n\nTirets.\n"),
        ("Unicode digits", {"id": "module-٣", "source": None}, "## Module ٣٣\n\nNon.\n\n## Module ٣ — Arabe\n\nOui.\n"),
        ("C# heading", {"id": "c", "title": "C", "source": None}, "# Langages\n\n## C#\n\nDièse.\n\n## Python\n"),
        ("tab and trailing hashes", {"source": "Module 2 — Tab"}, "##\tModule 2 — Tab\t##  \nTabulé.\n## Suite\n"),
        ("line separators", {"source": "Module 2 — Branches"},
         "## Module 2 — Branches\x0cAprès un saut de page\x1cpuis\u2028encore\x85fin\n\nTexte.\n## Autre\n"),
        ("indented and unclosed fences", {"source": "Après"},
         "## Avant\n   ```python\n## Après\n   ```\n## Après\n\nVu.\n```\n## Après\n"),
        ("long section with paragraphs", {"source": "Module 2 — Long"}, long_doc("x" * 95, 60)),
        ("long section without break", {"source": "Module 2 — Long"},
         "## Module 2 — Long\n\n" + "y" * 3000 + "\n\n## Fin\n"),
        ("long section with emoji", {"source": "Module 2 — Long"}, long_doc("😀" * 60, 60)),
        ("long section with trailing spaces", {"source": "Module 2 — Long"}, long_doc("z" * 90 + "   ", 60)),
        ("heading levels", {"source": "Niveau 3"},
         "# A\n### Niveau 3\nun\n#### Plus bas\ndeux\n### Même niveau\ntrois\n"),
        ("no match", {"source": "Introuvable"}, GIT_DOC),
    ]
    for label, module_patch, document in documents:
        def mutate(state, patch=module_patch):
            module = course_of(state)["modules"][1]
            module.update(patch)
            for key in [key for key, value in patch.items() if value is None]:
                module.pop(key)
        state, session_id, options = git_variant(mutate, session="session-git-2", document=document)
        cases.append((f"document: {label}", state, session_id, options))
    return cases


def js_options(options):
    today = options.get("today", TODAY)
    return {"learner": options.get("learner"), "today": today.isoformat() if today else None,
            "terminal": options.get("terminal"), "documentText": options.get("document")}


def task_sessions():
    """Sessions for has_open_task, awaits_start and replies_since_quiz."""
    block = "```kenobi-exercice\nfichier: exercice.py\nattendu: 1, 2 et 3\n```"
    verdict = lambda word, more="": say(f"Voilà.{more}\n\n```kenobi-verdict\nrésultat: {word}\n```")  # noqa: E731
    attempt = ask("[Exécution — tentative n° 1 …]", run={"status": "ok", "attempt": 1}, reason="exercise")
    opened = [say("**Écris une boucle**.\n\n" + block)]
    unasked = opened + [ask("Donne-moi la solution."), verdict("reussi", "\n\n" + block)]
    almost = unasked + [attempt, verdict("presque")]
    sessions = {
        "empty": [],
        "opened": opened,
        "verdict without attempt": unasked,
        "almost": almost,
        "passed, same block repeated": almost + [attempt, verdict("Réussi", "\n\n" + block.replace("1, 2 et 3", "1, 2 ET 3"))],
        "passed, next block": almost + [attempt, verdict("reussi", "\n\n" + block.replace("1, 2 et 3", "3, 2 et 1"))],
        "block in an error reply": [say(block, error=True)],
        "block in a pending reply": [say(block, pending=True)],
        "legacy run": [say(block), ask("…", run={"status": "ok"}, reason="exercise")],
        "legacy run with attempt": [say(block), ask("…", run={"status": "ok", "attempt": 2}, reason="exercise")],
        "legacy run empty": [say(block), ask("…", run={}, reason="exercise")],
        "attempt flag only": [say(block), ask("…", attempt=1), verdict("ok")],
        "verdict upper case": [say(block), attempt, say("```kenobi-verdict\nRÉSULTAT : RÉUSSI\n```")],
        "verdict dotless i": [say(block), attempt, say("```kenobı-verdıct\nresultat: reussı\n```")],
        "verdict dotted capital I": [say(block), attempt, say("```KENOBİ-VERDİCT\nRESULTAT: REUSSİ\n```")],
        "verdict long s and kelvin": [say(block), attempt, say("```\u212aenobi-verdict\nréſultat: o\u212a\n```")],
        "verdict reussie": [say(block), attempt, say("```kenobi-verdict\nrésultat: reussie\n```")],
        "verdict reussi.": [say(block), attempt, say("```kenobi-verdict\nrésultat: réussi.\n```")],
        "verdict reussi_": [say(block), attempt, say("```kenobi-verdict\nrésultat: reussi_\n```")],
        "verdict reussié": [say(block), attempt, say("```kenobi-verdict\nrésultat: reussié\n```")],
        "verdict with spaces and CRLF": [say(block), attempt, say("```kenobi-verdict  \r\n  resultat :  ok\r\n```")],
        "verdict on the same line": [say(block), attempt, say("```kenobi-verdict résultat: reussi```")],
        "exercices is not a block": [say("```kenobi-exercices\nattendu: 3\n```")],
        "exerciceé is not a block": [say("```kenobi-exerciceé\nattendu: 3\n```")],
        "block without expected": [say("```kenobi-exercice\nfichier: a.py\n```")],
        "block not closed": [say("Voilà.\n```kenobi-exercice\nattendu: 3")],
        "expected upper case": [say("```kenobi-exercice\nATTENDU : 3\n```"), attempt, verdict("reussi", "\n\n```kenobi-exercice\nattendu:3\n```")],
        "expected after CR only": [say("```kenobi-exercice\nfichier: x\rattendu: 3\n```")],
        "expected with CR": [say("```kenobi-exercice\nattendu: 1, 2\r\n```"), attempt,
                             verdict("reussi", "\n\n```kenobi-exercice\nattendu: 1,  2\n```")],
        "expected with accents": [say("```kenobi-exercice\nattendu: Créer\n```"), attempt,
                                  verdict("reussi", "\n\n```kenobi-exercice\nattendu: CRE\u0301ER\n```")],
        "expected with dotted I": [say("```kenobi-exercice\nattendu: İci\n```"), attempt,
                                   verdict("reussi", "\n\n```kenobi-exercice\nattendu: ici\n```")],
        "roles": [{"role": "assistant", "content": block}, {"role": "KENOBI", "content": "Bien."},
                  {"role": None, "content": "?"}, {"content": "sans rôle"}],
        "course given": [ask("Commençons."), say(COURSE_REPLY)],
        "course given, then error": [ask("Commençons."), say(COURSE_REPLY), say("Kenobi n'a pas pu répondre", error=True)],
        "course given, then exercise": [say(COURSE_REPLY), say(EXERCISE)],
        "ready variants": [say("``` \tkenobi_prêt\nbouton: Go\n```")],
        "ready english": [say("```kenobi-ready\n```")],
        "ready plural": [say("```kenobi-prets\n```")],
        "ready upper case": [say("```KENOBI-PRET\n```")],
        "ready dotless i": [say("```kenobı-pret\n```")],
        "quiz then replies": [say(QUIZ), say("Bien."), say("Encore.")],
        "quiz variants": [say("```kenobi_quiz\n```"), say("Bien.")],
        "quiz dotless i": [say("```kenobi-quız\n```"), say("Bien.")],
        "quiz suffix": [say("```kenobi-qcmx\n```"), say("Bien.")],
        "quiz only user": [ask(QUIZ), say("Bien.")],
        "quiz in error": [say(QUIZ), say(QUIZ, error=True), say("Bien.")],
    }
    return sessions


def note_sessions():
    return {
        "empty": {"messages": []},
        "no messages key": {},
        "no contract, course given": {"messages": [ask("Commençons."), say(COURSE_REPLY)]},
        "contract with empty objective": {"contract": {"objective": ""}, "messages": []},
        "contract not a dict": {"contract": "Lire", "messages": []},
        "fresh contract": {"contract": CONTRACT, "messages": []},
        "awaits start": {"contract": CONTRACT, "messages": [ask("Commençons."), say(COURSE_REPLY)]},
        "open task": {"contract": CONTRACT, "messages": [say(EXERCISE)]},
        "quiz recent": {"contract": CONTRACT, "messages": [say("Cours."), say(QUIZ)]},
        "quiz due": {"contract": CONTRACT, "messages": [say(QUIZ), say("Bien."), say("Encore.")]},
        "quiz due, open task": {"contract": CONTRACT, "messages": [say(EXERCISE), say("Bien."), say("Encore.")]},
    }


NOTE_MESSAGES = ["On commence", "[Exécution — tentative n° 2 pour la consigne en cours.]",
                 "[QCM — il a choisi « 1 » : ce n'est pas la bonne réponse, qui était « 2 ».]", "C’est parti !",
                 "Une question", "[Appel vocal] je bloque", " [Exécution — tentative", "[QCM"]
NOTE_TOOLS = [{"terminal": True, "language": "python", "file": "exercice.py"}, {"terminal": False, "language": "", "file": ""},
              {"terminal": 1, "language": "shell", "file": "exercice.sh"}]


def history_cases():
    """(label, session, message) for build_history."""
    long = [{"role": "user" if index % 2 == 0 else "tutor", "content": f"message {index} " + "x" * 9000}
            for index in range(100)]
    cases = [
        ("demo session, new message", DEMO["courses"][0]["sessions"][0], "Autre chose"),
        ("demo session, saved message again", DEMO["courses"][0]["sessions"][0],
         DEMO["courses"][0]["sessions"][0]["messages"][-1]["content"]),
        ("long history", {"messages": long + [say("Kenobi n'a pas pu répondre", error=True)]}, "suite"),
        ("character budget", {"messages": [ask("a" * 20000), say("b" * 20000)] * 5}, "c" * 1000),
        ("emoji are counted as characters", {"messages": [ask("😀" * 13000), say("😀" * 25000), ask("é" * 30000)]},
         "😀 suite"),
        ("emoji message near the budget", {"messages": [ask("a" * 50000), say("😀" * 45000)]}, "😀" * 10000),
        ("empty contents still count as positions",
         {"messages": [ask("y" * 5000)] + [ask("  "), say(""), ask("\n"), say(None), ask("\x1c")] * 2}, "z"),
        ("roles", {"messages": [{"role": "Tutor", "content": "1"}, {"role": "KENOBI", "content": "2"},
                                {"role": "system", "content": "3"}, {"role": None, "content": "4"},
                                {"role": 5, "content": "5"}, {"content": "6"}, {"role": "assistant", "content": "7"}]},
         "8"),
        ("pending and error values", {"messages": [ask("a", pending=0), say("b", error={}), ask("c", error=[]),
                                                   say("d", pending="oui"), ask("e", error=True), say("f", pending=1.5)]},
         "g"),
        ("saved message with spaces", {"messages": [say("Salut."), ask("  Bonjour  ")]}, "Bonjour"),
        ("unstripped message", {"messages": [say("Salut."), ask("Bonjour")]}, "  Bonjour \n"),
        ("same text from the tutor", {"messages": [ask("Salut."), say("Bonjour")]}, "Bonjour"),
        ("leading tutor replies and merged users", {"messages": [say("A"), say("B"), ask("C"), ask("D"), say("E")]}, "F"),
        ("contents of any type", {"messages": [ask(42), say(None), ask(["a"]), say(True), ask({"k": 1}), say(0)]}, "z"),
        ("Python whitespace", {"messages": [ask("\x1cBonjour\x1c"), say("\ufeffSalut\ufeff"), ask("\x85x\xa0")]},
         "\x1fsuite\u3000"),
        ("no messages", {}, "Bonjour"),
        ("only the message", {"messages": [ask("Bonjour")]}, "Bonjour"),
    ]
    return cases


@unittest.skipUnless(NODE, "node absent")
class ParityTest(unittest.TestCase):
    maxDiff = 6000

    def assertSame(self, labels, expected, calls):
        actual = js_many(calls, timeout=300)
        self.assertEqual(len(actual), len(expected))
        for label, want, got in zip(labels, expected, actual):
            with self.subTest(label):
                self.assertEqual(got, want)
        return len(labels)


class PromptParityTests(ParityTest):
    def test_context_and_system_prompt(self):
        labels, expected, calls = [], [], []
        for label, state, session_id, options in context_cases():
            for name, function in (("buildContext", tutor.build_context), ("buildSystem", tutor.build_system)):
                labels.append(f"{name}: {label}")
                expected.append(py_prompt(function, state, session_id, learner=options.get("learner"),
                                          today=options.get("today", TODAY), terminal=options.get("terminal"),
                                          document=options.get("document")))
                calls.append(call(name, state, session_id, js_options(options)))
        self.assertGreater(self.assertSame(labels, expected, calls), 300)

    def test_the_bundled_plan_is_read_like_the_data_file(self):
        state = copy.deepcopy(DEFAULT)
        course = state["courses"][0]
        course["sessions"] = [session("session-a", course["modules"][0]["lessons"][0]["id"])]
        tutor._plan_cache.clear()
        expected = tutor.build_system(state, "session-a", learner=CAMILLE, data_dir=DATA, today=TODAY, terminal="coach")
        self.assertIn("Module 0 — Les bases de Python", expected)
        got = js(HARNESS, "call", "buildSystem", [state, "session-a", {"learner": CAMILLE, "today": TODAY.isoformat(),
                                                                         "terminal": "coach", "documentText": PLAN}])
        self.assertEqual(got, {"ok": True, "value": expected})

    def test_today_defaults_to_the_local_date(self):
        before = date.today()
        expected = tutor.build_context(DEMO, "session-functions-1")
        got = js(HARNESS, "call", "buildContext", [DEMO, "session-functions-1", {}])
        if before != date.today():
            self.skipTest("midnight passed during the test")
        self.assertEqual(got, {"ok": True, "value": expected})

    def test_find_session_course_of_and_course_tools(self):
        labels, expected, calls = [], [], []
        states = [("demo", DEMO), ("synthetic", synthetic()), ("default", DEFAULT), ("empty", {}),
                  ("no courses", {"courses": []})]
        for state_label, state in states:
            for course_id in (None, "python", "git", "theorie", "absent"):
                labels.append(f"courseOf {state_label} {course_id}")
                expected.append(outcome(tutor.course_of, state, course_id))
                calls.append(call("courseOf", state, course_id))
            for session_id in ("session-functions-1", "session-loops-2", "session-git-1", "session-theorie", "absent", None):
                labels.append(f"findSession {state_label} {session_id}")
                expected.append(outcome(tutor.find_session, state, session_id))
                calls.append(call("findSession", state, session_id))
            for course in state.get("courses") or []:
                labels.append(f"courseTools {state_label} {course.get('id')}")
                expected.append(outcome(tutor.course_tools, course))
                calls.append(call("courseTools", course))
        for tools in ({}, {"terminal": True}, {"language": "PYTHON", "file": "a.py"}, {"terminal": "", "language": "shell"},
                      {"terminal": [1], "language": None, "file": 0}, {"terminal": True, "language": "toString"}):
            labels.append(f"courseTools {tools}")
            expected.append(outcome(tutor.course_tools, {"id": "x", "tools": tools}))
            calls.append(call("courseTools", {"id": "x", "tools": tools}))
        self.assertSame(labels, expected, calls)

    def test_plan_excerpt_and_headings(self):
        labels, expected, calls = [], [], []
        documents = {"plan": PLAN, "git": GIT_DOC, "git crlf": GIT_DOC.replace("\n", "\r\n"), "empty": "",
                     "long": long_doc("😀" * 60, 60), "separators": "# a\x0cb\x1d# c\u2029## d\n\n#\te #\n"}
        modules = [{"id": f"module-{n}", "title": "x"} for n in (0, 1, 2, 3, 6, 10, 13, 21)] + [
            {"source": "Module 1 — Premiers commits"}, {"id": "git-m03", "title": "Fusionner : les conflits !"},
            {"source": "Module 2 — Long"}, {"title": "Module 0 — Les bases de Python"}, {}, None,
            {"id": "x", "title": "Évaluation"}, {"source": "!!!"}, {"title": ""}]
        with tempfile.TemporaryDirectory() as folder:
            for doc_label, document in documents.items():
                (Path(folder) / "c").mkdir(exist_ok=True)
                path = Path(folder) / "c" / "COURSE.md"
                path.write_bytes(document.encode("utf-8"))
                tutor._plan_cache.clear()
                lines, headings = tutor._headings(path)
                labels += [f"splitLines {doc_label}", f"headings {doc_label}"]
                expected += [{"ok": True, "value": lines}, {"ok": True, "value": plain(headings)}]
                calls += [call("internals.splitLines", document), call("internals.headings", document)]
                for module in modules:
                    tutor._plan_cache.clear()
                    labels.append(f"planExcerpt {doc_label} {module}")
                    expected.append(outcome(tutor.plan_excerpt, {"id": "c"}, module, runtime_dir=folder))
                    calls.append(call("planExcerpt", {"id": "c"}, module, document))
        labels.append("planExcerpt without document")
        expected.append(outcome(tutor.plan_excerpt, {"id": "absent"}, {"id": "module-0"}, runtime_dir="/nonexistent"))
        calls.append(call("planExcerpt", {"id": "absent"}, {"id": "module-0"}, None))
        self.assertSame(labels, expected, calls)


class HistoryParityTests(ParityTest):
    def test_build_history(self):
        labels, expected, calls = [], [], []
        for label, session_value, message in history_cases():
            labels.append(label)
            expected.append(outcome(tutor.build_history, session_value, message))
            calls.append(call("buildHistory", session_value, message))
        self.assertSame(labels, expected, calls)

    def test_normalize_messages_matches_providers(self):
        cases = [[], [{"role": "assistant", "content": "a"}], [{"role": "user", "content": "  a "}, {"role": "user", "content": "b"}],
                 [{"role": "assistant", "content": "x"}, {"role": "user", "content": ""}, {"role": "tutor", "content": "y"},
                  {"role": "assistant", "content": "z"}, {"role": "assistant", "content": "w"}, {"content": 5}]]
        labels = [f"normalize #{index}" for index in range(len(cases))]
        expected = [outcome(providers.normalize_messages, messages) for messages in cases]
        self.assertSame(labels, expected, [call("normalizeMessages", messages) for messages in cases])


class NoteParityTests(ParityTest):
    def test_turn_note_and_phone_note(self):
        labels, expected, calls = [], [], []
        for session_label, session_value in note_sessions().items():
            labels.append(f"phoneNote {session_label}")
            expected.append(outcome(tutor.phone_note, session_value))
            calls.append(call("phoneNote", session_value))
            for message in NOTE_MESSAGES:
                for tools in NOTE_TOOLS:
                    for voice in (False, True):
                        labels.append(f"turnNote {session_label} | {message} | {tools['language']} | voice={voice}")
                        expected.append(outcome(tutor.turn_note, session_value, message, tools, voice))
                        calls.append(call("turnNote", session_value, message, tools, voice))
        self.assertGreater(self.assertSame(labels, expected, calls), 400)

    def test_open_task_awaited_start_and_quiz_count(self):
        labels, expected, calls = [], [], []
        for label, messages in task_sessions().items():
            value = {"contract": CONTRACT, "messages": messages}
            for name, function in (("hasOpenTask", tutor.has_open_task), ("awaitsStart", tutor.awaits_start),
                                   ("repliesSinceQuiz", tutor.replies_since_quiz)):
                labels.append(f"{name}: {label}")
                expected.append(outcome(function, value))
                calls.append(call(name, value))
        self.assertSame(labels, expected, calls)


class HelperParityTests(ParityTest):
    def test_french_date(self):
        days = [date(2026, 1, 1) + timedelta(days=offset) for offset in range(366)]
        days += [date(1, 1, 1), date(1900, 2, 28), date(2000, 2, 29), date(2024, 2, 29), date(9999, 12, 31)]
        self.assertSame([day.isoformat() for day in days], [{"ok": True, "value": tutor._french_date(day)} for day in days],
                        [call("internals.frenchDate", day.isoformat()) for day in days])

    def test_minutes_norm_task_key_and_str(self):
        labels, expected, calls = [], [], []
        for value in (20, 25.0, 25.7, "20 min", "environ 45 minutes", 0, 0.5, -3, "", None, True, False, "٢٠", "１５分",
                      "𝟒𝟓", "0012", "abc", [7], {"m": 9}):
            labels.append(f"minutes {value!r}")
            expected.append(outcome(tutor._minutes, value))
            calls.append(call("internals.minutes", value))
        texts = ["Module 1 — Premiers commits", "MODULE 1 : premiers COMMITS !", "Évaluation", "Œuvre", "straße", "İstanbul",
                 "ΟΔΟΣ", "Cre\u0301er", "a\xa0b\x1cc", "😀 x", "", None, 0, 5, True, ["a"], "ǅemal", "ﬁn"]
        for text in texts:
            for name, function in (("norm", tutor._norm), ("taskKey", tutor._task_key)):
                labels.append(f"{name} {text!r}")
                expected.append(outcome(function, text))
                calls.append(call(f"internals.{name}", text))
        values = [None, True, False, 0, -3, 2.5, 0.1, 1e-05, 1.5e-07, 123456.789, 0.0001, 0.0001234, 5e-324, 2 ** 60,
                  10 ** 20, "x", [], {}, [1, "a", None, True, 2.5], {"a": [1, {"b": None}]}, ["it's"], ['say "hi"'],
                  ["both ' and \""], ["tab\t nl\n cr\r back\\"], ["\x00\x1f\x7f\x80\x9f\xa0\xad"],
                  ["é\u200b\u2028\u3000\ue000😀\U000e0001\U0010ffff"]]
        for value in values:
            labels.append(f"str {value!r}")
            expected.append({"ok": True, "value": str(value)})
            calls.append(call("internals.str", value))
        for text in ["", " \t\n\x0b\x0c\r\x1c\x1d\x1e\x1f\x85\xa0\u1680\u2000\u200a\u2028\u2029\u202f\u205f\u3000x\ufeff \u200b",
                     "\ufeffy\ufeff"]:
            labels.append(f"strip {text!r}")
            expected.append({"ok": True, "value": text.strip()})
            calls.append(call("internals.strip", text))
        for text, limit in (("a" * 5000, 4000), ("😀" * 5000, 4000), ("😀" * 3000, 4000), ("b" * 100, 50)):
            labels.append(f"clip {text[:3]!r}×{len(text)} {limit}")
            expected.append(outcome(tutor._clip, text, limit))
            calls.append(call("internals.clip", text, limit))
        self.assertSame(labels, expected, calls)


def stream_cases():
    """(label, state, payload, options, script) for stream_reply and chat."""
    real = copy.deepcopy(DEFAULT)
    course = real["courses"][0]
    course["sessions"] = [session("session-a", course["modules"][0]["lessons"][0]["id"],
                                  [ask("On commence ?"), say("Oui.\n\n```kenobi-lecon\nobjectif: Lire\n```"),
                                   ask("Voici ma réponse")]),
                          session("session-b", course["modules"][0]["lessons"][1]["id"],
                                  [ask("Bonjour"), say(COURSE_REPLY)], contract=CONTRACT)]
    git = synthetic()
    git["courses"][0]["sessions"][0]["contract"] = CONTRACT
    base = {"ai": AI, "learner": CAMILLE, "today": TODAY.isoformat()}
    reply = {"deltas": ["Bien", " vu."]}
    cases = [
        ("normal turn", real, {"sessionId": "session-a", "message": "Voici ma réponse", "terminal": "coach"}, {}, reply),
        ("normal turn with the plan", real, {"sessionId": "session-a", "message": "Et ensuite ?", "terminal": "silent"},
         {"documentText": PLAN}, reply),
        ("voice turn", real, {"sessionId": "session-a", "message": "[Appel vocal] c'est quoi une variable"},
         {"voice": True}, reply),
        ("voice turn without name", real, {"sessionId": "session-b", "message": "[Appel vocal] je suis prêt"},
         {"voice": True, "learner": {"name": "  ", "about": ""}}, reply),
        ("phone turn, lesson start", real, {"sessionId": "session-a", "message": "Salut Kenobi"}, {"phone": True}, reply),
        ("phone turn with contract", real, {"sessionId": "session-b", "message": "On continue ?"},
         {"phone": True, "voice": True, "learner": {"name": "$& Léa", "about": ""}}, reply),
        ("attempt in the git course", git, {"sessionId": "session-git-1",
                                            "message": "[Exécution — tentative n° 1 pour la consigne en cours.]\n$ git commit",
                                            "terminal": "coach"}, {"documentText": GIT_DOC}, reply),
        ("theory course", git, {"sessionId": "session-theorie", "message": "  Une question  ", "terminal": "closed"}, {}, reply),
        ("done payload merged", real, {"sessionId": "session-a", "message": "Merci"}, {},
         {"deltas": ["ok"], "done": {"elapsed": 1.25, "firstToken": 0.5, "truncated": True, "model": "m", "mode": "x"}}),
        ("emoji message under the limit", real, {"sessionId": "session-a", "message": "😀" * 64001}, {}, reply),
        ("message at the limit", real, {"sessionId": "session-a", "message": "a" * tutor.MAX_MESSAGE}, {}, reply),
        ("emoji reply under the limit", real, {"sessionId": "session-a", "message": "Vas-y"}, {}, {"deltas": ["😀" * 12001]}),
        ("reply too long", real, {"sessionId": "session-a", "message": "Vas-y"}, {}, {"deltas": ["a" * 20000, "b" * 4001, "c"]}),
        ("empty message", real, {"sessionId": "session-a", "message": "  \x1c "}, {}, reply),
        ("message None", real, {"sessionId": "session-a", "message": None}, {}, reply),
        ("message as a number", real, {"sessionId": "session-a", "message": 42}, {}, reply),
        ("message too long", real, {"sessionId": "session-a", "message": "a" * (tutor.MAX_MESSAGE + 1)}, {}, reply),
        ("bad session id", real, {"sessionId": "x", "message": "a"}, {}, reply),
        ("session id with a space", real, {"sessionId": "session a", "message": "a"}, {}, reply),
        ("session id too long", real, {"sessionId": "s" * 121, "message": "a"}, {}, reply),
        ("session id missing", real, {"message": "a"}, {}, reply),
        ("unknown session", real, {"sessionId": "inconnue", "message": "a"}, {}, reply),
        ("no course", {"version": 1, "activeCourseId": "", "courses": []}, {"sessionId": "session-a", "message": "a"}, {}, reply),
        ("unconfigured model", real, {"sessionId": "inconnue", "message": "a"}, {"ai": UNCONFIGURED}, reply),
        ("key refused", real, {"sessionId": "session-a", "message": "a"}, {},
         {"error": [502, "Clé refusée par Service de test : vérifie-la dans les réglages.", "auth"]}),
        ("rate limit after a delta", real, {"sessionId": "session-a", "message": "a"}, {},
         {"deltas": ["Je "], "error": [503, "Limite ou crédit atteint chez Service de test : réessaie dans un instant.", "rate"]}),
        ("cancelled", real, {"sessionId": "session-a", "message": "a"}, {},
         {"deltas": ["Je "], "error": [499, "Réponse interrompue.", "cancelled"]}),
        ("blank reply", real, {"sessionId": "session-a", "message": "a"}, {}, {"deltas": ["  ", "\n"]}),
    ]
    return [(label, state, payload, {**base, **options}, script) for label, state, payload, options, script in cases]


class ReplyParityTests(ParityTest):
    def test_stream_reply_sends_the_same_call_and_emits_the_same_events(self):
        labels, expected, calls = [], [], []
        for label, state, payload, options, script in stream_cases():
            labels.append(label)
            expected.append(py_run("stream", state, payload, options, script))
            calls.append((HARNESS, "streamReply", [state, payload, options, script]))
        self.assertSame(labels, expected, calls)
        sent = [item for item in expected if item["calls"]]
        self.assertGreater(len(sent), 10)
        self.assertEqual({item["calls"][0]["options"]["maxTokens"] for item in sent}, {1200, 4000})

    def test_chat_returns_the_whole_reply(self):
        labels, expected, calls = [], [], []
        for label, state, payload, options, script in stream_cases():
            labels.append(label)
            expected.append(py_run("chat", state, payload, options, script))
            calls.append((HARNESS, "chat", [state, payload, options, script]))
        self.assertSame(labels, expected, calls)

    def test_connection_test(self):
        cases = [("ok", AI, {"deltas": ["  ok  "]}), ("long sample", AI, {"deltas": ["😀" * 30 + "x" * 30]}),
                 ("refused", AI, {"error": [502, "Clé refusée par Service de test : vérifie-la dans les réglages.", "auth"]}),
                 ("timing missing", AI, {"deltas": ["ok"], "done": {"model": "m"}}), ("unconfigured", UNCONFIGURED, {})]
        self.assertSame([label for label, _ai, _script in cases],
                        [py_test_connection(ai, script) for _label, ai, script in cases],
                        [(HARNESS, "testConnection", [ai, script]) for _label, ai, script in cases])

    def test_status_keeps_the_last_error_like_python(self):
        real = copy.deepcopy(DEFAULT)
        real["courses"][0]["sessions"] = [session("session-a", real["courses"][0]["modules"][0]["lessons"][0]["id"])]
        options = {"ai": AI, "learner": CAMILLE, "today": TODAY.isoformat()}
        payload = {"sessionId": "session-a", "message": "Salut"}
        refused = {"error": [502, "Clé refusée par Service de test : vérifie-la dans les réglages.", "auth"]}
        steps = [
            {"status": UNCONFIGURED}, {"status": AI},
            {"stream": [real, payload, options, refused]}, {"status": AI}, {"status": UNCONFIGURED},
            {"stream": [real, payload, options, {"error": [499, "Réponse interrompue.", "cancelled"]}]}, {"status": AI},
            {"stream": [real, payload, options, {"deltas": ["ok"]}]}, {"status": AI},
            {"test": [AI, {"error": [503, "Service de test est injoignable (refus).", "unreachable"]}]}, {"status": AI},
            {"test": [AI, {"deltas": ["ok"]}]}, {"status": AI},
            {"stream": [real, payload, options, {"deltas": ["a" * 24001]}]}, {"status": AI},
        ]
        expected = []
        tutor._remember(None)
        for step in steps:
            if "status" in step:
                expected.append(plain(tutor.status(FakeSettings(step["status"]))))
            elif "stream" in step:
                expected.append(py_run("stream", *step["stream"])["error"])
            else:
                expected.append(py_test_connection(*step["test"])["error"])
        tutor._remember(None)
        self.assertEqual(js(HARNESS, "statusScenario", steps), expected)


if __name__ == "__main__":
    unittest.main()
