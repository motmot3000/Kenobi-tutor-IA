// browser/voice.js: WAV checks, engines against a mocked fetch, and the call turn with a fake engine.
const test = require("node:test");
const assert = require("node:assert/strict");
const V = require("../browser/voice.js");
const Shared = require("../browser/shared.js");

const encoder = new TextEncoder();
const decoder = new TextDecoder();
const KEY = "test-key-not-real";
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function wav(seconds = 1, { rate = 16000, channels = 1, bits = 16 } = {}) {
  const width = Math.ceil(bits / 8);
  const data = Math.floor(seconds * rate) * channels * width;
  const bytes = new Uint8Array(44 + data);
  const view = new DataView(bytes.buffer);
  const text = (offset, value) => [...value].forEach((char, index) => { bytes[offset + index] = char.charCodeAt(0); });
  text(0, "RIFF"); view.setUint32(4, 36 + data, true); text(8, "WAVE"); text(12, "fmt ");
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, channels, true);
  view.setUint32(24, rate, true); view.setUint32(28, rate * channels * width, true);
  view.setUint16(32, channels * width, true); view.setUint16(34, bits, true);
  text(36, "data"); view.setUint32(40, data, true);
  return bytes;
}

const riff = (size = 64) => { const bytes = new Uint8Array(size); bytes.set(encoder.encode("RIFF")); return bytes; };
const base64 = (bytes) => Buffer.from(bytes).toString("base64");

function server(...replies) {
  const calls = [];
  const fetchImpl = async (url, init) => {
    calls.push({ url, init, headers: init.headers || {}, body: init.body });
    const reply = replies.length > 1 ? replies.shift() : replies[0];
    return reply({ url, init });
  };
  return { fetchImpl, calls };
}

const json = (value, status = 200) => () => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
const bytes = (value, status = 200) => () => new Response(value, { status });
const status = (code) => () => new Response("{}", { status: code });

async function failure(promise) {
  try {
    await promise;
  } catch (error) {
    return error;
  }
  assert.fail("expected an error");
}

class FakeEngine {
  constructor({ heard = "c'est quoi une boucle", failOn = null, delay = 0, problem = null } = {}) {
    this.name = "fake";
    this.label = "Voix d'essai";
    Object.assign(this, { heard, failOn, delay, issue: problem });
    this.spoken = [];
    this.terms = null;
    this.signals = [];
  }

  problem() {
    return this.issue;
  }

  async transcribe(_audio, terms = []) {
    this.terms = [...terms];
    return this.heard;
  }

  async speak(text, _voice, { signal } = {}) {
    this.signals.push(signal);
    if (this.failOn && text.includes(this.failOn)) throw new V.VoiceError("Voix en panne.");
    if (this.delay) await sleep(this.delay);
    this.spoken.push(text);
    return { audio: encoder.encode("RIFF" + text), mime: "audio/wav" };
  }
}

const voiceConfig = (extra = {}) => ({
  engine: "local", local: { voice: "pierre", url: "http://127.0.0.1:5111", token: "" },
  mistral: { apiKey: "", voice: "voice_589" }, elevenlabs: { apiKey: "", voice: "", model: "eleven_flash_v2_5" },
  swissNumbers: false, fillers: true, ...extra,
});

function replying(chunks, seen = {}) {
  return async function* startReply(text, signal) {
    seen.text = text;
    seen.signal = signal;
    for (const chunk of chunks) yield ["delta", { text: chunk }];
    yield ["done", { elapsed: 0.1, firstToken: 0.05 }];
  };
}

async function runTurn(engine, chunks, options = {}) {
  const seen = {};
  const events = [];
  for await (const event of V.turn({ config: voiceConfig(options.config), engine: [engine, "voix"], startReply: replying(chunks, seen), ...options })) {
    events.push(event);
  }
  return { events, seen };
}

// ── WAV ──────────────────────────────────────────────────────────────────

test("recordings are checked like Python's check_wav", () => {
  assert.equal(V.checkWav(wav(1.5)), 1.5);
  assert.equal(V.checkWav(wav(2, { channels: 2 }).buffer), 2);
  const cases = [
    [new Uint8Array(0), 413, "Enregistrement vide ou trop volumineux."],
    [null, 413, "Enregistrement vide ou trop volumineux."],
    [encoder.encode("pas un wav"), 400, "Enregistrement audio illisible."],
    [wav(1, { bits: 8 }), 400, "Format audio inattendu."],
    [wav(0.1, { channels: 3 }), 400, "Format audio inattendu."],
    [wav(Shared.voice.MAX_SECONDS + 1, { rate: 8000 }), 413, "Phrase trop longue (60 secondes au plus)."],
    [new Uint8Array(Shared.voice.MAX_AUDIO + 1), 413, "Enregistrement vide ou trop volumineux."],
    [wav(1).subarray(0, 30), 400, "Enregistrement audio illisible."],
  ];
  for (const [audio, code, message] of cases) {
    assert.throws(() => V.checkWav(audio), (error) => error instanceof V.VoiceError && error.status === code && error.message === message);
  }
});

test("noise transcripts are recognised, whatever the case", () => {
  for (const text of ["Sous-titres réalisés par la communauté d'Amara.org", "...", "Merci d'avoir regardé cette vidéo !", "[Musique]",
    "  SOUS-TITRAGE ST' 501  ", "(rires)", "…", "Abonnez-vous !", "MERCI D'AVOIR REGARDÉ", "sous-tıtres"]) {
    assert.ok(V.NOISE_TRANSCRIPTS.test(text), text);
  }
  for (const text of ["Merci, j'ai compris la boucle.", "Bonjour...", "[x] puis y", "abonnez vous"]) {
    assert.ok(!V.NOISE_TRANSCRIPTS.test(text), text);
  }
});

// ── Engines ──────────────────────────────────────────────────────────────

test("Voxtral transcription sends the same multipart fields as Python", async () => {
  const { fetchImpl, calls } = server(json({ text: "  bonjour Kenobi  " }));
  const engine = new V.MistralVoice(KEY, "voice_589", { fetchImpl });
  const terms = ["Kenobi", "boucle for", "print", "a", "console.log", "l’élève", "x".repeat(41), 5,
    ...Array.from({ length: 30 }, (_, index) => `mot${index}`)];
  const audio = wav(0.5);
  assert.equal(await engine.transcribe(audio, terms), "bonjour Kenobi");
  const [call] = calls;
  assert.equal(call.url, "https://api.mistral.ai/v1/audio/transcriptions");
  assert.equal(call.init.method, "POST");
  assert.deepEqual(call.headers, { Authorization: "Bearer " + KEY });
  assert.deepEqual([...call.body.keys()].slice(0, 2), ["model", "language"]);
  assert.equal(call.body.get("model"), Shared.voice.MISTRAL_STT_MODEL);
  assert.equal(call.body.get("language"), "fr");
  assert.deepEqual(call.body.getAll("context_bias"),
    ["Kenobi", "print", "console.log", "l’élève", ...Array.from({ length: 16 }, (_, index) => `mot${index}`)]);
  const file = call.body.get("file");
  assert.deepEqual([file.name, file.type], ["voix.wav", "audio/wav"]);
  assert.deepEqual(new Uint8Array(await file.arrayBuffer()), audio);
  const broken = new V.MistralVoice(KEY, "voice_589", { fetchImpl: server(bytes("[]")).fetchImpl });
  assert.equal((await failure(broken.transcribe(audio))).message, "Réponse de transcription Voxtral illisible.");
});

test("Voxtral synthesis reads base64 WAV and refuses anything else", async () => {
  const audio = riff(100);
  const { fetchImpl, calls } = server(json({ audio_data: base64(audio) }));
  const engine = new V.MistralVoice(KEY, "voice_589", { fetchImpl });
  const said = await engine.speak("é".repeat(5000));
  assert.deepEqual([said.audio, said.mime], [audio, "audio/wav"]);
  assert.equal(calls[0].url, "https://api.mistral.ai/v1/audio/speech");
  assert.deepEqual(calls[0].headers, { "Content-Type": "application/json", Authorization: "Bearer " + KEY });
  assert.deepEqual(JSON.parse(calls[0].body), { model: Shared.voice.MISTRAL_TTS_MODEL, input: "é".repeat(4000),
    voice_id: "voice_589", response_format: "wav" });
  await engine.speak("Salut", "voice_592");
  assert.equal(JSON.parse(calls[1].body).voice_id, "voice_592");
  const cases = [
    [{ audio_data: "pas du base64 !" }, "Réponse de synthèse Voxtral illisible."],
    [{ audio_data: base64(audio).replace(/=+$/, "") + "A" }, "Réponse de synthèse Voxtral illisible."],
    [{ audio_data: null }, "Réponse de synthèse Voxtral illisible."],
    [{}, "Audio Voxtral invalide."],
    [{ audio_data: base64(encoder.encode("ID3".padEnd(100, "x"))) }, "Audio Voxtral invalide."],
  ];
  for (const [answer, message] of cases) {
    const bad = new V.MistralVoice(KEY, "voice_589", { fetchImpl: server(json(answer)).fetchImpl });
    assert.equal((await failure(bad.speak("Salut"))).message, message);
  }
});

test("HTTP failures of a voice service are told like Python's _send", async () => {
  const engine = (reply) => new V.MistralVoice(KEY, "v", { fetchImpl: server(reply).fetchImpl });
  const cases = [
    [status(401), 502, "Clé refusée par Mistral : vérifie-la dans les réglages de la voix."],
    [status(403), 502, "Clé refusée par Mistral : vérifie-la dans les réglages de la voix."],
    [status(429), 503, "Limite ou crédit atteint chez Mistral."],
    [status(500), 502, "Mistral a répondu par une erreur 500."],
    [() => { throw new TypeError("Failed to fetch"); }, 503, "Mistral est injoignable."],
    [bytes(new Uint8Array(65537)), 502, "Réponse de Mistral trop volumineuse."],
  ];
  for (const [reply, code, message] of cases) {
    const error = await failure(engine(reply).transcribe(wav(0.2)));
    assert.ok(error instanceof V.VoiceError);
    assert.deepEqual([error.status, error.message], [code, message]);
    assert.ok(!error.message.includes(KEY));
  }
  const controller = new AbortController();
  const hanging = new V.MistralVoice(KEY, "v", { fetchImpl: () => new Promise(() => {}) });
  setTimeout(() => controller.abort(), 10);
  const error = await failure(hanging.speak("Salut", null, { signal: controller.signal }));
  assert.deepEqual([error.status, error.message], [499, "Réponse interrompue."]);
});

test("ElevenLabs: transcription fields, synthesis URL and language, voices French first", async () => {
  const stt = server(json({ text: " oui " }));
  assert.equal(await new V.ElevenLabsVoice(KEY, "v", "eleven_flash_v2_5", { fetchImpl: stt.fetchImpl }).transcribe(wav(0.2), ["Python"]), "oui");
  assert.equal(stt.calls[0].url, "https://api.elevenlabs.io/v1/speech-to-text");
  assert.deepEqual(stt.calls[0].headers, { "xi-api-key": KEY });
  assert.deepEqual([...stt.calls[0].body.entries()].slice(0, 4),
    [["model_id", "scribe_v2"], ["language_code", "fr"], ["tag_audio_events", "false"], ["diarize", "false"]]);

  const mp3 = new Uint8Array(300);
  const tts = server(bytes(mp3), bytes(mp3), bytes(new Uint8Array(10)));
  const flash = new V.ElevenLabsVoice(KEY, "voix d'été/1", "eleven_flash_v2_5", { fetchImpl: tts.fetchImpl });
  assert.deepEqual(await flash.speak("Bonjour"), { audio: mp3, mime: "audio/mpeg" });
  assert.equal(tts.calls[0].url, "https://api.elevenlabs.io/v1/text-to-speech/voix%20d%27%C3%A9t%C3%A9%2F1?output_format=mp3_44100_128");
  assert.deepEqual(tts.calls[0].headers, { "Content-Type": "application/json", "xi-api-key": KEY, Accept: "audio/mpeg" });
  assert.deepEqual(JSON.parse(tts.calls[0].body), { text: "Bonjour", model_id: "eleven_flash_v2_5", language_code: "fr" });
  const multilingual = new V.ElevenLabsVoice(KEY, "abc", "eleven_multilingual_v2", { fetchImpl: tts.fetchImpl });
  await multilingual.speak("Bonjour", "other");
  assert.equal(tts.calls[1].url, "https://api.elevenlabs.io/v1/text-to-speech/other?output_format=mp3_44100_128");
  assert.deepEqual(JSON.parse(tts.calls[1].body), { text: "Bonjour", model_id: "eleven_multilingual_v2" });
  assert.equal((await failure(multilingual.speak("x"))).message, "Audio ElevenLabs invalide.");

  const items = [
    { voice_id: "b", name: "Zoé", labels: { language: "fr", gender: "female" }, category: "premade" },
    { voice_id: "a", name: "adam", labels: {} },
    { voice_id: "c", name: "Céline", verified_languages: [{ language: "en" }, { language: "fr" }] },
    { voice_id: "d", name: "Bob" , labels: { language: "en-US" } },
    { name: "sans identifiant" }, "pas un objet",
  ];
  const list = server(status(404), json({ voices: items }));
  const voices = await new V.ElevenLabsVoice(KEY, "", "m", { fetchImpl: list.fetchImpl }).voices();
  assert.deepEqual(list.calls.map((call) => call.url), ["https://api.elevenlabs.io/v2/voices?page_size=100", "https://api.elevenlabs.io/v1/voices"]);
  assert.deepEqual(voices.map((voice) => [voice.id, voice.french]), [["c", true], ["b", true], ["a", false], ["d", false]]);
  assert.deepEqual(voices[1], { id: "b", name: "Zoé", language: "fr", french: true, gender: "female", category: "premade" });
  assert.equal(voices[0].language, "en");
  const refused = server(status(401));
  const error = await failure(new V.ElevenLabsVoice(KEY, "", "m", { fetchImpl: refused.fetchImpl }).voices());
  assert.equal(error.message, "Clé refusée par ElevenLabs : vérifie-la dans les réglages de la voix.");
  assert.equal(refused.calls.length, 1);
  assert.equal((await failure(V.voices({ config: voiceConfig() }))).message, "Clé ElevenLabs manquante.");
  const byKey = server(json({ voices: [{ voice_id: "z", name: "Zoé", labels: { language: "fr" } }] }));
  assert.deepEqual((await V.elevenLabsVoices("autre-clé", { fetchImpl: byKey.fetchImpl })).map((voice) => voice.id), ["z"]);
  assert.deepEqual(byKey.calls[0].headers, { "xi-api-key": "autre-clé" });
  assert.equal((await failure(V.elevenLabsVoices(""))).status, 400);
});

test("the local voice talks to the bridge with the OpenAI audio API", async () => {
  assert.match(new V.LocalVoice({ url: "  " }).problem(), /^La voix locale n'est pas branchée : lance le pont vocal/);
  const audio = riff(80);
  const { fetchImpl, calls } = server(json({ text: "  une boucle  " }), bytes(audio));
  const engine = new V.LocalVoice({ url: "127.0.0.1:5111/v1/", token: "code-1234", voice: "jessica", fetchImpl });
  assert.equal(engine.problem(), null);
  const terms = ["Kenobi", "Camille", ...Array.from({ length: 60 }, (_, index) => `terme${index}`)];
  assert.equal(await engine.transcribe(wav(0.2), terms), "une boucle");
  assert.equal(calls[0].url, "http://127.0.0.1:5111/v1/audio/transcriptions");
  assert.deepEqual(calls[0].headers, { Authorization: "Bearer code-1234" });
  const prompt = ("Séance de tutorat en informatique. Vocabulaire : " + terms.join(", ") + ".").slice(0, 380);
  assert.deepEqual([...calls[0].body.entries()].slice(0, 3), [["model", "whisper"], ["language", "fr"], ["prompt", prompt]]);
  assert.equal(calls[0].body.get("file").name, "voix.wav");
  assert.deepEqual(await engine.speak("Salut"), { audio, mime: "audio/wav" });
  assert.equal(calls[1].url, "http://127.0.0.1:5111/v1/audio/speech");
  assert.deepEqual(calls[1].headers, { "Content-Type": "application/json", Authorization: "Bearer code-1234" });
  assert.deepEqual(JSON.parse(calls[1].body), { model: "piper", input: "Salut", voice: "jessica", response_format: "wav" });

  const plain = server(json({ text: "oui" }));
  await new V.LocalVoice({ url: "http://localhost:9000", fetchImpl: plain.fetchImpl }).transcribe(wav(0.2), []);
  assert.deepEqual(plain.calls[0].headers, {});
  assert.equal(plain.calls[0].body.get("prompt"), null);
  const cases = [
    [status(401), 502, "Code refusé par le pont vocal : recolle le code affiché dans sa fenêtre."],
    [() => { throw new TypeError("Failed to fetch"); }, 503, "Le pont vocal de cet ordinateur ne répond pas : est-il lancé ?"],
    [status(500), 502, "Le pont vocal a répondu par une erreur 500."],
    [bytes("pas un wav, pas du tout, vraiment pas du tout, non"), 502, "Synthèse locale impossible."],
  ];
  for (const [reply, code, message] of cases) {
    const error = await failure(new V.LocalVoice({ url: "http://127.0.0.1:5111", fetchImpl: server(reply).fetchImpl }).speak("Salut"));
    assert.deepEqual([error.status, error.message], [code, message]);
  }
});

test("the engine, the status and the test sentence follow the settings", async () => {
  const [local, localVoice] = V.createEngine(voiceConfig());
  assert.deepEqual([local.name, local.label, localVoice], ["local", "Voix locale", "pierre"]);
  const [mistral, mistralVoice] = V.createEngine(voiceConfig({ engine: "mistral", mistral: { apiKey: KEY, voice: "voice_3141" } }));
  assert.deepEqual([mistral.name, mistral.key, mistralVoice], ["mistral", KEY, "voice_3141"]);
  const [eleven, elevenVoice] = V.createEngine(voiceConfig({ engine: "elevenlabs", elevenlabs: { apiKey: KEY, voice: "x", model: "m" } }));
  assert.deepEqual([eleven.name, eleven.model, elevenVoice], ["elevenlabs", "m", "x"]);

  const off = V.status({ config: voiceConfig({ local: { voice: "pierre", url: "" } }) });
  assert.deepEqual(off, {
    engine: "local", label: "Voix locale", ready: false, problem: new V.LocalVoice().problem(),
    local: { installed: false, running: false, installing: false, step: "", stepLabel: "", percent: null, error: "",
      voice: "", stt: "", folder: "", command: "" },
    fillers: true, fillerDelayMs: Shared.voice.FILLER_DELAY_MS,
  });
  assert.equal(V.status({ config: voiceConfig() }).local.installed, true);
  const noKey = V.status({ config: voiceConfig({ engine: "mistral" }) });
  assert.deepEqual([noKey.ready, noKey.label, noKey.problem], [false, "Voxtral (Mistral)", "Voix Voxtral : il manque la clé d'API Mistral dans les réglages de la voix."]);
  const error = await failure(V.test({ config: voiceConfig({ engine: "mistral" }) }));
  assert.deepEqual([error.status, error.name], [503, "VoiceError"]);

  const engine = new FakeEngine();
  const result = await V.test({ config: voiceConfig({ swissNumbers: true }), engine: [engine, "voix"] });
  assert.deepEqual(result, { audio: base64(encoder.encode("RIFF" + Shared.voice.TEST_SENTENCE)), mime: "audio/wav", text: Shared.voice.TEST_SENTENCE });
  await V.speak({ config: voiceConfig({ swissNumbers: true }), engine: [engine, "voix"], text: "75 lignes à 14h30" });
  assert.equal(engine.spoken[1], "septante-cinq lignes à quatorze heures trente");
});

test("greetings and fillers are said once in the chosen voice and kept", async () => {
  V._resetPhrases();
  const engine = new FakeEngine();
  const first = await V.phrases({ config: voiceConfig(), engine: [engine, "voix"], name: "<b>Camille</b> !" });
  assert.deepEqual(first.greetings.map((item) => item.text), ["Oui, bCamilleb ?", "Salut bCamilleb, je t'écoute.", "Je t'écoute, bCamilleb."]);
  assert.deepEqual(first.fillers.map((item) => item.text), Shared.voice.FILLERS);
  assert.deepEqual(Object.keys(first.greetings[0]), ["text", "mime", "audio"]);
  const count = engine.spoken.length;
  await V.phrases({ config: voiceConfig(), engine: [engine, "voix"], name: "<b>Camille</b> !" });
  assert.equal(engine.spoken.length, count);
  const plain = await V.phrases({ config: voiceConfig({ fillers: false }), engine: [engine, "voix"], name: "" });
  assert.deepEqual([plain.greetings[0].text, plain.fillers.length], ["Oui ?", 0]);

  V._resetPhrases();
  const broken = new FakeEngine({ failOn: "Voyons" });
  const none = await V.phrases({ config: voiceConfig(), engine: [broken, "voix"], name: "Léa" });
  assert.deepEqual(none, { greetings: [], fillers: [] });
  const spoken = broken.spoken.length;
  assert.deepEqual(await V.phrases({ config: voiceConfig(), engine: [broken, "voix"], name: "Léa" }), { greetings: [], fillers: [] });
  assert.equal(broken.spoken.length, spoken);  // a failure is not retried for a minute
  const silent = new FakeEngine({ problem: "Pas de voix." });
  assert.deepEqual(await V.phrases({ config: voiceConfig(), engine: [silent, "voix"], name: "Léa" }), { greetings: [], fillers: [] });
});

// ── Call turn ────────────────────────────────────────────────────────────

test("a spoken turn is transcribed, answered and said in order", async () => {
  const engine = new FakeEngine();
  const { events, seen } = await runTurn(engine, ["Une boucle répète. ", "Par exemple avec for. ", "Tu vois ?"],
    { audio: wav(), carry: "Kenobi,", terms: ["Python"] });
  const kinds = events.map(([kind]) => kind);
  assert.deepEqual(kinds.slice(0, 3), ["stage", "transcript", "stage"]);
  assert.deepEqual(events[0][1], { name: "transcription" });
  assert.deepEqual(events[2][1], { name: "answer" });
  assert.equal(kinds[kinds.length - 1], "done");
  const transcript = events[1][1];
  assert.deepEqual(Object.keys(transcript), ["text", "heard", "sttMs"]);
  assert.equal(transcript.text, "Kenobi, c'est quoi une boucle");
  assert.equal(seen.text, "Kenobi, c'est quoi une boucle");
  assert.deepEqual(engine.terms, ["Python"]);
  const segments = events.filter(([kind]) => kind === "segment").map(([, data]) => data);
  assert.deepEqual(segments.map((segment) => segment.index), [0, 1, 2]);
  assert.equal(segments.map((segment) => segment.text).join(" "), "Une boucle répète. Par exemple avec for. Tu vois?");
  assert.deepEqual(Buffer.from(segments[0].audio, "base64"), Buffer.from("RIFF" + segments[0].text));
  const done = events[events.length - 1][1];
  assert.deepEqual(Object.keys(done), ["reply", "segments", "voiceError", "firstAudio", "sttMs", "total", "elapsed", "firstToken"]);
  assert.deepEqual([done.reply, done.segments, done.voiceError], ["Une boucle répète. Par exemple avec for. Tu vois ?", 3, ""]);
  assert.equal(typeof done.firstAudio, "number");
  assert.equal(seen.signal.aborted, true);  // the turn is over: everything it started is released
});

test("noise or silence asks nothing of the tutor", async () => {
  const noise = await runTurn(new FakeEngine({ heard: "Sous-titres réalisés par la communauté d'Amara.org" }), ["x"], { audio: wav() });
  assert.deepEqual(noise.events, [["stage", { name: "transcription" }], ["empty", {}]]);
  assert.deepEqual(noise.seen, {});
  const silence = await runTurn(new FakeEngine({ heard: "" }), ["x"], { audio: wav() });
  assert.deepEqual(silence.events.map(([kind]) => kind), ["stage", "empty"]);
  const blank = await runTurn(new FakeEngine(), ["x"], { text: "   " });
  assert.deepEqual(blank.events, [["empty", {}]]);
});

test("a written turn during the call is spoken too, with Swiss numbers if chosen", async () => {
  const engine = new FakeEngine();
  const { events, seen } = await runTurn(engine, ["Presque. ", "Regarde la ligne 72."], { text: "[Exécution — tentative n° 1 …]",
    config: { swissNumbers: true } });
  assert.equal(seen.text, "[Exécution — tentative n° 1 …]");
  assert.deepEqual(events[0], ["transcript", { text: "[Exécution — tentative n° 1 …]", heard: "[Exécution — tentative n° 1 …]", sttMs: null }]);
  assert.deepEqual(engine.spoken, ["Presque.", "Regarde la ligne septante-deux."]);
  assert.deepEqual(events.filter(([kind]) => kind === "segment").map(([, data]) => data.text), ["Presque.", "Regarde la ligne 72."]);
  assert.equal(events[events.length - 1][1].sttMs, null);
});

test("when the voice breaks, the text still arrives and nothing more is said", async () => {
  const engine = new FakeEngine({ failOn: "ligne" });
  const { events } = await runTurn(engine, ["Presque. ", "Regarde la ligne 2. ", "Courage."], { audio: wav() });
  const [kind, done] = events[events.length - 1];
  assert.equal(kind, "done");
  assert.equal(done.reply, "Presque. Regarde la ligne 2. Courage.");
  assert.equal(done.voiceError, "Voix en panne.");
  assert.equal(done.segments, 1);
  assert.deepEqual(engine.spoken, ["Presque."]);
});

test("without a voice: a spoken turn is an error, a written turn stays on screen", async () => {
  const silent = new FakeEngine({ problem: "La voix locale n'est pas branchée." });
  const spoken = await runTurn(silent, ["x"], { audio: wav() });
  assert.deepEqual(spoken.events, [["error", { message: "La voix locale n'est pas branchée.", status: 503 }]]);
  const written = await runTurn(silent, ["Bien. ", "Continue."], { text: "Run" });
  const done = written.events[written.events.length - 1][1];
  assert.deepEqual([done.segments, done.voiceError, done.firstAudio], [0, "La voix locale n'est pas branchée.", null]);
  assert.equal(written.events.filter(([kind]) => kind === "segment").length, 0);
});

test("errors become an error event with the message and status of the application, or a generic one", async () => {
  const unreadable = await runTurn(new FakeEngine(), ["x"], { audio: encoder.encode("pas un wav") });
  assert.deepEqual(unreadable.events, [["error", { message: "Enregistrement audio illisible.", status: 400 }]]);
  const chatError = Object.assign(new Error("Aucun modèle d'IA n'est branché."), { name: "ChatError", status: 409 });
  const failing = async function* () { throw chatError; };
  let events = [];
  for await (const event of V.turn({ config: voiceConfig(), engine: [new FakeEngine(), "v"], startReply: failing, text: "Salut" })) events.push(event);
  assert.deepEqual(events[events.length - 1], ["error", { message: "Aucun modèle d'IA n'est branché.", status: 409 }]);
  const bug = () => { throw new TypeError("x is not a function"); };
  events = [];
  for await (const event of V.turn({ config: voiceConfig(), engine: [new FakeEngine(), "v"], startReply: bug, text: "Salut" })) events.push(event);
  assert.deepEqual(events[events.length - 1], ["error", { message: "Erreur pendant l'appel.", status: 500 }]);
});

test("closing the turn stops the model and the voice", async () => {
  const engine = new FakeEngine({ delay: 20 });
  const state = {};
  async function* startReply(_text, signal) {
    state.signal = signal;
    for (let index = 0; index < 200; index += 1) {
      if (signal.aborted) {
        state.stoppedAt = index;
        return;
      }
      yield ["delta", { text: `Phrase numéro ${index}. ` }];
      await sleep(5);
    }
    yield ["done", {}];
  }
  for await (const [kind] of V.turn({ config: voiceConfig(), engine: [engine, "v"], startReply, audio: wav() })) {
    if (kind === "segment") break;
  }
  assert.equal(state.signal.aborted, true);
  await sleep(60);
  assert.ok(state.stoppedAt === undefined || state.stoppedAt < 200);
  const spoken = engine.spoken.length;
  await sleep(100);
  assert.ok(engine.spoken.length <= spoken + 1);
  assert.ok(engine.signals.every((signal) => signal.aborted));
});

test("aborting the signal ends the turn at once, without more events", async () => {
  const controller = new AbortController();
  async function* startReply(_text, signal) {
    yield ["delta", { text: "Un. " }];
    await new Promise((resolve) => signal.addEventListener("abort", resolve, { once: true }));
    yield ["delta", { text: "Jamais lu. " }];
  }
  const events = [];
  for await (const event of V.turn({ config: voiceConfig(), engine: [new FakeEngine(), "v"], startReply, text: "Salut", signal: controller.signal })) {
    events.push(event);
    if (event[0] === "delta") controller.abort();
  }
  assert.deepEqual(events.map(([kind]) => kind).filter((kind) => kind !== "segment"), ["transcript", "stage", "delta"]);
});
