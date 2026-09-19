// The Settings panel's "Behavior learning" section (brain/lessons.py on the
// Brain side): the on/off toggle, the autonomy dropdown, the list of lessons
// she's learned (edit / retire / delete), proposals waiting for approval, and
// the add/edit modal. Self-contained -- it finds its own elements by id and
// only needs a `send` function, so brain_client.js just forwards it the
// `lessons_state` message. Everything is rebuilt from that one message, so
// the panel can never drift from what Brain actually has stored.
//
// All lesson text is set via textContent, never innerHTML -- it's
// LLM-generated, and rendering it as markup would be an injection hole.

export class LessonsUI {
  constructor({ send }) {
    this._send = send;
    const $ = (id) => document.getElementById(id);
    this.sectionEl = $("lessons-section");
    this.unavailableEl = $("lessons-unavailable");
    this.toggleEl = $("lessons-toggle");
    this.autonomyEl = $("lessons-autonomy");
    this.errorEl = $("lessons-error");
    this.pendingWrapEl = $("lessons-pending-wrap");
    this.pendingListEl = $("lessons-pending-list");
    this.listEl = $("lessons-list");
    this.addButtonEl = $("lessons-add-button");
    this.modalBackdropEl = $("lesson-modal-backdrop");
    this.modalTitleEl = $("lesson-modal-title");
    this.contentEl = $("lesson-content");
    this.nameEl = $("lesson-name");
    this.priorityEl = $("lesson-priority");
    this.saveButtonEl = $("lesson-save-button");
    this.cancelButtonEl = $("lesson-cancel-button");
    this._editingId = null;

    this.toggleEl?.addEventListener("change", () => this._send({ type: "set_lessons_active", active: this.toggleEl.checked }));
    this.autonomyEl?.addEventListener("change", () => this._send({ type: "set_lessons_autonomy", level: this.autonomyEl.value }));
    this.addButtonEl?.addEventListener("click", () => this._openModal(null));
    this.cancelButtonEl?.addEventListener("click", () => this._closeModal());
    this.saveButtonEl?.addEventListener("click", () => this._saveModal());
    this.modalBackdropEl?.addEventListener("click", (e) => {
      if (e.target === this.modalBackdropEl) this._closeModal();
    });
  }

  handleState(data) {
    const available = !!data.available;
    if (this.unavailableEl) this.unavailableEl.hidden = available;
    for (const el of this.sectionEl?.querySelectorAll(".lessons-when-available") ?? []) el.hidden = !available;
    if (this.toggleEl) this.toggleEl.checked = !!data.active;
    if (this.autonomyEl) this.autonomyEl.value = data.autonomy || "ask";
    if (this.errorEl) {
      this.errorEl.hidden = !data.error;
      this.errorEl.textContent = data.error || "";
    }
    this._renderPending(available ? data.pending || [] : []);
    this._renderLessons(data.lessons || []);
  }

  _renderLessons(lessons) {
    if (!this.listEl) return;
    this.listEl.replaceChildren();
    if (!lessons.length) {
      const empty = document.createElement("p");
      empty.className = "settings-hint";
      empty.textContent = "Nothing yet. Rate a reply with 👍/👎, or add one yourself.";
      this.listEl.appendChild(empty);
      return;
    }
    for (const lesson of lessons) {
      const row = document.createElement("div");
      row.className = "lesson-row";

      const text = document.createElement("div");
      text.className = "lesson-text";
      text.textContent = lesson.content;
      row.appendChild(text);

      const meta = document.createElement("div");
      meta.className = "lesson-meta";
      const strength = document.createElement("span");
      strength.className = "lesson-priority";
      strength.title = "Strength -- higher wins when she has more lessons than fit in her prompt";
      strength.textContent = `strength ${lesson.priority}`;
      meta.appendChild(strength);
      meta.appendChild(this._iconButton("✏️", "Edit", () => this._openModal(lesson)));
      meta.appendChild(
        this._iconButton("📦", "Retire -- turn it off but keep a record", () => this._send({ type: "retire_lesson", id: lesson.id })),
      );
      meta.appendChild(
        this._iconButton(
          "🗑️",
          "Delete",
          () => {
            if (window.confirm("Delete this lesson? This can't be undone.")) this._send({ type: "delete_lesson", id: lesson.id });
          },
          true,
        ),
      );
      row.appendChild(meta);
      this.listEl.appendChild(row);
    }
  }

  _renderPending(pending) {
    if (!this.pendingWrapEl || !this.pendingListEl) return;
    this.pendingWrapEl.hidden = !pending.length;
    this.pendingListEl.replaceChildren();
    for (const proposal of pending) {
      const row = document.createElement("div");
      row.className = "lesson-row pending";

      const text = document.createElement("div");
      text.className = "lesson-text";
      text.textContent = describeProposal(proposal);
      row.appendChild(text);
      if (proposal.reason) {
        const reason = document.createElement("div");
        reason.className = "lesson-reason";
        reason.textContent = proposal.reason;
        row.appendChild(reason);
      }

      const meta = document.createElement("div");
      meta.className = "lesson-meta";
      meta.appendChild(
        this._iconButton("✓ Approve", "Apply this change", () => this._send({ type: "resolve_lesson_proposal", id: proposal.id, approve: true })),
      );
      meta.appendChild(
        this._iconButton("✕ Reject", "Discard it", () => this._send({ type: "resolve_lesson_proposal", id: proposal.id, approve: false }), true),
      );
      row.appendChild(meta);
      this.pendingListEl.appendChild(row);
    }
  }

  _iconButton(label, title, onClick, danger = false) {
    const button = document.createElement("button");
    button.className = "icon-button lesson-button" + (danger ? " danger" : "");
    button.textContent = label;
    button.title = title;
    button.addEventListener("click", onClick);
    return button;
  }

  // lesson null = adding a new one; otherwise editing that lesson in place.
  _openModal(lesson) {
    if (!this.modalBackdropEl) return;
    this._editingId = lesson ? lesson.id : null;
    if (this.modalTitleEl) this.modalTitleEl.textContent = lesson ? "Edit Lesson" : "New Lesson";
    if (this.contentEl) this.contentEl.value = lesson ? lesson.content : "";
    if (this.nameEl) this.nameEl.value = lesson ? lesson.name : "";
    if (this.priorityEl) this.priorityEl.value = lesson ? lesson.priority : 5;
    this.modalBackdropEl.hidden = false;
  }

  _closeModal() {
    if (this.modalBackdropEl) this.modalBackdropEl.hidden = true;
  }

  _saveModal() {
    const content = this.contentEl?.value.trim() || "";
    if (!content) return;
    const priority = Math.max(0, Math.min(10, parseInt(this.priorityEl?.value, 10) || 0));
    this._send({
      type: "save_lesson",
      id: this._editingId,
      name: this.nameEl?.value.trim() || "",
      content,
      priority,
    });
    this._closeModal();
  }
}

// A proposal in words -- mirrors brain/lessons.py's _describe.
function describeProposal(p) {
  switch (p.action) {
    case "create":
      return `New lesson: ${p.content}`;
    case "revise":
      return `Change "${p.target_name}" to: ${p.content}`;
    case "retire":
      return `Retire: ${p.target_name}`;
    case "strengthen":
      return `Strengthen: ${p.target_name}`;
    case "weaken":
      return `Weaken: ${p.target_name}`;
    default:
      return p.content || p.target_name || "";
  }
}
