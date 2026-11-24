"""Validation de l'état Kenobi, plan du parcours livré avec l'application et génération des mémoires de cours."""

import copy
import json
import re


COURSE_ID_RE = re.compile(r"[a-z0-9-]+")
LESSON_STATUSES = {"done", "current", "todo", "review"}

# Anciens identifiants des leçons du parcours Python livré avec l'application (leçons renommées le 2026-11-14) :
# une leçon renommée garde sa progression et ses séances.
FORMER_LESSON_IDS = {
    "mimo-basics": "m0-premiers-pas",
    "mimo-types": "m0-types",
    "mimo-conditions": "m0-conditions",
    "mimo-loops": "m0-boucles",
    "mimo-lists": "m0-listes",
    "mimo-list-ops": "m0-listes-operations",
    "mimo-strings": "m0-chaines",
    "mimo-functions": "m0-fonctions",
    "mimo-collections": "m0-collections",
    "mimo-comprehensions": "m0-comprehensions",
    "mimo-classes": "m0-classes",
    "mimo-oop": "m0-heritage",
    "mimo-modules": "m0-modules",
    "mimo-errors": "m0-exceptions",
    "mimo-diagnostic": "m0-projet-nombre",
    "m8-api-rest": "m10-api-web",
}
PLAN_COURSE_FIELDS = ("title", "shortTitle", "description", "level", "goals")
PLAN_MODULE_FIELDS = ("id", "title", "description", "brief", "source")
PLAN_LESSON_FIELDS = ("id", "title", "duration", "objectives")


class StateValidationError(ValueError):
    """État JSON non conforme au contrat Kenobi."""


def _mapping(value, path):
    if not isinstance(value, dict):
        raise StateValidationError(f"{path} doit être un objet.")
    return value


def _string(value, path, *, nonempty=False):
    if not isinstance(value, str) or (nonempty and not value.strip()):
        suffix = " non vide" if nonempty else ""
        raise StateValidationError(f"{path} doit être une chaîne{suffix}.")
    return value


def _duration(value, path):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise StateValidationError(f"{path} doit être une chaîne ou un nombre.")
    return value


def _string_list(value, path):
    if not isinstance(value, list):
        raise StateValidationError(f"{path} doit être un tableau.")
    for index, item in enumerate(value):
        _string(item, f"{path}[{index}]")
    return value


def _required_string(mapping, key, path, *, nonempty=False):
    if key not in mapping:
        raise StateValidationError(f"{path}.{key} est requis.")
    return _string(mapping[key], f"{path}.{key}", nonempty=nonempty)


def validate_state(state):
    """Valide l'état complet et le renvoie sans le modifier."""
    _mapping(state, "state")
    if type(state.get("version")) is not int or state["version"] != 1:
        raise StateValidationError("state.version doit être égal à 1.")
    active_course_id = _required_string(state, "activeCourseId", "state")
    courses = state.get("courses")
    if not isinstance(courses, list):
        raise StateValidationError("state.courses doit être un tableau.")
    if "updatedAt" in state:
        _string(state["updatedAt"], "state.updatedAt")

    course_ids = []
    for course_index, course in enumerate(courses):
        path = f"state.courses[{course_index}]"
        _mapping(course, path)
        course_id = _required_string(course, "id", path, nonempty=True)
        if not COURSE_ID_RE.fullmatch(course_id):
            raise StateValidationError(
                f"{path}.id ne peut contenir que les caractères a-z, 0-9 et -."
            )
        course_ids.append(course_id)
        for key in ("title", "description"):
            _required_string(course, key, path)
        if "updatedAt" in course:
            _string(course["updatedAt"], f"{path}.updatedAt")
        if "goals" in course:
            _string_list(course["goals"], f"{path}.goals")
        if "tools" in course:
            tools = _mapping(course["tools"], f"{path}.tools")
            for key in ("language", "file"):
                if key in tools:
                    _string(tools[key], f"{path}.tools.{key}")

        modules = course.get("modules")
        if not isinstance(modules, list):
            raise StateValidationError(f"{path}.modules doit être un tableau.")
        for module_index, module in enumerate(modules):
            module_path = f"{path}.modules[{module_index}]"
            _mapping(module, module_path)
            _required_string(module, "id", module_path, nonempty=True)
            for key in ("title", "description"):
                _required_string(module, key, module_path)
            for key in ("brief", "source"):
                if key in module:
                    _string(module[key], f"{module_path}.{key}")
            lessons = module.get("lessons")
            if not isinstance(lessons, list):
                raise StateValidationError(f"{module_path}.lessons doit être un tableau.")
            for lesson_index, lesson in enumerate(lessons):
                lesson_path = f"{module_path}.lessons[{lesson_index}]"
                _mapping(lesson, lesson_path)
                _required_string(lesson, "id", lesson_path, nonempty=True)
                _required_string(lesson, "title", lesson_path)
                if "objectives" in lesson:
                    _string_list(lesson["objectives"], f"{lesson_path}.objectives")
                if "duration" not in lesson:
                    raise StateValidationError(f"{lesson_path}.duration est requis.")
                _duration(lesson["duration"], f"{lesson_path}.duration")
                status = _required_string(lesson, "status", lesson_path)
                if status not in LESSON_STATUSES:
                    allowed = ", ".join(sorted(LESSON_STATUSES))
                    raise StateValidationError(
                        f"{lesson_path}.status doit valoir : {allowed}."
                    )

        sessions = course.get("sessions")
        if not isinstance(sessions, list):
            raise StateValidationError(f"{path}.sessions doit être un tableau.")
        for session_index, session in enumerate(sessions):
            session_path = f"{path}.sessions[{session_index}]"
            _mapping(session, session_path)
            _required_string(session, "id", session_path, nonempty=True)
            for key in (
                "title",
                "startedAt",
                "updatedAt",
                "status",
                "lessonId",
                "summary",
            ):
                _required_string(session, key, session_path)
            if "duration" not in session:
                raise StateValidationError(f"{session_path}.duration est requis.")
            _duration(session["duration"], f"{session_path}.duration")
            messages = session.get("messages")
            if not isinstance(messages, list):
                raise StateValidationError(f"{session_path}.messages doit être un tableau.")
            for message_index, message in enumerate(messages):
                message_path = f"{session_path}.messages[{message_index}]"
                _mapping(message, message_path)
                _required_string(message, "id", message_path, nonempty=True)
                for key in ("role", "createdAt", "content"):
                    _required_string(message, key, message_path)

        memory = _mapping(course.get("memory"), f"{path}.memory")
        if "lastSessionId" not in memory:
            raise StateValidationError(f"{path}.memory.lastSessionId est requis.")
        last_session_id = memory["lastSessionId"]
        if last_session_id is not None:
            _string(last_session_id, f"{path}.memory.lastSessionId")
        for key in ("acquired", "toReview"):
            if key not in memory:
                raise StateValidationError(f"{path}.memory.{key} est requis.")
            _string_list(memory[key], f"{path}.memory.{key}")
        for key in ("nextFocus", "notes"):
            _required_string(memory, key, f"{path}.memory")
        if "facts" in memory:
            _string_list(memory["facts"], f"{path}.memory.facts")
        if "reviews" in memory:
            reviews = memory["reviews"]
            if not isinstance(reviews, list):
                raise StateValidationError(f"{path}.memory.reviews doit être un tableau.")
            for review_index, review in enumerate(reviews):
                review_path = f"{path}.memory.reviews[{review_index}]"
                _mapping(review, review_path)
                _required_string(review, "item", review_path, nonempty=True)
                _required_string(review, "due", review_path, nonempty=True)

    if len(course_ids) != len(set(course_ids)):
        raise StateValidationError("Les identifiants de cours doivent être uniques.")
    if active_course_id not in course_ids and (courses or active_course_id):
        raise StateValidationError("state.activeCourseId doit désigner un cours existant.")
    return state


def sync_builtin_plan(state, default_state):
    """Le plan d'un cours livré avec l'application suit data/default-state.json ; la progression reste celle de
    l'état. Un cours de l'état est concerné s'il a le même identifiant et au moins une leçon connue (ou un ancien
    identifiant) : titres, durées, objectifs et ordre viennent de l'application, statuts et séances de l'apprenant.
    Une page restée ouverte sur un ancien plan ne peut donc pas le réenregistrer. Renvoie True si l'état a changé."""
    changed = False
    references = {course.get("id"): course for course in (default_state or {}).get("courses") or [] if isinstance(course, dict)}
    for course in state.get("courses") or []:
        reference = copy.deepcopy(references.get(course.get("id")))
        if not reference:
            continue
        plan_ids = {lesson["id"] for _, lesson in _lessons(reference)}
        moved = {old: new for old, new in FORMER_LESSON_IDS.items() if new in plan_ids}
        current = [lesson for _, lesson in _lessons(course)]
        if not any(lesson.get("id") in plan_ids or lesson.get("id") in moved for lesson in current):
            continue  # un cours importé qui porte le même identifiant
        done = {moved.get(lesson.get("id"), lesson.get("id")) for lesson in current if lesson.get("status") == "done"}
        modules = []
        for module in reference.get("modules") or []:
            lessons = [{**{key: lesson[key] for key in PLAN_LESSON_FIELDS if key in lesson},
                        "status": "done" if lesson["id"] in done else "todo"} for lesson in module.get("lessons") or []]
            modules.append({**{key: module[key] for key in PLAN_MODULE_FIELDS if key in module}, "lessons": lessons})
        first = next((lesson for module in modules for lesson in module["lessons"] if lesson["status"] != "done"), None)
        if first:
            first["status"] = "current"
        titles = {lesson["id"]: lesson["title"] for module in modules for lesson in module["lessons"]}
        for session in course.get("sessions") or []:
            new_id = moved.get(session.get("lessonId"))
            if new_id:
                session["lessonId"] = new_id
                session["title"] = titles[new_id]
                changed = True
        if course.get("modules") != modules:
            course["modules"] = modules
            changed = True
        for key in PLAN_COURSE_FIELDS:
            if key in reference and course.get(key) != reference[key]:
                course[key] = reference[key]
                changed = True
    return changed


def _lessons(course):
    for module in course["modules"]:
        for lesson in module["lessons"]:
            yield module, lesson


def _yaml_string(value):
    # Les chaînes JSON entre guillemets doubles sont aussi des scalaires YAML.
    return json.dumps(value, ensure_ascii=False)


def _list_section(lines, values, empty_label):
    if values:
        lines.extend(f"- {value}" for value in values)
    else:
        lines.append(f"- {empty_label}")


def render_course_memory(state, course):
    """Produit un MEMORY.md français à partir des seules données du cours."""
    lessons = list(_lessons(course))
    done_count = sum(lesson["status"] == "done" for _, lesson in lessons)
    percent = round(done_count * 100 / len(lessons)) if lessons else 0
    next_lesson = next(
        (lesson for _, lesson in lessons if lesson["status"] == "current"),
        None,
    )
    if next_lesson is None:
        next_lesson = next(
            (lesson for _, lesson in lessons if lesson["status"] == "todo"),
            None,
        )

    updated_at = course.get("updatedAt") or state.get("updatedAt") or ""
    memory = course["memory"]
    lines = [
        "---",
        "type: apprentissage",
        f"cours: {_yaml_string(course['title'])}",
        f"mis_a_jour: {_yaml_string(updated_at)}",
        "---",
        "",
        f"# Mémoire — {course['title']}",
        "",
    ]
    goals = [goal for goal in course.get("goals") or [] if goal]
    if goals:
        lines.extend(["## Objectifs du cours", ""])
        lines.extend(f"- {goal}" for goal in goals)
        lines.append("")
    lines += [
        "## Progression",
        "",
        f"{done_count} / {len(lessons)} leçons terminées ({percent} %).",
        "",
        "## Prochaine leçon",
        "",
        next_lesson["title"] if next_lesson else "Aucune leçon à venir.",
        "",
        "## Points acquis",
        "",
    ]
    _list_section(lines, memory["acquired"], "Aucun point acquis enregistré.")
    lines.extend(["", "## Points à revoir", ""])
    _list_section(lines, memory["toReview"], "Aucun point à revoir enregistré.")
    reviews = sorted(memory.get("reviews") or [], key=lambda review: str(review.get("due", "")))
    lines.extend(["", "## Révisions prévues", ""])
    _list_section(
        lines,
        [f"{review['due']} — {review['item']} (J+{review.get('step', 1)})" for review in reviews],
        "Aucune révision prévue.",
    )
    lines.extend(["", "## Ce que Kenobi a retenu", ""])
    _list_section(lines, memory.get("facts") or [], "Rien de noté pour l'instant.")
    lines.extend(
        [
            "",
            "## Prochaine étape",
            "",
            memory["nextFocus"] or "Aucune prochaine étape enregistrée.",
            "",
            "## Notes",
            "",
            memory["notes"] or "Aucune note enregistrée.",
            "",
            "## Sessions récentes",
            "",
        ]
    )

    sessions = sorted(
        course["sessions"], key=lambda session: session["updatedAt"], reverse=True
    )
    if not sessions:
        lines.append("Aucune session enregistrée.")
    for session in sessions:
        lines.extend(
            [
                f"### {session['title']}",
                "",
                f"Mis à jour : {session['updatedAt']}",
                "",
                session["summary"] or "Aucun résumé enregistré.",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"
