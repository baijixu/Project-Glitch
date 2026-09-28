// Settings -> Memory backend (brain/memory_profiles.py on the Brain side):
// pick which memory she uses -- the built-in Local file, or a Hindsight or Mem0
// server someone runs themselves -- and add, edit or delete those servers.
// Self-contained like SamplingUI: finds its own elements by id, only needs
// `send`, and redraws from each `memory_profiles` Brain sends, so every
// device shows the same list.
//
// Picking one switches her to it straight away. Download / Clear Memory are
// only offered for the Local file: on a server, clearing means deleting her
// whole bank or user there -- that's left to the server's own tools.

const LOCAL_NAME = "Local file";

export class MemoryProfilesUI {
  constructor({ send }) {
    this._send = send;
    const $ = (id) => document.getElementById(id);
    this.selectEl = $("memory-profile-select");
    this.editButtonEl = $("edit-memory-profile-button");
    this.newButtonEl = $("new-memory-profile-button");
    this.deleteButtonEl = $("delete-memory-profile-button");
    this.errorEl = $("memory-profile-error");
    this.downloadButtonEl = $("download-memory-button");
    this.clearButtonEl = $("clear-memory-button");
    this.backdropEl = $("memory-profile-modal-backdrop");
    this.titleEl = $("memory-profile-modal-title");
    this.nameEl = $("memory-profile-name");
    this.backendEl = $("memory-profile-backend");
    this.urlEl = $("memory-profile-url");
    this.apiKeyEl = $("memory-profile-api-key");
    this.spaceLabelEl = $("memory-profile-space-label");
    this.spaceEl = $("memory-profile-space");
    this._state = { profiles: [], active: LOCAL_NAME, types: [] };
    this._editing = null; // the profile being edited, or null for a new one
    this._locked = false;

    this.selectEl?.addEventListener("change", () => {
      this._showError("");
      this._send({ type: "set_memory_profile", name: this.selectEl.value });
    });
    this.newButtonEl?.addEventListener("click", () => this._openModal(null));
    this.editButtonEl?.addEventListener("click", () => {
      const name = this.selectEl?.value;
      if (name && name !== LOCAL_NAME) this._send({ type: "get_memory_profile", name });
    });
    this.deleteButtonEl?.addEventListener("click", () => this._delete());
    this.backendEl?.addEventListener("change", () => this._renderBackendFields());
    $("memory-profile-cancel-button")?.addEventListener("click", () => this.close());
    $("memory-profile-save-button")?.addEventListener("click", () => this._save());
    this.backdropEl?.addEventListener("click", (e) => {
      if (e.target === this.backdropEl) this.close();
    });
    this._render();
  }

  handleState(data) {
    if (data.error) {
      this._showError(data.error);
      if (!data.profiles) return;
    } else {
      this._showError("");
    }
    this._state = { profiles: data.profiles || [], active: data.active || LOCAL_NAME, types: data.types || [] };
    this._render();
  }

  // The ✏️ editor's contents (memory_profile_content).
  handleContent(data) {
    if (!data.profile || !data.profile.type) {
      this._showError(`Couldn't open "${data.name}".`);
      return;
    }
    this._openModal({ name: data.name, ...data.profile });
  }

  // The harness lock disables every control in the section; lifting it has to
  // re-derive ✏️/🗑️, or they'd come back on for the built-in Local file.
  setLocked(locked) {
    this._locked = locked;
    this._updateButtons();
  }

  isOpen() {
    return !!this.backdropEl && !this.backdropEl.hidden;
  }

  close() {
    if (this.backdropEl) this.backdropEl.hidden = true;
  }

  _render() {
    if (!this.selectEl) return;
    this.selectEl.replaceChildren();
    const local = document.createElement("option");
    local.value = LOCAL_NAME;
    local.textContent = "Local file (built in, no setup)";
    this.selectEl.appendChild(local);
    for (const profile of this._state.profiles) {
      const option = document.createElement("option");
      option.value = profile.name;
      const label = this._label(profile.type);
      option.textContent = profile.name === label ? label : `${profile.name} (${label})`;
      this.selectEl.appendChild(option);
    }
    this.selectEl.value = this._state.active;
    const isLocal = this._state.active === LOCAL_NAME;
    if (this.downloadButtonEl) this.downloadButtonEl.hidden = !isLocal;
    if (this.clearButtonEl) this.clearButtonEl.hidden = !isLocal;
    this._updateButtons();
  }

  _updateButtons() {
    const builtIn = this.selectEl?.value === LOCAL_NAME;
    if (this.editButtonEl) this.editButtonEl.disabled = this._locked || builtIn;
    if (this.deleteButtonEl) this.deleteButtonEl.disabled = this._locked || builtIn;
    if (this.selectEl) this.selectEl.disabled = this._locked;
    if (this.newButtonEl) this.newButtonEl.disabled = this._locked;
  }

  _label(type) {
    return this._state.types.find((t) => t.type === type)?.label || type;
  }

  _openModal(profile) {
    if (!this.backdropEl) return;
    this._editing = profile;
    this.titleEl.textContent = profile ? "Edit Memory Backend" : "New Memory Backend";
    this.backendEl.replaceChildren();
    for (const t of this._state.types) {
      const option = document.createElement("option");
      option.value = t.type;
      option.textContent = t.label;
      this.backendEl.appendChild(option);
    }
    this.nameEl.value = profile?.name || "";
    this.backendEl.value = profile?.type || this._state.types[0]?.type || "";
    this.urlEl.value = profile?.url || "";
    this.apiKeyEl.value = profile?.api_key || "";
    this.spaceEl.value = profile?.space || "";
    this._renderBackendFields();
    this.backdropEl.hidden = false;
    this.nameEl.focus();
  }

  // What the bank / user id box is called, and its default, depend on the type.
  _renderBackendFields() {
    const t = this._state.types.find((x) => x.type === this.backendEl?.value);
    if (!t) return;
    this.spaceLabelEl.textContent = t.space_label;
    this.spaceEl.placeholder = `e.g. ${t.default_space} (the default if left blank)`;
    this.urlEl.placeholder = t.type === "mem0" ? "e.g. http://localhost:8888" : "e.g. http://localhost:8899";
  }

  _save() {
    const name = this.nameEl.value.trim();
    const url = this.urlEl.value.trim();
    if (!name || !url) {
      window.alert("A memory backend needs a name and its server URL.");
      return;
    }
    this._send({
      type: "save_memory_profile",
      name,
      original_name: this._editing?.name || "",
      backend: this.backendEl.value,
      url,
      api_key: this.apiKeyEl.value.trim(),
      space: this.spaceEl.value.trim(),
    });
    this.close();
  }

  _delete() {
    const name = this.selectEl?.value;
    if (!name || name === LOCAL_NAME) return;
    const note = name === this._state.active ? " She'll switch to the Local file." : "";
    if (!window.confirm(`Delete the memory backend "${name}"? Only the saved connection is removed -- the memories on that server stay there.${note}`)) return;
    this._send({ type: "delete_memory_profile", name });
  }

  _showError(text) {
    if (!this.errorEl) return;
    this.errorEl.textContent = text;
    this.errorEl.hidden = !text;
  }
}
