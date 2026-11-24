// Kenobi's voice in the browser: hearing (transcription) and speaking (synthesis) during a call. Port of voice.py.
// Engines: "mistral" (Voxtral) and "elevenlabs" by their APIs, and "local", a speech server on this computer that
// speaks the OpenAI audio API (Kenobi's voice bridge). The call turn follows voice.py: the sentence is transcribed,
// the model answers in a stream, each finished sentence is said while the rest is written, in order.
(function (root, factory) {
  const api = factory(root);
  if (typeof module === "object" && module.exports) module.exports = api;
  root.KenobiVoice = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function (root) {
  "use strict";
  const node = typeof module === "object" && module.exports;
  const Shared = node ? require("./shared.js") : root.KenobiShared;
  const Providers = node ? require("./providers.js") : root.KenobiProviders;
  const Speech = node ? require("./speech.js") : root.KenobiSpeech;
  const Py = Providers._py;
  const Net = Providers._net;
  const V = Shared.voice;
  const { MAX_AUDIO, MAX_SECONDS, MAX_SPOKEN, FILLER_DELAY_MS, MISTRAL_API, MISTRAL_STT_MODEL, MISTRAL_TTS_MODEL,
    ELEVEN_API, ELEVEN_STT_MODEL, FILLERS, GREETINGS_NAMED, GREETINGS, TEST_SENTENCE } = V;
  const MiB = 1024 * 1024;
  const LOCAL_TIMEOUT = 150000;
  const PCM_SUBFORMAT = [0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x10, 0x00, 0x80, 0x00, 0x00, 0xaa, 0x00, 0x38, 0x9b, 0x71];

  // What speech recognitions write on noise or silence: never sent to the tutor (Python's re.I, \w and `$`).
  const W = Py.WORD;
  const ci = Py.ignoreCase;
  const NOISE_TRANSCRIPTS = new RegExp(`^[^${W}]*(${ci("sous-titr")}[${W}]*[^\\n]*|${ci("merci d'avoir regardé")}[^\\n]*`
    + `|${ci("abonnez-vous")}[^\\n]*|${ci("amara.org")}[^\\n]*|\\.{2,}|…|\\[[^\\n]*\\]|\\([^\\n]*\\))[^${W}]*(?=\\n?$)`, "u");
  const CONTEXT_TERM = new RegExp(`^[${W}'’.-]{2,40}$`, "u");
  const NAME_UNSAFE = new RegExp(`[^${W}' -]`, "gu");

  class VoiceError extends Error {
    constructor(message, status = 502) {
      super(message);
      this.name = "VoiceError";
      this.status = status;
      this.message = message;
    }
  }

  const clock = root.performance && typeof root.performance.now === "function"
    ? () => root.performance.now() / 1000 : () => Date.now() / 1000;
  const round2 = (seconds) => Number(seconds.toFixed(2));

  function toBytes(value) {
    if (value instanceof Uint8Array) return value;
    if (value instanceof ArrayBuffer) return new Uint8Array(value);
    if (ArrayBuffer.isView(value)) return new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
    if (Array.isArray(value)) return Uint8Array.from(value);
    return null;
  }

  function base64(data) {
    const bytes = toBytes(data) || new Uint8Array(0);
    let binary = "";
    for (let index = 0; index < bytes.length; index += 0x8000) {
      binary += String.fromCharCode.apply(null, bytes.subarray(index, index + 0x8000));
    }
    return btoa(binary);
  }

  // base64.b64decode(text, validate=True): only canonical base64, or null.
  function fromBase64(text) {
    if (typeof text !== "string" || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(text)) return null;
    const binary = atob(text);
    const bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
    return bytes;
  }

  const isRiff = (bytes) => bytes.length >= 4 && bytes[0] === 0x52 && bytes[1] === 0x49 && bytes[2] === 0x46 && bytes[3] === 0x46;

  // ── WAV check: the reading rules of Python's wave module ─────────────────

  class WavEOF extends Error {}
  class WavFormat extends Error {}

  function wavFile(bytes) {
    return {
      position: 0,
      read(size) {
        const out = bytes.subarray(this.position, Math.min(bytes.length, this.position + Math.max(0, size)));
        this.position += out.length;
        return out;
      },
      seek(position, whence = 0) {
        this.position = whence === 1 ? this.position + position : position;
      },
      tell() {
        return this.position;
      },
    };
  }

  // wave._Chunk: a RIFF chunk read through its parent, word-aligned.
  class WavChunk {
    constructor(file) {
      this.file = file;
      const name = file.read(4);
      if (name.length < 4) throw new WavEOF();
      this.name = String.fromCharCode(...name);
      const size = file.read(4);
      if (size.length < 4) throw new WavEOF();
      this.size = new DataView(size.buffer, size.byteOffset, 4).getUint32(0, true);
      this.done = 0;
      this.offset = file.tell();
    }

    read(size = -1) {
      if (this.done >= this.size) return new Uint8Array(0);
      if (size < 0 || size > this.size - this.done) size = this.size - this.done;
      const data = this.file.read(size);
      this.done += data.length;
      if (this.done === this.size && this.size % 2) this.done += this.file.read(1).length;
      return data;
    }

    tell() {
      return this.done;
    }

    seek(position, whence = 0) {
      if (whence === 1) position += this.done;
      else if (whence === 2) position += this.size;
      if (position < 0 || position > this.size) throw new Error("RuntimeError");  // not caught by check_wav either
      this.file.seek(this.offset + position, 0);
      this.done = position;
    }

    skip() {
      const step = this.size - this.done + (this.size % 2);
      this.file.seek(step, 1);
      this.done += step;
    }
  }

  function readFormat(chunk) {
    const head = chunk.read(14);
    if (head.length < 14) throw new WavEOF();
    const view = new DataView(head.buffer, head.byteOffset, 14);
    const format = view.getUint16(0, true);
    const channels = view.getUint16(2, true);
    const rate = view.getUint32(4, true);
    if (format !== 0x0001 && format !== 0xfffe) throw new WavFormat("unknown format");
    const bits = chunk.read(2);
    if (bits.length < 2) throw new WavEOF();
    const width = Math.floor((new DataView(bits.buffer, bits.byteOffset, 2).getUint16(0, true) + 7) / 8);
    if (format === 0xfffe) {
      if (chunk.read(8).length < 8) throw new WavEOF();
      const subformat = chunk.read(16);
      if (subformat.length < 16) throw new WavEOF();
      if (PCM_SUBFORMAT.some((byte, index) => subformat[index] !== byte)) throw new WavFormat("unknown extended format");
    }
    if (!width) throw new WavFormat("bad sample width");
    if (!channels) throw new WavFormat("bad # of channels");
    return { channels, rate, width };
  }

  function readWav(bytes) {
    const riff = new WavChunk(wavFile(bytes));
    if (riff.name !== "RIFF") throw new WavFormat("file does not start with RIFF id");
    if (String.fromCharCode(...riff.read(4)) !== "WAVE") throw new WavFormat("not a WAVE file");
    let format = null, frames = null;
    while (true) {
      let chunk;
      try {
        chunk = new WavChunk(riff);
      } catch (error) {
        if (error instanceof WavEOF) break;
        throw error;
      }
      if (chunk.name === "fmt ") {
        format = readFormat(chunk);
      } else if (chunk.name === "data") {
        if (!format) throw new WavFormat("data chunk before fmt chunk");
        frames = Math.floor(chunk.size / (format.channels * format.width));
        break;
      }
      chunk.skip();
    }
    if (!format || frames === null) throw new WavFormat("fmt chunk and/or data chunk missing");
    return { ...format, frames };
  }

  /** Length in seconds of a 16-bit WAV recording sent by the page, or VoiceError (same rules as check_wav). */
  function checkWav(audio) {
    const bytes = toBytes(audio);
    if (!bytes || !bytes.length || bytes.length > MAX_AUDIO) throw new VoiceError("Enregistrement vide ou trop volumineux.", 413);
    let wav;
    try {
      wav = readWav(bytes);
    } catch (error) {
      if (error instanceof WavEOF || error instanceof WavFormat) throw new VoiceError("Enregistrement audio illisible.", 400);
      throw error;
    }
    if (wav.width !== 2 || (wav.channels !== 1 && wav.channels !== 2)) throw new VoiceError("Format audio inattendu.", 400);
    const seconds = wav.frames / Math.max(1, wav.rate);
    if (seconds > MAX_SECONDS) throw new VoiceError("Phrase trop longue (60 secondes au plus).", 413);
    return seconds;
  }

  // ── HTTP ────────────────────────────────────────────────────────────────

  // Python's _send: the status code only (the body and headers may repeat the request), size limit, short timeouts.
  async function send(url, init, { timeout, limit, service, messages = {}, signal = null, fetchImpl = null }) {
    const controller = new AbortController();
    let cancelled = false;
    const onAbort = () => {
      cancelled = true;
      controller.abort();
    };
    if (signal) {
      if (signal.aborted) onAbort();
      else signal.addEventListener("abort", onAbort, { once: true });
    }
    const interrupted = () => new VoiceError("Réponse interrompue.", 499);
    const unreachable = () => new VoiceError(messages.unreachable || `${service} est injoignable.`, 503);
    const timer = setTimeout(() => controller.abort(), timeout);
    try {
      if (cancelled) throw interrupted();
      let response;
      try {
        response = await Net.abortable(Net.fetcher(fetchImpl)(url, { ...init, signal: controller.signal }), controller.signal);
      } catch (_error) {
        throw cancelled ? interrupted() : unreachable();
      }
      clearTimeout(timer);
      if (!response.ok) {
        if (response.body) response.body.cancel().catch(() => {});
        const code = response.status;
        if (code === 401 || code === 403) {
          throw new VoiceError(messages.auth || `Clé refusée par ${service} : vérifie-la dans les réglages de la voix.`);
        }
        if (code === 429) throw new VoiceError(messages.rate || `Limite ou crédit atteint chez ${service}.`, 503);
        throw new VoiceError(messages.status ? messages.status(code) : `${service} a répondu par une erreur ${code}.`);
      }
      let raw;
      try {
        raw = await Net.readAtMost(response, limit + 1, timeout, controller.signal);
      } catch (_error) {
        throw cancelled ? interrupted() : unreachable();
      }
      if (raw.length > limit) throw new VoiceError(messages.tooLarge || `Réponse de ${service} trop volumineuse.`);
      return raw;
    } finally {
      clearTimeout(timer);
      if (signal) signal.removeEventListener("abort", onAbort);
      controller.abort();
    }
  }

  // json.loads(bytes): strict UTF-8 (a leading byte order mark is allowed), NaN and Infinity read.
  function loadsBytes(raw) {
    let text;
    try {
      text = new TextDecoder("utf-8", { fatal: true }).decode(raw);
    } catch (_error) {
      return undefined;
    }
    return Py.loads(text);
  }

  function form(fields, audio) {
    const body = new FormData();
    for (const [name, value] of fields) body.append(name, value);
    body.append("file", new Blob([toBytes(audio) || new Uint8Array(0)], { type: "audio/wav" }), "voix.wav");
    return body;
  }

  // urllib.parse.quote(text, safe=""): only letters, digits and _.-~ stay as they are.
  function quote(text) {
    return encodeURIComponent(text).replace(/[!'()*]/g, (char) => "%" + char.charCodeAt(0).toString(16).toUpperCase());
  }

  function transcriptOf(raw, unreadable) {
    const data = loadsBytes(raw);
    if (!Py.isDict(data)) throw new VoiceError(unreadable);
    const text = Py.get(data, "text");
    return typeof text === "string" ? Py.strip(text) : "";
  }

  // ── Local voice: the speech server of this computer (OpenAI audio API) ───

  const LOCAL_MESSAGES = {
    auth: "Code refusé par le pont vocal : recolle le code affiché dans sa fenêtre.",
    unreachable: "Le pont vocal de cet ordinateur ne répond pas : est-il lancé ?",
    rate: "Le pont vocal est occupé : réessaie dans un instant.",
    status: (code) => `Le pont vocal a répondu par une erreur ${code}.`,
    tooLarge: "Réponse du pont vocal trop volumineuse.",
  };

  function bridgeUrl(url) {
    let text = String(url || "").trim().replace(/\/+$/, "").replace(/\/v1$/, "");
    if (text && !/^https?:\/\//i.test(text)) text = "http://" + text;
    return text;
  }

  class LocalVoice {
    constructor({ url = "", token = "", voice = "", fetchImpl = null } = {}) {
      this.name = "local";
      this.label = "Voix locale";
      this.url = bridgeUrl(url);
      this.token = String(token || "").trim();
      this.voice = voice || "";
      this.fetchImpl = fetchImpl;
    }

    problem() {
      if (this.url) return null;
      return "La voix locale n'est pas branchée : lance le pont vocal de Kenobi sur ton ordinateur, puis colle son code dans les réglages de la voix.";
    }

    headers(json) {
      const headers = {};
      if (json) headers["Content-Type"] = "application/json";
      if (this.token) headers.Authorization = "Bearer " + this.token;
      return headers;
    }

    async transcribe(audio, terms = [], { signal = null } = {}) {
      const words = Array.from(terms || []);
      // Whisper follows a piece of sentence better than a dry list: the course words are given in context.
      const prompt = words.length ? Py.slice("Séance de tutorat en informatique. Vocabulaire : " + words.join(", ") + ".", 380) : "";
      const fields = [["model", "whisper"], ["language", "fr"]];
      if (prompt) fields.push(["prompt", prompt]);
      const raw = await send(this.url + "/v1/audio/transcriptions",
        { method: "POST", headers: this.headers(false), body: form(fields, audio) },
        { timeout: LOCAL_TIMEOUT, limit: 65536, service: "Le pont vocal", messages: LOCAL_MESSAGES, signal, fetchImpl: this.fetchImpl });
      const data = loadsBytes(raw);
      if (!Py.isDict(data)) throw new VoiceError("Transcription locale impossible.");
      const text = Py.get(data, "text");
      return Py.strip(Py.str(Py.truthy(text) ? text : ""));
    }

    async speak(text, voice = null, { signal = null } = {}) {
      const payload = { model: "piper", input: Py.slice(String(text), MAX_SPOKEN), voice: voice || this.voice || "pierre",
        response_format: "wav" };
      const audio = await send(this.url + "/v1/audio/speech",
        { method: "POST", headers: this.headers(true), body: JSON.stringify(payload) },
        { timeout: LOCAL_TIMEOUT, limit: 64 * MiB, service: "Le pont vocal", messages: LOCAL_MESSAGES, signal, fetchImpl: this.fetchImpl });
      if (audio.length < 44 || !isRiff(audio)) throw new VoiceError("Synthèse locale impossible.");
      return { audio, mime: "audio/wav" };
    }
  }

  // ── Voxtral (Mistral) ───────────────────────────────────────────────────

  class MistralVoice {
    constructor(key, voice, { fetchImpl = null } = {}) {
      this.name = "mistral";
      this.label = "Voxtral (Mistral)";
      this.key = key;
      this.voice = voice;
      this.fetchImpl = fetchImpl;
    }

    problem() {
      return this.key ? null : "Voix Voxtral : il manque la clé d'API Mistral dans les réglages de la voix.";
    }

    async transcribe(audio, terms = [], { signal = null } = {}) {
      const fields = [["model", MISTRAL_STT_MODEL], ["language", "fr"]];
      for (const term of Array.from(terms || []).slice(0, 24)) {
        if (typeof term === "string" && CONTEXT_TERM.test(term)) fields.push(["context_bias", term]);
      }
      const raw = await send(MISTRAL_API + "/audio/transcriptions",
        { method: "POST", headers: { Authorization: "Bearer " + this.key }, body: form(fields, audio) },
        { timeout: 60000, limit: 65536, service: "Mistral", signal, fetchImpl: this.fetchImpl });
      return transcriptOf(raw, "Réponse de transcription Voxtral illisible.");
    }

    async speak(text, voice = null, { signal = null } = {}) {
      const payload = { model: MISTRAL_TTS_MODEL, input: Py.slice(String(text), MAX_SPOKEN), voice_id: voice || this.voice,
        response_format: "wav" };
      const raw = await send(MISTRAL_API + "/audio/speech",
        { method: "POST", headers: { "Content-Type": "application/json", Authorization: "Bearer " + this.key },
          body: JSON.stringify(payload) },
        { timeout: 60000, limit: 64 * MiB, service: "Mistral", signal, fetchImpl: this.fetchImpl });
      const data = loadsBytes(raw);
      const audio = Py.isDict(data) ? fromBase64(Py.has(data, "audio_data") ? data.audio_data : "") : null;
      if (!audio) throw new VoiceError("Réponse de synthèse Voxtral illisible.");
      if (audio.length < 44 || !isRiff(audio)) throw new VoiceError("Audio Voxtral invalide.");
      return { audio, mime: "audio/wav" };
    }
  }

  // ── ElevenLabs ──────────────────────────────────────────────────────────

  class ElevenLabsVoice {
    constructor(key, voice, model, { fetchImpl = null } = {}) {
      this.name = "elevenlabs";
      this.label = "ElevenLabs";
      this.key = key;
      this.voice = voice;
      this.model = model;
      this.fetchImpl = fetchImpl;
    }

    problem() {
      if (!this.key) return "Voix ElevenLabs : il manque la clé d'API dans les réglages de la voix.";
      if (!this.voice) return "Voix ElevenLabs : choisis une voix dans les réglages.";
      return null;
    }

    async transcribe(audio, terms = [], { signal = null } = {}) {
      const fields = [["model_id", ELEVEN_STT_MODEL], ["language_code", "fr"], ["tag_audio_events", "false"], ["diarize", "false"]];
      const raw = await send(ELEVEN_API + "/v1/speech-to-text",
        { method: "POST", headers: { "xi-api-key": this.key }, body: form(fields, audio) },
        { timeout: 60000, limit: MiB, service: "ElevenLabs", signal, fetchImpl: this.fetchImpl });
      return transcriptOf(raw, "Réponse de transcription ElevenLabs illisible.");
    }

    async speak(text, voice = null, { signal = null } = {}) {
      const payload = { text: Py.slice(String(text), MAX_SPOKEN), model_id: this.model };
      if (/flash|turbo/u.test(String(this.model))) payload.language_code = "fr";
      const url = `${ELEVEN_API}/v1/text-to-speech/${quote(voice || this.voice || "")}?output_format=mp3_44100_128`;
      const audio = await send(url,
        { method: "POST", headers: { "Content-Type": "application/json", "xi-api-key": this.key, Accept: "audio/mpeg" },
          body: JSON.stringify(payload) },
        { timeout: 60000, limit: 32 * MiB, service: "ElevenLabs", signal, fetchImpl: this.fetchImpl });
      if (audio.length < 200) throw new VoiceError("Audio ElevenLabs invalide.");
      return { audio, mime: "audio/mpeg" };
    }

    /** The account's voices: [{id, name, language, french, gender, category}], French ones first. */
    async voices({ signal = null } = {}) {
      let raw = null;
      for (const path of ["/v2/voices?page_size=100", "/v1/voices"]) {
        try {
          raw = await send(ELEVEN_API + path, { method: "GET", headers: { "xi-api-key": this.key } },
            { timeout: 30000, limit: 8 * MiB, service: "ElevenLabs", signal, fetchImpl: this.fetchImpl });
          break;
        } catch (error) {
          if (!(error instanceof VoiceError) || error.status === 499 || error.message.includes("Clé refusée")
              || path === "/v1/voices") throw error;
        }
      }
      const data = loadsBytes(raw);
      if (!Py.isDict(data)) throw new VoiceError("Liste des voix ElevenLabs illisible.");
      const items = Py.truthy(Py.get(data, "voices")) ? data.voices : [];
      const voices = [];
      for (const item of Py.iterate(items)) {
        if (!Py.isDict(item) || !Py.truthy(Py.get(item, "voice_id"))) continue;
        const labels = Py.isDict(Py.get(item, "labels")) ? item.labels : {};
        const verified = Py.truthy(Py.get(item, "verified_languages")) ? item.verified_languages : [];
        const languages = Py.iterate(verified).filter(Py.isDict)
          .map((entry) => Py.str(Py.truthy(Py.get(entry, "language")) ? entry.language : ""));
        const language = Py.str(Py.truthy(Py.get(labels, "language")) ? labels.language : languages.length ? languages[0] : "");
        voices.push({
          id: Py.str(item.voice_id),
          name: Py.slice(Py.str(Py.truthy(Py.get(item, "name")) ? item.name : item.voice_id), 60),
          language: Py.slice(language, 12),
          french: language.startsWith("fr") || languages.includes("fr"),
          gender: Py.slice(Py.str(Py.truthy(Py.get(labels, "gender")) ? labels.gender : ""), 12),
          category: Py.slice(Py.str(Py.truthy(Py.get(item, "category")) ? item.category : ""), 20),
        });
      }
      voices.sort((a, b) => (a.french !== b.french ? (a.french ? -1 : 1) : Py.compare(a.name.toLowerCase(), b.name.toLowerCase())));
      return voices.slice(0, 200);
    }
  }

  // ── Engine choice ───────────────────────────────────────────────────────

  /** [engine, voiceId] for the resolved voice config, like VoiceManager.engine. */
  function createEngine(config, { fetchImpl = null } = {}) {
    if (config.engine === "mistral") {
      return [new MistralVoice(config.mistral.apiKey, config.mistral.voice, { fetchImpl }), config.mistral.voice];
    }
    if (config.engine === "elevenlabs") {
      const eleven = config.elevenlabs;
      return [new ElevenLabsVoice(eleven.apiKey, eleven.voice, eleven.model, { fetchImpl }), eleven.voice];
    }
    const local = config.local || {};
    return [new LocalVoice({ url: local.url, token: local.token, voice: local.voice, fetchImpl }), local.voice];
  }

  // `engine` (tests, or a caller with its own engine): an engine object, or [engine, voiceId].
  function chooseEngine(config, engine, fetchImpl) {
    if (Array.isArray(engine)) return engine;
    if (engine) return [engine, config ? createEngine(config)[1] : undefined];
    return createEngine(config, { fetchImpl });
  }

  const spoken = (text, config) => (Py.truthy(config && config.swissNumbers) ? Speech.spokenFr(text) : text);

  // An error of the application carries its message and status; anything else is a fault, said generically.
  function isAppError(error) {
    return Boolean(error) && (typeof error.status === "number"
      || ["VoiceError", "ProviderError", "ChatError", "ApiError"].includes(error.name));
  }

  const messageOf = (error, fallback) => (isAppError(error) && error.message) || fallback;
  const statusOf = (error, fallback) => (error && typeof error.status === "number" ? error.status : fallback);

  /** The voice chosen in the settings says `text`: {audio: Uint8Array, mime}. */
  async function speak({ config, text, signal = null, engine = null, fetchImpl = null } = {}) {
    const [voiceEngine, voice] = chooseEngine(config, engine, fetchImpl);
    const problem = voiceEngine.problem();
    if (problem) throw new VoiceError(problem, 503);
    return voiceEngine.speak(spoken(text, config), voice, { signal });
  }

  /** The settings' test sentence: {audio: base64, mime, text}. */
  async function test(options = {}) {
    const { audio, mime } = await speak({ ...options, text: TEST_SENTENCE });
    return { audio: base64(audio), mime, text: TEST_SENTENCE };
  }

  /** ElevenLabs voices of the given key, or of the key in the settings. */
  async function voices({ config = null, apiKey = "", signal = null, fetchImpl = null } = {}) {
    const eleven = (config && config.elevenlabs) || {};
    const key = Py.truthy(apiKey) ? apiKey : eleven.apiKey;
    if (!Py.truthy(key)) throw new VoiceError("Clé ElevenLabs manquante.", 400);
    return new ElevenLabsVoice(key, eleven.voice, eleven.model, { fetchImpl }).voices({ signal });
  }

  /** The same, for a key only (the voice and model chosen do not change the list). */
  function elevenLabsVoices(apiKey, options = {}) {
    return voices({ ...options, apiKey });
  }

  /** Same shape as VoiceManager.status(). */
  function status({ config, engine = null } = {}) {
    const [voiceEngine] = chooseEngine(config, engine, null);
    const problem = voiceEngine.problem();
    const url = bridgeUrl(config.local && config.local.url);
    return {
      engine: config.engine, label: voiceEngine.label, ready: problem === null || problem === undefined, problem: problem || "",
      local: { installed: Boolean(url), running: false, installing: false, step: "", stepLabel: "", percent: null,
        error: "", voice: "", stt: "", folder: "", command: "" },
      fillers: config.fillers, fillerDelayMs: FILLER_DELAY_MS,
    };
  }

  // Greetings and waiting phrases already said in the chosen voice: kept in memory (one voice at a time).
  const phraseCache = new Map();

  async function mapInOrder(items, limit, work, onFailure) {
    const results = new Array(items.length);
    let next = 0, failure = null;
    const worker = async () => {
      while (next < items.length && !failure) {
        const index = next++;
        try {
          results[index] = await work(items[index]);
        } catch (error) {
          if (!failure) {
            failure = { error };
            onFailure();
          }
        }
      }
    };
    await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker));
    if (failure) throw failure.error;
    return results;
  }

  /** {greetings, fillers}: [{text, mime, audio: base64}], built once per voice and name. */
  async function phrases({ config, name = "", signal = null, engine = null, fetchImpl = null } = {}) {
    const [voiceEngine, voice] = chooseEngine(config, engine, fetchImpl);
    if (voiceEngine.problem()) return { greetings: [], fillers: [] };
    const clean = Py.slice(Py.strip(String(name || "").replace(NAME_UNSAFE, "")), 30);
    const key = JSON.stringify([config.engine, voice, clean, config.swissNumbers, config.fillers]);
    const cached = phraseCache.get(key);
    if (cached && (cached.greetings.length || clock() - cached.at < 60)) {
      return { greetings: cached.greetings, fillers: cached.fillers };
    }
    const greetings = clean ? GREETINGS_NAMED.map((text) => text.split("{name}").join(clean)) : [...GREETINGS];
    const texts = greetings.map((text) => ["greetings", text])
      .concat(Py.truthy(config.fillers) ? FILLERS.map((text) => ["fillers", text]) : []);
    const stop = new AbortController();
    const onAbort = () => stop.abort();
    if (signal) {
      if (signal.aborted) stop.abort();
      else signal.addEventListener("abort", onAbort, { once: true });
    }
    let built = { greetings: [], fillers: [], at: clock() };
    try {
      // The local bridge says one sentence at a time; the APIs accept several at once.
      const entries = await mapInOrder(texts, voiceEngine instanceof LocalVoice ? 1 : 4, async ([kind, text]) => {
        const { audio, mime } = await voiceEngine.speak(spoken(text, config), voice, { signal: stop.signal });
        return [kind, { text, mime, audio: base64(audio) }];
      }, () => stop.abort());
      for (const [kind, entry] of entries) built[kind].push(entry);
    } catch (_error) {
      built = { greetings: [], fillers: [], at: clock() };  // never a half-said bank
    } finally {
      stop.abort();
      if (signal) signal.removeEventListener("abort", onAbort);
    }
    if (signal && signal.aborted) return { greetings: built.greetings, fillers: built.fillers };  // not kept: cut short
    phraseCache.clear();
    phraseCache.set(key, built);
    return { greetings: built.greetings, fillers: built.fillers };
  }

  // ── Call turn ───────────────────────────────────────────────────────────

  // Says the pieces in order, one at a time, and publishes each as soon as it is ready.
  // After a synthesis failure or a stop, nothing more is said: no gap, no piece out of order.
  class Speaker {
    constructor(synth, publish, signal) {
      this.synth = synth;
      this.publish = publish;
      this.signal = signal;
      this.count = 0;
      this.failed = false;
      this.error = "";
      this.firstReady = null;
      this.started = clock();
      this.tail = Promise.resolve();
    }

    put(text) {
      this.tail = this.tail.then(() => this.say(text));
    }

    async say(text) {
      if (this.failed || this.signal.aborted || !Py.strip(text)) return;
      let result;
      try {
        result = await this.synth(text);
      } catch (error) {
        this.failed = true;
        this.error = messageOf(error, "") || "Voix indisponible.";
        return;
      }
      if (this.signal.aborted) return;
      if (this.count === 0) this.firstReady = round2(clock() - this.started);
      this.publish("segment", { index: this.count, text, mime: result.mime, audio: base64(result.audio) });
      this.count += 1;
    }

    // Waits until everything queued has been said (or dropped).
    close() {
      return this.tail;
    }
  }

  class Channel {
    constructor() {
      this.items = [];
      this.closed = false;
      this.waiting = null;
    }

    push(item) {
      if (this.closed) return;
      this.items.push(item);
      this.wake();
    }

    close(drop = false) {
      this.closed = true;
      if (drop) this.items = [];
      this.wake();
    }

    wake() {
      const resolve = this.waiting;
      this.waiting = null;
      if (resolve) resolve();
    }

    async next() {
      while (!this.items.length) {
        if (this.closed) return null;
        await new Promise((resolve) => { this.waiting = resolve; });
      }
      return this.items.shift();
    }
  }

  /**
   * One call turn, as events: stage, transcript, delta, segment, then done, empty or error (VoiceManager.turn).
   * `startReply(text, signal)` returns the tutor's reply as an async iterable of ["delta", {text}] / ["done", {...}].
   * Closing this generator early, or aborting `signal`, stops the model and the voice (no more events).
   */
  async function* turn({ config, startReply, audio = null, text = null, carry = "", terms = [], signal = null,
    engine = null, fetchImpl = null } = {}) {
    const [voiceEngine, voice] = chooseEngine(config, engine, fetchImpl);
    const cancel = new AbortController();
    const out = new Channel();
    const publish = (kind, data = {}) => out.push([kind, data]);
    const onAbort = () => cancel.abort();
    let finished = false;
    // Stopped from outside: what was not read yet is dropped. At the end of the work, `done` must still arrive.
    cancel.signal.addEventListener("abort", () => out.close(!finished), { once: true });
    if (signal) {
      if (signal.aborted) cancel.abort();
      else signal.addEventListener("abort", onAbort, { once: true });
    }

    async function work() {
      const started = clock();
      try {
        const problem = voiceEngine.problem();
        let heard = text, sttMs = null;
        if (audio !== null && audio !== undefined) {
          if (problem) throw new VoiceError(problem, 503);
          checkWav(audio);
          publish("stage", { name: "transcription" });
          const begin = clock();
          heard = await voiceEngine.transcribe(audio, terms, { signal: cancel.signal });
          sttMs = Math.round((clock() - begin) * 1000);
          if (!heard || NOISE_TRANSCRIPTS.test(heard)) {
            publish("empty");
            return;
          }
        }
        heard = Py.strip(String(heard || ""));
        if (!heard) {
          publish("empty");
          return;
        }
        if (cancel.signal.aborted) return;
        const full = carry ? Py.strip(`${Py.strip(String(carry))} ${heard}`) : heard;
        publish("transcript", { text: full, heard, sttMs });
        publish("stage", { name: "answer" });
        // A written turn (a Run during the call) without an available voice: the reply stays on screen.
        const speaker = problem ? null
          : new Speaker((piece) => voiceEngine.speak(spoken(piece, config), voice, { signal: cancel.signal }), publish, cancel.signal);
        const segmenter = new Speech.StreamSegmenter();
        const reply = [];
        let result = {};
        for await (const [kind, data] of await startReply(full, cancel.signal)) {
          if (kind === "delta") {
            reply.push(data.text);
            publish("delta", { text: data.text });
            if (speaker) for (const piece of segmenter.feed(data.text)) speaker.put(piece);
          } else if (kind === "done") {
            result = data;
          }
          if (cancel.signal.aborted) break;
        }
        if (speaker) {
          for (const piece of segmenter.finish()) speaker.put(piece);
          await speaker.close();
        }
        if (cancel.signal.aborted) return;
        publish("done", {
          reply: reply.join(""), segments: speaker ? speaker.count : 0,
          voiceError: speaker && speaker.failed ? speaker.error : problem || "",
          firstAudio: speaker ? speaker.firstReady : null, sttMs, total: round2(clock() - started), ...result,
        });
      } catch (error) {
        if (!cancel.signal.aborted) {
          publish("error", { message: messageOf(error, "Erreur pendant l'appel."), status: statusOf(error, 500) });
        }
      }
    }

    work().finally(() => {
      finished = true;
      out.close();
      cancel.abort();
    });
    try {
      while (true) {
        const item = await out.next();
        if (item === null) return;
        yield item;
      }
    } finally {
      cancel.abort();
      if (signal) signal.removeEventListener("abort", onAbort);
    }
  }

  return {
    VoiceError,
    NOISE_TRANSCRIPTS,
    checkWav,
    LocalVoice,
    MistralVoice,
    ElevenLabsVoice,
    createEngine,
    turn,
    phrases,
    status,
    test,
    speak,
    voices,
    elevenLabsVoices,
    base64,
    _resetPhrases: () => phraseCache.clear(),
  };
});
