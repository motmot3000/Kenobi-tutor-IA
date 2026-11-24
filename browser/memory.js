// Port of memory.py for the browser version: state validation, the plan of the course shipped with the
// application, and the course memories (MEMORY.md). memory.py stays the reference: same messages, same results.
(function (root, factory) {
  const api = factory(root);
  if (typeof module === "object" && module.exports) module.exports = api;
  root.KenobiMemory = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function (root) {
  "use strict";

  const Shared = typeof module === "object" && module.exports ? require("./shared.js") : root.KenobiShared;
  const { FORMER_LESSON_IDS, PLAN_COURSE_FIELDS, PLAN_MODULE_FIELDS, PLAN_LESSON_FIELDS } = Shared.memory;
  const LESSON_STATUSES = Shared.memory.LESSON_STATUSES;
  // memory.COURSE_ID_RE is used with fullmatch: anchored here.
  const COURSE_ID_RE = /^[a-z0-9-]+$/;

  class StateValidationError extends Error {
    constructor(message) {
      super(message);
      this.name = "StateValidationError";
    }
  }

  // ── Python semantics on JSON values ─────────────────────────────────────────
  // sync_builtin_plan also runs on hand-written states: where Python would fail (KeyError, TypeError,
  // AttributeError, which server.py ignores), the same error name is thrown at the same point.

  class PythonError extends Error {
    constructor(name, message) {
      super(message);
      this.name = name;
    }
  }

  const hasOwn = (object, key) => Object.prototype.hasOwnProperty.call(object, key);
  const isDict = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
  const isNone = (value) => value === null || value === undefined;

  function typeName(value) {
    if (isNone(value)) return "NoneType";
    if (typeof value === "boolean") return "bool";
    if (typeof value === "number") return Number.isInteger(value) ? "int" : "float";
    if (typeof value === "string") return "str";
    return Array.isArray(value) ? "list" : "dict";
  }

  function truthy(value) {
    if (isNone(value)) return false;
    if (typeof value === "number") return value !== 0; // NaN is true in Python
    if (typeof value === "string" || Array.isArray(value)) return value.length > 0;
    if (typeof value === "object") return Object.keys(value).length > 0;
    return Boolean(value);
  }

  const orList = (value) => (truthy(value) ? value : []);

  // mapping.get(key, fallback)
  function get(mapping, key, fallback) {
    if (!isDict(mapping)) throw new PythonError("AttributeError", `'${typeName(mapping)}' object has no attribute 'get'`);
    return hasOwn(mapping, key) ? mapping[key] : fallback;
  }

  // container[key] with a string key
  function item(container, key) {
    if (isDict(container)) {
      if (hasOwn(container, key)) return container[key];
      throw new PythonError("KeyError", repr(key));
    }
    if (Array.isArray(container)) throw new PythonError("TypeError", "list indices must be integers or slices, not str");
    if (typeof container === "string") throw new PythonError("TypeError", "string indices must be integers, not 'str'");
    throw new PythonError("TypeError", `'${typeName(container)}' object is not subscriptable`);
  }

  // key in container
  function contains(container, key) {
    if (isDict(container)) return hasOwn(container, key);
    if (Array.isArray(container)) return container.some((value) => equal(value, key));
    if (typeof container === "string") return container.includes(key);
    throw new PythonError("TypeError", `argument of type '${typeName(container)}' is not iterable`);
  }

  // for value in container
  function iterate(container) {
    if (Array.isArray(container)) return container;
    if (typeof container === "string") return Array.from(container);
    if (isDict(container)) return Object.keys(container);
    throw new PythonError("TypeError", `'${typeName(container)}' object is not iterable`);
  }

  // Key of a Python dict or set: 1, 1.0 and True are the same key, lists and dicts cannot be keys.
  function hashKey(value) {
    if (isNone(value)) return "None";
    if (typeof value === "string") return `s:${value}`;
    if (typeof value === "boolean") return `n:${value ? 1 : 0}`;
    if (typeof value === "number") return Number.isNaN(value) ? {} : `n:${value === 0 ? 0 : value}`;
    throw new PythonError("TypeError", `unhashable type: '${typeName(value)}'`);
  }

  // ==, for JSON values
  function equal(a, b) {
    if (isNone(a) || isNone(b)) return isNone(a) && isNone(b);
    const numeric = (value) => typeof value === "number" || typeof value === "boolean";
    if (numeric(a) && numeric(b)) return Number(a) === Number(b);
    if (Array.isArray(a)) return Array.isArray(b) && a.length === b.length && a.every((value, index) => equal(value, b[index]));
    if (isDict(a)) {
      if (!isDict(b)) return false;
      const keys = Object.keys(a);
      return keys.length === Object.keys(b).length && keys.every((key) => hasOwn(b, key) && equal(a[key], b[key]));
    }
    return a === b;
  }

  function deepCopy(value) {
    if (Array.isArray(value)) return value.map(deepCopy);
    if (isDict(value)) return Object.fromEntries(Object.keys(value).map((key) => [key, deepCopy(value[key])]));
    return value;
  }

  function floatRepr(number) {
    if (Number.isNaN(number)) return "nan";
    if (!Number.isFinite(number)) return number > 0 ? "inf" : "-inf";
    if (number === 0) return Object.is(number, -0) ? "-0.0" : "0.0";
    const [mantissa, exponent] = Math.abs(number).toExponential().split("e");
    const digits = mantissa.replace(".", "");
    const point = Number(exponent) + 1;
    let text;
    if (point > 16 || point <= -4) {
      const power = point - 1;
      text = `${digits[0]}${digits.length > 1 ? `.${digits.slice(1)}` : ""}e${power < 0 ? "-" : "+"}${String(Math.abs(power)).padStart(2, "0")}`;
    } else if (point <= 0) {
      text = `0.${"0".repeat(-point)}${digits}`;
    } else if (point >= digits.length) {
      text = `${digits}${"0".repeat(point - digits.length)}.0`;
    } else {
      text = `${digits.slice(0, point)}.${digits.slice(point)}`;
    }
    return number < 0 ? `-${text}` : text;
  }

  // JavaScript has one number type: a number is printed as Python prints it after reading JSON.stringify's text
  // (an int below 1e21, a float otherwise).
  const numberText = (number) => (Number.isInteger(number) && Math.abs(number) < 1e21 ? String(number) : floatRepr(number));

  const NOT_PRINTABLE = /[\p{Cc}\p{Cf}\p{Cs}\p{Co}\p{Cn}\p{Zl}\p{Zp}\p{Zs}]/u;

  function reprText(text) {
    const quote = text.includes("'") && !text.includes('"') ? '"' : "'";
    let out = quote;
    for (const character of text) {
      const code = character.codePointAt(0);
      if (character === quote || character === "\\") out += `\\${character}`;
      else if (character === "\t") out += "\\t";
      else if (character === "\n") out += "\\n";
      else if (character === "\r") out += "\\r";
      else if (code < 0x20 || code === 0x7f) out += `\\x${code.toString(16).padStart(2, "0")}`;
      else if (code < 0x7f || !NOT_PRINTABLE.test(character)) out += character;
      else if (code <= 0xff) out += `\\x${code.toString(16).padStart(2, "0")}`;
      else if (code <= 0xffff) out += `\\u${code.toString(16).padStart(4, "0")}`;
      else out += `\\U${code.toString(16).padStart(8, "0")}`;
    }
    return out + quote;
  }

  function repr(value) {
    if (isNone(value)) return "None";
    if (typeof value === "boolean") return value ? "True" : "False";
    if (typeof value === "number") return numberText(value);
    if (typeof value === "string") return reprText(value);
    if (Array.isArray(value)) return `[${value.map(repr).join(", ")}]`;
    return `{${Object.keys(value).map((key) => `${reprText(key)}: ${repr(value[key])}`).join(", ")}}`;
  }

  // str(value), as in an f-string
  const str = (value) => (typeof value === "string" ? value : repr(value));

  const JSON_ESCAPES = { '"': '\\"', "\\": "\\\\", "\n": "\\n", "\r": "\\r", "\t": "\\t", "\b": "\\b", "\f": "\\f" };

  // json.dumps(value, ensure_ascii=False)
  function jsonDumps(value) {
    if (typeof value === "string") {
      return `"${value.replace(/[\x00-\x1f"\\]/g, (character) =>
        JSON_ESCAPES[character] || `\\u${character.charCodeAt(0).toString(16).padStart(4, "0")}`)}"`;
    }
    if (isNone(value)) return "null";
    if (typeof value === "boolean") return value ? "true" : "false";
    if (typeof value === "number") {
      if (Number.isNaN(value)) return "NaN";
      if (!Number.isFinite(value)) return value > 0 ? "Infinity" : "-Infinity";
      return numberText(value);
    }
    if (Array.isArray(value)) return `[${value.map(jsonDumps).join(", ")}]`;
    return `{${Object.keys(value).map((key) => `${jsonDumps(key)}: ${jsonDumps(value[key])}`).join(", ")}}`;
  }

  const isSpace = (code) => (code >= 0x09 && code <= 0x0d) || (code >= 0x1c && code <= 0x20) || code === 0x85 ||
    code === 0xa0 || code === 0x1680 || (code >= 0x2000 && code <= 0x200a) || code === 0x2028 || code === 0x2029 ||
    code === 0x202f || code === 0x205f || code === 0x3000;

  function strip(text) {
    let start = 0;
    let end = text.length;
    while (start < end && isSpace(text.charCodeAt(start))) start += 1;
    while (end > start && isSpace(text.charCodeAt(end - 1))) end -= 1;
    return text.slice(start, end);
  }

  function rstrip(text) {
    let end = text.length;
    while (end > 0 && isSpace(text.charCodeAt(end - 1))) end -= 1;
    return text.slice(0, end);
  }

  // Python compares strings by code point; JavaScript's < compares UTF-16 units.
  function compareText(a, b) {
    if (!/[\ud800-\udfff]/.test(a + b)) return a < b ? -1 : a > b ? 1 : 0;
    const left = Array.from(a, (character) => character.codePointAt(0));
    const right = Array.from(b, (character) => character.codePointAt(0));
    for (let index = 0; index < Math.min(left.length, right.length); index += 1) {
      if (left[index] !== right[index]) return left[index] - right[index];
    }
    return left.length - right.length;
  }

  // sorted(values, key=key, reverse=reverse): stable, equal keys keep their order.
  function sortedBy(values, key, reverse = false) {
    const entries = Array.from(iterate(values), (value, index) => ({ value, index, key: key(value) }));
    entries.sort((a, b) => (reverse ? compareText(b.key, a.key) : compareText(a.key, b.key)) || a.index - b.index);
    return entries.map((entry) => entry.value);
  }

  // round(): halves go to the even neighbour.
  function round(number) {
    const floor = Math.floor(number);
    const rest = number - floor;
    if (rest !== 0.5) return rest > 0.5 ? floor + 1 : floor;
    return floor % 2 === 0 ? floor : floor + 1;
  }

  // ── Validation ──────────────────────────────────────────────────────────────

  function fail(message) {
    throw new StateValidationError(message);
  }

  function mapping(value, path) {
    if (!isDict(value)) fail(`${path} doit être un objet.`);
    return value;
  }

  function string(value, path, nonempty = false) {
    if (typeof value !== "string" || (nonempty && !strip(value))) {
      fail(`${path} doit être une chaîne${nonempty ? " non vide" : ""}.`);
    }
    return value;
  }

  function duration(value, path) {
    if (typeof value !== "string" && typeof value !== "number") fail(`${path} doit être une chaîne ou un nombre.`);
    return value;
  }

  function stringList(value, path) {
    if (!Array.isArray(value)) fail(`${path} doit être un tableau.`);
    for (let index = 0; index < value.length; index += 1) string(value[index], `${path}[${index}]`);
    return value;
  }

  function requiredString(object, key, path, nonempty = false) {
    if (!hasOwn(object, key)) fail(`${path}.${key} est requis.`);
    return string(object[key], `${path}.${key}`, nonempty);
  }

  /** Validates the whole state and returns it unchanged; throws StateValidationError. */
  function validateState(state) {
    mapping(state, "state");
    if (get(state, "version") !== 1) fail("state.version doit être égal à 1.");
    const activeCourseId = requiredString(state, "activeCourseId", "state");
    const courses = get(state, "courses");
    if (!Array.isArray(courses)) fail("state.courses doit être un tableau.");
    if (hasOwn(state, "updatedAt")) string(state.updatedAt, "state.updatedAt");

    const courseIds = [];
    courses.forEach((course, courseIndex) => {
      const path = `state.courses[${courseIndex}]`;
      mapping(course, path);
      const courseId = requiredString(course, "id", path, true);
      if (!COURSE_ID_RE.test(courseId)) fail(`${path}.id ne peut contenir que les caractères a-z, 0-9 et -.`);
      courseIds.push(courseId);
      for (const key of ["title", "description"]) requiredString(course, key, path);
      if (hasOwn(course, "updatedAt")) string(course.updatedAt, `${path}.updatedAt`);
      if (hasOwn(course, "goals")) stringList(course.goals, `${path}.goals`);
      if (hasOwn(course, "tools")) {
        const tools = mapping(course.tools, `${path}.tools`);
        for (const key of ["language", "file"]) {
          if (hasOwn(tools, key)) string(tools[key], `${path}.tools.${key}`);
        }
      }

      const modules = get(course, "modules");
      if (!Array.isArray(modules)) fail(`${path}.modules doit être un tableau.`);
      modules.forEach((module, moduleIndex) => {
        const modulePath = `${path}.modules[${moduleIndex}]`;
        mapping(module, modulePath);
        requiredString(module, "id", modulePath, true);
        for (const key of ["title", "description"]) requiredString(module, key, modulePath);
        for (const key of ["brief", "source"]) {
          if (hasOwn(module, key)) string(module[key], `${modulePath}.${key}`);
        }
        const lessons = get(module, "lessons");
        if (!Array.isArray(lessons)) fail(`${modulePath}.lessons doit être un tableau.`);
        lessons.forEach((lesson, lessonIndex) => {
          const lessonPath = `${modulePath}.lessons[${lessonIndex}]`;
          mapping(lesson, lessonPath);
          requiredString(lesson, "id", lessonPath, true);
          requiredString(lesson, "title", lessonPath);
          if (hasOwn(lesson, "objectives")) stringList(lesson.objectives, `${lessonPath}.objectives`);
          if (!hasOwn(lesson, "duration")) fail(`${lessonPath}.duration est requis.`);
          duration(lesson.duration, `${lessonPath}.duration`);
          const status = requiredString(lesson, "status", lessonPath);
          if (!LESSON_STATUSES.includes(status)) {
            fail(`${lessonPath}.status doit valoir : ${[...LESSON_STATUSES].sort().join(", ")}.`);
          }
        });
      });

      const sessions = get(course, "sessions");
      if (!Array.isArray(sessions)) fail(`${path}.sessions doit être un tableau.`);
      sessions.forEach((session, sessionIndex) => {
        const sessionPath = `${path}.sessions[${sessionIndex}]`;
        mapping(session, sessionPath);
        requiredString(session, "id", sessionPath, true);
        for (const key of ["title", "startedAt", "updatedAt", "status", "lessonId", "summary"]) {
          requiredString(session, key, sessionPath);
        }
        if (!hasOwn(session, "duration")) fail(`${sessionPath}.duration est requis.`);
        duration(session.duration, `${sessionPath}.duration`);
        const messages = get(session, "messages");
        if (!Array.isArray(messages)) fail(`${sessionPath}.messages doit être un tableau.`);
        messages.forEach((message, messageIndex) => {
          const messagePath = `${sessionPath}.messages[${messageIndex}]`;
          mapping(message, messagePath);
          requiredString(message, "id", messagePath, true);
          for (const key of ["role", "createdAt", "content"]) requiredString(message, key, messagePath);
        });
      });

      const memory = mapping(get(course, "memory"), `${path}.memory`);
      if (!hasOwn(memory, "lastSessionId")) fail(`${path}.memory.lastSessionId est requis.`);
      if (!isNone(memory.lastSessionId)) string(memory.lastSessionId, `${path}.memory.lastSessionId`);
      for (const key of ["acquired", "toReview"]) {
        if (!hasOwn(memory, key)) fail(`${path}.memory.${key} est requis.`);
        stringList(memory[key], `${path}.memory.${key}`);
      }
      for (const key of ["nextFocus", "notes"]) requiredString(memory, key, `${path}.memory`);
      if (hasOwn(memory, "facts")) stringList(memory.facts, `${path}.memory.facts`);
      if (hasOwn(memory, "reviews")) {
        const reviews = memory.reviews;
        if (!Array.isArray(reviews)) fail(`${path}.memory.reviews doit être un tableau.`);
        reviews.forEach((review, reviewIndex) => {
          const reviewPath = `${path}.memory.reviews[${reviewIndex}]`;
          mapping(review, reviewPath);
          requiredString(review, "item", reviewPath, true);
          requiredString(review, "due", reviewPath, true);
        });
      }
    });

    if (new Set(courseIds).size !== courseIds.length) fail("Les identifiants de cours doivent être uniques.");
    if (!courseIds.includes(activeCourseId) && (courses.length > 0 || activeCourseId !== "")) {
      fail("state.activeCourseId doit désigner un cours existant.");
    }
    return state;
  }

  // ── Plan of the course shipped with the application ─────────────────────────

  function* lessonsOf(course) {
    for (const module of iterate(item(course, "modules"))) {
      for (const lesson of iterate(item(module, "lessons"))) yield [module, lesson];
    }
  }

  /** The plan of a shipped course follows data/default-state.json, the learner keeps progress and sessions (see
   * memory.sync_builtin_plan). Mutates `state`; returns true if it changed. */
  function syncBuiltinPlan(state, defaultState) {
    let changed = false;
    const references = new Map();
    for (const course of iterate(orList(get(truthy(defaultState) ? defaultState : {}, "courses")))) {
      if (isDict(course)) references.set(hashKey(get(course, "id")), course);
    }
    for (const course of iterate(orList(get(state, "courses")))) {
      const key = hashKey(get(course, "id"));
      const reference = deepCopy(references.has(key) ? references.get(key) : null);
      if (!truthy(reference)) continue;
      const planIds = new Set();
      for (const [, lesson] of lessonsOf(reference)) planIds.add(hashKey(item(lesson, "id")));
      const moved = new Map();
      for (const [former, renamed] of Object.entries(FORMER_LESSON_IDS)) {
        if (planIds.has(hashKey(renamed))) moved.set(hashKey(former), renamed);
      }
      const current = Array.from(lessonsOf(course), ([, lesson]) => lesson);
      const known = current.some((lesson) =>
        planIds.has(hashKey(get(lesson, "id"))) || moved.has(hashKey(get(lesson, "id"))));
      if (!known) continue; // an imported course that has the same id
      const done = new Set();
      for (const lesson of current) {
        if (get(lesson, "status") !== "done") continue;
        const id = get(lesson, "id");
        const idKey = hashKey(id);
        done.add(hashKey(moved.has(idKey) ? moved.get(idKey) : id));
      }
      const modules = [];
      for (const module of iterate(orList(get(reference, "modules")))) {
        const lessons = [];
        for (const lesson of iterate(orList(get(module, "lessons")))) {
          const entry = {};
          for (const field of PLAN_LESSON_FIELDS) if (contains(lesson, field)) entry[field] = item(lesson, field);
          entry.status = done.has(hashKey(item(lesson, "id"))) ? "done" : "todo";
          lessons.push(entry);
        }
        const entry = {};
        for (const field of PLAN_MODULE_FIELDS) if (contains(module, field)) entry[field] = item(module, field);
        entry.lessons = lessons;
        modules.push(entry);
      }
      const first = modules.flatMap((module) => module.lessons).find((lesson) => lesson.status !== "done");
      if (first) first.status = "current";
      const titles = new Map();
      for (const module of modules) {
        for (const lesson of module.lessons) {
          const id = item(lesson, "id");
          const title = item(lesson, "title");
          titles.set(hashKey(id), title);
        }
      }
      for (const session of iterate(orList(get(course, "sessions")))) {
        const lessonKey = hashKey(get(session, "lessonId"));
        const renamed = moved.has(lessonKey) ? moved.get(lessonKey) : null;
        if (!truthy(renamed)) continue;
        session.lessonId = renamed;
        if (!titles.has(hashKey(renamed))) throw new PythonError("KeyError", repr(renamed));
        session.title = titles.get(hashKey(renamed));
        changed = true;
      }
      if (!equal(get(course, "modules"), modules)) {
        course.modules = modules;
        changed = true;
      }
      for (const field of PLAN_COURSE_FIELDS) {
        if (hasOwn(reference, field) && !equal(get(course, field), reference[field])) {
          course[field] = reference[field];
          changed = true;
        }
      }
    }
    return changed;
  }

  // ── MEMORY.md ───────────────────────────────────────────────────────────────

  function listSection(lines, values, emptyLabel) {
    if (truthy(values)) {
      for (const value of iterate(values)) lines.push(`- ${str(value)}`);
    } else {
      lines.push(`- ${emptyLabel}`);
    }
  }

  const orText = (value, fallback) => (truthy(value) ? value : fallback);

  /** French MEMORY.md of a course, from the course data only (state accepted by validateState). */
  function renderCourseMemory(state, course) {
    const lessons = Array.from(lessonsOf(course), ([, lesson]) => lesson);
    const doneCount = lessons.filter((lesson) => item(lesson, "status") === "done").length;
    const percent = lessons.length ? round((doneCount * 100) / lessons.length) : 0;
    const nextLesson = lessons.find((lesson) => item(lesson, "status") === "current") ||
      lessons.find((lesson) => item(lesson, "status") === "todo") || null;

    let updatedAt = get(course, "updatedAt");
    if (!truthy(updatedAt)) updatedAt = orText(get(state, "updatedAt"), "");
    const memory = item(course, "memory");
    const title = item(course, "title");
    const lines = ["---", "type: apprentissage", `cours: ${jsonDumps(title)}`, `mis_a_jour: ${jsonDumps(updatedAt)}`,
      "---", "", `# Mémoire — ${str(title)}`, ""];
    const goals = iterate(orList(get(course, "goals"))).filter(truthy);
    if (goals.length) {
      lines.push("## Objectifs du cours", "");
      for (const goal of goals) lines.push(`- ${str(goal)}`);
      lines.push("");
    }
    lines.push("## Progression", "", `${doneCount} / ${lessons.length} leçons terminées (${percent} %).`, "",
      "## Prochaine leçon", "", nextLesson ? str(item(nextLesson, "title")) : "Aucune leçon à venir.", "",
      "## Points acquis", "");
    listSection(lines, item(memory, "acquired"), "Aucun point acquis enregistré.");
    lines.push("", "## Points à revoir", "");
    listSection(lines, item(memory, "toReview"), "Aucun point à revoir enregistré.");
    const reviews = sortedBy(orList(get(memory, "reviews")), (review) => str(get(review, "due", "")));
    lines.push("", "## Révisions prévues", "");
    listSection(lines, reviews.map((review) =>
      `${str(item(review, "due"))} — ${str(item(review, "item"))} (J+${str(get(review, "step", 1))})`),
    "Aucune révision prévue.");
    lines.push("", "## Ce que Kenobi a retenu", "");
    listSection(lines, orList(get(memory, "facts")), "Rien de noté pour l'instant.");
    lines.push("", "## Prochaine étape", "", str(orText(item(memory, "nextFocus"), "Aucune prochaine étape enregistrée.")),
      "", "## Notes", "", str(orText(item(memory, "notes"), "Aucune note enregistrée.")), "", "## Sessions récentes", "");

    const sessions = sortedBy(item(course, "sessions"), (session) => item(session, "updatedAt"), true);
    if (!sessions.length) lines.push("Aucune session enregistrée.");
    for (const session of sessions) {
      lines.push(`### ${str(item(session, "title"))}`, "", `Mis à jour : ${str(item(session, "updatedAt"))}`, "",
        str(orText(item(session, "summary"), "Aucun résumé enregistré.")), "");
    }
    return `${rstrip(lines.join("\n"))}\n`;
  }

  return Object.freeze({
    COURSE_ID_RE,
    LESSON_STATUSES,
    FORMER_LESSON_IDS,
    PLAN_COURSE_FIELDS,
    PLAN_MODULE_FIELDS,
    PLAN_LESSON_FIELDS,
    StateValidationError,
    validateState,
    syncBuiltinPlan,
    renderCourseMemory
  });
});
