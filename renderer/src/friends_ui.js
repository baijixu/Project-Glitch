// Settings -> Friends (brain/friends.py): her friends -- each a soul and the LLM engine they
// speak through, edited like souls (SavedItems) -- and Start a visit with its progress. The
// Friend mode toggle itself is one of settings_ui.js's switches.

import { $, renderDropdown } from "./ui.js";
import { SavedItems } from "./saved_items.js";

const NO_FRIEND = "Friends";
const HER_ENGINE = ""; // a friend with no engine of their own speaks through hers

export class FriendsUI {
  constructor(app) {
    this._app = app;
    this._engineNames = [];
    this._most = 3;
    this.items = new SavedItems(app, {
      label: "friend",
      title: "Friend",
      reserved: NO_FRIEND,
      ids: {
        select: "friend-select", edit: "edit-friend-button", add: "new-friend-button", delete: "delete-friend-button",
        backdrop: "friend-modal-backdrop", title: "friend-modal-title", name: "friend-name",
        save: "friend-save-button", cancel: "friend-cancel-button",
      },
      fields: { soul: "friend-soul", engine: "friend-engine" },
      messages: { get: "get_friend", save: "save_friend", delete: "delete_friend" },
      defaults: { engine: HER_ENGINE },
      canSave: (name, v) => name && v.soul,
      onSelect: () => this.items.updateEditControls(), // picking one only chooses what ✏️/🗑️ act on
      onOpen: (values) => this._fillEngines(values.engine),
    });
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

  setEngineNames(names) {
    this._engineNames = names;
  }

  setLocked(locked) {
    this.items.setLocked(locked);
  }

  _fillEngines(selected) {
    const select = $("friend-engine");
    if (!select) return;
    renderDropdown(select, [HER_ENGINE, ...this._engineNames], selected || HER_ENGINE);
    const her = select.querySelector('option[value=""]');
    if (her) her.textContent = "Her own LLM engine";
  }
}
