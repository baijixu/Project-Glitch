// The Settings panel's "Memory training" section (brain/training.py on the Brain
// side): the on/off toggle and the review queue of memories she's proposed. Each
// proposal is an editable box, an importance dropdown and Approve / Reject --
// nothing is saved to Hindsight until the user approves it. Like LessonsUI it's
// self-contained (finds its own elements by id, only needs `send`), and rebuilds
// everything from each `training_state` message so it can't drift from what
// Brain has. Proposal text is LLM-generated, so it only ever goes through
// textContent / .value, never innerHTML.
//
// A rebuild would throw away a half-edited box, so while any proposal box has
// focus or unsaved edits the list is left alone and refreshed on the next state
// message after the edit is done.

export class TrainingUI {
  constructor({ send, onNewProposals }) {
    this._send = send;
    this._onNewProposals = onNewProposals;
    const $ = (id) => document.getElementById(id);
    this.unavailableEl = $("training-unavailable");
    this.toggleEl = $("training-toggle");
    this.errorEl = $("training-error");
    this.countEl = $("training-count");
    this.listEl = $("training-list");
    this._seen = new Set();
    this._firstState = true;
    this._pendingState = null;

    this.toggleEl?.addEventListener("change", () => this._send({ type: "set_training_active", active: this.toggleEl.checked }));
  }

  handleState(data) {
    const available = !!data.available;
    if (this.unavailableEl) this.unavailableEl.hidden = available;
    if (this.toggleEl) {
      this.toggleEl.checked = !!data.active;
      this.toggleEl.disabled = !available;
    }
    if (this.errorEl) {
      this.errorEl.hidden = !data.error;
      this.errorEl.textContent = data.error || "";
    }
    const pending = available ? data.pending || [] : [];
    if (this.countEl) this.countEl.textContent = pending.length ? ` (${pending.length})` : "";

    // Toast for proposals that arrived since the last state -- not for the ones
    // already waiting when the page loaded.
    const fresh = pending.filter((p) => !this._seen.has(p.id));
    for (const p of pending) this._seen.add(p.id);
    if (!this._firstState && fresh.length) this._onNewProposals?.(fresh[fresh.length - 1].fact);
    this._firstState = false;

    if (this._editing()) {
      this._pendingState = pending; // rebuild once the user is done with the box they're in
      return;
    }
    this._render(pending);
  }

  _editing() {
    const active = document.activeElement;
    return !!active && !!this.listEl && this.listEl.contains(active) && active.tagName === "TEXTAREA";
  }

  _render(pending) {
    if (!this.listEl) return;
    this._pendingState = null;
    this.listEl.replaceChildren();
    if (!pending.length) {
      const empty = document.createElement("p");
      empty.className = "settings-hint";
      empty.textContent = "Nothing waiting. Anything she wants to remember will show up here first.";
      this.listEl.appendChild(empty);
      return;
    }
    for (const proposal of pending) this.listEl.appendChild(this._row(proposal));
  }

  _row(proposal) {
    const row = document.createElement("div");
    row.className = "lesson-row pending";

    const box = document.createElement("textarea");
    box.className = "settings-text-input training-fact";
    box.rows = 2;
    box.maxLength = 300;
    box.value = proposal.fact;
    box.addEventListener("blur", () => {
      if (this._pendingState) setTimeout(() => !this._editing() && this._pendingState && this._render(this._pendingState), 0);
    });
    row.appendChild(box);

    if (proposal.source) {
      const source = document.createElement("div");
      source.className = "lesson-reason";
      source.textContent = `From: "${proposal.source}"`;
      row.appendChild(source);
    }

    const meta = document.createElement("div");
    meta.className = "lesson-meta";
    const importance = document.createElement("select");
    importance.className = "settings-select training-importance";
    importance.title = "Core facts are always in her prompt; minor ones only come up when relevant";
    for (const [value, label] of [["core", "Core"], ["normal", "Normal"], ["minor", "Minor"]]) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      importance.appendChild(option);
    }
    importance.value = "normal";
    meta.appendChild(importance);
    meta.appendChild(
      this._button("✓ Save", "Save this to her memory", () => {
        const fact = box.value.trim();
        if (fact) this._send({ type: "resolve_memory_proposal", id: proposal.id, approve: true, fact, importance: importance.value });
      }),
    );
    meta.appendChild(this._button("✕ Reject", "Discard it", () => this._send({ type: "resolve_memory_proposal", id: proposal.id, approve: false }), true));
    row.appendChild(meta);
    return row;
  }

  _button(label, title, onClick, danger = false) {
    const button = document.createElement("button");
    button.className = "icon-button lesson-button" + (danger ? " danger" : "");
    button.textContent = label;
    button.title = title;
    button.addEventListener("click", onClick);
    return button;
  }
}
