// The conversation on screen: the History panel (kept across a reload in
// localStorage), the optional Chat Bubbles Over Avatar, and what can be done to
// a message there -- resend, edit the latest, rate her replies, clear.
//
// Brain keeps its own copy of the conversation (brain/conversation.py); this is
// only the view of it. Text here is what was said, so it only ever goes into the
// page through textContent / text nodes.

import { $, Modal, readPref, writePref } from "./ui.js";

const STORAGE_KEY = "glitch_chat_history";
const MAX_STORED_ENTRIES = 100;
const OVERLAY_PREF_KEY = "glitch_overlay_chat_active";

// Over-avatar bubbles stay put until the stack grows past "her bustline" --
// STACK_MAX_HEIGHT_PX must roughly match style.css's sense of that cutoff;
// MAX_BUBBLES is a backstop for a run of very short messages.
const OVERLAY_STACK_MAX_HEIGHT_PX = 260;
const MAX_OVERLAY_BUBBLES = 6;

export class HistoryUI {
  // app: send, sendForReply, canSendReply, flashStatus, setStatus.
  // displayName(role): the name shown above a bubble ("" for none).
  // onClear(): the conversation on screen was cleared.
  constructor(app, { displayName, onClear }) {
    this._app = app;
    this._displayName = displayName;
    this._onClear = onClear;
    this.listEl = $("history-list");
    this.overlayEl = $("bubble-overlay");
    this.overlayToggleEl = $("overlay-chat-toggle");
    this.rateModal = new Modal("rate-modal-backdrop", { onClose: () => (this._rating = null) });
    this.editModal = new Modal("edit-message-modal-backdrop", { onClose: () => (this._editing = null) });
    this._rateControls = new WeakMap(); // entry -> repaint its 👍/👎
    this._userBubbles = new WeakMap(); // entry -> its History bubble, for editing
    this._rating = null; // {entry, rating} while the reason pop-up is open
    this._editing = null; // the entry being edited

    // Per-browser display preference, off by default.
    this.overlayActive = readPref(OVERLAY_PREF_KEY, "0") === "1";
    this._applyOverlay();
    this.overlayToggleEl?.addEventListener("change", () => {
      this.overlayActive = this.overlayToggleEl.checked;
      writePref(OVERLAY_PREF_KEY, this.overlayActive ? "1" : "0");
      this._applyOverlay();
    });

    try {
      this.entries = JSON.parse(readPref(STORAGE_KEY, "[]"));
    } catch {
      this.entries = [];
    }
    for (const entry of this.entries) this._render(entry, null);

    for (const id of ["resend-last-button", "overlay-resend-button"]) $(id)?.addEventListener("click", () => this.resendLast());
    $("clear-chat-history-button")?.addEventListener("click", () => this._clearConversation());
    $("overlay-edit-button")?.addEventListener("click", () => {
      const last = this._lastEntry("user");
      if (last) this._edit(last);
    });
    for (const rating of ["up", "down"]) {
      $(`overlay-thumbs-${rating}`)?.addEventListener("click", () => this._openRate(this._lastEntry("glitch", (e) => e.text), rating));
    }

    $("rate-modal-cancel-button")?.addEventListener("click", () => this.rateModal.close());
    $("rate-modal-send-button")?.addEventListener("click", () => this._submitRate());
    $("rate-modal-textarea")?.addEventListener("input", (e) => ($("rate-modal-send-button").disabled = !e.target.value.trim()));
    $("rate-modal-textarea")?.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        this._submitRate();
      }
    });
    $("edit-message-cancel-button")?.addEventListener("click", () => this.editModal.close());
    $("edit-message-send-button")?.addEventListener("click", () => this._submitEdit());
  }

  // Adds a message: to the History panel, the stored history, and (while on)
  // the over-avatar bubbles. thumbnailUrl is a picture sent with it (shown, not
  // stored). Returns {bubbleEl, overlayBubbleEl, entry} for updateText.
  add(role, text, thumbnailUrl) {
    const timeText = new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    const entry = { role, text, timeText };
    const bubbleEl = this._render(entry, thumbnailUrl);
    this.entries.push(entry);
    while (this.entries.length > MAX_STORED_ENTRIES) this.entries.shift();
    this._save();
    const overlayBubbleEl = this.overlayActive ? this._addOverlayBubble(role, text) : null;
    return { bubbleEl, overlayBubbleEl, entry };
  }

  // A voice message's "🎤 (voice message)" swapped for the transcript, in place.
  updateText(added, text) {
    if (!added) return;
    const { bubbleEl, overlayBubbleEl, entry } = added;
    entry.text = text;
    if (bubbleEl) bubbleEl.textContent = text;
    if (overlayBubbleEl) overlayBubbleEl.textContent = text; // a no-op if it's already been pushed out
    this._save();
  }

  // Clears the History panel and the bubbles (Brain's conversation_cleared, or
  // Clear Chat while not connected).
  clear() {
    this._onClear();
    this.entries = [];
    if (this.listEl) this.listEl.innerHTML = "";
    if (this.overlayEl) this.overlayEl.innerHTML = "";
    writePref(STORAGE_KEY, null);
  }

  // Resend Last (and the over-avatar ↻): re-asks her latest message.
  resendLast() {
    const entry = this._lastEntry("user", (e) => e.text);
    if (entry) this._retry(entry);
  }

  _lastEntry(role, test = () => true) {
    return [...this.entries].reverse().find((e) => e.role === role && test(e));
  }

  _save() {
    writePref(STORAGE_KEY, JSON.stringify(this.entries));
  }

  // True when nothing has been said by the user since `entry`.
  _isLatest(entry) {
    const index = this.entries.indexOf(entry);
    return index !== -1 && !this.entries.slice(index + 1).some((e) => e.role === "user");
  }

  // ↻ on a user bubble. The latest one regenerates in place -- her stale reply
  // goes and Brain re-answers the same prompt (pop_last_exchange) instead of
  // seeing the question twice. An older one is just asked again as a new
  // message: popping a turn out from the middle would scramble the history.
  _retry(entry) {
    if (!this._app.canSendReply()) return;
    const text = entry.text;
    if (this._isLatest(entry)) {
      this._removeStaleReply(entry);
      this._app.sendForReply({ type: "regenerate_last", text }, `sent regenerate_last (${text.length} chars)`);
      return;
    }
    this._app.sendForReply({ type: "user_text", text }, `sent user_text (${text.length} chars, retry)`);
    this.add("user", text);
  }

  // ✏️ on the latest user bubble: fix it and she answers the corrected version,
  // her old reply removed -- the same in-place swap as regenerating.
  _edit(entry) {
    if (!this._app.canSendReply()) return;
    if (!this._isLatest(entry)) return this._app.flashStatus("Only your latest message can be edited", 2500);
    const textarea = $("edit-message-textarea");
    if (!this.editModal.el || !textarea) return;
    textarea.value = entry.text;
    this.editModal.open();
    this._editing = entry;
    textarea.focus();
    textarea.setSelectionRange(textarea.value.length, textarea.value.length);
  }

  _submitEdit() {
    const entry = this._editing;
    const text = ($("edit-message-textarea")?.value || "").trim();
    this.editModal.close();
    if (!entry || !text || text === entry.text) return;
    if (!this._app.canSendReply() || !this._isLatest(entry)) return;
    this._removeStaleReply(entry);
    entry.text = text;
    const bubble = this._userBubbles.get(entry);
    if (bubble?.lastChild) bubble.lastChild.nodeValue = text;
    const overlayBubble = this.overlayActive ? this.overlayEl?.lastElementChild : null;
    if (overlayBubble?.classList.contains("user") && overlayBubble.lastChild) overlayBubble.lastChild.nodeValue = text;
    this._save();
    this._app.sendForReply({ type: "regenerate_last", text, edited: true }, `sent regenerate_last (${text.length} chars, edited)`);
  }

  // Her reply to `entry` (the latest exchange), if there is one: from the panel,
  // the bubbles and the stored history. Bubbles aren't tracked per entry, but
  // the stale reply is always the newest one.
  _removeStaleReply(entry) {
    const index = this.entries.indexOf(entry);
    if (index === -1 || this.entries[index + 1]?.role !== "glitch") return;
    this.entries.splice(index + 1, 1);
    this.listEl?.lastElementChild?.remove();
    if (this.overlayActive) this.overlayEl?.lastElementChild?.remove();
    this._save();
  }

  // Clear Chat: this screen, and a fresh conversation for her too (Brain tells
  // every device, conversation_cleared). Her long-term memory isn't touched.
  _clearConversation() {
    if (!window.confirm("Clear the chat and start her on a fresh conversation? She keeps her long-term memory.")) return;
    if (!this._app.connected) {
      this.clear();
      this._app.setStatus("Brain isn't connected -- only this screen was cleared");
      return;
    }
    this._app.send({ type: "clear_conversation" });
    this.clear();
  }

  // ---- 👍 / 👎 -----------------------------------------------------------------
  // Either one opens the reason pop-up -- the reason is what lets Brain write a
  // lesson (brain/lessons.py), and a bare rating mostly went in empty. One
  // rating per reply: once rated both buttons lock, saved on the entry.

  _buildRateControls(entry) {
    const controls = document.createElement("span");
    controls.className = "rate-controls";
    const [up, down] = ["up", "down"].map((rating) => {
      const button = document.createElement("button");
      button.className = "rate-button";
      button.textContent = rating === "up" ? "👍" : "👎";
      button.title = rating === "up" ? "Good reply" : "Bad reply";
      button.addEventListener("click", () => this._openRate(entry, rating));
      return button;
    });
    controls.append(up, down);
    const paint = () => {
      up.disabled = down.disabled = !!entry.rating;
      up.classList.toggle("picked", entry.rating === "up");
      down.classList.toggle("picked", entry.rating === "down");
    };
    paint();
    this._rateControls.set(entry, paint);
    return controls;
  }

  // A reason is required: Send stays off until there is one. Enter sends,
  // Shift+Enter is a new line.
  _openRate(entry, rating) {
    if (!entry || entry.rating) return;
    this._rating = { entry, rating };
    $("rate-modal-title").textContent = rating === "up" ? "👍 What did you like?" : "👎 What should she do differently?";
    $("rate-modal-quote").textContent = entry.text;
    const box = $("rate-modal-textarea");
    box.value = "";
    box.placeholder =
      rating === "up" ? "e.g. the length was just right, or I liked the joke" : "e.g. too long, or don't bring up work at night";
    $("rate-modal-send-button").disabled = true;
    this.rateModal.open();
    box.focus();
  }

  _submitRate() {
    const target = this._rating;
    const note = $("rate-modal-textarea").value.trim();
    if (!target || !note) return;
    this.rateModal.close();
    const { entry, rating } = target;
    if (entry.rating || !entry.text) return;
    // The prompt she was answering: the closest user entry before this reply.
    const index = this.entries.indexOf(entry);
    const userText = [...this.entries.slice(0, Math.max(index, 0))].reverse().find((e) => e.role === "user")?.text || "";
    this._app.send({ type: "rate_reply", rating, note, user_text: userText, reply_text: entry.text });
    entry.rating = rating;
    this._save();
    this._rateControls.get(entry)?.();
    this._app.flashStatus(rating === "up" ? "👍 Thanks -- noted" : "👎 Thanks -- noted", 2500);
  }

  // ---- Rendering -----------------------------------------------------------------

  _render(entry, thumbnailUrl) {
    if (!this.listEl) return null;
    const { role, text, timeText } = entry;
    const group = document.createElement("div");
    group.className = `history-group history-${role}`;
    const name = this._displayName(role);
    if (name) group.appendChild(Object.assign(document.createElement("div"), { className: "history-name", textContent: name }));

    const bubble = document.createElement("div");
    bubble.className = "history-bubble";
    if (thumbnailUrl) bubble.appendChild(Object.assign(document.createElement("img"), { className: "history-thumbnail", src: thumbnailUrl, alt: "" }));
    if (text) bubble.appendChild(document.createTextNode(text));
    group.appendChild(bubble);

    const meta = document.createElement("div");
    meta.className = "history-meta";
    // ↻ and ✏️ only on the latest user message.
    if (role === "user") for (const button of this.listEl.querySelectorAll(".history-retry-button")) button.hidden = true;
    meta.appendChild(Object.assign(document.createElement("div"), { className: "history-time", textContent: timeText }));
    // Not on a picture message: only its caption could be resent.
    if (role === "user" && text && !thumbnailUrl) {
      for (const [label, title, extraClass, action] of [
        ["↻", "Resend this message", "", () => this._retry(entry)],
        ["✏️", "Edit this message (your latest one only)", " history-edit-button", () => this._edit(entry)],
      ]) {
        const button = Object.assign(document.createElement("button"), { className: `history-retry-button${extraClass}`, textContent: label, title });
        button.addEventListener("click", action);
        meta.appendChild(button);
      }
      this._userBubbles.set(entry, bubble);
    }
    if (role === "glitch" && text) meta.appendChild(this._buildRateControls(entry));
    group.appendChild(meta);

    this.listEl.appendChild(group);
    this.listEl.scrollTop = this.listEl.scrollHeight;
    return bubble;
  }

  // Turning the bubbles off clears what's floating -- nothing else would.
  _applyOverlay() {
    if (this.overlayToggleEl) this.overlayToggleEl.checked = this.overlayActive;
    document.body.classList.toggle("overlay-chat-active", this.overlayActive);
    if (!this.overlayActive && this.overlayEl) this.overlayEl.replaceChildren();
  }

  _addOverlayBubble(role, text) {
    if (!this.overlayEl || !text) return null;
    const bubble = document.createElement("div");
    bubble.className = `overlay-bubble ${role}`;
    const name = this._displayName(role);
    if (name) bubble.appendChild(Object.assign(document.createElement("div"), { className: "overlay-bubble-name", textContent: name }));
    bubble.appendChild(document.createTextNode(text));
    this.overlayEl.appendChild(bubble);
    requestAnimationFrame(() => bubble.classList.add("visible")); // next frame, so the fade-in plays
    this._trimOverlay();
    return bubble;
  }

  // Fades out the oldest bubble once the stack is too tall or too many, and
  // again after each fade until it fits. `visible` doubles as "not already
  // fading out".
  _trimOverlay() {
    const oldest = this.overlayEl?.firstElementChild;
    if (!oldest || !oldest.classList.contains("visible")) return;
    const tooMany = this.overlayEl.children.length > MAX_OVERLAY_BUBBLES;
    const tooTall = this.overlayEl.scrollHeight > OVERLAY_STACK_MAX_HEIGHT_PX;
    if (!tooMany && !tooTall) return;
    oldest.classList.remove("visible");
    oldest.addEventListener(
      "transitionend",
      () => {
        oldest.remove();
        this._trimOverlay();
      },
      { once: true },
    );
  }
}
