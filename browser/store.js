// Key-value storage of the web version: IndexedDB, one database per site.
// Holds the state (courses, sessions, memory), the documents of imported courses, the settings and the
// encrypted secrets (vault.js). Nothing leaves the browser from here.
(function (root, factory) {
  const api = factory(root);
  if (typeof module === "object" && module.exports) module.exports = api;
  root.KenobiStore = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function (root) {
  "use strict";

  const DB_NAME = "kenobi";
  const STORE = "kv";
  let opening = null;
  let memory = null; // fallback when IndexedDB is unavailable (some private modes): nothing persists

  function open() {
    if (memory) return Promise.resolve(null);
    if (opening) return opening;
    opening = new Promise((resolve) => {
      let request;
      try {
        request = root.indexedDB.open(DB_NAME, 1);
      } catch {
        memory = new Map();
        resolve(null);
        return;
      }
      request.onupgradeneeded = () => {
        if (!request.result.objectStoreNames.contains(STORE)) request.result.createObjectStore(STORE);
      };
      request.onsuccess = () => {
        const db = request.result;
        db.onversionchange = () => db.close();
        resolve(db);
      };
      request.onerror = () => {
        memory = new Map();
        resolve(null);
      };
      request.onblocked = () => {
        memory = new Map();
        resolve(null);
      };
    });
    return opening;
  }

  async function run(mode, action) {
    const db = await open();
    if (!db) return action(null);
    return new Promise((resolve, reject) => {
      const transaction = db.transaction(STORE, mode);
      const store = transaction.objectStore(STORE);
      let result;
      const request = action(store);
      if (request) request.onsuccess = () => { result = request.result; };
      transaction.oncomplete = () => resolve(result);
      transaction.onerror = () => reject(transaction.error || new Error("IndexedDB"));
      transaction.onabort = () => reject(transaction.error || new Error("IndexedDB"));
    });
  }

  async function get(key) {
    if (memory) return memory.get(key);
    const value = await run("readonly", (store) => store && store.get(key));
    return memory ? memory.get(key) : value;
  }

  async function set(key, value) {
    await run("readwrite", (store) => (store ? store.put(value, key) : (memory.set(key, value), null)));
  }

  async function remove(key) {
    await run("readwrite", (store) => (store ? store.delete(key) : (memory.delete(key), null)));
  }

  async function keys(prefix = "") {
    const all = memory ? [...memory.keys()] : await run("readonly", (store) => store && store.getAllKeys());
    return (all || []).map(String).filter((key) => key.startsWith(prefix));
  }

  async function persistent() {
    // Ask the browser to keep this site's data under storage pressure (granted silently or not at all).
    try {
      if (root.navigator?.storage?.persist && !(await root.navigator.storage.persisted())) await root.navigator.storage.persist();
    } catch { /* optional */ }
    return !memory;
  }

  return { get, set, remove, keys, persistent, available: () => !memory };
});
