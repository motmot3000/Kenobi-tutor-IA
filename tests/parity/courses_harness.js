// Harness for tests/test_parity_courses.py: a stub provider replays a scripted model answer; the calls it receives,
// the progress reported and the result (or the error) come back together. No network.
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

// script: {chunks: [text…], error: {status, message, code} (ProviderError after the chunks), runtime: "message"}
function stubProviders(script, calls) {
  return {
    ProviderError,
    async *streamChat(config, system, messages, options = {}) {
      calls.push({ config, system, messages, maxTokens: options.maxTokens, temperature: options.temperature,
        timeout: options.timeout });
      for (const chunk of script.chunks || []) yield ["delta", chunk];
      if (script.runtime) {
        const error = new Error(script.runtime);
        error.name = "RuntimeError";
        throw error;
      }
      if (script.error) throw new ProviderError(script.error.status, script.error.message, script.error.code || "");
      yield ["done", { elapsed: 0.1, firstToken: 0.05, truncated: false, model: "stub" }];
    }
  };
}

const failure = (error) => [error.name, error.message];

async function importCourse(markdown, filename, script, options = {}) {
  const calls = [];
  const progress = [];
  try {
    const [course, notice] = await Courses.importCourse(markdown, filename, {
      ...options, providers: stubProviders(script, calls), onProgress: (received) => progress.push(received)
    });
    return { course, notice, calls, progress };
  } catch (error) {
    return { error: failure(error), calls, progress };
  }
}

async function analyse(markdown, filename, ai, script) {
  const calls = [];
  const progress = [];
  try {
    const raw = await Courses.analyse(markdown, filename, ai, {
      providers: stubProviders(script, calls), onProgress: (received) => progress.push(received)
    });
    return { repr: Courses._internal.repr(raw), calls, progress };
  } catch (error) {
    return { error: failure(error), calls, progress };
  }
}

// repr() of a parsed value: NaN, infinities, key order and lone surrogates survive the comparison.
function parseRepr(text) {
  try {
    return { repr: Courses._internal.repr(Courses.parseAnalysis(text)) };
  } catch (error) {
    return { error: failure(error) };
  }
}

function loadRepr(text) {
  try {
    return { repr: Courses._internal.repr(Courses._internal.loadJson(text)) };
  } catch (error) {
    return { error: error.name };
  }
}

function textRepr(value, limit) {
  return Courses._internal.repr(Courses._internal.text(value, limit));
}

module.exports = { importCourse, analyse, parseRepr, loadRepr, textRepr };
