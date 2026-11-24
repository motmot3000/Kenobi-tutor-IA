// The server of Kenobi, inside the page: the web version (static hosting, no Python) answers the same API
// routes as server.py, so the interface code stays the same in both versions.
//
// - config.js says which version runs: "browser" when the files are served as they are (GitHub Pages, any static
//   server), "server" when server.py serves them (it answers config.js itself).
// - In the browser version, window.fetch is wrapped: a request to api/… is answered here; anything else goes out
//   unchanged. Links to api/… (downloads) are answered here too.
// - Data stay in this browser (IndexedDB, store.js); keys are encrypted (vault.js) and only sent to the service
//   they open, directly from the browser (providers.js, voice.js).
(function (root, factory) {
  const api = factory(root);
  if (typeof module === "object" && module.exports) module.exports = api;
  root.KenobiBackend = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function (root) {
  "use strict";

  const node = typeof module === "object" && module.exports;
  const load = (file, name) => (node ? require(`./${file}`) : root[name]);
  const Shared = load("shared.js", "KenobiShared");
  const Store = load("store.js", "KenobiStore");
  const Vault = load("vault.js", "KenobiVault");
  const lazy = {
    memory: () => load("memory.js", "KenobiMemory"),
    tutor: () => load("tutor.js", "KenobiTutor"),
    providers: () => load("providers.js", "KenobiProviders"),
    courses: () => load("courses.js", "KenobiCourses"),
    voice: () => load("voice.js", "KenobiVoice")
  };

  const VERSION = "0.8.0";
  const MAX_STATE_CHARS = 16 * 1024 * 1024;
  const TRASH_MAX = 20;
  const COURSE_ID_RE = /^[a-z0-9-]+$/;
  const MODEL_RE = /^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,119}$/;
  const VOICE_RE = /^[A-Za-z0-9_-]{1,64}$/;
  const PAIRING_RE = /^(\d{2,5})-([A-Za-z0-9_-]{16,128})$/;
  const PROVIDERS = Shared.settings.PROVIDERS;
  const PROVIDER_IDS = Object.fromEntries(PROVIDERS.map((item) => [item.id, item]));
  const MISTRAL_VOICES = Shared.settings.MISTRAL_VOICES;
  const LOCAL_VOICES = Shared.settings.LOCAL_VOICES;
  const VOICE_ENGINES = Shared.settings.VOICE_ENGINES;
  const REASONING = Shared.settings.REASONING;
  const ELEVENLABS_MODEL = Shared.settings.ELEVENLABS_MODEL;
  const KNOWN_ERRORS = ["ApiError", "SettingsError", "StateValidationError", "CourseError", "ChatError", "ProviderError",
    "VoiceError", "VaultLocked"];

  let nativeFetch = null;
  let basePath = "/";
  const staticCache = new Map();
  let lastProblem = { ok: null, message: "" };

  // ── Errors ────────────────────────────────────────────────────────────────

  class ApiError extends Error {
    constructor(status, message) {
      super(message);
      this.name = "ApiError";
      this.status = status;
    }
  }

  class SettingsError extends Error {
    constructor(message) {
      super(message);
      this.name = "SettingsError";
      this.status = 400;
    }
  }

  // Same mapping as server.py `_handle_errors`: every known error keeps its status and its French message.
  function errorStatus(error) {
    if (!error) return 500;
    if (["StateValidationError", "SettingsError", "CourseError"].includes(error.name)) return 400;
    if (error.name === "VaultLocked") return 423;
    if (Number.isInteger(error.status) && error.status >= 400) return error.status;
    return 500;
  }

  function errorMessage(error) {
    return error && KNOWN_ERRORS.includes(error.name) ? error.message : "Erreur interne de Kenobi.";
  }

  // ── Responses ─────────────────────────────────────────────────────────────

  function json(status, value, headers = {}) {
    return new Response(JSON.stringify(value), {
      status, headers: { "Content-Type": "application/json; charset=utf-8", ...headers }
    });
  }

  function text(body, type, headers = {}) {
    return new Response(body, { status: 200, headers: { "Content-Type": type, ...headers } });
  }

  function errorResponse(error) {
    return json(errorStatus(error), { error: errorMessage(error) });
  }

  // An event stream from an async generator of [kind, data]. Closing the stream on the page side (abort) stops
  // the generator, which stops the model and the voice.
  function sse(start, signal) {
    const encoder = new TextEncoder();
    const local = new AbortController();
    let iterator = null;
    let closed = false;
    const stop = () => {
      if (closed) return;
      closed = true;
      local.abort();
      try { iterator?.return?.(); } catch { /* already finished */ }
    };
    const stream = new ReadableStream({
      start(controller) {
        if (signal) {
          if (signal.aborted) {
            stop();
            controller.error(new DOMException("Aborted", "AbortError"));
            return;
          }
          signal.addEventListener("abort", () => {
            stop();
            try { controller.error(new DOMException("Aborted", "AbortError")); } catch { /* closed */ }
          }, { once: true });
        }
        iterator = start(local.signal);
      },
      async pull(controller) {
        if (closed) return;
        try {
          const { value, done } = await iterator.next();
          if (closed) return;
          if (done) {
            closed = true;
            controller.close();
            return;
          }
          const [kind, data] = value;
          controller.enqueue(encoder.encode(`event: ${kind}\ndata: ${JSON.stringify(data ?? {})}\n\n`));
        } catch (error) {
          if (closed) return;
          closed = true;
          local.abort();
          if (error?.name === "AbortError" || error?.code === "cancelled" || error?.status === 499) {
            try { controller.close(); } catch { /* closed */ }
            return;
          }
          if (!KNOWN_ERRORS.includes(error?.name)) {
            try { root.console?.error?.("Kenobi (version web) :", error); } catch { /* no console */ }
          }
          const message = errorMessage(error);
          controller.enqueue(encoder.encode(`event: error\ndata: ${JSON.stringify({ message, status: errorStatus(error) })}\n\n`));
          controller.close();
        }
      },
      cancel() { stop(); }
    });
    return new Response(stream, {
      status: 200, headers: { "Content-Type": "text/event-stream; charset=utf-8", "Cache-Control": "no-cache" }
    });
  }

  // ── Static files of the application ───────────────────────────────────────

  function appUrl(path) {
    return new URL(path, root.document ? root.document.baseURI : `http://localhost${basePath}`).href;
  }

  async function staticText(path, { optional = false } = {}) {
    if (staticCache.has(path)) return staticCache.get(path);
    const response = await nativeFetch(appUrl(path), { cache: "no-cache" });
    if (!response.ok) {
      if (optional) return null;
      throw new ApiError(500, `Fichier de l'application introuvable : ${path}.`);
    }
    const value = await response.text();
    staticCache.set(path, value);
    return value;
  }

  async function defaultState() {
    return JSON.parse(await staticText("data/default-state.json"));
  }

  // ── Settings (same rules as settings.py; secrets in the vault) ────────────

  const cleanText = (value, limit) => String(value ?? "").replace(/[\x00-\x08\x0b-\x1f\x7f]/g, "").trim().slice(0, limit);

  function cleanKey(value) {
    const key = String(value ?? "").trim();
    if (!key) return "";
    if (key.length > 512 || [...key].some((character) => /\s/u.test(character) || character.charCodeAt(0) < 33)) {
      throw new SettingsError("Clé d'API illisible : colle-la sans espace ni retour à la ligne.");
    }
    return key;
  }

  function normalizeBaseUrl(value) {
    const raw = String(value ?? "").trim().replace(/\/+$/, "");
    if (!raw) return "";
    let parts;
    try {
      parts = new URL(raw);
    } catch {
      throw new SettingsError("Adresse de l'API invalide : attendu http(s)://hôte[:port]/v1.");
    }
    if (!["http:", "https:"].includes(parts.protocol) || !parts.hostname || parts.username || parts.password) {
      throw new SettingsError("Adresse de l'API invalide : attendu http(s)://hôte[:port]/v1.");
    }
    if (parts.search || parts.hash || raw.length > 200) {
      throw new SettingsError("Adresse de l'API invalide : sans paramètres ni ancre.");
    }
    return raw;
  }

  function defaults() {
    return {
      version: 1,
      learner: { name: "", about: "" },
      ai: { provider: "", baseUrl: "", model: "", reasoning: "none" },
      voice: {
        engine: "local",
        local: { voice: LOCAL_VOICES[0], url: "" },
        mistral: { voice: MISTRAL_VOICES[0][0] },
        elevenlabs: { voice: "", voiceName: "", model: ELEVENLABS_MODEL },
        swissNumbers: false,
        fillers: true
      }
    };
  }

  function merge(base, extra) {
    for (const [key, value] of Object.entries(extra || {})) {
      if (value && typeof value === "object" && !Array.isArray(value) && base[key] && typeof base[key] === "object") merge(base[key], value);
      else if (key in base && typeof value === typeof base[key]) base[key] = value;
    }
    return base;
  }

  async function loadSettings() {
    return merge(defaults(), await Store.get("settings"));
  }

  const SECRET = { ai: "ai", mistral: "voice:mistral", elevenlabs: "voice:elevenlabs", local: "voice:local" };

  async function secret(name) {
    try {
      return { value: await Vault.get(name), locked: false, known: false };
    } catch (error) {
      if (error.name === "VaultLocked") return { value: "", locked: true, known: await Vault.has(name) };
      throw error;
    }
  }

  // The model config as providers.js expects it (settings.py Settings.ai()).
  async function aiConfig(data) {
    data = data || (await loadSettings());
    const ai = data.ai;
    const preset = PROVIDER_IDS[ai.provider];
    if (!preset) {
      return { provider: "", label: "", kind: "openai", baseUrl: "", model: "", apiKey: "", keySource: "",
        reasoning: "none", configured: false, keyless: false, locked: false };
    }
    const { value, locked, known } = await secret(SECRET.ai);
    const base = preset.editableUrl ? (ai.baseUrl || preset.baseUrl) : preset.baseUrl;
    const keyless = Boolean(preset.keyless || preset.keyOptional);
    const hasKey = Boolean(value) || Boolean(locked && known);
    return {
      provider: preset.id, label: preset.label, kind: preset.kind, baseUrl: base || "", model: ai.model,
      apiKey: value, keySource: hasKey ? "saved" : "", reasoning: ai.reasoning, keyless, locked: Boolean(locked && known),
      configured: Boolean(base && ai.model && (hasKey || keyless))
    };
  }

  function parsePairing(code) {
    const match = PAIRING_RE.exec(String(code || "").trim());
    if (!match) return null;
    const port = Number(match[1]);
    return port > 0 && port < 65536 ? { url: `http://127.0.0.1:${port}`, token: match[2] } : null;
  }

  // The voice config as voice.js expects it (settings.py Settings.voice(), plus the local bridge).
  async function voiceConfig(data) {
    data = data || (await loadSettings());
    const voice = data.voice;
    const engine = VOICE_ENGINES.includes(voice.engine) ? voice.engine : "local";
    let mistralKey = (await secret(SECRET.mistral)).value;
    if (!mistralKey && data.ai.provider === "mistral") mistralKey = (await secret(SECRET.ai)).value;
    const localSecret = (await secret(SECRET.local)).value;
    const pairing = parsePairing(localSecret);
    return {
      engine,
      local: {
        voice: LOCAL_VOICES.includes(voice.local.voice) ? voice.local.voice : LOCAL_VOICES[0],
        url: pairing ? pairing.url : voice.local.url,
        token: pairing ? pairing.token : localSecret
      },
      mistral: { apiKey: mistralKey, voice: voice.mistral.voice || MISTRAL_VOICES[0][0] },
      elevenlabs: { apiKey: (await secret(SECRET.elevenlabs)).value, voice: voice.elevenlabs.voice,
        model: voice.elevenlabs.model || ELEVENLABS_MODEL },
      swissNumbers: Boolean(voice.swissNumbers),
      fillers: Boolean(voice.fillers)
    };
  }

  async function updateSettings(patch) {
    if (!patch || typeof patch !== "object" || Array.isArray(patch)) throw new SettingsError("Réglages illisibles.");
    const data = await loadSettings();
    const secrets = [];
    if (patch.learner && typeof patch.learner === "object") {
      if ("name" in patch.learner) data.learner.name = cleanText(patch.learner.name, 40);
      if ("about" in patch.learner) data.learner.about = cleanText(patch.learner.about, 600);
    }
    if (patch.ai && typeof patch.ai === "object") {
      const current = data.ai;
      const next = patch.ai;
      const provider = String(("provider" in next ? next.provider : current.provider) || "");
      if (provider && !PROVIDER_IDS[provider]) throw new SettingsError("Fournisseur inconnu.");
      const preset = PROVIDER_IDS[provider] || {};
      const base = preset.editableUrl
        ? (normalizeBaseUrl("baseUrl" in next ? next.baseUrl : current.baseUrl) || preset.baseUrl || "")
        : (preset.baseUrl || "");
      const previousBase = current.baseUrl || PROVIDER_IDS[current.provider]?.baseUrl || "";
      // A key stays tied to the address it was given for: another provider or address forgets it.
      if (provider !== current.provider || base !== previousBase || next.clearKey) secrets.push([SECRET.ai, ""]);
      if (next.apiKey) secrets.push([SECRET.ai, cleanKey(next.apiKey)]);
      let model = String(("model" in next ? next.model : current.model) || "").trim();
      if (provider !== current.provider && !("model" in next)) model = "";
      if (model && !MODEL_RE.test(model)) throw new SettingsError("Nom de modèle invalide.");
      const reasoning = String(("reasoning" in next ? next.reasoning : current.reasoning) || "none");
      if (!REASONING.includes(reasoning)) throw new SettingsError("Réglage de raisonnement inconnu.");
      Object.assign(current, { provider, baseUrl: preset.editableUrl ? base : "", model, reasoning });
    }
    if (patch.voice && typeof patch.voice === "object") {
      const current = data.voice;
      const next = patch.voice;
      const engine = String(("engine" in next ? next.engine : current.engine) || "local");
      if (!VOICE_ENGINES.includes(engine)) throw new SettingsError("Moteur de voix inconnu.");
      current.engine = engine;
      for (const flag of ["swissNumbers", "fillers"]) if (flag in next) current[flag] = Boolean(next[flag]);
      const local = next.local && typeof next.local === "object" ? next.local : {};
      if ("voice" in local) {
        if (!LOCAL_VOICES.includes(local.voice)) throw new SettingsError("Voix locale inconnue.");
        current.local.voice = local.voice;
      }
      if (local.clear) {
        secrets.push([SECRET.local, ""]);
        current.local.url = "";
      }
      if (local.code) {
        const pairing = parsePairing(local.code);
        if (!pairing) throw new SettingsError("Code du pont vocal illisible : colle le code affiché dans sa fenêtre (port-jeton).");
        secrets.push([SECRET.local, String(local.code).trim()]);
        current.local.url = pairing.url;
      } else if ("url" in local) {
        // Any speech server that speaks the OpenAI audio API (address ending in /v1 or the bridge root).
        current.local.url = normalizeBaseUrl(local.url);
        secrets.push([SECRET.local, local.token ? cleanKey(local.token) : ""]);
      }
      for (const name of ["mistral", "elevenlabs"]) {
        const part = next[name] && typeof next[name] === "object" ? next[name] : {};
        if (part.clearKey) secrets.push([SECRET[name], ""]);
        if (part.apiKey) secrets.push([SECRET[name], cleanKey(part.apiKey)]);
        if ("voice" in part) {
          const value = String(part.voice || "").trim();
          if (value && !VOICE_RE.test(value)) throw new SettingsError("Identifiant de voix invalide.");
          current[name].voice = value;
        }
      }
      const eleven = next.elevenlabs && typeof next.elevenlabs === "object" ? next.elevenlabs : {};
      if ("voiceName" in eleven) current.elevenlabs.voiceName = cleanText(eleven.voiceName, 60);
      if ("model" in eleven) {
        const model = String(eleven.model || "").trim() || ELEVENLABS_MODEL;
        if (!MODEL_RE.test(model)) throw new SettingsError("Modèle ElevenLabs invalide.");
        current.elevenlabs.model = model;
      }
    }
    for (const [name, value] of secrets) await Vault.set(name, value);
    await Store.set("settings", data);
    return data;
  }

  async function publicSettings(data) {
    data = data || (await loadSettings());
    const ai = await aiConfig(data);
    const voice = await voiceConfig(data);
    const ownMistral = (await secret(SECRET.mistral)).value;
    return {
      mode: "browser",
      editable: true,
      vault: await Vault.status(),
      learner: { ...data.learner },
      ai: {
        provider: ai.provider, label: ai.label, baseUrl: ai.baseUrl, model: ai.model, reasoning: ai.reasoning,
        hasKey: Boolean(ai.keySource), keySource: ai.keySource, configured: ai.configured, locked: ai.locked
      },
      voice: {
        engine: voice.engine,
        local: { voice: voice.local.voice, url: voice.local.url, paired: Boolean(voice.local.url) },
        mistral: { hasKey: Boolean(voice.mistral.apiKey), ownKey: Boolean(ownMistral), voice: voice.mistral.voice },
        elevenlabs: { hasKey: Boolean(voice.elevenlabs.apiKey), voice: voice.elevenlabs.voice,
          voiceName: data.voice.elevenlabs.voiceName, model: voice.elevenlabs.model },
        swissNumbers: voice.swissNumbers,
        fillers: voice.fillers
      },
      providers: PROVIDERS.map((item) => ({
        id: item.id, label: item.label, baseUrl: item.baseUrl, keyUrl: item.keyUrl || "", keyless: Boolean(item.keyless),
        keyOptional: Boolean(item.keyOptional), editableUrl: Boolean(item.editableUrl), envKey: false
      })),
      mistralVoices: MISTRAL_VOICES.map(([id, label]) => ({ id, label })),
      localVoices: [{ id: "pierre", label: "Pierre" }, { id: "jessica", label: "Jessica" }]
    };
  }

  // ── State (same contract as server.py, with a version tag) ────────────────

  const newTag = () => `b-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;

  async function loadStateRecord() {
    let record = await Store.get("state");
    if (!record || !record.state) {
      record = { state: await defaultState(), tag: newTag() };
      await Store.set("state", record);
    }
    try {
      lazy.memory().syncBuiltinPlan(record.state, await defaultState());
    } catch { /* an incomplete state is served as it is: validation will say what is wrong */ }
    return record;
  }

  async function saveState(state, expectedTag) {
    const Memory = lazy.memory();
    const defaults = await defaultState();
    Memory.validateState(state);
    Memory.syncBuiltinPlan(state, defaults);
    Memory.validateState(state);
    if (JSON.stringify(state).length > MAX_STATE_CHARS) throw new ApiError(413, "Le corps de la requête ne doit pas dépasser 16 Mio.");
    const previous = await Store.get("state");
    if (expectedTag && previous?.tag && previous.tag !== expectedTag) {
      const conflict = new ApiError(409, "L'état a changé ailleurs (appel, autre onglet) : la page le relit.");
      conflict.tag = previous.tag;
      throw conflict;
    }
    const kept = new Set(state.courses.map((course) => course.id));
    const removed = (previous?.state?.courses || []).filter((course) => course && !kept.has(course.id));
    if (removed.length) {
      const trash = (await Store.get("trash")) || [];
      for (const course of removed) {
        trash.unshift({ removedAt: new Date().toISOString(), course, document: (await Store.get(`doc:${course.id}`)) || null });
        await Store.remove(`doc:${course.id}`);
      }
      await Store.set("trash", trash.slice(0, TRASH_MAX));
    }
    const tag = newTag();
    await Store.set("state", { state, tag });
    return tag;
  }

  async function courseDocument(courseId) {
    if (!COURSE_ID_RE.test(String(courseId || ""))) return null;
    const own = await Store.get(`doc:${courseId}`);
    if (typeof own === "string") return own;
    return staticText(`data/plan-formation-${courseId}.md`, { optional: true });
  }

  async function documentForSession(state, sessionId) {
    const [course] = lazy.tutor().findSession(state, sessionId);
    return course ? courseDocument(course.id) : null;
  }

  // ── Routes ────────────────────────────────────────────────────────────────

  async function readJson(request) {
    const type = (request.headers.get("Content-Type") || "").split(";")[0].trim().toLowerCase();
    if (type !== "application/json") throw new ApiError(415, "Content-Type application/json requis.");
    const body = await request.text();
    if (body.length > MAX_STATE_CHARS) throw new ApiError(413, "Le corps de la requête ne doit pas dépasser 16 Mio.");
    try {
      return JSON.parse(body);
    } catch {
      throw new ApiError(400, "Corps JSON invalide.");
    }
  }

  function remember(ok, message = "") {
    lastProblem = { ok, message };
  }

  async function health() {
    const ai = await aiConfig();
    let agent;
    try {
      agent = lazy.tutor().status(ai);
    } catch {
      agent = { connected: ai.configured, configured: ai.configured, name: "Kenobi", model: ai.model, provider: ai.label,
        providerId: ai.provider, problem: "", detail: "" };
    }
    if (ai.locked && ai.configured) {
      agent.problem = "Clés verrouillées : déverrouille-les dans les réglages.";
      agent.detail = agent.problem;
    } else if (lastProblem.ok === false && !agent.problem) {
      agent.problem = lastProblem.message;
    }
    return { status: "ok", version: VERSION, mode: "browser", agent };
  }

  async function requireAi() {
    const ai = await aiConfig();
    if (ai.locked && ai.configured) throw new Vault.VaultLocked();
    return ai;
  }

  function learnerOf(data) {
    return { name: data.learner.name, about: data.learner.about };
  }

  function voiceTerms(state, sessionId, name) {
    const Tutor = lazy.tutor();
    const [course] = Tutor.findSession(state, sessionId);
    const terms = ["Kenobi"];
    if (name) terms.push(name.split(/\s+/)[0]);
    if (course) terms.push(...(Shared.voiceTerms[Tutor.courseTools(course).language] || []));
    return terms;
  }

  async function chatStream(request) {
    const payload = await readJson(request);
    const { state } = await loadStateRecord();
    const data = await loadSettings();
    const ai = await requireAi();
    const documentText = await documentForSession(state, String(payload.sessionId || ""));
    return sse(async function* (signal) {
      try {
        yield* lazy.tutor().streamReply(state, payload, { ai, learner: learnerOf(data), signal, documentText });
        remember(true);
      } catch (error) {
        if (error?.code !== "cancelled" && error?.status !== 499) remember(false, errorMessage(error));
        throw error;
      }
    }, request.signal);
  }

  async function chatOnce(request) {
    const payload = await readJson(request);
    const { state } = await loadStateRecord();
    const data = await loadSettings();
    const ai = await requireAi();
    const documentText = await documentForSession(state, String(payload.sessionId || ""));
    return json(200, await lazy.tutor().chat(state, payload, { ai, learner: learnerOf(data), documentText }));
  }

  async function listModels(request) {
    const payload = await readJson(request);
    const wanted = payload.ai && typeof payload.ai === "object" ? payload.ai : {};
    const preset = PROVIDER_IDS[String(wanted.provider || "")];
    if (!preset) throw new ApiError(400, "Fournisseur inconnu.");
    const data = await loadSettings();
    const base = preset.editableUrl ? (normalizeBaseUrl(wanted.baseUrl) || preset.baseUrl) : preset.baseUrl;
    let key = String(wanted.apiKey || "").trim();
    if (!key && data.ai.provider === preset.id && (data.ai.baseUrl || preset.baseUrl) === base) key = (await secret(SECRET.ai)).value;
    if (!base) throw new ApiError(400, "Adresse de l'API manquante.");
    if (!key && !(preset.keyless || preset.keyOptional)) throw new ApiError(400, "Colle d'abord la clé d'API.");
    const config = { kind: preset.kind, label: preset.label, baseUrl: base, apiKey: key, model: "" };
    return json(200, { models: await lazy.providers().listModels(config) }, { "Cache-Control": "no-store" });
  }

  async function testModel() {
    const ai = await requireAi();
    try {
      const result = await lazy.tutor().testConnection(ai);
      remember(true);
      return json(200, result, { "Cache-Control": "no-store" });
    } catch (error) {
      remember(false, errorMessage(error));
      throw error;
    }
  }

  async function importCourse(request) {
    const payload = await readJson(request);
    const Courses = lazy.courses();
    const filename = String(payload.filename || "cours.md").slice(0, 120);
    const textValue = Courses.checkDocument(payload.markdown);
    const { state } = await loadStateRecord();
    const existing = new Set((state.courses || []).map((course) => course.id));
    for (const item of (await Store.get("trash")) || []) if (item?.course?.id) existing.add(item.course.id);
    const ai = payload.useAi === false ? null : await aiConfig();
    const usable = ai && ai.configured && !ai.locked ? ai : null;
    return sse(async function* (signal) {
      const progress = [];
      let last = 0;
      yield ["progress", { stage: usable ? "analysis" : "outline", chars: [...textValue].length, model: usable ? usable.model : "" }];
      let result = null;
      let failure = null;
      Courses.importCourse(textValue, filename, {
        ai: usable, existingIds: [...existing], signal,
        onProgress: (received) => {
          const now = Date.now();
          if (now - last > 400) {
            last = now;
            progress.push(received);
          }
        }
      }).then((value) => { result = value; }, (error) => { failure = error; });
      while (!result && !failure) {
        await new Promise((resolve) => setTimeout(resolve, 200));
        while (progress.length) yield ["progress", { stage: "analysis", received: progress.shift() }];
      }
      if (failure) {
        if (failure.name === "CourseError" || failure.name === "ProviderError") throw new ApiError(400, failure.message);
        throw failure;
      }
      const [course, notice] = result;
      await Store.set(`doc:${course.id}`, textValue);
      yield ["done", { course, notice }];
    }, request.signal);
  }

  // A course document written by the plugged model from what the learner wants (courses.write_course).
  async function writeCourse(request) {
    const payload = await readJson(request);
    const Courses = lazy.courses();
    const wish = Courses.checkWish(payload.wish);
    const ai = await requireAi();
    if (!ai.configured) throw new ApiError(409, "Branche d'abord un modèle d'IA dans les réglages : c'est lui qui écrit le cours.");
    return sse((signal) => Courses.writeCourse(wish, ai, { signal }), request.signal);
  }

  function courseIdParam(url) {
    const courseId = url.searchParams.get("courseId") || "";
    if (!courseId) throw new ApiError(400, "Le paramètre courseId est requis.");
    if (!COURSE_ID_RE.test(courseId)) throw new ApiError(400, "courseId ne peut contenir que a-z, 0-9 et -.");
    return courseId;
  }

  async function memoryMarkdown(url) {
    const courseId = courseIdParam(url);
    const { state } = await loadStateRecord();
    const course = (state.courses || []).find((item) => item.id === courseId);
    if (!course) throw new ApiError(404, "MEMORY.md introuvable pour ce cours.");
    const headers = url.searchParams.get("download") === "1" ? { "Content-Disposition": `attachment; filename="${courseId}-MEMORY.md"` } : {};
    return text(lazy.memory().renderCourseMemory(state, course), "text/markdown; charset=utf-8", headers);
  }

  async function courseSource(url) {
    const courseId = courseIdParam(url);
    const value = await courseDocument(courseId);
    if (value == null) throw new ApiError(404, "Ce cours n'a pas de document Markdown.");
    const headers = url.searchParams.get("download") === "1" ? { "Content-Disposition": `attachment; filename="${courseId}.md"` } : {};
    return text(value, "text/markdown; charset=utf-8", headers);
  }

  // Voice and call

  async function voiceStatus() {
    return json(200, await lazy.voice().status({ config: await voiceConfig() }), { "Cache-Control": "no-store" });
  }

  async function voiceTest() {
    return json(200, await lazy.voice().test({ config: await voiceConfig() }), { "Cache-Control": "no-store" });
  }

  async function elevenVoices(request) {
    const payload = await readJson(request);
    const key = String(payload.apiKey || "").trim().slice(0, 512) || (await secret(SECRET.elevenlabs)).value;
    if (!key) throw new ApiError(400, "Clé ElevenLabs manquante.");
    return json(200, { voices: await lazy.voice().elevenLabsVoices(key) }, { "Cache-Control": "no-store" });
  }

  async function callPhrases() {
    const Voice = lazy.voice();
    const data = await loadSettings();
    const config = await voiceConfig(data);
    const status = await Voice.status({ config });
    const phrases = status.ready ? await Voice.phrases({ config, name: data.learner.name }) : { greetings: [], fillers: [] };
    return json(200, { ...phrases, delayMs: status.fillerDelayMs, ready: status.ready, problem: status.problem,
      engine: status.label }, { "Cache-Control": "no-store" });
  }

  async function callTurn(request) {
    const type = (request.headers.get("Content-Type") || "").split(";")[0].trim().toLowerCase();
    let audio = null;
    let textValue = null;
    let sessionId;
    let terminal;
    let carry = "";
    if (type === "application/json") {
      const payload = await readJson(request);
      sessionId = String(payload.sessionId || "");
      terminal = payload.terminal;
      textValue = String(payload.text || "").trim();
      if (!textValue) throw new ApiError(400, "Message vide.");
    } else if (["audio/wav", "audio/x-wav", "audio/wave"].includes(type)) {
      audio = new Uint8Array(await request.arrayBuffer());
      if (audio.byteLength > Shared.voice.MAX_AUDIO) throw new ApiError(413, "Le corps de la requête ne doit pas dépasser 8 Mio.");
      sessionId = request.headers.get("X-Session-Id") || "";
      terminal = request.headers.get("X-Terminal");
      try {
        carry = decodeURIComponent(request.headers.get("X-Carry") || "").slice(0, 2000);
      } catch {
        carry = "";
      }
    } else {
      throw new ApiError(415, "Content-Type audio/wav ou application/json requis.");
    }
    const { state } = await loadStateRecord();
    const Tutor = lazy.tutor();
    if (!Tutor.findSession(state, sessionId)[1]) throw new ApiError(404, "Séance introuvable : recharge la page.");
    const data = await loadSettings();
    const ai = await requireAi();
    const config = await voiceConfig(data);
    const documentText = await documentForSession(state, sessionId);
    const spoken = audio !== null;
    const terms = voiceTerms(state, sessionId, data.learner.name);
    return sse((signal) => lazy.voice().turn({
      config, audio, text: textValue, carry, terms, signal,
      startReply: (heard, replySignal) => Tutor.streamReply(state, {
        sessionId, message: spoken ? `[Appel vocal] ${heard}` : heard, terminal
      }, { ai, learner: learnerOf(data), voice: true, signal: replySignal, documentText })
    }), request.signal);
  }

  // Terminal and voice bridges: the files to download carry this page's address, so the bridge lets it in.

  function terminalInfo() {
    return json(200, {
      available: false, reason: "remote", pairing: true, download: "api/terminal/pont",
      message: "Branche le terminal de cet ordinateur : télécharge le pont et lance-le."
    }, { "Cache-Control": "no-store" });
  }

  // The page of the app, path included (it may live in a sub-folder, e.g. on GitHub Pages): the bridges let its
  // origin in and open this address already paired.
  function pageAddress() {
    return root.document ? new URL(".", root.document.baseURI).href : "";
  }

  async function bridgeFile(source, filename) {
    const marker = 'EMBEDDED_KENOBI_URL = ""';
    const code = await staticText(source);
    if (code.split(marker).length !== 2) throw new ApiError(500, "Pont introuvable dans l'application.");
    const body = code.replace(marker, `EMBEDDED_KENOBI_URL = ${JSON.stringify(pageAddress())}`);
    return text(body, "text/x-python; charset=utf-8", {
      "Content-Disposition": `attachment; filename="${filename}"`, "Cache-Control": "no-store"
    });
  }

  // Backup of everything this browser keeps (never the keys).

  async function exportData() {
    const record = await loadStateRecord();
    const documents = {};
    for (const key of await Store.keys("doc:")) documents[key.slice(4)] = await Store.get(key);
    const data = await loadSettings();
    const value = {
      app: "kenobi", kind: "backup", version: 1, appVersion: VERSION, exportedAt: new Date().toISOString(),
      state: record.state, documents, settings: { learner: data.learner, ai: { ...data.ai }, voice: data.voice }
    };
    const stamp = new Date().toISOString().slice(0, 10);
    return text(JSON.stringify(value, null, 2), "application/json; charset=utf-8", {
      "Content-Disposition": `attachment; filename="kenobi-sauvegarde-${stamp}.json"`, "Cache-Control": "no-store"
    });
  }

  async function importData(request) {
    const value = await readJson(request);
    if (!value || value.app !== "kenobi" || value.kind !== "backup" || !value.state) {
      throw new ApiError(400, "Ce fichier n'est pas une sauvegarde de Kenobi.");
    }
    const Memory = lazy.memory();
    Memory.validateState(value.state);
    const documents = value.documents && typeof value.documents === "object" ? value.documents : {};
    for (const [id, markdown] of Object.entries(documents)) {
      if (COURSE_ID_RE.test(id) && typeof markdown === "string") await Store.set(`doc:${id}`, markdown);
    }
    if (value.settings && typeof value.settings === "object") {
      const data = await loadSettings();
      merge(data.learner, value.settings.learner);
      if (value.settings.voice) merge(data.voice, value.settings.voice);
      if (value.settings.ai && PROVIDER_IDS[value.settings.ai.provider]) {
        if (value.settings.ai.provider !== data.ai.provider) await Vault.set(SECRET.ai, "");
        merge(data.ai, value.settings.ai);
      }
      await Store.set("settings", data);
    }
    const tag = newTag();
    await Store.set("state", { state: value.state, tag });
    return json(200, { ok: true, tag, courses: value.state.courses.length });
  }

  async function vaultAction(request) {
    const payload = await readJson(request);
    const action = String(payload.action || "");
    if (action === "unlock") {
      if (!(await Vault.unlock(String(payload.passphrase || "")))) throw new ApiError(403, "Phrase secrète incorrecte.");
    } else if (action === "lock") Vault.lock();
    else if (action === "mode") {
      try {
        await Vault.setMode(String(payload.mode || ""), payload.passphrase);
      } catch (error) {
        if (error.name === "VaultLocked") throw error;
        throw new SettingsError(error.message);
      }
    } else if (action === "forget") await Vault.clearAll();
    else throw new ApiError(400, "Action inconnue.");
    return json(200, await publicSettings(), { "Cache-Control": "no-store" });
  }

  async function route(method, url, request) {
    const path = `/${url.pathname.slice(basePath.length)}`;
    if (method === "GET") {
      if (path === "/api/health") return json(200, await health(), { "Cache-Control": "no-store" });
      if (path === "/api/state") {
        const record = await loadStateRecord();
        return json(200, record.state, { ETag: `"${record.tag}"`, "Cache-Control": "no-store" });
      }
      if (path === "/api/state/tag") return json(200, { tag: (await Store.get("state"))?.tag || null }, { "Cache-Control": "no-store" });
      if (path === "/api/memory") return memoryMarkdown(url);
      if (path === "/api/settings") return json(200, await publicSettings(), { "Cache-Control": "no-store" });
      if (path === "/api/voice/status") return voiceStatus();
      if (path === "/api/courses/template") {
        return text(Shared.courses.TEMPLATE, "text/markdown; charset=utf-8", { "Content-Disposition": 'attachment; filename="modele-de-cours.md"' });
      }
      if (path === "/api/courses/source") return courseSource(url);
      if (path === "/api/terminal") return terminalInfo();
      if (path === "/api/terminal/pont") return bridgeFile("terminal_bridge.py", "kenobi-terminal.py");
      if (path === "/api/voice/pont") return bridgeFile("voice_bridge.py", "kenobi-voice.py");
      if (path === "/api/backup") return exportData();
      throw new ApiError(404, "Route API inconnue.");
    }
    if (method === "POST") {
      if (path === "/api/state") {
        const expected = (request.headers.get("If-Match") || "").replace(/"/g, "").trim() || null;
        const tag = await saveState(await readJson(request), expected);
        return json(200, { ok: true, tag }, { ETag: `"${tag}"` });
      }
      if (path === "/api/chat/stream") return chatStream(request);
      if (path === "/api/chat") return chatOnce(request);
      if (path === "/api/settings") {
        const data = await updateSettings(await readJson(request));
        return json(200, await publicSettings(data), { "Cache-Control": "no-store" });
      }
      if (path === "/api/settings/models") return listModels(request);
      if (path === "/api/settings/test") return testModel();
      if (path === "/api/settings/vault") return vaultAction(request);
      if (path === "/api/courses/import") return importCourse(request);
      if (path === "/api/courses/write") return writeCourse(request);
      if (path === "/api/voice/install") throw new ApiError(501, "Dans la version web, la voix locale passe par le pont vocal : télécharge-le dans les réglages de la voix.");
      if (path === "/api/voice/voices") return elevenVoices(request);
      if (path === "/api/voice/test") return voiceTest();
      if (path === "/api/call/phrases") return callPhrases();
      if (path === "/api/call/turn") return callTurn(request);
      if (path === "/api/backup") return importData(request);
      throw new ApiError(404, "Route API inconnue.");
    }
    throw new ApiError(405, "Méthode HTTP non autorisée.");
  }

  async function handle(input, init) {
    const request = input instanceof Request && !init ? input : new Request(input, init);
    const url = new URL(request.url);
    try {
      return await route(request.method.toUpperCase(), url, request);
    } catch (error) {
      if (error?.status === 409 && error.tag) return json(409, { error: error.message, conflict: true, tag: error.tag });
      if (!KNOWN_ERRORS.includes(error?.name)) {
        try { root.console?.error?.("Kenobi (version web) :", error); } catch { /* no console */ }
      }
      return errorResponse(error);
    }
  }

  function isApi(url) {
    const origin = root.location ? root.location.origin : url.origin;
    return url.origin === origin && url.pathname.startsWith(`${basePath}api/`);
  }

  // ── Installation in the page ──────────────────────────────────────────────

  async function download(url, link) {
    const response = await handle(url.href, { method: "GET" });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.error || `Erreur ${response.status}`);
    }
    const blob = await response.blob();
    const disposition = response.headers.get("Content-Disposition") || "";
    const match = /filename="([^"]+)"/.exec(disposition);
    if (link.hasAttribute("download") || match) {
      const anchor = root.document.createElement("a");
      anchor.href = URL.createObjectURL(blob);
      anchor.download = match ? match[1] : (link.getAttribute("download") || url.pathname.split("/").pop() || "kenobi.txt");
      root.document.body.append(anchor);
      anchor.click();
      anchor.remove();
      setTimeout(() => URL.revokeObjectURL(anchor.href), 30000);
      return null;
    }
    // Shown in a new tab, as plain text (a Markdown type would be saved instead of shown).
    return URL.createObjectURL(new Blob([await blob.text()], { type: "text/plain;charset=utf-8" }));
  }

  function interceptLinks() {
    root.document.addEventListener("click", (event) => {
      const link = event.target?.closest?.("a[href]");
      if (!link || event.defaultPrevented || event.button !== 0) return;
      let url;
      try {
        url = new URL(link.getAttribute("href"), root.document.baseURI);
      } catch {
        return;
      }
      if (!isApi(url)) return;
      event.preventDefault();
      const tab = !link.hasAttribute("download") && link.target === "_blank" ? root.open("", "_blank") : null;
      download(url, link).then((blobUrl) => {
        if (blobUrl && tab) tab.location.href = blobUrl;
        else if (blobUrl) root.open(blobUrl, "_blank", "noopener");
      }).catch((error) => {
        tab?.close();
        root.dispatchEvent(new CustomEvent("kenobi:toast", { detail: error.message }));
      });
    }, true);
  }

  function install() {
    if (nativeFetch) return true;
    nativeFetch = root.fetch.bind(root);
    root.__kenobiNativeFetch = nativeFetch;
    basePath = new URL(".", root.document.baseURI).pathname;
    root.fetch = (input, init) => {
      let url;
      try {
        url = new URL(input instanceof Request ? input.url : String(input), root.document.baseURI);
      } catch {
        return nativeFetch(input, init);
      }
      return isApi(url) ? handle(input instanceof Request ? input : url.href, init) : nativeFetch(input, init);
    };
    interceptLinks();
    Store.persistent();
    return true;
  }

  const enabled = !node && root.KENOBI_BACKEND === "browser";
  if (enabled) install();

  return {
    enabled,
    VERSION,
    install,
    handle,
    // Hooks for the Node tests: no page, an injected fetch and base path.
    _internals: {
      normalizeBaseUrl, cleanKey, parsePairing, updateSettings, publicSettings, aiConfig, voiceConfig, saveState,
      loadStateRecord, setBasePath: (value) => { basePath = value; },
      setNativeFetch: (value) => { nativeFetch = value; root.__kenobiNativeFetch = value; }
    }
  };
});
