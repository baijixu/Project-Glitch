// The 🧠 "Her mind" window (top left, under the inbox): what went into her last reply and what's
// waiting on him -- the memories recalled for it, the memory proposals in his review list, and how
// big her soul.md is and when it last changed. Asked for (`get_mind`) each time it opens or he
// hits Refresh, so it's never stale; its Growth tab asks for `get_growth` and draws the charts
// (growth_ui.js). Memories and proposals are model-written: textContent only.

import { $, Modal } from "./ui.js";
import { renderGrowth } from "./growth_ui.js";

const SHOWN_PENDING = 5; // the rest are a click away in Settings
const CHARS_PER_PAGE = 3000; // a printed page, roughly -- to read soul.md's size at a glance

export class MindUI {
  constructor(app) {
    this._app = app;
    this.modal = new Modal("mind-modal-backdrop");
    $("mind-button")?.addEventListener("click", () => this._open());
    this._tab = "now";
    this._period = "week";
    this._growth = null;
    $("mind-refresh-button")?.addEventListener("click", () => this._ask());
    for (const tab of document.querySelectorAll("#mind-modal [data-tab]")) tab.addEventListener("click", () => this._showTab(tab.dataset.tab));
    for (const button of document.querySelectorAll("#mind-modal [data-period]")) {
      button.addEventListener("click", () => {
        this._period = button.dataset.period;
        this._mark("[data-period]", "period", this._period);
        if (this._growth) renderGrowth(this._growth, this._period);
      });
    }
    $("mind-close-button")?.addEventListener("click", () => this.modal.close());
    $("mind-review-button")?.addEventListener("click", () => this._openReview());
  }

  get handlers() {
    return {
      mind: (data) => this._render(data),
      growth: (data) => {
        this._growth = data;
        renderGrowth(data, this._period);
      },
    };
  }

  _open() {
    this._ask();
    this.modal.open();
  }

  _ask() {
    this._app.send({ type: this._tab === "growth" ? "get_growth" : "get_mind" });
  }

  _showTab(tab) {
    this._tab = tab;
    this._mark("[data-tab]", "tab", tab);
    $("mind-now").hidden = tab !== "now";
    $("mind-growth").hidden = tab !== "growth";
    this._ask();
  }

  _mark(selector, key, value) {
    for (const b of document.querySelectorAll(`#mind-modal ${selector}`)) b.classList.toggle("active", b.dataset[key] === value);
  }

  _render({ recalled = [], pending = [], soul = {} }) {
    this._list($("mind-recalled"), recalled, "None -- send her a message and the memories she recalls for it show up here.");
    $("mind-pending-count").textContent = pending.length ? ` (${pending.length})` : "";
    const facts = pending.slice(0, SHOWN_PENDING).map((p) => (p.type ? `${p.type}: ${p.fact}` : p.fact));
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
