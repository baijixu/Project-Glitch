// Settings -> Friends (brain/friends.py): her friends -- each a soul, and the server and model
// they speak through (picked from what that server has loaded), edited like souls (SavedItems)
// -- and Start a visit with its progress. The Friend mode toggle is one of settings_ui.js's switches.

import { $, renderDropdown } from "./ui.js";
import { SavedItems } from "./saved_items.js";

const NO_FRIEND = "Friends";

export class FriendsUI {
  constructor(app) {
    this._app = app;
    this._most = 3;
    this._modelsFor = ""; // the server the model list was last asked for
    this.items = new SavedItems(app, {
      label: "friend",
      title: "Friend",
      reserved: NO_FRIEND,
      ids: {
        select: "friend-select", edit: "edit-friend-button", add: "new-friend-button", delete: "delete-friend-button",
        backdrop: "friend-modal-backdrop", title: "friend-modal-title", name: "friend-name",
        save: "friend-save-button", cancel: "friend-cancel-button",
      },
      fields: { soul: "friend-soul", endpoint: "friend-endpoint", model: "friend-model" },
      messages: { get: "get_friend", save: "save_friend", delete: "delete_friend" },
      canSave: (name, v) => name && v.soul,
      onSelect: () => this.items.updateEditControls(), // picking one only chooses what ✏️/🗑️ act on
      onOpen: (values) => this._askModels(values.model),
    });
    $("friend-endpoint")?.addEventListener("change", () => this._askModels($("friend-model")?.value || ""));
    this.items.render([], NO_FRIEND);
    $("start-visit-button")?.addEventListener("click", () => {
      $("start-visit-button").disabled = true; // back on with the visit_state Brain answers with
      this._app.send({ type: "start_visit" });
    });
  }

  get handlers() {
    return {
      friends: (data) => {
        this._most = data.most || this._most;
        this.items.render(data.names || [], NO_FRIEND);
        const add = $("new-friend-button");
        if (add) add.disabled = (data.names || []).length >= this._most;
        const error = $("friends-error");
        if (error) {
          error.textContent = data.error || "";
          error.hidden = !data.error;
        }
      },
      friend_content: (data) => this.items.handleContent(data),
      visit_state: (data) => {
        const button = $("start-visit-button");
        if (button) button.disabled = !!data.running;
        const status = $("visit-status");
        if (status) status.textContent = data.running ? `They're over: ${data.count} of ${data.total} messages...` : data.note || "";
      },
    };
  }

  // llm_models (brain_client routes it here and to the LLM engine editor): the models on the
  // server this editor asked about, keeping the friend's saved one selected.
  handleModels(data) {
    if (data.endpoint !== this._modelsFor) return;
    const select = $("friend-model");
    const keep = select?.value || "";
    this._fillModels(keep && !(data.models || []).includes(keep) ? [keep, ...(data.models || [])] : data.models || [], keep);
  }

  setLocked(locked) {
    this.items.setLocked(locked);
  }

  // No server: she speaks through her own LLM, so there's nothing to pick. Otherwise Brain is asked
  // what that server has loaded; the saved model shows until the answer comes.
  _askModels(model) {
    const endpoint = $("friend-endpoint")?.value.trim() || "";
    this._modelsFor = endpoint;
    if (endpoint) this._app.send({ type: "get_llm_models", endpoint });
    this._fillModels(model ? [model] : [], model);
  }

  _fillModels(models, selected) {
    const select = $("friend-model");
    if (!select) return;
    const endpoint = $("friend-endpoint")?.value.trim() || "";
    renderDropdown(select, endpoint ? ["", ...models] : [""], endpoint ? selected : "");
    const first = select.querySelector('option[value=""]');
    if (first) first.textContent = endpoint ? "The server's loaded model" : "Her own model";
    select.disabled = !endpoint;
  }
}
