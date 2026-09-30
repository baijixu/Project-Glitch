// One kind of thing saved Brain-side that Settings picks from a dropdown and
// edits in a modal: role-play profiles, souls, speech engines, LLM engines and
// harnesses all work the same way --
//
//   dropdown ...... a reserved first entry ("Default"/"None", never a saved file,
//                   never sent by Brain) plus every saved name; picking one
//                   loads it
//   ✏️ / 🗑️ ....... act on the picked one; off for the reserved entry and while
//                   a harness has locked Settings
//   ✏️ ............ asks Brain for what's saved (get_*), then opens the modal
//                   when that answer (*_content) comes back
//   ＋ ............ the same modal, empty; Save sends save_*
//
// A kind supplies its ids, its fields, and hooks for what it does differently.

import { $, Modal, renderDropdown } from "./ui.js";

export class SavedItems {
  // label ...... for the delete confirmation ("profile", "speech engine")
  // title ...... for the modal's title ("Profile" -> "New Profile"/"Edit Profile")
  // reserved ... the reserved first dropdown entry
  // ids ........ {select, edit, add, delete, backdrop, title, name, save, cancel}
  // fields ..... {field: elementId} -- a checkbox reads/writes .checked, anything else .value (trimmed)
  // messages ... {load, get, save, delete} message types
  // defaults ................... a new item's starting values (default: all empty)
  // canSave(name, values) ....... whether Save sends anything (default: a name and an endpoint)
  // toMessage(values) ........... the save message's fields (default: the values as they are)
  // fromContent(data) ........... the modal's values from Brain's *_content (default: the same keys)
  // onSelect(name) .............. picking a dropdown entry (default: load it)
  // onOpen(values) .............. after the modal is filled in and shown
  constructor(app, config) {
    this._app = app;
    this._config = config;
    const { ids } = config;
    this.selectEl = $(ids.select);
    this.editButtonEl = $(ids.edit);
    this.deleteButtonEl = $(ids.delete);
    this.titleEl = $(ids.title);
    this.nameEl = $(ids.name);
    this.fieldEls = Object.fromEntries(Object.entries(config.fields).map(([field, id]) => [field, $(id)]));
    this.modal = new Modal(ids.backdrop);
    this.names = [];
    this.active = config.reserved;
    this._locked = false;
    this._pendingEdit = null; // the name whose *_content should open the modal

    this.selectEl?.addEventListener("change", () => (config.onSelect ?? ((name) => this.load(name)))(this.selectEl.value));
    this.editButtonEl?.addEventListener("click", () => this.edit(this.selectEl?.value));
    $(ids.add)?.addEventListener("click", () => this.open());
    this.deleteButtonEl?.addEventListener("click", () => this.delete(this.selectEl?.value));
    $(ids.cancel)?.addEventListener("click", () => this.modal.close());
    $(ids.save)?.addEventListener("click", () => this.save());
  }

  // The saved list and which one is active, from Brain (`profiles`, `llm_engines`...).
  render(names, active) {
    this.names = names;
    this.active = active;
    renderDropdown(this.selectEl, [this._config.reserved, ...names], active);
    this.updateEditControls();
  }

  load(name) {
    if (!name) return;
    this._app.send({ type: this._config.messages.load, name });
    this.active = name;
    this.updateEditControls();
  }

  setLocked(locked) {
    this._locked = locked;
    this.updateEditControls();
  }

  updateEditControls() {
    const value = this.selectEl?.value;
    const disabled = this._locked || !value || value === this._config.reserved;
    if (this.editButtonEl) this.editButtonEl.disabled = disabled;
    if (this.deleteButtonEl) this.deleteButtonEl.disabled = disabled;
  }

  edit(name) {
    if (!name || name === this._config.reserved) return;
    this._pendingEdit = name;
    this._app.send({ type: this._config.messages.get, name });
  }

  // Brain's answer to edit(): opens the modal, unless it's for some other request.
  handleContent(data) {
    if (data.name !== this._pendingEdit) return;
    this._pendingEdit = null;
    this.open(data.name, (this._config.fromContent ?? ((d) => d))(data));
  }

  delete(name) {
    if (!name || name === this._config.reserved) return;
    if (!window.confirm(`Delete the saved ${this._config.label} "${name}"? This can't be undone.`)) return;
    this._app.send({ type: this._config.messages.delete, name });
  }

  // Empty for a new one; saving under the same name overwrites it, a new name
  // saves a new one alongside it.
  open(name = "", values = {}) {
    if (!this.modal.el) return;
    const filled = { ...this._config.defaults, ...values };
    if (this.titleEl) this.titleEl.textContent = `${name ? "Edit" : "New"} ${this._config.title}`;
    if (this.nameEl) this.nameEl.value = name;
    for (const [field, el] of Object.entries(this.fieldEls)) {
      if (!el) continue;
      if (el.type === "checkbox") el.checked = !!filled[field];
      else el.value = filled[field] ?? "";
    }
    this._config.onOpen?.(filled);
    this.modal.open();
  }

  values() {
    return Object.fromEntries(
      Object.entries(this.fieldEls).map(([field, el]) => [field, el?.type === "checkbox" ? !!el.checked : el?.value.trim() || ""]),
    );
  }

  save() {
    const name = this.nameEl?.value.trim() || "";
    const values = this.values();
    const canSave = this._config.canSave ?? ((n, v) => n && v.endpoint);
    if (!canSave(name, values)) return;
    this._app.send({ type: this._config.messages.save, name, ...(this._config.toMessage ?? ((v) => v))(values) });
    this.modal.close();
  }
}
