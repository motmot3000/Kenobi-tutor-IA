// Réglages (modèle d'IA, voix, profil), import d'un cours Markdown et confirmation : trois dialogues.
// Les clés d'API ne reviennent jamais du serveur : la page sait seulement qu'une clé est enregistrée.
(function () {
  "use strict";

  const C = window.KenobiCore;
  const $ = (id) => document.getElementById(id);
  const e = (value) => C.escapeHtml(value);
  const icon = (name) => `<svg class="icon" aria-hidden="true"><use href="#i-${name}"/></svg>`;
  // Course library: the official site is coming; until then, the courses/ folder of the repository.
  const LIBRARY_URL = "https://github.com/motmot3000/Kenobi-tutor-IA/tree/main/courses";
  const FORMAT_SAMPLE = `# Titre du cours
Une ou deux phrases : pour qui, pour apprendre quoi.
**Niveau :** débutant
**Langage et outils :** Python, dans l’éditeur et le terminal

## Objectifs du cours
- ce que l’apprenant saura faire à la fin…

## Module 1 — Titre du module
### Leçon 1 — Titre de la leçon
- Objectif : ce qu’il sait faire à la fin de la leçon.
- Notions : les notions abordées.
- Exercice type : une tâche qui prouve que c’est acquis.`;
  const REASONING = [
    ["none", "Désactivé (conseillé)"],
    ["auto", "Réglage par défaut du modèle"],
    ["low", "Léger"],
    ["medium", "Moyen"],
    ["high", "Poussé — plus lent"]
  ];

  let hooks = { onSaved: () => {}, onCourse: () => {}, toast: () => {} };
  let info = null;        // réglages publics du serveur
  let voiceStatus = null;
  let tab = "ai";
  let models = [];
  let elevenVoices = [];
  let pollTimer = 0;
  let importState = null; // {name, text, lines, course, notice, running, controller, stage, received, error}
  let confirmResolve = null;

  async function requestJson(path, body) {
    const response = await fetch(path, body === undefined ? { cache: "no-store" } : {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body)
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `Erreur ${response.status}`);
    return data;
  }

  async function load() {
    info = await requestJson("api/settings");
    return info;
  }

  // The web version (everything in the browser) or the self-hosted one (server.py).
  const web = () => info?.mode === "browser";

  function provider(id) {
    return (info?.providers || []).find((item) => item.id === id) || null;
  }

  // ── Dialogue des réglages ─────────────────────────────────────────────────

  function field(label, control, hint = "") {
    return `<div class="field"><span class="field-label">${label}</span>${control}${hint ? `<span class="field-hint">${hint}</span>` : ""}</div>`;
  }

  function renderAi() {
    const ai = info.ai;
    const current = provider(ai.provider) || provider("mistral");
    const locked = !info.editable;
    const options = info.providers.map((item) => `<option value="${e(item.id)}" ${item.id === current.id ? "selected" : ""}>${e(item.label)}</option>`).join("");
    const reasoning = REASONING.map(([value, label]) => `<option value="${value}" ${value === ai.reasoning ? "selected" : ""}>${e(label)}</option>`).join("");
    return `
      <p class="sheet-intro">${web()
        ? "Kenobi répond avec le modèle que tu branches ici : un modèle rapide suffit. Ta clé reste dans ce navigateur, chiffrée, et part directement chez le fournisseur choisi : Kenobi n’a pas de serveur."
        : "Kenobi répond avec le modèle que tu branches ici : un modèle rapide suffit. La clé reste sur cet ordinateur et ne part que chez le fournisseur choisi."}</p>
      ${info.vault?.locked ? renderUnlock() : ""}
      ${locked ? `<p class="sheet-warning">${icon("lock")}<span>Les réglages se modifient depuis l’ordinateur qui héberge Kenobi (http://127.0.0.1).</span></p>` : ""}
      <fieldset class="field-set" ${locked ? "disabled" : ""}>
        ${field(`<label for="ai-provider">Fournisseur</label>`, `<select id="ai-provider">${options}</select>`)}
        <div id="ai-provider-fields"></div>
        ${field(`<label for="ai-model">Modèle</label>`, `<div class="field-row"><input id="ai-model" type="text" autocomplete="off" spellcheck="false" value="${e(ai.provider === current.id ? ai.model : "")}" placeholder="nom du modèle, ou choisis-le dans la liste"><button class="button secondary" type="button" data-settings="load-models">${icon("repeat")}Charger la liste</button></div><div class="model-list" id="ai-model-list" role="listbox" aria-label="Modèles proposés" hidden></div>`,
          `<span id="ai-models-note">La liste vient du fournisseur, une fois la clé donnée.</span>`)}
        ${field(`<label for="ai-reasoning">Raisonnement étendu</label>`, `<select id="ai-reasoning">${reasoning}</select>`, "Un tuteur doit répondre vite : laisse-le désactivé, sauf modèle qui l’impose.")}
        <div class="sheet-actions"><span class="sheet-status" id="ai-status" role="status"></span><button class="button primary" type="button" data-settings="save-ai">${icon("check")}Enregistrer et tester</button></div>
      </fieldset>
      ${web() ? renderVault() : ""}`;
  }

  // Web version: where the keys are kept (vault.js), and the unlock box of a passphrase-locked vault.
  function renderUnlock() {
    return `<div class="vault-unlock" role="group" aria-label="Déverrouiller les clés">
      <p class="sheet-warning">${icon("lock")}<span>Tes clés sont verrouillées par ta phrase secrète.</span></p>
      <div class="field-row"><input id="vault-unlock-pass" type="password" autocomplete="current-password" placeholder="Ta phrase secrète"><button class="button primary" type="button" data-settings="vault-unlock">${icon("check")}Déverrouiller</button></div>
      <span class="sheet-status" id="vault-unlock-status" role="status"></span>
    </div>`;
  }

  function renderVault() {
    const mode = info.vault?.mode || "device";
    const option = (id, title, text) => `
      <label class="engine-card ${mode === id ? "is-selected" : ""}">
        <input type="radio" name="vault-mode" value="${id}" ${mode === id ? "checked" : ""}>
        <span class="engine-copy"><strong>${title}</strong><span>${text}</span></span>
      </label>`;
    return `
      <section class="vault-box" aria-labelledby="vault-title">
        <h3 id="vault-title">${icon("lock")}Où garder tes clés</h3>
        <div class="engine-list" role="radiogroup" aria-label="Où garder tes clés">
          ${option("device", "Sur cet appareil, chiffrées", "Le navigateur les chiffre avec une clé qu’il ne laisse jamais sortir. Pour ton ordinateur personnel.")}
          ${option("passphrase", "Verrouillées par une phrase secrète", "Demandée à chaque visite. Pour un ordinateur que tu partages.")}
          ${option("session", "Pour cette visite seulement", "Rien n’est enregistré : tu recolles la clé à chaque visite.")}
        </div>
        <div class="field vault-pass" hidden><label class="field-label" for="vault-new-pass">Nouvelle phrase secrète</label><input id="vault-new-pass" type="password" autocomplete="new-password" minlength="8" placeholder="8 caractères au moins"></div>
        <div class="sheet-actions"><span class="sheet-status" id="vault-status" role="status"></span><button class="button ghost" type="button" data-settings="vault-forget">${icon("trash")}Oublier toutes les clés</button><button class="button secondary" type="button" data-settings="vault-save">${icon("check")}Appliquer</button></div>
        <p class="field-hint">Conseil : crée une clé réservée à Kenobi, avec une limite de dépense chez le fournisseur. Tu peux la révoquer à tout moment.</p>
      </section>`;
  }

  async function vault(action, extra = {}) {
    info = await requestJson("api/settings/vault", { action, ...extra });
    hooks.onSaved(info);
    return info;
  }

  function renderProviderFields() {
    const box = $("ai-provider-fields");
    if (!box) return;
    const current = provider($("ai-provider").value);
    const same = info.ai.provider === current.id;
    const hasKey = same && info.ai.hasKey;
    const parts = [];
    if (current.editableUrl) {
      parts.push(field(`<label for="ai-base">Adresse de l’API</label>`,
        `<input id="ai-base" type="url" autocomplete="off" spellcheck="false" value="${e(same ? info.ai.baseUrl : current.baseUrl)}" placeholder="http://127.0.0.1:11434/v1">`,
        "L’adresse qui se termine par /v1 : Ollama, LM Studio, llama.cpp, une passerelle…"));
    }
    if (!current.keyless) {
      const hint = [];
      if (hasKey && info.ai.keySource === "env") hint.push("Clé trouvée dans l’environnement du serveur.");
      else if (hasKey) hint.push(`Une clé est enregistrée. <button class="link-button" type="button" data-settings="forget-ai-key">L’oublier</button>`);
      else if (current.envKey) hint.push("Une clé est disponible dans l’environnement du serveur : tu peux laisser vide.");
      if (current.keyUrl) hint.push(`<a href="${e(current.keyUrl)}" target="_blank" rel="noopener noreferrer">Obtenir une clé chez ${e(current.label.replace(/ \(.+\)$/, ""))}</a>`);
      if (current.keyOptional) hint.push("Facultative pour un service local.");
      parts.push(field(`<label for="ai-key">Clé d’API</label>`,
        `<input id="ai-key" type="password" autocomplete="off" spellcheck="false" placeholder="${hasKey ? "Clé enregistrée — laisse vide pour la garder" : "Colle ta clé ici"}">`, hint.join(" · ")));
    }
    box.innerHTML = parts.join("");
    models = [];
    renderModels();
  }

  function renderModels() {
    const list = $("ai-model-list");
    const input = $("ai-model");
    if (!list || !input) return;
    // A name that is already one of the models filters nothing: the whole list stays visible around it.
    const query = models.some((item) => item.id === input.value.trim()) ? "" : input.value.trim().toLowerCase();
    const shown = models.filter((item) => !query || item.id.toLowerCase().includes(query) || item.label.toLowerCase().includes(query)).slice(0, 80);
    list.hidden = !models.length;
    list.innerHTML = shown.length
      ? shown.map((item) => `<button type="button" role="option" class="model-choice" data-model="${e(item.id)}" aria-selected="${item.id === input.value}"><code>${e(item.id)}</code>${item.label ? `<span>${e(item.label)}</span>` : ""}</button>`).join("")
      : `<p class="model-empty">Aucun modèle ne correspond : le nom saisi sera utilisé tel quel.</p>`;
  }

  function aiPatch() {
    const patch = { provider: $("ai-provider").value, model: $("ai-model").value.trim(), reasoning: $("ai-reasoning").value };
    if ($("ai-base")) patch.baseUrl = $("ai-base").value.trim();
    if ($("ai-key")?.value.trim()) patch.apiKey = $("ai-key").value.trim();
    return patch;
  }

  function setStatus(id, text, tone = "") {
    const box = $(id);
    if (!box) return;
    box.textContent = text;
    box.dataset.tone = tone;
  }

  async function loadModels() {
    setStatus("ai-models-note", "Lecture de la liste…");
    try {
      const data = await requestJson("api/settings/models", { ai: aiPatch() });
      models = data.models || [];
      setStatus("ai-models-note", models.length ? `${models.length} modèles : clique pour choisir, ou tape pour filtrer.` : "Le fournisseur n’a renvoyé aucun modèle : saisis son nom.");
      renderModels();
    } catch (error) {
      models = [];
      renderModels();
      setStatus("ai-models-note", error.message, "error");
    }
  }

  async function saveAi() {
    const patch = aiPatch();
    if (!patch.model) return setStatus("ai-status", "Choisis un modèle.", "error");
    setStatus("ai-status", "Enregistrement…");
    try {
      info = await requestJson("api/settings", { ai: patch });
      if ($("ai-key")) $("ai-key").value = "";
      hooks.onSaved(info);
      if (!info.ai.configured) return setStatus("ai-status", "Enregistré, mais il manque la clé d’API.", "error");
      setStatus("ai-status", "Essai du modèle…");
      const result = await requestJson("api/settings/test", {});
      setStatus("ai-status", `Connecté : ${result.model} répond (premier mot en ${C.formatSeconds(result.firstToken) || "moins d’une seconde"}).`, "ok");
      renderProviderHint();
      hooks.onSaved(info);
    } catch (error) {
      setStatus("ai-status", error.message, "error");
      hooks.onSaved(info);
    }
  }

  function renderProviderHint() {
    const typed = $("ai-model")?.value;
    renderProviderFields();
    if ($("ai-model")) $("ai-model").value = typed || "";
  }

  // Voix

  // Web version: the local voice is the voice bridge (kenobi-voice.py) the learner runs on their computer.
  function bridgeVoiceBlock() {
    const local = info.voice.local || {};
    const voices = info.localVoices.map((item) => `<option value="${e(item.id)}" ${item.id === local.voice ? "selected" : ""}>${e(item.label)}</option>`).join("");
    if (local.paired) {
      return `<div class="engine-fields">
        ${field(`<label for="voice-local">Voix</label>`, `<select id="voice-local">${voices}</select>`)}
        <p class="field-hint">Pont vocal branché : <code>${e(local.url)}</code>. Il doit rester lancé pendant les appels. <button class="link-button" type="button" data-settings="voice-unpair">Le débrancher</button></p>
      </div>`;
    }
    return `<div class="engine-fields">
      <ol class="setup-steps">
        <li><strong>Télécharge le pont vocal</strong> : un seul fichier Python, qui fait tourner Whisper (il t’entend) et Piper (il te parle) sur ton ordinateur. <a class="text-button" href="api/voice/pont" download>${icon("download")}kenobi-voice.py</a></li>
        <li><strong>Lance-le</strong> dans un terminal, depuis ton dossier de téléchargements : <code>python3 kenobi-voice.py</code> (Python 3.8 ou plus ; <code>py kenobi-voice.py</code> sous Windows). La première fois, il installe Whisper et Piper : environ 1,1 Go, quelques minutes.</li>
        <li><strong>Colle le code</strong> qu’il affiche sous « Code à coller dans Kenobi ». Il peut aussi ouvrir Kenobi déjà branché.</li>
      </ol>
      ${field(`<label for="voice-local-code">Code du pont vocal</label>`, `<input id="voice-local-code" type="password" autocomplete="off" spellcheck="false" placeholder="8196-…">`)}
      <details class="voice-custom"><summary>Un autre serveur vocal compatible OpenAI</summary>
        ${field(`<label for="voice-local-url">Adresse</label>`, `<input id="voice-local-url" type="url" autocomplete="off" spellcheck="false" placeholder="http://127.0.0.1:8000">`, "Un serveur qui répond à /v1/audio/transcriptions et /v1/audio/speech (Speaches, LocalAI…).")}
        ${field(`<label for="voice-local-token">Clé (facultative)</label>`, `<input id="voice-local-token" type="password" autocomplete="off" spellcheck="false">`)}
      </details>
    </div>`;
  }

  function localVoiceBlock() {
    if (web()) return bridgeVoiceBlock();
    const local = voiceStatus?.local || {};
    if (local.installing) {
      const percent = Number.isFinite(local.percent) ? local.percent : 0;
      return `<div class="install-progress" role="status"><div class="progress-track"><span style="width:${percent}%"></span></div><span class="progress-label">${e(local.stepLabel || "Installation…")}</span></div>`;
    }
    if (local.installed) {
      const voices = info.localVoices.map((item) => `<option value="${e(item.id)}" ${item.id === info.voice.local.voice ? "selected" : ""}>${e(item.label)}</option>`).join("");
      return `<div class="engine-fields">${field(`<label for="voice-local">Voix</label>`, `<select id="voice-local">${voices}</select>`)}<p class="field-hint">Installée : Piper (${e(local.voice)}) pour parler, Whisper « ${e(local.stt)} » pour entendre.</p></div>`;
    }
    return `<div class="engine-fields">
      ${local.error ? `<p class="sheet-warning">${icon("info")}<span>${e(local.error)}</span></p>` : ""}
      <p class="field-hint">Pas encore installée : un environnement Python à part, une voix française et un modèle de transcription (environ 1,1 Go, quelques minutes). Rien d’autre n’est modifié.</p>
      <div class="field-row"><button class="button secondary" type="button" data-settings="install-voice" ${info.editable ? "" : "disabled"}>${icon("download")}Installer la voix locale</button><code class="install-command">${e(local.command || "")}</code></div>
    </div>`;
  }

  function renderVoice() {
    const voice = info.voice;
    const locked = !info.editable;
    const engine = (id, title, text, body) => `
      <label class="engine-card ${voice.engine === id ? "is-selected" : ""}">
        <input type="radio" name="voice-engine" value="${id}" ${voice.engine === id ? "checked" : ""}>
        <span class="engine-copy"><strong>${title}</strong><span>${text}</span></span>
      </label>
      <div class="engine-body" data-engine="${id}" ${voice.engine === id ? "" : "hidden"}>${body}</div>`;
    const mistralVoices = info.mistralVoices.map((item) => `<option value="${e(item.id)}" ${item.id === voice.mistral.voice ? "selected" : ""}>${e(item.label)}</option>`).join("");
    const shared = voice.mistral.hasKey && !voice.mistral.ownKey;
    const mistral = `<div class="engine-fields">
      ${field(`<label for="voice-mistral-key">Clé d’API Mistral</label>`, `<input id="voice-mistral-key" type="password" autocomplete="off" spellcheck="false" placeholder="${voice.mistral.hasKey ? (shared ? "Celle du modèle Mistral est utilisée" : "Clé enregistrée — laisse vide pour la garder") : "Colle ta clé ici"}">`,
        shared ? "La clé Mistral du modèle sert aussi à la voix." : `<a href="https://console.mistral.ai/api-keys" target="_blank" rel="noopener noreferrer">Obtenir une clé chez Mistral</a>`)}
      ${field(`<label for="voice-mistral-voice">Voix</label>`, `<select id="voice-mistral-voice">${mistralVoices}</select>`, "Voix françaises préréglées de Voxtral.")}
    </div>`;
    const elevenOptions = [
      ...(voice.elevenlabs.voice && !elevenVoices.some((item) => item.id === voice.elevenlabs.voice) ? [{ id: voice.elevenlabs.voice, name: voice.elevenlabs.voiceName || voice.elevenlabs.voice }] : []),
      ...elevenVoices
    ].map((item) => `<option value="${e(item.id)}" data-name="${e(item.name)}" ${item.id === voice.elevenlabs.voice ? "selected" : ""}>${e(item.name)}${item.language ? ` · ${e(item.language)}` : ""}${item.gender ? ` · ${e(item.gender)}` : ""}</option>`).join("");
    const eleven = `<div class="engine-fields">
      ${field(`<label for="voice-eleven-key">Clé d’API ElevenLabs</label>`, `<input id="voice-eleven-key" type="password" autocomplete="off" spellcheck="false" placeholder="${voice.elevenlabs.hasKey ? "Clé enregistrée — laisse vide pour la garder" : "Colle ta clé ici"}">`,
        `<a href="https://elevenlabs.io/app/settings/api-keys" target="_blank" rel="noopener noreferrer">Obtenir une clé chez ElevenLabs</a>`)}
      ${field(`<label for="voice-eleven-voice">Voix</label>`, `<div class="field-row"><select id="voice-eleven-voice">${elevenOptions || `<option value="">Charge d’abord les voix du compte</option>`}</select><button class="button secondary" type="button" data-settings="load-voices">${icon("repeat")}Charger les voix</button></div>`, `<span id="voice-eleven-note">Les voix viennent de ton compte ElevenLabs ; les françaises sont listées d’abord.</span>`)}
    </div>`;
    return `
      <p class="sheet-intro">Pour l’appel avec Kenobi : il t’entend (transcription) et te répond à voix haute (synthèse). ${web()
        ? "Deux possibilités : Whisper sur ton ordinateur, gratuit et privé, avec le pont vocal ; ou une clé d’API vocale, Mistral (Voxtral) ou ElevenLabs."
        : "La voix locale tourne sur cet ordinateur ; les deux autres passent par une clé d’API."}</p>
      ${locked ? `<p class="sheet-warning">${icon("lock")}<span>Les réglages se modifient depuis l’ordinateur qui héberge Kenobi (http://127.0.0.1).</span></p>` : ""}
      <fieldset class="field-set" ${locked ? "disabled" : ""}>
        <div class="engine-list" role="radiogroup" aria-label="Moteur de voix">
          ${engine("local", web() ? "Whisper sur ton ordinateur" : "Voix locale", web() ? "Gratuit, sans compte : le son ne quitte pas ton ordinateur. Un petit programme à lancer." : "Sur cet ordinateur, sans compte : rien ne sort de la machine.", localVoiceBlock())}
          ${engine("mistral", "Voxtral · Mistral", "Voix française naturelle et transcription rapide, par l’API de Mistral.", mistral)}
          ${engine("elevenlabs", "ElevenLabs", "Grand choix de voix, par l’API d’ElevenLabs.", eleven)}
        </div>
        <label class="check-row"><input id="voice-swiss" type="checkbox" ${voice.swissNumbers ? "checked" : ""}><span>Dire les nombres à la suisse (septante, huitante, nonante)</span></label>
        <label class="check-row"><input id="voice-fillers" type="checkbox" ${voice.fillers ? "checked" : ""}><span>Une courte phrase d’attente quand la réponse tarde (« Voyons voir. »)</span></label>
        <div class="sheet-actions"><span class="sheet-status" id="voice-status-line" role="status"></span><button class="button secondary" type="button" data-settings="test-voice">${icon("volume")}Écouter la voix</button><button class="button primary" type="button" data-settings="save-voice">${icon("check")}Enregistrer</button></div>
      </fieldset>
      <p class="sheet-note">${icon("info")}<span>${web()
        ? "Le micro n’est ouvert que pendant un appel. Avec une clé d’API, ce que tu dis part chez ce fournisseur pour être transcrit ; avec le pont vocal, rien ne sort de ton ordinateur."
        : "Le micro n’est ouvert que pendant un appel, et seulement sur une page sûre : l’ordinateur qui héberge Kenobi (http://127.0.0.1) ou une adresse en HTTPS."}</span></p>`;
  }

  function voicePatch() {
    const engine = document.querySelector('input[name="voice-engine"]:checked')?.value || info.voice.engine;
    const patch = { engine, swissNumbers: $("voice-swiss").checked, fillers: $("voice-fillers").checked, mistral: {}, elevenlabs: {}, local: {} };
    if ($("voice-local")) patch.local.voice = $("voice-local").value;
    if ($("voice-local-code")?.value.trim()) patch.local.code = $("voice-local-code").value.trim();
    else if ($("voice-local-url")?.value.trim()) {
      patch.local.url = $("voice-local-url").value.trim();
      patch.local.token = $("voice-local-token")?.value.trim() || "";
    }
    if ($("voice-mistral-voice")) patch.mistral.voice = $("voice-mistral-voice").value;
    if ($("voice-mistral-key")?.value.trim()) patch.mistral.apiKey = $("voice-mistral-key").value.trim();
    const select = $("voice-eleven-voice");
    if (select) {
      patch.elevenlabs.voice = select.value;
      patch.elevenlabs.voiceName = select.selectedOptions[0]?.dataset.name || "";
    }
    if ($("voice-eleven-key")?.value.trim()) patch.elevenlabs.apiKey = $("voice-eleven-key").value.trim();
    return patch;
  }

  async function saveVoice({ quiet = false } = {}) {
    try {
      info = await requestJson("api/settings", { voice: voicePatch() });
      voiceStatus = await requestJson("api/voice/status");
      hooks.onSaved(info);
      if (!quiet) {
        renderPanel();
        setStatus("voice-status-line", voiceStatus.ready ? "Enregistré : la voix est prête." : voiceStatus.problem, voiceStatus.ready ? "ok" : "error");
      }
      return true;
    } catch (error) {
      setStatus("voice-status-line", error.message, "error");
      return false;
    }
  }

  async function testVoice() {
    if (info.editable && !(await saveVoice({ quiet: true }))) return;
    setStatus("voice-status-line", "Kenobi prépare sa voix…");
    try {
      const sample = await requestJson("api/voice/test", {});
      const audio = new Audio(`data:${sample.mime};base64,${sample.audio}`);
      await audio.play();
      setStatus("voice-status-line", `« ${sample.text} »`, "ok");
    } catch (error) {
      setStatus("voice-status-line", error.message, "error");
    }
  }

  async function loadVoices() {
    setStatus("voice-eleven-note", "Lecture des voix…");
    try {
      const data = await requestJson("api/voice/voices", { apiKey: $("voice-eleven-key")?.value.trim() || "" });
      elevenVoices = data.voices || [];
      const typed = $("voice-eleven-key")?.value || "";
      const previous = $("voice-eleven-voice")?.value;
      if (!info.voice.elevenlabs.voice && elevenVoices.length) info.voice.elevenlabs.voice = previous || elevenVoices[0].id;
      renderPanel();
      if ($("voice-eleven-key")) $("voice-eleven-key").value = typed;
      setStatus("voice-eleven-note", `${elevenVoices.length} voix dans ce compte.`);
    } catch (error) {
      setStatus("voice-eleven-note", error.message, "error");
    }
  }

  async function installVoice() {
    try {
      voiceStatus = await requestJson("api/voice/install", {});
      renderPanel();
      pollInstall();
    } catch (error) {
      setStatus("voice-status-line", error.message, "error");
    }
  }

  function pollInstall() {
    clearTimeout(pollTimer);
    pollTimer = setTimeout(async () => {
      if (!$("settings-dialog").open) return;
      try {
        voiceStatus = await requestJson("api/voice/status");
      } catch {
        return pollInstall();
      }
      if (tab === "voice") {
        const body = document.querySelector('.engine-body[data-engine="local"]');
        if (body) body.innerHTML = localVoiceBlock();
      }
      if (voiceStatus.local?.installing) pollInstall();
      else hooks.onSaved(info);
    }, 1500);
  }

  // Profil

  function renderProfile() {
    const learner = info.learner;
    return `
      <p class="sheet-intro">Ce que Kenobi sait de toi dès la première séance. Le reste, il l’apprend en travaillant avec toi et le note dans la mémoire de chaque cours.</p>
      <fieldset class="field-set" ${info.editable ? "" : "disabled"}>
        ${field(`<label for="learner-name">Ton prénom</label>`, `<input id="learner-name" type="text" maxlength="40" autocomplete="given-name" value="${e(learner.name)}" placeholder="Comment Kenobi t’appelle">`)}
        ${field(`<label for="learner-about">À propos de toi</label>`, `<textarea id="learner-about" rows="4" maxlength="600" placeholder="Ton niveau, ton métier, pourquoi tu apprends, ce que tu veux construire…">${e(learner.about)}</textarea>`, "Envoyé à Kenobi avec chaque message : reste bref.")}
        <div class="sheet-actions"><span class="sheet-status" id="profile-status" role="status"></span><button class="button primary" type="button" data-settings="save-profile">${icon("check")}Enregistrer</button></div>
      </fieldset>`;
  }

  async function saveProfile() {
    try {
      info = await requestJson("api/settings", { learner: { name: $("learner-name").value, about: $("learner-about").value } });
      hooks.onSaved(info);
      setStatus("profile-status", "Enregistré.", "ok");
    } catch (error) {
      setStatus("profile-status", error.message, "error");
    }
  }

  // Web version: everything lives in this browser; a backup file moves it or keeps it safe (never the keys).
  function renderData() {
    return `
      <p class="sheet-intro">Dans la version web, tes cours, tes séances et la mémoire de Kenobi restent <strong>dans ce navigateur</strong>, sur cet ordinateur. Fais une sauvegarde de temps en temps : elle sert aussi à continuer sur un autre ordinateur ou un autre navigateur.</p>
      <div class="sheet-actions data-actions"><a class="button secondary" href="api/backup" download>${icon("download")}Exporter mes données</a><label class="button secondary">${icon("upload")}Importer une sauvegarde<input id="backup-file" type="file" accept=".json,application/json" hidden></label></div>
      <span class="sheet-status" id="data-status" role="status"></span>
      <p class="field-hint">La sauvegarde contient tes cours, tes séances, la mémoire et tes réglages, jamais tes clés. Importer une sauvegarde remplace ce que ce navigateur contient.</p>
      <p class="sheet-note">${icon("info")}<span>Effacer les données du site dans le navigateur efface aussi Kenobi : pense à exporter avant.</span></p>`;
  }

  async function importBackup(file) {
    if (!file) return;
    setStatus("data-status", "Lecture de la sauvegarde…");
    try {
      const text = await file.text();
      const response = await fetch("api/backup", { method: "POST", headers: { "Content-Type": "application/json" }, body: text });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || `Erreur ${response.status}`);
      setStatus("data-status", `Sauvegarde importée : ${data.courses} cours. Rechargement…`, "ok");
      setTimeout(() => window.location.reload(), 900);
    } catch (error) {
      setStatus("data-status", error.message, "error");
    }
  }

  function renderPanel() {
    const body = $("settings-body");
    if (!body || !info) return;
    body.innerHTML = tab === "voice" ? renderVoice() : tab === "profile" ? renderProfile() : tab === "data" && web() ? renderData() : renderAi();
    document.querySelectorAll("#settings-dialog [role=tab]").forEach((button) => {
      const selected = button.dataset.tab === tab;
      button.setAttribute("aria-selected", String(selected));
      button.tabIndex = selected ? 0 : -1;
    });
    if (tab === "ai") renderProviderFields();
    if (tab === "voice" && voiceStatus?.local?.installing) pollInstall();
  }

  async function openSettings(wanted) {
    const dialog = $("settings-dialog");
    tab = ["ai", "voice", "profile", "data"].includes(wanted) ? wanted : "ai";
    dialog.innerHTML = `
      <div class="sheet-head"><h2 id="settings-title">Réglages</h2><button class="icon-button" type="button" data-settings="close" aria-label="Fermer les réglages">${icon("close")}</button></div>
      <div class="sheet-tabs" role="tablist" aria-label="Réglages">
        <button type="button" role="tab" data-tab="ai">${icon("bot")}Modèle d’IA</button>
        <button type="button" role="tab" data-tab="voice">${icon("volume")}Voix</button>
        <button type="button" role="tab" data-tab="profile">${icon("memory")}Profil</button>
        <button type="button" role="tab" data-tab="data" ${window.KENOBI_BACKEND === "browser" ? "" : "hidden"}>${icon("download")}Données</button>
      </div>
      <div class="sheet-body" id="settings-body" tabindex="-1" autofocus><div class="loading-state"><span class="loading-line"></span><span class="loading-line short"></span></div></div>`;
    if (!dialog.open) dialog.showModal();
    try {
      [info, voiceStatus] = await Promise.all([requestJson("api/settings"), requestJson("api/voice/status")]);
      renderPanel();
    } catch (error) {
      $("settings-body").innerHTML = `<p class="sheet-warning">${icon("info")}<span>${e(error.message)}</span></p>`;
    }
  }

  function handleSettingsClick(event) {
    const tabButton = event.target.closest("[role=tab]");
    if (tabButton) {
      tab = tabButton.dataset.tab;
      renderPanel();
      return;
    }
    const choice = event.target.closest("[data-model]");
    if (choice) {
      $("ai-model").value = choice.dataset.model;
      renderModels();
      return;
    }
    const action = event.target.closest("[data-settings]")?.dataset.settings;
    if (action === "close") $("settings-dialog").close();
    else if (action === "load-models") loadModels();
    else if (action === "save-ai") saveAi();
    else if (action === "forget-ai-key") {
      requestJson("api/settings", { ai: { clearKey: true } }).then((value) => { info = value; hooks.onSaved(info); renderProviderHint(); }).catch((error) => setStatus("ai-status", error.message, "error"));
    } else if (action === "save-voice") saveVoice();
    else if (action === "test-voice") testVoice();
    else if (action === "load-voices") loadVoices();
    else if (action === "install-voice") installVoice();
    else if (action === "save-profile") saveProfile();
    else if (action === "voice-unpair") {
      requestJson("api/settings", { voice: { local: { clear: true } } }).then((value) => { info = value; hooks.onSaved(info); renderPanel(); }).catch((error) => setStatus("voice-status-line", error.message, "error"));
    } else if (action === "vault-unlock") {
      vault("unlock", { passphrase: $("vault-unlock-pass")?.value || "" }).then(() => renderPanel()).catch((error) => setStatus("vault-unlock-status", error.message, "error"));
    } else if (action === "vault-save") {
      const mode = document.querySelector('input[name="vault-mode"]:checked')?.value || "device";
      vault("mode", { mode, passphrase: $("vault-new-pass")?.value || "" })
        .then(() => { renderPanel(); setStatus("vault-status", mode === "session" ? "Tes clés ne sont plus enregistrées : elles seront oubliées en fermant l’onglet." : "Enregistré.", "ok"); })
        .catch((error) => setStatus("vault-status", error.message, "error"));
    } else if (action === "vault-forget") {
      confirm({ title: "Oublier toutes les clés ?", text: "Les clés d’API du modèle et de la voix, et le code du pont vocal, sont effacés de ce navigateur. Tes cours restent.", confirmLabel: "Oublier les clés", danger: true })
        .then((yes) => (yes ? vault("forget").then(() => { renderPanel(); setStatus("vault-status", "Clés oubliées.", "ok"); }) : null))
        .catch((error) => setStatus("vault-status", error.message, "error"));
    }
  }

  function handleSettingsChange(event) {
    if (event.target.id === "ai-provider") {
      $("ai-model").value = info.ai.provider === event.target.value ? info.ai.model : "";
      setStatus("ai-status", "");
      setStatus("ai-models-note", "La liste vient du fournisseur, une fois la clé donnée.");
      renderProviderFields();
    } else if (event.target.name === "vault-mode") {
      document.querySelectorAll(".vault-box .engine-card").forEach((card) => card.classList.toggle("is-selected", card.querySelector("input").checked));
      const pass = document.querySelector(".vault-pass");
      if (pass) pass.hidden = event.target.value !== "passphrase";
    } else if (event.target.id === "backup-file") {
      importBackup(event.target.files[0]);
    } else if (event.target.name === "voice-engine") {
      document.querySelectorAll(".engine-card").forEach((card) => card.classList.toggle("is-selected", card.querySelector("input").checked));
      document.querySelectorAll(".engine-body").forEach((body) => { body.hidden = body.dataset.engine !== event.target.value; });
    }
  }

  // ── Import d'un cours ─────────────────────────────────────────────────────

  function renderImport() {
    const dialog = $("import-dialog");
    const state = importState;
    const ai = info?.ai;
    const analysed = Boolean(ai?.configured);
    let body;
    if (state.course) {
      const course = state.course;
      const lessons = course.modules.reduce((sum, module) => sum + module.lessons.length, 0);
      const tools = C.courseTools(course);
      body = `
        ${state.notice ? `<p class="sheet-warning">${icon("info")}<span>${e(state.notice)}</span></p>` : ""}
        <section class="import-result" aria-label="Cours analysé">
          <div class="import-result-head"><span class="course-monogram" aria-hidden="true">${e(C.courseMonogram(course))}</span><div><h3>${e(course.title)}</h3><p>${e(course.description || "")}</p></div></div>
          <dl class="import-facts">
            <div><dt>Plan</dt><dd>${course.modules.length} module${course.modules.length > 1 ? "s" : ""} · ${lessons} leçon${lessons > 1 ? "s" : ""}</dd></div>
            <div><dt>Outils</dt><dd>${tools.terminal ? `Éditeur et terminal · ${e(tools.label)}` : "Sans terminal"}</dd></div>
            <div><dt>Niveau</dt><dd>${e(course.level)}</dd></div>
          </dl>
          ${course.goals.length ? `<h4>Objectifs</h4><ul class="import-goals">${course.goals.slice(0, 5).map((goal) => `<li>${e(goal)}</li>`).join("")}</ul>` : ""}
          <h4>Modules</h4>
          <ol class="import-modules">${course.modules.slice(0, 12).map((module) => `<li><strong>${e(module.title)}</strong><span>${module.lessons.length} leçon${module.lessons.length > 1 ? "s" : ""}</span></li>`).join("")}${course.modules.length > 12 ? `<li><span>… et ${course.modules.length - 12} autres</span></li>` : ""}</ol>
        </section>
        <div class="sheet-actions"><button class="button ghost" type="button" data-import="reset">Choisir un autre document</button><button class="button primary" type="button" data-import="accept">${icon("check")}Ajouter ce cours</button></div>`;
    } else if (state.mode === "write") {
      body = renderWrite(state, ai, analysed);
    } else {
      const progress = state.running ? `<div class="install-progress" role="status"><div class="progress-track is-indeterminate"><span></span></div><span class="progress-label">${state.stage === "analysis" ? `Analyse par ${e(ai?.model || "le modèle")}…${state.received ? ` ${state.received.toLocaleString("fr-FR")} caractères reçus` : ""}` : "Lecture des titres du document…"}</span></div>` : "";
      body = `
        <p class="sheet-intro">Donne à Kenobi le document Markdown d’un cours d’informatique ou de code : ses objectifs, ses modules, ses leçons. Il en tire le plan, ce que chaque leçon doit t’apprendre, et ouvre une mémoire propre à ce cours.</p>
        <label class="drop-zone ${state.text ? "has-file" : ""}" id="import-drop">
          <input id="import-file" type="file" accept=".md,.markdown,.txt,text/markdown,text/plain" hidden>
          ${icon(state.text ? "file" : "upload")}
          ${state.text ? `<strong>${e(state.name)}</strong><span>${state.lines.toLocaleString("fr-FR")} lignes · clique pour changer de fichier</span>` : `<strong>Dépose le fichier .md du cours</strong><span>ou clique pour le choisir</span>`}
        </label>
        ${state.error ? `<p class="sheet-warning is-error">${icon("info")}<span>${e(state.error)}</span></p>` : ""}
        <p class="sheet-note">${icon(analysed ? "spark" : "info")}<span>${analysed ? `Analyse par <strong>${e(ai.model)}</strong> (${e(ai.label)}). Le document part chez ce fournisseur.` : `Aucun modèle d’IA branché : le plan sera tiré des titres du document (## module, ### leçon). <button class="link-button" type="button" data-import="settings">Brancher un modèle</button>`}</span></p>
        ${progress}
        <details class="format-help">
          <summary>${icon("info")}Le format d’un cours, et comment en faire écrire un par une IA</summary>
          <p>Un cours est un seul document Markdown : un titre <code>#</code>, une description, le niveau et les outils, les objectifs, puis des modules <code>##</code> qui contiennent des leçons <code>###</code>, chacune avec son objectif, ses notions et un exercice type.</p>
          <pre class="format-sample"><code>${e(FORMAT_SAMPLE)}</code></pre>
          <p>Tu peux l’écrire toi-même à partir du modèle de document, le faire écrire par ton IA dans l’onglet « Écrire avec mon IA », ou par une autre IA (ChatGPT, Claude, Le Chat…) : copie la consigne ci-dessous, colle-la dans la conversation, ajoute ce que tu veux apprendre, et enregistre sa réponse dans un fichier <code>.md</code>.</p>
          <div class="sheet-actions format-actions"><button class="button secondary" type="button" data-import="copy-prompt">${icon("copy")}Copier la consigne pour une IA</button><span class="sheet-status" id="prompt-status" role="status"></span></div>
        </details>
        <p class="sheet-note">${icon("book")}<span>Pas de cours sous la main ? La bibliothèque officielle des cours de Kenobi arrive bientôt. En attendant, des cours prêts à importer t’attendent dans la <a href="${LIBRARY_URL}" target="_blank" rel="noopener noreferrer">bibliothèque du projet</a>.</span></p>
        <div class="sheet-actions"><a class="text-button" href="api/courses/template" download>${icon("download")}Modèle de document</a><button class="button primary" type="button" data-import="run" ${state.text && !state.running ? "" : "disabled"}>${icon("spark")}${state.running ? "Analyse en cours…" : "Analyser et créer le cours"}</button></div>`;
    }
    const tabs = state.course ? "" : `
      <div class="sheet-tabs" role="tablist" aria-label="Ajouter un cours">
        <button type="button" role="tab" data-import-mode="file" aria-selected="${state.mode !== "write"}">${icon("upload")}Importer un document</button>
        <button type="button" role="tab" data-import-mode="write" aria-selected="${state.mode === "write"}">${icon("spark")}Écrire avec mon IA</button>
      </div>`;
    dialog.innerHTML = `
      <div class="sheet-head"><h2 id="import-title">Ajouter un cours</h2><button class="icon-button" type="button" data-import="close" aria-label="Fermer">${icon("close")}</button></div>${tabs}
      <div class="sheet-body" tabindex="-1" autofocus>${body}</div>`;
  }

  // « Écrire avec mon IA » : the learner says what they want to learn, the plugged model writes the course document
  // in Kenobi's format (api/courses/write), the learner reads or edits it, then it is imported like a file.
  function renderWrite(state, ai, analysed) {
    const writing = state.writing;
    const progress = writing ? `<div class="install-progress" role="status"><div class="progress-track is-indeterminate"><span></span></div><span class="progress-label">${e(ai?.model || "Le modèle")} écrit le cours…${state.draft ? ` ${state.draft.length.toLocaleString("fr-FR")} caractères` : ""}</span></div>` : "";
    const actions = writing
      ? `<button class="button secondary" type="button" data-import="stop-write">${icon("stop")}Arrêter</button>`
      : state.draft
        ? `<button class="button ghost" type="button" data-import="restart-write">${icon("repeat")}Recommencer</button><button class="button secondary" type="button" data-import="download-draft">${icon("download")}Télécharger le .md</button><button class="button primary" type="button" data-import="use-draft" ${state.running ? "disabled" : ""}>${icon("check")}${state.running ? "Analyse en cours…" : "Créer ce cours"}</button>`
        : `<button class="button primary" type="button" data-import="write" ${analysed ? "" : "disabled"}>${icon("spark")}Écrire le cours</button>`;
    return `
      <p class="sheet-intro">Dis à ton IA ce que tu veux apprendre : le sujet, ton niveau, ton but, le temps que tu as. Elle écrit le document du cours au format de Kenobi ; tu le relis ou le modifies, puis Kenobi en tire le plan et ouvre le cours.</p>
      ${analysed ? "" : `<p class="sheet-warning">${icon("info")}<span>Branche d’abord un modèle d’IA : c’est lui qui écrit le cours. <button class="link-button" type="button" data-import="settings">Brancher un modèle</button></span></p>`}
      <div class="field"><label class="field-label" for="course-wish">Ce que tu veux apprendre</label>
        <textarea id="course-wish" rows="4" maxlength="4000" ${writing ? "readonly" : ""} placeholder="Exemple : je débute, je veux apprendre à automatiser mes fichiers Excel avec Python, 20 minutes par jour pendant un mois. Je connais un peu les formules.">${e(state.wish)}</textarea></div>
      ${state.draft || writing ? `<div class="field"><label class="field-label" for="course-draft">Le cours écrit par ${e(state.draftModel || ai?.model || "ton IA")}</label>
        <textarea id="course-draft" class="course-draft" rows="14" spellcheck="false" ${writing ? "readonly" : ""}>${e(state.draft)}</textarea>
        <span class="field-hint">${writing ? "Il s’écrit sous tes yeux." : "Relis-le : tu peux le modifier ici avant de créer le cours, ou le télécharger pour le garder."}</span></div>` : ""}
      ${state.error ? `<p class="sheet-warning is-error">${icon("info")}<span>${e(state.error)}</span></p>` : ""}
      ${progress}
      ${analysed ? `<p class="sheet-note">${icon("spark")}<span>Écrit par <strong>${e(ai.model)}</strong> (${e(ai.label)}) : ta demande part chez ce fournisseur.</span></p>` : ""}
      <div class="sheet-actions">${actions}</div>`;
  }

  async function writeDraft() {
    const state = importState;
    const wish = $("course-wish")?.value.trim() || "";
    if (state.writing) return;
    Object.assign(state, { wish, draft: "", draftModel: "", error: "", writing: true, writeController: new AbortController() });
    renderImport();
    try {
      const response = await fetch("api/courses/write", {
        method: "POST", headers: { "Content-Type": "application/json" }, signal: state.writeController.signal,
        body: JSON.stringify({ wish })
      });
      if (!response.ok || !response.body) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.error || `Erreur ${response.status}`);
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let finished = false;
      while (!finished) {
        const chunk = await reader.read();
        if (chunk.done) break;
        buffer += decoder.decode(chunk.value, { stream: true }).replace(/\r\n/g, "\n");
        let boundary;
        while ((boundary = buffer.indexOf("\n\n")) !== -1) {
          const event = parseSse(buffer.slice(0, boundary));
          buffer = buffer.slice(boundary + 2);
          if (!event) continue;
          if (event.type === "delta") {
            state.draft += event.data.text || "";
            const area = $("course-draft");
            if (area) {
              area.value = state.draft;
              area.scrollTop = area.scrollHeight;
            } else if (importState === state) renderImport();
            const line = document.querySelector("#import-dialog .progress-label");
            if (line) line.textContent = `${info?.ai?.model || "Le modèle"} écrit le cours… ${state.draft.length.toLocaleString("fr-FR")} caractères`;
          } else if (event.type === "done") {
            Object.assign(state, { draft: event.data.markdown || state.draft, draftModel: event.data.model || "" });
            if (event.data.truncated) state.error = "Le modèle s’est arrêté avant la fin : relis la fin du document, ou recommence avec une demande plus courte.";
            finished = true;
          } else if (event.type === "error") throw new Error(event.data.message || "Écriture du cours impossible.");
        }
      }
      if (!finished && !state.draft) throw new Error("L’écriture a été coupée avant la fin.");
    } catch (error) {
      if (error.name !== "AbortError") state.error = error.message;
    }
    state.writing = false;
    state.writeController = null;
    if (importState === state) renderImport();
  }

  function draftName(markdown) {
    const title = /^#\s+(.+)$/m.exec(markdown || "")?.[1] || "cours";
    const slug = C.stripAccents(title).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 40) || "cours";
    return `${slug}.md`;
  }

  function useDraft() {
    const state = importState;
    const text = ($("course-draft")?.value || state.draft || "").replace(/\r\n?/g, "\n");
    if (!text.trim()) return;
    Object.assign(state, { draft: text, text, name: draftName(text), lines: text.split("\n").length, error: "" });
    runImport();
  }

  function downloadDraft() {
    const text = $("course-draft")?.value || importState.draft || "";
    const link = document.createElement("a");
    link.href = URL.createObjectURL(new Blob([text], { type: "text/markdown;charset=utf-8" }));
    link.download = draftName(text);
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 30000);
  }

  // The instructions given to the plugged model, ready to paste into any other AI (ChatGPT, Claude, Le Chat…).
  async function copyPrompt() {
    const prompt = `${window.KenobiShared?.courses?.AUTHOR_SYSTEM || ""}\n\nCe que je veux apprendre :\n"""\n(décris ici le sujet, ton niveau, ton but et le temps que tu as)\n"""`;
    try {
      if (navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(prompt);
      else {
        const area = document.createElement("textarea");
        area.value = prompt;
        area.style.position = "fixed";
        area.style.opacity = "0";
        document.body.append(area);
        area.select();
        if (!document.execCommand("copy")) throw new Error("copie refusée");
        area.remove();
      }
      setStatus("prompt-status", "Consigne copiée : colle-la dans ton IA, ajoute ce que tu veux apprendre.", "ok");
    } catch {
      // The browser refused the clipboard: the text is shown, selected, ready for Ctrl+C.
      setStatus("prompt-status", "Copie automatique refusée par le navigateur : sélectionne le texte ci-dessous (Ctrl+C).", "error");
      const box = document.querySelector("#import-dialog .format-help");
      if (box && !box.querySelector(".prompt-text")) {
        const area = document.createElement("textarea");
        area.className = "prompt-text course-draft";
        area.readOnly = true;
        area.rows = 10;
        area.value = prompt;
        box.append(area);
        area.focus();
        area.select();
      }
    }
  }

  async function openImport() {
    importState = { mode: "file", name: "", text: "", lines: 0, course: null, notice: "", running: false, controller: null, stage: "", received: 0, error: "",
      wish: "", draft: "", draftModel: "", writing: false, writeController: null };
    const dialog = $("import-dialog");
    if (!info) {
      try { await load(); } catch { /* le dialogue le dira au moment d'analyser */ }
    }
    renderImport();
    if (!dialog.open) dialog.showModal();
  }

  async function takeFile(file) {
    if (!file) return;
    importState.error = "";
    importState.mode = "file";
    if (file.size > 600 * 1024) importState.error = "Fichier trop gros : 300 000 caractères au plus.";
    else {
      try {
        const text = (await file.text()).replace(/\r\n?/g, "\n");
        if (text.includes("\u0000")) importState.error = "Ce fichier n’est pas un document texte.";
        else Object.assign(importState, { name: file.name, text, lines: text.split("\n").length });
      } catch {
        importState.error = "Lecture du fichier impossible.";
      }
    }
    renderImport();
  }

  function parseSse(raw) {
    let type = "message";
    const data = [];
    for (const line of raw.split("\n")) {
      if (line.startsWith("event:")) type = line.slice(6).trim();
      else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
    }
    try {
      return data.length ? { type, data: JSON.parse(data.join("\n")) } : null;
    } catch {
      return null;
    }
  }

  async function runImport() {
    const state = importState;
    if (!state.text || state.running) return;
    Object.assign(state, { running: true, error: "", stage: "analysis", received: 0, controller: new AbortController() });
    renderImport();
    try {
      const response = await fetch("api/courses/import", {
        method: "POST", headers: { "Content-Type": "application/json" }, signal: state.controller.signal,
        body: JSON.stringify({ filename: state.name, markdown: state.text })
      });
      if (!response.ok || !response.body) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.error || `Erreur ${response.status}`);
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let finished = false;
      while (!finished) {
        const chunk = await reader.read();
        if (chunk.done) break;
        buffer += decoder.decode(chunk.value, { stream: true }).replace(/\r\n/g, "\n");
        let boundary;
        while ((boundary = buffer.indexOf("\n\n")) !== -1) {
          const event = parseSse(buffer.slice(0, boundary));
          buffer = buffer.slice(boundary + 2);
          if (!event) continue;
          if (event.type === "progress") {
            state.stage = event.data.stage || state.stage;
            state.received = event.data.received || state.received;
            const line = document.querySelector("#import-dialog .progress-label");
            if (line && state.stage === "analysis") line.textContent = `Analyse par ${info?.ai?.model || "le modèle"}…${state.received ? ` ${state.received.toLocaleString("fr-FR")} caractères reçus` : ""}`;
          } else if (event.type === "done") {
            Object.assign(state, { course: event.data.course, notice: event.data.notice || "" });
            finished = true;
          } else if (event.type === "error") throw new Error(event.data.message || "Import impossible.");
        }
      }
      if (!state.course) throw new Error("L’analyse a été coupée avant la fin.");
    } catch (error) {
      if (error.name !== "AbortError") state.error = error.message;
    }
    state.running = false;
    state.controller = null;
    if (importState === state) renderImport();
  }

  function handleImportClick(event) {
    const mode = event.target.closest("[data-import-mode]")?.dataset.importMode;
    if (mode && !importState.running && !importState.writing) {
      if (importState.mode === "write") importState.wish = $("course-wish")?.value || importState.wish;
      Object.assign(importState, { mode, error: "" });
      renderImport();
      return;
    }
    const action = event.target.closest("[data-import]")?.dataset.import;
    if (!action) return;
    if (action === "close") $("import-dialog").close();
    else if (action === "run") runImport();
    else if (action === "reset") { Object.assign(importState, { course: null, notice: "" }); renderImport(); }
    else if (action === "write") writeDraft();
    else if (action === "stop-write") importState.writeController?.abort();
    else if (action === "restart-write") { Object.assign(importState, { wish: $("course-wish")?.value || importState.wish, draft: "", draftModel: "", error: "" }); renderImport(); }
    else if (action === "use-draft") useDraft();
    else if (action === "download-draft") downloadDraft();
    else if (action === "copy-prompt") copyPrompt();
    else if (action === "settings") { $("import-dialog").close(); openSettings("ai"); }
    else if (action === "accept") {
      const course = importState.course;
      $("import-dialog").close();
      hooks.onCourse(course);
    }
  }

  // ── Confirmation ──────────────────────────────────────────────────────────

  function confirm({ title, text, confirmLabel = "Confirmer", danger = false }) {
    const dialog = $("confirm-dialog");
    dialog.innerHTML = `
      <div class="sheet-head"><h2 id="confirm-title">${e(title)}</h2></div>
      <div class="sheet-body"><p class="sheet-intro">${e(text)}</p>
        <div class="sheet-actions"><button class="button ghost" type="button" data-confirm="no">Annuler</button><button class="button ${danger ? "danger" : "primary"}" type="button" data-confirm="yes">${e(confirmLabel)}</button></div>
      </div>`;
    dialog.showModal();
    dialog.querySelector('[data-confirm="no"]').focus();
    return new Promise((resolve) => { confirmResolve = resolve; });
  }

  function init(options = {}) {
    hooks = { ...hooks, ...options };
    const settingsDialog = $("settings-dialog");
    const importDialog = $("import-dialog");
    const confirmDialog = $("confirm-dialog");
    if (!settingsDialog || !importDialog || !confirmDialog) return;
    settingsDialog.addEventListener("click", handleSettingsClick);
    settingsDialog.addEventListener("change", handleSettingsChange);
    settingsDialog.addEventListener("input", (event) => { if (event.target.id === "ai-model") renderModels(); });
    settingsDialog.addEventListener("close", () => clearTimeout(pollTimer));
    importDialog.addEventListener("click", handleImportClick);
    importDialog.addEventListener("change", (event) => { if (event.target.id === "import-file") takeFile(event.target.files[0]); });
    importDialog.addEventListener("dragover", (event) => { event.preventDefault(); $("import-drop")?.classList.add("is-drop"); });
    importDialog.addEventListener("dragleave", () => $("import-drop")?.classList.remove("is-drop"));
    importDialog.addEventListener("drop", (event) => {
      event.preventDefault();
      event.stopPropagation();
      if (!importState?.running && !importState?.course) takeFile(event.dataTransfer?.files?.[0]);
    });
    importDialog.addEventListener("close", () => { importState?.controller?.abort(); importState?.writeController?.abort(); });
    confirmDialog.addEventListener("click", (event) => {
      const answer = event.target.closest("[data-confirm]")?.dataset.confirm;
      if (!answer) return;
      confirmDialog.close(answer);
    });
    confirmDialog.addEventListener("close", () => {
      confirmResolve?.(confirmDialog.returnValue === "yes");
      confirmResolve = null;
      confirmDialog.returnValue = "";
    });
  }

  // The voice bridge opens Kenobi with #voix=<port>-<token>: the code is saved like a pasted one.
  async function pairVoice(code) {
    info = await requestJson("api/settings", { voice: { engine: "local", local: { code } } });
    hooks.onSaved(info);
    return info;
  }

  window.KenobiSettings = { init, load, open: openSettings, openImport, confirm, pairVoice, info: () => info };
})();
