// Settings -> LLM -> Sampling (brain/sampling.py on the Brain side): pick a
// sampling profile, tune its six boxes, and save them as a profile. Like
// TrainingUI it's self-contained -- finds its own elements by id, only needs
// `send` -- and redraws from each `sampling_state` Brain sends, so every
// device shows the same profiles.
//
// Picking a profile in the dropdown makes it active straight away (Brain
// applies it from her next reply). Editing a box does nothing until Save:
// the boxes then differ from the picked profile, and a note says so.
//
// A state that arrives while a box has focus (another device saved, say)
// waits until the box loses focus, so it can't overwrite what's being typed.

const KEYS = ["temperature", "top_p", "top_k", "min_p", "presence_penalty", "repeat_penalty"];

export class SamplingUI {
  constructor({ send }) {
    this._send = send;
    const $ = (id) => document.getElementById(id);
    this.panelEl = $("sampling-panel");
    this.selectEl = $("sampling-select");
    this.nameEl = $("sampling-name");
    this.saveButtonEl = $("sampling-save-button");
    this.deleteButtonEl = $("sampling-delete-button");
    this.dirtyEl = $("sampling-dirty");
    this.errorEl = $("sampling-error");
    this.inputs = Object.fromEntries(KEYS.map((key) => [key, $(`sampling-${key}`)]));
    this._state = null; // the last sampling_state from Brain
    this._pendingState = null; // one that arrived mid-edit, applied on blur
    this._locked = false; // true while a harness is in charge (see setLocked)

    this.selectEl?.addEventListener("change", () => {
      this._showError("");
      this._send({ type: "set_sampling_profile", name: this.selectEl.value });
    });
    for (const input of Object.values(this.inputs)) input?.addEventListener("input", () => this._updateDirty());
    this.panelEl?.addEventListener("focusout", () => this._applyPendingSoon());
    this.saveButtonEl?.addEventListener("click", () => this._save());
    this.deleteButtonEl?.addEventListener("click", () => this._delete());
  }

  handleState(data) {
    if (data.error) {
      // A refused save/delete, sent only to this device: nothing changed on
      // Brain, so keep the boxes as typed and just say why.
      this._showError(data.error);
      return;
    }
    this._showError("");
    if (this._editing()) {
      this._pendingState = data;
      return;
    }
    this._render(data);
  }

  // The harness lock (brain_client.js's _renderHarnessState) disables every
  // control in the section; lifting it re-enables them all, so Delete has to
  // be re-derived here or it would come back on for a built-in profile.
  setLocked(locked) {
    this._locked = locked;
    this._updateControls();
    // Disabling the box being typed in drops its focus without a focusout,
    // so a state that was waiting on that box is applied from here instead.
    this._applyPendingSoon();
  }

  _render(data) {
    this._state = data;
    this._pendingState = null;
    const names = Object.keys(data.profiles || {});
    if (this.selectEl) {
      this.selectEl.replaceChildren(
        ...names.map((name) => {
          const option = document.createElement("option");
          option.value = name;
          option.textContent = name;
          return option;
        }),
      );
      this.selectEl.value = data.active;
    }
    const values = (data.profiles || {})[data.active] || {};
    for (const key of KEYS) {
      if (this.inputs[key]) this.inputs[key].value = values[key] ?? "";
    }
    // Built-in names can't be saved over, so the name box starts empty for them
    // and with the profile's own name for a saved one (Save then updates it).
    if (this.nameEl) this.nameEl.value = this._isBuiltin(data.active) ? "" : data.active;
    this._updateDirty();
    this._updateControls();
  }

  _isBuiltin(name) {
    return (this._state?.builtin || []).includes(name);
  }

  _currentValues() {
    const values = {};
    for (const key of KEYS) {
      const text = this.inputs[key]?.value.trim() ?? "";
      if (text !== "") values[key] = Number(text);
    }
    return values;
  }

  _updateDirty() {
    if (!this.dirtyEl || !this._state) return;
    const saved = (this._state.profiles || {})[this._state.active] || {};
    const current = this._currentValues();
    const same =
      KEYS.every((key) => (key in saved) === (key in current) && (!(key in saved) || Number(saved[key]) === current[key]));
    this.dirtyEl.hidden = same;
  }

  _updateControls() {
    if (this.deleteButtonEl) this.deleteButtonEl.disabled = this._locked || !this._state || this._isBuiltin(this._state.active);
  }

  _save() {
    const name = this.nameEl?.value.trim() || "";
    if (!name) {
      this._showError("Give the profile a name first.");
      this.nameEl?.focus();
      return;
    }
    for (const key of KEYS) {
      const input = this.inputs[key];
      if (input && input.value.trim() !== "" && !input.checkValidity()) {
        this._showError(`${input.closest("label")?.querySelector("span")?.textContent || key}: ${input.validationMessage}`);
        input.focus();
        return;
      }
    }
    this._showError("");
    this._send({ type: "save_sampling_profile", name, values: this._currentValues() });
  }

  _delete() {
    const name = this._state?.active;
    if (!name || this._isBuiltin(name)) return;
    if (!window.confirm(`Delete the sampling profile "${name}"? She goes back to the default.`)) return;
    this._send({ type: "delete_sampling_profile", name });
  }

  _editing() {
    const active = document.activeElement;
    return (
      !!active && active.tagName === "INPUT" && !active.disabled && !!this.panelEl?.contains(active) // disabled = locked, not being typed in
    );
  }

  // Blur fires before focus lands on the next element, so check a tick later
  // whether focus simply moved to another box in the panel.
  _applyPendingSoon() {
    setTimeout(() => {
      if (this._pendingState && !this._editing()) this._render(this._pendingState);
    }, 0);
  }

  _showError(message) {
    if (!this.errorEl) return;
    this.errorEl.hidden = !message;
    this.errorEl.textContent = message || "";
  }
}
