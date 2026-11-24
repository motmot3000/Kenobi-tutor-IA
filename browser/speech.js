// Text for the voice: what is said, and in which pieces. Port of speech_text.py (same output for the same input).
// Python's `re` is Unicode-aware: \s, \w, \d, \b and `$` are rebuilt below so that both versions agree.
(function (root, factory) {
  const api = factory(root);
  if (typeof module === "object" && module.exports) module.exports = api;
  root.KenobiSpeech = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function (root) {
  "use strict";
  const Shared = typeof module === "object" && module.exports ? require("./shared.js") : root.KenobiShared;
  const { FIRST_MIN, FIRST_MAX, SEGMENT_MAX } = Shared.speech;

  // Python's \s (str.isspace) and \w (letters, numbers, underscore).
  const WS = "\\t\\n\\v\\f\\r\\x1c-\\x1f \\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000";
  const WORD = "\\p{L}\\p{N}_";
  const re = (source, flags = "gu") => new RegExp(source, flags);

  const SENTENCE_END = re(`(?<=[.!?…])["»)\\]]*[${WS}]+`);
  const URL = re(`https?://[^${WS}]+|www\\.[^${WS}]+`);
  // Python's `$` without MULTILINE also matches just before a final "\n".
  const KENOBI_BLOCK = re("```[ \\t]*kenobi[-_][^\\n]*\\n[^]*?(?:```|(?=\\n?$))");
  const CODE_BLOCK = re("```[^]*?(?:```|(?=\\n?$))");
  const SYMBOLS = [
    [re(`[${WS}]*%`), " pour cent"], [re(`[${WS}]*€`), " euros"],
    [re(`[${WS}]+&[${WS}]+`), " et "], [re(`[${WS}]*(?:->|→|=>)[${WS}]*`), ", "],
    [re(`≈[${WS}]*`), "environ "], [re(`[${WS}]*°C(?![${WORD}])`), " degrés"],
    [re(`(?<![${WORD}])[eE][tT][cC]\\.`), "et cetera."],
  ];
  const INLINE_CODE = re("`([^`]*)`");
  const IMAGE = re("!\\[([^\\]]*)\\]\\([^)]*\\)");
  const LINK = re("\\[([^\\]]+)\\]\\([^)]*\\)");
  const LINE_BREAK = /\r\n|[\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029]/u;
  const HEADING = re(`^[${WS}]{0,3}#{1,6}[${WS}]*`);
  const BULLET = re(`^[${WS}]*(?:[-*+•]|\\p{Nd}+[.)])[${WS}]+`);
  const QUOTE = re(`^[${WS}]*>[${WS}]?`);
  const STRONG = re("(\\*\\*|__|~~)([^\\n]+?)\\1");
  const EMPHASIS = re(`(?<![${WORD}*])[*_](?![${WS}])([^\\n]+?)(?<![${WS}])[*_](?![${WORD}*])`);
  const TABLE_ROW = re(`^[${WS}]*\\|[^\\n]*\\|[${WS}]*$`);
  const RULE = re(`^[${WS}]*[-=_*|:${WS}]{3,}$`);
  const SYMBOL_CHARS = /[\p{So}\p{Sk}\p{Cs}\p{Co}\u200d\ufe0f]/gu;
  const SPACE_BEFORE_PUNCT = re(`[${WS}]+([,.!?…;:])`);
  const REPEATED_PUNCT = re("([.!?…])\\1+");
  const WS_RUN = re(`[${WS}]+`);
  const STRIP = re(`^[${WS}]+|[${WS}]+$`);
  const PIECES = re(`(?<=[,;:])[${WS}]+|[${WS}]+`);
  const HOUR = re(`(?<![${WORD}])(\\p{Nd}{1,2})[${WS}]*h(?:[${WS}]*(\\p{Nd}{2}))?(?![${WORD}])`);
  const NUMBER = re(`(?<![${WORD}])\\p{Nd}{1,6}(?![${WORD}])`);
  const DIGIT = /^\p{Nd}$/u;
  const SURROGATE = /[\ud800-\udfff]/;

  const UNITS = ["zéro", "un", "deux", "trois", "quatre", "cinq", "six", "sept", "huit",
    "neuf", "dix", "onze", "douze", "treize", "quatorze", "quinze", "seize"];
  const TENS = { 20: "vingt", 30: "trente", 40: "quarante", 50: "cinquante", 60: "soixante",
    70: "septante", 80: "huitante", 90: "nonante" };

  function strip(text) {
    return text.replace(STRIP, "");
  }

  // Length in code points, like Python's len().
  function length(text) {
    if (!SURROGATE.test(text)) return text.length;
    let count = 0;
    for (const _char of text) count += 1;
    return count;
  }

  function requireString(text) {
    if (typeof text !== "string") throw new TypeError("expected a string");
    return text;
  }

  function stripInterfaceBlocks(text) {
    return requireString(text || "").replace(KENOBI_BLOCK, " ");
  }

  function normalize(text) {
    if (typeof text !== "string") return "";
    text = stripInterfaceBlocks(text)
      .replace(CODE_BLOCK, " ")
      .replace(INLINE_CODE, "$1")
      .replace(IMAGE, "$1")
      .replace(LINK, "$1")
      .replace(URL, "lien");
    const lines = [];
    for (let line of text.split(LINE_BREAK)) {
      line = line.replace(HEADING, "").replace(BULLET, "").replace(QUOTE, "").replace(STRONG, "$2")
        .replace(EMPHASIS, "$1").replace(TABLE_ROW, "").replace(RULE, "");
      line = strip(line);
      if (line) lines.push(line);
    }
    // A line becomes a sentence (a pause), without a full stop added to the last one.
    text = lines.map((line, index) =>
      index === lines.length - 1 || ".!?…:;,".includes(line[line.length - 1]) ? line : line + "."
    ).join(" ");
    for (const [pattern, spoken] of SYMBOLS) text = text.replace(pattern, spoken);
    text = text.replace(SYMBOL_CHARS, "").replace(SPACE_BEFORE_PUNCT, "$1").replace(REPEATED_PUNCT, "$1");
    return strip(text.replace(WS_RUN, " "));
  }

  function numberFr(n) {
    if (n < 17) return UNITS[n];
    if (n < 20) return "dix-" + UNITS[n - 10];
    if (n < 100) {
      const units = n % 10;
      const base = TENS[n - units];
      if (units === 0) return base;
      return base + (units === 1 ? " et un" : "-" + UNITS[units]);
    }
    if (n < 1000) {
      const hundreds = Math.floor(n / 100), rest = n % 100;
      const head = hundreds === 1 ? "cent" : UNITS[hundreds] + " cent";
      return rest === 0 ? head : head + " " + numberFr(rest);
    }
    const thousands = Math.floor(n / 1000), rest = n % 1000;
    const head = thousands === 1 ? "mille" : numberFr(thousands) + " mille";
    return rest === 0 ? head : head + " " + numberFr(rest);
  }

  // Value of a run of Unicode decimal digits, like Python's int(): every script encodes 0-9 contiguously.
  function decimal(digits) {
    let value = 0;
    for (const char of digits) {
      const code = char.codePointAt(0);
      let zero = code;
      if (code >= 48 && code <= 57) zero = 48;
      else while (zero > 0 && DIGIT.test(String.fromCodePoint(zero - 1))) zero -= 1;
      value = value * 10 + ((code - zero) % 10);
    }
    return value;
  }

  function hour(hours, minutes) {
    let spoken = (hours === 1 ? "une" : numberFr(hours)) + (hours <= 1 ? " heure" : " heures");
    if (minutes && decimal(minutes)) spoken += " " + numberFr(decimal(minutes));
    return spoken;
  }

  function spokenFr(text) {
    return requireString(text)
      .replace(HOUR, (_match, hours, minutes) => hour(decimal(hours), minutes))
      .replace(NUMBER, (digits) => numberFr(decimal(digits)));
  }

  function sentences(text) {
    return requireString(text).split(SENTENCE_END).map(strip).filter(Boolean);
  }

  function cutLong(sentence, limit) {
    const parts = [];
    let current = "";
    for (const piece of sentence.split(PIECES)) {
      const candidate = strip(current + " " + piece);
      if (current && length(candidate) > limit) {
        parts.push(current);
        current = piece;
      } else {
        current = candidate;
      }
    }
    if (current) parts.push(current);
    return parts;
  }

  function splitSegments(text, firstMax = FIRST_MAX, segmentMax = SEGMENT_MAX) {
    const units = [];
    for (const sentence of sentences(text)) {
      if (length(sentence) > segmentMax) units.push(...cutLong(sentence, segmentMax));
      else units.push(sentence);
    }
    const segments = [];
    let current = "";
    for (const unit of units) {
      const limit = segments.length ? segmentMax : firstMax;
      const candidate = strip(current + " " + unit);
      if (current && (length(candidate) > limit || (!segments.length && length(current) >= FIRST_MIN))) {
        segments.push(current);
        current = unit;
      } else {
        current = candidate;
      }
    }
    if (current) segments.push(current);
    return segments;
  }

  // Releases pieces to say from the model's stream, sentence by sentence.
  class StreamSegmenter {
    constructor() {
      this.buffer = "";
      this.released = 0;
    }

    _ready() {
      if ((this.buffer.split("```").length - 1) % 2) return [];  // inside a code or interface block: wait for its end
      let cut = -1;
      for (const match of this.buffer.matchAll(SENTENCE_END)) cut = match.index + match[0].length;
      if (cut < 0) return [];
      const ready = this.buffer.slice(0, cut);
      this.buffer = this.buffer.slice(cut);
      return this._emit(ready);
    }

    _emit(raw) {
      const text = normalize(raw);
      if (!text) return [];
      const groups = this.released === 0 ? splitSegments(text) : splitSegments(text, SEGMENT_MAX);
      this.released += groups.length;
      return groups;
    }

    feed(delta) {
      if (typeof delta !== "string" || !delta) return [];
      this.buffer += delta;
      return this._ready();
    }

    finish() {
      const raw = this.buffer;
      this.buffer = "";
      return this._emit(raw);
    }
  }

  return {
    FIRST_MIN, FIRST_MAX, SEGMENT_MAX,
    stripInterfaceBlocks, normalize, spokenFr, sentences, splitSegments, StreamSegmenter,
    _cutLong: cutLong, _numberFr: numberFr,
  };
});
