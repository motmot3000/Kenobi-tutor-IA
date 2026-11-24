// Small Python editor for the terminal drawer: a transparent textarea over the highlighted code.
// No dependency: highlighting comes from core.js; undo stays native thanks to insertText.
(function (root) {
  "use strict";

  const C = root.KenobiCore;
  const INDENT = 4;

  function create(host, { onChange = () => {}, onRun = () => {}, onSave = () => {}, label = "Code", placeholder = "" } = {}) {
    host.innerHTML = `
      <div class="editor-gutter" aria-hidden="true"><div class="editor-gutter-lines"></div></div>
      <div class="editor-surface">
        <pre class="editor-highlight" aria-hidden="true"><code></code></pre>
        <textarea class="editor-input" spellcheck="false" autocomplete="off" autocapitalize="off" autocorrect="off" wrap="off"></textarea>
      </div>`;
    const gutter = host.querySelector(".editor-gutter-lines");
    const surface = host.querySelector(".editor-surface");
    const highlight = host.querySelector(".editor-highlight");
    const code = highlight.querySelector("code");
    const input = host.querySelector(".editor-input");
    // Error from the last run, drawn on its line (like an "error lens"): band, gutter mark and message.
    const errorBand = document.createElement("div");
    const errorNote = document.createElement("div");
    const ruler = document.createElement("span");
    errorBand.className = "editor-error-band";
    errorNote.className = "editor-error-note";
    ruler.className = "editor-ruler";
    errorBand.hidden = true;
    errorNote.hidden = true;
    ruler.setAttribute("aria-hidden", "true");
    ruler.textContent = "0".repeat(40);
    surface.insertBefore(errorBand, input);
    surface.insertBefore(errorNote, input);
    surface.appendChild(ruler);
    input.setAttribute("aria-label", label);
    input.placeholder = placeholder;
    let language = "python";
    let lineCount = 0;
    let activeLine = -1;
    let escaped = false;
    let diagnostic = null;

    function paint() {
      const text = input.value;
      code.innerHTML = `${language === "python" ? C.highlightCode(text, "python") : C.escapeHtml(text)}\n`;
      const lines = text.split("\n").length;
      if (lines !== lineCount) {
        lineCount = lines;
        gutter.innerHTML = Array.from({ length: lines }, (_, index) => `<span>${index + 1}</span>`).join("");
        activeLine = -1;
      }
      markActiveLine();
      sync();
    }

    function metrics() {
      const style = getComputedStyle(input);
      return {
        lineHeight: parseFloat(style.lineHeight) || 20,
        top: parseFloat(style.paddingTop) || 0,
        left: parseFloat(style.paddingLeft) || 0,
        char: ruler.getBoundingClientRect().width / 40 || 8
      };
    }

    function placeDiagnostic() {
      gutter.querySelector(".is-error")?.classList.remove("is-error");
      const lines = input.value.split("\n");
      if (!diagnostic || diagnostic.line < 1 || diagnostic.line > lines.length) {
        errorBand.hidden = true;
        errorNote.hidden = true;
        return;
      }
      const { lineHeight, top, left, char } = metrics();
      const y = top + (diagnostic.line - 1) * lineHeight - input.scrollTop;
      const width = lines[diagnostic.line - 1].replace(/\t/g, "    ").length * char;
      errorBand.style.cssText = `height:${lineHeight}px;transform:translateY(${y}px)`;
      errorNote.style.cssText = `height:${lineHeight}px;line-height:${lineHeight}px;transform:translate(${left + width + 3 * char - input.scrollLeft}px,${y}px)`;
      errorNote.textContent = diagnostic.text;
      errorBand.hidden = false;
      errorNote.hidden = false;
      const mark = gutter.children[diagnostic.line - 1];
      if (mark) {
        mark.classList.add("is-error");
        mark.title = diagnostic.text;
      }
    }

    function sync() {
      highlight.scrollTop = input.scrollTop;
      highlight.scrollLeft = input.scrollLeft;
      gutter.style.transform = `translateY(${-input.scrollTop}px)`;
      placeDiagnostic();
    }

    function markActiveLine() {
      const index = input.value.slice(0, input.selectionStart).split("\n").length - 1;
      if (index === activeLine) return;
      gutter.children[activeLine]?.classList.remove("is-active");
      gutter.children[index]?.classList.add("is-active");
      activeLine = index;
    }

    function changed() {
      diagnostic = null;
      paint();
      onChange(input.value);
    }

    // insertText keeps Ctrl+Z working; setRangeText is the fallback.
    function replaceRange(start, end, text) {
      input.focus();
      input.setSelectionRange(start, end);
      const done = text ? document.execCommand("insertText", false, text) : document.execCommand("delete");
      if (!done) {
        input.setRangeText(text, start, end, "end");
        changed();
      }
    }

    function lineBounds(start, end) {
      const value = input.value;
      const from = value.lastIndexOf("\n", start - 1) + 1;
      const last = end > start && value[end - 1] === "\n" ? end - 1 : end;
      const stop = value.indexOf("\n", last);
      return [from, stop === -1 ? value.length : stop];
    }

    function indent() {
      const { selectionStart: start, selectionEnd: end, value } = input;
      if (start === end || !value.slice(start, end).includes("\n")) {
        const column = start - (value.lastIndexOf("\n", start - 1) + 1);
        replaceRange(start, end, " ".repeat(INDENT - (column % INDENT)));
        return;
      }
      const [from, to] = lineBounds(start, end);
      const lines = value.slice(from, to).split("\n");
      const text = lines.map((line) => (line ? " ".repeat(INDENT) + line : line)).join("\n");
      replaceRange(from, to, text);
      input.setSelectionRange(start + (lines[0] ? INDENT : 0), end + text.length - (to - from));
    }

    function dedent() {
      const { selectionStart: start, selectionEnd: end, value } = input;
      const [from, to] = lineBounds(start, end);
      let first = 0;
      let total = 0;
      const text = value.slice(from, to).split("\n").map((line, index) => {
        const count = Math.min(INDENT, line.match(/^ */)[0].length);
        if (index === 0) first = count;
        total += count;
        return line.slice(count);
      }).join("\n");
      if (!total) return;
      replaceRange(from, to, text);
      if (start === end) input.setSelectionRange(Math.max(from, start - first), Math.max(from, start - first));
      else input.setSelectionRange(Math.max(from, start - first), end - total);
    }

    function newline() {
      const { selectionStart: start, selectionEnd: end, value } = input;
      const before = value.slice(value.lastIndexOf("\n", start - 1) + 1, start);
      let spaces = before.match(/^ */)[0];
      if (/:\s*(#.*)?$/.test(before)) spaces += " ".repeat(INDENT);
      replaceRange(start, end, `\n${spaces}`);
    }

    function softBackspace(event) {
      const { selectionStart: start, selectionEnd: end, value } = input;
      if (start !== end) return;
      const before = value.slice(value.lastIndexOf("\n", start - 1) + 1, start);
      if (!before || /[^ ]/.test(before)) return;
      event.preventDefault();
      replaceRange(start - (before.length % INDENT || INDENT), start, "");
    }

    function toggleComment() {
      const { selectionStart: start, selectionEnd: end, value } = input;
      const [from, to] = lineBounds(start, end);
      const lines = value.slice(from, to).split("\n");
      const filled = lines.filter((line) => line.trim());
      if (!filled.length) return;
      const commented = filled.every((line) => /^\s*#/.test(line));
      const margin = Math.min(...filled.map((line) => line.match(/^ */)[0].length));
      const text = lines.map((line) => {
        if (!line.trim()) return line;
        return commented ? line.replace(/^(\s*)# ?/, "$1") : `${line.slice(0, margin)}# ${line.slice(margin)}`;
      }).join("\n");
      replaceRange(from, to, text);
      if (start === end) {
        const cursor = Math.max(from, start + text.length - (to - from));
        input.setSelectionRange(cursor, cursor);
      } else input.setSelectionRange(from, from + text.length);
    }

    input.addEventListener("keydown", (event) => {
      if (event.isComposing) return;
      const mod = event.ctrlKey || event.metaKey;
      if (event.key === "Escape") { escaped = true; return; }
      if (event.key === "Tab" && !mod && !event.altKey) {
        if (escaped) { escaped = false; return; }
        event.preventDefault();
        if (event.shiftKey) dedent();
        else indent();
        return;
      }
      escaped = false;
      if (mod && event.key === "Enter") { event.preventDefault(); onRun(); return; }
      if (mod && event.key.toLowerCase() === "s") { event.preventDefault(); onSave(); return; }
      if (mod && event.key === "/") { event.preventDefault(); toggleComment(); return; }
      if (event.key === "Enter" && !mod && !event.shiftKey && !event.altKey) { event.preventDefault(); newline(); return; }
      if (event.key === "Backspace" && !mod && !event.altKey) softBackspace(event);
    });
    input.addEventListener("input", changed);
    input.addEventListener("scroll", sync);
    input.addEventListener("keyup", markActiveLine);
    input.addEventListener("pointerup", markActiveLine);
    paint();

    return {
      element: input,
      getValue: () => input.value,
      setValue(text, { keepPosition = false } = {}) {
        const position = [input.selectionStart, input.selectionEnd, input.scrollTop, input.scrollLeft];
        input.value = String(text ?? "");
        if (keepPosition) {
          const max = input.value.length;
          input.setSelectionRange(Math.min(position[0], max), Math.min(position[1], max));
          input.scrollTop = position[2];
          input.scrollLeft = position[3];
        } else {
          input.setSelectionRange(0, 0);
          input.scrollTop = 0;
          input.scrollLeft = 0;
        }
        paint();
      },
      // Replaces the whole file and keeps Ctrl+Z able to bring the previous version back.
      replaceAll(text) {
        input.focus();
        input.select();
        replaceRange(0, input.value.length, String(text ?? ""));
        input.setSelectionRange(0, 0);
        input.scrollTop = 0;
        sync();
      },
      setLanguage(next) {
        language = next;
        paint();
      },
      setReadOnly(flag) {
        input.readOnly = Boolean(flag);
        host.classList.toggle("is-readonly", Boolean(flag));
      },
      // { line, text } from the last run, cleared at the next edit; null removes it.
      setDiagnostic(next) {
        diagnostic = next && Number.isInteger(next.line) ? { line: next.line, text: String(next.text || "") } : null;
        placeDiagnostic();
      },
      goToLine(line) {
        const lines = input.value.split("\n");
        const index = Math.max(1, Math.min(Number(line) || 1, lines.length)) - 1;
        const start = lines.slice(0, index).reduce((sum, item) => sum + item.length + 1, 0);
        input.focus();
        input.setSelectionRange(start, start + lines[index].length);
        input.scrollTop = Math.max(0, index * metrics().lineHeight - input.clientHeight / 3);
        sync();
        markActiveLine();
      },
      focus() {
        input.focus();
      },
      refresh: paint
    };
  }

  root.KenobiEditor = { create };
})(typeof globalThis !== "undefined" ? globalThis : this);
