# Kenobi's design

The interface speaks French: labels quoted below (« … ») are the words on screen.

## Reference

The visual language: midnight blue, cream, orange and blue; Sora and Inter typefaces, JetBrains Mono for code.

## Palette

| Role | Value | Use |
|---|---|---|
| Midnight blue | `#171c2e` | Terminal, main text, code editor |
| Slate | `#242c46` | Navigation rail: one step lighter than the terminal, so the two do not merge |
| Cream | `#fff7f0` | Page background |
| Orange | `#ffa959` | Landmarks, progress, brand |
| Blue | `#376ab7` | Main actions, selection, links |
| Light blue | `#dfeafe` | Selected states |
| Secondary text | `#333a4c` | Body content |
| Muted text | `#545b6c` | Metadata, help (6.4:1 on the cream background) |

Green `#78ba87` is kept for success. Surfaces use thin outlines, with no glass, glow or decorative texture. Three
tints give the verdict of a run: pale green (« Réussi », done), pale orange (« Presque », almost), pale red
(« Pas encore », not yet).

## Typography

- Sora, served locally from `assets/fonts/`, for headings, the brand and actions.
- Inter, local, for content and controls.
- JetBrains Mono, local (OFL), for code, the editor, the terminal and file paths: the same digits and symbols on
  every computer.
- A scale of nine sizes (`--text-2xs` 12 px … `--text-3xl` 28 px). Nothing under 12 px. Interface text 15 px,
  conversation messages 16 px, code 13.5 px.

## Layout

Room goes to the work: the conversation, the input and the terminal take the centre; both side panels stay but fold.

- Slate rail on the left (226 px): the course (plan, sessions, memory), the list of courses with their progress,
  « Ajouter un cours », then the plugged model and the settings. It folds into a 60 px column of icons (course
  monograms), set apart from the terminal by its tint and a dark rule.
- Top bar, 50 px: course › view or session, save state, plugged model (green dot, monospaced name; orange
  « Brancher un modèle » when there is none), call, lesson panel toggle.
- Centre: the plan by default, the conversation in its place during a session. The conversation takes the full
  height, with no repeated title: the session is named in the bar. Column of 940 px at most (1,040 px when the lesson
  panel is hidden).
- One-line input: tools as icons on the left, text, « Envoyer »; it grows with the text.
- Right panel (284 px, can be hidden): goal and criteria of the lesson, reviews, skills acquired, difficulties, what
  Kenobi remembered, next step.
- « Terminal » drawer at the bottom of the work area: a 38 px midnight-blue bar forming an L with the rail; open, it
  shares the space with the conversation (resizable, can be enlarged). A conversation read to the bottom stays there
  when the drawer opens or grows. A course without a terminal has no drawer.
- The folded rail and the hidden panel are remembered per browser (`kenobi.layout.v1`).
- Under 960 px, the context becomes a sliding panel.
- Under 760 px, the rail slides too and the terminal disappears.
- Under 560 px, « Envoyer » and « Document » shrink to their icon, the input holds on one line and the call opens at
  the bottom of the screen.

## Components

- Buttons with an 8 px radius, blue main action, light outlined secondary action.
- Plan modules are native disclosures, lessons are round checkboxes.
- Kenobi's messages span the full width with an avatar; the learner's messages are cream insets aligned right, to see
  at a glance who speaks.
- Midnight-blue code blocks with language, highlighting and an explicit copy button; console output on cream,
  labelled « Sortie », with no copy button.
- Lesson cards: goal (very pale blue background, light blue outline) and review (green outline and dot when the skill
  is acquired, orange dot otherwise), with the validation button inside the card. No thick side stripe.
- Task and verdict: a white « Consigne » card with what is expected and the number of runs, green once done; Kenobi's
  answer to a run starts with a « Réussi » (green), « Presque » (orange) or « Pas encore » (red) badge, repeated in
  the terminal bar. A task the model repeats does not produce a second card.
- Quick help as chips above the input; a single scrolling line on mobile. They step aside while « C'est parti ! »
  waits under the opening lesson.
- Opening lesson: a message from Kenobi in five parts with bold subheadings (what we will do, what it is for, an
  example, what to remember, today's goal), code and output apart, then the « Objectif de la leçon » card and two
  buttons: « C'est parti ! » (blue main action, arrow) and « J'ai une question » (secondary, question mark icon,
  opens the input).
- One-click answers: very pale blue chips (`#f3f7fe`, outline `#c9d9f4`, dark blue text) under Kenobi's last message
  only; they disappear as soon as one answers, and the answer becomes a learner message. The same chips carry « Oui »
  and « Non » under a closed question without buttons, « On continue » under an answer that asks nothing.
- The conversation reads from the bottom: it stays there at each answer, at each click in the input or on a message,
  when the terminal opens; if one scrolls up to read, it no longer moves until the next message is sent.
- Multiple-choice question: very pale blue card (`#f8faff`, outline `#c9d9f4`), header « QCM · une seule bonne
  réponse » with the question mark, three stacked full-width choices (46 px at least, round check, monospaced code).
  On click, without waiting for Kenobi: the right choice turns solid green and checked, a wrong one pale red with a red
  cross, the right answer is circled in green, the others fade; then a green « Bonne réponse. » banner or a pale
  orange « La bonne réponse : … » one, with the explanation. The choice is sent as a learner message.
- Call: a non-modal 324 px panel under the top bar, which leaves the conversation and the terminal usable. Kenobi's
  head in a white disc, state rings, the state in one word (« Écoute », « Réfléchit », « Parle »), a caption of the
  sentence being said, « Couper le micro », « Interrompre », « Raccrocher ». It folds without hanging up: the call
  button, blue during the call, shows the state and the duration.
- Settings, course import, confirmation: 640 px dialogs, title, tabs underlined in blue (« Modèle d'IA », « Voix »,
  « Profil »), 40 px fields, help under the field, main action at the bottom right, test status on the left. Voice
  engines as single-choice cards.
- « Ajouter un cours »: two tabs. « Importer un document »: a dashed drop zone for the course document, a fold-out
  « Le format d'un cours » with a sample of the format and « Copier la consigne pour une IA », then a preview of the
  analysed course (plan, tools, level, goals, modules) before adding it. « Écrire avec mon IA »: what the learner
  wants to learn, then the document as the model writes it, live, in a monospaced field the learner can edit,
  with « Recommencer », « Télécharger le .md » and « Créer ce cours ».
- Welcome screen (first visit): a 780 px dialog with the mascot, « Kenobi t'apprend à coder », a picture of a lesson,
  four points in two columns (a real course, practice, voice, your AI and your data), the three steps to start, and
  « Plus tard » / « C'est parti » (blue, arrow), which starts the guided tour. « Comment ça marche » in the rail
  reopens it.
- Guided tour (TourGuide JS): white dialogs with a highlighted target, step dots and « Suivant » / « Retour »; its
  last step points at the model button and ends on « Brancher mon IA » when no model is plugged.
- « Bien démarrer » card at the top of the plan: pale orange background, numbered steps (plug your AI, start the first
  lesson, then optional: add a course, talk aloud), each with its action on the right; a done step turns into a green
  check. It goes away once a model is plugged and a first lesson is open, or with « Masquer ».
- Terminal: midnight-blue surfaces (`#171c2e`, pane headers `#20283f`), JetBrains Mono (DejaVu Sans Mono as
  fallback). Editor on the left (line numbers, active line in orange, highlighting from `core.js`), terminal on the
  right (Kenobi palette or Konsole's Breeze). Run in blue, « Arrêter » in brick red while a program runs, with an
  orange line running along the terminal pane's header. Status chip of the last run in the bar: green done, red
  error, with what Kenobi received; once it has answered, an orange « Kenobi a répondu » chip scrolls the
  conversation to its answer, highlighted for a moment.
- Plug another computer: an orange « Brancher ce terminal » button in the bar while the page has no terminal. In the
  drawer, a midnight-blue panel in three steps numbered in orange (download, run, paste the code), Windows · macOS ·
  Linux tabs pre-selected from the browser, a command to copy on a darker background, a green padlock before the
  security note. « Pont pas lancé » and « Code à renouveler » keep only the useful steps.
- Error on its line: after a failed Run, a pale red band on the line, a red dot in the margin and the message in
  italics at the end of the line, cleared at the next keystroke.
- « Run » card in the conversation: midnight-blue header with the command and a status chip, error message in red,
  « Ce que Kenobi a reçu » can be unfolded. For an error in the open lesson, the chip links to the line in the
  editor.
- « ⋯ » menu of the terminal: keyboard shortcuts (drawn keys), unplug the computer. The drawer's menus open towards
  the side that has room.
- Closed terminal = off: the bar says « Désactivé · Kenobi ne regarde pas », hollow dot; Kenobi's mode and « Je passe
  sur mon terminal » (light outline, orange icon) appear only when the drawer is open. Switching to one's own
  terminal leaves a dotted line in the middle of the conversation, then Kenobi's answer.
- Attached files: a « Fichier » tool (paper clip) in the input; while dragging, the input turns dashed blue with
  « Dépose tes fichiers… »; chips « name · N lines »; in the conversation, a white card listing the files (blue icon,
  monospaced name, number of lines) with « Ce que Kenobi a reçu ».
- Mascot: a small tutor robot, dark screen and amber smile, a mentor's cream cape, a book on its chest, a `>>>`
  laptop. The head is the rail's logo (36 px), the favicon and Kenobi's avatar (32 px); while it thinks, it sways
  next to a white bubble with three orange dots. The full body opens an empty session. In the call, the animated head
  replaces the microphone: blinks, head tilted while listening, eyes up while thinking, mouth driven by the voice
  level (`--voice`). Vector SVG drawn from an illustration made for the project.
- Attachments above the input: cream chips with « Envoyer seule » and a cross to remove them.

## Interaction and accessibility

Going from the plan to the conversation uses View Transitions when available. Reduced motion is respected. Controls
have labels, a visible focus and suitable touch targets. Content stays visible without animation.

## Visual evidence

Review captures (`.impeccable/review/`) stay on the development computer and are not published. Public screenshots:
`docs/images/`.
