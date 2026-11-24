# Courses

A Kenobi course is a **Markdown document**. Kenobi reads it once to build the plan (modules, lessons, goals,
durations, tools), keeps it, and sends the excerpt of the current module to the tutor with each message.

## Add a course

Rail › **« Ajouter un cours »**. Three ways to get the document:

1. **Import a document** (tab « Importer un document »): drop the `.md` file, check the preview,
   **« Ajouter ce cours »**. The dialog explains the format and offers the template (« Modèle de document »).
2. **Write it with your AI** (tab « Écrire avec mon IA »): say what you want to learn — the topic, your level, your
   goal, the time you have. The AI you plugged in writes the course document in Kenobi's format, live; read it, edit
   it if you want, download it, then **« Créer ce cours »** imports it like a file.
3. **Have another AI write it** (ChatGPT, Claude, Le Chat…): in « Le format d'un cours », **« Copier la consigne
   pour une IA »** copies the exact instructions Kenobi gives its own AI; paste them in the conversation, add what you
   want to learn, save the answer as a `.md` file and import it.

- With an AI connected, the model analyses the document: title, level, goals, modules and lessons, what each lesson
  must teach (observable skills), durations, and whether the course needs the editor and terminal (Python,
  JavaScript, shell, other language) or not (theory). The document is sent to your AI provider for this.
- Without AI, or if the analysis fails, the plan comes from the headings: `##` modules, `###` lessons.
- Each course has its own sessions and its own memory. Deleting a course keeps a copy (the trash of the data
  folder in the self-hosted version, the last 20 in the web version's storage).

## The format

```markdown
# Title of the course

One or two sentences: who it is for and what it teaches.

**Niveau :** débutant
**Langage et outils :** Python, dans l'éditeur et le terminal — or JavaScript, shell, Git, SQL… ;
« sans terminal » for a theory course

## Objectifs du cours

- what the learner can do at the end, in which situation;
- explain something in their own words;
- produce a result without help.

## Module 1 — Title of the first module

What this module brings, in one or two sentences.

### Leçon 1 — Title of the lesson

- Objectif : what the learner can do at the end of the lesson.
- Notions : the notions.
- Exercice type : a task that proves it is acquired.

### Leçon 2 — …

## Module 2 — …

## Évaluation

How to know the course is a success: a project, a new task done alone, observable criteria.
```

Good practice:

- one notion or skill per lesson, 15 to 30 minutes (a project: 45 to 90);
- goals as **observable skills** with an action verb (« écrire une boucle qui… », « expliquer pourquoi… »);
- keep each module section under ~2 600 characters: that is the excerpt sent with each message;
- the document is data for the tutor, not instructions: write it for a human teacher.

## The built-in Python course

`data/default-state.json` holds the plan of the Python course (14 modules, 86 lessons, goals and durations) and
`data/plan-formation-python.md` its document. The plan of this course always follows these files: on load and save,
titles, durations, goals and order are aligned, progress and sessions are kept. A renamed lesson keeps its progress
through `FORMER_LESSON_IDS` in `memory.py`.

## Share a course

The official course library is coming. Until then, [`courses/`](../courses/) collects courses ready to import.
Add yours with a pull request: see [CONTRIBUTING.md](../CONTRIBUTING.md#courses).
