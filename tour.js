// Welcome screen and guided tour of Kenobi (TourGuide JS).
// First visit of the web version (or while no model is plugged): the welcome screen says what Kenobi is for and how
// to start, then « C'est parti » runs the tour, which ends by plugging an AI. « Comment ça marche » reopens it.
(function () {
  "use strict";

  const DONE_KEY = "kenobi.tour.v1";
  const REPOSITORY = "https://github.com/motmot3000/Kenobi-tutor-IA";
  let hooks = { mode: () => "browser", connected: () => false, openSettings: () => {}, beforeStart: () => {} };
  let client = null;

  const icon = (name) => `<svg class="icon" aria-hidden="true"><use href="#i-${name}"/></svg>`;
  const mascot = '<img class="tour-mascot" src="assets/kenobi-mascotte.svg" alt="" width="96" height="96">';
  // The first of these elements that is really on screen (on a phone, the navigation is a closed drawer).
  const onScreen = (node) => {
    const box = node && node.getBoundingClientRect();
    return Boolean(box && box.width && box.height && box.right > 0 && box.bottom > 0 &&
      box.left < window.innerWidth && box.top < window.innerHeight);
  };
  const first = (...selectors) => selectors.map((selector) => document.querySelector(selector)).find(onScreen) || null;
  const web = () => hooks.mode() === "browser";

  function steps() {
    const keyWhere = web()
      ? "Ta clé reste dans ce navigateur, chiffrée, et ne part que chez ce fournisseur."
      : "Ta clé reste sur l’ordinateur qui fait tourner Kenobi et ne part que chez ce fournisseur.";
    return [
      {
        title: "Bienvenue sur Kenobi",
        content: `${mascot}<p><strong>Kenobi est ton tuteur IA de code.</strong> Un cours, des leçons courtes, un retour à chaque essai : il te fait pratiquer et ne donne la solution que si tu la demandes.</p><p>Petite visite en six étapes.</p>`,
        target: null
      },
      {
        title: "Tes cours",
        content: "<p>Ici, tes cours. Le <strong>parcours Python</strong> est déjà là pour partir de zéro.</p><p>Tu peux <strong>en ajouter un toi-même</strong> (« Ajouter un cours ») : un document Markdown, ou un cours que ton IA écrit à partir de ce que tu veux apprendre. La bibliothèque officielle des cours de Kenobi arrive bientôt.</p>",
        target: first(".learning-section", "#course-list", ".menu-button")
      },
      {
        title: "Ici, tu parles",
        content: document.querySelector("#message-form")
          ? "<p>Tu écris à Kenobi ici : une question, une réponse, ton code. Souvent, <strong>un clic suffit</strong> : il propose des QCM et des boutons de réponse.</p>"
          : "<p>Ce bouton ouvre la leçon. En bas de la leçon, tu écris à Kenobi : une question, une réponse, ton code. Souvent, <strong>un clic suffit</strong> : il propose des QCM et des boutons de réponse.</p>",
        target: first("#message-form", "[data-action=\"resume-course\"]", "#main-content")
      },
      {
        title: "Ici, il te répond",
        content: "<p>Chaque leçon commence par <strong>un cours court</strong>, puis la pratique : à chaque essai, Kenobi dit « Réussi », « Presque » ou « Pas encore ». Sa mémoire du cours garde ce que tu sais déjà et ce qui reste à revoir.</p>",
        target: first(".message-list .message:last-child", ".chat-start") // nothing yet: the dialog is centred
      },
      {
        title: "Ici, tu l’appelles",
        content: "<p>Parle-lui <strong>à voix haute</strong> : il t’écoute et te répond. Pour la voix, deux options dans Réglages › Voix :</p><ul><li><strong>Whisper sur ton ordinateur</strong> : gratuit, rien ne sort de chez toi ; un petit programme à télécharger et lancer.</li><li><strong>Une clé d’API vocale</strong> : Mistral (Voxtral) ou ElevenLabs.</li></ul>",
        target: first("#call-button")
      },
      {
        title: "Ici, le terminal",
        content: "<p>Pour programmer : un <strong>éditeur</strong> avec le bouton Run et le <strong>vrai terminal</strong> de ton ordinateur. Kenobi voit ce que tu lances et te répond.</p><p>Pour le relier, clique sur « Brancher ce terminal » et suis les étapes : télécharger, lancer, coller le code.</p>",
        target: first("#terminal-toggle", "#terminal-drawer")
      },
      {
        title: "Ici, tu connectes ton IA",
        content: `<p>Kenobi répond avec le modèle que tu branches : choisis un fournisseur (Mistral, OpenAI, Anthropic, Google, OpenRouter…), <strong>colle ta clé d’API</strong> et choisis un modèle. Un modèle rapide suffit.</p><p>${keyWhere}</p>`,
        target: first("#model-button", "#agent-status", ".menu-button")
      }
    ];
  }

  function markDone() {
    try {
      localStorage.setItem(DONE_KEY, new Date().toISOString());
    } catch { /* private mode: the welcome may come back */ }
  }

  function seen() {
    try {
      return Boolean(localStorage.getItem(DONE_KEY));
    } catch {
      return false;
    }
  }

  // The tour; from the welcome screen it skips its own welcome step.
  async function start({ fromWelcome = false } = {}) {
    const Tour = window.tourguide && window.tourguide.TourGuideClient;
    if (!Tour) return false;
    hooks.beforeStart();
    if (client) {
      try { await client.exit(); } catch { /* not running */ }
    }
    const connected = hooks.connected();
    client = new Tour({
      steps: fromWelcome ? steps().slice(1) : steps(),
      nextLabel: "Suivant",
      prevLabel: "Retour",
      finishLabel: connected ? "C’est parti" : "Brancher mon IA",
      closeButton: true,
      dialogClass: "kenobi-tour",
      backdropColor: "rgba(23, 28, 46, 0.62)",
      targetPadding: 6,
      dialogMaxWidth: Math.min(380, window.innerWidth - 24),
      dialogZ: 1000,
      exitOnClickOutside: false,
      keyboardControls: true,
      showStepDots: true,
      showStepProgress: true,
      completeOnFinish: false,
      rememberStep: false,
      autoScroll: true,
      autoScrollSmooth: false
    });
    // The top bar may still move after the first measure (labels filled in by the health check): measure again.
    client.onAfterStepChange(() => setTimeout(() => client && client.updatePositions && client.updatePositions(), 60));
    client.onFinish(() => {
      markDone();
      if (!hooks.connected()) hooks.openSettings("ai");
    });
    client.onAfterExit(() => markDone());
    await client.start();
    setTimeout(() => client && client.updatePositions && client.updatePositions(), 120);
    return true;
  }

  function welcome() {
    const dialog = document.getElementById("welcome-dialog");
    if (!dialog) return start();
    const data = web()
      ? "Pas de compte, pas de serveur Kenobi : tes cours, ta progression et ta clé restent dans ce navigateur."
      : "Pas de compte : tes cours, ta progression et ta clé restent sur l’ordinateur qui fait tourner Kenobi.";
    dialog.innerHTML = `
      <div class="sheet-head welcome-head"><span class="welcome-kicker">${icon("spark")}Tuteur IA de code · open source</span><button class="icon-button" type="button" data-welcome="close" aria-label="Fermer">${icon("close")}</button></div>
      <div class="sheet-body welcome-body" tabindex="-1" autofocus>
        <header class="welcome-hero">
          <img src="assets/kenobi-mascotte.svg" alt="" width="112" height="112">
          <div>
            <h2 id="welcome-title">Kenobi t’apprend à coder</h2>
            <p>Comme un bon prof particulier : <strong>un cours court, puis de la pratique, et un retour à chaque essai</strong>. Il te guide vers la solution sans te la donner, sauf si tu la demandes.</p>
          </div>
        </header>
        <figure class="welcome-shot">
          <img src="assets/welcome-lesson.webp" alt="Une leçon de Kenobi : le plan du cours à gauche, l’objectif de la leçon et l’exercice au centre, le contrat de la leçon à droite" width="960" height="600" loading="lazy">
        </figure>
        <ul class="welcome-points">
          <li>${icon("book")}<span><strong>Un vrai parcours.</strong> Le cours Python complet est inclus, de zéro aux petits projets (86 leçons). Ajoute tes propres cours en Markdown, ou fais-en écrire un par ton IA.</span></li>
          <li>${icon("terminal")}<span><strong>Il te fait pratiquer.</strong> Un éditeur, ton vrai terminal, des QCM d’un clic : à chaque essai, « Réussi », « Presque » ou « Pas encore ».</span></li>
          <li>${icon("phone")}<span><strong>Tu peux lui parler.</strong> Un appel vocal, avec Whisper sur ton ordinateur ou une clé vocale (Mistral, ElevenLabs).</span></li>
          <li>${icon("lock")}<span><strong>Ton IA, tes données.</strong> Tu branches ta propre clé d’API (Mistral, OpenAI, Anthropic, Google, OpenRouter…). ${data}</span></li>
        </ul>
        <h3 class="welcome-subtitle">Pour commencer</h3>
        <ol class="welcome-steps">
          <li><strong>Branche ton IA</strong> : un fournisseur, ta clé d’API, un modèle rapide.</li>
          <li><strong>Ouvre la première leçon</strong> du cours Python : « Reprendre le cours ».</li>
          <li><strong>Apprends</strong> : écris, lance ton code, pose tes questions, à l’écrit ou à voix haute.</li>
        </ol>
        <div class="sheet-actions welcome-actions">
          <a class="text-button" href="${REPOSITORY}" target="_blank" rel="noopener noreferrer">${icon("code")}Le code sur GitHub</a>
          <button class="button ghost" type="button" data-welcome="close">Plus tard</button>
          <button class="button primary" type="button" data-welcome="tour">C’est parti${icon("arrow")}</button>
        </div>
      </div>`;
    if (!dialog.open) dialog.showModal();
    dialog.onclick = (event) => {
      const action = event.target.closest("[data-welcome]")?.dataset.welcome;
      if (!action) return;
      dialog.close();
      if (action === "tour") start({ fromWelcome: true });
      else markDone();
    };
    dialog.onclose = () => { if (!seen()) markDone(); };
    return true;
  }

  // First visit: the web version always welcomes; the self-hosted one only while no model is plugged.
  function maybeStart() {
    if (seen()) return;
    if (!web() && hooks.connected()) return;
    setTimeout(welcome, 400);
  }

  function init(options = {}) {
    hooks = { ...hooks, ...options };
  }

  window.KenobiTour = { init, start, welcome, maybeStart, seen };
})();
