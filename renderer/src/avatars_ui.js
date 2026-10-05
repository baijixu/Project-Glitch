// Settings -> Avatar (brain/avatars.py on the Brain side): pick, import, rename
// and delete avatars. The built-in Glitch loads locally with no round trip;
// any other one's bytes come from Brain (avatar_data). main.js does the actual
// swap, through onAvatarSwap.

import { $, Modal, arrayBufferToBase64, base64ToArrayBuffer, renderDropdown } from "./ui.js";

const BUILT_IN = "Glitch";

export class AvatarsUI {
  constructor(app, { onAvatarSwap }) {
    this._app = app;
    this._onAvatarSwap = onAvatarSwap;
    this.selectEl = $("avatar-select");
    this.renameButtonEl = $("rename-avatar-button");
    this.deleteButtonEl = $("delete-avatar-button");
    this.renameInputEl = $("rename-avatar-input");
    this.renameModal = new Modal("rename-avatar-modal-backdrop", { onClose: () => (this._renaming = null) });
    this._saved = []; // [{name, kind}] -- avatars.py's list_avatars
    this.active = BUILT_IN; // what main.js boots with
    this._renaming = null;
    this._locked = false;

    this._render([]);
    this.selectEl?.addEventListener("change", () => {
      this._load(this.selectEl.value);
      this._updateEditControls();
    });
    this.renameButtonEl?.addEventListener("click", () => this._openRename());
    this.deleteButtonEl?.addEventListener("click", () => this._delete());
    $("rename-avatar-cancel-button")?.addEventListener("click", () => this.renameModal.close());
    $("rename-avatar-save-button")?.addEventListener("click", () => this._rename());
    this.renameInputEl?.addEventListener("keydown", (e) => {
      if (e.key === "Enter") this._rename();
    });
    for (const kind of ["vrm", "png"]) {
      const inputEl = $(kind === "vrm" ? "avatar-file-input" : "avatar-png-file-input");
      $(kind === "vrm" ? "import-avatar-button" : "import-avatar-png-button")?.addEventListener("click", () => inputEl?.click());
      inputEl?.addEventListener("change", () => {
        const file = inputEl.files?.[0];
        if (file) this._import(file, kind);
        inputEl.value = ""; // otherwise re-picking the same file wouldn't fire "change" again
      });
    }
  }

  get handlers() {
    return {
      // After a rename/delete on any device: follow the new name, or go back to
      // the built-in Glitch if the one on screen was deleted.
      avatars: (data) => {
        if (data.renamed && data.renamed.from === this.active) {
          this.active = data.renamed.to;
        } else if (data.deleted && data.deleted === this.active) {
          this._onAvatarSwap?.(`/${BUILT_IN}.vrm`, "vrm");
          this._app.log("avatar", `'${data.deleted}' was deleted -- back to '${BUILT_IN}'`);
          this.active = BUILT_IN;
        }
        this._render(data.avatars || []);
      },
      // The answer to our own load_avatar, or unprompted after `ready` to restore
      // the one that was active -- the same either way. `kind` is Brain's record.
      avatar_data: (data) => {
        this._onAvatarSwap?.(base64ToArrayBuffer(data.data_b64), data.kind);
        this._app.log("avatar", `switched to avatar '${data.name}' (${data.kind})`);
        this.active = data.name;
        this._render(this._saved);
      },
    };
  }

  setLocked(locked) {
    this._locked = locked;
    this._updateEditControls();
  }

  _render(avatars) {
    this._saved = avatars;
    renderDropdown(this.selectEl, [BUILT_IN, ...avatars.map((a) => a.name)], this.active);
    this._updateEditControls();
  }

  // Rename/delete only apply to a saved avatar -- the built-in Glitch ships with the app.
  _updateEditControls() {
    const builtIn = !this.selectEl?.value || this.selectEl.value === BUILT_IN;
    for (const el of [this.renameButtonEl, this.deleteButtonEl]) if (el) el.disabled = builtIn || this._locked;
  }

  // Brain remembers the choice either way; for a saved one, its avatar_data
  // answer (carrying the kind) is what does the swap.
  _load(name) {
    if (name === BUILT_IN) {
      this._onAvatarSwap?.(`/${BUILT_IN}.vrm`, "vrm");
      this._app.log("avatar", `switched to avatar '${BUILT_IN}' (vrm)`);
      this.active = name;
      this._render(this._saved);
    }
    this._app.send({ type: "load_avatar", name });
  }

  // A .vrm model or a flat .png image: shown straight away (the bytes are
  // already here), then saved to Brain.
  async _import(file, kind) {
    const buffer = await file.arrayBuffer();
    try {
      await this._onAvatarSwap?.(buffer, kind);
    } catch (err) {
      // Not saved: a broken file made active left every later load stuck on "Loading avatar...".
      this._app.log("avatar", `import failed: ${err.message || err}`);
      return this._app.flashStatus(`Couldn't load that ${kind.toUpperCase()} file`, 4000);
    }
    const name = file.name.replace(new RegExp(`\\.${kind}$`, "i"), "");
    this.active = name;
    this._render(this._saved);
    this._app.send({ type: "save_avatar", name, data_b64: arrayBufferToBase64(buffer), kind });
  }

  _openRename() {
    const name = this.selectEl?.value;
    if (!name || name === BUILT_IN || !this.renameInputEl) return;
    this._renaming = name;
    this.renameInputEl.value = name;
    this.renameModal.open();
    this.renameInputEl.focus();
    this.renameInputEl.select();
  }

  // Brain answers every device with the new list (`avatars` with `renamed`).
  _rename() {
    const from = this._renaming;
    const to = this.renameInputEl?.value.trim() || "";
    this.renameModal.close();
    if (!from || !to || to === from) return;
    this._app.send({ type: "rename_avatar", name: from, new_name: to });
  }

  _delete() {
    const name = this.selectEl?.value;
    if (!name || name === BUILT_IN) return;
    if (!window.confirm(`Delete the avatar "${name}"? This can't be undone.`)) return;
    this._app.send({ type: "delete_avatar", name });
  }
}
