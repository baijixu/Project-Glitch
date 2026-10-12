// The 📥 inbox (top left, under the chat flower): her open questions for him -- from Heartbeat,
// her friends' visits, her diary and chat (brain/curiosity.py's open list) -- to answer or
// skip in his own time. An answer goes through memory like one given in chat (reach_out.py).
// Rebuilt from each `inbox` message; what he's typed survives a rebuild. Her questions are
// model-written, so they only ever go through textContent.

import { $, Modal } from "./ui.js";

export class InboxUI {
  constructor(app) {
    this._app = app;
    this.buttonEl = $("inbox-button");
    this.listEl = $("inbox-list");
    this.modal = new Modal("inbox-modal-backdrop");
    this._drafts = new Map(); // question id -> what he's typed
    this._questions = [];
    this.buttonEl?.addEventListener("click", () => this.modal.open());
    $("inbox-close-button")?.addEventListener("click", () => this.modal.close());
  }

  get handlers() {
    return { inbox: (data) => this._render(data.questions || []) };
  }

  _render(questions) {
    this._questions = questions;
    if (this.buttonEl) this.buttonEl.dataset.count = questions.length || "";
    if (!this.listEl) return;
    this.listEl.replaceChildren();
    if (!questions.length) {
      this.listEl.appendChild(Object.assign(document.createElement("p"), { className: "settings-hint", textContent: "Nothing waiting. Her questions show up here as she thinks of them." }));
      return;
    }
    for (const question of questions) this.listEl.appendChild(this._row(question));
  }

  _row(question) {
    const row = Object.assign(document.createElement("div"), { className: "lesson-row" });
    row.appendChild(Object.assign(document.createElement("div"), { className: "inbox-question", textContent: question.text }));
    const box = Object.assign(document.createElement("textarea"), {
      className: "settings-text-input training-fact",
      rows: 2,
      maxLength: 2000,
      placeholder: "Your answer",
      value: this._drafts.get(question.id) || "",
    });
    box.addEventListener("input", () => this._drafts.set(question.id, box.value));
    row.appendChild(box);
    const meta = Object.assign(document.createElement("div"), { className: "lesson-meta" });
    const button = (label, title, onClick) => {
      const el = Object.assign(document.createElement("button"), { className: "icon-button lesson-button", textContent: label, title });
      el.addEventListener("click", onClick);
      return meta.appendChild(el);
    };
    button("✓ Answer", "Answer her -- with Memory training on, it comes back as a memory to review", () => {
      const answer = box.value.trim();
      if (!answer) return box.focus();
      this._drafts.delete(question.id);
      this._app.send({ type: "answer_question", id: question.id, answer });
    });
    button("Skip", "Drop the question without answering", () => {
      this._drafts.delete(question.id);
      this._app.send({ type: "skip_question", id: question.id });
    });
    row.appendChild(meta);
    return row;
  }
}
