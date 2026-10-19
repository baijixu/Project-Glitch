// The 🧠 "Her mind" window (top left, under the inbox): what went into her last reply and what's
// waiting on him -- the memories recalled for it, the memory proposals in his review list, and how
// big her soul.md is and when it last changed. Asked for (`get_mind`) each time it opens or he
// hits Refresh, so it's never stale. Memories and proposals are model-written: textContent only.

import { $, Modal } from "./ui.js";

const SHOWN_PENDING = 5; // the rest are a click away in Settings
const CHARS_PER_PAGE = 3000; // a printed page, roughly -- to read soul.md's size at a glance

export class MindUI {
  constructor(app) {
    this._app = app;
    this.modal = new Modal("mind-modal-backdrop");
    $("mind-button")?.addEventListener("click", () => this._open());
    $("mind-refresh-button")?.addEventListener("click", () => this._app.send({ type: "get_mind" }));
    $("mind-close-button")?.addEventListener("click", () => this.modal.close());
    $("mind-review-button")?.addEventListener("click", () => this._openReview());
  }

  get handlers() {
    return { mind: (data) => this._render(data) };
  }

  _open() {
    this._app.send({ type: "get_mind" });
    this.modal.open();
  }

  _render({ recalled = [], pending = [], soul = {} }) {
    this._list($("mind-recalled"), recalled, "None -- send her a message and the memories she recalls for it show up here.");
    $("mind-pending-count").textContent = pending.length ? ` (${pending.length})` : "";
    const facts = pending.slice(0, SHOWN_PENDING).map((p) => p.fact);
    if (pending.length > SHOWN_PENDING) facts.push(`...and ${pending.length - SHOWN_PENDING} more`);
    this._list($("mind-pending"), facts, "Nothing waiting for you.");
    $("mind-review-button").hidden = !pending.length;
    const pages = (soul.chars / CHARS_PER_PAGE).toFixed(1);
    const edited = soul.edited ? new Date(soul.edited).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "";
    $("mind-soul").textContent = soul.edited
      ? `${soul.chars.toLocaleString()} characters, ${soul.lines} lines (about ${pages} pages). Last changed ${edited}.`
      : "No soul.md -- she's on her default personality.";
  }

  _list(el, items, empty) {
    el.replaceChildren(
      ...(items.length
        ? items.map((text) => Object.assign(document.createElement("li"), { textContent: text }))
        : [Object.assign(document.createElement("p"), { className: "settings-hint", textContent: empty })]),
    );
  }

  _openReview() {
    this.modal.close();
    if (!$("settings-panel")?.classList.contains("open")) $("settings-button")?.click();
    $("training-section")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}
