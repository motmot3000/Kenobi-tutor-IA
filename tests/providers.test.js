// browser/providers.js against a mocked fetch: no network, no real key.
const test = require("node:test");
const assert = require("node:assert/strict");
const P = require("../browser/providers.js");

const encoder = new TextEncoder();
const KEY = "test-key-not-real";
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function config(kind = "openai", extra = {}) {
  return { provider: "custom", label: "Stub", kind, baseUrl: "https://stub.test/v1/", model: "stub-fast", apiKey: KEY,
    reasoning: "none", configured: true, ...extra };
}

function abortError() {
  const error = new Error("This operation was aborted");
  error.name = "AbortError";
  return error;
}

// A body that streams `parts` like a real fetch does: aborting the request errors it.
function stream(parts, signal, log, { gapMs = 0, stall = false } = {}) {
  let index = 0;
  return new ReadableStream({
    start(controller) {
      if (signal) {
        signal.addEventListener("abort", () => {
          log.aborted = true;
          try { controller.error(abortError()); } catch (_error) { /* already closed */ }
        }, { once: true });
      }
    },
    async pull(controller) {
      if (index < parts.length) {
        if (gapMs) await sleep(gapMs);
        const part = parts[index++];
        try { controller.enqueue(typeof part === "string" ? encoder.encode(part) : part); } catch (_error) { /* errored */ }
      } else if (stall) {
        await new Promise((resolve) => signal && signal.addEventListener("abort", resolve, { once: true }));
      } else {
        try { controller.close(); } catch (_error) { /* errored */ }
      }
    },
    cancel() {
      log.cancelled = true;
    },
  });
}

// fetchImpl answering with the given replies in order (the last one repeats), and keeping what it received.
function server(...replies) {
  const calls = [];
  const fetchImpl = async (url, init) => {
    const call = { url, init, headers: init.headers, body: init.body ? JSON.parse(init.body) : null, log: {} };
    calls.push(call);
    const reply = replies.length > 1 ? replies.shift() : replies[0];
    return reply(call);
  };
  return { fetchImpl, calls };
}

const raw = (parts, options = {}) => (call) =>
  new Response(stream(parts, call.init.signal, call.log, options), { status: 200, headers: { "Content-Type": "text/event-stream" } });

const fail = (status, message = "refused") => () =>
  new Response(JSON.stringify({ error: { message } }), { status, headers: { "Content-Type": "application/json" } });

function openai(chunks, { stop = "stop", ...options } = {}) {
  const parts = chunks.map((text) => `data: ${JSON.stringify({ choices: [{ index: 0, delta: { content: text }, finish_reason: null }] })}\n\n`);
  parts.push(`data: ${JSON.stringify({ choices: [{ index: 0, delta: {}, finish_reason: stop }] })}\n\ndata: [DONE]\n\n`);
  return raw(parts, options);
}

function anthropic(chunks, { stop = "end_turn", ...options } = {}) {
  const parts = chunks.map((text) =>
    `event: content_block_delta\ndata: ${JSON.stringify({ type: "content_block_delta", delta: { type: "text_delta", text } })}\n\n`);
  parts.push(`event: message_delta\ndata: ${JSON.stringify({ type: "message_delta", delta: { stop_reason: stop } })}\n\n`);
  parts.push('event: message_stop\ndata: {"type": "message_stop"}\n\n');
  return raw(parts, options);
}

async function collect(iterable) {
  const events = [];
  for await (const event of iterable) events.push(event);
  return events;
}

async function failure(promise) {
  try {
    await promise;
  } catch (error) {
    return error;
  }
  assert.fail("expected a ProviderError");
}

const user = (content) => [{ role: "user", content }];
const deltas = (events) => events.filter(([kind]) => kind === "delta").map(([, text]) => text);
const reset = () => P._quirks.clear();

test("an OpenAI-compatible stream gives deltas then done, with the same request as Python", async () => {
  reset();
  const { fetchImpl, calls } = server(openai(["Bon", "jour", " !"]));
  const events = await collect(P.streamChat(config(), "Système.", user("Salut"), { fetchImpl }));
  assert.deepEqual(deltas(events), ["Bon", "jour", " !"]);
  const [kind, done] = events[events.length - 1];
  assert.equal(kind, "done");
  assert.deepEqual(Object.keys(done), ["elapsed", "firstToken", "truncated", "model"]);
  assert.equal(done.truncated, false);
  assert.equal(done.model, "stub-fast");
  const [request] = calls;
  assert.equal(request.url, "https://stub.test/v1/chat/completions");
  assert.equal(request.init.method, "POST");
  assert.deepEqual(request.headers, { "Content-Type": "application/json", Accept: "text/event-stream", Authorization: "Bearer " + KEY });
  assert.deepEqual(request.body, {
    model: "stub-fast", stream: true, messages: [{ role: "system", content: "Système." }, { role: "user", content: "Salut" }],
    max_tokens: 4000, temperature: 0.4, reasoning_effort: "none",
  });
  // "auto" sends no reasoning setting, and no system prompt means no system message.
  const second = server(openai(["ok"]));
  await collect(P.streamChat(config("openai", { reasoning: "auto" }), "", user("a"), { fetchImpl: second.fetchImpl, maxTokens: 50, temperature: null }));
  assert.deepEqual(second.calls[0].body, { model: "stub-fast", stream: true, messages: [{ role: "user", content: "a" }], max_tokens: 50 });
});

test("SSE cut at awkward places, CRLF, comments, named events and multi-line data are read like Python", async () => {
  reset();
  const accent = encoder.encode('data: {"choices":[{"delta":{"content":"é"}}]}\n\n');
  const cut = accent.indexOf(0xc3) + 1;  // inside the two bytes of "é"
  const parts = [
    ": keep-alive comment\r\n", "da", "ta: {\"choices\":[{\"delta\":{\"con", "tent\":\"Un\"}}]}\r", "\n\r\n",
    accent.subarray(0, cut), accent.subarray(cut),
    "event: message\ndata: {\"choices\":\ndata: [{\"delta\":{\"content\":\" deux\"}}]}\n\n",
    "data: pas du JSON\n\n", "   \n", "data:{\"choices\":[{\"delta\":{\"content\":\"!\"},\"finish_reason\":\"length\"}]}",
  ];
  const { fetchImpl } = server(raw(parts));
  const events = await collect(P.streamChat(config(), "", user("a"), { fetchImpl }));
  assert.deepEqual(deltas(events), ["Un", "é", " deux", "!"]);
  assert.equal(events[events.length - 1][1].truncated, true);
});

test("reasoning parts are never taken for the reply", async () => {
  reset();
  const chunk = (delta) => `data: ${JSON.stringify({ choices: [{ delta }] })}\n\n`;
  const { fetchImpl } = server(raw([
    chunk({ reasoning_content: "je réfléchis" }),
    chunk({ content: [{ type: "reasoning", text: "plan" }, { type: "thinking", text: "hmm" }, { type: "text", text: "A" }] }),
    chunk({ content: [{ type: "output_text", text: "B" }, { type: "text", text: 3 }, "C"] }),
    "data: [DONE]\n\n",
  ]));
  const events = await collect(P.streamChat(config(), "", user("a"), { fetchImpl }));
  assert.deepEqual(deltas(events), ["A", "B"]);
  const only = server(raw([chunk({ reasoning_content: "rien d'autre" }), "data: [DONE]\n\n"]));
  const error = await failure(collect(P.streamChat(config(), "", user("a"), { fetchImpl: only.fetchImpl })));
  assert.deepEqual([error.status, error.code, error.message], [502, "empty_reply", "Le modèle a renvoyé une réponse vide."]);
});

test("the Anthropic protocol uses its own headers, body and events", async () => {
  reset();
  const extra = [
    'event: message_start\ndata: {"type": "message_start", "message": {}}\n\n',
    'event: ping\ndata: {"type": "ping"}\n\n',
    'event: content_block_delta\ndata: {"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": "hmm"}}\n\n',
  ];
  const { fetchImpl, calls } = server(anthropic(["Oui", "."], { stop: "max_tokens" }));
  const [text, done] = await P.complete(config("anthropic"), "Système.", user("Salut"), { fetchImpl, maxTokens: 70000 });
  assert.equal(text, "Oui.");
  assert.equal(done.truncated, true);
  const [request] = calls;
  assert.equal(request.url, "https://stub.test/v1/messages");
  assert.deepEqual(request.headers, {
    "Content-Type": "application/json", Accept: "text/event-stream", "anthropic-version": "2023-06-01",
    "anthropic-dangerous-direct-browser-access": "true", "x-api-key": KEY,
  });
  assert.deepEqual(request.body, {
    model: "stub-fast", stream: true, messages: [{ role: "user", content: "Salut" }], max_tokens: 64000,
    system: [{ type: "text", text: "Système.", cache_control: { type: "ephemeral" } }], temperature: 0.4,
  });
  // Thinking deltas are skipped, and message_stop ends the reading even if something follows.
  const more = server(raw([...extra,
    'event: content_block_delta\ndata: {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Fin"}}\n\n',
    'event: message_stop\ndata: {"type": "message_stop"}\n\n',
    'event: content_block_delta\ndata: {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "trop"}}\n\n']));
  assert.equal((await P.complete(config("anthropic"), "", user("a"), { fetchImpl: more.fetchImpl }))[0], "Fin");
});

test("HTTP errors are named for the person and never show the key", async () => {
  const cases = [
    [401, "auth", "Clé refusée par Stub : vérifie-la dans les réglages."],
    [403, "auth", "Clé refusée par Stub : vérifie-la dans les réglages."],
    [404, "not_found", "Modèle « stub-fast » ou adresse introuvable chez Stub."],
    [429, "rate", "Limite ou crédit atteint chez Stub : réessaie dans un instant."],
    [500, "upstream", "Stub a répondu par une erreur 500 : boom"],
  ];
  for (const [status, code, message] of cases) {
    reset();
    const { fetchImpl } = server(fail(status, "boom"));
    const error = await failure(P.complete(config(), "", user("a"), { fetchImpl }));
    assert.equal(error.name, "ProviderError");
    assert.deepEqual([error.code, error.message], [code, message]);
    assert.equal(error.status, status === 429 ? 503 : 502);
    assert.ok(!error.message.includes(KEY));
  }
  reset();
  const empty = server(() => new Response(null, { status: 503 }));
  const error = await failure(P.complete(config("openai", { label: "" }), "", user("a"), { fetchImpl: empty.fetchImpl }));
  assert.equal(error.message, "Le fournisseur a répondu par une erreur 503.");
});

test("a wrong key answered with 400 is a key problem: one request, nothing learnt", async () => {
  reset();
  const { fetchImpl, calls } = server(fail(400, "API key not valid. Please pass a valid API key."));
  const error = await failure(P.complete(config(), "", user("a"), { fetchImpl }));
  assert.equal(error.code, "auth");
  assert.equal(calls.length, 1);
  assert.equal(P._quirks.size, 0);
});

test("max_tokens switches to the name the model wants, and the name is kept", async () => {
  reset();
  const { fetchImpl, calls } = server(fail(400, "Unsupported parameter: 'max_tokens'. Use 'max_completion_tokens' instead."), openai(["ok"]));
  await P.complete(config(), "", user("a"), { fetchImpl, maxTokens: 50 });
  assert.equal(calls[0].body.max_tokens, 50);
  assert.equal(calls[1].body.max_completion_tokens, 50);
  assert.ok(!("max_tokens" in calls[1].body));
  await P.complete(config(), "", user("b"), { fetchImpl, maxTokens: 50 });
  assert.equal(calls.length, 3);
  assert.equal(calls[2].body.max_completion_tokens, 50);
});

test("a refused reasoning or temperature setting is dropped and remembered", async () => {
  reset();
  const reasoning = server(fail(400, "Unsupported parameter: 'reasoning_effort'"), openai(["un"]), openai(["deux"]));
  assert.equal((await P.complete(config(), "", user("a"), { fetchImpl: reasoning.fetchImpl }))[0], "un");
  assert.equal((await P.complete(config(), "", user("b"), { fetchImpl: reasoning.fetchImpl }))[0], "deux");
  assert.deepEqual(reasoning.calls.map((call) => "reasoning_effort" in call.body), [true, false, false]);
  reset();
  const temperature = server(fail(422, "temperature does not support 0.4 with this model"), openai(["ok"]));
  await P.complete(config(), "", user("a"), { fetchImpl: temperature.fetchImpl });
  assert.deepEqual(temperature.calls.map((call) => "temperature" in call.body), [true, false]);
  assert.deepEqual([...P._quirks.values()], [{ no_temperature: true }]);
});

test("a refusal that no setting explains teaches nothing", async () => {
  reset();
  const tooLong = "This model's maximum context length is 8192 tokens.";
  const { fetchImpl, calls } = server(fail(400, tooLong), fail(400, tooLong), openai(["ok"]));
  const error = await failure(P.complete(config(), "", user("a"), { fetchImpl }));
  assert.deepEqual([error.code, error.message], ["bad_request", `Stub a refusé la demande : ${tooLong}`]);
  assert.equal(calls.length, 2);
  assert.deepEqual(Object.keys(calls[1].body), ["model", "stream", "messages"]);
  assert.equal(P._quirks.size, 0);
  await P.complete(config(), "", user("b"), { fetchImpl });
  const body = calls[calls.length - 1].body;
  assert.deepEqual([body.reasoning_effort, "temperature" in body, "max_tokens" in body], ["none", true, true]);
});

test("after four refused attempts the request is given up and nothing stays learnt", async () => {
  reset();
  const { fetchImpl, calls } = server(fail(400, "use max_completion_tokens"), fail(400, "reasoning is not supported"),
    fail(400, "temperature is not supported"), fail(400, "max_completion_tokens is not supported"));
  const error = await failure(P.complete(config(), "", user("a"), { fetchImpl }));
  assert.deepEqual([error.status, error.code, error.message], [502, "bad_request", "Stub refuse les réglages de la demande."]);
  assert.equal(calls.length, 4);
  assert.deepEqual(Object.keys(calls[3].body), ["model", "stream", "messages", "max_completion_tokens"]);
  assert.equal(P._quirks.size, 0);
});

test("Anthropic learns a lower max_tokens cap", async () => {
  reset();
  const { fetchImpl, calls } = server(fail(400, "max_tokens: 20000 > 8192, which is the maximum allowed"), anthropic(["ok"]));
  await P.complete(config("anthropic"), "", user("a"), { fetchImpl, maxTokens: 20000 });
  assert.deepEqual(calls.map((call) => call.body.max_tokens), [20000, 8192]);
});

test("unconfigured, empty, unreachable and relative addresses are told apart", async () => {
  reset();
  let error = await failure(P.complete({ ...config(), configured: false }, "", user("a")));
  assert.deepEqual([error.status, error.code], [409, "unconfigured"]);
  error = await failure(P.complete(config(), "", [{ role: "assistant", content: "Bonjour" }, { role: "user", content: "  " }]));
  assert.deepEqual([error.status, error.code, error.message], [400, "empty", "Message vide."]);
  const offline = server(() => { throw new TypeError("Failed to fetch"); });
  error = await failure(P.complete(config(), "", user("a"), { fetchImpl: offline.fetchImpl }));
  assert.deepEqual([error.status, error.code, error.message], [503, "unreachable", "Stub est injoignable (Failed to fetch)."]);
  const nowhere = server(openai(["ok"]));
  error = await failure(P.complete(config("openai", { baseUrl: "" }), "", user("a"), { fetchImpl: nowhere.fetchImpl }));
  assert.equal(error.message, "Stub est injoignable (unknown url type: '/chat/completions').");
  assert.equal(nowhere.calls.length, 0);
});

test("the native fetch is used, not the page's /api shim", async () => {
  reset();
  const { fetchImpl, calls } = server(openai(["natif"]));
  const shim = globalThis.fetch;
  globalThis.__kenobiNativeFetch = fetchImpl;
  globalThis.fetch = () => assert.fail("the shim must not be called");
  try {
    assert.equal((await P.complete(config(), "", user("a")))[0], "natif");
  } finally {
    globalThis.fetch = shim;
    delete globalThis.__kenobiNativeFetch;
  }
  assert.equal(calls.length, 1);
});

test("aborting the signal stops a reply in progress and the request", async () => {
  reset();
  const { fetchImpl, calls } = server(openai(["un", "deux", "trois"]));
  const controller = new AbortController();
  const seen = [];
  const error = await failure((async () => {
    for await (const [, value] of P.streamChat(config(), "", user("a"), { fetchImpl, signal: controller.signal })) {
      seen.push(value);
      controller.abort();
    }
  })());
  assert.deepEqual([error.status, error.code, error.message], [499, "cancelled", "Réponse interrompue."]);
  assert.deepEqual(seen, ["un"]);
  assert.equal(calls[0].init.signal.aborted, true);
  const early = server(openai(["x"]));
  const already = new AbortController();
  already.abort();
  const late = await failure(P.complete(config(), "", user("a"), { fetchImpl: early.fetchImpl, signal: already.signal }));
  assert.equal(late.code, "cancelled");
  assert.equal(early.calls.length, 0);
});

test("closing the generator early aborts the request", async () => {
  reset();
  const { fetchImpl, calls } = server(openai(["un", "deux", "trois"], { stall: true }));
  for await (const [kind] of P.streamChat(config(), "", user("a"), { fetchImpl })) {
    if (kind === "delta") break;
  }
  assert.equal(calls[0].init.signal.aborted, true);
  assert.equal(calls[0].log.cancelled || calls[0].log.aborted, true);
});

test("an abort while waiting for the headers ends at once, even if fetch ignores it", async () => {
  reset();
  const { fetchImpl } = server(() => new Promise(() => {}));
  const controller = new AbortController();
  setTimeout(() => controller.abort(), 20);
  const started = Date.now();
  const error = await failure(P.complete(config(), "", user("a"), { fetchImpl, signal: controller.signal }));
  assert.equal(error.code, "cancelled");
  assert.ok(Date.now() - started < 1000);
});

test("timeouts: headers, silence between chunks, and the whole reply", async () => {
  reset();
  const hanging = server(() => new Promise(() => {}));
  let error = await failure(P.complete(config(), "", user("a"), { fetchImpl: hanging.fetchImpl, connectTimeout: 30 }));
  assert.deepEqual([error.status, error.code, error.message], [503, "unreachable", "Stub est injoignable (timed out)."]);
  const silent = server(raw(['data: {"choices":[{"delta":{"content":"un"}}]}\n\n'], { stall: true }));
  const seen = [];
  error = await failure((async () => {
    for await (const [kind, value] of P.streamChat(config(), "", user("a"), { fetchImpl: silent.fetchImpl, readTimeout: 40 })) {
      if (kind === "delta") seen.push(value);
    }
  })());
  assert.deepEqual([error.status, error.code, error.message, seen], [504, "timeout", "Le modèle ne répond plus.", ["un"]]);
  assert.equal(silent.calls[0].init.signal.aborted, true);
  const slow = server(openai(Array.from({ length: 100 }, (_, index) => `${index} `), { gapMs: 15 }));
  error = await failure(P.complete(config(), "", user("a"), { fetchImpl: slow.fetchImpl, timeout: 80 }));
  assert.deepEqual([error.status, error.code, error.message], [504, "timeout", "Le modèle n'a pas terminé à temps."]);
});

test("a reply longer than MAX_REPLY is refused", async () => {
  reset();
  const piece = "a".repeat(50000);
  const { fetchImpl } = server(openai([piece, piece, piece, piece, piece]));
  const seen = [];
  const error = await failure((async () => {
    for await (const [kind, value] of P.streamChat(config(), "", user("a"), { fetchImpl })) if (kind === "delta") seen.push(value);
  })());
  assert.deepEqual([error.code, error.message, seen.length], ["too_long", "Réponse du modèle trop longue.", 4]);
});

test("an error inside the stream is reported", async () => {
  reset();
  const openaiError = server(raw(['data: {"choices":[{"delta":{"content":"début"}}]}\n\n', 'data: {"error": {"message": "overloaded"}}\n\n']));
  let error = await failure(P.complete(config(), "", user("a"), { fetchImpl: openaiError.fetchImpl }));
  assert.deepEqual([error.status, error.code, error.message], [502, "upstream", "Le modèle a signalé une erreur : overloaded"]);
  const plain = server(raw(['data: {"error": "quota"}\n\n']));
  error = await failure(P.complete(config(), "", user("a"), { fetchImpl: plain.fetchImpl }));
  assert.equal(error.message, "Le modèle a signalé une erreur : quota");
  const anthropicError = server(raw(['event: error\ndata: {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}\n\n']));
  error = await failure(P.complete(config("anthropic"), "", user("a"), { fetchImpl: anthropicError.fetchImpl }));
  assert.equal(error.message, "Le modèle a signalé une erreur : Overloaded");
});

test("messages alternate and start with the user", () => {
  assert.deepEqual(P.normalizeMessages([
    { role: "assistant", content: "Avant tout." },
    { role: "user", content: "Un" },
    { role: "user", content: "Deux" },
    { role: "assistant", content: "" },
    { role: "assistant", content: "Trois" },
    { role: "system", content: "  Quatre\n" },
  ]), [{ role: "user", content: "Un\n\nDeux" }, { role: "assistant", content: "Trois" }, { role: "user", content: "Quatre" }]);
});

test("validation errors in a list read like Python's", () => {
  const body = JSON.stringify({ detail: [{ loc: ["body", "x"], msg: "field required" }, { loc: ["y"], type: "int" }, "trois", "quatre"] });
  assert.equal(P._errorDetail(body), "field required; {'loc': ['y'], 'type': 'int'}; trois");
  assert.equal(P._httpError(config(), 422, encoder.encode(body)).message,
    "Stub a refusé la demande : field required; {'loc': ['y'], 'type': 'int'}; trois");
  assert.equal(P._errorDetail('{"error": {"message": "  a\\n\\tb  "}}'), "a b");
  assert.equal(P._errorDetail("pas du JSON"), "");
});

test("models are listed without the ones that do not chat, sorted, with their labels", async () => {
  const items = [
    { id: "stub-large", name: "Stub Large" }, { id: "stub-fast" }, { id: "text-embedding-3" }, "Plain-Model",
    { name: "models/gemini-pro", display_name: "Gemini Pro" }, { id: "voxtral-mini" },
    { id: "mistral-ocr", capabilities: { completion_chat: true } }, { id: "codestral", capabilities: { completion_chat: false } },
    { id: "stub-fast" }, { id: "x".repeat(121) }, { id: "  spaced  ", display_name: "spaced" }, 42, null,
  ];
  const { fetchImpl, calls } = server(() => new Response(JSON.stringify({ data: items })));
  const models = await P.listModels(config(), { fetchImpl });
  assert.deepEqual(models, [
    { id: "gemini-pro", label: "Gemini Pro" }, { id: "mistral-ocr", label: "" }, { id: "Plain-Model", label: "" },
    { id: "spaced", label: "" }, { id: "stub-fast", label: "" }, { id: "stub-large", label: "Stub Large" },
  ]);
  assert.equal(calls[0].url, "https://stub.test/v1/models");
  assert.equal(calls[0].init.method, "GET");
  assert.deepEqual(calls[0].headers, { "Content-Type": "application/json", Authorization: "Bearer " + KEY });

  const claude = server(() => new Response(JSON.stringify({ models: [{ id: "claude-x", display_name: "Claude X" }] })));
  assert.deepEqual(await P.listModels(config("anthropic"), { fetchImpl: claude.fetchImpl }), [{ id: "claude-x", label: "Claude X" }]);
  assert.equal(claude.calls[0].url, "https://stub.test/v1/models?limit=200");
  assert.equal(claude.calls[0].headers["x-api-key"], KEY);

  const refused = server(fail(404, "nope"));
  let error = await failure(P.listModels(config(), { fetchImpl: refused.fetchImpl }));
  assert.equal(error.message, "Modèle «  » ou adresse introuvable chez Stub.");
  const broken = server(() => new Response("<html>"));
  error = await failure(P.listModels(config(), { fetchImpl: broken.fetchImpl }));
  assert.deepEqual([error.status, error.code, error.message], [502, "upstream", "Liste des modèles illisible."]);
  error = await failure(P.listModels({ ...config(), baseUrl: "" }));
  assert.deepEqual([error.status, error.message], [400, "Adresse de l'API manquante."]);
  const many = server(() => new Response(JSON.stringify(Array.from({ length: 600 }, (_, index) => `m-${String(index).padStart(3, "0")}`))));
  assert.equal((await P.listModels(config(), { fetchImpl: many.fetchImpl })).length, 500);
});
