// AI model client for the browser: one streamed conversation, whatever the provider. Port of providers.py.
// Two protocols cover the usual services: "OpenAI compatible" (/chat/completions) and Anthropic (/messages), both
// streamed in SSE. The key only goes in the authorization header: never in a log or an error message.
(function (root, factory) {
  const api = factory(root);
  if (typeof module === "object" && module.exports) module.exports = api;
  root.KenobiProviders = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function (root) {
  "use strict";
  const Shared = typeof module === "object" && module.exports ? require("./shared.js") : root.KenobiShared;
  const CONNECT_TIMEOUT = Shared.providers.CONNECT_TIMEOUT * 1000;
  const READ_TIMEOUT = Shared.providers.READ_TIMEOUT * 1000;
  const MAX_LINE = Shared.providers.MAX_LINE;
  const MAX_REPLY = Shared.providers.MAX_REPLY;
  const ANTHROPIC_VERSION = Shared.providers.ANTHROPIC_VERSION;
  const MODELS_TIMEOUT = 20000;
  const ERROR_BODY = 4096;
  const MODELS_BODY = 4 * 1024 * 1024;
  const EMPTY = new Uint8Array(0);

  // ── Python semantics for what the providers send back ────────────────────
  // Python's \s (str.isspace), \w, and its re.IGNORECASE on letters ('i' also matches 'ı' and 'İ').
  const WS = "\\t\\n\\v\\f\\r\\x1c-\\x1f \\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000";
  const WORD = "\\p{L}\\p{N}_";
  const STRIP = new RegExp(`^[${WS}]+|[${WS}]+$`, "gu");
  const LSTRIP = new RegExp(`^[${WS}]+`, "u");
  const WS_RUN = new RegExp(`[${WS}]+`, "gu");
  const SURROGATE = /[\ud800-\udfff]/;
  const NOT_PRINTABLE = /[\p{C}\p{Z}]/u;
  const CASE_EXTRA = { i: "ıİ", k: "\u212a", s: "\u017f" };

  function ignoreCase(literal) {
    let out = "";
    for (const char of literal) {
      const lower = char.toLowerCase(), upper = char.toUpperCase();
      if (lower === upper) out += /[\^$\\.*+?()[\]{}|/]/.test(char) ? "\\" + char : char;
      else out += `[${lower}${upper}${CASE_EXTRA[lower] || ""}]`;
    }
    return out;
  }

  const NOT_CHAT = new RegExp(["embed", "moderation", "ocr", "tts", "transcri", "whisper", "dall-e", "image", "rerank",
    "guard", "realtime", "voxtral"].map(ignoreCase).join("|"), "u");
  const KEY_PROBLEM = new RegExp(`${ignoreCase("api")}[ _-]?${ignoreCase("key")}`, "u");

  const isDict = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
  const has = (value, key) => isDict(value) && Object.prototype.hasOwnProperty.call(value, key);
  const get = (value, key) => (has(value, key) ? value[key] : null);
  const strip = (text) => text.replace(STRIP, "");
  const lstrip = (text) => text.replace(LSTRIP, "");

  function truthy(value) {
    if (value === null || value === undefined || value === false || value === 0 || value === "") return false;
    if (Array.isArray(value)) return value.length > 0;
    if (typeof value === "object") return Object.keys(value).length > 0;
    return true;  // NaN included, as in Python
  }

  // Length and prefix in code points, like Python's len() and [:n].
  function length(text) {
    if (!SURROGATE.test(text)) return text.length;
    let count = 0;
    for (const _char of text) count += 1;
    return count;
  }

  function slice(text, end) {
    if (!SURROGATE.test(text)) return text.slice(0, end);
    let out = "", count = 0;
    for (const char of text) {
      if (count++ >= end) break;
      out += char;
    }
    return out;
  }

  // Python's string order (code points, where JavaScript compares UTF-16 units).
  function compare(a, b) {
    if (!SURROGATE.test(a) && !SURROGATE.test(b)) return a < b ? -1 : a > b ? 1 : 0;
    const x = Array.from(a, (char) => char.codePointAt(0)), y = Array.from(b, (char) => char.codePointAt(0));
    for (let index = 0; index < Math.min(x.length, y.length); index += 1) {
      if (x[index] !== y[index]) return x[index] < y[index] ? -1 : 1;
    }
    return x.length === y.length ? 0 : x.length < y.length ? -1 : 1;
  }

  function hex(value, width) {
    return value.toString(16).padStart(width, "0");
  }

  function reprString(text) {
    const quote = text.includes("'") && !text.includes('"') ? '"' : "'";
    let out = quote;
    for (const char of text) {
      if (char === quote || char === "\\") out += "\\" + char;
      else if (char === "\t") out += "\\t";
      else if (char === "\n") out += "\\n";
      else if (char === "\r") out += "\\r";
      else if (char !== " " && NOT_PRINTABLE.test(char)) {
        const code = char.codePointAt(0);
        out += code < 0x100 ? "\\x" + hex(code, 2) : code < 0x10000 ? "\\u" + hex(code, 4) : "\\U" + hex(code, 8);
      } else out += char;
    }
    return out + quote;
  }

  function reprNumber(value) {
    if (Number.isInteger(value)) return Object.is(value, -0) ? "0" : String(value);
    if (!Number.isFinite(value)) return Number.isNaN(value) ? "nan" : value > 0 ? "inf" : "-inf";
    const [mantissa, exponentText] = value.toExponential().split("e");
    const exponent = Number(exponentText);
    const sign = mantissa.startsWith("-") ? "-" : "";
    const digits = mantissa.replace("-", "").replace(".", "");
    if (exponent < -4 || exponent >= 16) {
      const head = digits.length > 1 ? digits[0] + "." + digits.slice(1) : digits;
      return `${sign}${head}e${exponent < 0 ? "-" : "+"}${String(Math.abs(exponent)).padStart(2, "0")}`;
    }
    if (exponent < 0) return `${sign}0.${"0".repeat(-exponent - 1)}${digits}`;
    if (digits.length > exponent + 1) return `${sign}${digits.slice(0, exponent + 1)}.${digits.slice(exponent + 1)}`;
    return `${sign}${digits}${"0".repeat(exponent + 1 - digits.length)}.0`;
  }

  // Python's repr() and str() of a decoded JSON value.
  function repr(value) {
    if (value === null || value === undefined) return "None";
    if (value === true) return "True";
    if (value === false) return "False";
    if (typeof value === "number") return reprNumber(value);
    if (typeof value === "string") return reprString(value);
    if (Array.isArray(value)) return "[" + value.map(repr).join(", ") + "]";
    if (typeof value === "object") {
      return "{" + Object.keys(value).map((key) => reprString(key) + ": " + repr(value[key])).join(", ") + "}";
    }
    return String(value);
  }

  function str(value) {
    return typeof value === "string" ? value : repr(value);
  }

  // What Python's `for item in value` gives for a JSON value (only lists matter to the callers).
  function iterate(value) {
    if (Array.isArray(value)) return value;
    if (typeof value === "string") return Array.from(value);
    if (isDict(value)) return Object.keys(value);
    throw new TypeError(`'${value === true || value === false ? "bool" : typeof value}' object is not iterable`);
  }

  // json.loads: also reads NaN, Infinity and -Infinity, which JSON.parse refuses. undefined if unreadable.
  const NON_FINITE = { NaN: NaN, Infinity: Infinity, "-Infinity": -Infinity };
  const MARK = "\u0000kenobi-number\u0000";

  function loads(text) {
    try {
      return JSON.parse(text);
    } catch (_error) {
      const marked = markNonFinite(text);
      if (marked === null) return undefined;
      try {
        return JSON.parse(marked, (_key, value) =>
          typeof value === "string" && value.startsWith(MARK) ? NON_FINITE[value.slice(MARK.length)] : value);
      } catch (_again) {
        return undefined;
      }
    }
  }

  function markNonFinite(text) {
    let out = "", changed = false, inString = false;
    for (let index = 0; index < text.length;) {
      const char = text[index];
      if (inString) {
        const step = char === "\\" ? 2 : 1;
        if (char === '"') inString = false;
        out += text.slice(index, index + step);
        index += step;
        continue;
      }
      const token = /^(?:-?Infinity|NaN)/.exec(text.slice(index, index + 9));
      if (token) {
        out += JSON.stringify(MARK + token[0]);
        index += token[0].length;
        changed = true;
        continue;
      }
      if (char === '"') inString = true;
      out += char;
      index += 1;
    }
    return changed ? out : null;
  }

  // bytes.decode("utf-8", "replace"): a byte order mark stays in the text, as in Python.
  function decode(bytes) {
    if (typeof bytes === "string") return bytes;
    return new TextDecoder("utf-8", { ignoreBOM: true }).decode(bytes || EMPTY);
  }

  // ── Network ─────────────────────────────────────────────────────────────

  // The page replaces window.fetch with a shim for /api/*: the providers are called with the real one.
  function fetcher(fetchImpl) {
    const impl = fetchImpl || root.__kenobiNativeFetch || root.fetch;
    return (url, init) => impl.call(root, url, init);
  }

  function abortError() {
    const error = new Error("The operation was aborted.");
    error.name = "AbortError";
    return error;
  }

  function timeoutError() {
    const error = new Error("timed out");
    error.name = "TimeoutError";
    return error;
  }

  // Settles as soon as `signal` aborts, even if the promise ignores it.
  function abortable(promise, signal) {
    if (signal.aborted) {
      promise.catch(() => {});
      return Promise.reject(abortError());
    }
    return new Promise((resolve, reject) => {
      const onAbort = () => reject(abortError());
      signal.addEventListener("abort", onAbort, { once: true });
      promise.then((value) => {
        signal.removeEventListener("abort", onAbort);
        resolve(value);
      }, (error) => {
        signal.removeEventListener("abort", onAbort);
        reject(error);
      });
    });
  }

  function toBytes(value) {
    if (value instanceof Uint8Array) return value;
    if (typeof value === "string") return new TextEncoder().encode(value);
    if (value instanceof ArrayBuffer) return new Uint8Array(value);
    if (ArrayBuffer.isView(value)) return new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
    return EMPTY;
  }

  function concat(parts) {
    if (parts.length === 1) return parts[0];
    const out = new Uint8Array(parts.reduce((size, part) => size + part.length, 0));
    let offset = 0;
    for (const part of parts) {
      out.set(part, offset);
      offset += part.length;
    }
    return out;
  }

  // One chunk of a body: rejects after `idleMs` of silence, or as soon as `signal` aborts.
  function readChunk(reader, signal, idleMs) {
    return new Promise((resolve, reject) => {
      let timer = null;
      const settle = (callback, value) => {
        clearTimeout(timer);
        signal.removeEventListener("abort", onAbort);
        callback(value);
      };
      const onAbort = () => settle(reject, abortError());
      if (signal.aborted) {
        reject(abortError());
        return;
      }
      signal.addEventListener("abort", onAbort, { once: true });
      timer = setTimeout(() => settle(reject, timeoutError()), idleMs);
      reader.read().then((result) => settle(resolve, result), (error) => settle(reject, error));
    });
  }

  // response.read(limit): at most `limit` bytes; the rest of the body is dropped.
  async function readAtMost(response, limit, idleMs, signal) {
    if (!response.body) return EMPTY;
    const reader = response.body.getReader();
    const parts = [];
    let size = 0;
    try {
      while (size < limit) {
        const { done, value } = await readChunk(reader, signal, idleMs);
        if (done) break;
        const bytes = toBytes(value);
        parts.push(bytes);
        size += bytes.length;
      }
    } finally {
      reader.cancel().catch(() => {});
    }
    const body = parts.length ? concat(parts) : EMPTY;
    return body.length > limit ? body.subarray(0, limit) : body;
  }

  // ── Errors ──────────────────────────────────────────────────────────────

  class ProviderError extends Error {
    constructor(status, message, code = "") {
      super(message);
      this.name = "ProviderError";
      this.status = status;
      this.message = message;
      this.code = code;
    }
  }

  class HttpFailure extends Error {
    constructor(status, body) {
      super(String(status));
      this.status = status;
      this.body = body;
    }
  }

  const label = (config) => str(truthy(get(config, "label")) ? config.label : "Le fournisseur");
  const cancelled = () => new ProviderError(499, "Réponse interrompue.", "cancelled");
  const late = () => new ProviderError(504, "Le modèle n'a pas terminé à temps.", "timeout");
  const silent = () => new ProviderError(504, "Le modèle ne répond plus.", "timeout");

  function unreachable(config, reason) {
    return new ProviderError(503, `${label(config)} est injoignable (${slice(String(reason), 120)}).`, "unreachable");
  }

  function reasonOf(error) {
    const cause = error && error.cause;
    const reason = (cause && (cause.message || cause.code)) || (error && error.message) || String(error);
    return strip(String(reason).replace(WS_RUN, " ")) || "erreur réseau";
  }

  // The provider's own sentence, if it gives one: short, with nothing of what was sent to it.
  function errorDetail(body) {
    const data = loads(decode(body));
    if (data === undefined) return "";
    const error = isDict(data) ? get(data, "error") : null;
    let detail = isDict(error) ? get(error, "message") : typeof error === "string" ? error : null;
    if (!truthy(detail) && isDict(data)) detail = truthy(get(data, "message")) ? data.message : get(data, "detail");
    if (Array.isArray(detail)) {
      detail = detail.slice(0, 3).map((item) =>
        isDict(item) ? str(truthy(get(item, "msg")) ? item.msg : item) : str(item)).join("; ");
    }
    return slice(strip(str(truthy(detail) ? detail : "").replace(WS_RUN, " ")), 220);
  }

  function httpError(config, status, body = EMPTY) {
    const name = label(config);
    const detail = errorDetail(body);
    const auth = () => new ProviderError(502, `Clé refusée par ${name} : vérifie-la dans les réglages.`, "auth");
    if (status === 401 || status === 403) return auth();
    if (status === 404) {
      return new ProviderError(502, `Modèle « ${str(get(config, "model"))} » ou adresse introuvable chez ${name}.`, "not_found");
    }
    if (status === 429) return new ProviderError(503, `Limite ou crédit atteint chez ${name} : réessaie dans un instant.`, "rate");
    if (status === 400 || status === 422) {
      // Some providers answer 400, not 401, to a missing or wrong key (Google).
      if (KEY_PROBLEM.test(detail)) return auth();
      return new ProviderError(502, `${name} a refusé la demande${detail ? " : " + detail : "."}`, "bad_request");
    }
    return new ProviderError(502, `${name} a répondu par une erreur ${status}${detail ? " : " + detail : "."}`, "upstream");
  }

  // ── One call: its abort controller, the caller's signal and the total deadline ──

  class Call {
    constructor(signal, timeoutMs) {
      this.controller = new AbortController();
      this.signal = this.controller.signal;
      this.external = signal || null;
      this.why = "";
      this.onAbort = () => this.stop("cancel");
      if (this.external) {
        if (this.external.aborted) this.stop("cancel");
        else this.external.addEventListener("abort", this.onAbort, { once: true });
      }
      // setTimeout fires at once beyond 2^31 - 1 ms: a longer deadline is only checked between lines.
      this.timer = timeoutMs == null || !(timeoutMs <= 2147483647) ? null
        : setTimeout(() => this.stop("deadline"), Math.max(0, timeoutMs));
    }

    get cancelled() {
      return this.why === "cancel" || Boolean(this.external && this.external.aborted);
    }

    stop(why) {
      if (!this.why) this.why = why;
      this.controller.abort();
    }

    close() {
      clearTimeout(this.timer);
      if (this.external) this.external.removeEventListener("abort", this.onAbort);
      this.stop("closed");
    }
  }

  function interruption(call, config, error) {
    if (call.cancelled) return cancelled();
    if (call.why === "deadline") return late();
    if (call.why === "headers") return unreachable(config, "timed out");
    return unreachable(config, reasonOf(error));
  }

  function headers(config, stream) {
    const out = { "Content-Type": "application/json" };
    if (stream) out.Accept = "text/event-stream";
    if (get(config, "kind") === "anthropic") {
      out["anthropic-version"] = ANTHROPIC_VERSION;
      out["anthropic-dangerous-direct-browser-access"] = "true";
      if (truthy(get(config, "apiKey"))) out["x-api-key"] = String(config.apiKey);
    } else if (truthy(get(config, "apiKey"))) {
      out.Authorization = "Bearer " + config.apiKey;
    }
    return out;
  }

  // The response once its headers arrived (2xx), or HttpFailure with the start of the error body.
  async function open(config, path, payload, { stream, call, connectTimeout, fetchImpl }) {
    const url = str(get(config, "baseUrl") ?? "").replace(/\/+$/u, "") + path;
    if (!/^https?:\/\//iu.test(url)) throw unreachable(config, "unknown url type: " + reprString(url));
    if (call.why) throw interruption(call, config, null);
    const init = { method: payload == null ? "GET" : "POST", headers: headers(config, stream && payload != null),
      signal: call.signal };
    if (payload != null) init.body = JSON.stringify(payload);
    const timer = setTimeout(() => call.stop("headers"), connectTimeout);
    let response;
    try {
      response = await abortable(fetcher(fetchImpl)(url, init), call.signal);
    } catch (error) {
      throw interruption(call, config, error);
    } finally {
      clearTimeout(timer);
    }
    if (response.ok) return response;
    let body = EMPTY;
    try {
      body = await readAtMost(response, ERROR_BODY, connectTimeout, call.signal);
    } catch (_error) {
      body = EMPTY;
    }
    if (call.cancelled) throw cancelled();
    throw new HttpFailure(response.status, body);
  }

  function normalizeMessages(messages) {
    const result = [];
    for (const item of messages || []) {
      const role = get(item, "role") === "assistant" ? "assistant" : "user";
      const raw = get(item, "content");
      const content = strip(str(truthy(raw) ? raw : ""));
      if (!content) continue;
      if (result.length && result[result.length - 1].role === role) result[result.length - 1].content += "\n\n" + content;
      else result.push({ role, content });
    }
    while (result.length && result[0].role === "assistant") result.shift();
    return result;
  }

  // ── What a (baseUrl, model) pair already refused: not sent again with each message ──

  const quirks = new Map();
  const quirkKey = (config) => JSON.stringify([get(config, "baseUrl"), get(config, "model")]);
  const getQuirks = (config) => ({ ...(quirks.get(quirkKey(config)) || {}) });

  function learn(config, facts) {
    const key = quirkKey(config);
    quirks.set(key, { ...(quirks.get(key) || {}), ...facts });
  }

  // Forgets what was learnt during a call that failed anyway: the removed setting was not the cause.
  function restore(config, facts) {
    if (Object.keys(facts).length) quirks.set(quirkKey(config), { ...facts });
    else quirks.delete(quirkKey(config));
  }

  function openaiPayload(config, system, messages, maxTokens, temperature, known) {
    const payload = {
      model: config.model,
      stream: true,
      messages: (system ? [{ role: "system", content: system }] : []).concat(messages),
    };
    const maxKey = has(known, "max_key") ? known.max_key : "max_tokens";
    if (truthy(maxTokens) && maxKey) payload[maxKey] = maxTokens;
    if (temperature !== null && temperature !== undefined && !truthy(known.no_temperature)) payload.temperature = temperature;
    const reasoning = truthy(get(config, "reasoning")) ? config.reasoning : "auto";
    if (reasoning !== "auto" && !truthy(known.no_reasoning)) payload.reasoning_effort = reasoning;
    return payload;
  }

  function anthropicPayload(config, system, messages, maxTokens, temperature, known) {
    const payload = {
      model: config.model,
      stream: true,
      messages,
      max_tokens: Math.min(truthy(maxTokens) ? maxTokens : 4096, has(known, "max_cap") ? known.max_cap : 64000),
    };
    if (system) payload.system = [{ type: "text", text: system, cache_control: { type: "ephemeral" } }];
    if (temperature !== null && temperature !== undefined && !truthy(known.no_temperature)) payload.temperature = temperature;
    return payload;
  }

  // An optional setting refused (400/422): learn which one and send again without it. Nothing was produced.
  function adapt(config, failure, payload) {
    if (failure.status !== 400 && failure.status !== 422) return false;
    const text = decode(failure.body).toLowerCase();
    if (KEY_PROBLEM.test(text)) return false;  // a wrong key told in a 400: no setting to remove
    const sent = (name) => has(payload, name);
    if (get(config, "kind") === "anthropic") {
      if (text.includes("max_tokens") && (sent("max_tokens") ? payload.max_tokens : 0) > 4096) {
        learn(config, { max_cap: payload.max_tokens > 8192 ? 8192 : 4096 });
        return true;
      }
      if (text.includes("temperature") && sent("temperature")) {
        learn(config, { no_temperature: true });
        return true;
      }
      return false;
    }
    if (text.includes("max_completion_tokens") && sent("max_tokens")) {
      learn(config, { max_key: "max_completion_tokens" });
      return true;
    }
    if (text.includes("reasoning") && sent("reasoning_effort")) {
      learn(config, { no_reasoning: true });
      return true;
    }
    if (text.includes("temperature") && sent("temperature")) {
      learn(config, { no_temperature: true });
      return true;
    }
    if ((text.includes("max_tokens") || text.includes("max_completion_tokens"))
        && (sent("max_tokens") || sent("max_completion_tokens"))) {
      learn(config, { max_key: "" });
      return true;
    }
    if (["reasoning_effort", "temperature", "max_tokens", "max_completion_tokens"].some(sent)) {
      learn(config, { no_reasoning: true, no_temperature: true, max_key: "" });
      return true;
    }
    return false;
  }

  // ── Reading the stream ───────────────────────────────────────────────────

  const clock = root.performance && typeof root.performance.now === "function"
    ? () => root.performance.now() / 1000 : () => Date.now() / 1000;
  const round2 = (seconds) => Number(seconds.toFixed(2));

  // SSE events (name, data) of a streamed response, read line by line as Python's readline does.
  async function* sse(response, call, deadline, readTimeout) {
    const reader = response.body ? response.body.getReader() : null;
    const decoder = new TextDecoder("utf-8", { ignoreBOM: true });
    let buffer = EMPTY, start = 0, ended = !reader;
    let event = null, data = [];
    try {
      while (true) {
        if (call.cancelled) throw cancelled();
        if (call.why === "deadline" || clock() > deadline) throw late();
        let line = null;
        while (line === null) {
          const newline = buffer.subarray(start, Math.min(buffer.length, start + MAX_LINE + 1)).indexOf(0x0a);
          if (newline !== -1) {
            line = buffer.subarray(start, start + newline + 1);
            start += newline + 1;
          } else if (buffer.length - start >= MAX_LINE + 1) {
            line = buffer.subarray(start, start + MAX_LINE + 1);
            start += MAX_LINE + 1;
          } else if (ended) {
            line = buffer.subarray(start);
            start = buffer.length;
          } else {
            let result;
            try {
              result = await readChunk(reader, call.signal, readTimeout);
            } catch (_error) {
              if (call.cancelled) throw cancelled();
              if (call.why === "deadline") throw late();
              throw silent();
            }
            if (result.done) {
              ended = true;
            } else {
              buffer = concat([buffer.subarray(start), toBytes(result.value)]);
              start = 0;
            }
          }
        }
        if (!line.length) {
          if (data.length) yield [event, data.join("\n")];
          return;
        }
        const text = decoder.decode(line).replace(/[\r\n]+$/u, "");
        if (!text) {
          if (data.length) yield [event, data.join("\n")];
          event = null;
          data = [];
        } else if (text.startsWith(":")) {
          continue;
        } else if (text.startsWith("event:")) {
          event = strip(text.slice(6));
        } else if (text.startsWith("data:")) {
          data.push(lstrip(text.slice(5)));
        }
      }
    } finally {
      if (reader) reader.cancel().catch(() => {});
    }
  }

  // Typed parts: only the text is kept, never the reasoning.
  function textOf(content) {
    if (typeof content === "string") return content;
    if (Array.isArray(content)) {
      return content.filter((part) => isDict(part) && (get(part, "type") === "text" || get(part, "type") === "output_text")
        && typeof get(part, "text") === "string").map((part) => part.text).join("");
    }
    return "";
  }

  async function* readOpenai(events) {
    let finished = null;
    for await (const [, raw] of events) {
      if (raw === "[DONE]") break;
      const chunk = loads(raw);
      if (!isDict(chunk)) continue;
      if (truthy(get(chunk, "error"))) {
        const error = chunk.error;
        const detail = isDict(error) ? get(error, "message") : str(error);
        throw new ProviderError(502, `Le modèle a signalé une erreur : ${slice(str(detail), 200)}`, "upstream");
      }
      const choices = get(chunk, "choices");
      for (const choice of iterate(truthy(choices) ? choices : [])) {
        if (!isDict(choice)) continue;
        const delta = isDict(get(choice, "delta")) ? choice.delta : {};
        const text = textOf(get(delta, "content"));
        if (text) yield ["delta", text];
        if (truthy(get(choice, "finish_reason"))) finished = choice.finish_reason;
      }
    }
    yield ["finish", truthy(finished) ? finished : "stop"];
  }

  async function* readAnthropic(events) {
    let finished = null;
    for await (const [event, raw] of events) {
      const chunk = loads(raw);
      if (!isDict(chunk)) continue;
      const kind = truthy(get(chunk, "type")) ? chunk.type : event;
      if (kind === "error") {
        const error = isDict(get(chunk, "error")) ? chunk.error : {};
        const message = has(error, "message") ? error.message : "";
        throw new ProviderError(502, `Le modèle a signalé une erreur : ${slice(str(message), 200)}`, "upstream");
      }
      if (kind === "content_block_delta") {
        const delta = isDict(get(chunk, "delta")) ? chunk.delta : {};
        if (get(delta, "type") === "text_delta" && typeof get(delta, "text") === "string" && delta.text) {
          yield ["delta", delta.text];
        }
      } else if (kind === "message_delta") {
        const delta = isDict(get(chunk, "delta")) ? chunk.delta : {};
        if (truthy(get(delta, "stop_reason"))) finished = delta.stop_reason === "max_tokens" ? "length" : "stop";
      } else if (kind === "message_stop") {
        break;
      }
    }
    yield ["finish", truthy(finished) ? finished : "stop"];
  }

  // ── Public API ──────────────────────────────────────────────────────────

  /**
   * Yields ["delta", text] as the reply arrives, then ["done", {elapsed, firstToken, truncated, model}].
   * `signal` aborts the request at once (ProviderError 499); closing the generator early aborts it too.
   */
  async function* streamChat(config, system, messages, options = {}) {
    const { maxTokens = 4000, temperature = 0.4, timeout = 180000, signal = null, fetchImpl = null,
      connectTimeout = CONNECT_TIMEOUT, readTimeout = READ_TIMEOUT } = options || {};
    if (!truthy(get(config, "configured"))) {
      throw new ProviderError(409, "Aucun modèle d'IA n'est branché : ouvre les réglages pour en choisir un.", "unconfigured");
    }
    const history = normalizeMessages(messages);
    if (!history.length) throw new ProviderError(400, "Message vide.", "empty");
    const start = clock();
    const call = new Call(signal, timeout);
    let first = null, finished = "stop";
    try {
      let response = null;
      const known = getQuirks(config);
      for (let attempt = 0; attempt < 4 && !response; attempt += 1) {
        const current = getQuirks(config);
        const anthropic = get(config, "kind") === "anthropic";
        const payload = (anthropic ? anthropicPayload : openaiPayload)(config, system, history, maxTokens, temperature, current);
        try {
          response = await open(config, anthropic ? "/messages" : "/chat/completions", payload,
            { stream: true, call, connectTimeout, fetchImpl });
        } catch (error) {
          if (!(error instanceof HttpFailure)) throw error;
          if (adapt(config, error, payload)) continue;
          restore(config, known);
          throw httpError(config, error.status, error.body);
        }
      }
      if (!response) {
        restore(config, known);
        throw new ProviderError(502, `${label(config)} refuse les réglages de la demande.`, "bad_request");
      }
      const read = get(config, "kind") === "anthropic" ? readAnthropic : readOpenai;
      let total = 0;
      for await (const [kind, value] of read(sse(response, call, start + timeout / 1000, readTimeout))) {
        if (kind === "finish") {
          finished = value;
          continue;
        }
        total += length(value);
        if (total > MAX_REPLY) throw new ProviderError(502, "Réponse du modèle trop longue.", "too_long");
        if (first === null) first = clock() - start;
        yield ["delta", value];
      }
    } finally {
      call.close();
    }
    if (first === null) throw new ProviderError(502, "Le modèle a renvoyé une réponse vide.", "empty_reply");
    yield ["done", {
      elapsed: round2(clock() - start),
      firstToken: round2(first),
      truncated: finished === "length",
      model: config.model,
    }];
  }

  /** The whole reply, for work without progressive display: [text, {elapsed, firstToken, truncated, model}]. */
  async function complete(config, system, messages, options = {}) {
    const parts = [];
    let result = {};
    for await (const [kind, value] of streamChat(config, system, messages, options)) {
      if (kind === "delta") parts.push(value);
      else result = value;
    }
    return [parts.join(""), result];
  }

  /** Chat models offered by the provider: [{id, label}]. */
  async function listModels(config, { fetchImpl = null, signal = null } = {}) {
    if (!truthy(get(config, "baseUrl"))) throw new ProviderError(400, "Adresse de l'API manquante.", "unconfigured");
    const path = get(config, "kind") === "anthropic" ? "/models?limit=200" : "/models";
    const call = new Call(signal, null);
    let raw;
    try {
      let response;
      try {
        response = await open(config, path, null, { stream: false, call, connectTimeout: MODELS_TIMEOUT, fetchImpl });
      } catch (error) {
        if (error instanceof HttpFailure) throw httpError({ ...config, model: "" }, error.status, error.body);
        throw error;
      }
      try {
        raw = await readAtMost(response, MODELS_BODY, MODELS_TIMEOUT, call.signal);
      } catch (error) {
        throw call.cancelled ? cancelled() : unreachable(config, reasonOf(error));
      }
    } finally {
      call.close();
    }
    const data = loads(decode(raw));
    if (data === undefined) throw new ProviderError(502, "Liste des modèles illisible.", "upstream");
    let items = isDict(data) ? get(data, "data") : data;
    if (!Array.isArray(items)) items = isDict(data) && Array.isArray(get(data, "models")) ? data.models : [];
    const models = [], seen = new Set();
    for (let item of items) {
      if (typeof item === "string") item = { id: item };
      if (!isDict(item)) continue;
      let id = strip(str(truthy(get(item, "id")) ? item.id : truthy(get(item, "name")) ? item.name : ""));
      if (id.startsWith("models/")) id = id.slice("models/".length);
      if (!id || seen.has(id) || length(id) > 120) continue;
      const capabilities = isDict(get(item, "capabilities")) ? item.capabilities : null;
      if (capabilities !== null && get(capabilities, "completion_chat") === false) continue;
      if (capabilities === null && NOT_CHAT.test(id)) continue;
      seen.add(id);
      const name = strip(str(truthy(get(item, "display_name")) ? item.display_name
        : truthy(get(item, "name")) ? item.name : ""));
      models.push({ id, label: name && name !== id ? name : "" });
    }
    models.sort((a, b) => compare(a.id.toLowerCase(), b.id.toLowerCase()));
    return models.slice(0, 500);
  }

  return {
    ProviderError,
    normalizeMessages,
    streamChat,
    complete,
    listModels,
    MAX_REPLY,
    // For the tests and the other browser modules.
    _quirks: quirks,
    _errorDetail: errorDetail,
    _httpError: httpError,
    _textOf: textOf,
    _py: { WS, WORD, ignoreCase, isDict, has, get, truthy, strip, lstrip, length, slice, compare, repr, str, iterate,
      loads, decode },
    _net: { fetcher, abortable, readChunk, readAtMost, toBytes, concat, abortError },
  };
});
