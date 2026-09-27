// Settings -> Chat Logs: read or delete her daily chat logs (brain/chat_logs/,
// role-play in chat_logs/roleplay/ -- see brain/conversation.py). Self-contained
// like SamplingUI: finds its own elements by id, only needs `send`, and redraws
// from each `chat_logs` / `chat_log_content` message Brain sends.
//
// A log is the user's own conversation, so it's only ever put on the page as
// text (textContent), never as HTML.

const MODE_NAMES = { main: "Chat", roleplay: "Role-play" };
// Where a new entry starts in a log -- same shape as conversation.py's _ENTRY_START.
const MESSAGE_LINE = /^\*\*(\d{2}:\d{2}:\d{2})\*\* ([^:]+): ?(.*)$/;
const NOTE_LINE = /^\*(\d{2}:\d{2}:\d{2}) -- (.*)\*$/;

export class ChatLogsUI {
  constructor({ send }) {
    this._send = send;
    const $ = (id) => document.getElementById(id);
    this.modeEl = $("chat-logs-mode");
    this.dayEl = $("chat-logs-day");
    this.readButtonEl = $("chat-logs-read-button");
    this.deleteButtonEl = $("chat-logs-delete-button");
    this.backdropEl = $("chat-log-modal-backdrop");
    this.titleEl = $("chat-log-modal-title");
    this.bodyEl = $("chat-log-body");
    this._days = { main: [], roleplay: [] };

    this.modeEl?.addEventListener("change", () => this.refresh());
    this.dayEl?.addEventListener("change", () => this._updateButtons());
    this.readButtonEl?.addEventListener("click", () => this._read());
    this.deleteButtonEl?.addEventListener("click", () => this._delete());
    $("chat-log-close-button")?.addEventListener("click", () => this.close());
    this.backdropEl?.addEventListener("click", (e) => {
      if (e.target === this.backdropEl) this.close();
    });
    this._render();
  }

  get _mode() {
    return this.modeEl?.value === "roleplay" ? "roleplay" : "main";
  }

  // Asks Brain for the current mode's list -- when Settings opens, and when the
  // Chat / Role-play choice changes.
  refresh() {
    this._send({ type: "get_chat_logs", mode: this._mode });
  }

  handleList(data) {
    this._days[data.mode === "roleplay" ? "roleplay" : "main"] = data.days || [];
    this._render();
  }

  handleContent(data) {
    if (data.error) {
      window.alert(`Couldn't open that log: ${data.error}`);
      return;
    }
    this.titleEl.textContent = `${MODE_NAMES[data.mode] || "Chat"} log -- ${_prettyDate(data.date)}`;
    this.bodyEl.replaceChildren(..._renderLog(data.content || ""));
    this.backdropEl.hidden = false;
    this.bodyEl.scrollTop = 0;
  }

  close() {
    if (this.backdropEl) this.backdropEl.hidden = true;
  }

  isOpen() {
    return !!this.backdropEl && !this.backdropEl.hidden;
  }

  _render() {
    if (!this.dayEl) return;
    const days = this._days[this._mode];
    const keep = this.dayEl.value;
    this.dayEl.replaceChildren();
    if (!days.length) {
      const empty = document.createElement("option");
      empty.value = "";
      empty.textContent = `No ${MODE_NAMES[this._mode].toLowerCase()} logs yet`;
      this.dayEl.appendChild(empty);
    }
    for (const day of days) {
      const option = document.createElement("option");
      option.value = day.date;
      option.textContent = `${_prettyDate(day.date)} (${_prettySize(day.size)})`;
      this.dayEl.appendChild(option);
    }
    if (days.some((d) => d.date === keep)) this.dayEl.value = keep;
    this._updateButtons();
  }

  _updateButtons() {
    const none = !this.dayEl?.value;
    if (this.readButtonEl) this.readButtonEl.disabled = none;
    if (this.deleteButtonEl) this.deleteButtonEl.disabled = none;
  }

  _read() {
    const date = this.dayEl?.value;
    if (date) this._send({ type: "get_chat_log", mode: this._mode, date });
  }

  _delete() {
    const date = this.dayEl?.value;
    if (!date) return;
    const what = `${MODE_NAMES[this._mode].toLowerCase()} log for ${_prettyDate(date)}`;
    if (!window.confirm(`Delete the ${what}? This can't be undone.`)) return;
    this._send({ type: "delete_chat_log", mode: this._mode, date });
  }
}

// "2026-09-26" -> "Sat 26 Sep 2026" (local reading of a plain date).
function _prettyDate(date) {
  const [y, m, d] = String(date).split("-").map(Number);
  if (!y || !m || !d) return String(date);
  return new Date(y, m - 1, d).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short", year: "numeric" });
}

function _prettySize(bytes) {
  return bytes >= 1024 ? `${Math.round(bytes / 1024)} KB` : `${bytes} B`;
}

// The log's lines as elements: one block per message (time, who, what they
// said -- including any further lines of it), notes like "chat cleared" in
// italics, and "---" as a divider. The "# ..." heading is left out; the pop-up's
// title says which day it is.
function _renderLog(text) {
  const nodes = [];
  let current = null;
  for (const line of text.split("\n")) {
    if (line.startsWith("# ")) continue;
    const message = line.match(MESSAGE_LINE);
    const note = line.match(NOTE_LINE);
    if (message) {
      current = document.createElement("div");
      current.className = `chat-log-entry ${message[2].startsWith("You") ? "from-user" : "from-glitch"}`;
      const meta = document.createElement("div");
      meta.className = "chat-log-meta";
      meta.textContent = `${message[2]} · ${message[1]}`;
      const said = document.createElement("div");
      said.className = "chat-log-text";
      said.textContent = message[3];
      current.append(meta, said);
      nodes.push(current);
    } else if (note) {
      current = null;
      const div = document.createElement("div");
      div.className = "chat-log-note";
      div.textContent = `${note[1]} -- ${note[2]}`;
      nodes.push(div);
    } else if (line.trim() === "---") {
      current = null;
      nodes.push(document.createElement("hr"));
    } else if (current) {
      // more of the same message (her replies often run to several paragraphs)
      current.querySelector(".chat-log-text").textContent += `\n${line}`;
    }
  }
  for (const node of nodes) {
    const said = node.querySelector?.(".chat-log-text");
    if (said) said.textContent = said.textContent.replace(/\n+$/, "");
  }
  if (!nodes.length) {
    const empty = document.createElement("div");
    empty.className = "chat-log-note";
    empty.textContent = "This log is empty.";
    nodes.push(empty);
  }
  return nodes;
}
