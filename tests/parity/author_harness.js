// Harness for tests/test_parity_authoring.py: writing a course with a stub provider (no network).
"use strict";

const Courses = require("../../browser/courses.js");

class ProviderError extends Error {
  constructor(status, message, code = "") {
    super(message);
    this.name = "ProviderError";
    this.status = status;
    this.code = code;
  }
}

function stubProviders(script, calls) {
  return {
    ProviderError,
    async *streamChat(config, system, messages, options = {}) {
      calls.push({ system, messages, maxTokens: options.maxTokens, temperature: options.temperature, timeout: options.timeout });
      for (const chunk of script.chunks || []) yield ["delta", chunk];
      if (script.error) throw new ProviderError(script.error.status, script.error.message, script.error.code || "");
      yield ["done", { elapsed: 0.5, firstToken: 0.1, truncated: Boolean(script.truncated), model: "stub" }];
    }
  };
}

async function writeCourse(wish, ai, script) {
  const calls = [];
  const events = [];
  try {
    for await (const event of Courses.writeCourse(wish, ai, { providers: stubProviders(script, calls) })) events.push(event);
    return { events, calls };
  } catch (error) {
    return { error: [error.name, error.message], events, calls };
  }
}

function checkWish(wish) {
  try {
    return { value: Courses.checkWish(wish) };
  } catch (error) {
    return { error: [error.name, error.message] };
  }
}

module.exports = { writeCourse, checkWish };
