// Settings -> Role-play (brain/characters.py on the Brain side): the role-play
// toggle, the engine role-play switches to, and the saved role-play profiles
// and souls. Profiles and souls are only in play while role-play is on, so
// their sections hide while it's off.

import { SavedItems } from "./saved_items.js";
import { $, Modal } from "./ui.js";

// Reserved "no profile"/"no role-play soul" entries -- brain/profiles.py's
// DEFAULT_PROFILE_NAME and brain/souls.py's DEFAULT_SOUL_NAME.
export const DEFAULT_PROFILE_NAME = "Default";
export const DEFAULT_SOUL_NAME = "Default";

// brain/profiles.py stores one freeform markdown blob per profile -- it goes
// straight into her prompt. The modal still edits it as two fields
// (identity/scenario), joined and split on this marker, chosen to look nothing
// like prose anyone would type.
const PROFILE_SCENARIO_MARKER = "\n\n=== SCENARIO ===\n";

function combineProfile({ identity, scenario }) {
  return scenario ? `${identity}${PROFILE_SCENARIO_MARKER}${scenario}` : identity;
}

// A profile saved before the split has no marker: all of it is the identity.
function splitProfile(content) {
  const idx = content.indexOf(PROFILE_SCENARIO_MARKER);
  if (idx === -1) return { identity: content, scenario: "" };
  return { identity: content.slice(0, idx), scenario: content.slice(idx + PROFILE_SCENARIO_MARKER.length) };
}

export class CharactersUI {
  constructor(app) {
    this._app = app;
    this.toggleEl = $("roleplay-toggle");
    this.badgeEl = $("roleplay-badge");
    this.profileOptionsEl = $("profile-options");
    this.soulSectionEl = $("soul-section");
    this.engineSelectEl = $("roleplay-engine-select");
    this.confirmThinkEl = $("roleplay-confirm-think-toggle");
    this.confirmModal = new Modal("roleplay-confirm-modal-backdrop");
    // On until Brain's roleplay_state says otherwise (profiles.py's default).
    this.active = true;
    this._engine = ""; // "" = keep her current engine (llm_engines.read_roleplay_engine)
    this._engineNames = [];
    this._locked = false;

    this.profiles = new SavedItems(app, {
      label: "profile",
      title: "Profile",
      reserved: DEFAULT_PROFILE_NAME,
      ids: {
        select: "profile-select", edit: "edit-profile-button", add: "new-profile-button", delete: "delete-profile-button",
        backdrop: "profile-modal-backdrop", title: "profile-modal-title", name: "profile-name",
        save: "profile-save-button", cancel: "profile-cancel-button",
      },
      fields: { identity: "profile-identity", scenario: "profile-scenario" },
      messages: { load: "load_profile", get: "get_profile", save: "save_profile", delete: "delete_profile" },
      canSave: (name, v) => name && (v.identity || v.scenario),
      toMessage: (v) => ({ content: combineProfile(v) }),
      fromContent: (data) => splitProfile(data.content),
    });
    this.souls = new SavedItems(app, {
      label: "soul",
      title: "Soul",
      reserved: DEFAULT_SOUL_NAME,
      ids: {
        select: "soul-select", edit: "edit-soul-button", add: "new-soul-button", delete: "delete-soul-button",
        backdrop: "soul-modal-backdrop", title: "soul-modal-title", name: "soul-name",
        save: "soul-save-button", cancel: "soul-cancel-button",
      },
      fields: { description: "soul-description", examples: "soul-examples" },
      messages: { load: "load_soul", get: "get_soul", save: "save_soul", delete: "delete_soul" },
      canSave: (name, v) => name && v.description,
    });
    this.profiles.render([], DEFAULT_PROFILE_NAME);
    this.souls.render([], DEFAULT_SOUL_NAME);

    this._renderToggle();
    this.toggleEl?.addEventListener("change", () => {
      const turningOn = this.toggleEl.checked;
      // The switch only moves once it's decided: turning on with a role-play
      // engine set means switching engines, so that waits for the dialog.
      this.toggleEl.checked = this.active;
      if (turningOn && this._engine) this._openConfirm();
      else this._setActive(turningOn);
    });
    this.engineSelectEl?.addEventListener("change", () => app.send({ type: "set_roleplay_engine", name: this.engineSelectEl.value }));
    $("roleplay-confirm-cancel-button")?.addEventListener("click", () => this.confirmModal.close());
    $("roleplay-confirm-enable-button")?.addEventListener("click", () => {
      const think = !!this.confirmThinkEl?.checked;
      this.confirmModal.close();
      this._setActive(true, think);
    });
  }

  get handlers() {
    return {
      roleplay_state: (data) => {
        this.active = !!data.active;
        this._renderToggle();
      },
      roleplay_engine: (data) => {
        this._engine = data.name || "";
        this._renderEngineSelect();
      },
      profiles: (data) => this.profiles.render(data.names || [], data.active || DEFAULT_PROFILE_NAME),
      profile_content: (data) => this.profiles.handleContent(data),
      souls: (data) => this.souls.render(data.names || [], data.active || DEFAULT_SOUL_NAME),
      soul_content: (data) => this.souls.handleContent(data),
    };
  }

  // The name her replies are shown under: the role-play soul's while role-play is on.
  get glitchName() {
    return this.active && this.souls.active !== DEFAULT_SOUL_NAME ? this.souls.active : "Glitch";
  }

  // The saved LLM engines, for the role-play engine dropdown.
  setEngineNames(names) {
    this._engineNames = names;
    this._renderEngineSelect();
  }

  // While a harness is in charge. The toggle picks it up on its next render.
  setLocked(locked) {
    this._locked = locked;
    this.profiles.setLocked(locked);
    this.souls.setLocked(locked);
  }

  // think only when switching to a role-play engine (from the confirm dialog);
  // without it Brain keeps her on her current engine. Brain answers with an
  // llm_engines broadcast, so the LLM dropdown follows any switch.
  _setActive(active, think) {
    this.active = active;
    this._renderToggle();
    const message = { type: "set_roleplay_active", active };
    if (think !== undefined) message.think = !!think;
    this._app.send(message);
  }

  _openConfirm() {
    const nameEl = $("roleplay-confirm-engine-name");
    if (nameEl) nameEl.textContent = this._engine;
    if (this.confirmThinkEl) this.confirmThinkEl.checked = false;
    this.confirmModal.open();
  }

  _renderToggle() {
    if (this.toggleEl) {
      this.toggleEl.checked = this.active;
      this.toggleEl.disabled = this._locked;
    }
    if (this.profileOptionsEl) this.profileOptionsEl.hidden = !this.active;
    if (this.soulSectionEl) this.soulSectionEl.hidden = !this.active;
    this.badgeEl?.classList.toggle("active", this.active);
  }

  // "Keep current engine" ("") or any saved engine.
  _renderEngineSelect() {
    const el = this.engineSelectEl;
    if (!el) return;
    el.replaceChildren();
    for (const [value, label] of [["", "Keep current engine"], ...this._engineNames.map((n) => [n, n])]) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      el.appendChild(option);
    }
    el.value = this._engineNames.includes(this._engine) ? this._engine : "";
  }
}
