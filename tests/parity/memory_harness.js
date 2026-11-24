// Harness for tests/test_parity_memory.py: syncBuiltinPlan changes the state it is given, so the state is returned
// with the result (also when it throws halfway, like memory.sync_builtin_plan).
"use strict";

const Memory = require("../../browser/memory.js");

function syncPlan(state, defaultState) {
  try {
    return { changed: Memory.syncBuiltinPlan(state, defaultState), state };
  } catch (error) {
    return { error: [error.name, error.message], state };
  }
}

// What server.py does before writing the state (_serialize, _write_locked): validate, sync, validate, MEMORY.md.
function pipeline(state, defaultState) {
  try {
    Memory.validateState(state);
    const changed = Memory.syncBuiltinPlan(state, defaultState);
    Memory.validateState(state);
    return { changed, state, memories: state.courses.map((course) => Memory.renderCourseMemory(state, course)) };
  } catch (error) {
    return { error: [error.name, error.message] };
  }
}

module.exports = { syncPlan, pipeline };
