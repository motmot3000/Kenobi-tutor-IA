// Calls of browser/speech.js for tests/test_parity_speech.py (several results per Node start).
"use strict";

const crypto = require("node:crypto");
const S = require("../../browser/speech.js");

const WS = new RegExp("^[\\t\\n\\v\\f\\r\\x1c-\\x1f \\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000]$", "u");

function all(texts) {
  return texts.map((text) => ({
    strip: S.stripInterfaceBlocks(text),
    normalize: S.normalize(text),
    sentences: S.sentences(text),
    segments: S.splitSegments(text),
    segmentsWide: S.splitSegments(text, S.SEGMENT_MAX),
    segmentsSmall: S.splitSegments(text, 30, 60),
    cut: S._cutLong(text, 50),
    spoken: S.spokenFr(text),
    spokenNormal: S.spokenFr(S.normalize(text)),
  }));
}

// For each chunking: the pieces released by every feed(), then by finish().
function stream(chunkings) {
  return chunkings.map((chunks) => {
    const segmenter = new S.StreamSegmenter();
    const released = chunks.map((chunk) => segmenter.feed(chunk));
    released.push(segmenter.finish());
    return released;
  });
}

function numbersText(start, end) {
  const parts = [];
  for (let n = start; n < end; n += 1) parts.push(String(n));
  return parts.join("\n");
}

function numbersDigest(start, end) {
  return crypto.createHash("sha256").update(S.spokenFr(numbersText(start, end)), "utf8").digest("hex");
}

function numbers(start, end) {
  return S.spokenFr(numbersText(start, end)).split("\n");
}

// One character in several contexts, and the character classes this engine gives it (Unicode version check).
function probes(codePoints) {
  return codePoints.map((code) => {
    const char = String.fromCodePoint(code);
    return {
      classes: [WS.test(char), /^[\p{L}\p{N}_]$/u.test(char), /^\p{Nd}$/u.test(char), /^[\p{So}\p{Sk}\p{Co}]$/u.test(char)],
      out: [S.normalize("a" + char + "b"), S.normalize(char + "*x* y"), S.spokenFr("1" + char + "2"), S.sentences("Fin." + char + "Suite")],
    };
  });
}

module.exports = { all, stream, numbersDigest, numbers, probes };
