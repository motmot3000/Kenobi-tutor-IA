const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const C = require("../core.js");

const state = JSON.parse(fs.readFileSync(path.join(__dirname, "fixtures/demo-state.json"), "utf8"));
const course = C.getCourse(state);

test("default course exposes its linear progress and current lesson", () => {
  const progress = C.getProgress(course);
  assert.equal(progress.total, 18);
  assert.equal(progress.done, 6);
  assert.equal(progress.percent, 33);
  assert.equal(progress.current.id, "functions-write");
  assert.equal(progress.current.moduleTitle, "Construire avec des fonctions");
});

test("message parser separates prose and fenced code without losing content", () => {
  const blocks = C.parseContent("Voici un exemple :\n\n```python\nprint('bonjour')\n```\n\nÀ toi de jouer.");
  assert.deepEqual(blocks.map((block) => block.type), ["text", "code", "text"]);
  assert.equal(blocks[1].language, "python");
  assert.equal(blocks[1].content, "print('bonjour')");
  assert.equal(blocks[2].content, "À toi de jouer.");
});

test("Python highlighting escapes markup and classifies useful tokens", () => {
  const html = C.highlightCode("def test():\n    return '<script>' # comment", "python");
  assert.match(html, /token-keyword/);
  assert.match(html, /token-string/);
  assert.match(html, /token-comment/);
  assert.doesNotMatch(html, /<script>/);
  assert.match(html, /&lt;script&gt;/);
});

test("inline message text escapes HTML and keeps inline code", () => {
  const html = C.textToHtml("Utilise `print` et pas <b>ceci</b>.");
  assert.match(html, /<code>print<\/code>/);
  assert.match(html, /&lt;b&gt;/);
  assert.doesNotMatch(html, /<b>/);
});

test("markdown renders emphasis, lists and headings without trusting HTML", () => {
  const html = C.renderMarkdown("### Bilan\n\n**Objectif :** écrire `aire_rectangle`.\n\n- un\n- deux\n\n1. prédire\n2. lancer\n\n<img src=x onerror=alert(1)>");
  assert.match(html, /<h4>Bilan<\/h4>/);
  assert.match(html, /<strong>Objectif :<\/strong>/);
  assert.match(html, /<code>aire_rectangle<\/code>/);
  assert.match(html, /<ul><li>un<\/li><li>deux<\/li><\/ul>/);
  assert.match(html, /<ol><li>prédire<\/li><li>lancer<\/li><\/ol>/);
  assert.doesNotMatch(html, /<img/);
  assert.match(html, /&lt;img/);
});

test("markdown keeps snake_case identifiers and sends fences to the code renderer", () => {
  const seen = [];
  const html = C.renderMarkdown("La fonction aire_rectangle_total marche.\n\n```text\n12\n```", {
    code: (content, language) => { seen.push([content, language]); return "<div>code</div>"; }
  });
  assert.doesNotMatch(html, /<em>/);
  assert.deepEqual(seen, [["12", "text"]]);
  assert.equal(C.languageLabel("text"), "Sortie");
  assert.equal(C.languageLabel(""), "Code");
  assert.ok(C.isOutputLanguage("text"));
});

test("tutor roles are recognised whatever the stored name", () => {
  assert.equal(C.normalizeRole("assistant"), "tutor");
  assert.equal(C.normalizeRole("tutor"), "tutor");
  assert.equal(C.normalizeRole("user"), "user");
});

test("Kenobi blocks are extracted, parsed and hidden from the visible text", () => {
  const reply = "On démarre.\n\n```kenobi-lecon\nobjectif: Distinguer print et return\ncritère: Prédire la sortie\ncritere: Écrire une fonction qui renvoie\n```";
  const result = C.extractKenobiBlocks(reply);
  assert.equal(result.text, "On démarre.");
  assert.equal(result.pending, false);
  assert.equal(result.blocks[0].type, "lecon");
  assert.equal(result.blocks[0].fields.objectif, "Distinguer print et return");
  assert.deepEqual(result.blocks[0].fields.critere, ["Prédire la sortie", "Écrire une fonction qui renvoie"]);

  const streaming = C.extractKenobiBlocks("Bilan prêt.\n\n```kenobi-bilan\nstatut: acq");
  assert.equal(streaming.text, "Bilan prêt.");
  assert.equal(streaming.pending, true);
  assert.equal(streaming.blocks.length, 0);
});

test("block fields accept accents, aliases and list continuation", () => {
  const fields = C.parseBlockFields("statut: acquise\nÀ revoir: portée des variables\nacquis:\n- écrit une boucle for\n- lit une erreur\nprochaine étape: module 1\nrésumé: séance courte");
  assert.equal(fields.statut, "acquise");
  assert.deepEqual(fields.a_revoir, ["portée des variables"]);
  assert.deepEqual(fields.acquis, ["écrit une boucle for", "lit une erreur"]);
  assert.equal(fields.prochaine_etape, "module 1");
  assert.equal(fields.resume, "séance courte");
  assert.ok(C.isValidatedStatus("acquise"));
  assert.ok(!C.isValidatedStatus("a-consolider"));
});

test("a validated bilan closes the lesson, moves on and schedules reviews", () => {
  const clone = structuredClone(course);
  const session = C.createSession(clone, "functions-write", new Date("2026-11-08T10:00:00"), "session-bilan");
  clone.sessions.push(session);
  const now = new Date("2026-11-08T11:00:00");
  const result = C.applyBilan(clone, session, {
    statut: "acquise",
    acquis: ["écrit une fonction qui renvoie une valeur"],
    a_revoir: ["différence print / return"],
    revision: ["différence print / return | J+7"],
    prochaine_etape: "Leçon suivante",
    resume: "Fonction écrite seul."
  }, { validate: true, now });
  assert.equal(result.validated, true);
  assert.equal(C.getLesson(clone, "functions-write").status, "done");
  assert.notEqual(result.next.id, "functions-write");
  assert.equal(session.status, "completed");
  assert.equal(session.summary, "Fonction écrite seul.");
  assert.equal(clone.memory.acquired[0], "écrit une fonction qui renvoie une valeur");
  assert.equal(clone.memory.toReview[0], "différence print / return");
  assert.deepEqual(clone.memory.reviews, [{ item: "différence print / return", step: 7, due: "2026-11-15" }]);
  assert.equal(clone.memory.nextFocus, "Leçon suivante");
});

test("reviews move 1 → 7 → 21 → retired and restart at J+1 after a miss", () => {
  const memory = { reviews: [] };
  const now = new Date("2026-11-08T09:00:00");
  C.scheduleReview(memory, "boucle while", 1, now);
  assert.equal(C.dueReviews(memory, new Date("2026-11-09T08:00:00")).length, 1);
  C.recordReview(memory, "boucle while | ok", now);
  assert.equal(memory.reviews[0].step, 7);
  C.recordReview(memory, "Boucle  While | ok", now);
  assert.equal(memory.reviews[0].step, 21);
  C.recordReview(memory, "boucle while | à revoir", now);
  assert.deepEqual(memory.reviews[0], { item: "boucle while", step: 1, due: "2026-11-09" });
  C.recordReview(memory, "boucle while | ok", now);
  C.recordReview(memory, "boucle while | ok", now);
  C.recordReview(memory, "boucle while | ok", now);
  assert.equal(memory.reviews.length, 0);
});

test("progress normalization keeps one current lesson, the first incomplete", () => {
  const clone = structuredClone(course);
  clone.modules[0].lessons[0].status = "todo";
  C.normalizeCourseProgress(clone);
  const lessons = C.getLessons(clone);
  assert.equal(lessons.filter((lesson) => lesson.status === "current").length, 1);
  assert.equal(lessons[0].status, "current");
  assert.equal(lessons[1].status, "done");
});

test("new sessions point to the requested lesson and contain no fake answer", () => {
  const now = new Date("2026-11-03T12:00:00Z");
  const session = C.createSession(course, "functions-write", now, "session-test");
  assert.equal(session.id, "session-test");
  assert.equal(session.lessonId, "functions-write");
  assert.equal(session.status, "active");
  assert.equal(session.startedAt, now.toISOString());
  assert.deepEqual(session.messages, []);
});

test("resume prefers the active session attached to the current lesson", () => {
  const session = C.getResumeSession(course, "functions-write");
  assert.equal(session.id, "session-functions-1");
});

const failingRun = {
  cmd: "cd ~/Kenobi/python/m0-types && python3 exercice.py",
  exit: 1,
  cwd: "~/Kenobi/python/m0-types",
  file: "exercice.py",
  source: "run",
  durationMs: 84,
  code: "nom = input()\nprint(nom.uper())\n",
  output: "Tom\nTraceback (most recent call last):\n  File \"~/Kenobi/python/m0-types/exercice.py\", line 2, in <module>\n    print(nom.uper())\n          ^^^^^^^^\nAttributeError: 'str' object has no attribute 'uper'. Did you mean: 'upper'?"
};

test("only Python commands are seen, and the REPL is told apart", () => {
  for (const command of ["python3 exercice.py", "cd ~/x && python3 a.py", "PYTHONPATH=. python -m pytest", "pip install rich", "./outil.py", "/usr/bin/python3.13 a.py"]) {
    assert.ok(C.isPythonCommand(command), command);
  }
  for (const command of ["python exercice.py", "py exercice.py", "C:\\Python313\\python.exe a.py", "cd ~\\Kenobi\\python\\x; python exercice.py"]) {
    assert.ok(C.isPythonCommand(command), command);
  }
  for (const command of ["ls -la", "cat .env", "git status", "nano exercice.py", "Get-ChildItem"]) {
    assert.ok(!C.isPythonCommand(command), command);
  }
  assert.ok(C.isReplCommand("python3"));
  assert.ok(C.isReplCommand("python3 -q"));
  assert.ok(!C.isReplCommand("python3 exercice.py"));
  assert.ok(!C.isReplCommand("python3 -c 'print(1)'"));
});

test("tracebacks give the error type, message and the line in the learner's file", () => {
  assert.deepEqual(C.parseTraceback(failingRun.output, "exercice.py"), {
    type: "AttributeError",
    message: "'str' object has no attribute 'uper'. Did you mean: 'upper'?",
    line: 2,
    file: "exercice.py"
  });
  const nested = "Traceback (most recent call last):\n  File \"/x/exercice.py\", line 7, in <module>\n    main()\n  File \"/usr/lib/python3.13/json/__init__.py\", line 346, in loads\n    return _default_decoder.decode(s)\njson.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)";
  const parsed = C.parseTraceback(nested, "exercice.py");
  assert.equal(parsed.type, "JSONDecodeError");
  assert.equal(parsed.line, 7);
  const syntax = "  File \"/x/exercice.py\", line 3\n    if x = 2:\n         ^\nSyntaxError: invalid syntax";
  assert.equal(C.parseTraceback(syntax, "exercice.py").line, 3);
  assert.equal(C.parseTraceback("42", "exercice.py"), null);
  assert.equal(C.runStatus({ exit: 0 }), "ok");
  assert.equal(C.runStatus({ exit: 130 }), "interrupted");
  assert.equal(C.runStatus({ exit: 1, output: "Traceback…\nKeyboardInterrupt" }), "interrupted");
  assert.equal(C.runLabel(C.summarizeRun(failingRun), 1), "AttributeError · ligne 2");
});

test("secrets are masked but ordinary exercise values are kept", () => {
  const text = "OPENAI_API_KEY=sk-proj-abcdefghijklmnop1234\ntoken = \"a8f9c7e6d5b4a3928171\"\npassword = input('Mot de passe : ')\nif password == \"1234\":\nAuthorization: Bearer abcdef123456789xyz";
  const masked = C.maskSecrets(text);
  assert.doesNotMatch(masked, /sk-proj/);
  assert.doesNotMatch(masked, /a8f9c7e6d5b4a3928171/);
  assert.doesNotMatch(masked, /abcdef123456789xyz/);
  assert.match(masked, /password = input\('Mot de passe : '\)/);
  assert.match(masked, /"1234"/);
});

test("a run sent to Kenobi carries the real command, code and output within budget", () => {
  const message = C.formatRunForKenobi(failingRun, { reason: "error" });
  assert.match(message, /^\[Exécution — exécution libre, envoyée seule : le programme s'est arrêté sur une erreur\.\]/);
  const attempt = C.formatRunForKenobi(failingRun, { reason: "exercise", attempt: 3, expected: "TOM" });
  assert.match(attempt, /^\[Exécution — tentative n° 3 pour la consigne en cours\. Attendu : « TOM »\.\]/);
  assert.match(message, /Commande : `cd ~\/Kenobi\/python\/m0-types && python3 exercice\.py`/);
  assert.match(message, /code de retour 1 · 0,1 s/);
  assert.match(message, /Erreur repérée : AttributeError à la ligne 2/);
  assert.match(message, /```python\nnom = input\(\)\nprint\(nom\.uper\(\)\)\n\n```/);
  assert.match(message, /```text\nTom\nTraceback/);

  const huge = { ...failingRun, code: "x = 1\n".repeat(4000), output: Array.from({ length: 900 }, (_, index) => `ligne ${index}`).join("\n") };
  const bounded = C.formatRunForKenobi(huge, { reason: "attached", budget: 7000 });
  assert.ok(bounded.length < 7000, `longueur ${bounded.length}`);
  assert.match(bounded, /\[… fichier coupé …\]/);
  assert.match(bounded, /lignes coupées/);
  assert.match(bounded, /ligne 899/);

  const tiny = C.formatRunForKenobi(failingRun, { budget: 500 });
  assert.doesNotMatch(tiny, /```/);
  assert.equal(C.fence("text", "a ``` b"), "````text\na ``` b\n````");
});

test("runs are kept in the conversation as a short record", () => {
  assert.deepEqual(C.runRecord({ ...failingRun, lesson: "m0-types" }), {
    cmd: failingRun.cmd,
    exit: 1,
    file: "exercice.py",
    lesson: "m0-types",
    source: "run",
    durationMs: 84,
    status: "error",
    errorType: "AttributeError",
    errorLine: 2,
    errorMessage: "'str' object has no attribute 'uper'. Did you mean: 'upper'?"
  });
});

test("coach mode answers every run of an open instruction, and only errors outside one", () => {
  const ok = { ...failingRun, exit: 0, output: "TOM" };
  const task = { messageId: "m1", file: "exercice.py", expected: "TOM", attempts: 2 };
  // Outside an instruction: an error is sent alone, a success waits for the next message.
  assert.deepEqual(C.runDelivery(failingRun, { mode: "coach", hasSession: true }), { action: "send", reason: "error" });
  assert.deepEqual(C.runDelivery(ok, { mode: "coach", hasSession: true }), { action: "attach", reason: "success" });
  assert.deepEqual(C.runDelivery(ok, { mode: "coach", hasSession: true, fix: true }), { action: "send", reason: "fixed" });
  assert.equal(C.runDelivery(failingRun, { mode: "coach", hasSession: true, busy: true }).action, "attach");
  // While an instruction is open: every run is an attempt, right or wrong, as many times as wanted.
  assert.deepEqual(C.runDelivery(ok, { mode: "coach", hasSession: true, task }), { action: "send", reason: "exercise" });
  assert.deepEqual(C.runDelivery(failingRun, { mode: "coach", hasSession: true, task }), { action: "send", reason: "exercise" });
  // Kenobi still answering: the run waits its turn instead of being lost.
  assert.deepEqual(C.runDelivery(ok, { mode: "coach", hasSession: true, task, busy: true }), { action: "queue", reason: "exercise" });
  // A run typed by hand counts too; the console or an interrupted program does not.
  assert.equal(C.runDelivery({ ...ok, source: "shell", cmd: "python3 exercice.py" }, { mode: "coach", hasSession: true, task }).action, "send");
  assert.equal(C.runDelivery({ ...ok, source: "shell", cmd: "pip install rich" }, { mode: "coach", hasSession: true, task }).action, "attach");
  assert.equal(C.runDelivery({ ...ok, cmd: "python3" }, { mode: "coach", hasSession: true, task }).action, "attach");
  assert.equal(C.runDelivery({ ...failingRun, exit: 130 }, { mode: "coach", hasSession: true, task }).action, "attach");
  // The learner's choices come first.
  assert.equal(C.runDelivery(failingRun, { mode: "silent", hasSession: true, task }).action, "attach");
  assert.equal(C.runDelivery(failingRun, { mode: "coach", hasSession: false }).action, "attach");
  assert.equal(C.runDelivery(failingRun, { mode: "off", hasSession: true, task }).action, "ignore");
  // On the command line nothing leaves by itself: the learner asks for the check.
  const gitRun = { cmd: "git status", exit: 128, output: "fatal: not a git repository", source: "shell" };
  assert.deepEqual(C.runDelivery(gitRun, { mode: "coach", hasSession: true, task, language: "shell" }), { action: "attach", reason: "shell" });
});

test("files from the learner's computer are checked, read and formatted for Kenobi", () => {
  assert.equal(C.cleanFileName("C:\\projets\\jeu`1`.py"), "jeu1.py");
  assert.equal(C.attachedFileLanguage("jeu.PY"), "python");
  assert.equal(C.attachedFileLanguage("notes.txt"), "texte");
  assert.equal(C.attachedFileLanguage("photo.png"), null);
  assert.ok(C.ATTACH_ACCEPT.split(",").includes(".py"));
  assert.match(C.attachmentProblem({ name: "photo.png", size: 10 }), /fichiers texte/);
  assert.match(C.attachmentProblem({ name: "gros.py", size: 600 * 1024 }), /trop gros/);
  const five = Array.from({ length: 5 }, (_, index) => ({ name: `f${index}.py`, chars: 10 }));
  assert.match(C.attachmentProblem({ name: "a.py", size: 10 }, five), /5 fichiers/);
  assert.equal(C.attachmentProblem({ name: "a.py", size: 10 }), null);
  assert.match(C.readAttachment("bin.py", "a\u0000b").error, /pas un fichier texte/);
  assert.match(C.readAttachment("vide.py", "  \n").error, /vide/);
  assert.match(C.readAttachment("long.py", "x".repeat(60001)).error, /trop long/);
  assert.match(C.readAttachment("b.py", "x".repeat(50000), [{ name: "a.py", chars: 60000 }]).error, /Trop de texte/);
  const { file } = C.readAttachment("jeu.py", "\ufeffimport random\r\nprint('```')\r\napi_key = 'sk4abcdefgh12345678'\r\n");
  assert.deepEqual([file.lines, file.content.includes("\r"), file.content.startsWith("import")], [3, false, true]);
  const text = C.formatAttachedFile(file);
  assert.match(text, /^\[Fichier joint : `jeu\.py` — 3 lignes/);
  assert.match(text, /\n````python\nimport random\n/);
  assert.ok(text.endsWith("\n````"));
  assert.ok(!text.includes("sk4abcdefgh12345678"));
});

test("a pairing code is read alone, from a copied line or from a Kenobi link", () => {
  const token = "a".repeat(40) + "0123abcd";
  assert.deepEqual(C.parsePairing(`  8194-${token}  `), { port: 8194, token });
  assert.deepEqual(C.parsePairing(`http://192.168.1.20:8192/#terminal=8196-${token.toUpperCase()}`), { port: 8196, token });
  assert.deepEqual(C.parsePairing(`Code à coller dans Kenobi :\n  8195-${token}\n`), { port: 8195, token });
  assert.deepEqual(C.parsePairing(`« 8195-${token}ʼ.`), { port: 8195, token });
  assert.deepEqual(C.parsePairing(`"8197-${token}".`), { port: 8197, token });
  assert.equal(C.parsePairing(`8194-${token.slice(1)}`), null);
  assert.equal(C.parsePairing(`8194-${token}f`), null);
  assert.equal(C.parsePairing(`80-${token}`), null);
  assert.equal(C.parsePairing(`99999-${token}`), null);
  assert.equal(C.parsePairing("8194-abc"), null);
  assert.equal(C.parsePairing(""), null);
});

test("the bridge launch command runs the newest copy in the real downloads folder", () => {
  assert.equal(C.clientOs({ userAgentData: { platform: "Windows" } }), "windows");
  assert.equal(C.clientOs({ platform: "Win32" }), "windows");
  assert.equal(C.clientOs({ platform: "MacIntel" }), "macos");
  assert.equal(C.clientOs({ platform: "Linux x86_64", userAgent: "Mozilla/5.0 (X11; Linux x86_64)" }), "linux");
  assert.equal(C.bridgeLaunch("windows").command,
    'py "$((ls $HOME\\Downloads\\kenobi-terminal*.py | sort LastWriteTime | select -Last 1).FullName)"');
  assert.equal(C.bridgeLaunch("macos").command, 'python3 "$(ls -t ~/Downloads/kenobi-terminal*.py | head -n 1)"');
  assert.equal(C.bridgeLaunch("linux").command,
    'python3 "$(ls -t "$(xdg-user-dir DOWNLOAD)"/kenobi-terminal*.py | head -n 1)"');
  for (const os of ["windows", "macos", "linux"]) assert.match(C.bridgeLaunch(os).note, /dernière copie/);
});

test("after a hint on an automatic error, the next success is the fix", () => {
  const session = { messages: [
    { id: "a", role: "user", content: "[Exécution …]", run: { status: "error" }, auto: true },
    { id: "b", role: "tutor", content: "Regarde la ligne 2." }
  ] };
  assert.equal(C.awaitingFix(session), true);
  session.messages.push({ id: "c", role: "user", content: "Je ne vois pas." });
  assert.equal(C.awaitingFix(session), false);
  assert.equal(C.awaitingFix({ messages: [{ role: "user", content: "x", run: { status: "error" } }] }), false);
});

const exerciseMessage = { id: "m1", role: "tutor", content: "Écris un programme qui affiche ton prénom en majuscules.\n\n```kenobi-exercice\nfichier: prenom.py\nattendu: TOM\n```" };
const verdictMessage = (id, verdict, more = "") => ({ id, role: "tutor", content: `Voilà.${more}\n\n\`\`\`kenobi-verdict\nresultat: ${verdict}\n\`\`\`` });

test("an instruction stays open until Kenobi says it is passed, whatever the runs", () => {
  const session = { messages: [exerciseMessage, { id: "m2", role: "user", content: "…", run: { status: "error" } }] };
  assert.deepEqual(C.openTask(session), { messageId: "m1", file: "prenom.py", expected: "TOM", attempts: 1, verdict: null, done: false, open: true });
  assert.equal(C.exerciseSubmitted(session, "m1"), false);
  // A run that ends without error is not a pass: only the verdict closes the instruction.
  session.messages.push(verdictMessage("m3", "pas-encore"), { id: "m4", role: "user", content: "…", run: { status: "ok" } });
  assert.equal(C.openTask(session).attempts, 2);
  assert.equal(C.openTask(session).verdict, "pas-encore");
  session.messages.push(verdictMessage("m5", "presque"), { id: "m6", role: "user", content: "…", attempt: 3 });
  assert.equal(C.openTask(session).attempts, 3);
  session.messages.push(verdictMessage("m7", "réussi"));
  assert.equal(C.openTask(session), null);
  assert.equal(C.pendingExercise(session), null);
  assert.deepEqual(C.taskState(session, "m1"), { messageId: "m1", file: "prenom.py", expected: "TOM", attempts: 3, verdict: "reussi", done: true, open: false });
  assert.equal(C.exerciseSubmitted(session, "m1"), true);
  // Later runs belong to no instruction.
  session.messages.push({ id: "m8", role: "user", content: "…", run: { status: "ok" } });
  assert.equal(C.taskState(session, "m1").attempts, 3);
  const blocks = C.extractKenobiBlocks(session.messages[0].content);
  assert.equal(blocks.text, "Écris un programme qui affiche ton prénom en majuscules.");
  assert.equal(blocks.blocks[0].type, "exercice");
});

test("a verdict written with the next exercise closes the previous one and opens the new", () => {
  const session = { messages: [
    exerciseMessage,
    { id: "m2", role: "user", content: "…", run: { status: "ok" } },
    { id: "m3", role: "tutor", content: "Juste. Suite.\n\n```kenobi-exercice\nfichier: age.py\nattendu: 42\n```\n\n```kenobi-verdict\nresultat: reussi\n```" }
  ] };
  assert.equal(C.taskState(session, "m1").done, true);
  assert.deepEqual(C.openTask(session), { messageId: "m3", file: "age.py", expected: "42", attempts: 0, verdict: null, done: false, open: true });
  // A newer exercise without verdict replaces the one before it.
  session.messages.push({ id: "m4", role: "tutor", content: "Autre idée.\n\n```kenobi-exercice\nattendu: 7\n```" });
  assert.equal(C.taskState(session, "m3").open, false);
  assert.equal(C.openTask(session).messageId, "m4");
  assert.equal(C.openTask({ messages: [{ id: "x", role: "tutor", content: "Bonjour." }] }), null);
});

test("a verdict only counts as the answer to a run, and a repeated block opens nothing", () => {
  const again = (id, more = "") => ({ id, role: "tutor", content: `Regarde le pas.${more}\n\n\`\`\`kenobi-exercice\nfichier: prenom.py\nattendu: tom\n\`\`\`` });
  const session = { messages: [
    exerciseMessage,
    { id: "m2", role: "user", content: "…", run: { status: "error", attempt: 1 }, reason: "exercise" },
    verdictMessage("m3", "presque"),
    { id: "m4", role: "user", content: "Donne-moi la solution." },
    // The solution, the same block again and a verdict nobody asked for: the instruction stays as it was.
    { id: "m5", role: "tutor", content: "Voici.\n\n```kenobi-exercice\nfichier: prenom.py\nattendu: TOM\n```\n\n```kenobi-verdict\nresultat: reussi\n```" },
    { id: "m6", role: "user", content: "C'est quoi upper ?" },
    again("m7")
  ] };
  assert.deepEqual(C.openTask(session), { messageId: "m1", file: "prenom.py", expected: "TOM", attempts: 1, verdict: "presque", done: false, open: true });
  assert.equal(C.answersAttempt(session, "m3"), true);
  assert.equal(C.answersAttempt(session, "m5"), false);
  assert.equal(C.taskState(session, "m5"), null);
  assert.equal(C.repeatsOpenTask(session, "m5", { fichier: "prenom.py", attendu: "TOM" }), true);
  assert.equal(C.repeatsOpenTask(session, "m7", { attendu: "tom" }), true);
  assert.equal(C.repeatsOpenTask(session, "m1", { fichier: "prenom.py", attendu: "TOM" }), false);
  assert.equal(C.repeatsOpenTask(session, "m7", { attendu: "42" }), false);
  // The run that passes, answered with the same block once more: passed, and not opened again.
  session.messages.push({ id: "m8", role: "user", content: "…", run: { status: "ok", attempt: 2 }, reason: "exercise" },
    { id: "m9", role: "tutor", content: "Juste.\n\n```kenobi-exercice\nattendu: TOM\n```\n\n```kenobi-verdict\nresultat: reussi\n```" });
  assert.equal(C.openTask(session), null);
  assert.deepEqual([C.taskState(session, "m1").attempts, C.taskState(session, "m1").done], [2, true]);
  // Asked again later, the same exercise is a new instruction.
  session.messages.push({ id: "m10", role: "user", content: "Encore." }, again("m11"));
  assert.equal(C.openTask(session).messageId, "m11");
  assert.equal(C.repeatsOpenTask(session, "m11", { attendu: "tom" }), false);
});

test("sessions from before the verdicts keep their handed-in exercises", () => {
  const session = { messages: [
    exerciseMessage,
    { id: "m2", role: "user", content: "…", run: { status: "error" }, auto: true, reason: "error" },
    { id: "m3", role: "tutor", content: "Regarde la ligne 1." },
    { id: "m4", role: "user", content: "…", run: { status: "ok" }, auto: true, reason: "exercise" },
    { id: "m5", role: "tutor", content: "Bien vu." }
  ] };
  assert.deepEqual(C.taskState(session, "m1"), { messageId: "m1", file: "prenom.py", expected: "TOM", attempts: 2, verdict: "reussi", done: true, open: false });
  assert.equal(C.openTask(session), null);
});

test("verdicts are read whatever the wording", () => {
  for (const [value, expected] of [["reussi", "reussi"], ["Réussi", "reussi"], ["ok", "reussi"], ["presque", "presque"], ["Presque !", "presque"],
    ["pas-encore", "pas-encore"], ["pas encore", "pas-encore"], ["raté", "pas-encore"], ["", null], ["peut-être", null]]) {
    assert.equal(C.verdictOf({ resultat: value }), expected, value);
  }
  const parsed = C.extractKenobiBlocks("Presque.\n\n```kenobi-verdict\nrésultat: presque\n```");
  assert.deepEqual(parsed.blocks, [{ type: "verdict", fields: { resultat: "presque" } }]);
  assert.equal(C.VERDICTS[C.verdictOf(parsed.blocks[0].fields)].label, "Presque");
});

test("facts Kenobi asks to remember join the course memory once", () => {
  const memory = { facts: ["préfère les exemples de jeux"] };
  const blocks = C.extractKenobiBlocks("Noté.\n\n```kenobi-memoire\nretenir: veut créer des outils pour sa classe\nretenir: Préfère les exemples de jeux\noublier: inconnu\n```").blocks;
  assert.equal(blocks[0].type, "memoire");
  assert.deepEqual(C.rememberFacts(memory, blocks[0].fields), ["veut créer des outils pour sa classe", "Préfère les exemples de jeux"]);
  assert.deepEqual(memory.facts, ["veut créer des outils pour sa classe", "Préfère les exemples de jeux"]);
  C.rememberFacts(memory, { oublier: ["veut créer des outils pour sa classe"] });
  assert.deepEqual(memory.facts, ["Préfère les exemples de jeux"]);
});

const quizReply = (id, more = "") => ({ id, role: "tutor", content: `Qu'affiche \`print(fruits[-1])\` ?${more}\n\n\`\`\`kenobi-qcm\nchoix: \`pomme\`\nchoix: \`figue\`\nchoix: une erreur IndexError\nréponse: 2\nexplication: L'indice -1 désigne le dernier élément.\n\`\`\`` });

test("the course ends with a ready block, and answer buttons are read with their labels", () => {
  const course = "**Ce qu'on va faire** : une liste.\n\n```kenobi-lecon\nobjectif: Lire une liste\ncritere: Afficher un élément\n```\n\n```kenobi-prêt\nbouton: C'est parti !\n```";
  const parsed = C.extractKenobiBlocks(course);
  assert.equal(parsed.text, "**Ce qu'on va faire** : une liste.");
  assert.deepEqual(parsed.blocks.map((block) => block.type), ["lecon", "pret"]);
  assert.deepEqual(C.replyLabels(parsed.blocks[1].fields, ["C’est parti !"]), ["C’est parti !"]);
  assert.deepEqual(C.replyLabels({}, ["C’est parti !"]), ["C’est parti !"]);
  const buttons = C.extractKenobiBlocks("On continue ?\n\n```kenobi-boutons\nbouton: Oui, la suite\nbouton: oui,  la suite\nbouton: Encore un exemple\nbouton: Un\nbouton: De trop\n```").blocks[0];
  assert.equal(buttons.type, "boutons");
  assert.deepEqual(C.replyLabels(buttons.fields), ["Oui, la suite", "Encore un exemple", "Un"]);
  const session = { messages: [{ id: "a", role: "tutor", content: course }] };
  assert.ok(C.isLastMessage(session, "a"));
  session.messages.push({ id: "b", role: "user", content: "C’est parti !" });
  assert.ok(!C.isLastMessage(session, "a"));
  assert.ok(!C.isLastMessage({ messages: [] }, "a"));
});

test("a QCM knows its right option whatever the way Kenobi names it", () => {
  const options = ["`pomme`", "`figue`", "une erreur IndexError"];
  for (const [value, expected] of [["2", 1], ["2.", 1], ["2 — `figue`", 1], ["B", 1], ["b)", 1], ["c", 2], ["`figue`", 1], ["Figue", 1],
    ["une erreur indexerror", 2], ["4", -1], ["", -1], ["la deuxième", -1]]) {
    assert.equal(C.quizAnswerIndex(value, options), expected, value);
  }
  // Numbers as options: the text is found when it is not a position.
  assert.equal(C.quizAnswerIndex("5", ["6", "5", "4"]), 1);
  assert.equal(C.quizAnswerIndex("2 lignes", ["1 ligne", "3 lignes", "2 lignes"]), 2);
  assert.equal(C.quizAnswerIndex("a = 5", ["b = 5", "a = 5"]), 1);
  // A one-letter option is its own text before being a position.
  assert.equal(C.quizAnswerIndex("c", ["c", "une erreur IndexError", "rien"]), 0);
  assert.equal(C.quizAnswerIndex("C)", ["c", "une erreur IndexError", "rien"]), 2);
  assert.equal(C.quizAnswerIndex("d", ["a", "b", "c"]), -1);
});

test("QCM options are mixed the same way every time, and the right one moves between messages", () => {
  const fields = C.extractKenobiBlocks(quizReply("q1").content).blocks[0].fields;
  assert.deepEqual(fields.choix, ["`pomme`", "`figue`", "une erreur IndexError"]);
  const quiz = C.quizOf(fields, "q1");
  assert.deepEqual(C.quizOf(fields, "q1"), quiz);
  assert.equal(quiz.judged, true);
  assert.equal(quiz.explanation, "L'indice -1 désigne le dernier élément.");
  assert.deepEqual([...quiz.options.map((option) => option.text)].sort(), [...fields.choix].sort());
  assert.deepEqual(quiz.options.filter((option) => option.correct).map((option) => option.text), ["`figue`"]);
  const places = new Set(Array.from({ length: 40 }, (_, index) => C.quizOf(fields, `message-${index}`).options.findIndex((option) => option.correct)));
  assert.deepEqual([...places].sort(), [0, 1, 2]);
  assert.equal(C.quizOf({ choix: ["seul"] }, "x"), null);
  assert.equal(C.quizOf({ choix: ["a", "b", "c"], reponse: "peut-être" }, "x").judged, false);
});

test("a QCM is answered once, with the newest one alone waiting for a click", () => {
  const session = { messages: [quizReply("q1")] };
  const quiz = C.messageQuiz(session.messages[0]);
  assert.equal(C.latestQuiz(session), "q1");
  assert.equal(C.quizAnswer(session, "q1"), null);
  assert.equal(C.formatQuizAnswer(quiz, "`figue`"), "[QCM — il a choisi « `figue` » : c'est la bonne réponse. L'interface le lui a dit et a montré l'explication.]");
  assert.equal(C.formatQuizAnswer(quiz, "`pomme`"), "[QCM — il a choisi « `pomme` » : ce n'est pas la bonne réponse, qui était « `figue` ». L'interface le lui a dit et a montré l'explication.]");
  assert.match(C.formatQuizAnswer(C.quizOf({ choix: ["a", "b"] }, "x"), "a"), /ne connaît pas la bonne réponse/);
  const answer = { id: "a1", role: "user", content: "[QCM — …]", qcm: { messageId: "q1", choice: "`pomme`", correct: false } };
  session.messages.push(answer, quizReply("q2", " Et maintenant ?"));
  assert.equal(C.quizAnswer(session, "q1"), answer);
  assert.equal(C.latestQuiz(session), "q2");
  // A QCM changes nothing to the instruction in progress.
  const tasks = { messages: [exerciseMessage, quizReply("q3")] };
  assert.equal(C.openTask(tasks).messageId, "m1");
  assert.equal(C.messageQuiz({ id: "x", role: "tutor", content: "Rien." }), null);
  assert.equal(C.messageQuiz({ ...quizReply("p"), pending: true }), null);
});

test("a yes/no question is told apart from a question to answer in words", () => {
  for (const question of ["Prêt ?", "On continue ?", "Tu veux un autre exemple ?", "Tu veux que je t’explique autrement ?",
    "Est-ce que c’est clair ?", "Tu voulais dire « prédire » ?", "**C’est clair** pour toi ?", "Ok, on passe à la suite ?"]) {
    assert.ok(C.closedQuestion(C.closingQuestion(question)), question);
  }
  for (const question of ["Qu’affiche ce code ?", "Pourquoi `liste[4]` provoque-t-il une erreur ?", "Tu préfères un QCM ou un exercice ?",
    "Avec `age = 15`, quelle ligne s’affiche ?", "Tu vois pourquoi ?", "Combien de lignes affiche `range(1, 10, 4)` ?"]) {
    assert.ok(!C.closedQuestion(C.closingQuestion(question)), question);
  }
  assert.equal(C.closingQuestion("Bien vu : `x.append(3)` ajoute **à la fin**.\n\nTu veux un autre exemple ?"), "Tu veux un autre exemple ?");
  assert.equal(C.closingQuestion("Voici :\n```python\nprint('ok ?')\n```"), "");
  assert.equal(C.closingQuestion("Prêt ? Alors lance."), "");
});

test("answers in one click are offered when Kenobi asks a yes/no question without buttons", () => {
  const reply = (content, extra = {}) => ({ id: "r", role: "tutor", content, ...extra });
  const offer = (message, more = {}) => C.fallbackReplies({ status: "active", messages: [{ id: "u", role: "user", content: "?" }, message], ...more }, message);
  assert.deepEqual(offer(reply("Bien vu. Tu veux un autre exemple ?")), ["Oui", "Non"]);
  assert.deepEqual(offer(reply("Qu’affiche ce code ?\n\n```python\nprint(2 * 3)\n```\n\nÀ ton avis, quelle valeur ?")), []);
  assert.deepEqual(offer(reply("Voilà la différence entre `print` et `return`.")), ["On continue"]);
  // Kenobi's own buttons, a QCM, an instruction or the start of practice: nothing more to offer.
  assert.deepEqual(offer(reply("On continue ?\n\n```kenobi-boutons\nbouton: Oui\nbouton: Encore un exemple\n```")), []);
  assert.deepEqual(offer(quizReply("r")), []);
  assert.deepEqual(offer(reply("**Écris une boucle**.\n\n```kenobi-exercice\nattendu: 1, 2 et 3\n```")), []);
  assert.deepEqual(offer(reply("Des questions ?\n\n```kenobi-pret\nbouton: C'est parti !\n```")), []);
  assert.deepEqual(offer(reply("Acquis.\n\n```kenobi-bilan\nstatut: acquise\n```")), []);
  assert.deepEqual(offer(reply("Encore fragile.\n\n```kenobi-bilan\nstatut: a-consolider\n```")), ["On continue"]);
  // While an instruction is open, he works in the editor: only a yes/no question gets its buttons.
  const working = { status: "active", messages: [exerciseMessage, { id: "u", role: "user", content: "C’est quoi range ?" }] };
  const answer = reply("`range` produit une suite de nombres.");
  assert.deepEqual(C.fallbackReplies({ ...working, messages: [...working.messages, answer] }, answer), []);
  const hint = reply("Tu veux un indice ?");
  assert.deepEqual(C.fallbackReplies({ ...working, messages: [...working.messages, hint] }, hint), ["Oui", "Non"]);
  // Only under the last, complete message of an open session.
  const message = reply("Tu veux un autre exemple ?");
  assert.deepEqual(C.fallbackReplies({ status: "active", messages: [message, { id: "u2", role: "user", content: "Oui" }] }, message), []);
  assert.deepEqual(offer(message, { status: "completed" }), []);
  for (const flag of ["pending", "error", "stopped"]) assert.deepEqual(offer(reply("Tu veux un autre exemple ?", { [flag]: true })), [], flag);
});

test("a course says what it works with: terminal, language, default file", () => {
  assert.deepEqual(C.courseTools({ id: "python" }), { label: "Python", file: "exercice.py", watch: "tes commandes Python", runs: true, terminal: true, language: "python" });
  assert.equal(C.courseTools({ id: "autre-cours" }).terminal, false);
  const git = C.courseTools({ tools: { terminal: true, language: "shell", file: "" } });
  assert.deepEqual([git.terminal, git.language, git.file, git.runs], [true, "shell", "exercice.sh", false]);
  assert.deepEqual([C.courseTools({ tools: { terminal: true, language: "cobol" } }).language, C.courseTools({ tools: { terminal: false } }).language], ["autre", ""]);
  assert.equal(C.courseTools({ tools: { terminal: true, language: "javascript", file: "app.js" } }).file, "app.js");
  assert.deepEqual([C.courseMonogram({ shortTitle: "Python" }), C.courseMonogram({ title: "Git et GitHub" }), C.courseMonogram({ shortTitle: "Réseaux" }), C.courseMonogram({})], ["Py", "Ge", "Re", "Co"]);
});

test("what Kenobi follows in the terminal depends on the course", () => {
  assert.ok(C.isCourseCommand("python3 exercice.py", "python"));
  assert.ok(!C.isCourseCommand("git status", "python"));
  for (const command of ["node exercice.js", "npm test", "./outil.mjs", "npx vitest"]) assert.ok(C.isCourseCommand(command, "javascript"), command);
  assert.ok(!C.isCourseCommand("python3 a.py", "javascript"));
  assert.ok(C.isCourseCommand("git status", "shell"));
  assert.ok(C.isCourseCommand("gcc main.c && ./a.out", "autre"));
  assert.ok(!C.isCourseCommand("ls", ""));
  assert.ok(C.isReplCommand("node"));
  assert.ok(!C.isReplCommand("node exercice.js"));
  const check = C.formatCheckForKenobi("$ git status\nOn branch main\ntoken ghp_abcdefghijklmnopqrstuvwxyz123456", { attempt: 2, expected: "un commit" });
  assert.match(check, /^\[Exécution — tentative n° 2 pour la consigne en cours\. Attendu : « un commit »\.\]/);
  assert.match(check, /On branch main/);
  assert.doesNotMatch(check, /ghp_abcdefghij/);
});
