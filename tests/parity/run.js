// Runs one function of a browser module for the Python parity tests (tests/test_parity_*.py).
//
// stdin : {"module": "tutor", "call": "buildSystem", "args": [...]}
//         (a list of such objects is accepted too: the answers come back in the same order)
// stdout: {"ok": true, "value": ...} or {"ok": false, "error": "message", "name": "ErrorName"}
//
// `call` may name an async function or an async generator: a generator is drained into a list.
// `module` names a file of browser/ ("tutor" → browser/tutor.js), or a test harness of this folder with the
// "test:" prefix ("test:tutor_harness" → tests/parity/tutor_harness.js), for calls that need stubs.
"use strict";

const path = require("node:path");

function modulePath(name) {
  const text = String(name || "");
  if (!/^(test:)?[a-z0-9_-]+$/i.test(text)) throw new Error(`invalid module name ${text}`);
  return text.startsWith("test:")
    ? path.join(__dirname, `${text.slice(5)}.js`)
    : path.join(__dirname, "..", "..", "browser", `${text}.js`);
}

async function runOne(request) {
  try {
    const module = require(modulePath(request.module));
    const target = String(request.call || "").split(".").reduce((value, key) => (value == null ? value : value[key]), module);
    if (typeof target !== "function") throw new Error(`unknown function ${request.module}.${request.call}`);
    let value = target(...(request.args || []));
    if (value && typeof value[Symbol.asyncIterator] === "function") {
      const items = [];
      for await (const item of value) items.push(item);
      value = items;
    } else {
      value = await value;
    }
    return { ok: true, value: value === undefined ? null : value };
  } catch (error) {
    return { ok: false, error: String(error && error.message || error), name: error && error.name || "Error" };
  }
}

let input = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => { input += chunk; });
process.stdin.on("end", async () => {
  const request = JSON.parse(input || "{}");
  const answer = Array.isArray(request) ? await Promise.all(request.map(runOne)) : await runOne(request);
  process.stdout.write(JSON.stringify(answer));
});
