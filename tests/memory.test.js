// JavaScript-only behaviour of browser/memory.js (parity with memory.py: tests/test_parity_memory.py).
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const Memory = require("../browser/memory.js");

const DATA = path.join(__dirname, "..", "data");
const readDefault = () => JSON.parse(fs.readFileSync(path.join(DATA, "default-state.json"), "utf8"));

test("loads as a classic script, without require", () => {
  const context = vm.createContext({});
  for (const file of ["shared.js", "memory.js"]) {
    vm.runInContext(fs.readFileSync(path.join(__dirname, "..", "browser", file), "utf8"), context);
  }
  const state = readDefault();
  assert.equal(context.KenobiMemory.validateState(state), state);
});

test("validation returns the same object and throws StateValidationError", () => {
  const state = readDefault();
  assert.equal(Memory.validateState(state), state);
  assert.throws(() => Memory.validateState({ version: 1, activeCourseId: "x", courses: [] }), (error) =>
    error instanceof Memory.StateValidationError && error.name === "StateValidationError" &&
    error.message === "state.activeCourseId doit désigner un cours existant.");
});

test("the synchronised plan shares nothing with the default state", () => {
  const defaults = readDefault();
  const snapshot = JSON.stringify(defaults);
  const state = readDefault();
  const course = state.courses[0];
  course.modules[0].lessons[0].id = "mimo-basics";
  course.modules[0].lessons[0].status = "done";
  assert.equal(Memory.syncBuiltinPlan(state, defaults), true);
  course.modules[0].lessons[0].objectives.push("changed");
  course.goals.push("changed");
  assert.equal(JSON.stringify(defaults), snapshot);
  assert.equal(Memory.syncBuiltinPlan(state, defaults), true);
  assert.equal(Memory.syncBuiltinPlan(state, defaults), false);
});

test("course ids follow COURSE_ID_RE", () => {
  assert.ok(Memory.COURSE_ID_RE.test("python-3"));
  assert.ok(!Memory.COURSE_ID_RE.test("python\n"));
  assert.ok(!Memory.COURSE_ID_RE.test("Python"));
});
