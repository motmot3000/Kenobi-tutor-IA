(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.KenobiCore = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const PYTHON_KEYWORDS = new Set("and as assert async await break class continue def del elif else except False finally for from global if import in is lambda None nonlocal not or pass raise return True try while with yield".split(" "));
  const PYTHON_BUILTINS = new Set("print len range str int float bool list dict set tuple input open sum min max enumerate zip type isinstance sorted abs round".split(" "));
  const TUTOR_ROLES = new Set(["tutor", "assistant", "kenobi"]);
  const LANGUAGE_LABELS = {
    python: "Python", py: "Python", python3: "Python",
    text: "Sortie", txt: "Sortie", output: "Sortie", sortie: "Sortie", console: "Console", pycon: "Console",
    bash: "Terminal", sh: "Terminal", shell: "Terminal", zsh: "Terminal",
    json: "JSON", csv: "CSV", sql: "SQL", html: "HTML", css: "CSS", javascript: "JavaScript", js: "JavaScript",
    typescript: "TypeScript", markdown: "Markdown", yaml: "YAML", toml: "TOML", ini: "Réglages", xml: "XML",
    powershell: "PowerShell", texte: "Texte"
  };
  const OUTPUT_LANGUAGES = new Set(["text", "txt", "output", "sortie", "console", "pycon"]);
  const BLOCK_KINDS = {
    lecon: "lecon", "leçon": "lecon", bilan: "bilan", revision: "revision", "révision": "revision",
    exercice: "exercice", exercise: "exercice", verdict: "verdict", memoire: "memoire", "mémoire": "memoire", memory: "memoire",
    pret: "pret", "prêt": "pret", ready: "pret", qcm: "qcm", quiz: "qcm", boutons: "boutons", bouton: "boutons", buttons: "boutons"
  };
  const MULTI_FIELDS = new Set(["critere", "acquis", "a_revoir", "revision", "revu", "preuve", "retenir", "oublier", "choix", "bouton"]);
  const VERDICTS = {
    reussi: { label: "Réussi", tone: "ok" },
    presque: { label: "Presque", tone: "near" },
    "pas-encore": { label: "Pas encore", tone: "miss" }
  };
  const FIELD_ALIASES = {
    criteres: "critere", critere: "critere", criterion: "critere",
    acquis: "acquis", acquise: "acquis",
    a_revoir: "a_revoir", arevoir: "a_revoir", fragile: "a_revoir",
    revision: "revision", revisions: "revision",
    revu: "revu", revue: "revu",
    preuve: "preuve", preuves: "preuve",
    objectif: "objectif", but: "objectif",
    statut: "statut", status: "statut",
    prochaine_etape: "prochaine_etape", suite: "prochaine_etape",
    resume: "resume", journal: "resume",
    fichier: "fichier", file: "fichier",
    attendu: "attendu", expected: "attendu", sortie_attendue: "attendu",
    resultat: "resultat", result: "resultat", verdict: "resultat",
    retenir: "retenir", note: "retenir", retiens: "retenir", oublier: "oublier",
    choix: "choix", option: "choix", options: "choix", proposition: "choix", propositions: "choix",
    reponse: "reponse", bonne_reponse: "reponse", answer: "reponse", correct: "reponse",
    explication: "explication", explanation: "explication", pourquoi: "explication",
    bouton: "bouton", boutons: "bouton", button: "bouton", question: "question"
  };

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (character) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    })[character]);
  }

  function stripAccents(value) {
    return String(value ?? "").normalize("NFD").replace(/[\u0300-\u036f]/g, "");
  }

  function inlineText(value) {
    return escapeHtml(value).replace(/`([^`\n]+)`/g, "<code>$1</code>");
  }

  function textToHtml(value) {
    return String(value ?? "").trim().split(/\n{2,}/).filter(Boolean).map((paragraph) =>
      `<p>${inlineText(paragraph).replace(/\n/g, "<br>")}</p>`
    ).join("");
  }

  // Inline Markdown on escaped text: code spans first, then emphasis and safe links.
  function inlineMarkdown(value) {
    const spans = [];
    let html = escapeHtml(value).replace(/`([^`\n]+)`/g, (_, code) => {
      spans.push(`<code>${code}</code>`);
      return `\u0000${spans.length - 1}\u0000`;
    });
    html = html
      .replace(/\*\*([^*\n](?:[^*\n]|\*(?!\*))*?)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[^\w])__([^_\n]+?)__(?=[^\w]|$)/g, "$1<strong>$2</strong>")
      .replace(/(^|[^*\w])\*([^*\s][^*\n]*?)\*(?=[^*\w]|$)/g, "$1<em>$2</em>")
      .replace(/(^|[^\w])_([^_\s][^_\n]*?)_(?=[^\w]|$)/g, "$1<em>$2</em>")
      .replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
    return html.replace(/\u0000(\d+)\u0000/g, (_, index) => spans[Number(index)]);
  }

  function languageLabel(language) {
    const key = String(language || "").toLowerCase();
    return LANGUAGE_LABELS[key] || (key ? key : "Code");
  }

  function isOutputLanguage(language) {
    return OUTPUT_LANGUAGES.has(String(language || "").toLowerCase());
  }

  function defaultCodeRenderer(content, language) {
    return `<pre><code>${highlightCode(content, language)}</code></pre>`;
  }

  function splitTableRow(line) {
    return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim());
  }

  function isTableStart(lines, index) {
    if (index + 1 >= lines.length) return false;
    return /\|/.test(lines[index]) && /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(lines[index + 1]);
  }

  // Small, safe Markdown renderer for tutor messages (escape first, never trust HTML).
  function renderMarkdown(source, options = {}) {
    const renderCode = options.code || defaultCodeRenderer;
    const lines = String(source ?? "").replace(/\r\n?/g, "\n").split("\n");
    const out = [];
    let paragraph = [];
    const flush = () => {
      if (!paragraph.length) return;
      out.push(`<p>${inlineMarkdown(paragraph.join("\n")).replace(/\n/g, "<br>")}</p>`);
      paragraph = [];
    };
    let index = 0;
    while (index < lines.length) {
      const line = lines[index];
      const fence = line.match(/^\s*(`{3,}|~{3,})\s*([\w+#.-]*)[^\n]*$/);
      if (fence) {
        flush();
        const marker = fence[1];
        const body = [];
        index += 1;
        while (index < lines.length && !lines[index].trim().startsWith(marker)) {
          body.push(lines[index]);
          index += 1;
        }
        index += 1;
        out.push(renderCode(body.join("\n").replace(/^\n+|\n+$/g, ""), fence[2] || ""));
        continue;
      }
      if (!line.trim()) { flush(); index += 1; continue; }
      const heading = line.match(/^\s*(#{1,6})\s+(.+?)\s*#*\s*$/);
      if (heading) {
        flush();
        const level = heading[1].length <= 2 ? 3 : 4;
        out.push(`<h${level}>${inlineMarkdown(heading[2])}</h${level}>`);
        index += 1;
        continue;
      }
      if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) { flush(); out.push("<hr>"); index += 1; continue; }
      if (/^\s*>/.test(line)) {
        flush();
        const quote = [];
        while (index < lines.length && /^\s*>/.test(lines[index])) {
          quote.push(lines[index].replace(/^\s*>\s?/, ""));
          index += 1;
        }
        out.push(`<blockquote>${renderMarkdown(quote.join("\n"), options)}</blockquote>`);
        continue;
      }
      if (isTableStart(lines, index)) {
        flush();
        const head = splitTableRow(lines[index]);
        index += 2;
        const rows = [];
        while (index < lines.length && /\|/.test(lines[index]) && lines[index].trim()) {
          rows.push(splitTableRow(lines[index]));
          index += 1;
        }
        out.push(`<div class="table-wrap"><table><thead><tr>${head.map((cell) => `<th>${inlineMarkdown(cell)}</th>`).join("")}</tr></thead><tbody>${rows.map((row) => `<tr>${head.map((_, column) => `<td>${inlineMarkdown(row[column] || "")}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`);
        continue;
      }
      const item = line.match(/^(\s*)([-*+•]|\d{1,3}[.)])\s+(.*)$/);
      if (item) {
        flush();
        const ordered = /\d/.test(item[2]);
        const start = ordered ? parseInt(item[2], 10) : 1;
        const items = [];
        while (index < lines.length) {
          const current = lines[index].match(/^(\s*)([-*+•]|\d{1,3}[.)])\s+(.*)$/);
          if (current && /\d/.test(current[2]) === ordered) {
            items.push([current[3]]);
            index += 1;
          } else if (items.length && lines[index].trim() && /^\s{2,}\S/.test(lines[index]) && !/^\s*(`{3,}|~{3,})/.test(lines[index])) {
            items[items.length - 1].push(lines[index].trim());
            index += 1;
          } else break;
        }
        const tag = ordered ? "ol" : "ul";
        const startAttribute = ordered && start !== 1 ? ` start="${start}"` : "";
        out.push(`<${tag}${startAttribute}>${items.map((parts) => `<li>${inlineMarkdown(parts.join("\n")).replace(/\n/g, "<br>")}</li>`).join("")}</${tag}>`);
        continue;
      }
      paragraph.push(line);
      index += 1;
    }
    flush();
    return out.join("");
  }

  function parseContent(value) {
    const source = String(value ?? "");
    const blocks = [];
    const fence = /```([a-zA-Z0-9_+-]*)[^\S\r\n]*\r?\n?([\s\S]*?)```/g;
    let cursor = 0;
    let match;
    while ((match = fence.exec(source)) !== null) {
      const before = source.slice(cursor, match.index).trim();
      if (before) blocks.push({ type: "text", content: before });
      blocks.push({ type: "code", language: match[1] || "", content: match[2].replace(/^\n|\n$/g, "") });
      cursor = match.index + match[0].length;
    }
    const rest = source.slice(cursor).trim();
    if (rest) blocks.push({ type: "text", content: rest });
    if (!blocks.length && source.trim()) blocks.push({ type: "text", content: source.trim() });
    return blocks;
  }

  function highlightCode(value, language) {
    const source = String(value ?? "");
    if (!["python", "py", "python3"].includes(String(language).toLowerCase())) return escapeHtml(source);
    const tokens = /(#[^\n]*|"""[\s\S]*?"""|'''[\s\S]*?'''|[fFrRbB]?"(?:\\.|[^"\\\n])*"|[fFrRbB]?'(?:\\.|[^'\\\n])*'|\b(?:and|as|assert|async|await|break|class|continue|def|del|elif|else|except|False|finally|for|from|global|if|import|in|is|lambda|None|nonlocal|not|or|pass|raise|return|True|try|while|with|yield)\b|\b(?:print|len|range|str|int|float|bool|list|dict|set|tuple|input|open|sum|min|max|enumerate|zip|type|isinstance|sorted|abs|round)\b|\b\d+(?:\.\d+)?\b)/g;
    let output = "";
    let cursor = 0;
    let match;
    while ((match = tokens.exec(source)) !== null) {
      output += escapeHtml(source.slice(cursor, match.index));
      const token = match[0];
      let className = "token-number";
      if (token.startsWith("#")) className = "token-comment";
      else if (/^[fFrRbB]?["']/.test(token)) className = "token-string";
      else if (PYTHON_KEYWORDS.has(token)) className = "token-keyword";
      else if (PYTHON_BUILTINS.has(token)) className = "token-builtin";
      output += `<span class="${className}">${escapeHtml(token)}</span>`;
      cursor = match.index + token.length;
    }
    return output + escapeHtml(source.slice(cursor));
  }

  function normalizeRole(role) {
    return TUTOR_ROLES.has(String(role || "").toLowerCase()) ? "tutor" : "user";
  }

  function normalizeFieldName(name) {
    const key = stripAccents(name).toLowerCase().trim().replace(/[\s-]+/g, "_");
    return FIELD_ALIASES[key] || key;
  }

  function parseBlockFields(body) {
    const fields = {};
    let lastMulti = null;
    for (const raw of String(body ?? "").split("\n")) {
      const line = raw.trim();
      if (!line) continue;
      const pair = line.replace(/^[-*•]\s+/, "").match(/^([A-Za-zÀ-ÿ_ -]{2,30}?)\s*:\s*(.*)$/);
      if (pair) {
        const key = normalizeFieldName(pair[1]);
        const value = pair[2].trim();
        if (MULTI_FIELDS.has(key)) {
          fields[key] = fields[key] || [];
          if (value) fields[key].push(value);
          lastMulti = key;
        } else {
          fields[key] = value;
          lastMulti = null;
        }
      } else if (lastMulti && /^[-*•]\s+/.test(line)) {
        fields[lastMulti].push(line.replace(/^[-*•]\s+/, ""));
      }
    }
    return fields;
  }

  // Interface blocks emitted by Kenobi at the end of a reply (```kenobi-lecon, -pret, -qcm, -boutons, -exercice,
  // -verdict, -bilan, -revision, -memoire).
  function extractKenobiBlocks(value) {
    const source = String(value ?? "");
    const blocks = [];
    const pattern = /```[ \t]*kenobi[-_]([A-Za-zÀ-ÿ]+)[^\n]*\n([\s\S]*?)```/g;
    let clean = source.replace(pattern, (whole, kind, body) => {
      const type = BLOCK_KINDS[stripAccents(kind).toLowerCase()] || BLOCK_KINDS[kind.toLowerCase()];
      if (!type) return whole;
      blocks.push({ type, fields: parseBlockFields(body) });
      return "";
    });
    const open = clean.search(/```[ \t]*kenobi[-_]/);
    const pending = open !== -1;
    if (pending) clean = clean.slice(0, open);
    return { text: clean.replace(/\n{3,}/g, "\n\n").trim(), blocks, pending };
  }

  function localDate(date = new Date()) {
    const value = date instanceof Date ? date : new Date(date);
    const pad = (number) => String(number).padStart(2, "0");
    return `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(value.getDate())}`;
  }

  function addDays(date, days) {
    const value = new Date(date instanceof Date ? date.getTime() : date);
    value.setDate(value.getDate() + days);
    return localDate(value);
  }

  function parseRevision(line) {
    const [itemPart, delayPart = ""] = String(line ?? "").split("|");
    const item = itemPart.trim();
    const match = delayPart.match(/(\d+)/);
    const step = match ? Math.max(1, Math.min(60, parseInt(match[1], 10))) : 1;
    return item ? { item, step } : null;
  }

  function sameItem(a, b) {
    return stripAccents(a).toLowerCase().replace(/\s+/g, " ").trim() === stripAccents(b).toLowerCase().replace(/\s+/g, " ").trim();
  }

  function scheduleReview(memory, item, step, now = new Date()) {
    memory.reviews = Array.isArray(memory.reviews) ? memory.reviews : [];
    const existing = memory.reviews.find((review) => sameItem(review.item, item));
    const entry = { item, step, due: addDays(now, step) };
    if (existing) Object.assign(existing, entry);
    else memory.reviews.push(entry);
    memory.reviews.sort((a, b) => String(a.due).localeCompare(String(b.due)));
  }

  // A review done from memory moves 1 → 7 → 21 → retired; a missed one starts again at J+1.
  function recordReview(memory, line, now = new Date()) {
    const [itemPart, resultPart = ""] = String(line ?? "").split("|");
    const item = itemPart.trim();
    if (!item) return null;
    const success = /\b(ok|reussi|réussi|acquis|juste)\b/i.test(resultPart) && !/(a[-_ ]?revoir|rate|raté|ko|echec|échec)/i.test(stripAccents(resultPart));
    memory.reviews = Array.isArray(memory.reviews) ? memory.reviews : [];
    const existing = memory.reviews.find((review) => sameItem(review.item, item));
    if (success) {
      if (!existing) return { item, result: "ok" };
      const next = { 1: 7, 7: 21 }[Number(existing.step)] || null;
      if (next) scheduleReview(memory, existing.item, next, now);
      else memory.reviews = memory.reviews.filter((review) => review !== existing);
      return { item, result: "ok", next };
    }
    scheduleReview(memory, existing ? existing.item : item, 1, now);
    return { item, result: "a-revoir", next: 1 };
  }

  function dueReviews(memory, now = new Date()) {
    const today = localDate(now);
    return (Array.isArray(memory?.reviews) ? memory.reviews : []).filter((review) => String(review.due) <= today);
  }

  function prependUnique(list, values) {
    const result = Array.isArray(list) ? [...list] : [];
    for (const value of [...values].reverse()) {
      const text = String(value || "").trim();
      if (!text) continue;
      const position = result.findIndex((existing) => sameItem(existing, text));
      if (position !== -1) result.splice(position, 1);
      result.unshift(text);
    }
    return result;
  }

  function isValidatedStatus(status) {
    return /^acquise?$/i.test(stripAccents(String(status || "")).trim());
  }

  // Applies a bilan to the course memory; `validate` also closes the lesson and the session.
  function applyBilan(course, session, bilan, { validate = false, now = new Date() } = {}) {
    const memory = course.memory;
    const acquired = bilan.acquis || [];
    const toReview = bilan.a_revoir || [];
    memory.acquired = prependUnique(memory.acquired, acquired);
    memory.toReview = prependUnique((memory.toReview || []).filter((item) => !acquired.some((done) => sameItem(done, item))), toReview);
    const planned = (bilan.revision || []).map(parseRevision).filter(Boolean);
    for (const revision of planned) scheduleReview(memory, revision.item, revision.step, now);
    for (const item of toReview) {
      if (!planned.some((revision) => sameItem(revision.item, item))) scheduleReview(memory, item, 1, now);
    }
    for (const line of bilan.revu || []) recordReview(memory, line, now);
    if (bilan.prochaine_etape) memory.nextFocus = bilan.prochaine_etape;
    if (bilan.resume) session.summary = bilan.resume;
    memory.lastSessionId = session.id;
    session.updatedAt = now.toISOString();
    if (!validate) return { validated: false, next: getProgress(course).current };
    return completeLesson(course, session, now);
  }

  // Closes the lesson of a session once the learner confirms the bilan.
  function completeLesson(course, session, now = new Date()) {
    for (const module of course.modules || []) {
      const lesson = (module.lessons || []).find((item) => item.id === session.lessonId);
      if (lesson) lesson.status = "done";
    }
    normalizeCourseProgress(course);
    session.status = "completed";
    session.validatedAt = now.toISOString();
    session.updatedAt = now.toISOString();
    return { validated: true, next: getProgress(course).current };
  }

  function getCourse(state, courseId) {
    if (!state || !Array.isArray(state.courses)) return null;
    return state.courses.find((course) => course.id === (courseId || state.activeCourseId)) || state.courses[0] || null;
  }

  function getLessons(course) {
    if (!course || !Array.isArray(course.modules)) return [];
    return course.modules.flatMap((module) => (module.lessons || []).map((lesson) => ({ ...lesson, moduleId: module.id, moduleTitle: module.title })));
  }

  function getProgress(course) {
    const lessons = getLessons(course);
    const done = lessons.filter((lesson) => lesson.status === "done").length;
    const total = lessons.length;
    const current = lessons.find((lesson) => lesson.status === "current") || lessons.find((lesson) => lesson.status !== "done") || null;
    return { done, total, percent: total ? Math.round((done / total) * 100) : 0, current };
  }

  function normalizeCourseProgress(course) {
    const lessons = getLessons(course);
    const firstIncomplete = lessons.find((lesson) => lesson.status !== "done");
    for (const module of course.modules || []) {
      for (const lesson of module.lessons || []) {
        if (lesson.status === "done") continue;
        lesson.status = firstIncomplete && lesson.id === firstIncomplete.id ? "current" : "todo";
      }
    }
    return course;
  }

  function getLesson(course, lessonId) {
    return getLessons(course).find((lesson) => lesson.id === lessonId) || null;
  }

  function sortedSessions(course) {
    return [...(course?.sessions || [])].sort((a, b) => String(b.updatedAt).localeCompare(String(a.updatedAt)));
  }

  function getResumeSession(course, lessonId) {
    const sessions = sortedSessions(course);
    return sessions.find((session) => session.status === "active" && session.lessonId === lessonId)
      || sessions.find((session) => session.status === "active")
      || null;
  }

  function createSession(course, lessonId, now = new Date(), id) {
    const lesson = getLesson(course, lessonId) || getProgress(course).current;
    if (!lesson) throw new Error("Aucune leçon disponible pour cette session.");
    const timestamp = now.toISOString();
    return {
      id: id || `session-${now.getTime()}`,
      title: lesson.title,
      startedAt: timestamp,
      updatedAt: timestamp,
      duration: 0,
      status: "active",
      lessonId: lesson.id,
      summary: `Session consacrée à « ${lesson.title} ».`,
      messages: []
    };
  }

  function sameDay(a, b) {
    return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  }

  function formatDate(value, now = new Date()) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "Date inconnue";
    if (sameDay(date, now)) return "Aujourd’hui";
    const yesterday = new Date(now);
    yesterday.setDate(now.getDate() - 1);
    if (sameDay(date, yesterday)) return "Hier";
    return new Intl.DateTimeFormat("fr-FR", { day: "numeric", month: "short", year: date.getFullYear() !== now.getFullYear() ? "numeric" : undefined }).format(date);
  }

  function formatTime(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "";
    return new Intl.DateTimeFormat("fr-FR", { hour: "2-digit", minute: "2-digit" }).format(date);
  }

  function formatSeconds(value) {
    const number = Number(value);
    if (!Number.isFinite(number) || number <= 0) return "";
    return `${number.toLocaleString("fr-FR", { maximumFractionDigits: 1, minimumFractionDigits: number < 10 ? 1 : 0 })} s`;
  }

  // ── Verdicts, mémoire et consigne en cours ─────────────────────────────────

  // « reussi », « presque » or « pas-encore », whatever the accents or the wording of the model.
  function verdictOf(fields) {
    const value = stripAccents(String(fields?.resultat || "")).toLowerCase().trim();
    if (!value) return null;
    if (/^(reussi|reussie|ok|juste|correct|valide)/.test(value)) return "reussi";
    if (/^presque/.test(value)) return "presque";
    if (/^(pas|non|rate|echec|incorrect|faux)/.test(value)) return "pas-encore";
    return null;
  }

  function messageBlocks(message) {
    return normalizeRole(message?.role) === "tutor" && !message.pending && !message.error ? extractKenobiBlocks(message.content).blocks : [];
  }

  // True when the learner's message carries a run or a check: only then can the reply hold a verdict.
  function carriesAttempt(message) {
    return Boolean(message && (message.run || message.attempt));
  }

  function sameTask(a, b) {
    return Boolean(a && b && a.expected && b.expected) && sameItem(a.expected, b.expected) && (!a.file || !b.file || a.file === b.file);
  }

  // Every instruction of a session, in order. An exercise block opens one; it stays open until a « reussi »
  // verdict or a different exercise. Models are not that disciplined, so two things are read strictly:
  // a verdict only counts as the answer to a run or a check, and a block that says the open instruction again
  // (or the one this very message just passed) opens nothing.
  function sessionTasks(session) {
    const tasks = [];
    let current = null;
    let answered = null;
    const pass = (task) => Object.assign(task, { verdict: "reussi", done: true, open: false });
    for (const message of Array.isArray(session?.messages) ? session.messages : []) {
      if (normalizeRole(message.role) === "user") {
        if (current && carriesAttempt(message)) {
          current.attempts += 1;
          // Sessions from before the verdicts: a run that worked was the exercise handed in.
          if (message.reason === "exercise" && message.run?.status === "ok" && !message.run.attempt) {
            pass(current);
            current = null;
          }
        }
        answered = message;
        continue;
      }
      const blocks = messageBlocks(message);
      const judged = carriesAttempt(answered) ? blocks.map((item) => (item.type === "verdict" ? verdictOf(item.fields) : null)).find(Boolean) : null;
      let passed = null;
      if (judged && current) {
        current.verdict = judged;
        if (judged === "reussi") {
          passed = pass(current);
          current = null;
        }
      }
      const block = blocks.find((item) => item.type === "exercice");
      if (!block) continue;
      const next = { messageId: message.id, file: block.fields.fichier || "", expected: block.fields.attendu || "", attempts: 0, verdict: null, done: false, open: true };
      if (sameTask(current || passed, next)) continue;
      if (current) current.open = false;
      current = next;
      tasks.push(next);
    }
    return tasks;
  }

  // The state of the instruction opened by a message: runs sent since, last verdict, done or not.
  function taskState(session, messageId) {
    return sessionTasks(session).find((task) => task.messageId === messageId) || null;
  }

  // The instruction Kenobi is waiting on, if any.
  function openTask(session) {
    const tasks = sessionTasks(session);
    const last = tasks[tasks.length - 1];
    return last && last.open ? last : null;
  }

  // True when the exercise block of a message only says again the instruction already open before it: the
  // interface keeps the first card, with its attempts.
  function repeatsOpenTask(session, messageId, fields) {
    const messages = Array.isArray(session?.messages) ? session.messages : [];
    const index = messages.findIndex((message) => message.id === messageId);
    if (index < 1) return false;
    return sameTask(openTask({ messages: messages.slice(0, index) }), { file: fields?.fichier || "", expected: fields?.attendu || "" });
  }

  // True when a tutor message answers a run or a check: the only place where a verdict is shown.
  function answersAttempt(session, messageId) {
    const messages = Array.isArray(session?.messages) ? session.messages : [];
    let index = messages.findIndex((message) => message.id === messageId) - 1;
    while (index >= 0 && normalizeRole(messages[index].role) !== "user") index -= 1;
    return index >= 0 && carriesAttempt(messages[index]);
  }

  // Durable facts Kenobi asked the interface to keep (kenobi-memoire): newest first, no duplicate, bounded.
  function rememberFacts(memory, fields, limit = 30) {
    const kept = (fields?.retenir || []).map((item) => String(item).trim().slice(0, 240)).filter(Boolean);
    const dropped = (fields?.oublier || []).map((item) => String(item).trim()).filter(Boolean);
    let facts = (Array.isArray(memory.facts) ? memory.facts : []).filter((fact) => !dropped.some((item) => sameItem(item, fact)));
    facts = prependUnique(facts, kept).slice(0, limit);
    memory.facts = facts;
    return kept;
  }

  // ── QCM et boutons de réponse ──────────────────────────────────────────────

  // A small seeded generator (FNV-1a hash, then mulberry32): the same message always shows its options the same way.
  function seededRandom(seed) {
    let state = 2166136261;
    for (const character of String(seed ?? "")) state = Math.imul(state ^ character.codePointAt(0), 16777619) >>> 0;
    return () => {
      state = (state + 0x6d2b79f5) >>> 0;
      let value = Math.imul(state ^ (state >>> 15), state | 1);
      value ^= value + Math.imul(value ^ (value >>> 7), value | 61);
      return ((value ^ (value >>> 14)) >>> 0) / 4294967296;
    };
  }

  function shuffledIndexes(count, seed) {
    const random = seededRandom(seed);
    const order = Array.from({ length: count }, (_, index) => index);
    for (let index = count - 1; index > 0; index -= 1) {
      const other = Math.floor(random() * (index + 1));
      [order[index], order[other]] = [order[other], order[index]];
    }
    return order;
  }

  const plainOption = (value) => stripAccents(value).replace(/[`*_"«»]/g, "").toLowerCase().replace(/\s+/g, " ").trim();

  // The right option as Kenobi gave it: its number as asked (« 2 », « 2 — … »), else its text, else its letter.
  function quizAnswerIndex(value, options) {
    const raw = String(value ?? "").trim();
    if (!raw) return -1;
    const number = raw.match(/^(?:n°\s*)?(\d)\s*(?:[).:]\s*)?(?:$|[-—–]\s)/);
    if (number && Number(number[1]) >= 1 && Number(number[1]) <= options.length) return Number(number[1]) - 1;
    const wanted = plainOption(raw);
    const named = options.findIndex((option) => plainOption(option) === wanted);
    if (named !== -1) return named;
    const letter = raw.match(/^([a-d])\s*(?:[).:]\s*)?(?:$|[-—–]\s)/i);
    const position = letter ? letter[1].toLowerCase().charCodeAt(0) - 97 : -1;
    return position < options.length ? position : -1;
  }

  // A QCM ready to show: options in a stable mixed order (models tend to put the right answer at the same place),
  // each one knowing whether it is the right one. `judged` is false when Kenobi gave no usable answer.
  function quizOf(fields, seed) {
    const options = (fields?.choix || []).map((item) => String(item).trim()).filter(Boolean).slice(0, 4);
    if (options.length < 2) return null;
    const answer = quizAnswerIndex(fields.reponse, options);
    return {
      question: String(fields.question || "").trim(),
      explanation: String(fields.explication || "").trim(),
      options: shuffledIndexes(options.length, seed).map((index) => ({ text: options[index], correct: index === answer })),
      judged: answer !== -1
    };
  }

  // The QCM a message carries, or null.
  function messageQuiz(message) {
    const block = messageBlocks(message).find((item) => item.type === "qcm");
    return block ? quizOf(block.fields, message.id) : null;
  }

  // The learner's answer to the QCM of a message: a later message that names it.
  function quizAnswer(session, messageId) {
    return (Array.isArray(session?.messages) ? session.messages : []).find((message) => message.qcm?.messageId === messageId) || null;
  }

  // Only the newest QCM of a session waits for a click; an older one stays as it was left.
  function latestQuiz(session) {
    const messages = Array.isArray(session?.messages) ? session.messages : [];
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      if (messageBlocks(messages[index]).some((block) => block.type === "qcm")) return messages[index].id;
    }
    return null;
  }

  // What Kenobi receives when the learner clicks an option. The interface has already told right from wrong.
  function formatQuizAnswer(quiz, choice) {
    const picked = quiz?.options.find((option) => option.text === choice);
    if (!quiz?.judged || !picked) return `[QCM — il a choisi « ${choice} ». L'interface ne connaît pas la bonne réponse : dis-lui si c'est juste.]`;
    if (picked.correct) return `[QCM — il a choisi « ${choice} » : c'est la bonne réponse. L'interface le lui a dit et a montré l'explication.]`;
    const right = quiz.options.find((option) => option.correct);
    return `[QCM — il a choisi « ${choice} » : ce n'est pas la bonne réponse, qui était « ${right.text} ». L'interface le lui a dit et a montré l'explication.]`;
  }

  // Answer buttons (« C'est parti ! », kenobi-boutons) only make sense under the last message of the conversation.
  function isLastMessage(session, messageId) {
    const messages = Array.isArray(session?.messages) ? session.messages : [];
    return messages.length > 0 && messages[messages.length - 1].id === messageId;
  }

  // Labels of answer buttons: short, unique, three at most; the fallback when Kenobi wrote none.
  function replyLabels(fields, fallback = []) {
    const labels = [];
    for (const item of fields?.bouton || []) {
      const text = String(item).replace(/\s+/g, " ").replace(/(\p{L})'(\p{L})/gu, "$1’$2").trim().slice(0, 60);
      if (text && !labels.some((label) => sameItem(label, text))) labels.push(text);
    }
    return (labels.length ? labels : fallback).slice(0, 3);
  }

  // The question a reply ends on, if it ends on one: its last sentence, code and emphasis set aside.
  function closingQuestion(text) {
    const plain = String(text || "")
      .replace(/```[\s\S]*?(?:```|$)/g, " ")
      .replace(/`[^`\n]*`/g, "x")
      .replace(/[*_]+/g, "")
      .trim();
    const last = plain.split("\n").map((line) => line.trim()).filter(Boolean).pop() || "";
    const match = last.match(/([^.!?…]*\?)[\s»"’)]*$/);
    return match ? match[1].trim() : "";
  }

  // A yes/no question: it opens like one (« Tu veux… », « On continue », « Prêt », « Est-ce que… ») and asks for
  // nothing else (no « quel », « comment », « pourquoi », no « ou » between two choices).
  const CLOSED_OPENING = /^(?:et |alors,? |bon,? |donc,? |bien,? |parfait,? |d'accord,? |ok,? )?(?:tu veux|veux-tu|tu voudrais|voudrais-tu|tu preferes|on continue|on passe|on y va|on enchaine|on valide|on reprend|on commence|on attaque|on essaie|on refait|on fait|pret|prete|tu es pret|es-tu pret|c'est clair|c'est bon|ca te va|ca va|tu as compris|as-tu compris|c'est compris|tu me suis|tu vois|d'accord|ok|est-ce que|est-ce clair|tu voulais dire|tu te sens|tu t'en sens|tu as des questions|des questions|une question)\b/;
  const OPEN_WORDS = /\b(?:quel|quelle|quels|quelles|lequel|laquelle|lesquels|lesquelles|comment|pourquoi|combien|quoi|ou|quand)\b/;

  function closedQuestion(question) {
    const plain = stripAccents(String(question || "")).toLowerCase().replace(/[’`]/g, "'").replace(/^[\s«"'(]+/, "").trim();
    return plain.endsWith("?") && CLOSED_OPENING.test(plain) && !OPEN_WORDS.test(plain);
  }

  // Answers in one click under Kenobi's last message when he wrote no button (what can be clicked should not
  // have to be typed): « Oui » / « Non » under a yes/no question, « On continue » under a reply
  // that asks nothing while no instruction is open. Nothing when there is already something to click or to do.
  function fallbackReplies(session, message) {
    if (!session || session.status !== "active" || !message || normalizeRole(message.role) !== "tutor") return [];
    if (message.pending || message.error || message.stopped || !isLastMessage(session, message.id)) return [];
    const { text, blocks } = extractKenobiBlocks(message.content);
    if (blocks.some((block) => ["pret", "boutons", "qcm", "exercice"].includes(block.type))) return [];
    if (blocks.some((block) => block.type === "bilan" && isValidatedStatus(block.fields.statut))) return [];
    const question = closingQuestion(text);
    if (question) return closedQuestion(question) ? ["Oui", "Non"] : [];
    return text.trim() && !openTask(session) ? ["On continue"] : [];
  }

  // ── Cours ──────────────────────────────────────────────────────────────────

  const COURSE_LANGUAGES = {
    python: { label: "Python", file: "exercice.py", watch: "tes commandes Python", runs: true },
    javascript: { label: "JavaScript", file: "exercice.js", watch: "tes commandes Node.js", runs: true },
    shell: { label: "Ligne de commande", file: "exercice.sh", watch: "toutes tes commandes", runs: false },
    autre: { label: "Code", file: "exercice.txt", watch: "toutes tes commandes", runs: false }
  };

  // What the course works with: a terminal or not, the language Kenobi follows, the default file of the editor.
  function courseTools(course) {
    const tools = course && typeof course.tools === "object" && course.tools ? course.tools : null;
    if (!tools) {
      const legacy = course?.id === "python";
      return { ...COURSE_LANGUAGES.python, terminal: legacy, language: legacy ? "python" : "" };
    }
    let language = String(tools.language || "").toLowerCase();
    if (!COURSE_LANGUAGES[language]) language = tools.terminal ? "autre" : "";
    const preset = COURSE_LANGUAGES[language] || { label: "", file: "", watch: "", runs: false };
    return { terminal: Boolean(tools.terminal), language, ...preset, file: tools.file || preset.file };
  }

  function courseShort(course) {
    return String(course?.shortTitle || course?.title || "Cours").trim();
  }

  // Two letters for the course badge: « Py » for Python, initials for several words.
  function courseMonogram(course) {
    const words = stripAccents(courseShort(course)).replace(/[^A-Za-z0-9 ]+/g, " ").trim().split(/\s+/).filter(Boolean);
    if (!words.length) return "··";
    const text = words.length > 1 ? words[0][0] + words[1][0] : words[0].slice(0, 2);
    return text[0].toUpperCase() + text.slice(1).toLowerCase();
  }

  // ── Exécutions du terminal intégré ─────────────────────────────────────────

  const PYTHON_TOOL = /^(?:python(?:3(?:\.\d+)?)?|py|pip3?|ipython3?)(?:\.exe)?$/i;
  const PYTHON_INTERPRETER = /^(?:python(?:3(?:\.\d+)?)?|py|ipython3?)(?:\.exe)?$/i;
  const programName = (word) => String(word || "").split(/[\\/]/).pop();
  const SECRET_PATTERNS = [
    /-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----/g,
    /\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}/g,
    /\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}|\bgithub_pat_[A-Za-z0-9_]{20,}/g,
    /\bxox[abprs]-[A-Za-z0-9-]{10,}/g,
    /\bAKIA[0-9A-Z]{16}\b/g,
    /\bAIza[0-9A-Za-z_-]{35}\b/g,
    /\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}/g
  ];
  const SECRET_ASSIGNMENT = /((?:api[_-]?key|secret|token|passw(?:or)?d|mot[_ -]?de[_ -]?passe|authorization)[A-Za-z0-9_]*["']?\s*[:=]\s*["']?)([A-Za-z0-9_\-./+=]{16,})/gi;
  const BEARER = /(Bearer\s+)[A-Za-z0-9._~+/-]{12,}=*/g;
  const RUN_HEADERS = {
    error: "exécution libre, envoyée seule : le programme s'est arrêté sur une erreur.",
    fixed: "exécution libre, envoyée seule : nouvelle version après ton indice, terminée sans erreur.",
    attached: "dernière exécution, jointe par l'interface à son message.",
    manual: "exécution envoyée par lui depuis le terminal."
  };
  const NODE_TOOL = /^(?:node|nodejs|npm|npx|pnpm|yarn|deno|bun|tsx|ts-node)(?:\.exe|\.cmd)?$/i;

  function commandSegments(command) {
    return String(command || "").split(/&&|\|\||;|\|/).map((part) => part.trim()).filter(Boolean);
  }

  function segmentWords(segment) {
    const words = (segment.match(/"[^"]*"|'[^']*'|\S+/g) || []).map((word) => word.replace(/^(["'])(.*)\1$/, "$2"));
    while (words.length && /^[A-Za-z_][A-Za-z0-9_]*=/.test(words[0])) words.shift();
    return words;
  }

  // Kenobi only sees Python: Run, python/python3/py, pip and scripts *.py.
  function isPythonCommand(command) {
    return commandSegments(command).some((segment) => {
      const words = segmentWords(segment);
      return Boolean(words.length) && (PYTHON_TOOL.test(programName(words[0])) || /\.py$/i.test(words[0]));
    });
  }

  function isReplCommand(command) {
    return commandSegments(command).some((segment) => {
      const words = segmentWords(segment);
      if (!words.length) return false;
      if (/^(?:node|nodejs)(?:\.exe)?$/i.test(programName(words[0]))) return words.length === 1;
      if (!PYTHON_INTERPRETER.test(programName(words[0]))) return false;
      return !words.slice(1).some((word) => /\.py$/.test(word) || word === "-c" || word === "-m");
    });
  }

  // What Kenobi sees of the terminal depends on the course: Python, Node.js, or every command (shell, other code).
  function isCourseCommand(command, language) {
    if (language === "python") return isPythonCommand(command);
    if (language === "javascript") {
      return commandSegments(command).some((segment) => {
        const words = segmentWords(segment);
        return Boolean(words.length) && (NODE_TOOL.test(programName(words[0])) || /\.(?:js|mjs|cjs|ts)$/i.test(words[0]));
      });
    }
    if (language === "shell" || language === "autre") return Boolean(String(command || "").trim());
    return false;
  }

  // A run counts as an attempt on the open instruction when it launches a file. In a course that follows every
  // command (shell, other code) nothing is an attempt by itself: the learner asks for the check when done.
  function isAttempt(run, language) {
    if (language === "shell" || language === "autre") return false;
    if (run?.source === "run") return true;
    return /\.(?:py|js|mjs|cjs|ts|sh)(?:\s|$|["'])/i.test(String(run?.cmd || ""));
  }

  // The terminal screen sent as an attempt (« Vérifier ») in a course done on the command line.
  function formatCheckForKenobi(text, { attempt = 1, expected = "" } = {}) {
    return [
      `[Exécution — tentative n° ${attempt} pour la consigne en cours.${expected ? ` Attendu : « ${String(expected).slice(0, 300)} ».` : ""}]`,
      "Écran de son terminal, envoyé par lui pour vérification :",
      fence("text", truncateLines(maskSecrets(text), { max: 7000 }) || "(écran vide)")
    ].join("\n");
  }

  function parseTraceback(output, file) {
    const lines = String(output || "").split("\n");
    let index = -1;
    let type = "";
    let message = "";
    for (let position = lines.length - 1; position >= 0; position -= 1) {
      const match = lines[position].match(/^([A-Za-z_][\w.]*(?:Error|Exception|Exit|Interrupt|Warning))(?::\s?(.*))?$/);
      if (match) {
        type = match[1].split(".").pop();
        message = (match[2] || "").trim();
        index = position;
        break;
      }
    }
    if (!type) return null;
    let line = null;
    let where = "";
    for (let position = index; position >= 0; position -= 1) {
      const frame = lines[position].match(/^\s*File "([^"]+)", line (\d+)/);
      if (!frame) continue;
      const name = frame[1].split("/").pop();
      if (!where) { where = name; line = Number(frame[2]); }
      if (file && name === file) { where = name; line = Number(frame[2]); break; }
    }
    return { type, message, line, file: where || null };
  }

  function runStatus(run) {
    if (run?.exit === 0) return "ok";
    if (run?.exit === 130 || /(^|\n)KeyboardInterrupt\s*$/.test(String(run?.output || ""))) return "interrupted";
    return "error";
  }

  function summarizeRun(run) {
    const status = runStatus(run);
    const error = status === "error" ? parseTraceback(run.output, run.file) : null;
    return {
      status,
      errorType: error?.type || "",
      errorLine: error?.line || null,
      errorMessage: (error?.message || "").slice(0, 240)
    };
  }

  function runLabel(summary, exit) {
    if (summary.status === "ok") return "Réussi";
    if (summary.status === "interrupted") return "Interrompu";
    if (summary.errorType) return `${summary.errorType}${summary.errorLine ? ` · ligne ${summary.errorLine}` : ""}`;
    return `Erreur · code ${exit ?? "?"}`;
  }

  // What the conversation keeps of a run: enough for the card, never the whole output.
  function runRecord(run) {
    return {
      cmd: String(run.cmd || "").slice(0, 300),
      exit: Number.isInteger(run.exit) ? run.exit : null,
      file: run.file || "",
      lesson: run.lesson || "",
      source: run.source || "shell",
      durationMs: Number.isFinite(run.durationMs) ? run.durationMs : null,
      ...summarizeRun(run)
    };
  }

  function maskSecrets(text) {
    let value = String(text ?? "");
    for (const pattern of SECRET_PATTERNS) value = value.replace(pattern, "[masqué]");
    value = value.replace(BEARER, "$1[masqué]");
    return value.replace(SECRET_ASSIGNMENT, (whole, prefix, secret) => (/[A-Za-z]/.test(secret) && /\d/.test(secret) ? `${prefix}[masqué]` : whole));
  }

  function truncateLines(text, { head = 60, tail = 40, max = 4000 } = {}) {
    let value = String(text ?? "");
    const lines = value.split("\n");
    if (lines.length > head + tail) value = [...lines.slice(0, head), `[… ${lines.length - head - tail} lignes coupées …]`, ...lines.slice(-tail)].join("\n");
    if (value.length > max) {
      const half = Math.max(50, Math.floor((max - 30) / 2));
      value = `${value.slice(0, half)}\n[… sortie coupée …]\n${value.slice(-half)}`;
    }
    return value;
  }

  function truncateCode(code, max = 5000) {
    const value = String(code ?? "");
    return value.length > max ? `${value.slice(0, max)}\n# [… fichier coupé …]` : value;
  }

  function fence(language, content) {
    const longest = Math.max(2, ...(String(content).match(/`+/g) || []).map((ticks) => ticks.length));
    const marker = "`".repeat(longest + 1);
    return `${marker}${language}\n${content}\n${marker}`;
  }

  function fileLanguage(file) {
    const name = String(file || "");
    if (/\.py$/i.test(name)) return "python";
    if (/\.(?:js|mjs|cjs)$/i.test(name)) return "javascript";
    if (/\.sh$/i.test(name)) return "bash";
    return "text";
  }

  // Message sent to Kenobi for a run: the real command, code and output, secrets masked, under 12 000 characters.
  function formatRunForKenobi(run, { reason = "manual", budget = 9000, attempt = 1, expected = "" } = {}) {
    const summary = summarizeRun(run);
    const seconds = Number.isFinite(run.durationMs) ? formatSeconds(Math.max(run.durationMs, 100) / 1000) : "";
    const header = reason === "exercise"
      ? `tentative n° ${attempt} pour la consigne en cours.${expected ? ` Attendu : « ${String(expected).slice(0, 300)} ».` : ""}`
      : RUN_HEADERS[reason] || RUN_HEADERS.manual;
    const lines = [`[Exécution — ${header}]`];
    lines.push(`Commande : \`${run.cmd}\`${run.cwd ? ` · dossier \`${run.cwd}\`` : ""} · code de retour ${run.exit ?? "inconnu"}${seconds ? ` · ${seconds}` : ""}`);
    if (summary.status === "error" && summary.errorType) {
      lines.push(`Erreur repérée : ${summary.errorType}${summary.errorLine ? ` à la ligne ${summary.errorLine}` : ""}${summary.errorMessage ? ` — ${summary.errorMessage}` : ""}`);
    }
    if (summary.status === "interrupted") lines.push("Programme interrompu par lui (Ctrl+C).");
    const room = budget - lines.join("\n").length - 160;
    if (room > 600) {
      const codeMax = run.code ? Math.min(5000, Math.floor(room * 0.55)) : 0;
      if (run.code) lines.push("", `Fichier \`${run.file || "exercice.py"}\` :`, fence(fileLanguage(run.file), truncateCode(maskSecrets(run.code), codeMax)));
      const output = truncateLines(maskSecrets(run.output || ""), { max: Math.max(300, room - codeMax) });
      lines.push("", run.truncated ? "Sortie du terminal (début et fin) :" : "Sortie du terminal :", fence("text", output || "(aucune sortie)"));
    }
    return lines.join("\n");
  }

  function formatFileForKenobi({ file, code }, { budget = 6000 } = {}) {
    return [
      `[Fichier ouvert dans l'éditeur, joint par l'interface : \`${file}\` — version pas encore lancée]`,
      fence(fileLanguage(file), truncateCode(maskSecrets(code), Math.max(400, Math.min(5000, budget - 200))))
    ].join("\n");
  }

  function formatTerminalSnapshot(text, { selection = false } = {}) {
    return [
      `[Terminal — ${selection ? "sélection" : "écran"} envoyé par lui depuis l'interface]`,
      fence("text", truncateLines(maskSecrets(text), { max: 6000 }) || "(écran vide)")
    ].join("\n");
  }

  // Kept for the cards: the open instruction, and whether an exercise has been passed.
  function pendingExercise(session) {
    return openTask(session);
  }

  function exerciseSubmitted(session, messageId) {
    return Boolean(taskState(session, messageId)?.done);
  }

  // ── Fichiers joints à un message (code écrit ailleurs : VS Code, son propre terminal) ──

  const ATTACH_LIMITS = { files: 5, fileChars: 60000, totalChars: 100000, bytes: 512 * 1024 };
  const ATTACH_LANGUAGES = {
    py: "python", pyw: "python", pyi: "python", json: "json", csv: "csv", tsv: "csv", sql: "sql", html: "html",
    htm: "html", css: "css", js: "javascript", mjs: "javascript", ts: "typescript", sh: "bash", ps1: "powershell",
    md: "markdown", yaml: "yaml", yml: "yaml", toml: "toml", ini: "ini", cfg: "ini", conf: "ini", xml: "xml",
    txt: "texte", log: "texte", rst: "texte"
  };
  const ATTACH_ACCEPT = Object.keys(ATTACH_LANGUAGES).map((suffix) => `.${suffix}`).join(",");

  // The name as Kenobi and the conversation show it: no path, no backtick or line break.
  function cleanFileName(name) {
    return String(name ?? "").split(/[\\/]/).pop().replace(/[`\u0000-\u001f]/g, "").trim().slice(0, 120) || "fichier.txt";
  }

  function attachedFileLanguage(name) {
    const suffix = String(name ?? "").toLowerCase().match(/\.([a-z0-9]+)$/)?.[1] || "";
    return ATTACH_LANGUAGES[suffix] || null;
  }

  // Why this file cannot join the message (null when it can), before reading it.
  function attachmentProblem({ name, size }, joined = []) {
    if (!attachedFileLanguage(name)) return `${name} : seuls les fichiers texte se joignent (.py, .txt, .csv, .json, .md…).`;
    if (joined.length >= ATTACH_LIMITS.files) return `${ATTACH_LIMITS.files} fichiers au plus par message.`;
    if (size > ATTACH_LIMITS.bytes) return `${name} est trop gros pour la discussion : colle seulement la partie utile.`;
    return null;
  }

  // The file once read: { file } ready to join, or { error } in words for the learner.
  function readAttachment(name, text, joined = []) {
    const content = String(text ?? "").replace(/^\ufeff/, "").replace(/\r\n?/g, "\n");
    if (content.includes("\u0000")) return { error: `${name} n’est pas un fichier texte.` };
    if (!content.trim()) return { error: `${name} est vide.` };
    if (content.length > ATTACH_LIMITS.fileChars) return { error: `${name} est trop long (60 000 caractères au plus, environ 1 500 lignes) : joins la partie utile.` };
    const total = joined.reduce((sum, item) => sum + item.chars, 0) + content.length;
    if (total > ATTACH_LIMITS.totalChars) return { error: `Trop de texte pour un seul message (100 000 caractères au plus) : envoie ${name} dans le message suivant.` };
    const lines = content.replace(/\n$/, "").split("\n").length;
    return { file: { name, content, lines, chars: content.length } };
  }

  // A whole file for Kenobi, secrets masked; the conversation shows it as a card.
  function formatAttachedFile({ name, content, lines }) {
    return [
      `[Fichier joint : \`${name}\` — ${lines} ligne${lines > 1 ? "s" : ""}, pris sur son ordinateur]`,
      fence(attachedFileLanguage(name) || "texte", maskSecrets(content).replace(/\n$/, ""))
    ].join("\n");
  }

  // ── Brancher le terminal d'un autre ordinateur ─────────────────────────────

  const BRIDGE_FILE = "kenobi-terminal.py";

  // Code printed by the bridge ("8194-<48 hex>"): alone, inside a line copied from its window (spaces, quotes,
  // final dot) or inside a Kenobi link (#terminal=…).
  function parsePairing(value) {
    const match = String(value ?? "").match(/(?:^|[^0-9a-z])(?:terminal=)?(\d{4,5})-([0-9a-f]{48})(?![0-9a-z])/i);
    if (!match) return null;
    const port = Number(match[1]);
    return port >= 1024 && port <= 65535 ? { port, token: match[2].toLowerCase() } : null;
  }

  function clientOs(nav = {}) {
    const platform = String(nav.userAgentData?.platform || nav.platform || "").toLowerCase();
    const agent = String(nav.userAgent || "").toLowerCase();
    if (/win/.test(platform) || /windows/.test(agent)) return "windows";
    if (/mac|iphone|ipad/.test(platform) || /mac os x|macintosh/.test(agent)) return "macos";
    return "linux";
  }

  // The command runs the newest copy in the downloads folder: a browser names the next download
  // « kenobi-terminal(1).py », and an older copy may have been set for another address of Kenobi. On Linux the
  // folder has the name of the system language (Téléchargements, Downloads…): xdg-user-dir gives it.
  const BRIDGE_GLOB = BRIDGE_FILE.replace(".py", "*.py");

  function bridgeLaunch(os) {
    if (os === "windows") {
      return {
        command: `py "$((ls $HOME\\Downloads\\${BRIDGE_GLOB} | sort LastWriteTime | select -Last 1).FullName)"`,
        where: "Dans PowerShell",
        note: "Elle lance la dernière copie téléchargée. Ou double-clique sur le fichier. Il faut Python 3 : python.org, ou « winget install Python.Python.3.13 »."
      };
    }
    if (os === "macos") {
      return {
        command: `python3 "$(ls -t ~/Downloads/${BRIDGE_GLOB} | head -n 1)"`,
        where: "Dans le Terminal",
        note: "Elle lance la dernière copie téléchargée. Python 3 est là sur la plupart des Mac ; sinon python.org."
      };
    }
    return {
      command: `python3 "$(ls -t "$(xdg-user-dir DOWNLOAD)"/${BRIDGE_GLOB} | head -n 1)"`,
      where: "Dans ton terminal",
      note: "Elle trouve ton dossier de téléchargements (Téléchargements, Downloads…) et lance la dernière copie. Enregistré ailleurs : tape python3 et une espace, glisse le fichier dans le terminal, puis Entrée."
    };
  }

  // True when the learner's last message is an error run Kenobi has answered: the next success is the fix.
  function awaitingFix(session) {
    const messages = Array.isArray(session?.messages) ? session.messages : [];
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      const message = messages[index];
      if (normalizeRole(message.role) !== "user") continue;
      return Boolean(message.run && message.auto && message.run.status === "error");
    }
    return false;
  }

  // Coach: while an instruction is open, every run is sent (queued if Kenobi is still answering) and gets a
  // verdict. Without one, an error is sent alone (where to look), a success after a hint too; the rest waits.
  function runDelivery(run, { mode = "coach", busy = false, hasSession = false, task = null, fix = false, language = "python" } = {}) {
    if (mode === "off") return { action: "ignore", reason: "off" };
    if (!hasSession) return { action: "attach", reason: "no-session" };
    if (mode !== "coach") return { action: "attach", reason: "silent" };
    const status = runStatus(run);
    if (status === "interrupted" || isReplCommand(run.cmd)) return { action: "attach", reason: "interrupted" };
    if (task && isAttempt(run, language)) return { action: busy ? "queue" : "send", reason: "exercise" };
    // On the command line every command would be an interruption: Kenobi answers when asked (« Vérifier »).
    if (language === "shell" || language === "autre") return { action: "attach", reason: "shell" };
    if (busy) return { action: "attach", reason: "busy" };
    if (status === "error") return { action: "send", reason: "error" };
    if (fix) return { action: "send", reason: "fixed" };
    return { action: "attach", reason: "success" };
  }

  return {
    escapeHtml,
    inlineText,
    inlineMarkdown,
    textToHtml,
    renderMarkdown,
    parseContent,
    highlightCode,
    languageLabel,
    isOutputLanguage,
    normalizeRole,
    parseBlockFields,
    extractKenobiBlocks,
    localDate,
    addDays,
    parseRevision,
    scheduleReview,
    recordReview,
    dueReviews,
    isValidatedStatus,
    applyBilan,
    completeLesson,
    getCourse,
    getLessons,
    getLesson,
    getProgress,
    normalizeCourseProgress,
    sortedSessions,
    getResumeSession,
    createSession,
    formatDate,
    formatTime,
    formatSeconds,
    isPythonCommand,
    isReplCommand,
    parseTraceback,
    runStatus,
    summarizeRun,
    runLabel,
    runRecord,
    maskSecrets,
    truncateLines,
    fence,
    formatRunForKenobi,
    formatFileForKenobi,
    formatTerminalSnapshot,
    pendingExercise,
    exerciseSubmitted,
    awaitingFix,
    runDelivery,
    VERDICTS,
    verdictOf,
    taskState,
    openTask,
    repeatsOpenTask,
    answersAttempt,
    rememberFacts,
    quizAnswerIndex,
    quizOf,
    messageQuiz,
    quizAnswer,
    latestQuiz,
    formatQuizAnswer,
    isLastMessage,
    replyLabels,
    closingQuestion,
    closedQuestion,
    fallbackReplies,
    courseTools,
    courseShort,
    courseMonogram,
    isCourseCommand,
    isAttempt,
    formatCheckForKenobi,
    stripAccents,
    ATTACH_LIMITS,
    ATTACH_ACCEPT,
    cleanFileName,
    attachedFileLanguage,
    attachmentProblem,
    readAttachment,
    formatAttachedFile,
    parsePairing,
    clientOs,
    bridgeLaunch
  };
});
