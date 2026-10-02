// Settings -> Voice, LLM and Harness (brain/engines.py on the Brain side): the
// saved speech engines (with their custom voices), LLM engines and harnesses,
// and the two status lights for them.

import { SavedItems } from "./saved_items.js";
import { $, Modal, renderDropdown } from "./ui.js";

// The reserved "nothing" entries -- brain/tts_engines.py's, llm_engines.py's
// and harness.py's NONE_NAME.
const NONE = "None";
// The voice picker's "use the engine's own default voice" entry -- sent to
// Brain as "" (see brain/engines.py's _set_tts_voice).
const DEFAULT_VOICE = "Default";

// A status light: green (in use), blue (reachable), red (unreachable), or
// plain grey (nothing known yet / nothing to reach).
function paintLight(el, color) {
  if (!el) return;
  el.classList.remove("red", "blue", "green");
  if (color) el.classList.add(color);
}

function reachabilityColor(reachable) {
  if (reachable === true) return "blue";
  if (reachable === false) return "red";
  return null; // null = not configured, undefined = not checked yet: both grey
}

// ============================================================================
// Speech engines
// ============================================================================

export class SpeechEnginesUI {
  constructor(app) {
    this._app = app;
    this.lightEl = $("tts-light");
    this.voiceSelectEl = $("tts-voice-select");
    this.createVoiceButtonEl = $("create-tts-voice-button");
    this.blendModal = new Modal("kokoro-blend-modal-backdrop");
    this._reachable = {}; // name -> tts_health's `reachable`
    this._voicesFor = ""; // which engine the voice picker is showing
    this.engines = new SavedItems(app, {
      label: "speech engine",
      title: "Speech Engine",
      reserved: NONE,
      ids: {
        select: "tts-engine-select", edit: "edit-tts-engine-button", add: "new-tts-engine-button", delete: "delete-tts-engine-button",
        backdrop: "tts-engine-modal-backdrop", title: "tts-engine-modal-title", name: "tts-engine-name",
        save: "tts-engine-save-button", cancel: "tts-engine-cancel-button",
      },
      fields: {
        endpoint: "tts-engine-endpoint", api_key: "tts-engine-api-key", voice: "tts-engine-voice",
        model: "tts-engine-model", voices_dir: "tts-engine-voices-dir",
      },
      messages: { load: "load_tts_engine", get: "get_tts_engine", save: "save_tts_engine", delete: "delete_tts_engine" },
      onSelect: (name) => {
        this.engines.load(name);
        this._renderLight();
        this._requestVoices(name);
      },
    });
    this.engines.render([], NONE);
    this._renderLight();

    this.voiceSelectEl?.addEventListener("change", () => this._setVoice(this.voiceSelectEl.value));
    this.createVoiceButtonEl?.addEventListener("click", () => this._openBlend());
    $("kokoro-blend-cancel-button")?.addEventListener("click", () => this.blendModal.close());
    $("kokoro-blend-create-button")?.addEventListener("click", () => this._createBlend());
  }

  get handlers() {
    return {
      tts_engines: (data) => {
        this.engines.render(data.names || [], data.active || NONE);
        this._renderLight();
        this._requestVoices(this.engines.active);
      },
      tts_engine_content: (data) => this.engines.handleContent(data),
      tts_voices: (data) => {
        // Dropped if the user has already picked a different engine since.
        if (data.name === (this.engines.selectEl?.value || NONE)) this._renderVoices(data);
        if (data.error) this._app.flashStatus(data.error, 6000);
      },
      tts_health: (data) => {
        this._reachable[data.name] = data.reachable;
        this._renderLight();
      },
    };
  }

  setLocked(locked) {
    this.engines.setLocked(locked);
  }

  // There's always an active engine, so green is "the one in use"; blue/red
  // only show while looking at another saved one. None stays grey.
  _renderLight() {
    const name = this.engines.selectEl?.value || this.engines.active;
    if (name === NONE) return paintLight(this.lightEl, null);
    paintLight(this.lightEl, name && name === this.engines.active ? "green" : reachabilityColor(name ? this._reachable[name] : undefined));
  }

  // Which custom voices the engine has (brain/kokoro_voices.py), and whether
  // it can make more -- nothing to ask about for None.
  _requestVoices(name) {
    if (!name || name === NONE) {
      this._renderVoices({ name: name || "", voices: [], active_voice: "", can_create_voice: false });
      return;
    }
    this._app.send({ type: "get_tts_voices", name });
  }

  // The picker only shows once the engine has a custom voice (with only its
  // default there's nothing to pick); "create" shows when it has a voices folder.
  _renderVoices(data) {
    this._voicesFor = data.name;
    if (this.createVoiceButtonEl) this.createVoiceButtonEl.hidden = !data.can_create_voice;
    if (!this.voiceSelectEl) return;
    if (!data.voices || data.voices.length === 0) {
      this.voiceSelectEl.hidden = true;
      return;
    }
    renderDropdown(this.voiceSelectEl, [DEFAULT_VOICE, ...data.voices], data.active_voice || DEFAULT_VOICE);
    this.voiceSelectEl.hidden = false;
  }

  _setVoice(voice) {
    if (!this._voicesFor) return;
    this._app.send({ type: "set_tts_voice", name: this._voicesFor, voice: voice === DEFAULT_VOICE ? "" : voice });
  }

  _openBlend() {
    if (!this.blendModal.el) return;
    for (const id of ["kokoro-blend-name", "kokoro-blend-spec"]) if ($(id)) $(id).value = "";
    this.blendModal.open();
  }

  // Kokoro's own blend syntax ("voice1(2)+voice2(1)"), sent through unparsed --
  // whatever the engine's server accepts or rejects is what counts.
  _createBlend() {
    const voiceName = $("kokoro-blend-name")?.value.trim() || "";
    const spec = $("kokoro-blend-spec")?.value.trim() || "";
    if (!this._voicesFor || !voiceName || !spec) return;
    this._app.send({ type: "combine_kokoro_voice", name: this._voicesFor, voice_name: voiceName, spec });
    this.blendModal.close();
  }
}

// ============================================================================
// LLM engines
// ============================================================================

export class LlmEnginesUI {
  // onEnginesChanged(names): the saved engines changed (role-play's engine dropdown lists them too).
  constructor(app, { onEnginesChanged }) {
    this._app = app;
    this._onEnginesChanged = onEnginesChanged;
    this.providerEl = $("llm-engine-provider");
    this.endpointEl = $("llm-engine-endpoint");
    this.modelOptionsEl = $("llm-engine-model-options");
    // Which endpoint the latest get_llm_models asked about -- an answer for an
    // endpoint since edited away from is dropped.
    this._pendingModelsFor = null;
    this.engines = new SavedItems(app, {
      label: "LLM engine",
      title: "LLM Engine",
      reserved: NONE,
      ids: {
        select: "llm-engine-select", edit: "edit-llm-engine-button", add: "new-llm-engine-button", delete: "delete-llm-engine-button",
        backdrop: "llm-engine-modal-backdrop", title: "llm-engine-modal-title", name: "llm-engine-name",
        save: "llm-engine-save-button", cancel: "llm-engine-cancel-button",
      },
      fields: {
        provider: "llm-engine-provider", endpoint: "llm-engine-endpoint", model: "llm-engine-model",
        api_key: "llm-engine-api-key", think: "llm-engine-think-toggle",
      },
      messages: { load: "load_llm_engine", get: "get_llm_engine", save: "save_llm_engine", delete: "delete_llm_engine" },
      defaults: { provider: "openai" },
      // Think only means anything on Ollama's own API (see OllamaLLM's docstring).
      toMessage: (v) => ({ ...v, provider: v.provider || "openai", think: v.provider === "ollama" && v.think }),
      fromContent: (data) => ({ ...data, provider: data.provider || "openai", think: !!data.think }),
      onOpen: (v) => {
        this._updateForProvider(v.provider);
        this._showModelOptions([]); // not the last engine's
        if (v.endpoint) this._fetchModels(); // editing: the endpoint's already known
      },
    });
    this.engines.render([], NONE);

    this.providerEl?.addEventListener("change", () => this._updateForProvider(this.providerEl.value));
    $("fetch-llm-models-button")?.addEventListener("click", () => this._fetchModels());
    // On blur too: typing an endpoint and tabbing to Model has real choices waiting.
    this.endpointEl?.addEventListener("blur", () => this._fetchModels());
    // Picking a fetched model fills the Model field (still typeable for anything else).
    this.modelOptionsEl?.addEventListener("change", () => {
      if (this.modelOptionsEl.value) $("llm-engine-model").value = this.modelOptionsEl.value;
    });
  }

  get handlers() {
    return {
      llm_engines: (data) => {
        this.engines.render(data.names || [], data.active || NONE);
        this._onEnginesChanged(this.engines.names);
      },
      llm_engine_content: (data) => this.engines.handleContent(data),
      llm_models: (data) => {
        if (data.endpoint === this._pendingModelsFor) this._showModelOptions(data.models || []);
      },
    };
  }

  setLocked(locked) {
    this.engines.setLocked(locked);
  }

  // Ollama's native API is its own base URL (no /v1), and the only one where
  // think does anything -- so the think row only shows for it.
  _updateForProvider(provider) {
    const isOllama = provider === "ollama";
    for (const id of ["llm-engine-think-row", "llm-engine-think-hint"]) if ($(id)) $(id).hidden = !isOllama;
    if (this.endpointEl) this.endpointEl.placeholder = isOllama ? "e.g. http://localhost:11434" : "e.g. http://localhost:1234/v1";
  }

  // The models loaded at whatever endpoint is typed (saved or not), for the
  // Model field's suggestions -- a manual refresh too, so no "already asked".
  _fetchModels() {
    const endpoint = this.endpointEl?.value.trim() || "";
    if (!endpoint) return;
    this._pendingModelsFor = endpoint;
    this._app.send({
      type: "get_llm_models",
      endpoint,
      api_key: $("llm-engine-api-key")?.value.trim() || "",
      provider: this.providerEl?.value || "openai",
    });
  }

  _showModelOptions(models) {
    if (!this.modelOptionsEl) return;
    const option = (value, text) => Object.assign(document.createElement("option"), { value, textContent: text });
    this.modelOptionsEl.replaceChildren(option("", `Pick one of ${models.length} available models`), ...models.map((m) => option(m, m)));
    this.modelOptionsEl.hidden = !models.length;
  }
}

// ============================================================================
// Harnesses
// ============================================================================

export class HarnessUI {
  // onLockChange(locked): a harness connected or disconnected -- everything it
  // bypasses (her profile, soul, engines, avatar...) locks or unlocks.
  constructor(app, { onLockChange }) {
    this._app = app;
    this._onLockChange = onLockChange;
    this.toggleEl = $("harness-toggle");
    this.lightEl = $("harness-light");
    this.lockableEl = $("harness-lockable");
    this.confirmModal = new Modal("harness-confirm-modal-backdrop", { onClose: () => (this._pendingConnect = null) });
    this.active = false;
    this._activeName = "";
    // What the dropdown shows while not connected (harness_state's `selected`),
    // so turning a harness off doesn't forget which one was picked.
    this._selectedName = "";
    this._available = [];
    this._reachable = {}; // name -> harness_health's `reachable`
    this._pendingConnect = null; // which harness the confirm dialog is for
    this.harnesses = new SavedItems(app, {
      label: "harness",
      title: "Harness",
      reserved: NONE,
      ids: {
        select: "harness-select", edit: "edit-harness-button", add: "new-harness-button", delete: "delete-harness-button",
        backdrop: "harness-modal-backdrop", title: "harness-modal-title", name: "harness-name",
        save: "harness-modal-save-button", cancel: "harness-modal-cancel-button",
      },
      fields: { endpoint: "harness-endpoint", model: "harness-model", api_key: "harness-api-key" },
      messages: { get: "get_harness", save: "save_harness", delete: "delete_harness" },
      // Picking one only picks it (Brain remembers the pick); the toggle connects.
      onSelect: (name) => {
        this.harnesses.updateEditControls();
        if (this.toggleEl) this.toggleEl.disabled = name === NONE;
        this._renderLight();
        app.send({ type: "select_harness", name });
      },
    });
    this._renderDropdown();

    this.toggleEl?.addEventListener("change", () => {
      const turningOn = this.toggleEl.checked;
      // Only Brain's harness_state moves the switch -- connecting can be refused.
      this.toggleEl.checked = this.active;
      if (!turningOn) return app.send({ type: "set_harness_active", active: false, name: "" });
      const name = this.harnesses.selectEl?.value;
      if (name && name !== NONE) this._openConfirm(name);
    });
    $("harness-confirm-cancel-button")?.addEventListener("click", () => this.confirmModal.close());
    $("harness-confirm-connect-button")?.addEventListener("click", () => {
      const name = this._pendingConnect;
      this.confirmModal.close();
      if (name) app.send({ type: "set_harness_active", active: true, name });
    });
  }

  get handlers() {
    return {
      harness_state: (data) => {
        this.active = !!data.active;
        this._activeName = data.name || "";
        this._selectedName = data.selected || "";
        this._available = data.available || [];
        this._renderState();
      },
      harness_content: (data) => this.harnesses.handleContent(data),
      // Stored as-is: null (not configured) must stay apart from false (down).
      harness_health: (data) => {
        this._reachable[data.name] = data.reachable;
        this._renderLight();
      },
    };
  }

  _openConfirm(name) {
    this._pendingConnect = name;
    const textEl = $("harness-confirm-modal-text");
    if (textEl) {
      textEl.textContent =
        `Connect Glitch to the "${name}" harness? She'll use it as her brain instead of her ` +
        "profile/soul/LLM settings below until you disconnect.";
    }
    this.confirmModal.open();
  }

  _renderDropdown() {
    renderDropdown(this.harnesses.selectEl, [NONE, ...this._available], this._activeName || this._selectedName || NONE);
  }

  // While connected: the dropdown is locked (disconnect first), and so is
  // every control in #harness-lockable -- a real .disabled on each, which the
  // sections re-derive their own edit buttons from once it lifts.
  _renderState() {
    this._renderDropdown();
    const selectEl = this.harnesses.selectEl;
    if (this.toggleEl) {
      this.toggleEl.checked = this.active;
      this.toggleEl.disabled = this.active ? false : selectEl?.value === NONE;
    }
    if (selectEl) selectEl.disabled = this.active;
    this.harnesses.updateEditControls();
    this.lockableEl?.classList.toggle("harness-locked", this.active);
    for (const el of this.lockableEl?.querySelectorAll("select, button, input") || []) el.disabled = this.active;
    this._onLockChange(this.active);
    this._renderLight();
  }

  // Green while connected; otherwise the picked one's last health check.
  _renderLight() {
    if (this.active) return paintLight(this.lightEl, "green");
    const name = this._activeName || this.harnesses.selectEl?.value;
    paintLight(this.lightEl, reachabilityColor(name ? this._reachable[name] : undefined));
  }
}
