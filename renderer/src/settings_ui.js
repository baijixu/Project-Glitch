// The Settings controls small enough not to need a module of their own: the
// Brain-side on/off switches (private chat, voice, web search, curiosity, heartbeat, memory), memory's
// download and clear, the Notes and soul.md/user.md editors, and the context
// meter.

import { $, Modal, downloadText, writePref } from "./ui.js";
import { _clock } from "./curiosity_timer_ui.js";

// Heartbeat's countdown when it isn't counting.
const HEARTBEAT_PAUSED = {
  off: "Heartbeat is off.",
  roleplay: "Paused during role-play.",
  harness: "Paused while a harness is in control.",
};

// Settings -> Curiosity's dial, in order (brain/curiosity.py's LEVELS): its name, and what it does in a line.
const CHATTINESS = [
  ["quiet", "Quiet", "She lets you lead: answers, doesn't steer or ask, and never reaches out first."],
  ["reserved", "Reserved", "Asks now and then, and reaches out once after 30-90 minutes of quiet."],
  ["balanced", "Balanced", "The odd question, a bit of herself, and one reach-out after 15-60 minutes of quiet."],
  ["chatty", "Chatty", "Adds something of her own to every reply, asks more, and reaches out up to twice (10-30 minutes apart)."],
  ["chatterbox", "Chatterbox", "Carries the conversation: stories, tangents, a question back -- and up to three reach-outs, 5-20 minutes apart."],
];

// Brain-side switches: message it sends -> the state message Brain answers with.
const SWITCHES = [
  { id: "private-toggle", set: "set_private_active", state: "private_state" },
  { id: "voice-toggle", set: "set_voice_active", state: "voice_state" },
  { id: "web-search-toggle", set: "set_web_search_active", state: "web_search_state" },
  { id: "curiosity-toggle", set: "set_curiosity_active", state: "curiosity_state" },
  { id: "heartbeat-toggle", set: "set_heartbeat_active", state: "heartbeat_state" },
  { id: "friends-toggle", set: "set_friends_active", state: "friends_state" },
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
    $("chattiness-slider")?.addEventListener("input", (e) => this._showChattiness(Number(e.target.value)));
    $("chattiness-slider")?.addEventListener("change", (e) => app.send({ type: "set_chattiness", chattiness: CHATTINESS[e.target.value][0] }));
    this._showChattiness(2);
    // Heartbeat's countdown (heartbeat_timer): Brain sends when her next thought is due, the ticking is here.
    this._heartbeatTimer = null;
    setInterval(() => this._renderHeartbeatTimer(), 1000);
    // Heartbeat's Think now: one wander right away; what she thought comes back as heartbeat_note.
    $("think-now-button")?.addEventListener("click", () => {
      $("think-now-button").disabled = true;
      this._showThinkNote("Thinking...");
      app.send({ type: "think_now" });
    });
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
    $("soul-user-editor-soul")?.addEventListener("input", () => this._countSoul());
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
      heartbeat_timer: (data) => {
        this._heartbeatTimer = data;
        this._renderHeartbeatTimer();
      },
      heartbeat_note: (data) => {
        if ($("think-now-button")) $("think-now-button").disabled = false;
        this._showThinkNote(data.text || "");
      },
      soul_and_user_content: (data) => {
        if (!this._answered("get_soul_and_user") || !this.soulUserModal.el) return;
        if ($("soul-user-editor-soul")) $("soul-user-editor-soul").value = data.soul || "";
        if ($("soul-user-editor-user")) $("soul-user-editor-user").value = data.user || "";
        this._soulMax = data.soul_max;
        this._countSoul();
        this.soulUserModal.open();
      },
    };
    for (const { id, state } of SWITCHES) {
      handlers[state] = (data) => {
        if ($(id)) $(id).checked = !!data.active;
      };
    }
    const showCuriosity = handlers.curiosity_state;
    handlers.curiosity_state = (data) => {
      showCuriosity(data);
      const index = CHATTINESS.findIndex(([name]) => name === data.chattiness);
      if (index >= 0) this._showChattiness(index);
    };
    const showPrivate = handlers.private_state;
    handlers.private_state = (data) => {
      showPrivate(data);
      $("roleplay-badge")?.classList.toggle("private", !!data.active); // the PM badge
    };
    return handlers;
  }

  _showChattiness(index) {
    const [, label, hint] = CHATTINESS[index];
    if ($("chattiness-slider")) $("chattiness-slider").value = String(index);
    if ($("chattiness-name")) $("chattiness-name").textContent = label;
    if ($("chattiness-hint")) $("chattiness-hint").textContent = hint;
  }

  // The context meter goes back to empty with the conversation.
  resetContextMeter() {
    for (const id of ["context-meter", "context-meter-conversation"]) this._setBar(id, 0, "after her next reply", "low");
  }

  _renderHeartbeatTimer() {
    const el = $("heartbeat-timer");
    const timer = this._heartbeatTimer;
    if (!el || !timer) return;
    if (timer.state !== "counting" || typeof timer.due_at !== "number") {
      el.textContent = HEARTBEAT_PAUSED[timer.state] || "";
      return;
    }
    const left = Math.round(timer.due_at - Date.now() / 1000);
    // Brain checks once a minute (and waits if she's busy replying).
    el.textContent = left > 0 ? `Her next thought in ${_clock(left)}.` : "Her next thought within a minute.";
  }

  _showThinkNote(text) {
    const note = $("think-now-note");
    if (!note) return;
    note.textContent = text;
    note.hidden = !text;
  }

  // soul.md's live size against the Brain's cap: Save is off while it's over (the Brain would refuse it anyway).
  _countSoul() {
    const chars = ($("soul-user-editor-soul")?.value || "").length;
    const over = chars - (this._soulMax || Infinity);
    const count = $("soul-user-editor-count");
    if (count) count.textContent = `${chars.toLocaleString()} / ${(this._soulMax || 0).toLocaleString()} characters` +
      (over > 0 ? ` -- ${over.toLocaleString()} over. Shorten it to save (anything cut can become a memory).` : "");
    const save = $("soul-user-editor-save-button");
    if (save) save.disabled = over > 0;
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
