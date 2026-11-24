// Terminal drawer: the default terminal of this computer (xterm.js + terminal_bridge.py), the lesson editor,
// Run, and the runs handed to app.js so that Kenobi can see them. Kenobi never types in the terminal.
// The course decides what is followed (Python, Node.js or every command) and where the files live.
// Open, the drawer is on: the page is plugged into the bridge and Kenobi sees the runs (Coach or Silencieux).
// Closed, it is off: the page leaves the bridge (the shell keeps running there) and Kenobi sees nothing.
// On the computer that runs server.py the server hands over the bridge token; on any other computer (and in the
// web version) the page pairs once with the
// bridge downloaded from Kenobi (code or link), then finds it again on 127.0.0.1.
(function () {
  "use strict";

  const C = window.KenobiCore;
  const STORAGE_KEY = "kenobi.terminal.v1";
  const PAIRING_KEY = "kenobi.terminal.pairing.v1";
  const BRIDGE_PORTS = [8194, 8195, 8196, 8197, 8198, 8199];
  const DEFAULT_FILE = "exercice.py";
  const DEFAULTS = { open: false, own: false, height: 360, split: 0.44, fontSize: 13, theme: "kenobi", mode: "coach", editor: true, maximized: false };
  const FONT_SIZES = [11, 12, 13, 14, 15, 16, 18];
  const MODES = {
    coach: { label: "Coach", icon: "eye", hint: "Tant qu’une consigne est en cours, Kenobi répond à chaque lancement (réussi, presque, pas encore). Hors consigne, seulement quand ton code plante." },
    silent: { label: "Silencieux", icon: "eye", hint: "Kenobi voit tes exécutions et ton fichier, mais ne parle que quand tu lui écris." },
    off: { label: "Ne regarde pas", icon: "eye-off", hint: "Rien ne part vers Kenobi. Le bouton « Montrer à Kenobi » reste possible." }
  };
  const SCROLLBAR = {
    scrollbarSliderBackground: "rgba(255, 247, 240, 0.14)",
    scrollbarSliderHoverBackground: "rgba(255, 247, 240, 0.26)",
    scrollbarSliderActiveBackground: "rgba(255, 169, 89, 0.45)"
  };
  const THEMES = {
    kenobi: {
      background: "#171c2e", foreground: "#f4ede6", cursor: "#ffa959", cursorAccent: "#171c2e",
      selectionBackground: "rgba(255, 169, 89, 0.34)", selectionInactiveBackground: "rgba(255, 169, 89, 0.18)",
      black: "#2a3350", red: "#f07a72", green: "#7fd396", yellow: "#ffc46b", blue: "#76a7ff", magenta: "#c9a0f5", cyan: "#69d4d1", white: "#d9dde8",
      brightBlack: "#7f89a6", brightRed: "#ff958c", brightGreen: "#a4e6b5", brightYellow: "#ffd893", brightBlue: "#a0c2ff", brightMagenta: "#dfc1ff", brightCyan: "#93e7e4", brightWhite: "#ffffff",
      ...SCROLLBAR
    },
    konsole: {
      background: "#232627", foreground: "#fcfcfc", cursor: "#fcfcfc", cursorAccent: "#232627",
      selectionBackground: "rgba(61, 174, 233, 0.38)", selectionInactiveBackground: "rgba(61, 174, 233, 0.2)",
      black: "#232627", red: "#ed1515", green: "#11d116", yellow: "#f67400", blue: "#1d99f3", magenta: "#9b59b6", cyan: "#1abc9c", white: "#fcfcfc",
      brightBlack: "#7f8c8d", brightRed: "#c0392b", brightGreen: "#1cdc9a", brightYellow: "#fdbc4b", brightBlue: "#3daee9", brightMagenta: "#8e44ad", brightCyan: "#16a085", brightWhite: "#ffffff",
      ...SCROLLBAR
    }
  };
  const STATUS_TEXT = {
    off: "Désactivé · Kenobi ne regarde pas",
    idle: "Prêt",
    connecting: "Connexion…",
    unpaired: "Non branché",
    absent: "Pont pas lancé",
    foreign: "Pont à remplacer",
    rekey: "Code à renouveler",
    blocked: "Bloqué par le navigateur",
    bridge: "Pont du terminal arrêté",
    offline: "Pont injoignable",
    exited: "Terminal fermé",
    replaced: "Ouvert dans un autre onglet"
  };
  // unpaired: no code yet · absent: nothing listens · foreign: something on the port ignores this page (a copy
  // of the bridge from before 1.3.0 set for another address, or another program) · rekey: the bridge refused the
  // code · blocked: the bridge answers but the browser stops the connection.
  const NEEDS_PLUG = new Set(["unpaired", "absent", "foreign", "rekey", "blocked"]);
  // A refused code is not retried by itself: the bridge would print a refusal in its window each time.
  const RETRY_ON_RETURN = new Set(["absent", "foreign", "blocked", "offline"]);
  const PROBE_TIMEOUT = 2500;
  const OS_NAMES = { windows: "Windows", macos: "macOS", linux: "Linux" };
  // Chrome and Edge refuse a page of the network (http) access to this computer (Local Network Access);
  // Firefox lets it through: checked with Firefox 153 ESR on 2026-11-10.
  const BLOCKS_LOCAL_ACCESS = /Chrome\//.test(navigator.userAgent) && !/Firefox\//.test(navigator.userAgent) && !window.isSecureContext;

  const $ = (id) => document.getElementById(id);
  const e = (value) => C.escapeHtml(value);
  const icon = (name) => `<svg class="icon" aria-hidden="true"><use href="#i-${name}"/></svg>`;
  const settings = { ...DEFAULTS, ...readJson(STORAGE_KEY) };
  const pending = new Map();

  let hooks = { onRun: () => "", onShow: () => {}, toast: () => {} };
  let drawer;
  let editor = null;
  let term = null;
  let fitAddon = null;
  let socket = null;
  let serverInfo = null;
  let config = null;
  let status = "idle";
  let info = {};
  let busy = false;
  let lesson = { id: "", title: "", course: "", language: "python", file: DEFAULT_FILE };
  let available = true;
  let currentFolder = "";
  let file = DEFAULT_FILE;
  let loadedKey = "";
  let savedText = "";
  let saveTimer = 0;
  let changeTimer = 0;
  let reconnectTimer = 0;
  let reconnectDelay = 1500;
  let requestCounter = 0;
  let fitFrame = 0;
  let lastRun = null;
  let queuedCode = null;
  let plugOs = C.clientOs(navigator);
  let plugError = "";
  let plugDetail = {};
  let plugBusy = false;
  let pasted = false;
  let typedCode = "";
  let overlayView = null;
  let detected = null;
  let detectTimer = 0;
  let probeCount = 0;
  let connectSeq = 0;

  function readJson(key) {
    try {
      return JSON.parse(localStorage.getItem(key)) || {};
    } catch {
      return {};
    }
  }

  function storeSettings() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
    } catch {
      /* Private mode: settings simply do not persist. */
    }
  }

  // ── Pairing with this computer's bridge ───────────────────────────────────

  function readPairing() {
    const value = readJson(PAIRING_KEY);
    return C.parsePairing(`${value.port}-${value.token}`);
  }

  function storePairing(pairing) {
    try {
      localStorage.setItem(PAIRING_KEY, JSON.stringify({ ...pairing, at: new Date().toISOString() }));
    } catch {
      /* Without storage the pairing lasts until the page is closed. */
    }
  }

  function forgetPairing() {
    try {
      localStorage.removeItem(PAIRING_KEY);
    } catch {
      /* Nothing stored. */
    }
  }

  // The bridge opens Kenobi with #terminal=<port>-<token>: keep it, then clean the address bar.
  function takePairingFromUrl() {
    const pairing = C.parsePairing(location.hash);
    if (!pairing) return false;
    storePairing(pairing);
    history.replaceState(null, "", location.pathname + location.search);
    settings.open = true;
    return true;
  }

  const wsUrl = (port) => `ws://127.0.0.1:${port}/terminal`;

  async function loadConfig() {
    try {
      const response = await fetch("api/terminal", { cache: "no-store" });
      serverInfo = await response.json();
    } catch {
      serverInfo = { available: false, reason: "offline" };
    }
    if (serverInfo.available) config = { ...serverInfo, mode: "local" };
    else if (serverInfo.reason === "remote") {
      const pairing = readPairing();
      config = pairing ? { available: true, mode: "paired", ...pairing, url: wsUrl(pairing.port) } : { available: false, mode: "paired", reason: "unpaired" };
    } else config = { available: false, mode: "local", reason: serverInfo.reason || "offline" };
    // A plug panel stays until connect() knows better: rebuilding it would wipe the code being pasted.
    if (settings.open && !config.available) setStatus(unavailableStatus());
    else if (settings.open && status === "bridge") setStatus("idle");
    updateMenu();
    return config;
  }

  function unavailableStatus() {
    return config?.reason === "unpaired" ? "unpaired" : config?.reason === "bridge" ? "bridge" : "offline";
  }

  // One look at a port of this computer. Our bridge answers /health to this page (CORS); another program, or a
  // bridge from before 1.3.0 set for another address of Kenobi, does not. A loopback answer takes milliseconds:
  // a request still pending after PROBE_TIMEOUT is held by the browser, which waits for a permission.
  async function probe(port, mode = "cors") {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), PROBE_TIMEOUT);
    try {
      const response = await fetch(`http://127.0.0.1:${port}/health`, { cache: "no-store", mode, signal: controller.signal });
      if (mode === "no-cors") return { listens: true };
      const data = await response.json().catch(() => null);
      return data?.ok ? { health: data } : { listens: true };
    } catch (error) {
      return { held: error?.name === "AbortError" };
    } finally {
      clearTimeout(timer);
    }
  }

  // What answers on this computer: bridges that let this page in (the paired port first), whether something holds
  // the paired port without letting the page in (an old copy of the bridge, another program), and whether the
  // browser holds the requests. The other ports are looked at now and then only: each failed look is logged by the
  // browser.
  async function findBridge(preferred, { scan = probeCount++ % 3 === 0 } = {}) {
    const ports = scan ? [preferred, ...BRIDGE_PORTS.filter((item) => item !== preferred)] : [preferred];
    const results = await Promise.all(ports.map((port) => probe(port)));
    const bridges = ports.map((port, index) => ({ port, health: results[index].health })).filter((item) => item.health);
    let held = Boolean(results[0].held);
    let foreign = [];
    if (!results[0].health && !held) {
      const look = results[0].listens ? { listens: true } : await probe(preferred, "no-cors");
      if (look.listens) foreign = [preferred];
      held = Boolean(look.held);
    }
    return { bridges, foreign, held: held && !bridges.length };
  }

  async function connect() {
    clearTimeout(reconnectTimer);
    if (!settings.open || (socket && socket.readyState <= WebSocket.OPEN)) return;
    // A newer attempt (code pasted, retry, drawer closed) makes this one give up at its next step.
    const attempt = ++connectSeq;
    if (!config) await loadConfig();
    if (attempt !== connectSeq || !settings.open) return;
    if (!config?.available) {
      if (config?.reason !== "unpaired") scheduleReconnect();
      return;
    }
    if (!NEEDS_PLUG.has(status)) setStatus("connecting");
    if (config.mode !== "paired") return openSocket([{ url: config.url, health: null }]);
    const found = await findBridge(config.port);
    if (attempt !== connectSeq || !settings.open || socket) return;
    // Without a bridge of ours: what holds the paired port, if anything, says what to do.
    const missing = found.foreign.includes(config.port) ? "foreign" : "absent";
    if (!found.bridges.length) {
      if (found.held) return retryLater("blocked", { held: true });
      // Chrome and Edge stop these requests before they leave the page: the bridge may well be running.
      if (missing === "absent" && BLOCKS_LOCAL_ACCESS) return retryLater("blocked", { chrome: true });
      return retryLater(missing, { port: config.port });
    }
    openSocket(found.bridges.map((item) => ({ ...item, url: wsUrl(item.port) })), { missing, pairedRefused: false });
  }

  // Plug into the first candidate; if it refuses the code, the next bridge that answered (two bridges on this
  // computer) before saying the code is wrong. The port that lets the page in is remembered with the code.
  function openSocket(candidates, chain = { missing: "absent", pairedRefused: false }) {
    const paired = config.mode === "paired";
    const [target, ...others] = candidates;
    const health = target.health;
    let opened;
    try {
      opened = new WebSocket(target.url, ["kenobi-terminal.v1", `token.${config.token}`]);
    } catch {
      return retryLater(paired ? "blocked" : "offline");
    }
    socket = opened;
    let ready = false;
    let denied = null;
    opened.binaryType = "arraybuffer";
    opened.addEventListener("open", () => {
      if (socket === opened) sendResize();
    });
    opened.addEventListener("message", (event) => {
      if (socket !== opened) return;
      if (typeof event.data !== "string") {
        term?.write(new Uint8Array(event.data));
        return;
      }
      let data;
      try {
        data = JSON.parse(event.data);
      } catch {
        return; /* Ignore a malformed event rather than break the terminal. */
      }
      if (data?.t === "denied") {
        denied = data;
        return;
      }
      if (data?.t === "ready") {
        ready = true;
        reconnectDelay = 1500;
        probeCount = 0;
        plugDetail = {};
        plugError = "";
        typedCode = "";
        pasted = false;
        if (paired && target.port !== config.port) {
          Object.assign(config, { port: target.port, url: target.url });
          storePairing({ port: target.port, token: config.token });
        }
      }
      handleEvent(data);
    });
    opened.addEventListener("close", (event) => {
      // An older socket closing after a new one was opened (new code, retry, unplug) changes nothing.
      if (socket !== opened) return;
      socket = null;
      rejectPending("Le terminal s’est déconnecté.");
      if (event.code === 4000) return setStatus("replaced");
      // A refusal on another port than the paired one comes from someone else's bridge: ours is not there (yet).
      if (denied || event.code === 4401 || event.code === 4403) {
        // On the computer that runs the server, the token comes from it: ask again, it may have been renewed.
        if (!paired) return retryLater("offline");
        if (denied?.reason === "origin" || event.code === 4403) return settle("foreign", { origin: true, message: denied?.message || "" });
        if (target.port === config.port) chain.pairedRefused = true;
        if (others.length) return openSocket(others, chain);
        if (!chain.pairedRefused) return retryLater(chain.missing, { port: config.port });
        return settle("rekey", { port: config.port });
      }
      if (!ready && paired) {
        // The bridge answered /health, yet the socket fell before a word from it. A bridge from before 1.3.0
        // refuses a wrong code that way; a newer one always says why, so the browser stopped the connection.
        const oldBridge = (health?.protocol || 1) < 2;
        if (oldBridge && target.port === config.port) chain.pairedRefused = true;
        if (others.length) return openSocket(others, chain);
        if (chain.pairedRefused) return settle("rekey", { port: config.port });
        if (oldBridge || target.port !== config.port) return retryLater(chain.missing, { port: config.port });
        return retryLater("blocked", {});
      }
      retryLater(paired ? "absent" : "offline", { port: config?.port });
    });
  }

  // A plug status that waits for the learner (new code, other address): no retry, the bridge would print a refusal
  // in its window at each attempt.
  function settle(next, detail = {}) {
    plugDetail = detail;
    explainPaste(next);
    setStatus(next);
  }

  function retryLater(next, detail = {}) {
    plugDetail = detail;
    explainPaste(next);
    setStatus(next);
    scheduleReconnect();
  }

  // Right after « Brancher », say what happened to the code, even when the panel stays the same.
  function explainPaste(next) {
    plugBusy = false;
    if (!pasted) return;
    pasted = false;
    if (next === "rekey") plugError = "Le pont a refusé ce code : copie celui qu’affiche sa fenêtre, sous « Code à coller dans Kenobi ».";
    else if (next === "absent") plugError = `Rien ne répond sur le port ${plugDetail.port || ""} : la fenêtre du pont est-elle encore ouverte ?`;
    else plugError = "";
  }

  function scheduleReconnect() {
    clearTimeout(reconnectTimer);
    const paired = config?.mode === "paired";
    reconnectTimer = setTimeout(() => {
      if (!settings.open || socket) return;
      config = null; // reread: another tab may hold a newer code, the server a renewed token
      connect();
    }, reconnectDelay);
    reconnectDelay = Math.min(reconnectDelay * 2, paired ? 4000 : 15000);
  }

  function rejectPending(message) {
    for (const [id, entry] of pending) {
      clearTimeout(entry.timer);
      entry.reject(new Error(message));
      pending.delete(id);
    }
  }

  // Forget the current socket before closing it, so that its closing does not touch the status.
  function dropSocket() {
    const closing = socket;
    socket = null;
    closing?.close();
    rejectPending("Le terminal est débranché.");
  }

  function reconnectNow() {
    clearTimeout(reconnectTimer);
    reconnectDelay = 1500;
    probeCount = 0;
    config = null;
    dropSocket();
    connect();
  }

  function pairWith(text) {
    const pairing = C.parsePairing(text);
    if (!pairing) {
      plugError = "Code illisible : copie toute la ligne que le pont affiche, par exemple 8194-3f9a…";
      overlayView = null;
      updateOverlay();
      return;
    }
    plugError = "";
    plugBusy = true;
    pasted = true;
    storePairing(pairing);
    config = { available: true, mode: "paired", ...pairing, url: wsUrl(pairing.port) };
    clearTimeout(reconnectTimer);
    reconnectDelay = 1500;
    probeCount = 0;
    dropSocket();
    updateOverlay();
    connect();
  }

  function unplug() {
    forgetPairing();
    config = { available: false, mode: "paired", reason: "unpaired" };
    connectSeq += 1;
    clearTimeout(reconnectTimer);
    dropSocket();
    typedCode = "";
    plugError = "";
    setStatus("unpaired");
    updateMenu();
    hooks.toast("Ce navigateur est débranché du terminal de cet ordinateur.");
  }

  // Not paired yet: look now and then for a bridge already running here, to say « colle son code » (or that an
  // old copy holds the port) instead of « télécharge ».
  function updateDetection() {
    clearTimeout(detectTimer);
    if (status !== "unpaired" || !settings.open || !available) {
      detected = null;
      return;
    }
    detectTimer = setTimeout(detectNow, detected === null ? 300 : 3000);
  }

  async function detectNow() {
    clearTimeout(detectTimer);
    if (status !== "unpaired" || !settings.open) return updateDetection();
    if (document.visibilityState === "hidden") {
      detectTimer = setTimeout(detectNow, 3000);
      return;
    }
    // An old copy takes the first port, 8194: another program may well hold the next ones, it is not reported.
    const found = await findBridge(BRIDGE_PORTS[0], { scan: true });
    if (status !== "unpaired") return;
    const next = { port: found.bridges[0]?.port || 0, foreign: found.foreign[0] || 0, held: found.held };
    if (JSON.stringify(next) !== JSON.stringify(detected)) {
      detected = next;
      updateOverlay();
    }
    updateDetection();
  }

  function connected() {
    return Boolean(socket && socket.readyState === WebSocket.OPEN);
  }

  function send(message) {
    if (connected()) socket.send(JSON.stringify(message));
  }

  function request(message, timeout = 10000) {
    return new Promise((resolve, reject) => {
      if (!connected()) {
        reject(new Error("Le terminal n’est pas branché."));
        return;
      }
      const id = `r${++requestCounter}`;
      const timer = setTimeout(() => {
        pending.delete(id);
        reject(new Error("Le pont du terminal ne répond pas."));
      }, timeout);
      pending.set(id, { resolve, reject, timer });
      socket.send(JSON.stringify({ ...message, id }));
    });
  }

  function handleEvent(event) {
    if (event.id && pending.has(event.id)) {
      const entry = pending.get(event.id);
      pending.delete(event.id);
      clearTimeout(entry.timer);
      if (event.t === "error") entry.reject(new Error(event.message || "Demande refusée par le terminal."));
      else entry.resolve(event);
      return;
    }
    if (event.t === "ready") {
      // The bridge replays the end of the screen right after: start from a clean screen, not a double one.
      term?.reset();
      info = event;
      busy = Boolean(event.busy);
      setStatus("ready");
      updateTitle();
      updateMenu();
      editor?.setReadOnly(false);
      if (dirty()) save();
      else loadFile();
      hooks.onActiveChange?.(true);
    } else if (event.t === "state") {
      busy = Boolean(event.busy);
      if (event.integration) info.integration = true;
      updateRunButton();
    } else if (event.t === "cwd") {
      info.cwd = event.cwd;
      updateTitle();
    } else if (event.t === "command-start") {
      busy = true;
      updateRunButton();
      editor?.setDiagnostic(null);
      if (C.isCourseCommand(event.cmd, lesson.language)) showLast({ running: true, cmd: event.cmd });
    } else if (event.t === "command-done") {
      busy = false;
      updateRunButton();
      commandDone(event);
    } else if (event.t === "exit") {
      busy = false;
      setStatus("exited");
    } else if (event.t === "restarted") {
      term?.reset();
    } else if (event.t === "fatal") {
      hooks.toast(event.message);
    }
  }

  function commandDone(event) {
    const run = {
      id: event.runId || `cmd-${Date.now()}`,
      cmd: event.cmd || "",
      exit: event.exit,
      output: event.output || "",
      truncated: Boolean(event.truncated),
      durationMs: event.durationMs,
      cwd: event.cwd || "",
      file: event.file || "",
      code: typeof event.code === "string" ? event.code : "",
      source: event.source || "shell",
      lesson: event.lesson || "",
      at: Date.now()
    };
    if (!run.lesson && run.cwd.replace(/\\/g, "/").endsWith(`/${lesson.id}`)) run.lesson = lesson.id;
    if (run.source === "run" || C.isCourseCommand(run.cmd, lesson.language)) {
      lastRun = run;
      showDiagnostic(run);
      const delivery = hooks.onRun({ ...run, lesson: run.lesson || lesson.id }) || "";
      showLast({ run, delivery });
    }
    if (!dirty()) reloadFileIfChanged();
  }

  // The error of the last run, shown on its line in the editor until the next edit.
  function showDiagnostic(run) {
    if (!editor || run.file !== file || run.lesson !== lesson.id) return;
    const summary = C.summarizeRun(run);
    if (summary.status === "error" && summary.errorLine) {
      editor.setDiagnostic({ line: summary.errorLine, text: `${summary.errorType}${summary.errorMessage ? ` : ${summary.errorMessage}` : ""}` });
    } else editor.setDiagnostic(null);
  }

  // ── Status, bar and overlay ───────────────────────────────────────────────

  function setStatus(next) {
    if (next !== status && !NEEDS_PLUG.has(next)) plugBusy = false;
    status = next;
    drawer.dataset.status = next;
    $("terminal-status-text").textContent = next === "ready" ? `${info.shellLabel || info.shell || "Terminal"} · ${info.host || "cet ordinateur"}` : STATUS_TEXT[next] || next;
    $("terminal-plug").hidden = !NEEDS_PLUG.has(next) || !settings.open;
    updateOverlay();
    updateRunButton();
    updateDetection();
  }

  function simpleOverlay(title, text, action, label) {
    return `
      <div class="term-overlay-card">
        ${icon("terminal")}
        <strong>${e(title)}</strong>
        <p>${e(text)}</p>
        ${label ? `<button class="button primary" type="button" data-plug="${action}">${e(label)}</button>` : ""}
      </div>`;
  }

  function plugCopy() {
    const port = plugDetail.port || BRIDGE_PORTS[0];
    if (status === "foreign" && plugDetail.origin) {
      return ["Le pont refuse l’adresse de cette page", `${plugDetail.message || "Ce pont n’accepte pas cette adresse."} Ou ouvre Kenobi par l’adresse du réseau local.`];
    }
    return {
      unpaired: ["Brancher le terminal de cet ordinateur", "Kenobi utilisera ton terminal habituel : PowerShell sous Windows, zsh ou bash sous macOS et Linux. Une seule fois par ordinateur."],
      absent: ["Le pont de cet ordinateur n’est pas lancé", "Relance-le : le terminal se rebranche tout seul dans les secondes qui suivent."],
      foreign: ["Le pont lancé ne répond pas à cette page", `Sur le port ${port} tourne une ancienne copie du pont, réglée pour une autre adresse de Kenobi, ou un autre programme. Ferme la fenêtre de ce pont, puis lance celui que tu télécharges ici : il se branche tout seul.`],
      rekey: ["Le pont a changé de code", "Ce navigateur garde un ancien code : pont relancé avec --nouveau-jeton, ou code d’un autre ordinateur. Colle celui qu’affiche la fenêtre du pont."],
      blocked: ["Le navigateur bloque la connexion au pont", plugDetail.chrome
        ? "Chrome et Edge interdisent à une page du réseau de joindre cet ordinateur. Ouvre Kenobi dans Firefox, à la même adresse, puis colle le code du pont : le pont, lui, peut rester lancé."
        : plugDetail.held
          ? "Le navigateur attend ton accord pour que cette page joigne le pont de cet ordinateur : accepte sa demande (fenêtre ou icône à gauche de l’adresse), puis Réessayer."
          : "Le pont répond, mais le navigateur coupe la connexion. Autorise cette page à joindre les appareils locaux (icône à gauche de l’adresse) ou désactive l’extension qui bloque, puis Réessayer."]
    }[status];
  }

  // What the detection found while no code is set yet: a bridge to paste the code of, or an old copy in the way.
  function detectionNote() {
    if (status !== "unpaired" || !detected) return "";
    const notes = [];
    if (detected.port) notes.push(`<p class="plug-found">${icon("check")}Pont détecté sur le port ${e(detected.port)} : colle le code qu’affiche sa fenêtre.</p>`);
    if (detected.foreign) notes.push(`<p class="plug-warning">${icon("info")}Le port ${e(detected.foreign)} est pris par une ancienne copie du pont ou un autre programme. Si c’est un ancien pont, ferme sa fenêtre et lance celui que tu télécharges ici.</p>`);
    if (detected.held) notes.push(`<p class="plug-warning">${icon("info")}Le navigateur demande ton accord pour joindre cet ordinateur : accepte-le (icône à gauche de l’adresse).</p>`);
    return notes.join("");
  }

  function plugOverlay() {
    const launch = C.bridgeLaunch(plugOs);
    const download = `${serverInfo?.download || "api/terminal/pont"}?kenobi=${encodeURIComponent(location.origin)}`;
    const copy = plugCopy();
    const fresh = status === "unpaired" || (status === "foreign" && !plugDetail.origin);
    const steps = [];
    if (fresh) {
      steps.push(`
        <li>
          <div class="plug-step-copy"><strong>Télécharge le pont</strong><span>Un seul fichier Python, réglé pour ce Kenobi.</span></div>
          <a class="button secondary plug-download" href="${e(download)}" download="kenobi-terminal.py">${icon("download")}kenobi-terminal.py</a>
        </li>`);
    }
    if (fresh || status === "absent") {
      steps.push(`
        <li>
          <div class="plug-step-copy"><strong>${status === "absent" ? "Relance le pont" : "Lance-le"}</strong><span>${e(launch.where)} :</span></div>
          <div class="plug-command"><code>${e(launch.command)}</code><button type="button" data-plug="copy-command" aria-label="Copier la commande">${icon("copy")}<span>Copier</span></button></div>
          <p class="plug-note">${e(launch.note)}</p>
        </li>`);
    }
    if (status !== "blocked") {
      const lead = status === "rekey" ? ["Colle son code", "La fenêtre du pont l’affiche sous « Code à coller dans Kenobi »."]
        : status === "absent" ? ["S’il affiche un autre code", "Colle-le ici :"]
        : ["Colle le code du pont", "Kenobi s’ouvre souvent tout seul, déjà branché. Sinon, colle le code que la fenêtre du pont affiche :"];
      steps.push(`
        <li>
          <div class="plug-step-copy"><strong>${e(lead[0])}</strong><span>${e(lead[1])}</span></div>
          <form class="plug-code" data-plug-form>
            <input type="text" name="code" inputmode="text" autocomplete="off" spellcheck="false" placeholder="8194-3f9a…" aria-label="Code affiché par le pont">
            <button class="button primary" type="submit"${plugBusy ? " disabled" : ""}>${icon("plug")}${plugBusy ? "Branchement…" : "Brancher"}</button>
          </form>
          ${plugError ? `<p class="plug-error" role="alert">${e(plugError)}</p>` : ""}
        </li>`);
    }
    return `
      <section class="plug-panel" aria-labelledby="plug-title">
        <header class="plug-head">
          <span class="plug-icon">${icon("plug")}</span>
          <div><h3 id="plug-title">${e(copy[0])}</h3><p>${e(copy[1])}</p></div>
        </header>
        ${BLOCKS_LOCAL_ACCESS ? `<p class="plug-warning">${icon("info")}Chrome et Edge bloquent le branchement depuis une page du réseau : ouvre Kenobi dans Firefox.</p>` : ""}
        ${detectionNote()}
        ${fresh || status === "absent" ? `<div class="plug-os" role="radiogroup" aria-label="Système de cet ordinateur">${Object.entries(OS_NAMES).map(([os, name]) => `<button type="button" role="radio" data-plug-os="${os}" aria-checked="${os === plugOs}">${e(name)}</button>`).join("")}</div>` : ""}
        ${steps.length ? `<ol class="plug-steps">${steps.join("")}</ol>` : ""}
        <footer class="plug-foot">
          <p>${icon("lock")}Le pont n’écoute que cet ordinateur et ne laisse entrer que Kenobi. Kenobi ne tape jamais dans ton terminal.</p>
          ${status !== "unpaired" ? `<button class="text-button" type="button" data-plug="retry">${icon("repeat")}Réessayer</button>` : ""}
        </footer>
      </section>`;
  }

  // The same panel is not rebuilt: what is being typed and the focus stay in place during the retries.
  function updateOverlay() {
    const overlay = $("term-overlay");
    let view = "";
    if (NEEDS_PLUG.has(status)) view = plugOverlay();
    else if (status === "bridge") view = simpleOverlay("Le pont du terminal est arrêté", "Relance-le sur l’ordinateur qui fait tourner Kenobi : python3 terminal_bridge.py --service (ou son service, par exemple systemctl --user restart kenobi-terminal.service)", "retry", "Réessayer");
    else if (status === "offline") view = simpleOverlay("Le pont du terminal ne répond pas", "Nouvelle tentative automatique dans quelques secondes. S’il reste arrêté, relance-le : python3 terminal_bridge.py --service (ou son service).", "retry", "Réessayer");
    else if (status === "exited") view = simpleOverlay("Le terminal s’est fermé", "Le shell a quitté (commande exit).", "restart", "Relancer le terminal");
    else if (status === "replaced") view = simpleOverlay("Terminal ouvert dans un autre onglet", "Un seul onglet à la fois pilote le terminal.", "retry", "Le reprendre ici");
    if (view === overlayView) return;
    overlayView = view;
    const active = overlay.contains(document.activeElement) ? document.activeElement : null;
    const focused = active ? active.name || active.dataset.plugOs || active.dataset.plug || "" : null;
    overlay.classList.toggle("is-plug", NEEDS_PLUG.has(status));
    overlay.hidden = !view;
    overlay.innerHTML = view;
    const input = overlay.querySelector("input[name=code]");
    if (input) input.value = typedCode;
    if (focused === "code") input?.focus();
    else if (focused) overlay.querySelector(`[data-plug-os="${focused}"], [data-plug="${focused}"]`)?.focus();
  }

  function updateTitle() {
    const where = info.cwd || info.workspace || "";
    $("term-title").textContent = where ? `${info.shellLabel || info.shell || "Terminal"} · ${where}` : info.shellLabel || "Terminal";
    $("term-title").title = [info.python, info.osLabel, info.host].filter(Boolean).join(" · ");
  }

  function showLast({ run, running, cmd, delivery, reply = false, verdict = "" }) {
    const box = $("terminal-last");
    if (running) {
      box.className = "terminal-last is-running";
      box.innerHTML = `<span class="last-pulse" aria-hidden="true"></span><span class="last-cmd">${e(cmd)}</span><span>en cours</span>`;
      box.hidden = false;
      return;
    }
    const summary = C.summarizeRun(run);
    const tone = summary.status === "ok" ? "ok" : summary.status === "interrupted" ? "stopped" : "error";
    const seconds = C.formatSeconds(Math.max(run.durationMs || 0, 100) / 1000);
    const deliveryHtml = !delivery ? "" : reply
      ? `<button class="last-delivery is-reply ${verdict ? `is-${e(verdict)}` : ""}" type="button" data-term="show-reply"><img src="assets/kenobi.svg" alt="" width="16" height="16">${e(delivery)}</button>`
      : `<span class="last-delivery">${e(delivery)}</span>`;
    box.className = `terminal-last is-${tone}`;
    box.innerHTML = `${icon(tone === "ok" ? "check" : tone === "stopped" ? "stop" : "close")}<span class="last-label">${e(C.runLabel(summary, run.exit))}</span><span class="last-meta">${e(seconds)}</span>${deliveryHtml}`;
    box.title = run.cmd;
    box.hidden = false;
  }

  function setDelivery(runId, label, options = {}) {
    if (lastRun && lastRun.id === runId) showLast({ run: lastRun, delivery: label, reply: Boolean(options.reply), verdict: options.verdict || "" });
  }

  function updateRunButton() {
    const button = $("run-button");
    if (!button) return;
    const usable = status === "ready" && Boolean(lesson.id);
    const stop = usable && busy;
    drawer.dataset.busy = String(Boolean(stop));
    button.disabled = !usable;
    button.classList.toggle("is-stop", stop);
    button.innerHTML = stop ? `${icon("stop")}<span>Arrêter</span>` : `${icon("play")}<span>Run</span>`;
    button.title = !lesson.id ? "Ouvre une leçon pour lancer ton code" : stop ? "Arrêter le programme (Ctrl+C)" : "Enregistrer et lancer le fichier (Ctrl+Entrée)";
  }

  function updateModeButton() {
    const mode = MODES[settings.mode] ? settings.mode : "coach";
    $("terminal-mode-label").textContent = MODES[mode].label;
    $("terminal-mode-button").dataset.mode = mode;
    $("terminal-mode-button").querySelector("use").setAttribute("href", `#i-${MODES[mode].icon}`);
    document.querySelectorAll("#terminal-mode-popover [data-mode]").forEach((option) => {
      option.setAttribute("aria-checked", String(option.dataset.mode === mode));
    });
  }

  function updateMenu() {
    const theme = document.querySelector('#terminal-menu [data-term="theme"] span');
    if (theme) theme.textContent = settings.theme === "konsole" ? "Couleurs de Kenobi" : "Couleurs de Konsole";
    const toggle = document.querySelector('#terminal-menu [data-term="editor"] span');
    if (toggle) toggle.textContent = settings.editor ? "Masquer l’éditeur" : "Afficher l’éditeur";
    const unplugItem = document.querySelector('#terminal-menu [data-term="unplug"]');
    if (unplugItem) unplugItem.hidden = config?.mode !== "paired" || !config?.available;
  }

  // ── Drawer layout ─────────────────────────────────────────────────────────

  function setOpen(open, { focus = false } = {}) {
    const wasOpen = settings.open;
    settings.open = open;
    storeSettings();
    drawer.dataset.state = open ? "open" : "closed";
    document.body.classList.toggle("terminal-open", open);
    $("terminal-body").hidden = !open;
    $("terminal-resize").hidden = !open;
    $("terminal-maximize").hidden = !open;
    $("terminal-toggle").setAttribute("aria-expanded", String(open));
    $("terminal-toggle").title = open ? "Fermer et désactiver le terminal (Ctrl+J)" : "Ouvrir et activer le terminal (Ctrl+J)";
    if (!open) {
      deactivate();
      return;
    }
    ensureEditor();
    ensureTerminal();
    scheduleFit();
    if (!wasOpen && settings.own) {
      settings.own = false;
      storeSettings();
      hooks.toast("Terminal intégré réactivé : Kenobi voit de nouveau ce que tu lances.");
    }
    activate();
    if (focus) requestAnimationFrame(() => {
      if (NEEDS_PLUG.has(status)) $("term-overlay").querySelector("a, input, button")?.focus();
      else if (settings.editor) editor?.focus();
      else term?.focus();
    });
  }

  // Open: plug into the bridge (or show how to plug this computer).
  async function activate() {
    if (socket) return;
    if (!config) await loadConfig();
    if (!settings.open || socket) return;
    if (config?.available) connect();
    else if (config) setStatus(unavailableStatus());
  }

  // Closed: save the file, leave the bridge, forget what Kenobi would have received. The shell keeps running
  // in the bridge and comes back as it was when the drawer opens again.
  async function deactivate() {
    clearTimeout(reconnectTimer);
    clearTimeout(saveTimer);
    connectSeq += 1;
    if (connected() && dirty()) await save();
    if (settings.open) return;
    dropSocket();
    busy = false;
    lastRun = null;
    $("terminal-last").hidden = true;
    editor?.setReadOnly(true);
    setStatus("off");
    hooks.onActiveChange?.(false);
  }

  // « Je passe sur mon terminal » : off until the learner opens it again, and Kenobi is told in the conversation.
  function switchToOwnTerminal() {
    settings.own = true;
    setOpen(false);
    hooks.onOwnTerminal?.();
  }

  function applyLayout() {
    drawer.style.setProperty("--terminal-height", `${settings.height}px`);
    drawer.classList.toggle("is-maximized", settings.maximized);
    $("terminal-maximize").setAttribute("aria-pressed", String(settings.maximized));
    $("terminal-maximize").setAttribute("aria-label", settings.maximized ? "Réduire le terminal" : "Agrandir le terminal");
    $("terminal-maximize").querySelector("use").setAttribute("href", settings.maximized ? "#i-shrink" : "#i-expand");
    $("terminal-body").style.gridTemplateColumns = settings.editor ? `minmax(0, ${settings.split}fr) 7px minmax(0, ${1 - settings.split}fr)` : "minmax(0, 1fr)";
    $("editor-pane").hidden = !settings.editor;
    $("pane-splitter").hidden = !settings.editor;
    drawer.dataset.theme = settings.theme;
    drawer.style.setProperty("--editor-font-size", `${settings.fontSize}px`);
    scheduleFit();
  }

  function scheduleFit() {
    cancelAnimationFrame(fitFrame);
    fitFrame = requestAnimationFrame(() => {
      editor?.refresh();
      if (!term || !fitAddon || drawer.dataset.state !== "open") return;
      const host = $("xterm-host");
      if (!host.clientWidth || !host.clientHeight) return;
      try {
        fitAddon.fit();
      } catch {
        /* The terminal is hidden for a moment: the next resize fits it. */
      }
    });
  }

  function sendResize() {
    if (term) send({ t: "resize", cols: term.cols, rows: term.rows });
  }

  function drag(event, onMove) {
    event.preventDefault();
    const move = (moveEvent) => {
      onMove(moveEvent);
      applyLayout();
    };
    const stop = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
      drawer.classList.remove("is-resizing");
      storeSettings();
    };
    drawer.classList.add("is-resizing");
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
  }

  function dragResize(event) {
    const shell = drawer.parentElement.getBoundingClientRect();
    const bottom = drawer.getBoundingClientRect().bottom;
    drag(event, (moveEvent) => {
      settings.height = Math.max(190, Math.min(Math.round(bottom - moveEvent.clientY), shell.height - 150));
      settings.maximized = false;
    });
  }

  function dragSplit(event) {
    const body = $("terminal-body").getBoundingClientRect();
    drag(event, (moveEvent) => {
      settings.split = Math.max(0.25, Math.min(0.72, (moveEvent.clientX - body.left) / body.width));
    });
  }

  function nudge(key, step, apply) {
    if (!["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"].includes(key)) return false;
    apply(key === "ArrowUp" || key === "ArrowRight" ? step : -step);
    applyLayout();
    storeSettings();
    return true;
  }

  // ── xterm.js ──────────────────────────────────────────────────────────────

  function ensureTerminal() {
    if (term || typeof window.Terminal !== "function") return;
    term = new window.Terminal({
      fontFamily: '"DejaVu Sans Mono", "Cascadia Mono", Consolas, "SF Mono", Menlo, "Liberation Mono", monospace',
      fontSize: settings.fontSize,
      lineHeight: 1.18,
      cursorBlink: true,
      scrollback: 5000,
      theme: THEMES[settings.theme] || THEMES.kenobi,
      drawBoldTextInBrightColors: true,
      macOptionIsMeta: true
    });
    fitAddon = new window.FitAddon.FitAddon();
    term.loadAddon(fitAddon);
    term.open($("xterm-host"));
    term.onData((data) => send({ t: "input", d: data }));
    term.onBinary((data) => {
      if (connected()) socket.send(Uint8Array.from(data, (character) => character.charCodeAt(0) & 255));
    });
    term.onResize(sendResize);
    term.attachCustomKeyEventHandler((event) => {
      if (event.type !== "keydown") return true;
      const key = event.key.toLowerCase();
      if (event.ctrlKey && !event.altKey && key === "c" && (event.shiftKey || term.hasSelection())) {
        event.preventDefault();
        copy();
        return false;
      }
      if (event.ctrlKey && !event.altKey && key === "v") return false;
      if (event.ctrlKey && (key === "enter" || key === "j")) return false;
      return true;
    });
    new ResizeObserver(scheduleFit).observe($("xterm-host"));
  }

  function screenText() {
    if (!term) return { text: "", selection: false };
    const selected = term.getSelection();
    if (selected.trim()) return { text: selected, selection: true };
    const buffer = term.buffer.active;
    const lines = [];
    for (let row = buffer.viewportY; row < buffer.viewportY + term.rows; row += 1) {
      lines.push(buffer.getLine(row)?.translateToString(true) ?? "");
    }
    while (lines.length && !lines[lines.length - 1].trim()) lines.pop();
    return { text: lines.join("\n"), selection: false };
  }

  async function writeClipboard(text) {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return;
    }
    const area = document.createElement("textarea");
    area.value = text;
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    const done = document.execCommand("copy");
    area.remove();
    if (!done) throw new Error("copie refusée");
  }

  async function copy() {
    const selected = term?.getSelection() || "";
    const text = selected || lastRun?.output || screenText().text;
    if (!text.trim()) return hooks.toast("Rien à copier pour l’instant.");
    try {
      await writeClipboard(text);
      term?.clearSelection();
      hooks.toast(selected ? "Sélection copiée." : lastRun?.output ? "Dernière sortie copiée." : "Écran du terminal copié.");
    } catch {
      hooks.toast("Le navigateur refuse la copie : sélectionne, puis Ctrl+C.");
    }
  }

  async function paste() {
    if (!term) return;
    try {
      const text = await navigator.clipboard.readText();
      if (text) term.paste(text);
      term.focus();
    } catch {
      hooks.toast("Le navigateur refuse la lecture du presse-papiers : clique dans le terminal, puis Ctrl+V.");
    }
  }

  function clearScreen() {
    if (!term) return;
    term.clear();
    if (!busy) send({ t: "input", d: "\x0c" });
    term.focus();
  }

  function setFontSize(direction) {
    const index = FONT_SIZES.indexOf(settings.fontSize);
    settings.fontSize = FONT_SIZES[Math.max(0, Math.min(FONT_SIZES.length - 1, (index === -1 ? 2 : index) + direction))];
    storeSettings();
    if (term) term.options.fontSize = settings.fontSize;
    applyLayout();
  }

  function toggleTheme() {
    settings.theme = settings.theme === "konsole" ? "kenobi" : "konsole";
    storeSettings();
    if (term) term.options.theme = THEMES[settings.theme];
    applyLayout();
    updateMenu();
  }

  // ── Editor and lesson files ───────────────────────────────────────────────

  function ensureEditor() {
    if (editor || !window.KenobiEditor) return;
    editor = window.KenobiEditor.create($("code-editor"), {
      label: "Code du fichier de la leçon",
      placeholder: "Écris ton code ici, puis Run (Ctrl+Entrée).",
      onChange: () => {
        updateDirty();
        clearTimeout(saveTimer);
        saveTimer = setTimeout(save, 700);
        notifyEditorChange();
      },
      onRun: run,
      onSave: () => save({ announce: true })
    });
    editor.setReadOnly(status !== "ready");
  }

  function dirty() {
    return Boolean(editor) && editor.getValue() !== savedText;
  }

  function notifyEditorChange() {
    clearTimeout(changeTimer);
    changeTimer = setTimeout(() => hooks.onEditorChange?.(), 400);
  }

  function updateDirty() {
    $("file-dirty").hidden = !dirty();
  }

  // The lesson folder as the bridge names it; before the first answer, the one we expect.
  function lessonFolder() {
    if (currentFolder) return currentFolder;
    if (!lesson.id) return "";
    const sep = info.sep || "/";
    const base = lesson.course && lesson.course !== "python" && info.root ? `${info.root}${sep}${lesson.course}` : info.workspace;
    return base ? `${base}${sep}${lesson.id}` : "";
  }

  function updateFileLabels() {
    $("file-name").textContent = file;
    const sep = info.sep || "/";
    const folder = lessonFolder();
    $("file-path").textContent = folder;
    $("file-path").title = folder ? `${folder}${sep}${file}` : "";
    editor?.setLanguage(/\.py$/i.test(file) ? "python" : "text");
  }

  function rememberFolder(path) {
    const text = String(path || "");
    const cut = Math.max(text.lastIndexOf("/"), text.lastIndexOf("\\"));
    if (cut > 0) currentFolder = text.slice(0, cut);
  }

  async function loadFile({ force = false } = {}) {
    if (!editor || !lesson.id || !connected()) return;
    const key = `${lesson.id}/${file}`;
    if (!force && key === loadedKey && dirty()) return;
    try {
      const result = await request({ t: "open", lesson: lesson.id, course: lesson.course, file });
      if (`${lesson.id}/${file}` !== key) return;
      savedText = result.code || "";
      rememberFolder(result.path);
      editor.setValue(savedText, { keepPosition: key === loadedKey });
      if (key !== loadedKey) editor.setDiagnostic(null);
      loadedKey = key;
      if (queuedCode !== null) {
        editor.replaceAll(queuedCode);
        queuedCode = null;
      }
      updateDirty();
      updateFileLabels();
      notifyEditorChange();
    } catch (error) {
      hooks.toast(error.message);
    }
  }

  async function reloadFileIfChanged() {
    if (!editor || !lesson.id || !connected() || dirty()) return;
    try {
      const result = await request({ t: "open", lesson: lesson.id, course: lesson.course, file });
      if (!dirty() && `${lesson.id}/${file}` === loadedKey && result.code !== savedText) {
        savedText = result.code || "";
        editor.setValue(savedText, { keepPosition: true });
        updateDirty();
      }
    } catch {
      /* The next command or focus will try again. */
    }
  }

  async function save({ announce = false } = {}) {
    clearTimeout(saveTimer);
    if (!editor || !lesson.id || !connected()) return false;
    const text = editor.getValue();
    if (text === savedText && !announce) return true;
    try {
      await request({ t: "save", lesson: lesson.id, course: lesson.course, file, code: text });
      if (`${lesson.id}/${file}` === loadedKey) savedText = text;
      updateDirty();
      if (announce) hooks.toast(`${file} enregistré.`);
      return true;
    } catch (error) {
      hooks.toast(`${file} non enregistré : ${error.message}`);
      return false;
    }
  }

  async function switchFile(name) {
    if (name === file) return;
    if (dirty()) await save();
    file = name;
    loadedKey = "";
    updateFileLabels();
    await loadFile({ force: true });
  }

  async function refreshFileList() {
    const list = $("file-list");
    $("file-error").hidden = true;
    list.innerHTML = `<li class="file-empty">Lecture du dossier…</li>`;
    try {
      const result = await request({ t: "list", lesson: lesson.id, course: lesson.course });
      $("file-popover-path").textContent = result.path || "";
      const names = result.files.map((item) => item.name);
      if (!names.includes(file)) names.unshift(file);
      list.innerHTML = names.map((name) => `<li><button type="button" class="file-choice" data-file="${e(name)}" aria-current="${name === file}">${icon("file")}<span>${e(name)}</span>${name === file ? icon("check") : ""}</button></li>`).join("");
    } catch (error) {
      list.innerHTML = `<li class="file-empty">${e(error.message)}</li>`;
    }
  }

  async function createFile(event) {
    event.preventDefault();
    const input = $("file-new-name");
    let name = input.value.trim();
    if (!name) return;
    if (!/\.[A-Za-z0-9]+$/.test(name)) name += (lesson.file || DEFAULT_FILE).match(/\.[A-Za-z0-9]+$/)?.[0] || ".py";
    const error = $("file-error");
    try {
      const opened = await request({ t: "open", lesson: lesson.id, course: lesson.course, file: name });
      if (!opened.exists) await request({ t: "save", lesson: lesson.id, course: lesson.course, file: name, code: "" });
      input.value = "";
      $("file-popover").hidePopover();
      await switchFile(name);
      editor?.focus();
    } catch (failure) {
      error.textContent = failure.message;
      error.hidden = false;
    }
  }

  // ── Run ───────────────────────────────────────────────────────────────────

  async function run() {
    if (busy && status === "ready") {
      send({ t: "interrupt" });
      return;
    }
    if (!lesson.id) return hooks.toast("Ouvre une leçon pour lancer ton code.");
    if (drawer.dataset.state !== "open") setOpen(true);
    if (!connected()) return hooks.toast(NEEDS_PLUG.has(status) ? "Branche d’abord le terminal de cet ordinateur." : "Le terminal n’est pas encore branché.");
    clearTimeout(saveTimer);
    const code = editor ? editor.getValue() : null;
    try {
      const result = await request({ t: "run", lesson: lesson.id, course: lesson.course, file, code });
      if (result.t === "run-refused") {
        hooks.toast(result.message);
        return;
      }
      if (code !== null && `${lesson.id}/${file}` === loadedKey) savedText = code;
      updateDirty();
      if (!result.captured) hooks.toast("Ce terminal ne signale pas ses commandes : Kenobi ne verra pas cette sortie.");
      term?.focus();
    } catch (error) {
      hooks.toast(error.message);
    }
  }

  // ── Popovers ──────────────────────────────────────────────────────────────

  // Opens toward the side with more room (the bar moves with the drawer), never past the window.
  function place(popover, anchor, { alignEnd = false } = {}) {
    if (!anchor) return;
    const rect = anchor.getBoundingClientRect();
    const below = window.innerHeight - rect.bottom - 14;
    const above = rect.top - 14;
    const up = below < 320 && above > below;
    Object.assign(popover.style, { position: "fixed", margin: "0", inset: "auto", maxHeight: `${Math.max(160, up ? above : below)}px` });
    if (up) popover.style.bottom = `${window.innerHeight - rect.top + 6}px`;
    else popover.style.top = `${rect.bottom + 6}px`;
    if (alignEnd) popover.style.right = `${Math.max(8, window.innerWidth - rect.right)}px`;
    else popover.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - 300))}px`;
  }

  function buildModeOptions() {
    $("mode-options").innerHTML = Object.entries(MODES).map(([mode, item]) => `
      <button class="mode-option" type="button" role="radio" data-mode="${mode}" aria-checked="false">
        <span class="mode-icon">${icon(item.icon)}</span>
        <span class="mode-copy"><strong>${e(item.label)}</strong><small>${e(item.hint)}</small></span>
        <svg class="icon selected-check" aria-hidden="true"><use href="#i-check"/></svg>
      </button>`).join("");
  }

  // ── Public API ────────────────────────────────────────────────────────────

  function setLesson(next) {
    const id = next?.id || "";
    const course = next?.course || "";
    setAvailable(next ? next.terminal !== false : true);
    if (id === lesson.id && course === lesson.course) {
      lesson.title = next?.title || lesson.title;
      return;
    }
    if (dirty()) save();
    lesson = { id, title: next?.title || "", course, language: next?.language || "python", file: next?.file || DEFAULT_FILE };
    file = lesson.file;
    loadedKey = "";
    currentFolder = "";
    if (editor) {
      editor.setDiagnostic(null);
      if (!id) editor.setValue("");
    }
    updateFileLabels();
    updateRunButton();
    if (status === "ready") loadFile({ force: true });
  }

  // A course without terminal hides the drawer; the choice « open » comes back with the next course that has one.
  function setAvailable(next) {
    if (next === available || !drawer) return;
    available = next;
    drawer.hidden = !next;
    document.body.classList.toggle("terminal-open", next && settings.open);
    if (!next) {
      clearTimeout(reconnectTimer);
      connectSeq += 1;
      if (connected() && dirty()) save();
      dropSocket();
      busy = false;
      lastRun = null;
      $("terminal-last").hidden = true;
      hooks.onActiveChange?.(false);
    } else if (settings.open) setOpen(true);
    else setStatus("off");
  }

  function showEditor() {
    if (!settings.editor) {
      settings.editor = true;
      storeSettings();
      applyLayout();
      updateMenu();
    }
  }

  function loadCode(code) {
    showEditor();
    setOpen(true);
    if (!editor) return;
    const text = String(code || "").replace(/\n?$/, "\n");
    if (status !== "ready" || !loadedKey) {
      queuedCode = text;
      hooks.toast(NEEDS_PLUG.has(status) ? "Branche le terminal : le code ira dans l’éditeur." : "Le code ira dans l’éditeur dès que le terminal sera prêt.");
      return;
    }
    editor.replaceAll(text);
    editor.focus();
    hooks.toast("Code placé dans l’éditeur. Ctrl+Z pour revenir en arrière.");
  }

  async function openFile(name) {
    showEditor();
    setOpen(true, { focus: true });
    if (name && name !== file && /^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$/.test(name)) await switchFile(name);
    if (status === "ready") editor?.focus();
  }

  async function goToLine(name, line, lessonId) {
    if (lessonId && lessonId !== lesson.id) return hooks.toast("Ce code appartient à une autre leçon que celle ouverte.");
    showEditor();
    setOpen(true);
    if (name && name !== file && /^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$/.test(name)) await switchFile(name);
    if (status === "ready") editor?.goToLine(line);
  }

  function currentFile() {
    if (!editor || !lesson.id || !loadedKey) return null;
    return { file, code: editor.getValue(), lesson: lesson.id, course: lesson.course };
  }

  function placeAndShow(popoverId, anchor, options) {
    const popover = $(popoverId);
    place(popover, anchor, options);
    popover.showPopover();
  }

  function handleTerminalAction(action, button) {
    if (action === "copy") copy();
    else if (action === "paste") paste();
    else if (action === "clear") clearScreen();
    else if (action === "interrupt") send({ t: "interrupt" });
    else if (action === "show") {
      const snapshot = screenText();
      if (!snapshot.text.trim()) hooks.toast("L’écran du terminal est vide.");
      else hooks.onShow(snapshot);
    } else if (action === "show-reply") hooks.onShowReply?.();
    else if (action === "restart") {
      send({ t: "restart" });
      hooks.toast("Terminal relancé.");
    } else if (action === "font-up") setFontSize(1);
    else if (action === "font-down") setFontSize(-1);
    else if (action === "theme") toggleTheme();
    else if (action === "editor") {
      settings.editor = !settings.editor;
      storeSettings();
      applyLayout();
      updateMenu();
    } else if (action === "unplug") unplug();
    else if (action === "keys") {
      $("terminal-menu").hidePopover?.();
      placeAndShow("terminal-keys", document.querySelector('[popovertarget="terminal-menu"]'), { alignEnd: true });
      return;
    }
    if (button?.closest("#terminal-menu")) $("terminal-menu").hidePopover?.();
  }

  function handlePlugAction(target) {
    const os = target.closest("[data-plug-os]")?.dataset.plugOs;
    if (os) {
      plugOs = os;
      updateOverlay();
      $("term-overlay").querySelector(`[data-plug-os="${os}"]`)?.focus();
      return;
    }
    const action = target.closest("[data-plug]")?.dataset.plug;
    if (action === "copy-command") {
      writeClipboard(C.bridgeLaunch(plugOs).command)
        .then(() => hooks.toast("Commande copiée : colle-la dans ton terminal."))
        .catch(() => hooks.toast("Copie refusée : sélectionne la commande à la main."));
    } else if (action === "retry") reconnectNow();
    else if (action === "restart") send({ t: "restart" });
  }

  function init(options = {}) {
    hooks = { ...hooks, ...options };
    drawer = $("terminal-drawer");
    if (!drawer) return;
    takePairingFromUrl();
    buildModeOptions();
    updateModeButton();
    updateMenu();
    applyLayout();
    setLesson(options.lesson);

    if (!settings.open) setStatus("off");
    $("terminal-toggle").addEventListener("click", () => setOpen(drawer.dataset.state !== "open", { focus: true }));
    $("terminal-plug").addEventListener("click", () => setOpen(true, { focus: true }));
    $("terminal-own").addEventListener("click", switchToOwnTerminal);
    $("terminal-maximize").addEventListener("click", () => {
      settings.maximized = !settings.maximized;
      storeSettings();
      applyLayout();
    });
    $("terminal-resize").addEventListener("pointerdown", dragResize);
    $("terminal-resize").addEventListener("keydown", (event) => {
      if (nudge(event.key, 24, (delta) => { settings.height = Math.max(190, settings.height + delta); settings.maximized = false; })) event.preventDefault();
    });
    $("pane-splitter").addEventListener("pointerdown", dragSplit);
    $("pane-splitter").addEventListener("keydown", (event) => {
      if (nudge(event.key, 0.04, (delta) => { settings.split = Math.max(0.25, Math.min(0.72, settings.split + delta)); })) event.preventDefault();
    });
    $("run-button").addEventListener("click", run);
    $("term-overlay").addEventListener("click", (event) => handlePlugAction(event.target));
    $("term-overlay").addEventListener("submit", (event) => {
      if (!event.target.matches("[data-plug-form]")) return;
      event.preventDefault();
      pairWith(event.target.elements.code.value);
    });
    // A whole code pasted (or dropped) plugs at once: no need to look for the button.
    $("term-overlay").addEventListener("input", (event) => {
      if (event.target.name !== "code") return;
      typedCode = event.target.value;
      if (/^insertFrom(Paste|Drop)/.test(event.inputType || "") && C.parsePairing(typedCode)) pairWith(typedCode);
    });
    drawer.addEventListener("click", (event) => {
      const button = event.target.closest("[data-term]");
      if (button) handleTerminalAction(button.dataset.term, button);
    });
    $("terminal-menu").addEventListener("click", (event) => {
      const button = event.target.closest("[data-term]");
      if (button) handleTerminalAction(button.dataset.term, button);
    });
    $("mode-options").addEventListener("click", (event) => {
      const option = event.target.closest("[data-mode]");
      if (!option) return;
      settings.mode = option.dataset.mode;
      storeSettings();
      updateModeButton();
      $("terminal-mode-popover").hidePopover();
      hooks.toast(`Kenobi : ${MODES[settings.mode].label.toLowerCase()}.`);
      hooks.onModeChange?.(settings.mode);
    });
    $("file-list").addEventListener("click", (event) => {
      const choice = event.target.closest("[data-file]");
      if (!choice) return;
      $("file-popover").hidePopover();
      switchFile(choice.dataset.file);
    });
    $("file-new-form").addEventListener("submit", createFile);

    $("terminal-mode-popover").addEventListener("beforetoggle", (event) => {
      if (event.newState === "open") place(event.currentTarget, $("terminal-mode-button"), { alignEnd: true });
    });
    $("terminal-menu").addEventListener("beforetoggle", (event) => {
      if (event.newState === "open") place(event.currentTarget, document.querySelector('[popovertarget="terminal-menu"]'), { alignEnd: true });
    });
    $("file-popover").addEventListener("beforetoggle", (event) => {
      if (event.newState !== "open") return;
      place(event.currentTarget, $("file-button"));
      refreshFileList();
    });

    document.addEventListener("keydown", (event) => {
      if (event.defaultPrevented || !event.ctrlKey || event.altKey || event.metaKey) return;
      const key = event.key.toLowerCase();
      if (key === "j" && !event.shiftKey) {
        if (!available) return;
        event.preventDefault();
        setOpen(drawer.dataset.state !== "open", { focus: true });
      } else if (key === "enter" && drawer.contains(event.target) && !event.target.closest("#term-overlay")) {
        event.preventDefault();
        run();
      }
    });
    // Back from the bridge's window: try at once instead of waiting for the next retry.
    const comeBack = () => {
      if (status === "ready") reloadFileIfChanged();
      else if (settings.open && available && RETRY_ON_RETURN.has(status) && !plugBusy) reconnectNow();
      else if (status === "unpaired") detectNow();
    };
    window.addEventListener("focus", comeBack);
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") comeBack();
    });
    // The bridge's link opened in a tab where Kenobi was already loaded.
    window.addEventListener("hashchange", () => {
      const pairing = C.parsePairing(location.hash);
      if (!pairing) return;
      history.replaceState(null, "", location.pathname + location.search);
      setOpen(true);
      pairWith(`${pairing.port}-${pairing.token}`);
    });

    loadConfig().then(() => {
      if (settings.open && available) setOpen(true);
    });
  }

  window.KenobiTerminal = {
    init,
    setLesson,
    setDelivery,
    loadCode,
    openFile,
    goToLine,
    currentFile,
    mode: () => (MODES[settings.mode] ? settings.mode : "coach"),
    // What Kenobi sees, sent with each message: coach, silent or off while the terminal is on, closed otherwise.
    watchState: () => (available && settings.open && status === "ready" ? (MODES[settings.mode] ? settings.mode : "coach") : "closed"),
    ready: () => available && status === "ready",
    language: () => lesson.language,
    // For a course done on the command line: open on the terminal itself, and hand over what is on screen.
    open: () => { setOpen(true); requestAnimationFrame(() => term?.focus()); },
    screen: () => (term ? screenText() : { text: "", selection: false })
  };
})();
