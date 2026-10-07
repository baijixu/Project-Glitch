// Settings -> Background (brain/backgrounds.py on the Brain side): pick, add or delete the
// picture behind her. Brain keeps them and sends whichever is picked to every device, so
// nothing is shown here until it arrives -- the same picture everywhere.

import { $, arrayBufferToBase64, base64ToArrayBuffer, renderDropdown } from "./ui.js";

const NONE = "None";
const KINDS = { "image/png": "png", "image/jpeg": "jpg", "image/webp": "webp" };
const MIME = { png: "image/png", jpg: "image/jpeg", webp: "image/webp" };

export class BackgroundsUI {
  constructor(app) {
    this._app = app;
    this.selectEl = $("background-select");
    this.deleteButtonEl = $("delete-background-button");
    this.inputEl = $("background-file-input");
    this._url = null; // the picture on screen, as an object URL
    renderDropdown(this.selectEl, [NONE], NONE); // until Brain's list arrives

    this.selectEl?.addEventListener("change", () => {
      app.send({ type: "set_background", name: this.selectEl.value === NONE ? "" : this.selectEl.value });
    });
    this.deleteButtonEl?.addEventListener("click", () => this._delete());
    $("add-background-button")?.addEventListener("click", () => this.inputEl?.click());
    this.inputEl?.addEventListener("change", () => {
      const file = this.inputEl.files?.[0];
      if (file) this._add(file);
      this.inputEl.value = ""; // otherwise re-picking the same file wouldn't fire "change" again
    });
  }

  get handlers() {
    return {
      backgrounds: (data) => {
        renderDropdown(this.selectEl, [NONE, ...(data.names || [])], data.active || NONE);
        if (this.deleteButtonEl) this.deleteButtonEl.disabled = !data.active;
        if (!data.active) this._show(null);
      },
      background_data: (data) => {
        this._show(new Blob([base64ToArrayBuffer(data.data_b64)], { type: MIME[data.kind] }));
        this._app.log("background", `showing background '${data.name}'`);
      },
    };
  }

  // The page's backdrop (style.css's --backdrop): the canvas is see-through, so it shows behind her.
  _show(blob) {
    if (this._url) URL.revokeObjectURL(this._url);
    this._url = blob ? URL.createObjectURL(blob) : null;
    if (this._url) document.body.style.setProperty("--backdrop", `url("${this._url}")`);
    else document.body.style.removeProperty("--backdrop");
    document.body.classList.toggle("has-backdrop", !!this._url);
  }

  async _add(file) {
    const kind = KINDS[file.type];
    if (!kind) return this._app.flashStatus("Pick a PNG, JPG or WebP image", 4000);
    const name = file.name.replace(/\.[^.]+$/, "");
    this._app.send({ type: "save_background", name, data_b64: arrayBufferToBase64(await file.arrayBuffer()), kind });
  }

  _delete() {
    const name = this.selectEl?.value;
    if (!name || name === NONE) return;
    if (!window.confirm(`Delete the background "${name}"? This can't be undone.`)) return;
    this._app.send({ type: "delete_background", name });
  }
}
