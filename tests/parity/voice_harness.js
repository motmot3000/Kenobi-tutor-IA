// Calls of browser/voice.js for tests/test_parity_voice.py: prepared HTTP answers and a fake engine, no network.
"use strict";

const V = require("../../browser/voice.js");

const encoder = new TextEncoder();
const bytesOf = (text) => new Uint8Array(Buffer.from(text || "", "base64"));
const b64 = (bytes) => Buffer.from(bytes).toString("base64");

function describe(error) {
  if (error instanceof V.VoiceError) return { error: { status: error.status, message: error.message } };
  return { crash: true };
}

// An answer: {status, base64} or {network: true}.
function fetcher(replies, requests) {
  let index = 0;
  return async (url, init) => {
    const fields = init.body instanceof FormData
      ? [...init.body.entries()].filter(([name]) => name !== "file").map(([name, value]) => [name, String(value)]) : null;
    const headers = Object.fromEntries(Object.entries(init.headers || {}).map(([name, value]) => [name.toLowerCase(), value]));
    requests.push({ url, method: init.method, headers, fields, json: typeof init.body === "string" ? JSON.parse(init.body) : null });
    const reply = replies[Math.min(index, replies.length - 1)];
    index += 1;
    if (reply.network) throw new TypeError("Failed to fetch");
    return new Response(bytesOf(reply.base64), { status: reply.status || 200 });
  };
}

async function attempt(work) {
  try {
    return await work();
  } catch (error) {
    return describe(error);
  }
}

function checkWav(recordings) {
  return recordings.map((recording) => {
    try {
      return { seconds: V.checkWav(bytesOf(recording)) };
    } catch (error) {
      return describe(error);
    }
  });
}

function noise(texts) {
  return texts.map((text) => V.NOISE_TRANSCRIPTS.test(text));
}

function engineFor(item, fetchImpl) {
  if (item.engine === "elevenlabs") return new V.ElevenLabsVoice(item.key, item.voice, item.model, { fetchImpl });
  return new V.MistralVoice(item.key, item.voice, { fetchImpl });
}

// Each case: {engine, key, voice, model, method: "transcribe" | "speak" | "voices", args, replies}.
async function calls(cases) {
  const results = [];
  for (const item of cases) {
    const requests = [];
    const engine = engineFor(item, fetcher(item.replies, requests));
    const args = item.method === "transcribe" ? [bytesOf(item.args[0]), item.args[1]] : item.args;
    const result = await attempt(async () => {
      const value = await engine[item.method](...args);
      if (item.method === "speak") return { audio: b64(value.audio), mime: value.mime };
      return { value };
    });
    results.push({ result, requests });
  }
  return results;
}

async function localPrompts(termsLists) {
  const prompts = [];
  for (const terms of termsLists) {
    const requests = [];
    const engine = new V.LocalVoice({ url: "http://127.0.0.1:5111", fetchImpl: fetcher([{ base64: b64(encoder.encode('{"text": ""}')) }], requests) });
    await engine.transcribe(new Uint8Array(0), terms);
    const prompt = requests[0].fields.find(([name]) => name === "prompt");
    prompts.push(prompt ? prompt[1] : "");
  }
  return prompts;
}

class FakeEngine {
  constructor({ heard = "c'est quoi une boucle", failOn = null, problem = null } = {}) {
    this.name = "fake";
    this.label = "Voix d'essai";
    Object.assign(this, { heard, failOn, issue: problem });
    this.spoken = [];
    this.terms = null;
  }

  problem() {
    return this.issue;
  }

  async transcribe(_audio, terms = []) {
    this.terms = [...terms];
    return this.heard;
  }

  async speak(text) {
    if (this.failOn && text.includes(this.failOn)) throw new V.VoiceError("Voix en panne.");
    this.spoken.push(text);
    return { audio: encoder.encode("RIFF" + text), mime: "audio/wav" };
  }
}

const voiceConfig = (swissNumbers, fillers = true) => ({ engine: "fake", local: { voice: "pierre", url: "" }, swissNumbers, fillers });

async function phrases(cases) {
  const results = [];
  for (const item of cases) {
    V._resetPhrases();
    const engine = new FakeEngine({ failOn: item.failOn || null });
    const value = await V.phrases({ config: voiceConfig(item.swiss, item.fillers), engine: [engine, "voix"], name: item.name });
    results.push({ value, spoken: [...engine.spoken].sort() });
  }
  return results;
}

function replyFrom(scenario) {
  return async function* startReply(text) {
    for (const [index, chunk] of scenario.chunks.entries()) {
      if (scenario.raiseAt === index) {
        if (scenario.bug) throw new TypeError("x is not a function");
        throw Object.assign(new Error(scenario.errorMessage), { name: "ChatError", status: scenario.errorStatus });
      }
      yield ["delta", { text: chunk }];
    }
    yield ["done", { elapsed: 0.1, firstToken: 0.05, heardText: text }];
  };
}

async function turns(scenarios) {
  const results = [];
  for (const scenario of scenarios) {
    const engine = new FakeEngine({ heard: scenario.heard, failOn: scenario.failOn, problem: scenario.problem });
    const events = [];
    for await (const event of V.turn({
      config: voiceConfig(scenario.swiss), engine: [engine, "voix"], startReply: replyFrom(scenario),
      audio: scenario.audio === null ? null : bytesOf(scenario.audio), text: scenario.text, carry: scenario.carry, terms: scenario.terms,
    })) events.push(event);
    results.push({ events, spoken: engine.spoken, terms: engine.terms });
  }
  return results;
}

module.exports = { checkWav, noise, calls, localPrompts, phrases, turns };
