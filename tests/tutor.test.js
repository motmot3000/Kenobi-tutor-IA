// Behaviour of browser/tutor.js that has no Python counterpart: loading as a classic script, the lazy providers,
// AbortSignal and the `today` option. Parity with tutor.py is proved in tests/test_parity_tutor.py.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const Tutor = require("../browser/tutor.js");

const SOURCE = path.join(__dirname, "..", "browser", "tutor.js");
const AI = { provider: "custom", label: "Service de test", kind: "openai", baseUrl: "http://127.0.0.1:9/v1", model: "stub",
  apiKey: "cle", keySource: "saved", reasoning: "none", configured: true, keyless: true };

function state() {
  return {
    version: 1,
    activeCourseId: "git",
    courses: [{
      id: "git", title: "Git", description: "", tools: { terminal: true, language: "shell", file: "exercice.sh" },
      modules: [{ id: "module-1", title: "Commits", description: "", lessons: [{ id: "l1", title: "Commit", status: "current", duration: 20 }] }],
      sessions: [{ id: "session-1", title: "Commit", startedAt: "", updatedAt: "", duration: 0, status: "active", lessonId: "l1",
        summary: "", messages: [{ id: "m1", role: "user", createdAt: "", content: "Bonjour" }] }],
      memory: { lastSessionId: null, acquired: [], toReview: [], nextFocus: "", notes: "" },
    }],
  };
}

class ProviderError extends Error {
  constructor(status, message, code = "") {
    super(message);
    this.name = "ProviderError";
    this.status = status;
    this.code = code;
  }
}

async function drain(events) {
  const items = [];
  for await (const item of events) items.push(item);
  return items;
}

test("loading the tutor does not load the providers", () => {
  assert.equal(Object.keys(require.cache).some((file) => file.endsWith(`${path.sep}providers.js`)), false);
  assert.equal(globalThis.KenobiTutor, Tutor);
  assert.match(Tutor.buildSystem(state(), "session-1", { today: "2026-11-24" }), /Tu es Kenobi/);
});

test("as a classic script, the tutor uses the KenobiShared and KenobiProviders globals", async () => {
  const seen = [];
  const sandbox = {
    KenobiShared: require("../browser/shared.js"),
    KenobiProviders: {
      ProviderError,
      async *streamChat(config, system, messages, options) {
        seen.push(options);
        yield ["delta", "Salut"];
        yield ["done", { model: config.model }];
      },
    },
  };
  vm.runInNewContext(fs.readFileSync(SOURCE, "utf8"), sandbox, { filename: SOURCE });
  const events = await drain(sandbox.KenobiTutor.streamReply(state(), { sessionId: "session-1", message: "Bonjour" }, { ai: AI }));
  assert.deepEqual(JSON.parse(JSON.stringify(events)), [["delta", { text: "Salut" }], ["done", { mode: "api", model: "stub" }]]);
  assert.equal(seen[0].maxTokens, 4000);
});

test("the abort signal reaches the provider and a cancelled reply is not an error to remember", async () => {
  const controller = new AbortController();
  const providers = {
    ProviderError,
    async *streamChat(config, system, messages, options) {
      assert.equal(options.signal, controller.signal);
      yield ["delta", "Je "];
      controller.abort();
      if (options.signal.aborted) throw new ProviderError(499, "Réponse interrompue.", "cancelled");
    },
  };
  const before = Tutor.status(AI);
  const events = Tutor.streamReply(state(), { sessionId: "session-1", message: "Bonjour" }, { ai: AI, signal: controller.signal, providers });
  await assert.rejects(drain(events), (error) => error.name === "ChatError" && error.status === 499 && error.message === "Réponse interrompue.");
  assert.deepEqual(Tutor.status(AI), before);
});

test("a raw AbortError is a cancelled reply too", async () => {
  const providers = {
    async *streamChat() {
      yield ["delta", "Je "];
      throw Object.assign(new Error("The operation was aborted."), { name: "AbortError" });
    },
  };
  const events = Tutor.streamReply(state(), { sessionId: "session-1", message: "Bonjour" }, { ai: AI, providers });
  await assert.rejects(drain(events), (error) => error.name === "ChatError" && error.status === 499);
  assert.equal(Tutor.status(AI).problem, "");
});

test("leaving the stream early closes the provider stream", async () => {
  let closed = false;
  const providers = {
    async *streamChat() {
      try {
        yield ["delta", "Un"];
        yield ["delta", "Deux"];
      } finally {
        closed = true;
      }
    },
  };
  for await (const [kind] of Tutor.streamReply(state(), { sessionId: "session-1", message: "Bonjour" }, { ai: AI, providers })) {
    assert.equal(kind, "delta");
    break;
  }
  assert.equal(closed, true);
});

test("a turn changes neither the state nor the saved messages", async () => {
  const value = state();
  const copy = JSON.parse(JSON.stringify(value));
  const providers = { async *streamChat() { yield ["done", {}]; } };
  await drain(Tutor.streamReply(value, { sessionId: "session-1", message: "Encore" }, { ai: AI, providers, voice: true, learner: { name: "Léa" } }));
  assert.deepEqual(value, copy);
});

test("today is an ISO date, a Date, or the local date", () => {
  const context = (today) => Tutor.buildContext(state(), "session-1", { today });
  assert.match(context("2026-11-24"), /Date : mardi 24 novembre 2026\./);
  assert.match(context(new Date(2026, 10, 8, 23, 30)), /Date : dimanche 8 novembre 2026\./);
  const now = new Date();
  assert.match(context(null), new RegExp(`Date : ${Tutor.internals.frenchDate(now)}\\.`));
  assert.throws(() => context("2026-02-30"), RangeError);
  assert.throws(() => context("24/11/2026"), RangeError);
});

test("the providers are required only when a reply is asked", async () => {
  const events = Tutor.streamReply(state(), { sessionId: "session-1", message: "  " }, { ai: AI });
  await assert.rejects(drain(events), (error) => error.name === "ChatError" && error.status === 400);
  await assert.rejects(Tutor.testConnection({ ...AI, configured: false }), (error) => error.status === 409);
});
