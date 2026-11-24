# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

HTML, CSS and JavaScript without framework or build step. Two backends with the same API: in the page
(`browser/`, for the web version on static hosting) or a small Python server (standard library only, `server.py`).
Only the local voice adds packages, in a separate Python environment.

## Users

People learning computer science or programming with an AI tutor, on their own computer, in short work sessions.
They may start from zero. They bring their own AI (an API key, or a local model).

## Product purpose

Kenobi is an AI tutor specialised in computer science and code. It brings together a course, its plan, its sessions,
the conversation with the tutor, a terminal and a voice call. It works with the AI model the learner plugs in. Coming
back to a course shows the progress and lets the tutor pick up what was done, understood or left fragile.

## Positioning

The conversation belongs to a lasting course, not to an isolated chat. The tutor is not a code generator: it makes
the learner practise, answers every attempt and gives the solution only when asked. The progress and memory of each
course stay readable between sessions and exportable as Markdown.

## Operating context

- Web version: a public static site; data in the browser (IndexedDB), keys encrypted in the browser, AI and voice
  providers called directly from the page. Local helpers (terminal bridge, voice bridge) run on the learner's computer
  on request.
- Self-hosted version: `server.py` on the learner's computer or home server; keys in a private file; usable from the
  local network.

## Capabilities and constraints

- At the opening of a course, the plan and the way back in are shown before the conversation.
- Several courses, each with its plan, sessions and memory. A course is added from a Markdown document: the plugged
  model extracts modules, lessons, goals and tools; without a model, the headings are enough.
- The domain is computer science and code: Python, JavaScript, command line, other languages, or theory without
  terminal. A full Python course ships with the application.
- Model plugged with an API key (Mistral, OpenAI, Anthropic, Google, OpenRouter, DeepSeek, Groq, Ollama, any
  OpenAI-compatible service). No account. Keys are never shown back.
- The plugged model stays visible in the top bar; without a model, the interface says so and leads to the settings,
  without simulating answers.
- One active conversation at a time, organised in sessions within the course.
- Kenobi is direct: the answer first, few words, the important information and the instruction in bold.
- Each run of an instruction gets feedback: « Réussi », « Presque » or « Pas encore », with a nudge. No solution or
  big hint without an explicit request. One-click answers (multiple-choice questions, buttons) whenever possible.
- Real voice call: animated head of Kenobi, listening, thinking, speaking, interruption by voice, a filler when the
  answer takes time. Local voice (Whisper + Piper) or Voxtral (Mistral) or ElevenLabs by API key.
- Memory of each course in Markdown: skills, points to review, spaced reviews, facts kept by the tutor, thread of
  sessions.
- Integrated terminal: editor with Run, real terminal of the learner's computer, runs seen by Kenobi according to the
  chosen mode. Kenobi reads, it never types.
- Guided tour on the first visit: courses, conversation, call, terminal, connecting an AI.
- Clear, calm interface, usable on desktop and mobile (no terminal on phones).
- Mascot: a small, kind robot tutor, good at programming, in the logo, the chat and the call.

## Brand commitments

Displayed name: Kenobi. Palette: midnight blue `#171c2e`, cream `#fff7f0`, orange `#ffa959`, blue `#376ab7`;
typefaces Sora and Inter, JetBrains Mono for code.

## Product principles

- Progress stays visible without leaving the conversation.
- Coming back starts with the useful context, not with a history to read again.
- Code is first-class learning content: readable, copyable and tied to the lesson.
- The learner does, the tutor guides: feedback on every attempt, help only on request.
- The interface always says what is really plugged: model, voice, terminal.
- Keys and data stay with the learner; what goes to a provider is said before it is sent.
- The memory stays portable and readable outside the application.

## Accessibility and inclusion

Keyboard navigation, readable contrasts, explicit labels, comfortable touch targets, no text under 12 px, and
reduced motion respected.
