// Settings -> Memory backend (brain/memory_profiles.py on the Brain side):
// pick which memory she uses -- the built-in Local file, or a Hindsight
// server someone runs themselves -- and add, edit or delete those servers,
// like the other saved kinds (SavedItems). Redraws from each
// `memory_profiles` Brain sends, so every device shows the same list.
//
// Picking one switches her to it straight away. Download / Clear Memory are
// only offered for the Local file: on a server, clearing means deleting her
// whole bank or user there -- that's left to the server's own tools.

import { $ } from "./ui.js";
import { SavedItems } from "./saved_items.js";

const LOCAL_NAME = "Local file";

export class MemoryProfilesUI {
  constructor(app) {
    this._types = [];
    this._profiles = []; // [{name, type}]
    this._active = LOCAL_NAME;
    this.items = new SavedItems(app, {
      label: "memory backend",
      title: "Memory Backend",
      reserved: LOCAL_NAME,
      ids: {
        select: "memory-profile-select", edit: "edit-memory-profile-button", add: "new-memory-profile-button",
        delete: "delete-memory-profile-button", backdrop: "memory-profile-modal-backdrop", title: "memory-profile-modal-title",
        name: "memory-profile-name", save: "memory-profile-save-button", cancel: "memory-profile-cancel-button",
      },
      fields: { backend: "memory-profile-backend", url: "memory-profile-url", api_key: "memory-profile-api-key", space: "memory-profile-space" },
      messages: { load: "set_memory_profile", get: "get_memory_profile", save: "save_memory_profile", delete: "delete_memory_profile" },
      canSave: (name, values) => {
        if (name && values.url) return true;
        window.alert("A memory backend needs a name and its server URL.");
        return false;
      },
      onSelect: (name) => {
        this._showError("");
        this.items.load(name);
      },
      onOpen: (values) => {
        if (!values.backend) $("memory-profile-backend").value = this._types[0]?.type || "";
        this._renderBackendFields();
      },
      optionLabel: (name) => {
        const profile = this._profiles.find((p) => p.name === name);
        if (!profile) return "Local file (built in, no setup)";
        const label = this._types.find((t) => t.type === profile.type)?.label || profile.type;
        return name === label ? label : `${name} (${label})`;
      },
      confirmDelete: (name) =>
        `Delete the memory backend "${name}"? Only the saved connection is removed -- the memories on that server stay there.` +
        (name === this._active ? " She'll switch to the Local file." : ""),
    });
    $("memory-profile-backend")?.addEventListener("change", () => this._renderBackendFields());
  }

  handleState(data) {
    this._showError(data.error || "");
    if (data.error && !data.profiles) return;
    this._types = data.types || [];
    this._profiles = data.profiles || [];
    this._active = data.active || LOCAL_NAME;
    $("memory-profile-backend")?.replaceChildren(
      ...this._types.map((t) => Object.assign(document.createElement("option"), { value: t.type, textContent: t.label })),
    );
    this.items.render(this._profiles.map((p) => p.name), this._active);
    for (const id of ["download-memory-button", "clear-memory-button"]) {
      if ($(id)) $(id).hidden = this._active !== LOCAL_NAME;
    }
  }

  handleContent(data) {
    this.items.handleContent(data);
  }

  setLocked(locked) {
    this.items.setLocked(locked);
  }

  // What the bank / user id box is called, and its default, depend on the type.
  _renderBackendFields() {
    const t = this._types.find((x) => x.type === $("memory-profile-backend")?.value);
    if (!t) return;
    $("memory-profile-space-label").textContent = t.space_label;
    $("memory-profile-space").placeholder = `e.g. ${t.default_space} (the default if left blank)`;
  }

  _showError(text) {
    const el = $("memory-profile-error");
    if (!el) return;
    el.textContent = text;
    el.hidden = !text;
  }
}
