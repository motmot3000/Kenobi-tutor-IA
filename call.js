// Appel vocal avec Kenobi. Une boucle simple :
//   écoute → la personne parle → réflexion → parole → écoute.
// - Parler par-dessus Kenobi l'interrompt : sa voix baisse aussitôt, elle est coupée dès que la parole est
//   confirmée. Reparler pendant qu'il réfléchit remplace le tour : les mots restés sans réponse ouvrent le suivant.
// - Le micro est lu par un AudioWorklet ; la phrase part en WAV 16 kHz. La réponse arrive en flux (SSE) : chaque
//   phrase est jouée dès qu'elle est prête, bout à bout en WebAudio, sans trou.
// - Si la réponse tarde, une courte phrase d'attente est dite dans la même voix, au plus une toutes les 8 s.
// - Un tour raté ne raccroche jamais : un mot à l'écran, et la ligne écoute de nouveau.
// Le tuteur a tout le contexte de la séance ouverte ; chaque tour s'ajoute à la discussion (app.js, par les crochets).
(function () {
  "use strict";

  const VoiceActivity = window.KenobiVoiceActivity;
  const $ = (id) => document.getElementById(id);
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const PREROLL_MS = 500;        // son gardé avant le début de la parole (première syllabe)
  const MIN_VOICED_MS = 300;     // plus court : un bruit, une toux, un clic ; jamais envoyé
  const BARGE_CONFIRM_MS = 450;  // parole tenue avant de couper la voix de Kenobi
  const TARGET_RATE = 16000;
  const FILLER_GAP_MS = 8000;
  const STATUS = { off: "Prêt", listen: "Écoute", think: "Réfléchit", speak: "Parle", reconnect: "Reconnecte…" };

  let hooks = { sessionId: () => null, beforeTurn: async () => {}, terminal: () => "closed", openTurn: () => null,
    delta: () => {}, closeTurn: () => null, toast: () => {}, openSettings: () => {} };
  let panel, orb;
  let call = null;               // {serial, ctx, stream, source, meter, gain, analyser, startedAt}
  let serial = 0;
  let starting = false, muted = false, suspended = false;
  let micLevel = 0, heardSpeech = false;
  let quietTimer = 0, noticeTimer = 0, clockTimer = 0, levelFrame = 0, fillerTimer = 0;
  let fillerDelay = 1100, fillerBank = [], fillerNext = 0, lastFillerAt = -1e9, greetingBank = [];
  let ring = [], ringMs = 0;     // dernières trames du micro (pré-écoute)
  let utt = null;                // la phrase en train d'être dite
  let turn = null;               // le tour que la ligne attend ou dit
  let carry = "";                // mots d'un tour remplacé avant toute réponse
  let wakeLock = null;
  let readiness = { ready: false, problem: "", label: "" };
  const vad = VoiceActivity ? new VoiceActivity() : null;

  // ── Voix de Kenobi ────────────────────────────────────────────────────────

  const player = {
    sources: new Set(), until: 0, chain: Promise.resolve(), gen: 0, pending: 0, lastEnd: -1e9,
    playing() { return this.sources.size > 0 || this.pending > 0; },
    // `wanted()` est redemandé une fois le son décodé : une réponse interrompue ne s'entend jamais.
    // `onStart()` est appelé au moment où ce morceau commence vraiment à s'entendre (sous-titre).
    enqueue(b64, wanted, onStart) {
      if (!call || typeof b64 !== "string" || !b64) return;
      const gen = this.gen;
      const owned = call;
      let decoded;
      try {
        const bytes = Uint8Array.from(atob(b64), (character) => character.charCodeAt(0));
        decoded = owned.ctx.decodeAudioData(bytes.buffer);
      } catch {
        return;
      }
      this.pending += 1;
      const settle = () => { this.pending = Math.max(0, this.pending - 1); };
      this.chain = this.chain.then(() => decoded).then((buffer) => {
        settle();
        if (gen !== this.gen || call !== owned || (wanted && !wanted())) { display(); return; }
        const source = owned.ctx.createBufferSource();
        source.buffer = buffer;
        source.connect(owned.gain);
        const at = Math.max(owned.ctx.currentTime + 0.02, this.until);
        source.start(at);
        this.until = at + buffer.duration;
        this.sources.add(source);
        if (onStart) setTimeout(() => { if (this.sources.has(source)) onStart(); }, Math.max(0, (at - owned.ctx.currentTime) * 1000));
        source.onended = () => { this.sources.delete(source); this.lastEnd = performance.now(); display(); };
        display();
      }).catch(() => { settle(); display(); });
    },
    stop() {
      this.gen += 1;
      for (const source of this.sources) { source.onended = null; try { source.stop(); } catch { /* déjà fini */ } }
      this.sources.clear();
      this.until = 0;
      this.chain = Promise.resolve();
      this.pending = 0;
      this.lastEnd = -1e9;
      this.level(1);
      display();
    },
    level(value) {
      if (call) call.gain.gain.setTargetAtTime(value, call.ctx.currentTime, 0.03);
    }
  };

  // ── Écran ─────────────────────────────────────────────────────────────────

  function notice(text, ms) {
    clearTimeout(noticeTimer);
    const box = $("call-notice");
    if (!box) return;
    box.textContent = text || "";
    if (text && ms) noticeTimer = setTimeout(() => { if (box.textContent === text) box.textContent = ""; }, ms);
    // Panneau réduit pendant l'appel : le message passe par l'interface, sinon personne ne le verrait.
    if (text && call && panel && !panel.open) hooks.toast(text);
  }

  function caption(text) {
    const box = $("voice-caption");
    if (box) box.textContent = text || "";
  }

  function phase() {
    if (!call) return "off";
    if (player.playing()) return "speak";
    // Entre deux phrases d'une même réponse : il parle encore, pas de clignotement.
    if (turn && !turn.finished && turn.received && performance.now() - player.lastEnd < 900) return "speak";
    if (turn && !turn.finished) return "think";
    return "listen";
  }

  function display() {
    if (!panel) return;
    const mode = phase();
    if (orb.dataset.state !== mode) orb.dataset.state = mode;
    const active = Boolean(call);
    const status = starting ? "Connexion…"
      : active && suspended ? "Micro suspendu"
      : active && muted && mode === "listen" ? "Micro coupé"
      : !active && !readiness.ready ? "Indisponible"
      : STATUS[mode];
    if ($("voice-status").textContent !== status) $("voice-status").textContent = status;
    $("call-start").hidden = active || starting;
    $("call-start").disabled = !readiness.ready;
    $("call-hangup").hidden = !active && !starting;
    $("call-mute").hidden = !active;
    $("call-mute").setAttribute("aria-pressed", String(muted));
    const resume = muted || suspended;
    $("call-mute").querySelector("span").textContent = resume ? "Reprendre le micro" : "Couper le micro";
    $("call-mute").querySelector("use").setAttribute("href", resume ? "#i-mic-off" : "#i-mic");
    $("call-skip").hidden = mode !== "speak";
    $("call-minimize").hidden = !active;
    const button = $("call-button");
    if (button) {
      button.classList.toggle("is-live", active);
      button.dataset.state = active ? mode : "";
      button.title = active ? "Appel en cours : afficher le panneau" : "";
    }
    if (active) tickClock();
  }

  // Ce que fait la ligne, en un mot : lisible dans la barre du haut même quand le panneau est réduit.
  function liveWord() {
    if (suspended) return "Suspendu";
    const mode = phase();
    if (muted && mode === "listen") return "Micro coupé";
    return STATUS[mode] || "En appel";
  }

  function tickClock() {
    const label = $("call-button-label");
    const timer = $("call-timer");
    if (!call) {
      if (label) label.textContent = "Appeler le tuteur";
      if (timer) timer.textContent = "";
      return;
    }
    const seconds = Math.floor((performance.now() - call.startedAt) / 1000);
    const text = `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
    if (timer) timer.textContent = text;
    if (label) label.textContent = `${liveWord()} · ${text}`;
  }

  // La bouche de Kenobi suit sa voix (niveau du son joué), l'anneau d'écoute suit le micro.
  function animate() {
    cancelAnimationFrame(levelFrame);
    if (!call) {
      orb.style.setProperty("--voice", "0");
      orb.style.setProperty("--mic", "0");
      return;
    }
    const data = new Uint8Array(call.analyser.fftSize);
    let smooth = 0;
    const step = () => {
      if (!call) return animate();
      call.analyser.getByteTimeDomainData(data);
      let sum = 0;
      for (let index = 0; index < data.length; index += 1) {
        const value = (data[index] - 128) / 128;
        sum += value * value;
      }
      const level = Math.min(1, Math.sqrt(sum / data.length) * 5);
      smooth += (level - smooth) * 0.4;
      orb.style.setProperty("--voice", smooth.toFixed(3));
      orb.style.setProperty("--mic", (muted ? 0 : Math.min(1, micLevel * 14)).toFixed(3));
      levelFrame = requestAnimationFrame(step);
    };
    levelFrame = requestAnimationFrame(step);
  }

  // ── Formats audio ─────────────────────────────────────────────────────────

  // Trames Float32 au rythme de capture → PCM 16 bits à 16 kHz (moyenne par fenêtre).
  function toPcm16(frames, rate) {
    const total = frames.reduce((size, frame) => size + frame.length, 0);
    const all = new Float32Array(total);
    let at = 0;
    for (const frame of frames) { all.set(frame, at); at += frame.length; }
    const ratio = rate > TARGET_RATE ? rate / TARGET_RATE : 1;
    const out = new Int16Array(Math.floor(total / ratio));
    for (let index = 0; index < out.length; index += 1) {
      const from = Math.floor(index * ratio);
      const to = Math.max(from + 1, Math.floor((index + 1) * ratio));
      let sum = 0;
      for (let cursor = from; cursor < to; cursor += 1) sum += all[cursor];
      const value = Math.max(-1, Math.min(1, sum / (to - from)));
      out[index] = value < 0 ? value * 0x8000 : value * 0x7fff;
    }
    return { samples: out, rate: ratio > 1 ? TARGET_RATE : rate };
  }

  function wavBlob({ samples, rate }) {
    const buffer = new ArrayBuffer(44 + samples.length * 2);
    const view = new DataView(buffer);
    const text = (offset, value) => [...value].forEach((character, index) => view.setUint8(offset + index, character.charCodeAt(0)));
    text(0, "RIFF"); view.setUint32(4, 36 + samples.length * 2, true); text(8, "WAVE"); text(12, "fmt ");
    view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
    view.setUint32(24, rate, true); view.setUint32(28, rate * 2, true); view.setUint16(32, 2, true);
    view.setUint16(34, 16, true); text(36, "data"); view.setUint32(40, samples.length * 2, true);
    new Int16Array(buffer, 44).set(samples);
    return new Blob([buffer], { type: "audio/wav" });
  }

  // ── Micro ─────────────────────────────────────────────────────────────────

  function onFrame({ rms, pcm }) {
    if (!call || muted || suspended) return;
    const now = performance.now();
    const rate = call.ctx.sampleRate;
    micLevel = rms;
    if (pcm && pcm.length) {
      ring.push(pcm);
      ringMs += pcm.length / rate * 1000;
      while (ring.length > 1 && ringMs - ring[0].length / rate * 1000 >= PREROLL_MS) ringMs -= ring.shift().length / rate * 1000;
    }
    const speaking = player.playing();
    const event = vad.tick(rms, now, speaking);
    if (event === "start") beginUtterance(speaking);
    else if (utt && pcm && pcm.length) { utt.frames.push(pcm); utt.ms += pcm.length / rate * 1000; }
    if (utt && utt.barge && !utt.confirmed && vad.voicedMs >= BARGE_CONFIRM_MS) confirmBarge();
    if (event === "end") endUtterance(vad.lastVoicedMs);
  }

  function beginUtterance(barge) {
    heardSpeech = true;
    utt = { frames: [...ring], ms: ringMs, rate: call.ctx.sampleRate, barge, confirmed: !barge };
    if (barge) player.level(0.2); // sa voix baisse le temps de savoir si c'est bien une parole
    display();
  }

  // La personne parle vraiment par-dessus Kenobi : sa voix est coupée, le reste de cette réponse ne sera pas dit.
  function confirmBarge() {
    utt.confirmed = true;
    if (turn) turn.silenced = true;
    clearTimeout(fillerTimer);
    player.stop();
  }

  function endUtterance(voiced) {
    const spoken = utt;
    utt = null;
    if (!spoken) return;
    if (voiced < MIN_VOICED_MS || !spoken.confirmed) {
      // Un bruit, ou l'écho de sa propre voix : rien n'est envoyé, Kenobi continue.
      if (spoken.barge) player.level(1);
      releaseHeld();
      display();
      return;
    }
    sendTurn({ frames: spoken.frames, rate: spoken.rate });
  }

  async function connectMic(owned, captured) {
    owned.source?.disconnect?.();
    if (owned.meter) { owned.meter.port.onmessage = null; owned.meter.disconnect(); }
    owned.source = owned.ctx.createMediaStreamSource(captured);
    owned.meter = new window.AudioWorkletNode(owned.ctx, "vad-meter");
    owned.meter.port.onmessage = ({ data }) => { if (call === owned) onFrame(data); };
    owned.source.connect(owned.meter);
    owned.meter.connect(owned.ctx.destination); // le processeur sort du silence, jamais le micro
    captured.getAudioTracks().forEach((track) => {
      track.enabled = !muted;
      track.onended = () => { if (call === owned) suspend("Le micro s’est coupé. Clique sur « Reprendre le micro »."); };
    });
  }

  function suspend(message) {
    suspended = true;
    vad.reset();
    utt = null;
    notice(message);
    display();
  }

  async function resumeMic() {
    if (!call) return;
    const owned = call;
    try {
      await owned.ctx.resume();
      if (owned.stream.getAudioTracks().some((track) => track.readyState === "ended")) {
        const captured = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true }, video: false });
        if (call !== owned) { captured.getTracks().forEach((track) => track.stop()); return; }
        owned.stream.getTracks().forEach((track) => track.stop());
        owned.stream = captured;
        await connectMic(owned, captured);
      }
      if (call !== owned) return;
      suspended = owned.ctx.state !== "running";
      vad.reset();
      notice(suspended ? "Le micro attend le navigateur. Clique sur « Reprendre le micro »." : "Appel repris.", 2500);
    } catch {
      if (call === owned) { suspended = true; notice("Appel conservé. Clique sur « Reprendre le micro »."); }
    }
    display();
  }

  // ── Un tour ───────────────────────────────────────────────────────────────

  function live(t) {
    return !t.dead && call && t.serial === call.serial && turn === t;
  }

  function parseSse(raw) {
    let type = "message";
    const data = [];
    for (const line of raw.split("\n")) {
      if (line.startsWith("event:")) type = line.slice(6).trim();
      else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
    }
    if (!data.length) return null;
    try {
      return { type, data: JSON.parse(data.join("\n")) };
    } catch {
      return null;
    }
  }

  // Le tour est remplacé par une nouvelle phrase : la page coupe le flux, le serveur arrête le modèle et la voix.
  function abandon(t) {
    if (t.dead) return;
    t.dead = true;
    if (t.finished) return; // déjà répondu et enregistré : seul le son qui restait est abandonné
    t.controller.abort();
    const unanswered = !t.reply.trim();
    if (t.spoken && t.heard && unanswered) carry = t.heard;
    if (t.handle) hooks.closeTurn(t.handle, { content: t.reply, stopped: true, dropped: unanswered });
    t.resolve?.(null);
  }

  // `request` : {frames, rate} pour une phrase dite, {text, extra} pour un message écrit pendant l'appel (un Run).
  async function sendTurn(request) {
    if (!call) return null;
    if (turn) abandon(turn);
    const t = {
      serial: call.serial, controller: new AbortController(), spoken: !request.text, received: 0, stage: "sent",
      heard: "", reply: "", handle: null, held: [], dead: false, finished: false, silenced: false, resolve: null
    };
    const outcome = new Promise((resolve) => { t.resolve = resolve; });
    turn = t;
    caption("");
    display();
    try {
      await hooks.beforeTurn();
      if (!live(t)) return outcome;
      const sessionId = hooks.sessionId();
      if (!sessionId) { endTurn(t, "Ouvre une séance pour parler à Kenobi."); return outcome; }
      const sent = carry;
      const options = request.text
        ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify({ sessionId, text: request.text, terminal: hooks.terminal() }) }
        : {
          headers: { "Content-Type": "audio/wav", "X-Session-Id": sessionId, "X-Terminal": hooks.terminal(), "X-Carry": encodeURIComponent(sent) },
          body: wavBlob(toPcm16(request.frames, request.rate))
        };
      const response = await fetch("api/call/turn", { method: "POST", signal: t.controller.signal, ...options });
      if (!response.ok || !response.body) {
        const data = await response.json().catch(() => ({}));
        endTurn(t, data.error || `Erreur ${response.status}.`);
        return outcome;
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (live(t)) {
        const chunk = await reader.read();
        if (chunk.done) break;
        buffer += decoder.decode(chunk.value, { stream: true }).replace(/\r\n/g, "\n");
        let boundary;
        while ((boundary = buffer.indexOf("\n\n")) !== -1) {
          const event = parseSse(buffer.slice(0, boundary));
          buffer = buffer.slice(boundary + 2);
          if (event && live(t)) handleEvent(t, event, request);
        }
      }
      if (live(t) && !t.finished) endTurn(t, "La réponse a été coupée.");
    } catch (error) {
      if (!t.dead) endTurn(t, error.name === "AbortError" ? "" : "Connexion perdue avec Kenobi.");
    }
    return outcome;
  }

  function handleEvent(t, event, request) {
    const { type, data } = event;
    if (type === "stage") t.stage = data.name;
    else if (type === "transcript") {
      t.heard = data.text || "";
      carry = "";
      if (t.spoken) caption(`« ${t.heard} »`);
      t.handle = hooks.openTurn({ text: t.heard, spoken: t.spoken, extra: request.extra || {} });
      // S'il doit réfléchir, il le dit : la phrase d'attente part si rien n'est prêt à dire après le délai.
      armFiller(t);
    } else if (type === "delta") {
      t.reply += data.text || "";
      if (t.handle) hooks.delta(t.handle, t.reply);
    } else if (type === "segment") {
      if (data.index !== t.received) return; // dans l'ordre, une seule fois
      t.received += 1;
      clearTimeout(fillerTimer);
      if (t.silenced) return;
      if (utt) t.held.push(data); // la personne parle : attendre de savoir si c'était un bruit
      else speakSegment(t, data);
    } else if (type === "empty") {
      t.finished = true;
      clearTimeout(fillerTimer);
      notice("Je n’ai rien entendu de clair.", 2500);
      t.resolve(null);
      if (turn === t) turn = null;
    } else if (type === "done") finishTurn(t, data);
    else if (type === "error") endTurn(t, data.message || "Le tour a échoué.");
    display();
  }

  function speakSegment(t, segment) {
    player.enqueue(segment.audio, () => !t.dead && !t.silenced, segment.text ? () => caption(segment.text) : null);
  }

  function finishTurn(t, result) {
    t.finished = true;
    clearTimeout(fillerTimer);
    if (result.voiceError && !t.received) notice(`${result.voiceError} La réponse est à l’écran.`, 6000);
    const reply = t.handle ? hooks.closeTurn(t.handle, { content: result.reply || t.reply, meta: result, stopped: t.silenced }) : null;
    t.resolve(reply);
    if (turn === t && !player.playing()) turn = null;
    display();
  }

  function endTurn(t, message) {
    if (t.finished && t.dead) return;
    t.finished = true;
    t.dead = true;
    clearTimeout(fillerTimer);
    try { t.controller.abort(); } catch { /* déjà fermé */ }
    const reply = t.handle ? hooks.closeTurn(t.handle, { content: t.reply, error: message && !t.reply ? message : "", stopped: Boolean(t.reply) }) : null;
    t.resolve(reply);
    if (turn === t) turn = null;
    if (message) notice(`${message} L’appel continue.`, 6000);
    display();
  }

  // Un bruit a été pris pour une parole pendant qu'une réponse arrivait : dire ce qui a été retenu.
  function releaseHeld() {
    const t = turn;
    if (!t || t.dead || t.silenced || !t.held.length) return;
    for (const segment of t.held.splice(0)) speakSegment(t, segment);
  }

  // ── Phrase d'attente et accueil, dans la voix de Kenobi ───────────────────

  function armFiller(t) {
    clearTimeout(fillerTimer);
    if (!fillerBank.length) return;
    fillerTimer = setTimeout(() => {
      if (!live(t) || t.received || t.finished || utt || player.playing()) return;
      if (performance.now() - lastFillerAt < FILLER_GAP_MS) return;
      lastFillerAt = performance.now();
      if (fillerNext >= fillerBank.length) fillerNext = 0;
      player.enqueue(fillerBank[fillerNext++], () => live(t) && !t.received && !utt);
    }, Math.max(400, fillerDelay));
  }

  async function loadPhrases(owned) {
    try {
      const response = await fetch("api/call/phrases", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      const info = await response.json();
      if (call !== owned) return;
      const audio = (list) => (Array.isArray(list) ? list : []).map((item) => item && item.audio).filter((value) => typeof value === "string" && value);
      greetingBank = audio(info.greetings);
      fillerBank = audio(info.fillers);
      fillerNext = Math.floor(Math.random() * Math.max(1, fillerBank.length));
      if (Number.isFinite(info.delayMs)) fillerDelay = info.delayMs;
      // Décroche comme une personne, si rien ne s'est encore dit.
      if (greetingBank.length && !utt && !turn && !heardSpeech && performance.now() - owned.startedAt < 6000) {
        player.enqueue(greetingBank[Math.floor(Math.random() * greetingBank.length)], () => !utt && !turn);
      }
    } catch {
      /* Sans phrases, la ligne reste simplement silencieuse en attendant. */
    }
  }

  // ── Appel ─────────────────────────────────────────────────────────────────

  async function keepAwake(on) {
    try {
      if (on && !wakeLock && navigator.wakeLock) {
        wakeLock = await navigator.wakeLock.request("screen");
        wakeLock.addEventListener?.("release", () => { wakeLock = null; });
      } else if (!on && wakeLock) {
        const lock = wakeLock;
        wakeLock = null;
        await lock.release();
      }
    } catch {
      wakeLock = null; // facultatif : l'écran peut simplement se mettre en veille
    }
  }

  async function refreshReadiness() {
    let problem = "";
    let label = "";
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
      problem = "Le micro demande une page sûre : ouvre Kenobi sur l’ordinateur qui l’héberge (http://127.0.0.1) ou en HTTPS.";
    } else if (!window.AudioWorkletNode || !(window.AudioContext || window.webkitAudioContext) || !VoiceActivity) {
      problem = "Ce navigateur est trop ancien pour l’appel (AudioWorklet manquant).";
    } else {
      try {
        const [voice, health] = await Promise.all([
          fetch("api/voice/status", { cache: "no-store" }).then((response) => response.json()),
          fetch("api/health", { cache: "no-store" }).then((response) => response.json())
        ]);
        label = voice.label || "";
        if (!health.agent?.configured) problem = "Branche d’abord un modèle d’IA dans les réglages.";
        else if (!voice.ready) problem = voice.problem || "La voix n’est pas prête.";
      } catch {
        problem = "Le serveur de Kenobi ne répond pas.";
      }
    }
    readiness = { ready: !problem, problem, label };
    const foot = $("call-foot");
    if (foot) {
      foot.innerHTML = "";
      if (problem) {
        const link = document.createElement("button");
        link.type = "button";
        link.className = "text-button";
        link.textContent = "Ouvrir les réglages";
        link.addEventListener("click", () => hooks.openSettings(problem.includes("modèle") ? "ai" : "voice"));
        foot.append(link);
      } else foot.textContent = label === "Voix locale" ? "Voix locale · rien ne quitte cet ordinateur" : `Voix : ${label}`;
    }
    if (!call) notice(problem);
    display();
    return readiness;
  }

  async function start() {
    if (call || starting) return;
    await refreshReadiness();
    if (!readiness.ready) return;
    if (!hooks.sessionId()) { notice("Ouvre une leçon pour parler à Kenobi."); return; }
    starting = true;
    const mine = ++serial;
    display();
    let ctx = null;
    let captured = null;
    try {
      // Créé et repris dans le clic : la voix de Kenobi peut ensuite jouer à tout moment.
      ctx = new (window.AudioContext || window.webkitAudioContext)();
      await ctx.resume();
      captured = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true }, video: false });
      await ctx.audioWorklet.addModule("vad-worklet.js");
      if (mine !== serial) throw new Error("annulé");
      if (ctx.state !== "running") throw new Error("audio suspendu, clique de nouveau sur Appeler");
      const gain = ctx.createGain();
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 512;
      gain.connect(analyser);
      analyser.connect(ctx.destination);
      const owned = { serial: mine, ctx, stream: captured, source: null, meter: null, gain, analyser, startedAt: performance.now() };
      call = owned;
      await connectMic(owned, captured);
      ctx.onstatechange = () => {
        if (call !== owned) return;
        if (ctx.state !== "running") suspend("Audio suspendu par le navigateur. L’appel est conservé.");
        else if (suspended) resumeMic();
      };
      starting = false; muted = false; suspended = false;
      vad.reset(); ring = []; ringMs = 0; utt = null; turn = null; carry = ""; heardSpeech = false; micLevel = 0;
      keepAwake(true);
      clearInterval(clockTimer);
      clockTimer = setInterval(tickClock, 1000);
      tickClock();
      animate();
      clearTimeout(quietTimer);
      quietTimer = setTimeout(() => { if (call === owned && !heardSpeech && !muted) notice("Je ne t’entends pas. Vérifie le micro."); }, 9000);
      notice("");
      caption("");
      display();
      loadPhrases(owned);
    } catch (error) {
      captured?.getTracks().forEach((track) => track.stop());
      ctx?.close().catch(() => {});
      if (mine === serial) {
        call = null;
        starting = false;
        const denied = error?.name === "NotAllowedError" || error?.name === "SecurityError";
        notice(denied ? "Micro refusé : autorise-le pour cette page dans le navigateur, puis rappelle." : `Micro indisponible : ${error.message}`);
        display();
      }
    }
  }

  function hangup(reason) {
    serial += 1;
    const ended = call;
    call = null;
    starting = false; muted = false; suspended = false;
    clearTimeout(fillerTimer); clearTimeout(quietTimer); clearInterval(clockTimer);
    if (turn) abandon(turn);
    turn = null;
    utt = null; ring = []; ringMs = 0; carry = "";
    vad?.reset();
    player.gen += 1;
    for (const source of player.sources) { source.onended = null; try { source.stop(); } catch { /* déjà fini */ } }
    player.sources.clear(); player.until = 0; player.chain = Promise.resolve(); player.pending = 0; player.lastEnd = -1e9;
    if (ended) {
      if (ended.meter) { ended.meter.port.onmessage = null; ended.meter.disconnect(); }
      ended.source?.disconnect?.();
      ended.stream?.getTracks().forEach((track) => track.stop());
      ended.ctx.onstatechange = null;
      ended.ctx.close().catch(() => {});
    }
    keepAwake(false);
    tickClock();
    animate();
    caption("");
    if (panel) notice(reason || (ended ? "Appel terminé. Micro fermé." : readiness.problem));
    if (reason && panel && !panel.open) hooks.toast(reason);
    display();
  }

  function interrupt() {
    if (turn) turn.silenced = true;
    clearTimeout(fillerTimer);
    player.stop();
    notice("À toi.", 1500);
  }

  function toggleMute() {
    if (!call) return;
    if (suspended) { resumeMic(); return; }
    muted = !muted;
    call.stream.getAudioTracks().forEach((track) => { track.enabled = !muted; });
    utt = null;
    vad.reset();
    micLevel = 0;
    notice(muted ? "Micro coupé. Kenobi peut finir sa réponse." : "Micro ouvert.", 2500);
    display();
  }

  // ── API ───────────────────────────────────────────────────────────────────

  function open({ autostart = true } = {}) {
    if (!panel) return;
    if (!panel.open) panel.show();
    if (call || starting) return;
    refreshReadiness().then((state) => { if (state.ready && autostart) start(); });
  }

  function close() {
    hangup();
    notice("");
    if (panel?.open) panel.close();
  }

  // L'appel continue, le panneau laisse la place au travail ; le bouton de la barre du haut le rouvre.
  function minimize() {
    if (panel?.open) panel.close();
  }

  function init(options = {}) {
    hooks = { ...hooks, ...options };
    panel = $("call-panel");
    orb = $("voice-orb");
    if (!panel || !orb) return;
    $("call-start").addEventListener("click", start);
    $("call-hangup").addEventListener("click", () => hangup());
    $("call-skip").addEventListener("click", interrupt);
    $("call-mute").addEventListener("click", toggleMute);
    orb.addEventListener("click", () => { if (phase() === "speak") interrupt(); });
    window.addEventListener("offline", () => { if (call) notice("Réseau perdu. L’appel attend…"); });
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden && call) { keepAwake(true); if (suspended || call.ctx.state !== "running") resumeMic(); }
    });
    window.addEventListener("pagehide", () => hangup());
    display();
  }

  window.KenobiCall = {
    init,
    open,
    close,
    minimize,
    hangup,
    active: () => Boolean(call),
    // Un message écrit pendant l'appel (un Run envoyé seul) : Kenobi y répond à voix haute.
    say: (text, extra) => (call ? sendTurn({ text, extra }) : Promise.resolve(null)),
    refresh: refreshReadiness
  };
})();
