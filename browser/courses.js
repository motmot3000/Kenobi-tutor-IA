// Port of courses.py for the browser version: import a course from a Markdown document, structured by the
// plugged model (providers.js) or, without one, from the document's headings. courses.py stays the reference: same
// messages, same course for the same document and the same model answer.
(function (root, factory) {
  const api = factory(root);
  if (typeof module === "object" && module.exports) module.exports = api;
  root.KenobiCourses = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function (root) {
  "use strict";

  const node = typeof module === "object" && module.exports;
  const Shared = node ? require("./shared.js") : root.KenobiShared;
  const {
    MAX_DOCUMENT, MIN_DOCUMENT, MAX_MODULES, MAX_LESSONS_PER_MODULE, MAX_LESSONS, ANALYSIS_TOKENS, LANGUAGES,
    DEFAULT_FILES, TEMPLATE, ANALYSIS_SYSTEM
  } = Shared.courses;
  // Extensions the terminal's editor can open (terminal_bridge.TEXT_SUFFIXES).
  const EDITOR_SUFFIXES = new Set(Shared.courses._EDITOR_SUFFIXES);
  const ANALYSIS_TIMEOUT = 300000; // courses.py: timeout=300 (seconds)

  class CourseError extends Error {
    constructor(message) {
      super(message);
      this.name = "CourseError";
    }
  }

  // ── Python semantics on strings and JSON values ─────────────────────────────

  class PythonError extends Error {
    constructor(name, message) {
      super(message);
      this.name = name;
    }
  }

  const hasOwn = (object, key) => Object.prototype.hasOwnProperty.call(object, key);
  const isDict = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
  const isNone = (value) => value === null || value === undefined;
  const get = (mapping, key) => (hasOwn(mapping, key) ? mapping[key] : undefined); // dict.get

  function typeName(value) {
    if (isNone(value)) return "NoneType";
    if (typeof value === "boolean") return "bool";
    if (typeof value === "number") return Number.isInteger(value) ? "int" : "float";
    if (typeof value === "bigint") return "int";
    if (typeof value === "string") return "str";
    return Array.isArray(value) ? "list" : "dict";
  }

  function truthy(value) {
    if (isNone(value)) return false;
    if (typeof value === "number") return value !== 0; // NaN is true in Python
    if (typeof value === "string" || Array.isArray(value)) return value.length > 0;
    if (value instanceof Set || value instanceof Map) return value.size > 0;
    if (typeof value === "object") return Object.keys(value).length > 0;
    return Boolean(value);
  }

  // Python's whitespace (str.isspace, \s of re): JavaScript's \s and trim() differ on U+001C–U+001F, U+0085, U+FEFF.
  const WS = "\\t\\n\\x0b\\x0c\\r\\x1c-\\x1f \\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000";
  const S = `[${WS}]`;
  const isSpace = (code) => (code >= 0x09 && code <= 0x0d) || (code >= 0x1c && code <= 0x20) || code === 0x85 ||
    code === 0xa0 || code === 0x1680 || (code >= 0x2000 && code <= 0x200a) || code === 0x2028 || code === 0x2029 ||
    code === 0x202f || code === 0x205f || code === 0x3000;

  function strip(text) {
    let start = 0;
    let end = text.length;
    while (start < end && isSpace(text.charCodeAt(start))) start += 1;
    while (end > start && isSpace(text.charCodeAt(end - 1))) end -= 1;
    return text.slice(start, end);
  }

  function lstrip(text) {
    let start = 0;
    while (start < text.length && isSpace(text.charCodeAt(start))) start += 1;
    return text.slice(start);
  }

  function rstrip(text) {
    let end = text.length;
    while (end > 0 && isSpace(text.charCodeAt(end - 1))) end -= 1;
    return text.slice(0, end);
  }

  const stripDashes = (text) => text.replace(/^-+|-+$/g, "");
  const isHigh = (code) => code >= 0xd800 && code <= 0xdbff;
  const isLow = (code) => code >= 0xdc00 && code <= 0xdfff;

  // len(text): Python counts code points, not UTF-16 units.
  function length(text) {
    let count = text.length;
    for (let index = 0; index < text.length - 1; index += 1) {
      if (isHigh(text.charCodeAt(index)) && isLow(text.charCodeAt(index + 1))) {
        count -= 1;
        index += 1;
      }
    }
    return count;
  }

  // text[:count], count >= 0
  function head(text, count) {
    if (text.length <= count) return text;
    let index = 0;
    for (let taken = 0; taken < count && index < text.length; taken += 1) {
      index += isHigh(text.charCodeAt(index)) && isLow(text.charCodeAt(index + 1)) ? 2 : 1;
    }
    return text.slice(0, index);
  }

  function floatRepr(number) {
    if (Number.isNaN(number)) return "nan";
    if (!Number.isFinite(number)) return number > 0 ? "inf" : "-inf";
    if (number === 0) return Object.is(number, -0) ? "-0.0" : "0.0";
    const [mantissa, exponent] = Math.abs(number).toExponential().split("e");
    const digits = mantissa.replace(".", "");
    const point = Number(exponent) + 1;
    let text;
    if (point > 16 || point <= -4) {
      const power = point - 1;
      text = `${digits[0]}${digits.length > 1 ? `.${digits.slice(1)}` : ""}e${power < 0 ? "-" : "+"}${String(Math.abs(power)).padStart(2, "0")}`;
    } else if (point <= 0) {
      text = `0.${"0".repeat(-point)}${digits}`;
    } else if (point >= digits.length) {
      text = `${digits}${"0".repeat(point - digits.length)}.0`;
    } else {
      text = `${digits.slice(0, point)}.${digits.slice(point)}`;
    }
    return number < 0 ? `-${text}` : text;
  }

  // JavaScript has one number type: a number is printed as Python prints it after reading JSON.stringify's text
  // (an int below 1e21, a float otherwise).
  const numberText = (number) => (Number.isInteger(number) && Math.abs(number) < 1e21 ? String(number) : floatRepr(number));

  const NOT_PRINTABLE = /[\p{Cc}\p{Cf}\p{Cs}\p{Co}\p{Cn}\p{Zl}\p{Zp}\p{Zs}]/u;
  // Key order of the objects read by loadJson, when JavaScript's differs (integer-like keys come first in JS).
  const KEY_ORDER = Symbol("pythonKeyOrder");

  function reprText(text) {
    const quote = text.includes("'") && !text.includes('"') ? '"' : "'";
    let out = quote;
    for (const character of text) {
      const code = character.codePointAt(0);
      if (character === quote || character === "\\") out += `\\${character}`;
      else if (character === "\t") out += "\\t";
      else if (character === "\n") out += "\\n";
      else if (character === "\r") out += "\\r";
      else if (code < 0x20 || code === 0x7f) out += `\\x${code.toString(16).padStart(2, "0")}`;
      else if (code < 0x7f || !NOT_PRINTABLE.test(character)) out += character;
      else if (code <= 0xff) out += `\\x${code.toString(16).padStart(2, "0")}`;
      else if (code <= 0xffff) out += `\\u${code.toString(16).padStart(4, "0")}`;
      else out += `\\U${code.toString(16).padStart(8, "0")}`;
    }
    return out + quote;
  }

  function repr(value) {
    if (isNone(value)) return "None";
    if (typeof value === "boolean") return value ? "True" : "False";
    if (typeof value === "number") return numberText(value);
    if (typeof value === "bigint") return value.toString();
    if (typeof value === "string") return reprText(value);
    if (Array.isArray(value)) return `[${value.map(repr).join(", ")}]`;
    const keys = Object.keys(value);
    const order = value[KEY_ORDER];
    const ordered = order && order.length === keys.length ? order : keys;
    return `{${ordered.map((key) => `${reprText(key)}: ${repr(value[key])}`).join(", ")}}`;
  }

  // str(value)
  const str = (value) => (typeof value === "string" ? value : repr(value));

  // Decimal value of a Unicode digit (category Nd): digits come in runs of ten, from 0 to 9.
  const DIGIT = /^\p{Nd}$/u;
  function decimal(character, code) {
    if (!DIGIT.test(character)) return -1;
    let start = code;
    while (start > 0 && DIGIT.test(String.fromCodePoint(start - 1))) start -= 1;
    return (code - start) % 10;
  }

  // What float() and int() do first: Unicode digits and spaces become ASCII, anything else stops the number.
  function asciiNumber(text) {
    let out = "";
    for (const character of text) {
      const code = character.codePointAt(0);
      if (code < 127) out += character;
      else if (isSpace(code)) out += " ";
      else {
        const digit = decimal(character, code);
        if (digit < 0) return `${out}?`;
        out += String(digit);
      }
    }
    return out;
  }

  const cStrip = (text) => text.replace(/^[\t\n\v\f\r ]+|[\t\n\v\f\r ]+$/g, "");

  // float(value)
  function toFloat(value) {
    if (typeof value === "boolean") return value ? 1 : 0;
    if (typeof value === "number") return value;
    if (typeof value === "bigint") {
      const number = Number(value);
      if (!Number.isFinite(number)) throw new PythonError("OverflowError", "int too large to convert to float");
      return number;
    }
    if (typeof value !== "string") {
      throw new PythonError("TypeError", `float() argument must be a string or a real number, not '${typeName(value)}'`);
    }
    let text = asciiNumber(value);
    const refuse = () => new PythonError("ValueError", `could not convert string to float: ${repr(value)}`);
    if (text.includes("_")) {
      if (/(?:^|[^0-9])_|_(?![0-9])/.test(text)) throw refuse();
      text = text.replace(/_/g, "");
    }
    const match = /^([+-]?)(?:(inf(?:inity)?)|(nan)|((?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?))$/i.exec(cStrip(text));
    if (!match) throw refuse();
    if (match[2]) return match[1] === "-" ? -Infinity : Infinity;
    if (match[3]) return NaN;
    return Number(match[1] + match[4]);
  }

  // int(value) of a float
  function truncate(number) {
    if (Number.isNaN(number)) throw new PythonError("ValueError", "cannot convert float NaN to integer");
    if (!Number.isFinite(number)) throw new PythonError("OverflowError", "cannot convert float infinity to integer");
    return Math.trunc(number) || 0;
  }

  // int(value)
  function toInt(value) {
    if (typeof value === "boolean") return value ? 1 : 0;
    if (typeof value === "number") return truncate(value);
    if (typeof value === "bigint") return Number(value);
    if (typeof value === "string") {
      const text = cStrip(asciiNumber(value));
      if (/^[+-]?[0-9]+(?:_[0-9]+)*$/.test(text) && text.replace(/[^0-9]/g, "").length <= 4300) {
        return Number(text.replace(/_/g, "")) || 0;
      }
      throw new PythonError("ValueError", `invalid literal for int() with base 10: ${repr(value)}`);
    }
    throw new PythonError("TypeError",
      `int() argument must be a string, a bytes-like object or a real number, not '${typeName(value)}'`);
  }

  // json.loads: strict JSON plus NaN, Infinity and -Infinity; integers of more than 4300 digits are refused.
  // Errors are ValueError, as json.JSONDecodeError is one.
  const NUMBER = /(-?(?:0|[1-9][0-9]*))(\.[0-9]+)?([eE][-+]?[0-9]+)?/y;
  const ESCAPES = { '"': '"', "\\": "\\", "/": "/", b: "\b", f: "\f", n: "\n", r: "\r", t: "\t" };

  function loadJson(text) {
    let index = 0;
    const fail = (message) => {
      throw new PythonError("ValueError", `${message} (char ${index})`);
    };
    const skip = () => {
      while (index < text.length && " \t\n\r".includes(text[index])) index += 1;
    };

    function parseString() {
      index += 1;
      let out = "";
      let start = index;
      for (;;) {
        if (index >= text.length) fail("Unterminated string");
        const character = text[index];
        if (character === '"') {
          out += text.slice(start, index);
          index += 1;
          return out;
        }
        if (character === "\\") {
          out += text.slice(start, index);
          const escape = text[index + 1];
          if (escape === "u") {
            const hex = text.slice(index + 2, index + 6);
            if (!/^[0-9a-fA-F]{4}$/.test(hex)) fail("Invalid \\uXXXX escape");
            out += String.fromCharCode(parseInt(hex, 16));
            index += 6;
          } else if (escape !== undefined && hasOwn(ESCAPES, escape)) {
            out += ESCAPES[escape];
            index += 2;
          } else {
            fail("Invalid \\escape");
          }
          start = index;
          continue;
        }
        if (character < " ") fail("Invalid control character");
        index += 1;
      }
    }

    function parseObject() {
      index += 1;
      const object = {};
      const keys = [];
      const done = () => {
        index += 1;
        const actual = Object.keys(object);
        if (keys.some((key, position) => key !== actual[position])) Object.defineProperty(object, KEY_ORDER, { value: keys });
        return object;
      };
      skip();
      if (text[index] === "}") return done();
      for (;;) {
        if (text[index] !== '"') fail("Expecting property name enclosed in double quotes");
        const key = parseString();
        skip();
        if (text[index] !== ":") fail("Expecting ':' delimiter");
        index += 1;
        skip();
        const value = parseValue();
        if (!hasOwn(object, key)) keys.push(key);
        Object.defineProperty(object, key, { value, writable: true, enumerable: true, configurable: true });
        skip();
        if (text[index] === "}") return done();
        if (text[index] !== ",") fail("Expecting ',' delimiter");
        index += 1;
        skip();
      }
    }

    function parseArray() {
      index += 1;
      const array = [];
      skip();
      if (text[index] === "]") {
        index += 1;
        return array;
      }
      for (;;) {
        array.push(parseValue());
        skip();
        if (text[index] === "]") {
          index += 1;
          return array;
        }
        if (text[index] !== ",") fail("Expecting ',' delimiter");
        index += 1;
        skip();
      }
    }

    function parseValue() {
      const character = text[index];
      if (character === '"') return parseString();
      if (character === "{") return parseObject();
      if (character === "[") return parseArray();
      const words = [["null", null], ["true", true], ["false", false], ["NaN", NaN], ["Infinity", Infinity],
        ["-Infinity", -Infinity]];
      for (const [word, value] of words) {
        if (text.startsWith(word, index)) {
          index += word.length;
          return value;
        }
      }
      NUMBER.lastIndex = index;
      const match = NUMBER.exec(text);
      if (!match) fail("Expecting value");
      const integer = match[2] === undefined && match[3] === undefined;
      if (integer && match[1].replace("-", "").length > 4300) {
        fail("Exceeds the limit (4300 digits) for integer string conversion");
      }
      index += match[0].length;
      const number = Number(match[0]);
      // Python's ints are exact: an integer beyond 2^53 stays a BigInt.
      return integer && !Number.isSafeInteger(number) ? BigInt(match[0]) : number;
    }

    skip();
    const value = parseValue();
    skip();
    if (index !== text.length) fail("Extra data");
    return value;
  }

  // ── Tools ───────────────────────────────────────────────────────────────────

  const WHITESPACE_RUN = new RegExp(`${S}+`, "gu");
  const LEADING_HASHES = new RegExp(`^#+${S}*`, "u");

  // courses._text: one line of text, without the Markdown marks that the interface would show as they are.
  function text(value, limit) {
    let result = strip(str(truthy(value) ? value : "").replace(WHITESPACE_RUN, " "));
    result = strip(result.replace(/`/g, "").replace(/\*/g, "").replace(LEADING_HASHES, ""));
    return rstrip(head(result, limit));
  }

  function slugify(value, limit = 40) {
    const lowered = str(truthy(value) ? value : "").toLowerCase().normalize("NFD").replace(/\p{Mn}/gu, "");
    return stripDashes(stripDashes(lowered.replace(/[^a-z0-9]+/g, "-")).slice(0, limit));
  }

  // courses._strings
  function strings(values, limit, count) {
    const list = typeof values === "string" ? [values] : values;
    const result = [];
    for (const value of Array.isArray(list) ? list : []) {
      const line = text(value, limit);
      if (line && !result.includes(line)) result.push(line);
      if (result.length >= count) break;
    }
    return result;
  }

  // courses._duration: minutes between 10 and 120. Like Python, an infinite value raises OverflowError.
  function duration(value, fallback = 20) {
    let number;
    try {
      number = truncate(toFloat(value));
    } catch (error) {
      if (error.name === "TypeError" || error.name === "ValueError") return fallback;
      throw error;
    }
    return Math.max(10, Math.min(120, number));
  }

  function checkDocument(markdown) {
    if (typeof markdown !== "string") throw new CourseError("Document illisible.");
    const content = markdown.replace(/\r\n/g, "\n").replace(/\r/g, "\n").replace(/^\ufeff+/, "");
    if (content.includes("\x00")) throw new CourseError("Ce fichier n'est pas un document texte.");
    if (length(strip(content)) < MIN_DOCUMENT) throw new CourseError("Document trop court pour en tirer un cours.");
    if (length(content) > MAX_DOCUMENT) {
      throw new CourseError("Document trop long (300 000 caractères au plus) : découpe-le en plusieurs cours.");
    }
    return content;
  }

  // ── Structure from the headings (no model) ──────────────────────────────────

  // Python's re.IGNORECASE also matches "ı" and "İ" with "i"; its \b is Unicode-aware.
  const I = "[i\\u0131\\u0130]";
  const WORD_END = "(?![\\p{L}\\p{N}_])";
  const HEADING = new RegExp(`^(#{1,6})${S}+([^\\n]+?)${S}*#*${S}*$`, "u");
  const ITEM = new RegExp(`^${S}{0,3}(?:[-*+]|\\p{Nd}{1,3}[.)])${S}+(?:\\[[ xX]\\]${S}+)?([^\\n]+)$`, "u");
  const GOALS = /objectif|competence|but du cours|a la fin|acquis vises|finalite/i;
  // Sections about the course that are not modules: recognised at the start of the title, or as the whole title.
  const NOT_MODULE = new RegExp(
    "^(?:(?:prerequis|pre requis|ressources?|bibliographie|annexes?|glossaire|licence|sommaire|table des matieres|" +
    "a propos|references|mode d emploi)\\b.*|(?:introduction|presentation|conclusion|evaluation|organisation|" +
    "fonctionnement)(?: generale| du cours| de la formation)?)$", "i");
  const NUMBERING = new RegExp(`^(?:module|chap${I}tre|part${I}e|le[cç]on|s[eé]ance|un${I}t[eé]|bloc|[eé]tape)` +
    `${S}*\\p{Nd}+${S}*[-—–:.)]*${S}*`, "iu");
  const CODE = new RegExp(`\`\`\`${S}*(python|py|bash|sh|shell|javascr${I}pt|js|sql|c|cpp|java|rust|go)${WORD_END}`, "iu");
  const CODE_LANGUAGE = new RegExp(`\`\`\`${S}*(python|py|bash|sh|shell|javascr${I}pt|js)${WORD_END}`, "iu");
  const LANGUAGE_NAMES = { py: "python", python: "python", js: "javascript", javascript: "javascript", bash: "shell",
    sh: "shell", shell: "shell" };
  const MARKDOWN_SUFFIX = /\.(md|markdown|txt)(?=\n?$)/iu;

  function plain(value) {
    const lowered = str(truthy(value) ? value : "").toLowerCase().normalize("NFD").replace(/\p{Mn}/gu, "");
    return lowered.replace(/[^a-z0-9]+/g, " ").trim();
  }

  const isFence = (line) => lstrip(line).startsWith("```");

  // Flat tree of the headings: {level, title, body: lines until the next heading}.
  function sections(lines) {
    const result = [];
    let current = null;
    let fenced = false;
    for (const line of lines) {
      if (isFence(line)) fenced = !fenced;
      const match = fenced ? null : HEADING.exec(line);
      if (match) {
        current = { level: match[1].length, title: strip(match[2]), body: [] };
        result.push(current);
      } else if (current !== null) {
        current.body.push(line);
      }
    }
    return result;
  }

  function items(body, count = 12) {
    const result = [];
    let fenced = false;
    for (const line of body) {
      if (isFence(line)) fenced = !fenced;
      const match = fenced ? null : ITEM.exec(line);
      if (match) {
        const entry = text(match[1], 160);
        if (entry) result.push(entry);
      }
      if (result.length >= count) break;
    }
    return result;
  }

  function firstParagraph(body) {
    const paragraph = [];
    for (const line of body) {
      const stripped = strip(line);
      if (!stripped) {
        if (paragraph.length) break;
        continue;
      }
      if (!paragraph.length && ["#", "|", "```", ">", "-", "*", "---"].some((mark) => stripped.startsWith(mark))) continue;
      paragraph.push(stripped);
    }
    return text(paragraph.join(" "), 400);
  }

  /** Course from the headings: H1 = course, H2 = modules, H3 (or lists) = lessons. */
  function outline(markdown, filename = "") {
    const lines = markdown.split("\n");
    const found = sections(lines);
    const top = found.reduce((lowest, section) => Math.min(lowest, section.level), found.length ? Infinity : 1);
    const titles = found.filter((section) => section.level === top);
    const singleTitle = titles.length === 1;
    const title = singleTitle ? titles[0].title : String(truthy(filename) ? filename : "").replace(MARKDOWN_SUFFIX, "");
    const moduleLevel = singleTitle ? top + 1 : top;
    const description = singleTitle ? firstParagraph(titles[0].body) : "";
    const goals = [];
    let modules = [];
    let pending = null;
    for (const section of found) {
      const { level, title: name } = section;
      const plainName = plain(name);
      if (level === moduleLevel) {
        pending = null;
        if (GOALS.test(plainName)) {
          goals.push(...items(section.body, 8));
          continue;
        }
        if (NOT_MODULE.test(plainName)) continue;
        pending = { title: name, source: name, description: firstParagraph(section.body), body: section.body, lessons: [] };
        modules.push(pending);
      } else if (level === moduleLevel + 1 && pending !== null) {
        if (GOALS.test(plainName) || NOT_MODULE.test(plainName)) continue;
        pending.lessons.push({ title: name, objectives: items(section.body, 3) });
      }
    }
    for (const module of modules) {
      if (!module.lessons.length) module.lessons = items(module.body, 12).map((entry) => ({ title: entry, objectives: [] }));
      if (!module.lessons.length) module.lessons = [{ title: module.title, objectives: [] }];
      module.brief = module.description;
      delete module.body;
    }
    if (!modules.length) {
      const entries = items(lines, 30);
      const name = title || "Cours";
      modules = [{
        title: name, source: "", description: "", brief: "",
        lessons: entries.length ? entries.map((entry) => ({ title: entry, objectives: [] })) : [{ title: name, objectives: [] }]
      }];
    }
    const code = CODE.test(markdown);
    let language = "";
    if (code) {
      const first = CODE_LANGUAGE.exec(markdown);
      const name = first ? first[1].toLowerCase() : "";
      language = hasOwn(LANGUAGE_NAMES, name) ? LANGUAGE_NAMES[name] : "autre";
    }
    return {
      title: title || "Cours importé", shortTitle: "", description, level: "", goals, notes: "", modules,
      tools: { terminal: code, language, file: hasOwn(DEFAULT_FILES, language) ? DEFAULT_FILES[language] : "" }
    };
  }

  function documentHeadings(markdown) {
    return sections(markdown.split("\n")).map((section) => section.title);
  }

  // ── Analysis by the model ───────────────────────────────────────────────────

  const FENCED_JSON = new RegExp(`\`\`\`(?:json)?${S}*(\\{[\\s\\S]*\\})${S}*\`\`\``, "u");

  /** The JSON object of the model's answer, even surrounded by text or ```json. */
  function parseAnalysis(answer) {
    const source = str(truthy(answer) ? answer : "");
    const fenced = FENCED_JSON.exec(source);
    const candidates = fenced ? [fenced[1]] : [];
    const start = source.indexOf("{");
    const end = source.lastIndexOf("}");
    if (start !== -1 && end > start) candidates.push(source.slice(start, end + 1));
    for (const candidate of candidates) {
      let value;
      try {
        value = loadJson(candidate);
      } catch (error) {
        if (error.name === "ValueError") continue;
        throw error;
      }
      if (isDict(value)) return value;
    }
    throw new CourseError("La réponse du modèle n'est pas une structure de cours lisible.");
  }

  function defaultProviders() {
    if (root.KenobiProviders) return root.KenobiProviders;
    if (node) return require("./providers.js");
    throw new Error("KenobiProviders is not loaded (browser/providers.js).");
  }

  /** Course structure proposed by the plugged model (raw object, for buildCourse). Throws ProviderError. */
  async function analyse(markdown, filename, ai, { onProgress = null, signal = null, providers = null } = {}) {
    const request = `Nom du fichier : ${truthy(filename) ? str(filename) : "cours.md"}\n\nDocument du cours :\n"""\n${str(markdown)}\n"""`;
    const api = providers || defaultProviders();
    const parts = [];
    let received = 0;
    const stream = api.streamChat(ai, ANALYSIS_SYSTEM, [{ role: "user", content: request }],
      { maxTokens: ANALYSIS_TOKENS, temperature: 0.2, timeout: ANALYSIS_TIMEOUT, signal: signal || undefined });
    for await (const [kind, value] of stream) {
      if (kind !== "delta") continue;
      parts.push(value);
      received += length(value);
      if (onProgress) onProgress(received);
    }
    return parseAnalysis(parts.join(""));
  }

  // ── Building the course ─────────────────────────────────────────────────────

  function unique(base, taken, limit) {
    const stem = stripDashes((base || "cours").slice(0, limit)) || "cours";
    let candidate = stem;
    let number = 2;
    while (taken.has(candidate)) {
      const suffix = `-${number}`;
      candidate = stripDashes(stem.slice(0, limit - suffix.length)) + suffix;
      number += 1;
    }
    taken.add(candidate);
    return candidate;
  }

  function tools(raw) {
    const source = isDict(raw) ? raw : {};
    const terminal = truthy(get(source, "terminal"));
    let language = strip(str(truthy(get(source, "language")) ? get(source, "language") : "")).toLowerCase();
    const aliases = { bash: "shell", sh: "shell", zsh: "shell", git: "shell", js: "javascript", node: "javascript", py: "python" };
    if (hasOwn(aliases, language)) language = aliases[language];
    if (!terminal) return { terminal: false, language: "", file: "" };
    if (!LANGUAGES.includes(language)) language = "autre";
    let file = strip(str(truthy(get(source, "file")) ? get(source, "file") : ""));
    const suffix = file.includes(".") ? `.${file.slice(file.lastIndexOf(".") + 1).toLowerCase()}` : "";
    if (!/^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$/.test(file) || !EDITOR_SUFFIXES.has(suffix)) file = DEFAULT_FILES[language];
    return { terminal: true, language, file };
  }

  // Values of an iterable given by the caller (array, Set, string…); a plain object gives its keys.
  function valuesOf(value) {
    if (isNone(value)) return [];
    if (typeof value === "object" && typeof value[Symbol.iterator] !== "function") return Object.keys(value);
    return Array.from(value);
  }

  // Python's datetime.isoformat(), "+00:00" written "Z". A Date is written in UTC (datetime.now(timezone.utc)); an
  // ISO string is read like datetime.fromisoformat and keeps its offset (no offset: no suffix).
  const ISO = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2})(?::(\d{2})(?::(\d{2})(?:[.,](\d+))?)?)?(Z|[+-]\d{2}(?::?\d{2}(?::?\d{2})?)?)?)?$/;

  function isoformat(moment) {
    const pad = (number, width = 2) => String(number).padStart(width, "0");
    if (moment instanceof Date) {
      if (Number.isNaN(moment.getTime())) throw new PythonError("ValueError", "Invalid Date");
      const micro = moment.getUTCMilliseconds() * 1000;
      return `${pad(moment.getUTCFullYear(), 4)}-${pad(moment.getUTCMonth() + 1)}-${pad(moment.getUTCDate())}T` +
        `${pad(moment.getUTCHours())}:${pad(moment.getUTCMinutes())}:${pad(moment.getUTCSeconds())}` +
        `${micro ? `.${pad(micro, 6)}` : ""}Z`;
    }
    const value = String(moment);
    const match = ISO.exec(value);
    const invalid = () => new PythonError("ValueError", `Invalid isoformat string: ${repr(value)}`);
    if (!match) throw invalid();
    const [, year, month, day, hour = "00", minute = "00", second = "00", fraction = "", offset = ""] = match;
    const date = new Date(Date.UTC(Number(year), Number(month) - 1, Number(day)));
    if (date.getUTCMonth() !== Number(month) - 1 || date.getUTCDate() !== Number(day) || Number(year) < 1 ||
        Number(hour) > 23 || Number(minute) > 59 || Number(second) > 59) throw invalid();
    const micro = Number(fraction.slice(0, 6).padEnd(6, "0"));
    let zone = "";
    if (offset === "Z") {
      zone = "Z";
    } else if (offset) {
      const [hours, minutes = "00", seconds = "00"] = offset.slice(1).match(/\d{2}/g);
      if (Number(hours) > 23 || Number(minutes) > 59 || Number(seconds) > 59) throw invalid();
      zone = hours === "00" && minutes === "00" && seconds === "00"
        ? "Z" : `${offset[0]}${hours}:${minutes}${seconds === "00" ? "" : `:${seconds}`}`;
    }
    return `${year}-${month}-${day}T${pad(hour)}:${minute}:${second}${micro ? `.${pad(micro, 6)}` : ""}${zone}`;
  }

  /** Course ready for the state: safe ids, bounded lengths, first lesson current. Throws CourseError. */
  function buildCourse(raw, { existingIds = [], filename = "", chars = 0, analysis = "ia", headings = null, now = null } = {}) {
    if (!isDict(raw)) throw new CourseError("Structure de cours illisible.");
    const moment = isNone(now) ? new Date() : now;
    const title = text(get(raw, "title"), 120) || "Cours importé";
    const short = text(get(raw, "shortTitle"), 24) || strip(head(title.replace(NUMBERING, ""), 24)) || head(title, 24);
    const courseId = unique(slugify(short, 24) || slugify(title, 24), new Set(valuesOf(existingIds)), 24);
    const known = new Set(valuesOf(truthy(headings) ? headings : []).map(plain));
    const modules = [];
    const lessonIds = new Set();
    let total = 0;
    const rawModules = get(raw, "modules");
    for (const entry of Array.isArray(rawModules) ? rawModules : []) {
      if (modules.length >= MAX_MODULES || total >= MAX_LESSONS || !isDict(entry)) break;
      const moduleTitle = text(get(entry, "title"), 140);
      if (!moduleTitle) continue;
      const lessons = [];
      const rawLessons = get(entry, "lessons");
      for (let lesson of Array.isArray(rawLessons) ? rawLessons : []) {
        if (lessons.length >= MAX_LESSONS_PER_MODULE || total >= MAX_LESSONS) break;
        if (typeof lesson === "string") lesson = { title: lesson };
        if (!isDict(lesson)) continue;
        const lessonTitle = text(get(lesson, "title"), 140);
        if (!lessonTitle) continue;
        const number = `${String(modules.length + 1).padStart(2, "0")}${String(lessons.length + 1).padStart(2, "0")}`;
        const slug = slugify(lessonTitle.replace(NUMBERING, ""), 26) || "lecon";
        const lessonId = unique(stripDashes(`${courseId}-${number}-${slug}`.slice(0, 64)), lessonIds, 64);
        const lessonEntry = { id: lessonId, title: lessonTitle, duration: duration(get(lesson, "duration")), status: "todo" };
        const objectives = strings(get(lesson, "objectives"), 220, 4);
        if (objectives.length) lessonEntry.objectives = objectives;
        lessons.push(lessonEntry);
        total += 1;
      }
      if (!lessons.length) continue;
      const module = {
        id: `${courseId}-m${String(modules.length + 1).padStart(2, "0")}`,
        title: moduleTitle,
        description: text(get(entry, "description"), 300),
        lessons
      };
      const brief = text(get(entry, "brief"), 700);
      if (brief) module.brief = brief;
      const source = text(get(entry, "source"), 200);
      // A title the document does not contain would be of no use: only existing ones are kept.
      if (source && (!known.size || known.has(plain(source)))) module.source = source;
      modules.push(module);
    }
    if (!modules.length) {
      throw new CourseError("Aucun module ni leçon reconnu dans ce document : ajoute des titres (##, ###).");
    }
    modules[0].lessons[0].status = "current";
    const first = modules[0].lessons[0].title;
    const description = text(get(raw, "description"), 500);
    const level = text(get(raw, "level"), 60) || "Niveau libre";
    const createdAt = isoformat(moment);
    return {
      id: courseId,
      title,
      shortTitle: short,
      description,
      level,
      createdAt,
      goals: strings(get(raw, "goals"), 240, 8),
      tools: tools(get(raw, "tools")),
      source: { file: text(filename, 120) || "cours.md", importedAt: isoformat(moment), chars: toInt(chars), analysis },
      modules,
      sessions: [],
      memory: {
        lastSessionId: null,
        acquired: [],
        toReview: [],
        reviews: [],
        facts: [],
        nextFocus: `Commencer par « ${first} ».`,
        notes: text(get(raw, "notes"), 900)
      }
    };
  }

  function isProviderError(error, providers) {
    if (!error || typeof error !== "object") return false;
    const api = providers || root.KenobiProviders;
    if (api && typeof api.ProviderError === "function" && error instanceof api.ProviderError) return true;
    return error.name === "ProviderError";
  }

  /** The course drawn from the document, and a notice if the model's analysis could not be used: [course, notice].
   * A cancelled analysis (ProviderError "cancelled") is thrown again. */
  async function importCourse(markdown, filename = "",
    { ai = null, existingIds = [], onProgress = null, signal = null, providers = null, now = null } = {}) {
    const content = checkDocument(markdown);
    const headings = documentHeadings(content);
    let notice = "";
    let raw = null;
    if (truthy(ai) && truthy(ai.configured)) {
      try {
        raw = await analyse(content, filename, ai, { onProgress, signal, providers });
      } catch (error) {
        if (isProviderError(error, providers)) {
          if (error.code === "cancelled") throw error;
          notice = `Analyse par le modèle impossible (${error.message}) : structure tirée des titres du document.`;
        } else if (error instanceof CourseError) {
          notice = `${error.message} Structure tirée des titres du document.`;
        } else {
          throw error;
        }
      }
    } else {
      notice = "Aucun modèle d'IA branché : structure tirée des titres du document.";
    }
    const options = { existingIds, filename, chars: length(content), headings, now };
    if (raw !== null) {
      try {
        const course = buildCourse(raw, { ...options, analysis: "ia" });
        if (get(raw, "informatique") === false) {
          notice = "Ce document ne semble pas porter sur l'informatique : Kenobi est un tuteur de code et " +
            "d'informatique, le cours est importé tel quel.";
        }
        return [course, notice];
      } catch (error) {
        if (!(error instanceof CourseError)) throw error;
        notice = `${error.message} Structure tirée des titres du document.`;
      }
    }
    return [buildCourse(outline(content, filename), { ...options, analysis: "titres" }), notice];
  }

  // ── Writing a course with the plugged model (courses.write_course) ─────────

  const AUTHOR_SYSTEM = Shared.courses.AUTHOR_SYSTEM;
  const AUTHOR_MAX_WISH = Shared.courses.AUTHOR_MAX_WISH;
  const AUTHOR_TOKENS = Shared.courses.AUTHOR_TOKENS;

  function authorMessages(wish) {
    return [{ role: "user", content: `Ce que je veux apprendre :\n"""\n${str(wish)}\n"""` }];
  }

  function checkWish(wish) {
    const value = strip(str(wish == null ? "" : wish).split("\r\n").join("\n"));
    if (length(value) < 10) throw new CourseError("Décris en quelques phrases le cours que tu veux : le sujet, ton niveau, ton but.");
    if (length(value) > AUTHOR_MAX_WISH) throw new CourseError(`Description trop longue : ${AUTHOR_MAX_WISH} caractères au plus.`);
    return value;
  }

  function cleanDocument(value) {
    const content = strip(str(value == null ? "" : value));
    const fenced = /^```[ \t]*(?:markdown|md)?[ \t]*\n([\s\S]*?)\n?```$/i.exec(content);
    return `${strip(fenced ? fenced[1] : content)}\n`;
  }

  async function* writeCourse(wish, ai, { signal = null, providers = null } = {}) {
    const content = checkWish(wish);
    if (!(ai && ai.configured)) throw new CourseError("Branche d'abord un modèle d'IA dans les réglages : c'est lui qui écrit le cours.");
    const api = providers || defaultProviders();
    const parts = [];
    let result = {};
    for await (const [kind, value] of api.streamChat(ai, AUTHOR_SYSTEM, authorMessages(content),
      { maxTokens: AUTHOR_TOKENS, temperature: 0.5, timeout: 300000, signal: signal || undefined })) {
      if (kind === "delta") {
        parts.push(value);
        yield ["delta", { text: value }];
      } else {
        result = value || {};
      }
    }
    yield ["done", {
      markdown: cleanDocument(parts.join("")),
      model: result.model !== undefined ? result.model : (ai.model || ""),
      elapsed: result.elapsed !== undefined ? result.elapsed : null,
      truncated: Boolean(result.truncated)
    }];
  }

  return Object.freeze({
    MAX_DOCUMENT, MIN_DOCUMENT, MAX_MODULES, MAX_LESSONS_PER_MODULE, MAX_LESSONS, ANALYSIS_TOKENS, LANGUAGES,
    DEFAULT_FILES, TEMPLATE, ANALYSIS_SYSTEM, AUTHOR_SYSTEM, AUTHOR_MAX_WISH, AUTHOR_TOKENS,
    authorMessages,
    checkWish,
    cleanDocument,
    writeCourse,
    CourseError,
    slugify,
    checkDocument,
    outline,
    parseAnalysis,
    analyse,
    buildCourse,
    documentHeadings,
    importCourse,
    // Private helpers of courses.py, exposed for the parity tests.
    _internal: Object.freeze({ text, duration, plain, tools, strings, items, firstParagraph, sections, loadJson, repr })
  });
});
