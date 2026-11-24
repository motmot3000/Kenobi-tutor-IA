// Calls of browser/providers.js for tests/test_parity_providers.py, with a fetch that replays prepared answers.
// An answer: {status, base64, cuts}: the body bytes, delivered in chunks cut at the given byte offsets.
"use strict";

const P = require("../../browser/providers.js");

function fetcher(replies, requests) {
  let index = 0;
  return async (url, init) => {
    requests.push({
      url, method: init.method,
      headers: Object.fromEntries(Object.entries(init.headers).map(([name, value]) => [name.toLowerCase(), value])),
      body: init.body === undefined ? null : JSON.parse(init.body),
    });
    const reply = replies[Math.min(index, replies.length - 1)];
    index += 1;
    const body = Buffer.from(reply.base64 || "", "base64");
    const chunks = [];
    let last = 0;
    for (const cut of [...(reply.cuts || []), body.length]) {
      if (cut > last) chunks.push(new Uint8Array(body.subarray(last, cut)));
      last = Math.max(last, cut);
    }
    const stream = new ReadableStream({
      start(controller) {
        for (const chunk of chunks) controller.enqueue(chunk);
        controller.close();
      },
    });
    return new Response(stream, { status: reply.status || 200 });
  };
}

function describe(error) {
  if (error.name !== "ProviderError") return { name: error.name };
  return { name: error.name, status: error.status, message: error.message, code: error.code };
}

// Calls in sequence (what was learnt about a model stays from one call to the next, as in Python).
async function run(calls) {
  P._quirks.clear();
  const results = [];
  for (const call of calls) {
    const requests = [];
    const events = [];
    let error = null;
    try {
      const options = { ...call.options, fetchImpl: fetcher(call.replies, requests) };
      for await (const [kind, value] of P.streamChat(call.config, call.system, call.messages, options)) {
        events.push(kind === "done" ? [kind, { keys: Object.keys(value), truncated: value.truncated, model: value.model }] : [kind, value]);
      }
    } catch (caught) {
      error = describe(caught);
    }
    const quirks = P._quirks.get(JSON.stringify([call.config.baseUrl ?? null, call.config.model ?? null])) || null;
    results.push({ events, error, requests, quirks });
  }
  return results;
}

// Independent scenarios: nothing learnt is kept from one to the next.
async function runMany(scenarios) {
  const results = [];
  for (const calls of scenarios) results.push(await run(calls));
  return results;
}

async function models(cases) {
  const results = [];
  for (const { config, reply } of cases) {
    const requests = [];
    try {
      results.push({ models: await P.listModels(config, { fetchImpl: fetcher([reply], requests) }), requests });
    } catch (caught) {
      results.push({ error: describe(caught), requests });
    }
  }
  return results;
}

function details(bodies) {
  return bodies.map((body) => P._errorDetail(new Uint8Array(Buffer.from(body, "base64"))));
}

function normalize(lists) {
  return lists.map((messages) => P.normalizeMessages(messages));
}

module.exports = { run, runMany, models, details, normalize };
