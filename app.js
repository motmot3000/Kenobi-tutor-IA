"use strict";

const C = window.KenobiCore;
const mainContent = document.getElementById("main-content");
const contextPanel = document.getElementById("context-panel");
const saveStatus = document.getElementById("save-status");
const toast = document.getElementById("toast");
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
const narrowScreen = window.matchMedia("(max-width: 960px)");
const phoneScreen = window.matchMedia("(max-width: 560px)");
const settingsUi = window.KenobiSettings || null;
const callUi = window.KenobiCall || null;
const LAYOUT_KEY = "kenobi.layout.v1";

// Help the learner can ask for in one click (help-seeking stays their decision).
const QUICK_ACTIONS = [
  { id: "hint", label: "Un indice", prompt: "Donne-moi un indice, sans la solution." },
  { id: "explain", label: "Explique autrement", prompt: "Je ne comprends pas encore. Explique autrement, avec un exemple très simple." },
  { id: "example", label: "Un exemple", prompt: "Montre-moi un exemple résolu proche, puis laisse-moi essayer." },
  { id: "solution", label: "La solution", prompt: "Donne-moi la solution, puis une variante à refaire seul." },
  { id: "bilan", label: "Faire le bilan", prompt: "Fais le bilan de cette leçon : qu’est-ce que je sais faire seul, et qu’est-ce qui reste fragile ?" }
];

let state = null;
// Version tag of the saved state this page last read or wrote (see fetchState).
let stateTag = null;
let checkingRemote = false;
const removedCourses = new Set();
let health = null;
let settingsInfo = null;
let currentView = "overview";
let activeSessionId = null;
let renderedChatSessionId = null;
let memoryText = "";
let memoryRequest = 0;
let saveQueue = Promise.resolve();
let toastTimer = null;
let scrollChatAfterRender = false;
// The conversation is read from its bottom: it stays there through every redraw and resize, unless the learner
// scrolls up to read; sending a message brings it back down.
let chatPinned = true;
let streaming = null;
// A spoken turn of the call being answered, and the session the call belongs to.
let voiceTurn = null;
let callSessionId = null;
let pendingFrame = 0;
// Terminal: last run not yet seen by Kenobi, the run waiting for him to finish, and the editor code he last received.
let pendingRun = null;
let queuedRun = null;
let droppedCode = null;
const sharedCode = new Map();
const ATTACH_MAX_AGE = 30 * 60 * 1000;
const terminal = window.KenobiTerminal || null;
// Files joined from the learner's computer (own terminal, VS Code…), sent with the next message of that session.
let draftFiles = [];
let draftSessionId = null;
let dragDepth = 0;
const OWN_TERMINAL_MESSAGE = "[Terminal] Je passe sur mon propre terminal (VS Code). Tu ne vois plus mes exécutions : je t’enverrai mon code et ce qu’il affiche ici, en copier-coller ou en fichier joint.";

const e = C.escapeHtml;
const icon = (name, className = "") => `<svg class="icon ${className}" aria-hidden="true"><use href="#i-${name}"/></svg>`;
const newId = (prefix) => `${prefix}-${crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`}`;
const plural = (count, word) => `${count} ${word}${count > 1 ? "s" : ""}`;
const layout = readLayout();
const isBusy = () => Boolean(streaming || voiceTurn);
const learnerName = () => settingsInfo?.learner?.name?.trim() || "Toi";

function readLayout() {
  try {
    return { sidebar: "open", context: true, ...JSON.parse(localStorage.getItem(LAYOUT_KEY) || "{}") };
  } catch {
    return { sidebar: "open", context: true };
  }
}

// The side panels fold to leave the middle to the conversation and the terminal; the choice is remembered.
function applyLayout({ store = false } = {}) {
  document.body.classList.toggle("sidebar-rail", layout.sidebar === "rail");
  document.body.classList.toggle("context-hidden", !layout.context);
  if (!store) return;
  try {
    localStorage.setItem(LAYOUT_KEY, JSON.stringify(layout));
  } catch {
    /* Private mode: the layout simply does not persist. */
  }
  window.dispatchEvent(new Event("resize"));
}
const capitalize = (text) => String(text || "").replace(/^\s*\p{Ll}/u, (letter) => letter.toUpperCase());

async function requestJson(path, options) {
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `Erreur HTTP ${response.status}`);
  return data;
}

function course() {
  return C.getCourse(state);
}

function activeSession() {
  return course()?.sessions.find((item) => item.id === activeSessionId) || null;
}

function showToast(message) {
  clearTimeout(toastTimer);
  toast.querySelector("span").textContent = message;
  toast.hidden = false;
  toastTimer = setTimeout(() => { toast.hidden = true; }, 5200);
}

function closePanels() {
  document.body.classList.remove("sidebar-open", "context-open");
  document.getElementById("page-backdrop").hidden = true;
}

function syncBackdrop() {
  const open = document.body.classList.contains("sidebar-open") || document.body.classList.contains("context-open");
  document.getElementById("page-backdrop").hidden = !open;
}

function transitionRender(update) {
  if (document.startViewTransition && !reducedMotion.matches) document.startViewTransition(update);
  else update();
}

// A view other than the conversation opens at its top; the conversation opens at its bottom (render).
function setView(view) {
  transitionRender(() => {
    currentView = view;
    render();
    if (view !== "chat") mainContent.scrollTop = 0;
  });
  closePanels();
  if (view === "memory" && course()) loadMemory();
}

// Pending (streaming) replies never reach the disk: the server reads the saved state as context.
function snapshotState() {
  return JSON.stringify(state, (key, value) => (key === "messages" && Array.isArray(value) ? value.filter((message) => !message.pending) : value));
}

// The saved state carries a version tag. It can change elsewhere: a call through the voice gateway adds its turns
// to the session, another tab saves. This page then merges what it does not have yet instead of overwriting it.
async function fetchState() {
  const response = await fetch("api/state", { cache: "no-store" });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `Erreur HTTP ${response.status}`);
  stateTag = (response.headers.get("ETag") || "").replace(/"/g, "") || null;
  return data;
}

// Messages and sessions that only the saved state has join this page's state, in time order; this page stays the
// authority for everything else (lessons, memory, contracts). Returns the number of messages added.
function mergeRemoteState(remote) {
  let added = 0;
  for (const remoteCourse of remote?.courses || []) {
    const local = state.courses.find((item) => item.id === remoteCourse.id);
    if (!local) {
      if (!removedCourses.has(remoteCourse.id)) {
        state.courses.push(remoteCourse);
        added += 1;
      }
      continue;
    }
    for (const remoteSession of remoteCourse.sessions || []) {
      const session = local.sessions.find((item) => item.id === remoteSession.id);
      if (!session) {
        local.sessions.push(remoteSession);
        added += Math.max(1, (remoteSession.messages || []).length);
        continue;
      }
      const known = new Set(session.messages.map((message) => message.id));
      const fresh = (remoteSession.messages || []).filter((message) => message?.id && !known.has(message.id));
      if (!fresh.length) continue;
      const pending = session.messages.filter((message) => message.pending);
      const settled = session.messages.filter((message) => !message.pending).concat(fresh);
      settled.sort((a, b) => String(a.createdAt || "").localeCompare(String(b.createdAt || "")));
      session.messages = settled.concat(pending);
      if (String(remoteSession.updatedAt || "") > String(session.updatedAt || "")) session.updatedAt = remoteSession.updatedAt;
      added += fresh.length;
    }
  }
  return added;
}

function announceRemote(added) {
  if (!added) return;
  render();
  showToast(added > 1 ? `${added} messages sont arrivés d’ailleurs (appel, autre onglet).` : "Un message est arrivé d’ailleurs (appel, autre onglet).");
}

async function postState(snapshot) {
  const headers = { "Content-Type": "application/json" };
  if (stateTag) headers["If-Match"] = `"${stateTag}"`;
  const response = await fetch("api/state", { method: "POST", headers, body: snapshot });
  const data = await response.json().catch(() => ({}));
  if (response.status === 409 && data.conflict) return { conflict: true };
  if (!response.ok) throw new Error(data.error || `Erreur HTTP ${response.status}`);
  if (data.tag) stateTag = data.tag;
  return { conflict: false };
}

function persist({ silent = false } = {}) {
  if (!state) return saveQueue;
  const timestamp = new Date().toISOString();
  state.updatedAt = timestamp;
  if (course()) course().updatedAt = timestamp;
  saveQueue = saveQueue.then(async () => {
    saveStatus.textContent = "Enregistrement…";
    saveStatus.className = "save-status is-saving";
    try {
      // Taken now, not when persist() was called: a merge may have happened in between.
      let outcome = await postState(snapshotState());
      if (outcome.conflict) {
        announceRemote(mergeRemoteState(await fetchState()));
        outcome = await postState(snapshotState());
        if (outcome.conflict) throw new Error("l’état change ailleurs en même temps, réessaie dans un instant");
      }
      saveStatus.textContent = "Enregistré localement";
      saveStatus.className = "save-status";
    } catch (error) {
      saveStatus.textContent = "Échec de l’enregistrement";
      saveStatus.className = "save-status is-error";
      if (!silent) showToast(`Modification conservée à l’écran, mais non enregistrée : ${error.message}`);
    }
  });
  return saveQueue;
}

// Looks for a newer saved state while the page is visible (a call through the voice gateway, another tab).
async function checkRemoteState() {
  if (!state || document.hidden || checkingRemote) return;
  checkingRemote = true;
  try {
    await saveQueue;
    const { tag } = await requestJson("api/state/tag");
    if (tag && stateTag && tag !== stateTag) {
      const added = mergeRemoteState(await fetchState());
      announceRemote(added);
      if (added) persist({ silent: true });
    }
  } catch {
    /* Server unreachable for a moment: the next check will tell. */
  } finally {
    checkingRemote = false;
  }
}

function render() {
  if (!state) return;
  const input = document.getElementById("message-input");
  const sameChat = currentView === "chat" && renderedChatSessionId === activeSessionId;
  const keepDraft = input && sameChat;
  const draft = keepDraft ? { value: input.value, start: input.selectionStart, end: input.selectionEnd, focused: document.activeElement === input, height: input.style.height } : null;
  const readingAt = mainContent.scrollTop;

  const currentCourse = course();
  renderSidebar(currentCourse);
  renderTopbar(currentCourse);
  let content;
  if (!currentCourse) {
    content = renderNoCourse();
    renderedChatSessionId = null;
  } else if (currentView === "chat") {
    const session = activeSession();
    content = renderChat(session);
    renderedChatSessionId = session ? session.id : null;
  } else {
    if (currentView === "sessions") content = renderSessions();
    else if (currentView === "memory") content = renderMemory();
    else content = renderOverview();
    renderedChatSessionId = null;
  }
  mainContent.innerHTML = content;
  // Not « data-view » : that attribute marks the navigation buttons, and a click anywhere in the conversation
  // would then reopen the view and scroll it back to the top.
  mainContent.dataset.screen = currentCourse ? currentView : "empty";
  contextPanel.innerHTML = currentCourse ? renderContext() : "";

  const nextInput = document.getElementById("message-input");
  if (draft && nextInput) {
    nextInput.value = draft.value;
    nextInput.style.height = draft.height;
    if (draft.focused) {
      nextInput.focus({ preventScroll: true });
      nextInput.setSelectionRange(draft.start, draft.end);
    }
  }
  if (currentView === "chat") {
    // Another session, or a message just sent: back to the bottom. Otherwise the reader stays where he was.
    if (scrollChatAfterRender || !sameChat) chatPinned = true;
    scrollChatAfterRender = false;
    if (chatPinned) pinChat();
    else mainContent.scrollTop = readingAt;
  }
  watchChatSize();
  terminal?.setLesson(currentLesson());
}

function renderSidebar(currentCourse) {
  document.getElementById("session-count").textContent = String(currentCourse?.sessions.length || 0);
  document.getElementById("course-count").textContent = String(state.courses.length);
  document.getElementById("course-list").innerHTML = state.courses.map((item) => {
    const progress = C.getProgress(item);
    const active = item.id === currentCourse?.id;
    return `
      <button class="course-button ${active ? "is-active" : ""}" type="button" data-action="switch-course" data-course-id="${e(item.id)}" ${active ? 'aria-current="true"' : ""} title="${e(item.title)}">
        <span class="course-monogram" aria-hidden="true">${e(C.courseMonogram(item))}</span>
        <span class="course-button-copy"><strong>${e(C.courseShort(item))}</strong><span>${progress.percent} % · ${progress.done}/${progress.total} leçons</span></span>
        <span class="course-progress" aria-hidden="true"><span style="width:${progress.percent}%"></span></span>
      </button>`;
  }).join("");
  document.querySelectorAll("[data-view]").forEach((button) => {
    const active = Boolean(currentCourse) && button.dataset.view === (currentView === "chat" ? "overview" : currentView);
    button.classList.toggle("active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
}

function renderTopbar(currentCourse) {
  const titles = { overview: "Plan du cours", sessions: "Séances de travail", memory: "Mémoire du cours" };
  const session = currentCourse && currentView === "chat" ? activeSession() : null;
  let sub = "";
  if (session) {
    const validated = C.getLesson(currentCourse, session.lessonId)?.status === "done";
    sub = `${session.status === "active" ? "Séance en cours" : validated ? "Leçon validée" : "Séance close"} · ${C.formatDate(session.updatedAt)}`;
  }
  document.getElementById("topbar-course").textContent = currentCourse ? C.courseShort(currentCourse) : "Kenobi";
  document.getElementById("topbar-title").textContent = !currentCourse ? "Bienvenue" : session ? session.title : titles[currentView] || titles.overview;
  document.getElementById("topbar-sub").textContent = sub;
  document.title = currentCourse ? `Kenobi · ${C.courseShort(currentCourse)}` : "Kenobi · Tuteur de code";
}

// No course yet: the first step is to add one (and to plug a model).
function renderNoCourse() {
  return `
    <section class="chat-empty-state">
      <img class="chat-start-mascot" src="assets/kenobi-mascotte.svg" alt="" width="120" height="120">
      <h1>Aucun cours pour l’instant</h1>
      <p>Ajoute le document Markdown d’un cours d’informatique ou de code : Kenobi en tire le plan et t’accompagne leçon après leçon.</p>
      ${renderSetupBanner()}
      <button class="button primary" type="button" data-action="import-course">${icon("plus")}Ajouter un cours</button>
    </section>`;
}

function renderSetupBanner() {
  if (!health || health.agent?.configured) return "";
  return `
    <section class="setup-banner" aria-label="Brancher un modèle d’IA">
      <div><strong>Kenobi n’a pas encore de modèle d’IA.</strong><span>Branche une clé d’API (Mistral, OpenAI, Anthropic, OpenRouter…) ou un modèle local : c’est lui qui fait répondre le tuteur.</span></div>
      <button class="button primary" type="button" data-action="open-settings" data-tab="ai">${icon("key")}Brancher un modèle</button>
    </section>`;
}

// « Bien démarrer »: the first steps, until an AI is plugged and a first lesson is open (or the learner hides it).
const START_KEY = "kenobi.start.hidden";
function renderGetStarted(currentCourse) {
  if (!health) return "";
  const connected = Boolean(health.agent?.configured);
  const started = state.courses.some((item) => item.sessions.length);
  let hidden = false;
  try { hidden = localStorage.getItem(START_KEY) === "1"; } catch { /* private mode */ }
  if (connected && (started || hidden)) return "";
  if (!connected && !started && hidden) return renderSetupBanner();
  const step = (done, number, title, text, action) => `
    <li class="start-step ${done ? "is-done" : ""}">
      <span class="start-mark" aria-hidden="true">${done ? icon("check") : number}</span>
      <div><strong>${title}</strong><span>${text}</span></div>
      ${action}
    </li>`;
  const model = connected ? `${e(health.agent.model)} · ${e(health.agent.provider)}` : "";
  return `
    <section class="get-started" aria-labelledby="start-title">
      <div class="get-started-head">
        <div><h2 id="start-title">Bien démarrer avec Kenobi</h2><p>Deux étapes pour ta première leçon ; le reste quand tu veux.</p></div>
        <div class="get-started-links"><button class="text-button" type="button" data-action="open-welcome">${icon("quiz")}Comment ça marche</button>${connected ? `<button class="text-button" type="button" data-action="hide-start">Masquer</button>` : ""}</div>
      </div>
      <ol class="start-steps">
        ${step(connected, 1, "Branche ton IA", connected ? `Branchée : ${model}.` : "Choisis un fournisseur (Mistral, OpenAI, Anthropic, Google, OpenRouter…), colle ta clé d’API, choisis un modèle rapide.",
          connected ? "" : `<button class="button primary" type="button" data-action="open-settings" data-tab="ai">${icon("key")}Brancher mon IA</button>`)}
        ${step(started, 2, "Commence la première leçon", `« ${e(C.getProgress(currentCourse).current?.title || currentCourse.title)} » : Kenobi fait d’abord un cours court, puis te fait pratiquer.`,
          started ? "" : `<button class="button ${connected ? "primary" : "secondary"}" type="button" data-action="resume-course">Commencer${icon("arrow")}</button>`)}
        ${step(false, 3, "Ajoute ton propre cours <em>(facultatif)</em>", "Un document Markdown, ou un cours écrit par ton IA à partir de ce que tu veux apprendre.",
          `<button class="button secondary" type="button" data-action="import-course">${icon("plus")}Ajouter un cours</button>`)}
        ${step(false, 4, "Parle-lui à voix haute <em>(facultatif)</em>", "Whisper sur ton ordinateur (gratuit, privé) ou une clé vocale Mistral ou ElevenLabs.",
          `<button class="button secondary" type="button" data-action="open-settings" data-tab="voice">${icon("volume")}Régler la voix</button>`)}
      </ol>
    </section>`;
}

// The lesson whose folder the terminal works in: the open session's, otherwise the next one of the course.
function currentLesson() {
  const currentCourse = course();
  if (!currentCourse) return null;
  const tools = C.courseTools(currentCourse);
  const session = currentView === "chat" ? activeSession() : null;
  const lesson = session ? C.getLesson(currentCourse, session.lessonId) : C.getProgress(currentCourse).current;
  return {
    id: lesson?.id || "", title: lesson?.title || "", course: currentCourse.id,
    language: tools.language || "python", file: tools.file || "exercice.py", terminal: tools.terminal
  };
}

function renderOverview() {
  const currentCourse = course();
  const progress = C.getProgress(currentCourse);
  const next = progress.current;
  const due = C.dueReviews(currentCourse.memory);
  const tools = C.courseTools(currentCourse);
  const goals = (currentCourse.goals || []).filter(Boolean);
  const hasDocument = Boolean(currentCourse.source) || currentCourse.id === "python";
  const objectives = (next?.objectives || []).filter(Boolean);
  const intro = objectives.length
    ? objectives.map((item) => capitalize(item)).join(" · ")
    : "Kenobi commence par le cours et l’objectif du jour, te fait pratiquer (exercices, QCM), puis propose de valider la leçon quand tu réussis seul.";
  return `
    <section class="overview-view" aria-labelledby="course-title">
      ${renderGetStarted(currentCourse)}
      <header class="course-heading">
        <div><h1 id="course-title">${e(currentCourse.title)}</h1><p>${e(currentCourse.description)}</p></div>
        <div class="course-heading-actions">
          ${hasDocument ? `<a class="button ghost" href="api/courses/source?courseId=${encodeURIComponent(currentCourse.id)}" target="_blank" rel="noopener" aria-label="Document du cours" title="Ouvrir le document Markdown du cours">${icon("file")}<span>Document</span></a>` : ""}
          <button class="icon-button" type="button" data-action="delete-course" data-course-id="${e(currentCourse.id)}" aria-label="Supprimer ce cours" title="Supprimer ce cours">${icon("trash")}</button>
        </div>
      </header>
      ${next ? `
        <section class="next-lesson" aria-labelledby="next-lesson-title">
          <div class="next-lesson-copy">
            <span class="lesson-position">${icon("play")}Prochaine étape · ${e(next.moduleTitle)}</span>
            <h2 id="next-lesson-title">${e(next.title)}</h2>
            <p class="next-lesson-description">${e(intro)}</p>
            <div class="lesson-meta"><span>${icon("clock")}${next.duration} min environ</span>${currentCourse.level ? `<span>${icon("book")}${e(capitalize(currentCourse.level))}</span>` : ""}${tools.terminal ? `<span>${icon("terminal")}${e(tools.label)}</span>` : ""}${due.length ? `<span class="due-pill">${icon("repeat")}${plural(due.length, "révision")} aujourd’hui</span>` : ""}</div>
          </div>
          <div class="next-lesson-action">
            <div class="progress-copy"><strong>${progress.done} sur ${progress.total} leçons</strong><span>${progress.percent} % du cours</span></div>
            <button class="button primary" type="button" data-action="resume-course">Reprendre le cours${icon("arrow")}</button>
          </div>
        </section>` : `
        <section class="next-lesson"><div><h2>Cours terminé</h2><p class="next-lesson-description">Toutes les leçons sont validées. Tu peux rouvrir une leçon du plan pour réviser.</p></div></section>`}
      ${goals.length ? `
        <section class="course-goals" aria-labelledby="goals-title">
          <h2 id="goals-title">À la fin du cours, tu sais</h2>
          <ul>${goals.slice(0, 8).map((goal) => `<li>${icon("check")}<span>${C.inlineMarkdown(goal)}</span></li>`).join("")}</ul>
        </section>` : ""}
      <section class="curriculum" aria-labelledby="curriculum-title">
        <div class="section-heading"><h2 id="curriculum-title">Le plan du cours</h2><span>${progress.total} leçons · ${plural(currentCourse.modules.length, "module")}</span></div>
        <div class="module-list">${currentCourse.modules.map((module) => renderModule(module, next)).join("")}</div>
      </section>
    </section>`;
}

function renderModule(module, next) {
  const done = module.lessons.filter((lesson) => lesson.status === "done").length;
  const complete = done === module.lessons.length;
  const current = module.lessons.some((lesson) => lesson.id === next?.id);
  return `
    <details class="module ${complete ? "is-complete" : ""} ${current ? "is-current" : ""}" ${current ? "open" : ""}>
      <summary class="module-summary">
        <span class="module-state">${icon(complete ? "check" : current ? "play" : "book")}</span>
        <span class="module-heading"><span class="module-title">${e(module.title)}</span><span class="module-description">${e(module.description)}</span></span>
        <span class="module-count">${done}/${module.lessons.length}</span>
        ${icon("chevron")}
      </summary>
      <ol class="lesson-list">${module.lessons.map((lesson) => `
        <li class="lesson-row is-${lesson.status}">
          <button class="lesson-check" type="button" data-action="toggle-lesson" data-lesson-id="${e(lesson.id)}" aria-label="${lesson.status === "done" ? `Rouvrir la leçon ${e(lesson.title)}` : `Marquer ${e(lesson.title)} comme validée`}">${icon("check")}</button>
          <button class="lesson-open" type="button" data-action="start-lesson" data-lesson-id="${e(lesson.id)}">${e(lesson.title)}</button>
          <span class="lesson-duration">${lesson.duration} min</span>
        </li>`).join("")}</ol>
    </details>`;
}

function renderList(items, empty, className = "") {
  const values = (items || []).filter(Boolean).slice(0, 4);
  if (!values.length) return `<p class="context-empty">${e(empty)}</p>`;
  return `<ul class="memory-list ${className}">${values.map((item) => `<li>${C.inlineMarkdown(item)}</li>`).join("")}</ul>`;
}

function dueLabel(due) {
  const today = C.localDate();
  if (due <= today) return "aujourd’hui";
  if (due === C.addDays(new Date(), 1)) return "demain";
  return C.formatDate(`${due}T12:00:00`);
}

function renderContext() {
  const currentCourse = course();
  const memory = currentCourse.memory;
  const session = currentView === "chat" ? activeSession() : null;
  const progress = C.getProgress(currentCourse);
  const lesson = session ? C.getLesson(currentCourse, session.lessonId) : progress.current;
  const contract = session?.contract;
  const reviews = Array.isArray(memory.reviews) ? memory.reviews : [];
  const sessions = C.sortedSessions(currentCourse);
  const lastSession = sessions.find((item) => item.id === memory.lastSessionId) || sessions[0];
  return `
    <div class="context-head"><h2>${session ? "Leçon en cours" : "Reprendre le fil"}</h2><button class="icon-button context-close" type="button" data-action="close-panels" aria-label="Fermer le contexte">${icon("close")}</button>${icon("memory")}</div>
    ${lesson ? `<section class="context-lesson">
      <span class="context-label">${e(lesson.moduleTitle || "")}</span>
      <h3>${e(lesson.title)}</h3>
      ${contract?.objective ? `<p class="contract-objective">${C.inlineMarkdown(capitalize(contract.objective))}</p>${(contract.criteria || []).length ? `<ul class="criteria-list">${contract.criteria.map((item) => `<li>${icon("check")}<span>${C.inlineMarkdown(capitalize(item))}</span></li>`).join("")}</ul>` : ""}`
        : (lesson.objectives || []).length ? `<ul class="criteria-list">${lesson.objectives.map((item) => `<li>${icon("target")}<span>${C.inlineMarkdown(capitalize(item))}</span></li>`).join("")}</ul>`
        : `<p class="context-hint">${session ? "Kenobi fait d’abord le cours, puis annonce l’objectif et les critères de réussite." : "Prochaine leçon du cours."}</p>`}
    </section>` : ""}
    ${!session && lastSession ? `<section class="last-point"><span class="context-label">Dernière séance · ${C.formatDate(lastSession.updatedAt)}</span><h3>${e(lastSession.title)}</h3><p>${e(lastSession.summary)}</p><button class="text-button context-link" type="button" data-action="open-session" data-session-id="${e(lastSession.id)}">Revoir cette séance${icon("arrow")}</button></section>` : ""}
    <section class="memory-section"><h3>${icon("repeat")}Révisions</h3>${reviews.length ? `<ul class="review-list">${reviews.slice(0, 4).map((review) => `<li class="${review.due <= C.localDate() ? "is-due" : ""}"><span>${C.inlineMarkdown(review.item)}</span><time>${e(dueLabel(review.due))}</time></li>`).join("")}</ul>` : `<p class="context-empty">Aucune révision prévue.</p>`}</section>
    <section class="memory-section"><h3>${icon("check")}Acquis</h3>${renderList(memory.acquired, "Rien de validé pour l’instant.")}</section>
    <section class="memory-section"><h3>${icon("memory")}À retravailler</h3>${renderList(memory.toReview, "Rien de noté.", "review")}</section>
    <section class="memory-section"><h3>${icon("flag")}Prochaine étape</h3><p class="next-focus">${C.inlineMarkdown(memory.nextFocus || "À définir avec Kenobi.")}</p></section>
    ${(memory.facts || []).length ? `<section class="memory-section"><h3>${icon("bot")}Ce que Kenobi a retenu</h3>${renderList(memory.facts, "")}</section>` : ""}
    <div class="context-footer"><button class="button secondary full" type="button" data-view="memory">${icon("file")}Voir la mémoire du cours</button></div>`;
}

function codeFence() {
  const language = C.courseTools(course()).language;
  return language === "javascript" ? "javascript" : language === "shell" ? "bash" : language === "python" ? "python" : "text";
}

function renderComposer(session, tools, busy) {
  if (health && !health.agent?.configured) {
    return `
      <div class="composer-setup">
        <span>${icon("key")}Branche un modèle d’IA pour parler à Kenobi.</span>
        <button class="button primary" type="button" data-action="open-settings" data-tab="ai">Ouvrir les réglages</button>
      </div>`;
  }
  const codeTools = tools.terminal ? `
              <button class="composer-tool" type="button" data-action="insert-code" aria-label="Insérer un bloc de code" title="Insérer un bloc de code">${icon("code")}</button>
              <button class="composer-tool" type="button" data-action="insert-output" aria-label="Insérer ce que ton programme affiche" title="Insérer ce que ton programme affiche">${icon("terminal")}</button>` : "";
  return `
        <form class="composer" id="message-form">
          <div class="composer-attachments" id="composer-attachments">${renderAttachments(session)}</div>
          <div class="composer-row">
            <div class="composer-tools">${codeTools}
              <button class="composer-tool" type="button" data-action="attach-file" aria-label="Joindre des fichiers de ton ordinateur" title="Joindre des fichiers de ton ordinateur, ou les glisser ici">${icon("paperclip")}</button>
              <input id="attach-input" type="file" multiple hidden accept="${e(C.ATTACH_ACCEPT)}">
            </div>
            <label class="sr-only" for="message-input">Ta réponse ou ta question</label>
            <textarea id="message-input" rows="1" maxlength="12000" placeholder="${phoneScreen.matches ? "Écris à Kenobi…" : "Réponds à Kenobi ou pose ta question…"}" title="Entrée : envoyer · Maj + Entrée : nouvelle ligne${terminal ? " · Ctrl + J : terminal" : ""}"></textarea>
            ${streaming ? `<button class="button secondary stop-button" type="button" data-action="stop" aria-label="Arrêter la réponse">${icon("stop")}<span>Arrêter</span></button>` : `<button class="button primary send-button" type="submit" aria-label="Envoyer" ${busy ? "disabled" : ""}><span>Envoyer</span>${icon("send")}</button>`}
          </div>
        </form>`;
}

function renderChat(session) {
  if (!session) return `<section class="chat-empty-state"><h1>Aucune séance ouverte</h1><p>Reprends la prochaine leçon pour ouvrir un fil de travail.</p><button class="button primary" type="button" data-action="resume-course">Reprendre le cours</button></section>`;
  const currentCourse = course();
  const lesson = C.getLesson(currentCourse, session.lessonId);
  const tools = C.courseTools(currentCourse);
  const messages = session.messages || [];
  const busy = isBusy();
  const validated = lesson?.status === "done";
  const next = C.getProgress(currentCourse).current;
  const connected = !health || health.agent?.configured;
  return `
    <section class="chat-view" aria-label="Séance : ${e(session.title)}">
      <div class="message-list" aria-label="Messages de la séance">
        ${messages.length ? messages.map((message) => renderMessage(message, session)).join("") : renderChatStart(lesson, session)}
      </div>
      <div class="composer-wrap">
        ${session.status !== "active" && validated && next && next.id !== session.lessonId ? `<div class="closed-notice">${icon("check")}<span>Leçon validée. Tu peux encore poser une question ici.</span><button class="text-button" type="button" data-action="start-lesson" data-lesson-id="${e(next.id)}">Leçon suivante${icon("arrow")}</button></div>` : ""}
        ${messages.length && connected && !awaitsStart(session) ? `<div class="quick-actions" role="group" aria-label="Demander de l’aide à Kenobi">${QUICK_ACTIONS.map((action) => `<button class="quick-action" type="button" data-action="quick" data-quick="${action.id}" ${busy ? "disabled" : ""}>${e(action.label)}</button>`).join("")}</div>` : ""}
        ${renderComposer(session, tools, busy)}
      </div>
    </section>`;
}

// « C'est parti ! » waits under Kenobi's last message: the help pills have nothing to help with yet.
function awaitsStart(session) {
  const last = session.messages[session.messages.length - 1];
  return Boolean(last && !last.pending && C.normalizeRole(last.role) === "tutor"
    && C.extractKenobiBlocks(last.content).blocks.some((block) => block.type === "pret"));
}

function renderChatStart(lesson, session) {
  const due = C.dueReviews(course().memory);
  const connected = !health || health.agent?.configured;
  // Even a known lesson starts with the course, in a shorter form.
  const knownPrompt = "Je pense déjà savoir faire : fais-moi le cours en version express, puis teste-moi.";
  const objectives = (lesson?.objectives || []).filter(Boolean);
  return `
    <div class="chat-start">
      <img class="chat-start-mascot" src="assets/kenobi-mascotte.svg" alt="" width="112" height="112">
      <h2>${e(lesson?.title || session.title)}</h2>
      ${objectives.length ? `<ul class="chat-start-goals">${objectives.map((item) => `<li>${icon("target")}<span>${C.inlineMarkdown(capitalize(item))}</span></li>`).join("")}</ul>` : ""}
      <p>Kenobi commence par le cours : la notion, à quoi elle sert, des exemples. Tu poses tes questions, puis tu pratiques${due.length ? ` (avec ${plural(due.length, "révision")} due${due.length > 1 ? "s" : ""})` : ""} jusqu’à réussir seul.</p>
      ${connected ? `<button class="button primary" type="button" data-action="start-session">Commencer la leçon${icon("arrow")}</button>
      <div class="suggestion-list">
        <button class="suggestion" type="button" data-action="suggestion" data-prompt="${e(knownPrompt)}">Je pense déjà savoir</button>
        <button class="suggestion" type="button" data-action="suggestion" data-prompt="J’ai une question sur cette leçon : ">Poser une question</button>
      </div>` : ""}
    </div>`;
}

function renderRich(text) {
  return C.renderMarkdown(text, { code: renderCodeBlock });
}

function renderCodeBlock(content, language) {
  const output = C.isOutputLanguage(language);
  const code = ["python", "py", "python3", "javascript", "js", "bash", "sh", "shell"].includes(String(language).toLowerCase());
  const toEditor = !output && code && terminal && C.courseTools(course()).terminal ? `<button class="copy-code" type="button" data-action="code-to-editor" aria-label="Mettre ce code dans l’éditeur du terminal">${icon("terminal")}<span>Éditeur</span></button>` : "";
  const copy = output ? "" : `<button class="copy-code" type="button" data-action="copy-code" aria-label="Copier ce code">${icon("copy")}<span>Copier</span></button>`;
  return `<div class="code-block ${output ? "is-output" : ""}"><div class="code-head"><span class="code-language">${icon(output ? "terminal" : "code")}${e(C.languageLabel(language))}</span><span class="code-actions">${toEditor}${copy}</span></div><pre><code>${C.highlightCode(content, language)}</code></pre></div>`;
}

function shortCommand(command) {
  return String(command || "").replace(/^cd\s+\S+\s+&&\s+/, "");
}

// An error of the open lesson links to its line in the editor.
function renderRunPill(run) {
  const label = e(C.runLabel(run, run.exit));
  if (!terminal || !run.errorLine || !run.file || !run.lesson || currentLesson()?.id !== run.lesson) return `<span class="run-pill">${label}</span>`;
  return `<button class="run-pill is-link" type="button" data-action="goto-line" data-file="${e(run.file)}" data-line="${run.errorLine}" data-lesson="${e(run.lesson)}" title="Voir la ligne ${run.errorLine} dans l’éditeur">${label}${icon("arrow")}</button>`;
}

// What Kenobi received with the learner's words: run, editor file or joined files.
function receivedPart(message) {
  return (message.text ? message.content.slice(message.text.length) : message.content).trim();
}

function receivedDetails(text) {
  return text ? `<details class="run-card-details"><summary>${icon("chevron")}Ce que Kenobi a reçu</summary><div class="message-text">${renderRich(text)}</div></details>` : "";
}

function renderRunCard(message, { details = true } = {}) {
  const run = message.run;
  const tone = run.status === "ok" ? "ok" : run.status === "interrupted" ? "interrupted" : "error";
  return `
    <section class="run-card is-${tone}" aria-label="Exécution de ${e(run.file || shortCommand(run.cmd))}">
      <div class="run-card-head">${icon("terminal")}<code title="${e(run.cmd)}">${e(shortCommand(run.cmd))}</code>${renderRunPill(run)}</div>
      ${tone === "error" && run.errorMessage ? `<p class="run-card-error">${e(run.errorMessage)}</p>` : ""}
      ${details ? receivedDetails(receivedPart(message)) : ""}
    </section>`;
}

// Whole files joined from the learner's computer: one line per file, their content behind « Ce que Kenobi a reçu ».
function renderFilesCard(message) {
  const rows = message.files.map((item) => `<li>${icon("file")}<code title="${e(item.name)}">${e(item.name)}</code><span>${e(plural(item.lines, "ligne"))}</span></li>`).join("");
  return `
    <section class="run-card is-files" aria-label="Fichiers joints">
      <ul class="file-card-list">${rows}</ul>
      ${receivedDetails(receivedPart(message))}
    </section>`;
}

// « Je passe sur mon terminal » : a line in the conversation, Kenobi's answer follows.
function renderNotice(message, time) {
  return `
    <article class="message is-notice" data-message-id="${e(message.id)}">
      <p class="notice-line">${icon("external")}<span>Tu passes sur ton propre terminal : Kenobi ne regarde plus le terminal intégré.</span>${time}</p>
    </article>`;
}

// « Vérifier » : the terminal screen sent as an attempt, folded behind « Ce que Kenobi a reçu ».
function renderCheckMessage(message, time) {
  return `
    <article class="message is-user is-run" data-message-id="${e(message.id)}">
      <div class="message-body">
        <div class="message-meta"><strong>${e(learnerName())}</strong><span class="meta-subtle">Vérification n° ${message.attempt}</span>${time}</div>
        <section class="run-card is-ok"><div class="run-card-head">${icon("terminal")}<code>Écran du terminal</code></div>${receivedDetails(message.content)}</section>
      </div>
    </article>`;
}

// The option clicked in a QCM, shown as the learner's answer; the card above says why.
function renderQuizAnswer(message, time) {
  const { choice, correct } = message.qcm;
  const tone = correct === true ? "is-right" : correct === false ? "is-wrong" : "";
  return `
    <article class="message is-user is-quiz-answer" data-message-id="${e(message.id)}">
      <div class="message-body">
        <div class="message-meta"><strong>${e(learnerName())}</strong><span class="meta-subtle">Réponse au QCM</span>${time}</div>
        <p class="quiz-answer ${tone}">${correct === true ? icon("check") : correct === false ? icon("close") : ""}<span>${C.inlineMarkdown(choice)}</span></p>
      </div>
    </article>`;
}

// A message that carries a run or the editor file: the learner's words (if any), then what Kenobi received.
const RUN_ORIGINS = {
  error: "Run · erreur envoyée",
  exercise: "Run · tentative",
  fixed: "Run · correction envoyée",
  manual: "Run · envoyé à Kenobi"
};

function renderAttachedMessage(message, time) {
  const run = message.run;
  const files = Array.isArray(message.files) ? message.files : [];
  const origin = files.length ? (files.length > 1 ? `${files.length} fichiers joints` : "Fichier joint")
    : !run ? "Fichier joint"
    : message.reason === "exercise" && run.attempt ? `Run · tentative n° ${run.attempt}`
    : RUN_ORIGINS[message.reason] || (message.auto ? (run.status === "ok" ? RUN_ORIGINS.exercise : RUN_ORIGINS.error) : "Run · joint au message");
  const note = message.text ? `<div class="run-note"><div class="message-text">${renderRich(message.text)}</div></div>` : "";
  const fileDetails = !run && message.file ? `<section class="run-card is-ok"><div class="run-card-head">${icon("file")}<code>${e(message.file)}</code><span class="run-pill">pas encore lancé</span></div>${receivedDetails(receivedPart(message))}</section>` : "";
  // Joined files carry everything Kenobi received; a run sent along only shows its head.
  const cards = files.length ? `${renderFilesCard(message)}${run ? renderRunCard(message, { details: false }) : ""}` : run ? renderRunCard(message) : fileDetails;
  return `
    <article class="message is-user is-run" data-message-id="${e(message.id)}">
      <div class="message-body">
        <div class="message-meta"><strong>${e(learnerName())}</strong><span class="meta-subtle">${e(origin)}</span>${time}</div>
        ${note}${cards}
      </div>
    </article>`;
}

function renderMessage(message, session) {
  const time = `<time datetime="${e(message.createdAt)}">${C.formatTime(message.createdAt)}</time>`;
  const spoken = message.voice ? `<span class="meta-voice" title="Dit pendant l’appel">${icon("mic")}à voix haute</span>` : "";
  if (C.normalizeRole(message.role) !== "tutor") {
    if (message.notice === "own-terminal") return renderNotice(message, time);
    if (message.qcm) return renderQuizAnswer(message, time);
    if (message.run || message.file || message.files?.length) return renderAttachedMessage(message, time);
    if (message.attempt) return renderCheckMessage(message, time);
    return `
      <article class="message is-user" data-message-id="${e(message.id)}">
        <div class="message-body">
          <div class="message-meta"><strong>${e(learnerName())}</strong>${spoken}${time}</div>
          <div class="message-text">${renderRich(message.voice ? message.text || "" : message.content)}</div>
        </div>
      </article>`;
  }
  const meta = [];
  if (message.pending) meta.push(`<span class="meta-live">${message.content ? (message.voice ? "parle…" : "écrit…") : "réfléchit…"}</span>`);
  else if (message.firstToken) meta.push(`<span class="meta-subtle" title="Premier mot après ${e(C.formatSeconds(message.firstToken))}, réponse complète en ${e(C.formatSeconds(message.elapsed))}">${e(C.formatSeconds(message.elapsed || message.firstToken))}</span>`);
  if (message.stopped) meta.push(`<span class="meta-subtle">interrompu</span>`);
  return `
    <article class="message is-tutor ${message.pending ? "is-pending" : ""} ${message.error ? "is-error" : ""}" data-message-id="${e(message.id)}">
      <span class="message-avatar" aria-hidden="true"><img src="assets/kenobi.svg" alt="" width="32" height="32"></span>
      <div class="message-body">
        <div class="message-meta"><strong>Kenobi</strong>${spoken}${time}${meta.join("")}</div>
        <div class="message-text">${renderTutorContent(message, session)}</div>
      </div>
    </article>`;
}

function renderTutorContent(message, session) {
  if (message.error) return `<p>${e(message.content)}</p><button class="text-button" type="button" data-action="retry-message" data-message-id="${e(message.id)}">${icon("repeat")}Réessayer</button>`;
  // Kenobi thinking: a speech bubble with three dots next to his face, like a chat partner typing.
  if (message.pending && !message.content) {
    return `<div class="thinking" role="status"><span class="typing-bubble" aria-hidden="true"><i></i><i></i><i></i></span><span class="thinking-label">${e(message.status || "Kenobi réfléchit…")}</span></div>`;
  }
  const { text, blocks, pending } = C.extractKenobiBlocks(message.content);
  // The verdict on a run comes first: it is the answer; the rest explains it.
  const verdicts = C.answersAttempt(session, message.id) ? blocks.filter((block) => block.type === "verdict") : [];
  let html = verdicts.map((block) => renderBlock(block, message, session)).join("");
  html += text ? renderRich(text) : "";
  if (message.pending && message.status) html += `<div class="tool-status">${e(message.status)}</div>`;
  html += blocks.filter((block) => block.type !== "verdict").map((block) => renderBlock(block, message, session)).join("");
  if (message.pending && pending) html += `<div class="tool-status">Kenobi prépare une fiche…</div>`;
  // A yes/no question or a reply that asks nothing, without Kenobi's buttons: the interface offers them.
  if (!message.pending) html += renderReplyButtons(C.fallbackReplies(session, message));
  return html;
}

function renderReplyButtons(labels) {
  return labels.length ? `<div class="reply-buttons" role="group" aria-label="Réponses en un clic">${labels.map((label) => `<button class="reply-button" type="button" data-action="quick-reply" data-label="${e(label)}" ${isBusy() ? "disabled" : ""}>${C.inlineMarkdown(label)}</button>`).join("")}</div>` : "";
}

// Answer buttons live under the last message of the conversation, once it is complete.
function replyActive(message, session) {
  return Boolean(session && !message.pending && C.isLastMessage(session, message.id));
}

// A QCM: three options in one click; the card says at once whether the answer is right, and why.
function renderQuiz(block, message, session) {
  const quiz = C.quizOf(block.fields, message.id);
  if (!quiz) return "";
  const answer = session ? C.quizAnswer(session, message.id) : null;
  const latest = session ? C.latestQuiz(session) === message.id : false;
  const open = Boolean(!answer && !message.pending && latest);
  const chosen = answer?.qcm?.choice;
  const options = quiz.options.map((option, index) => {
    let state = "";
    if (answer && option.text === chosen) state = answer.qcm.correct === true ? "is-right" : answer.qcm.correct === false ? "is-wrong" : "is-picked";
    else if (answer) state = quiz.judged && option.correct ? "is-answer" : "is-dim";
    const mark = ["is-right", "is-answer"].includes(state) ? icon("check") : state === "is-wrong" ? icon("close") : "";
    return `<button class="quiz-option ${state}" type="button" data-action="quiz-answer" data-message-id="${e(message.id)}" data-choice="${index}" ${open && !isBusy() ? "" : "disabled"}><span class="quiz-mark" aria-hidden="true">${mark}</span><span class="quiz-text">${C.inlineMarkdown(option.text)}</span></button>`;
  }).join("");
  let feedback = "";
  if (answer && answer.qcm.correct === null) {
    feedback = `<p class="quiz-feedback">${icon("info")}<span>Réponse envoyée : Kenobi te dit si c’est juste.</span></p>`;
  } else if (answer) {
    const right = quiz.options.find((option) => option.correct);
    const correct = answer.qcm.correct === true;
    const verdict = correct ? "Bonne réponse." : right ? `La bonne réponse : ${C.inlineMarkdown(right.text)}.` : "Ce n’est pas la bonne réponse.";
    feedback = `<p class="quiz-feedback ${correct ? "is-right" : "is-wrong"}">${icon(correct ? "check" : "repeat")}<span><strong>${verdict}</strong>${quiz.explanation ? ` ${C.inlineMarkdown(quiz.explanation)}` : ""}</span></p>`;
  } else if (!message.pending && !latest) {
    feedback = `<p class="card-note">Remplacé par un QCM plus récent.</p>`;
  }
  return `
    <section class="quiz-card ${answer ? "is-answered" : ""}" aria-label="QCM">
      <div class="card-head">${icon("quiz")}<span>QCM · une seule bonne réponse</span></div>
      ${quiz.question ? `<p class="quiz-question">${C.inlineMarkdown(quiz.question)}</p>` : ""}
      <div class="quiz-options" role="group" aria-label="Choix de réponse">${options}</div>
      ${feedback}
    </section>`;
}

function renderBlock(block, message, session) {
  const fields = block.fields;
  if (block.type === "qcm") return renderQuiz(block, message, session);
  if (block.type === "pret") {
    // End of the course: one click to start practising, or a question first.
    if (!replyActive(message, session)) return "";
    const [label] = C.replyLabels(fields, ["C’est parti !"]);
    return `
      <div class="ready-bar" role="group" aria-label="Prêt à passer à la pratique ?">
        <button class="button primary" type="button" data-action="quick-reply" data-label="${e(label)}" ${isBusy() ? "disabled" : ""}>${e(label)}${icon("arrow")}</button>
        <button class="button secondary" type="button" data-action="suggestion" data-prompt="J’ai une question : ">${icon("quiz")}J’ai une question</button>
      </div>`;
  }
  if (block.type === "boutons") return replyActive(message, session) ? renderReplyButtons(C.replyLabels(fields)) : "";
  if (block.type === "lecon") {
    const criteria = fields.critere || [];
    return `
      <section class="lesson-card" aria-label="Objectif de la leçon">
        <div class="card-head">${icon("target")}<span>Objectif de la leçon</span></div>
        <p class="card-objective">${C.inlineMarkdown(capitalize(fields.objectif))}</p>
        ${criteria.length ? `<ul class="card-criteria">${criteria.map((item) => `<li>${icon("check")}<span>${C.inlineMarkdown(capitalize(item))}</span></li>`).join("")}</ul>` : ""}
      </section>`;
  }
  if (block.type === "exercice") {
    if (C.repeatsOpenTask(session, message.id, fields)) return "";
    const file = fields.fichier || C.courseTools(course()).file || "exercice.py";
    const task = session && !message.pending ? C.taskState(session, message.id) : null;
    const done = Boolean(task?.done);
    const attempts = task?.attempts || 0;
    const tools = C.courseTools(course());
    const tone = done || (task && !task.open) ? "secondary" : "primary";
    let action = "";
    let note;
    if (tools.runs) {
      // A language launched with Run: every run is an attempt.
      if (terminal && !message.pending) action = `<button class="button ${tone}" type="button" data-action="open-editor" data-file="${e(file)}">${icon("terminal")}Ouvrir dans l’éditeur</button>`;
      note = done ? `Réussie en ${plural(attempts, "lancement")}.`
        : !task || task.open ? (attempts ? `${plural(attempts, "lancement")} · Kenobi répond à chaque Run.` : "Écris ton code, puis Run : Kenobi répond à chaque lancement.")
        : "Remplacée par la consigne suivante.";
    } else {
      // The command line: the learner works in the terminal and asks for the check.
      if (terminal && !message.pending && !done && (!task || task.open)) action = `<button class="button secondary" type="button" data-action="open-terminal">${icon("terminal")}Ouvrir le terminal</button><button class="button primary" type="button" data-action="check-task" ${isBusy() ? "disabled" : ""}>${icon("check")}Vérifier</button>`;
      note = done ? `Réussie en ${plural(attempts, "vérification")}.`
        : !task || task.open ? (attempts ? `${plural(attempts, "vérification")} · relance « Vérifier » quand tu veux.` : "Fais-le dans le terminal, puis « Vérifier » : Kenobi regarde ton écran.")
        : "Remplacée par la consigne suivante.";
    }
    return `
      <section class="exercise-card ${done ? "is-done" : ""}" aria-label="Consigne">
        <div class="card-head">${icon(done ? "check" : "code")}<span>${done ? "Consigne réussie" : "Consigne"}</span>${tools.runs ? `<code>${e(file)}</code>` : ""}</div>
        ${fields.attendu ? `<p class="exercise-expected"><strong>Attendu :</strong> ${C.inlineMarkdown(fields.attendu)}</p>` : ""}
        ${message.pending ? "" : `<div class="card-actions">${action}<span class="card-note">${note}</span></div>`}
      </section>`;
  }
  if (block.type === "verdict") {
    const verdict = C.verdictOf(fields);
    if (!verdict) return "";
    const tone = C.VERDICTS[verdict];
    return `<p class="verdict-chip is-${tone.tone}">${icon(verdict === "reussi" ? "check" : verdict === "presque" ? "target" : "repeat")}<span>${tone.label}</span></p>`;
  }
  if (block.type === "memoire") {
    const kept = fields.retenir || [];
    return kept.length ? `<div class="review-chips">${kept.map((item) => `<span class="review-chip is-note">${icon("memory")}Noté · ${C.inlineMarkdown(item)}</span>`).join("")}</div>` : "";
  }
  if (block.type === "revision") {
    return `<div class="review-chips">${(fields.revu || []).map((line) => {
      const [item, result = ""] = line.split("|").map((part) => part.trim());
      const ok = /^(ok|r[ée]ussi|acquis|juste)/i.test(result);
      return `<span class="review-chip ${ok ? "is-ok" : "is-miss"}">${icon(ok ? "check" : "repeat")}${ok ? "Révision réussie" : "À revoir demain"} · ${C.inlineMarkdown(item)}</span>`;
    }).join("")}</div>`;
  }
  if (block.type !== "bilan") return "";
  const acquired = C.isValidatedStatus(fields.statut);
  const currentCourse = course();
  const lesson = session ? C.getLesson(currentCourse, session.lessonId) : null;
  const next = C.getProgress(currentCourse).current;
  const row = (label, values) => values && values.length ? `<div><dt>${label}</dt><dd>${values.length > 1 ? `<ul>${values.map((value) => `<li>${C.inlineMarkdown(value)}</li>`).join("")}</ul>` : C.inlineMarkdown(values[0])}</dd></div>` : "";
  const revisions = (fields.revision || []).map((line) => {
    const parsed = C.parseRevision(line);
    return parsed ? `${parsed.item} — dans ${parsed.step} j` : "";
  }).filter(Boolean);
  let actions = "";
  if (message.pending) actions = "";
  else if (acquired && lesson && lesson.status !== "done" && session.status === "active") {
    actions = `<div class="card-actions"><button class="button primary" type="button" data-action="validate-lesson" data-message-id="${e(message.id)}">${icon("check")}Valider la leçon</button><span class="card-note">Kenobi juge la compétence acquise : à toi de confirmer.</span></div>`;
  } else if (acquired && lesson?.status === "done") {
    actions = `<div class="card-actions"><span class="card-done">${icon("check")}Leçon validée</span>${next && next.id !== lesson.id ? `<button class="button primary" type="button" data-action="start-lesson" data-lesson-id="${e(next.id)}">Leçon suivante : ${e(next.title)}${icon("arrow")}</button>` : ""}</div>`;
  } else if (!acquired) {
    actions = `<p class="card-note">Bilan enregistré dans la mémoire du parcours. La leçon reste ouverte.</p>`;
  }
  return `
    <section class="bilan-card ${acquired ? "is-acquired" : "is-consolidate"}" aria-label="Bilan de la leçon">
      <div class="card-head">${icon("flag")}<span>Bilan de la leçon</span><span class="bilan-status">${acquired ? "Compétence acquise" : "À consolider"}</span></div>
      ${(fields.preuve || []).length ? `<p class="bilan-proof">${C.inlineMarkdown(fields.preuve.join(" "))}</p>` : ""}
      <dl class="bilan-grid">${row("Acquis", fields.acquis)}${row("À revoir", fields.a_revoir)}${row("Révisions", revisions)}${fields.prochaine_etape ? row("Ensuite", [fields.prochaine_etape]) : ""}</dl>
      ${actions}
    </section>`;
}

function renderSessions() {
  const sessions = C.sortedSessions(course());
  return `
    <section class="sessions-view" aria-labelledby="sessions-title">
      <header class="view-heading"><div><h1 id="sessions-title">Séances de travail</h1><p>Chaque séance garde son fil, son bilan et sa place dans le cours.</p></div><button class="button primary" type="button" data-action="new-session">${icon("plus")}Nouvelle séance</button></header>
      <div class="session-list">${sessions.length ? sessions.map((session) => {
        const lesson = C.getLesson(course(), session.lessonId);
        const label = session.status === "active" ? "En cours" : session.validatedAt ? "Validée" : "Terminée";
        return `<article class="session-row"><div><div class="session-title-line"><h2>${e(session.title)}</h2><span class="session-state ${session.status === "active" ? "" : session.validatedAt ? "validated" : "completed"}">${label}</span></div><p class="session-summary">${e(session.summary)}</p><div class="session-meta"><span>${C.formatDate(session.startedAt)}</span><span>${e(lesson?.moduleTitle || C.courseShort(course()))}</span><span>${plural(session.messages.length, "message")}</span></div></div><button class="button secondary session-open" type="button" data-action="open-session" data-session-id="${e(session.id)}">Ouvrir${icon("arrow")}</button></article>`;
      }).join("") : `<p class="context-empty">Aucune séance pour l’instant.</p>`}</div>
    </section>`;
}

function renderMemory() {
  const body = memoryText.replace(/^---\n[\s\S]*?\n---\n/, "");
  return `
    <section class="memory-view" aria-labelledby="memory-title">
      <header class="view-heading"><div><h1 id="memory-title">Mémoire du cours</h1><p>Ce que Kenobi relit à chaque message : acquis, points fragiles, révisions, ce qu’il a retenu de toi et le fil des séances.</p></div><div class="memory-toolbar"><button class="button secondary" type="button" data-action="copy-memory" ${memoryText ? "" : "disabled"}>${icon("copy")}Copier</button><a class="button primary" href="api/memory?courseId=${encodeURIComponent(course().id)}&download=1" download>${icon("download")}Exporter</a></div></header>
      <div class="markdown-preview">${memoryText ? `<div class="memory-rendered">${C.renderMarkdown(body)}</div>` : `<div class="loading-state"><span class="loading-line"></span><p>Lecture du document Markdown…</p></div>`}</div>
      <p class="memory-explanation">${icon("info")}<span>Fichier <code>data/runtime/${e(course().id)}/MEMORY.md</code>, régénéré à chaque enregistrement. Les bilans de Kenobi le mettent à jour.</span></p>
    </section>`;
}

async function loadMemory() {
  const requestId = ++memoryRequest;
  const courseId = course().id;
  try {
    await saveQueue;
    const response = await fetch(`api/memory?courseId=${encodeURIComponent(courseId)}`);
    if (!response.ok) throw new Error("Le fichier de mémoire n’est pas encore disponible.");
    const text = await response.text();
    if (requestId !== memoryRequest) return;
    memoryText = text;
    if (currentView === "memory") render();
  } catch (error) {
    if (requestId !== memoryRequest) return;
    memoryText = "";
    showToast(error.message);
  }
}

function openChat(session) {
  if (callUi?.active() && session.id !== callSessionId) callUi.hangup("Appel terminé : tu as changé de séance.");
  course().memory.lastSessionId = session.id;
  activeSessionId = session.id;
  scrollChatAfterRender = true;
  setView("chat");
}

function startLesson(lessonId) {
  const currentCourse = course();
  let session = C.sortedSessions(currentCourse).find((item) => item.status === "active" && item.lessonId === lessonId);
  const created = !session;
  if (!session) {
    currentCourse.sessions.forEach((item) => { if (item.status === "active") item.status = "completed"; });
    session = C.createSession(currentCourse, lessonId);
    currentCourse.sessions.push(session);
  }
  openChat(session);
  if (created) persist();
}

function resumeCourse() {
  const currentCourse = course();
  const next = C.getProgress(currentCourse).current;
  if (!next) return setView("sessions");
  const session = C.getResumeSession(currentCourse, next.id);
  if (session) openChat(session);
  else startLesson(next.id);
}

function openSession(sessionId) {
  const session = course().sessions.find((item) => item.id === sessionId);
  if (session) openChat(session);
}

function newSession() {
  const next = C.getProgress(course()).current;
  if (!next) return showToast("Toutes les leçons sont validées. Ouvre une leçon du plan pour réviser.");
  const currentCourse = course();
  currentCourse.sessions.forEach((item) => { if (item.status === "active") item.status = "completed"; });
  const session = C.createSession(currentCourse, next.id);
  currentCourse.sessions.push(session);
  openChat(session);
  persist();
}

function toggleLesson(lessonId) {
  const currentCourse = course();
  for (const module of currentCourse.modules) {
    const target = module.lessons.find((item) => item.id === lessonId);
    if (target) target.status = target.status === "done" ? "todo" : "done";
  }
  C.normalizeCourseProgress(currentCourse);
  render();
  persist();
  showToast("Plan du cours mis à jour.");
}

// ── Cours ───────────────────────────────────────────────────────────────────

function leaveCourse() {
  if (callUi?.active()) callUi.hangup("Appel terminé : tu as changé de cours.");
  activeSessionId = null;
  pendingRun = null;
  queuedRun = null;
  memoryText = "";
}

function switchCourse(courseId) {
  if (!state.courses.some((item) => item.id === courseId)) return;
  if (state.activeCourseId !== courseId) {
    leaveCourse();
    state.activeCourseId = courseId;
    persist({ silent: true });
  }
  setView("overview");
}

// A course imported from a Markdown document: it joins the state with its own memory, and opens.
function addCourse(imported) {
  if (!imported?.id || state.courses.some((item) => item.id === imported.id)) return showToast("Ce cours existe déjà.");
  leaveCourse();
  state.courses.push(imported);
  state.activeCourseId = imported.id;
  persist();
  setView("overview");
  const lessons = C.getProgress(imported).total;
  showToast(`Cours ajouté : ${plural(imported.modules.length, "module")}, ${plural(lessons, "leçon")}.`);
}

async function deleteCourse(courseId) {
  const target = state.courses.find((item) => item.id === courseId);
  if (!target || isBusy()) return;
  const sessions = target.sessions.length;
  const confirmed = await settingsUi?.confirm({
    title: `Supprimer « ${C.courseShort(target)} » ?`,
    text: `Le plan, ${sessions ? `${plural(sessions, "séance")} et ` : ""}la mémoire de ce cours quittent Kenobi. Une copie part dans la corbeille du dossier de données (data/runtime/corbeille) ; tes fichiers de code restent sur ton ordinateur.`,
    confirmLabel: "Supprimer le cours",
    danger: true
  });
  if (!confirmed) return;
  leaveCourse();
  removedCourses.add(courseId);
  state.courses = state.courses.filter((item) => item.id !== courseId);
  state.activeCourseId = state.courses[0]?.id || "";
  await persist();
  setView("overview");
  showToast("Cours supprimé. Une copie est dans la corbeille des données.");
}

// ── Conversation ────────────────────────────────────────────────────────────

function isNearBottom() {
  return mainContent.scrollHeight - mainContent.scrollTop - mainContent.clientHeight < 160;
}

function pinChat() {
  mainContent.scrollTop = mainContent.scrollHeight;
}

// Whatever changes the height of the conversation or of its frame (a reply growing, buttons appearing, the
// terminal opening, the keyboard of a phone) keeps a conversation read at its bottom at its bottom.
const chatSize = typeof ResizeObserver === "function"
  ? new ResizeObserver(() => { if (currentView === "chat" && chatPinned) pinChat(); })
  : null;

function watchChatSize() {
  if (!chatSize) return;
  chatSize.disconnect();
  chatSize.observe(mainContent);
  const view = mainContent.querySelector(".chat-view");
  if (view) chatSize.observe(view);
}

// Only the learner's own scrolling unpins the conversation (or pins it again at the bottom).
mainContent.addEventListener("scroll", () => {
  if (currentView === "chat") chatPinned = isNearBottom();
}, { passive: true });

function updateMessageNode(message) {
  const node = mainContent.querySelector(`[data-message-id="${CSS.escape(message.id)}"]`);
  if (!node) return;
  const stick = chatPinned || isNearBottom();
  node.outerHTML = renderMessage(message, course()?.sessions.find((item) => item.messages.includes(message)));
  if (stick) pinChat();
}

function scheduleMessageUpdate(message) {
  if (pendingFrame) return;
  pendingFrame = requestAnimationFrame(() => {
    pendingFrame = 0;
    updateMessageNode(message);
  });
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

async function streamReply(session, reply, text, controller) {
  const response = await fetch("api/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sessionId: session.id, message: text, terminal: terminalWatch() }),
    signal: controller.signal
  });
  if (!response.ok || !response.body) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.error || `Erreur HTTP ${response.status}`);
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
        reply.content += event.data.text || "";
        reply.status = "";
      }
      else if (event.type === "done") {
        Object.assign(reply, { elapsed: event.data.elapsed, firstToken: event.data.firstToken, mode: event.data.mode });
        finished = true;
      } else if (event.type === "error") throw new Error(event.data.message || "Kenobi a rencontré une erreur.");
      scheduleMessageUpdate(reply);
    }
  }
  if (!finished) throw new Error("La réponse de Kenobi a été coupée.");
  if (!reply.content.trim()) throw new Error("Kenobi a renvoyé une réponse vide.");
}

// Interface blocks from Kenobi: lesson contract, bilan (memory), reviews done and facts to remember.
function applyReplyBlocks(message, session) {
  if (message.blocksApplied) return [];
  const currentCourse = course();
  const notes = [];
  for (const block of C.extractKenobiBlocks(message.content).blocks) {
    if (block.type === "lecon" && block.fields.objectif) {
      session.contract = { objective: block.fields.objectif, criteria: block.fields.critere || [] };
    } else if (block.type === "bilan") {
      C.applyBilan(currentCourse, session, block.fields, { validate: false });
      notes.push(C.isValidatedStatus(block.fields.statut) ? "Compétence acquise selon Kenobi : valide la leçon pour continuer." : "Bilan enregistré dans la mémoire du cours.");
    } else if (block.type === "revision") {
      for (const line of block.fields.revu || []) C.recordReview(currentCourse.memory, line);
      notes.push("Révision enregistrée.");
    } else if (block.type === "memoire") {
      if (C.rememberFacts(currentCourse.memory, block.fields).length) notes.push("Noté dans la mémoire du cours.");
    }
  }
  message.blocksApplied = true;
  return notes;
}

// What Kenobi sees of the terminal: coach or silent while it is open, nothing when it is closed.
function terminalWatch() {
  return terminal?.watchState?.() || "closed";
}

// What would leave with the next message: the last run not yet seen and the editor file if it changed.
function messageAttachments(session) {
  if (!terminal || !session || !["coach", "silent"].includes(terminalWatch())) return {};
  const result = {};
  if (pendingRun && pendingRun.lesson === session.lessonId && Date.now() - pendingRun.at < ATTACH_MAX_AGE) result.run = pendingRun;
  const current = terminal.currentFile();
  if (current && current.lesson === session.lessonId && current.code.trim()
    && current.code !== sharedCode.get(session.id) && current.code !== droppedCode && current.code !== result.run?.code) {
    result.file = current;
  }
  return result;
}

function sessionDrafts(session) {
  return session && draftSessionId === session.id ? draftFiles : [];
}

function renderAttachments(session) {
  const { run, file } = messageAttachments(session);
  const chips = sessionDrafts(session).map((item) => `<span class="attachment-chip is-joined">${icon("file")}<span><strong>${e(item.name)}</strong> · ${e(plural(item.lines, "ligne"))}</span><button class="chip-remove" type="button" data-action="drop-attachment" data-file-id="${e(item.id)}" aria-label="Retirer ${e(item.name)}">${icon("close")}</button></span>`);
  if (run) {
    const summary = C.summarizeRun(run);
    chips.push(`<span class="attachment-chip ${summary.status === "error" ? "is-error" : ""}">${icon("terminal")}<span>Dernière exécution · ${e(C.runLabel(summary, run.exit))}</span><button type="button" data-action="send-run">Envoyer seule</button><button class="chip-remove" type="button" data-action="drop-run" aria-label="Ne pas joindre cette exécution">${icon("close")}</button></span>`);
  }
  if (file) {
    chips.push(`<span class="attachment-chip">${icon("file")}<span>${e(file.file)} sera joint</span><button class="chip-remove" type="button" data-action="drop-file" aria-label="Ne pas joindre ce fichier">${icon("close")}</button></span>`);
  }
  return chips.join("");
}

function updateAttachments() {
  const slot = document.getElementById("composer-attachments");
  if (slot) slot.innerHTML = renderAttachments(currentView === "chat" ? activeSession() : null);
}

// Files from the learner's computer, read here and sent with the next message (text files only, within the limits).
async function addFiles(list) {
  const session = currentView === "chat" ? activeSession() : null;
  if (!session) return showToast("Ouvre une séance pour joindre un fichier.");
  if (draftSessionId !== session.id) {
    draftFiles = [];
    draftSessionId = session.id;
  }
  const problems = [];
  for (const item of Array.from(list || [])) {
    const name = C.cleanFileName(item.name);
    const others = draftFiles.filter((draft) => draft.name !== name);
    const problem = C.attachmentProblem({ name, size: item.size }, others);
    if (problem) {
      problems.push(problem);
      continue;
    }
    let text;
    try {
      text = await item.text();
    } catch {
      problems.push(`${name} : lecture impossible.`);
      continue;
    }
    const result = C.readAttachment(name, text, others);
    if (result.error) problems.push(result.error);
    else draftFiles = [...others, { id: newId("file"), ...result.file }];
  }
  updateAttachments();
  if (problems.length) showToast(problems.join(" "));
  document.getElementById("message-input")?.focus({ preventScroll: true });
}

function dropDraft(fileId) {
  draftFiles = draftFiles.filter((item) => item.id !== fileId);
  updateAttachments();
}

// The drawer was closed with « Je passe sur mon terminal » : Kenobi hears it and answers in the conversation.
function handleOwnTerminal() {
  const session = currentView === "chat" ? activeSession() : null;
  if (!session || isBusy()) {
    showToast("Terminal désactivé : Kenobi ne regarde plus. Envoie-lui ton code et ce qu’il affiche ici, ou joins tes fichiers.");
    return;
  }
  sendMessage(OWN_TERMINAL_MESSAGE, { attach: false, notice: "own-terminal" });
}

function handleTerminalActive(active) {
  if (!active) {
    pendingRun = null;
    queuedRun = null;
  }
  updateAttachments();
}

// Called by the terminal for every command of the course. Coach: while an instruction is open, each run is
// sent and gets a verdict (queued if Kenobi is still answering); otherwise errors are sent, the rest waits.
function handleRun(run) {
  const session = currentView === "chat" ? activeSession() : null;
  const task = session ? C.openTask(session) : null;
  const decision = C.runDelivery(run, {
    mode: terminal.mode(),
    busy: isBusy(),
    hasSession: Boolean(session),
    task,
    fix: session ? C.awaitingFix(session) : false,
    language: C.courseTools(course()).language
  });
  if (decision.action === "ignore") return "Kenobi ne regarde pas";
  if (decision.action === "send") {
    sendRun(run, decision.reason, task);
    return "envoyé à Kenobi";
  }
  if (decision.action === "queue") {
    queuedRun = { run, sessionId: session.id };
    if (pendingRun) {
      pendingRun = null;
      updateAttachments();
    }
    return "Kenobi répond dès qu’il a fini";
  }
  pendingRun = run;
  updateAttachments();
  return session ? "joint à ton prochain message" : "visible dès que tu écris dans une séance";
}

function replyVerdict(reply) {
  if (!reply || reply.error) return "";
  return C.extractKenobiBlocks(reply.content).blocks.map((block) => (block.type === "verdict" ? C.verdictOf(block.fields) : null)).find(Boolean) || "";
}

async function sendRun(run, reason, task) {
  if (pendingRun?.id === run.id) pendingRun = null;
  const attempt = reason === "exercise" && task ? task.attempts + 1 : 0;
  const record = C.runRecord(run);
  if (attempt) record.attempt = attempt;
  const text = C.formatRunForKenobi(run, { reason, attempt, expected: task?.expected || "" });
  const extra = { attach: false, background: true, run: record, auto: reason !== "manual", reason, code: run.code };
  // During a call Kenobi answers a run aloud; otherwise in the conversation.
  const reply = callUi?.active() && activeSessionId === callSessionId ? await callUi.say(text, extra) : await sendMessage(text, extra);
  if (!reply) return;
  const verdict = replyVerdict(reply);
  const label = reply.error ? "Kenobi n’a pas pu répondre" : verdict ? `Kenobi : ${C.VERDICTS[verdict].label.toLowerCase()}` : reply.stopped ? "réponse interrompue" : "Kenobi a répondu";
  terminal?.setDelivery(run.id, label, { reply: !reply.error, verdict });
}

// « Vérifier » on an instruction done on the command line: the terminal screen is the attempt.
async function checkTask() {
  const session = currentView === "chat" ? activeSession() : null;
  const task = session ? C.openTask(session) : null;
  if (!session || !task || isBusy()) return;
  if (!terminal?.ready()) return showToast("Ouvre d’abord le terminal : Kenobi vérifie ce qui s’y affiche.");
  const snapshot = terminal.screen();
  if (!snapshot.text.trim()) return showToast("L’écran du terminal est vide : fais d’abord la consigne.");
  const attempt = task.attempts + 1;
  const text = C.formatCheckForKenobi(snapshot.text, { attempt, expected: task.expected });
  const extra = { attach: false, background: true, attempt, reason: "exercise", text: "" };
  if (callUi?.active() && activeSessionId === callSessionId) await callUi.say(text, extra);
  else await sendMessage(text, extra);
}

// The run that arrived while Kenobi was answering: sent as soon as he is done, if the instruction is still open.
function flushQueuedRun() {
  if (!queuedRun || isBusy()) return;
  const { run, sessionId } = queuedRun;
  queuedRun = null;
  const session = currentView === "chat" ? activeSession() : null;
  const task = session && session.id === sessionId ? C.openTask(session) : null;
  if (task) sendRun(run, "exercise", task);
  else {
    pendingRun = run;
    updateAttachments();
  }
}

function handleShow(snapshot) {
  const session = currentView === "chat" ? activeSession() : null;
  if (!session) return showToast("Ouvre une séance pour montrer ton terminal à Kenobi.");
  if (isBusy()) return showToast("Kenobi répond encore : réessaie dans un instant.");
  sendMessage(C.formatTerminalSnapshot(snapshot.text, { selection: snapshot.selection }), { attach: false, background: true });
}

// From the terminal bar: bring Kenobi's latest answer into view, with a short highlight.
function showLatestReply() {
  if (currentView !== "chat") setView("chat");
  requestAnimationFrame(() => {
    const node = [...mainContent.querySelectorAll(".message.is-tutor")].pop();
    if (!node) return;
    node.scrollIntoView({ behavior: reducedMotion.matches ? "auto" : "smooth", block: "nearest" });
    node.classList.remove("is-flash");
    void node.offsetWidth;
    node.classList.add("is-flash");
  });
}

// Sends a message to Kenobi and resolves with his reply (null when nothing left).
async function sendMessage(override, extra = {}) {
  if (isBusy()) return null;
  const input = document.getElementById("message-input");
  const fromComposer = override === undefined;
  const typed = String(fromComposer ? input?.value || "" : override).trim();
  const currentCourse = course();
  const session = activeSession();
  if (!session) return null;
  const joined = extra.attach === false ? [] : sessionDrafts(session);
  if (!typed && !joined.length) return null;
  let text = typed;
  const stored = {};
  for (const key of ["run", "auto", "reason", "text", "file", "files", "notice", "voice", "attempt", "qcm"]) if (extra[key] !== undefined) stored[key] = extra[key];
  let shared = extra.code;
  const parts = joined.map(C.formatAttachedFile);
  if (joined.length) {
    stored.files = joined.map(({ name, lines, chars }) => ({ name, lines, chars }));
    draftFiles = [];
  }
  if (extra.attach !== false && typed.length < 10500) {
    const { run, file } = messageAttachments(session);
    const terminalParts = [];
    if (run) {
      terminalParts.push(C.formatRunForKenobi(run, { reason: "attached", budget: 11500 - typed.length - (file ? 3200 : 0) }));
      stored.run = C.runRecord(run);
      shared = run.code || shared;
      pendingRun = null;
    }
    if (file) {
      terminalParts.push(C.formatFileForKenobi(file, { budget: 11500 - typed.length - terminalParts.join("").length }));
      stored.file = file.file;
      shared = file.code;
    }
    parts.push(...terminalParts);
  }
  if (parts.length) {
    stored.text = typed;
    text = [typed, ...parts].filter(Boolean).join("\n\n");
  }
  if (shared) sharedCode.set(session.id, shared);
  const now = new Date().toISOString();
  session.messages.push({ id: newId("message"), role: "user", createdAt: now, content: text, ...stored });
  session.updatedAt = now;
  currentCourse.memory.lastSessionId = session.id;
  if (fromComposer && input) {
    input.value = "";
    input.style.height = "";
  }
  scrollChatAfterRender = true;

  if (health && !health.agent?.configured) {
    render();
    await persist();
    showToast("Message gardé. Branche un modèle d’IA dans les réglages pour que Kenobi réponde.");
    return null;
  }

  const reply = { id: newId("message"), role: "tutor", createdAt: new Date().toISOString(), content: "", pending: true };
  session.messages.push(reply);
  const controller = new AbortController();
  streaming = { controller, messageId: reply.id };
  render();
  try {
    await persist();
    await streamReply(session, reply, text, controller);
  } catch (error) {
    if (controller.signal.aborted) {
      reply.content = reply.content || "_Réponse interrompue._";
      reply.stopped = true;
    } else {
      reply.error = true;
      reply.content = `Kenobi n’a pas pu répondre : ${error.message}`;
      showToast(error.message);
    }
  } finally {
    reply.pending = false;
    delete reply.status;
    streaming = null;
  }
  const notes = reply.error ? [] : applyReplyBlocks(reply, session);
  session.updatedAt = new Date().toISOString();
  render();
  await persist();
  if (notes.length) showToast(notes.join(" "));
  // A run sent from the terminal leaves the focus where the learner is working.
  if (!extra.background) requestAnimationFrame(() => document.getElementById("message-input")?.focus({ preventScroll: true }));
  if (reply.error) refreshHealth();
  flushQueuedRun();
  return reply;
}

// ── Appel vocal : chaque tour entre dans la discussion ──────────────────────

function callSession() {
  for (const item of state?.courses || []) {
    const found = item.sessions.find((session) => session.id === callSessionId);
    if (found) return found;
  }
  return null;
}

function openCall() {
  if (!callUi) return showToast("L’appel n’est pas disponible dans ce navigateur.");
  if (!course()) return showToast("Ajoute d’abord un cours.");
  if (!callUi.active()) {
    if (currentView !== "chat" || !activeSession()) resumeCourse();
    if (!activeSession()) return showToast("Ouvre une leçon pour appeler Kenobi.");
    callSessionId = activeSessionId;
  }
  callUi.open();
}

// What was heard (or a run sent during the call) becomes a message; Kenobi's answer fills in as he speaks.
function openVoiceTurn({ text, spoken, extra = {} }) {
  const session = callSession();
  if (!session) return null;
  const now = new Date().toISOString();
  const stored = {};
  for (const key of ["run", "auto", "reason", "attempt", "text", "qcm"]) if (extra[key] !== undefined) stored[key] = extra[key];
  const user = { id: newId("message"), role: "user", createdAt: now, content: spoken ? `[Appel vocal] ${text}` : text, ...(spoken ? { text, voice: true } : {}), ...stored };
  const reply = { id: newId("message"), role: "tutor", createdAt: now, content: "", pending: true, voice: true };
  if (extra.code) sharedCode.set(session.id, extra.code);
  session.messages.push(user, reply);
  session.updatedAt = now;
  voiceTurn = { session, user, reply };
  scrollChatAfterRender = true;
  render();
  return voiceTurn;
}

function updateVoiceTurn(handle, content) {
  handle.reply.content = content;
  scheduleMessageUpdate(handle.reply);
}

function closeVoiceTurn(handle, { content = "", meta = {}, stopped = false, error = "", dropped = false } = {}) {
  const { session, user, reply } = handle;
  if (voiceTurn === handle) voiceTurn = null;
  if (dropped) {
    // Replaced before any answer: its words open the next turn, the conversation keeps a single message.
    session.messages = session.messages.filter((message) => message !== user && message !== reply);
    render();
    return null;
  }
  reply.pending = false;
  if (error) {
    reply.error = true;
    reply.content = `Kenobi n’a pas pu répondre : ${error}`;
  } else {
    reply.content = content || reply.content || "_Réponse interrompue._";
    if (stopped) reply.stopped = true;
    if (meta.elapsed) Object.assign(reply, { elapsed: meta.elapsed, firstToken: meta.firstToken });
  }
  const notes = reply.error ? [] : applyReplyBlocks(reply, session);
  session.updatedAt = new Date().toISOString();
  render();
  persist();
  if (notes.length) showToast(notes.join(" "));
  flushQueuedRun();
  return reply;
}

function retryMessage(messageId) {
  const session = activeSession();
  if (!session || isBusy()) return;
  const index = session.messages.findIndex((item) => item.id === messageId);
  const previous = session.messages[index - 1];
  if (index < 1 || C.normalizeRole(previous.role) !== "user") return;
  session.messages.splice(index - 1, 2);
  sendMessage(previous.content, {
    attach: false, run: previous.run, auto: previous.auto, reason: previous.reason, text: previous.text,
    file: previous.file, files: previous.files, notice: previous.notice, qcm: previous.qcm,
    ...(previous.voice ? { text: previous.text, voice: true } : {})
  });
}

// A one-click answer under Kenobi's last message (« C'est parti ! », kenobi-boutons), sent as if typed.
// During the call, Kenobi answers it aloud.
function sendQuickReply(label) {
  if (!label || !activeSession() || isBusy()) return;
  if (callUi?.active() && activeSessionId === callSessionId) callUi.say(label, {});
  else sendMessage(label, { attach: false });
}

// A click on a QCM option: the card judges at once (the answer is in the block), then Kenobi carries on.
function answerQuiz(messageId, index) {
  const session = activeSession();
  if (!session || isBusy() || C.quizAnswer(session, messageId) || C.latestQuiz(session) !== messageId) return;
  const quiz = C.messageQuiz(session.messages.find((item) => item.id === messageId));
  const option = quiz?.options[index];
  if (!option) return;
  const text = C.formatQuizAnswer(quiz, option.text);
  const extra = { attach: false, qcm: { messageId, choice: option.text, correct: quiz.judged ? option.correct : null } };
  if (callUi?.active() && activeSessionId === callSessionId) callUi.say(text, extra);
  else sendMessage(text, extra);
}

function validateLesson() {
  const currentCourse = course();
  const session = activeSession();
  if (!session) return;
  const result = C.completeLesson(currentCourse, session);
  currentCourse.memory.lastSessionId = session.id;
  render();
  persist();
  showToast(result.next ? `Leçon validée. Prochaine : « ${result.next.title} ».` : "Leçon validée : cours terminé.");
}

function insertBlock(language, placeholder) {
  const input = document.getElementById("message-input");
  if (!input) return;
  const start = input.selectionStart;
  const end = input.selectionEnd;
  const selected = input.value.slice(start, end).replace(/\n$/, "");
  const before = input.value.slice(0, start);
  const prefix = before && !before.endsWith("\n") ? "\n" : "";
  const opening = `${prefix}\`\`\`${language}\n`;
  const block = `${opening}${selected || placeholder}\n\`\`\`\n`;
  input.value = before + block + input.value.slice(end);
  input.focus({ preventScroll: true });
  const cursor = start + opening.length;
  input.setSelectionRange(cursor, cursor + (selected || placeholder).length);
  autoGrow(input);
}

function autoGrow(input) {
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 260)}px`;
}

async function copyText(text, button) {
  try {
    if (navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(text);
    else {
      const area = document.createElement("textarea");
      area.value = text;
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      const copied = document.execCommand("copy");
      area.remove();
      if (!copied) throw new Error("Copie refusée par le navigateur");
    }
    const label = button?.querySelector("span");
    if (label) {
      const previous = label.textContent;
      label.textContent = "Copié";
      setTimeout(() => { label.textContent = previous; }, 1600);
    }
  } catch (error) {
    showToast(`Impossible de copier automatiquement : ${error.message}`);
  }
}

// ── Événements ──────────────────────────────────────────────────────────────

async function handleAction(button) {
  const action = button.dataset.action;
  if (action === "go-overview") setView("overview");
  else if (action === "switch-course") switchCourse(button.dataset.courseId);
  else if (action === "import-course") settingsUi?.openImport();
  else if (action === "delete-course") deleteCourse(button.dataset.courseId);
  else if (action === "open-settings") settingsUi?.open(button.dataset.tab);
  else if (action === "toggle-sidebar") {
    layout.sidebar = layout.sidebar === "rail" ? "open" : "rail";
    applyLayout({ store: true });
  } else if (action === "toggle-context") {
    if (narrowScreen.matches) {
      closePanels();
      document.body.classList.add("context-open");
      syncBackdrop();
    } else {
      layout.context = !layout.context;
      applyLayout({ store: true });
    }
  } else if (action === "resume-course") resumeCourse();
  else if (action === "start-lesson") startLesson(button.dataset.lessonId);
  else if (action === "open-session") openSession(button.dataset.sessionId);
  else if (action === "new-session") newSession();
  else if (action === "toggle-lesson") toggleLesson(button.dataset.lessonId);
  else if (action === "start-session") {
    const lesson = C.getLesson(course(), activeSession()?.lessonId);
    sendMessage(`Commençons la leçon « ${lesson?.title || activeSession()?.title || ""} ».`);
  } else if (action === "quick") {
    const quick = QUICK_ACTIONS.find((item) => item.id === button.dataset.quick);
    if (quick) sendMessage(quick.prompt);
  } else if (action === "quick-reply") sendQuickReply(button.dataset.label);
  else if (action === "quiz-answer") answerQuiz(button.dataset.messageId, Number(button.dataset.choice));
  else if (action === "validate-lesson") validateLesson();
  else if (action === "retry-message") retryMessage(button.dataset.messageId);
  else if (action === "stop") streaming?.controller.abort();
  else if (action === "insert-code") insertBlock(codeFence(), "ton code");
  else if (action === "insert-output") insertBlock("text", "ce que ton programme affiche");
  else if (action === "copy-code") {
    const code = button.closest(".code-block")?.querySelector("pre code")?.textContent || "";
    await copyText(code, button);
  } else if (action === "code-to-editor") {
    terminal?.loadCode(button.closest(".code-block")?.querySelector("pre code")?.textContent || "");
  } else if (action === "open-editor") terminal?.openFile(button.dataset.file);
  else if (action === "open-terminal") terminal?.open();
  else if (action === "check-task") checkTask();
  else if (action === "goto-line") terminal?.goToLine(button.dataset.file, Number(button.dataset.line), button.dataset.lesson);
  else if (action === "send-run") {
    if (pendingRun && !isBusy()) sendRun(pendingRun, "manual", null);
  } else if (action === "drop-run") {
    pendingRun = null;
    updateAttachments();
  } else if (action === "drop-file") {
    droppedCode = terminal?.currentFile()?.code ?? null;
    updateAttachments();
  } else if (action === "attach-file") document.getElementById("attach-input")?.click();
  else if (action === "drop-attachment") dropDraft(button.dataset.fileId);
  else if (action === "copy-memory") await copyText(memoryText, button);
  else if (action === "suggestion") {
    const input = document.getElementById("message-input");
    if (input) {
      input.value = button.dataset.prompt || "";
      input.focus({ preventScroll: true });
      input.setSelectionRange(input.value.length, input.value.length);
      autoGrow(input);
    }
  } else if (action === "open-sidebar") {
    closePanels();
    document.body.classList.add("sidebar-open");
    syncBackdrop();
  } else if (["close-sidebar", "close-panels"].includes(action)) closePanels();
  else if (action === "start-tour") {
    closePanels();
    window.KenobiTour?.start();
  } else if (action === "open-welcome") {
    closePanels();
    window.KenobiTour?.welcome();
  } else if (action === "hide-start") {
    try { localStorage.setItem(START_KEY, "1"); } catch { /* private mode */ }
    render();
  } else if (action === "open-call") openCall();
  else if (action === "close-call") callUi?.close();
  else if (action === "minimize-call") callUi?.minimize();
  else if (action === "retry") window.location.reload();
}

document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-action], button[data-view]");
  if (!button || button.disabled) return;
  if (button.dataset.view) setView(button.dataset.view);
  else handleAction(button);
});

document.addEventListener("submit", (event) => {
  if (event.target.id !== "message-form") return;
  event.preventDefault();
  sendMessage();
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closePanels();
  if (event.target.id === "message-input" && event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    sendMessage();
  }
});

document.addEventListener("input", (event) => {
  if (event.target.id === "message-input") autoGrow(event.target);
});

document.addEventListener("change", (event) => {
  if (event.target.id !== "attach-input") return;
  addFiles(event.target.files);
  event.target.value = "";
});

// Files copied in the file manager, then pasted in the message box.
document.addEventListener("paste", (event) => {
  if (event.target.id !== "message-input" || !event.clipboardData?.files?.length) return;
  event.preventDefault();
  addFiles(event.clipboardData.files);
});

// Files dropped anywhere on the conversation join the next message; elsewhere the browser must not open them.
function draggingFiles(event) {
  return Array.from(event.dataTransfer?.types || []).includes("Files");
}

function canDropHere(event) {
  return currentView === "chat" && Boolean(activeSession()) && Boolean(event.target.closest?.("#main-content"));
}

function setDropTarget(active) {
  document.getElementById("message-form")?.classList.toggle("is-drop", active);
}

document.addEventListener("dragenter", (event) => {
  if (!draggingFiles(event)) return;
  dragDepth += 1;
  setDropTarget(currentView === "chat" && Boolean(activeSession()));
});

document.addEventListener("dragover", (event) => {
  if (!draggingFiles(event)) return;
  event.preventDefault();
  event.dataTransfer.dropEffect = canDropHere(event) ? "copy" : "none";
});

document.addEventListener("dragleave", (event) => {
  if (!draggingFiles(event)) return;
  dragDepth = Math.max(0, dragDepth - 1);
  if (!dragDepth) setDropTarget(false);
});

document.addEventListener("drop", (event) => {
  if (!draggingFiles(event)) return;
  event.preventDefault();
  dragDepth = 0;
  setDropTarget(false);
  if (canDropHere(event)) addFiles(event.dataTransfer.files);
});

// ── Démarrage et état de Kenobi ─────────────────────────────────────────────

function shortModel(model) {
  const name = String(model || "").split("/").pop();
  return name.length > 26 ? `${name.slice(0, 25)}…` : name;
}

function updateAgentIndicator() {
  const info = health?.agent;
  const label = document.getElementById("model-button-label");
  const button = document.getElementById("model-button");
  const statusText = document.getElementById("agent-status-text");
  const statusBadge = document.getElementById("agent-status-badge");
  const statusBox = document.getElementById("agent-status");
  if (!label || !button) return;
  const configured = Boolean(info?.configured);
  const trouble = configured && Boolean(info.problem);
  label.textContent = configured ? shortModel(info.model) : "Brancher un modèle";
  button.classList.toggle("is-online", configured && !trouble);
  button.classList.toggle("is-degraded", trouble);
  button.classList.toggle("is-offline", !configured);
  button.title = configured ? `${info.model} · ${info.provider}${trouble ? ` — ${info.problem}` : ""}` : "Aucun modèle d’IA branché : ouvrir les réglages";
  if (statusText) statusText.textContent = configured ? `${shortModel(info.model)} · ${info.provider}` : "Aucun modèle branché";
  if (statusBox) statusBox.title = button.title;
  if (statusBadge) statusBadge.textContent = configured ? (trouble ? "Erreur" : "Prêt") : "À régler";
  if (statusBox) {
    statusBox.classList.toggle("is-online", configured && !trouble);
    statusBox.classList.toggle("is-trouble", trouble || !configured);
  }
}

async function refreshHealth() {
  const before = Boolean(health?.agent?.configured);
  try {
    health = await requestJson("api/health");
  } catch {
    health = { agent: { configured: false, detail: "Le serveur local ne répond pas." } };
  }
  updateAgentIndicator();
  return before !== Boolean(health.agent?.configured);
}

// Settings saved: the name, the model and the voice may have changed.
async function handleSettingsSaved(info) {
  settingsInfo = info;
  await refreshHealth();
  render();
  callUi?.refresh();
}

async function init() {
  applyLayout();
  try {
    [state, health, settingsInfo] = await Promise.all([
      fetchState(),
      requestJson("api/health"),
      requestJson("api/settings").catch(() => null)
    ]);
    for (const item of state.courses) for (const session of item.sessions) session.messages = session.messages.filter((message) => !message.pending);
    saveStatus.textContent = "Enregistré localement";
    saveStatus.className = "save-status";
    updateAgentIndicator();
    settingsUi?.init({ onSaved: handleSettingsSaved, onCourse: addCourse, toast: showToast });
    callUi?.init({
      sessionId: () => callSessionId,
      beforeTurn: () => saveQueue,
      terminal: terminalWatch,
      openTurn: openVoiceTurn,
      delta: updateVoiceTurn,
      closeTurn: closeVoiceTurn,
      toast: showToast,
      openSettings: (tab) => settingsUi?.open(tab)
    });
    terminal?.init({
      lesson: currentLesson(),
      onRun: handleRun,
      onShow: handleShow,
      toast: showToast,
      onEditorChange: updateAttachments,
      onModeChange: updateAttachments,
      onShowReply: showLatestReply,
      onActiveChange: handleTerminalActive,
      onOwnTerminal: handleOwnTerminal
    });
    render();
    persist({ silent: true });
    setInterval(async () => { if (await refreshHealth()) render(); }, 60000);
    setInterval(checkRemoteState, 5000);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) checkRemoteState(); });
    window.addEventListener("focus", checkRemoteState);
    window.addEventListener("kenobi:toast", (event) => showToast(String(event.detail || "")));
    // The voice bridge opens the web version with #voix=<port>-<token>: saved like a pasted code, then forgotten.
    const voicePairing = /^#voix=(\d{2,5}-[A-Za-z0-9_-]{16,128})$/.exec(window.location.hash);
    if (voicePairing && window.KENOBI_BACKEND === "browser") {
      history.replaceState(null, "", window.location.pathname + window.location.search);
      settingsUi?.pairVoice(voicePairing[1])
        .then(() => { showToast("Pont vocal branché : tu peux appeler Kenobi."); callUi?.refresh(); })
        .catch((error) => showToast(error.message));
    }
    window.KenobiTour?.init({
      mode: () => health?.mode || settingsInfo?.mode || "server",
      connected: () => Boolean(health?.agent?.configured),
      openSettings: (tab) => settingsUi?.open(tab),
      beforeStart: () => {
        closePanels();
        if (narrowScreen.matches) return;
        if (layout.sidebar === "rail") {
          layout.sidebar = "open";
          applyLayout({ store: true });
        }
      }
    });
    window.KenobiTour?.maybeStart();
  } catch (error) {
    saveStatus.textContent = "Serveur indisponible";
    const hint = window.KENOBI_BACKEND === "browser"
      ? "Recharge la page. Si le problème revient, ton navigateur refuse peut-être le stockage local (navigation privée, réglages de confidentialité)."
      : "Vérifie que le serveur tourne (<code>python3 server.py</code>), puis réessaie.";
    mainContent.innerHTML = `<section class="error-state"><h1>Impossible d’ouvrir Kenobi</h1><p>${e(error.message)}. ${hint}</p><button class="button primary" type="button" data-action="retry">Réessayer</button></section>`;
  }
}

init();
