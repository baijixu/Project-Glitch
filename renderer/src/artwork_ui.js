// Settings -> Artwork (brain/pictures.py on the Brain side): every picture she's shown you, newest
// first, to look at full size or delete for good. Rebuilt from each `artwork` message, so every
// device shows the same set; the pictures themselves load only once the fold is opened.

import { $ } from "./ui.js";

export class ArtworkUI {
  constructor(app) {
    this._app = app;
    this.listEl = $("artwork-list");
    this.countEl = $("artwork-count");
    this.detailsEl = $("artwork-details");
    this._names = [];
    this.detailsEl?.addEventListener("toggle", () => this.detailsEl.open && this._render());
  }

  get handlers() {
    return {
      artwork: (data) => {
        this._names = data.names || [];
        this._app.keepOnlyPictures(this._names);
        if (this.countEl) this.countEl.textContent = this._names.length ? ` (${this._names.length})` : "";
        if (this.detailsEl?.open) this._render();
      },
    };
  }

  _render() {
    if (!this.listEl) return;
    this.listEl.replaceChildren();
    if (!this._names.length) {
      this.listEl.appendChild(Object.assign(document.createElement("p"), { className: "settings-hint", textContent: "No pictures yet." }));
      return;
    }
    for (const name of this._names) {
      const tile = document.createElement("div");
      tile.className = "artwork-tile";
      const img = Object.assign(document.createElement("img"), { alt: "" });
      img.addEventListener("click", () => tile.classList.toggle("expanded"));
      this._app.loadPicture(name).then((url) => (url ? (img.src = url) : tile.remove()));
      const meta = document.createElement("div");
      meta.className = "artwork-tile-meta";
      meta.appendChild(Object.assign(document.createElement("span"), { textContent: when(name) }));
      const remove = Object.assign(document.createElement("button"), {
        className: "icon-button lesson-button danger",
        textContent: "🗑️",
        title: "Delete this picture",
      });
      remove.addEventListener("click", () => {
        if (window.confirm("Delete this picture for good? It goes from her chat too.")) {
          this._app.send({ type: "delete_picture", name });
        }
      });
      meta.appendChild(remove);
      tile.append(img, meta);
      this.listEl.appendChild(tile);
    }
  }
}

// "20261014-221906-cabin.png" -> "Oct 14, 10:19 PM" (pictures.py names them by when they were kept).
function when(name) {
  const m = /^(\d{4})(\d\d)(\d\d)-(\d\d)(\d\d)/.exec(name);
  if (!m) return "";
  const date = new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]);
  return date.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}
