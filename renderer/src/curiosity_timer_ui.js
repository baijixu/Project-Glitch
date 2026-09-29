// Settings -> Curiosity: a live countdown to when she may speak first, and a
// Test button that makes her reach out now. Self-contained like SamplingUI:
// finds its own elements by id, only needs `send`, and redraws from each
// `curiosity_timer` Brain sends (brain/main.py's _curiosity_timer_message).
// Brain sends the moment she's due; the ticking happens here.

const PAUSED = {
  off: "Curiosity is off.",
  roleplay: "Paused during role-play.",
  harness: "Paused while a harness is in control.",
  waiting: "She reached out -- waiting for your reply before she does again.",
};

export class CuriosityTimerUI {
  constructor({ send }) {
    this._send = send;
    this.textEl = document.getElementById("curiosity-timer");
    this.testButtonEl = document.getElementById("curiosity-test-button");
    this.errorEl = document.getElementById("curiosity-test-error");
    this._state = null;
    this._dueAt = null;
    this.testButtonEl?.addEventListener("click", () => this._test());
    setInterval(() => this._render(), 1000);
  }

  handleTimer(data) {
    this._showError(data.error || "");
    this._state = data.state;
    this._dueAt = typeof data.due_at === "number" ? data.due_at : null;
    if (this.testButtonEl) this.testButtonEl.disabled = ["off", "roleplay", "harness"].includes(data.state);
    this._render();
  }

  _render() {
    if (!this.textEl || !this._state) return;
    if (this._state !== "counting" || this._dueAt === null) {
      this.textEl.textContent = PAUSED[this._state] || "";
      return;
    }
    const left = Math.round(this._dueAt - Date.now() / 1000);
    // Brain checks once a minute, so past zero she speaks within the next minute.
    this.textEl.textContent = left > 0 ? `She may reach out in ${_clock(left)}.` : "She'll reach out within a minute.";
  }

  _test() {
    this._showError("");
    if (this.testButtonEl) this.testButtonEl.disabled = true;
    // Re-enabled by the curiosity_timer Brain sends once she's spoken (or refused).
    this._send({ type: "test_reach_out" });
  }

  _showError(text) {
    if (!this.errorEl) return;
    this.errorEl.textContent = text;
    this.errorEl.hidden = !text;
  }
}

// 3725 -> "1:02:05", 125 -> "2:05".
function _clock(seconds) {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  const mm = h ? String(m).padStart(2, "0") : String(m);
  return `${h ? `${h}:` : ""}${mm}:${String(s).padStart(2, "0")}`;
}
