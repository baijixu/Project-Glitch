// Settings -> Your journal (brain/his_journal.py): entries he writes about his own life, for her nightly
// diary and her reach-outs -- nowhere else. Write one, hide one from her (or let her read it again), or delete
// it. The list is asked for when the fold opens and rebuilt from each `his_journal` message.

import { $ } from "./ui.js";

export class HisJournalUI {
  constructor(app) {
    this._app = app;
    this.inputEl = $("his-journal-input");
    this.listEl = $("his-journal-list");
    this.detailsEl = $("his-journal-details");
    $("his-journal-add")?.addEventListener("click", () => {
      const text = this.inputEl?.value.trim();
      if (!text) return;
      app.send({ type: "add_journal_entry", text });
      this.inputEl.value = "";
      if (this.detailsEl) this.detailsEl.open = true;
    });
    this.detailsEl?.addEventListener("toggle", () => this.detailsEl.open && app.send({ type: "get_his_journal" }));
  }

  get handlers() {
    return { his_journal: (data) => this._render(data.entries || []) };
  }

  _render(entries) {
    if ($("his-journal-count")) $("his-journal-count").textContent = entries.length ? ` (${entries.length})` : "";
    if (!this.listEl) return;
    if (!entries.length) {
      this.listEl.replaceChildren(Object.assign(document.createElement("p"), { className: "settings-hint", textContent: "No entries yet." }));
      return;
    }
    this.listEl.replaceChildren(...entries.map((entry) => {
      const row = Object.assign(document.createElement("div"), { className: `his-journal-entry${entry.hidden ? " hidden-from-her" : ""}` });
      const when = new Date(entry.written).toLocaleString([], { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
      const meta = Object.assign(document.createElement("div"), { className: "lesson-meta" });
      meta.append(Object.assign(document.createElement("span"), { textContent: entry.hidden ? `${when} -- hidden from her` : when }));
      const hide = Object.assign(document.createElement("button"), {
        className: "icon-button lesson-button",
        textContent: entry.hidden ? "👁️" : "🙈",
        title: entry.hidden ? "Let her read it" : "Hide it from her",
      });
      hide.addEventListener("click", () => this._app.send({ type: "hide_journal_entry", id: entry.id, hidden: !entry.hidden }));
      const remove = Object.assign(document.createElement("button"), { className: "icon-button lesson-button danger", textContent: "🗑️", title: "Delete this entry" });
      remove.addEventListener("click", () => window.confirm("Delete this entry for good?") && this._app.send({ type: "delete_journal_entry", id: entry.id }));
      meta.append(hide, remove);
      row.append(Object.assign(document.createElement("p"), { className: "his-journal-text", textContent: entry.text }), meta);
      return row;
    }));
  }
}
