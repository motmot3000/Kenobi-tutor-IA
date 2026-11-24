// JavaScript-only behaviour of browser/courses.js (parity with courses.py: tests/test_parity_courses.py).
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const Courses = require("../browser/courses.js");

const DOCUMENT = "# Git : les bases\n\nUn cours court pour versionner son code.\n\n## Module 1 — Premiers commits\n\n" +
  "### Leçon 1 — Créer un dépôt\n\n- Objectif : initialiser avec `git init`.\n\n## Module 2 — Les branches\n\n- Créer une branche\n";
const ANSWER = JSON.stringify({ title: "Git", modules: [{ title: "Bases", lessons: [{ title: "Dépôt", duration: 25 }] }] });
const RAW = { title: "Git", modules: [{ title: "M", source: "Les branches", lessons: ["L"] }] };

function providerError(status, message, code) {
  const error = new Error(message);
  error.name = "ProviderError";
  Object.assign(error, { status, code });
  return error;
}

test("loads as a classic script, without require", () => {
  const context = vm.createContext({});
  for (const file of ["shared.js", "courses.js"]) {
    vm.runInContext(fs.readFileSync(path.join(__dirname, "..", "browser", file), "utf8"), context);
  }
  assert.equal(typeof context.KenobiCourses.importCourse, "function");
  assert.equal(context.KenobiCourses.slugify("Écrire une fonction"), "ecrire-une-fonction");
});

test("the AbortSignal reaches the provider and a cancelled analysis is thrown again", async () => {
  const controller = new AbortController();
  let options = null;
  const providers = {
    async *streamChat(config, system, messages, received) {
      options = received;
      yield ["delta", "{"];
      controller.abort();
      throw providerError(499, "Réponse interrompue.", "cancelled");
    }
  };
  await assert.rejects(
    Courses.importCourse(DOCUMENT, "git.md", { ai: { configured: true }, providers, signal: controller.signal }),
    { name: "ProviderError", message: "Réponse interrompue." });
  assert.equal(options.signal, controller.signal);
  assert.deepEqual([options.maxTokens, options.temperature, options.timeout], [16000, 0.2, 300000]);
});

test("providers default to globalThis.KenobiProviders, and are not needed without a model", async () => {
  const [course, notice] = await Courses.importCourse(DOCUMENT, "git.md", {});
  assert.equal(course.source.analysis, "titres");
  assert.match(notice, /^Aucun modèle d'IA branché/);
  const seen = [];
  globalThis.KenobiProviders = {
    async *streamChat(config) {
      seen.push(config.model);
      yield ["delta", ANSWER];
      yield ["done", {}];
    }
  };
  try {
    const [imported, quiet] = await Courses.importCourse(DOCUMENT, "git.md", { ai: { configured: true, model: "m" } });
    assert.deepEqual([imported.source.analysis, quiet, seen], ["ia", "", ["m"]]);
  } finally {
    delete globalThis.KenobiProviders;
  }
});

test("a provider failure is recognised by its class as well as by its name", async () => {
  class ProviderError extends Error {}
  const providers = {
    ProviderError,
    async *streamChat() {
      yield* [];
      throw new ProviderError("Clé refusée.");
    }
  };
  const [course, notice] = await Courses.importCourse(DOCUMENT, "git.md", { ai: { configured: true }, providers });
  assert.equal(course.source.analysis, "titres");
  assert.equal(notice, "Analyse par le modèle impossible (Clé refusée.) : structure tirée des titres du document.");
});

test("dates are written like Python's isoformat", () => {
  const at = (milliseconds) => new Date(Date.UTC(2026, 10, 24, 9, 30, 0, milliseconds));
  assert.equal(Courses.buildCourse(RAW, { now: at(123) }).createdAt, "2026-11-24T09:30:00.123000Z");
  assert.equal(Courses.buildCourse(RAW, { now: at(0) }).source.importedAt, "2026-11-24T09:30:00Z");
  assert.match(Courses.buildCourse(RAW).createdAt, /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d{6})?Z$/);
  assert.throws(() => Courses.buildCourse(RAW, { now: "24/11/2026" }), { name: "ValueError" });
});

test("existing ids and headings may be Sets", () => {
  const course = Courses.buildCourse(RAW, { existingIds: new Set(["git"]), headings: new Set(["Les branches"]) });
  assert.equal(course.id, "git-2");
  assert.equal(course.modules[0].source, "Les branches");
});

test("errors are Error subclasses with a name", () => {
  assert.throws(() => Courses.checkDocument("court"), (error) =>
    error instanceof Courses.CourseError && error instanceof Error && error.name === "CourseError");
});

test("integers beyond 2^53 keep their exact value", () => {
  const raw = Courses.parseAnalysis('{"n": 12345678901234567890123, "small": 3}');
  assert.equal(raw.n, 12345678901234567890123n);
  assert.equal(raw.small, 3);
});
