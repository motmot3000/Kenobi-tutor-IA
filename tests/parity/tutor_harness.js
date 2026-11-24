// Harness of the tutor parity tests (tests/test_parity_tutor.py): a stub KenobiProviders that records what the
// tutor sends and replays a scripted reply, and runners that turn results and errors into plain JSON.
"use strict";

const Tutor = require("../../browser/tutor.js");

class ProviderError extends Error {
  constructor(status, message, code = "") {
    super(message);
    this.name = "ProviderError";
    this.status = status;
    this.message = message;
    this.code = code;
  }
}

// script: {deltas: [...], error: [status, message, code], abort: true, done: {...}}
function stubProviders(script, calls) {
  const finish = (config) => script.done || { elapsed: 0.5, firstToken: 0.1, truncated: false, model: config.model };
  const record = (config, system, messages, options) => calls.push({
    config,
    system,
    messages: JSON.parse(JSON.stringify(messages)),
    options: {
      keys: Object.keys(options).sort(),
      maxTokens: options.maxTokens ?? null,
      temperature: options.temperature ?? null,
      timeout: options.timeout ?? null,
    },
  });
  return {
    ProviderError,
    async *streamChat(config, system, messages, options = {}) {
      record(config, system, messages, options);
      for (const delta of script.deltas || []) yield ["delta", delta];
      if (script.error) throw new ProviderError(...script.error);
      if (script.abort) throw Object.assign(new Error("The operation was aborted."), { name: "AbortError" });
      yield ["done", finish(config)];
    },
    async complete(config, system, messages, options = {}) {
      record(config, system, messages, options);
      if (script.error) throw new ProviderError(...script.error);
      return [(script.deltas || []).join(""), finish(config)];
    },
  };
}

function failure(error) {
  if (error && error.name === "ChatError") return { ok: false, name: "ChatError", status: error.status, message: error.message };
  return { ok: false, name: error && error.name || "Error" };
}

// Any function of the module ("buildContext", "internals.minutes"…): {ok, value} or the error.
async function call(path, args) {
  const target = String(path).split(".").reduce((value, key) => value[key], Tutor);
  try {
    return { ok: true, value: await target(...args) };
  } catch (error) {
    return failure(error);
  }
}

async function streamReply(state, payload, options = {}, script = {}) {
  const calls = [];
  const events = [];
  let error = null;
  try {
    for await (const event of Tutor.streamReply(state, payload, { ...options, providers: stubProviders(script, calls) })) {
      events.push(event);
    }
  } catch (caught) {
    error = failure(caught);
  }
  return { events, error, calls };
}

async function chat(state, payload, options = {}, script = {}) {
  const calls = [];
  try {
    return { result: await Tutor.chat(state, payload, { ...options, providers: stubProviders(script, calls) }), error: null, calls };
  } catch (error) {
    return { result: null, error: failure(error), calls };
  }
}

async function testConnection(ai, script = {}) {
  const calls = [];
  try {
    return { result: await Tutor.testConnection(ai, { providers: stubProviders(script, calls) }), error: null, calls };
  } catch (error) {
    return { result: null, error: failure(error), calls };
  }
}

// Steps run in order, in one process: the status keeps the last error, like tutor.py's _last.
// step: {status: ai} | {stream: [state, payload, options, script]} | {test: [ai, script]}
async function statusScenario(steps) {
  const results = [];
  for (const step of steps) {
    if (step.status !== undefined) results.push(Tutor.status(step.status));
    else if (step.stream) results.push((await streamReply(...step.stream)).error);
    else if (step.test) results.push((await testConnection(...step.test)).error);
  }
  return results;
}

module.exports = { call, streamReply, chat, testConnection, statusScenario };
