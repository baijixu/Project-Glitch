// Settings -> Library (brain/library.py on the Brain side): books for her to read on her own
// time. Add one (TXT, Markdown, PDF, EPUB, Word), see how far she's got, take one off the
// shelf. Rebuilt from each `library` message, so every device shows the same shelf.

import { $ } from "./ui.js";

export class LibraryUI {
  constructor(app) {
    this._app = app;
    this.listEl = $("library-list");
    this.noteEl = $("library-note");
    this.inputEl = $("library-file-input");
    $("add-book-button")?.addEventListener("click", () => this.inputEl?.click());
    this.inputEl?.addEventListener("change", () => this._add([...(this.inputEl.files || [])]));
  }

  get handlers() {
    return { library: (data) => this._render(data) };
  }

  async _add(files) {
    this.inputEl.value = ""; // otherwise re-picking the same file wouldn't fire "change" again
    for (const file of files) {
      this._note(`Adding ${file.name}...`); // a big PDF takes Brain a moment to read
      this._app.send({ type: "add_book", name: file.name, data_b64: (await file.bytes()).toBase64() });
    }
  }

  _note(text) {
    if (!this.noteEl) return;
    this.noteEl.hidden = !text;
    this.noteEl.textContent = text;
  }

  _render(data) {
    this._note(data.error || "");
    if (!this.listEl) return;
    this.listEl.replaceChildren();
    const books = data.books || [];
    if (!books.length) {
      this.listEl.appendChild(Object.assign(document.createElement("p"), { className: "settings-hint", textContent: "No books yet." }));
      return;
    }
    for (const book of books) {
      const row = document.createElement("div");
      row.className = "lesson-row";
      const status = book.percent >= 100 ? "finished" : book.reading ? `reading now, ${book.percent}%` : book.percent ? `${book.percent}%` : "not started";
      row.appendChild(Object.assign(document.createElement("div"), { textContent: book.title }));
      const meta = document.createElement("div");
      meta.className = "lesson-meta";
      meta.appendChild(Object.assign(document.createElement("span"), { className: "lesson-reason", textContent: status }));
      const remove = Object.assign(document.createElement("button"), {
        className: "icon-button lesson-button danger",
        textContent: "🗑️",
        title: "Take it off her shelf",
      });
      remove.addEventListener("click", () => {
        if (window.confirm(`Take "${book.title}" off her shelf? Where she got to goes with it.`)) {
          this._app.send({ type: "delete_book", name: book.name });
        }
      });
      meta.appendChild(remove);
      row.appendChild(meta);
      this.listEl.appendChild(row);
    }
  }
}
