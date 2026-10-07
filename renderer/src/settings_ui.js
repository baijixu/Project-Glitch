// The Settings controls small enough not to need a module of their own: the
// Brain-side on/off switches (voice, web search, curiosity, memory), memory's
// download and clear, the Notes and soul.md/user.md editors, and the context
// meter.

import { $, Modal, downloadText, writePref } from "./ui.js";

// Brain-side switches: message it sends -> the state message Brain answers with.
const SWITCHES = [
  { id: "voice-toggle", set: "set_voice_active", state: "voice_state" },
  { id: "web-search-toggle", set: "set_web_search_active", state: "web_search_state" },
  { id: "curiosity-toggle", set: "set_curiosity_active", state: "curiosity_state" },
  { id: "memory-toggle", set: "set_memory_active", state: "memory_state" },
];

export class SettingsUI {
  constructor(app) {
    this._app = app;
    this.notesModal = new Modal("notes-modal-backdrop");
    this.soulUserModal = new Modal("soul-user-editor-modal-backdrop");
    // Set when a button asked Brain for something, so only that answer acts on it.
    this._waitingFor = new Set();

    for (const { id, set } of SWITCHES) $(id)?.addEventListener("change", (e) => app.send({ type: set, active: e.target.checked }));
    $("download-memory-button")?.addEventListener("click", () => this._ask("get_memory_content"));
    $("clear-memory-button")?.addEventListener("click", () => {
      if (window.confirm("Clear everything Glitch remembers about you? This can't be undone.")) app.send({ type: "clear_memory" });
    });

    // UI style: this device's own (index.html applies it before the page draws).
    const uiStyleEl = $("ui-style-select");
    if (uiStyleEl) uiStyleEl.value = document.documentElement.dataset.ui || "";
    uiStyleEl?.addEventListener("change", () => {
      document.documentElement.dataset.ui = uiStyleEl.value;
      writePref("glitch_ui_style", uiStyleEl.value);
    });

    // Both editors ask Brain for the file each time they open -- nothing is
    // cached here, so they always show what's really there.
    $("open-notes-button")?.addEventListener("click", () => this._ask("get_notes"));
    $("notes-cancel-button")?.addEventListener("click", () => this.notesModal.close());
    $("notes-save-button")?.addEventListener("click", () => {
      app.send({ type: "save_notes", content: $("notes-textarea")?.value ?? "" });
      this.notesModal.close();
    });
    // Her main soul.md and user.md, written directly -- separate from the saved
    // role-play souls and profiles.
    $("open-soul-user-editor-button")?.addEventListener("click", () => this._ask("get_soul_and_user"));
    $("soul-user-editor-cancel-button")?.addEventListener("click", () => this.soulUserModal.close());
    $("soul-user-editor-save-button")?.addEventListener("click", () => {
      app.send({ type: "save_soul_and_user", soul: $("soul-user-editor-soul")?.value ?? "", user: $("soul-user-editor-user")?.value ?? "" });
      this.soulUserModal.close();
    });
  }

  get handlers() {
    const handlers = {
      context_usage: (data) => this._renderContextUsage(data),
      memory_content: (data) => this._answered("get_memory_content") && this._downloadMemory(data.entries || []),
      notes_content: (data) => {
        if (!this._answered("get_notes") || !this.notesModal.el) return;
        if ($("notes-textarea")) $("notes-textarea").value = data.content || "";
        this.notesModal.open();
      },
      soul_and_user_content: (data) => {
        if (!this._answered("get_soul_and_user") || !this.soulUserModal.el) return;
        if ($("soul-user-editor-soul")) $("soul-user-editor-soul").value = data.soul || "";
        if ($("soul-user-editor-user")) $("soul-user-editor-user").value = data.user || "";
        this.soulUserModal.open();
      },
    };
    for (const { id, state } of SWITCHES) {
      handlers[state] = (data) => {
        if ($(id)) $(id).checked = !!data.active;
      };
    }
    return handlers;
  }

  // The context meter goes back to empty with the conversation.
  resetContextMeter() {
    for (const id of ["context-meter", "context-meter-conversation"]) this._setBar(id, 0, "after her next reply", "low");
  }

  _ask(type) {
    this._waitingFor.add(type);
    this._app.send({ type });
  }

  _answered(type) {
    return this._waitingFor.delete(type);
  }

  // Two bars: the conversation against what she keeps (past 100% the oldest part
  // goes with the next message), and her latest reply against the model's context.
  _renderContextUsage(data) {
    const conversation = Number(data.conversation) || 0;
    const keep = Number(data.keep) || 0;
    if (keep > 0) {
      // The conversation's count is the estimate the trim uses, hence the "~".
      const pct = Math.round((conversation / keep) * 100);
      this._setBar("context-meter-conversation", pct,
        `~${conversation.toLocaleString()} / ${keep.toLocaleString()} kept (${pct}%)${pct > 100 ? " -- trims next message" : ""}`,
        pct > 100 ? "high" : pct >= 80 ? "mid" : "low");
    } else {
      this._setBar("context-meter-conversation", 0, "after her next reply", "low");
    }
    const used = Number(data.used) || 0;
    const window_ = Number(data.window) || 0;
    if (window_ > 0) {
      const pct = Math.round((used / window_) * 100);
      this._setBar("context-meter", pct, `${used.toLocaleString()} / ${window_.toLocaleString()} tokens (${pct}%)`,
        pct >= 85 ? "high" : pct >= 60 ? "mid" : "low");
    } else {
      this._setBar("context-meter", 0, `${used.toLocaleString()} tokens (the server doesn't report its limit)`, "low");
    }
  }

  _setBar(id, pct, text, level) {
    if ($(`${id}-text`)) $(`${id}-text`).textContent = text;
    const fillEl = $(`${id}-fill`);
    if (fillEl) {
      fillEl.style.width = `${Math.max(0, Math.min(100, pct))}%`;
      fillEl.dataset.level = level;
    }
  }

  _downloadMemory(entries) {
    const header = [`Glitch's memory of you -- exported ${new Date().toISOString()}`, `${entries.length} fact${entries.length === 1 ? "" : "s"}`, ""];
    downloadText(`glitch-memory-${Date.now()}.txt`, header.concat(entries.map((e) => `- ${e}`)).join("\n") + "\n");
  }
}
