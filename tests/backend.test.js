// The web version's backend (browser/backend.js), vault and storage, in Node: no IndexedDB (the store falls back to
// memory), Web Crypto from Node, static files read from the repository, providers answered by a fake fetch.
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const ROOT = path.join(__dirname, "..");
const PAGE = "https://example.github.io/Kenobi-tutor-IA/";
globalThis.document = { baseURI: PAGE };
globalThis.location = new URL(PAGE);

const providerCalls = [];
let providerReply = ["Bonjour, ", "c'est Kenobi."];

function sseBody(chunks) {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const text of chunks) {
        controller.enqueue(encoder.encode(`data: ${JSON.stringify({ choices: [{ index: 0, delta: { content: text } }] })}\n\n`));
      }
      controller.enqueue(encoder.encode(`data: ${JSON.stringify({ choices: [{ index: 0, delta: {}, finish_reason: "stop" }] })}\n\ndata: [DONE]\n\n`));
      controller.close();
    }
  });
}

// Static files of the app come from the repository; the AI provider is a fake that streams providerReply.
async function fakeFetch(input, init = {}) {
  const url = new URL(typeof input === "string" ? input : input.url);
  if (url.origin === "https://example.github.io") {
    const file = path.join(ROOT, url.pathname.replace("/Kenobi-tutor-IA/", ""));
    if (!fs.existsSync(file)) return new Response("not found", { status: 404 });
    return new Response(fs.readFileSync(file));
  }
  if (url.hostname === "llm.test") {
    const headers = new Headers(init.headers || (typeof input === "object" ? input.headers : undefined));
    providerCalls.push({ url: url.href, headers: Object.fromEntries(headers.entries()), body: init.body ? JSON.parse(init.body) : null });
    if (url.pathname.endsWith("/models")) return Response.json({ data: [{ id: "stub-fast" }, { id: "stub-embed" }] });
    return new Response(sseBody(providerReply), { headers: { "Content-Type": "text/event-stream" } });
  }
  return new Response("unexpected", { status: 599 });
}

const Backend = require("../browser/backend.js");
const Vault = require("../browser/vault.js");
Backend._internals.setNativeFetch(fakeFetch);
Backend._internals.setBasePath("/Kenobi-tutor-IA/");

const api = (route, init) => Backend.handle(new URL(`api/${route}`, PAGE).href, init);
const post = (route, body, headers = {}) => api(route, {
  method: "POST", headers: { "Content-Type": "application/json", ...headers }, body: JSON.stringify(body)
});

async function events(response) {
  const text = await response.text();
  return text.split("\n\n").filter(Boolean).map((block) => {
    const kind = /^event: (.+)$/m.exec(block)?.[1];
    const data = /^data: (.+)$/m.exec(block)?.[1];
    return [kind, data ? JSON.parse(data) : null];
  });
}

test("health says no model is plugged at first", async () => {
  const value = await (await api("health")).json();
  assert.equal(value.mode, "browser");
  assert.equal(value.agent.configured, false);
});

test("the built-in course is served with a version tag", async () => {
  const response = await api("state");
  assert.equal(response.status, 200);
  const tag = response.headers.get("ETag").replace(/"/g, "");
  const state = await response.json();
  assert.equal(state.courses[0].id, "python");
  assert.equal((await (await api("state/tag")).json()).tag, tag);
});

test("a stale page is refused, a fresh one saves", async () => {
  const response = await api("state");
  const tag = response.headers.get("ETag").replace(/"/g, "");
  const state = await response.json();
  state.courses[0].memory.notes = "Page A";
  const saved = await post("state", state, { "If-Match": `"${tag}"` });
  assert.equal(saved.status, 200);
  const next = (await saved.json()).tag;
  assert.notEqual(next, tag);
  state.courses[0].memory.notes = "Page B";
  const stale = await post("state", state, { "If-Match": `"${tag}"` });
  assert.equal(stale.status, 409);
  assert.deepEqual(await stale.json(), { error: "L'état a changé ailleurs (appel, autre onglet) : la page le relit.", conflict: true, tag: next });
  const invalid = await post("state", { version: 1 });
  assert.equal(invalid.status, 400);
});

test("settings never give a key back, and a key follows its provider", async () => {
  let info = await (await post("settings", { ai: { provider: "custom", baseUrl: "https://llm.test/v1", model: "stub-fast", apiKey: "sk-test-123456" } })).json();
  assert.equal(info.ai.configured, true);
  assert.equal(info.ai.hasKey, true);
  assert.doesNotMatch(JSON.stringify(info), /sk-test-123456/);
  assert.equal(await Vault.get("ai"), "sk-test-123456");
  info = await (await post("settings", { ai: { baseUrl: "https://other.test/v1" } })).json();
  assert.equal(info.ai.hasKey, false, "another address forgets the key");
  info = await (await post("settings", { ai: { baseUrl: "https://llm.test/v1", apiKey: "sk-test-123456" } })).json();
  assert.equal(info.ai.hasKey, true);
  const refused = await post("settings", { ai: { apiKey: "sk with spaces" } });
  assert.equal(refused.status, 400);
  assert.match((await refused.json()).error, /Clé d'API illisible/);
});

test("model list and test go to the provider with the saved key", async () => {
  const models = await (await post("settings/models", { ai: { provider: "custom", baseUrl: "https://llm.test/v1" } })).json();
  assert.deepEqual(models.models.map((item) => item.id), ["stub-fast"]);
  assert.equal(providerCalls.at(-1).headers.authorization, "Bearer sk-test-123456");
  providerReply = ["ok"];
  const result = await (await post("settings/test", {})).json();
  assert.equal(result.ok, true);
  assert.equal(result.model, "stub-fast");
});

test("a chat turn streams the tutor's answer built from the saved state", async () => {
  const state = await (await api("state")).json();
  const lesson = state.courses[0].modules[0].lessons[0];
  const now = new Date().toISOString();
  state.courses[0].sessions.push({
    id: "session-web-1", title: lesson.title, startedAt: now, updatedAt: now, duration: "", status: "active",
    lessonId: lesson.id, summary: "", messages: [{ id: "message-1", role: "user", createdAt: now, content: "Bonjour" }]
  });
  const tag = (await (await api("state/tag")).json()).tag;
  assert.equal((await post("state", state, { "If-Match": `"${tag}"` })).status, 200);
  providerReply = ["Bonjour, ", "c'est Kenobi."];
  const response = await post("chat/stream", { sessionId: "session-web-1", message: "Bonjour", terminal: "closed" });
  assert.equal(response.headers.get("Content-Type"), "text/event-stream; charset=utf-8");
  const list = await events(response);
  assert.deepEqual(list.filter(([kind]) => kind === "delta").map(([, data]) => data.text).join(""), "Bonjour, c'est Kenobi.");
  assert.equal(list.at(-1)[0], "done");
  const sent = providerCalls.at(-1).body;
  assert.equal(sent.model, "stub-fast");
  assert.match(sent.messages[0].content, /Tu es Kenobi/);
  assert.match(sent.messages[0].content, new RegExp(lesson.title.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
});

test("documents, memory and template are served as Markdown", async () => {
  const source = await api("courses/source?courseId=python");
  assert.equal(source.status, 200);
  assert.match(await source.text(), /^# Parcours de formation Python/);
  const memory = await api("memory?courseId=python&download=1");
  assert.match(memory.headers.get("Content-Disposition"), /python-MEMORY\.md/);
  assert.match(await memory.text(), /Page A/);
  const template = await api("courses/template");
  assert.match(await template.text(), /^# Titre du cours/);
  assert.equal((await api("memory?courseId=../x")).status, 400);
});

test("bridges are downloaded with this page's address written in them", async () => {
  const terminal = await (await api("terminal/pont")).text();
  assert.match(terminal, /EMBEDDED_KENOBI_URL = "https:\/\/example\.github\.io\/Kenobi-tutor-IA\/"/);
  assert.equal((await (await api("terminal")).json()).reason, "remote");
});

test("a backup holds everything but the keys, and restores", async () => {
  const backup = await (await api("backup")).json();
  assert.equal(backup.app, "kenobi");
  assert.doesNotMatch(JSON.stringify(backup), /sk-test-123456/);
  backup.state.courses[0].memory.notes = "Restaurée";
  const restored = await post("backup", backup);
  assert.equal(restored.status, 200);
  assert.equal((await (await api("state")).json()).courses[0].memory.notes, "Restaurée");
  assert.equal((await post("backup", { app: "autre" })).status, 400);
});

test("the vault locks with a passphrase and forgets on demand", async () => {
  assert.equal((await Vault.status()).mode, "device");
  let info = await (await post("settings/vault", { action: "mode", mode: "passphrase", passphrase: "court" })).json();
  assert.match(info.error, /trop courte/);
  info = await (await post("settings/vault", { action: "mode", mode: "passphrase", passphrase: "une phrase assez longue" })).json();
  assert.equal(info.vault.mode, "passphrase");
  await post("settings/vault", { action: "lock" });
  const health = await (await api("health")).json();
  assert.match(health.agent.problem, /verrouillées/);
  const locked = await post("chat/stream", { sessionId: "session-web-1", message: "Bonjour" });
  assert.equal(locked.status, 423);
  assert.equal((await post("settings/vault", { action: "unlock", passphrase: "mauvaise phrase" })).status, 403);
  info = await (await post("settings/vault", { action: "unlock", passphrase: "une phrase assez longue" })).json();
  assert.equal(info.vault.locked, false);
  assert.equal(await Vault.get("ai"), "sk-test-123456");
  info = await (await post("settings/vault", { action: "mode", mode: "session" })).json();
  assert.equal(info.vault.mode, "session");
  assert.equal(await Vault.get("ai"), "sk-test-123456", "the visit keeps its keys in memory");
  info = await (await post("settings/vault", { action: "forget" })).json();
  assert.equal(info.ai.hasKey, false);
  assert.equal(info.vault.mode, "session", "forgetting the keys keeps the chosen mode");
});

test("the visit-only mode survives a reload of the page", async () => {
  const Store = require("../browser/store.js");
  assert.equal(await Store.get("vault-mode"), "session");
  assert.equal(await Store.get("vault"), undefined, "nothing written");
  await post("settings/vault", { action: "mode", mode: "device" });
  assert.equal(await Store.get("vault-mode"), undefined);
});

test("voice bridge pairing codes are checked", async () => {
  const bad = await post("settings", { voice: { local: { code: "pas-un-code" } } });
  assert.equal(bad.status, 400);
  const info = await (await post("settings", { voice: { engine: "local", local: { code: `8196-${"a".repeat(43)}` } } })).json();
  assert.equal(info.voice.local.paired, true);
  assert.equal(info.voice.local.url, "http://127.0.0.1:8196");
  assert.doesNotMatch(JSON.stringify(info), /aaaaaaaaaaaa/);
});

test("unknown routes and methods", async () => {
  assert.equal((await api("nope")).status, 404);
  assert.equal((await api("state", { method: "DELETE" })).status, 405);
});
