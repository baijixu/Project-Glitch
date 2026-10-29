// Settings -> Gallery (brain/pictures.py on the Brain side): the pictures she's made and the ones he's
// given her, newest first, each with her description -- to add to, look at full size or delete for good.
// Rebuilt from each `artwork` message, so every device shows the same set; the pictures themselves load
// only once the fold is opened.

import { downscaleImage, readAsDataUrl } from "./capture.js";
import { $ } from "./ui.js";

export class ArtworkUI {
  constructor(app) {
    this._app = app;
    this.listEl = $("artwork-list");
    this.countEl = $("artwork-count");
    this.detailsEl = $("artwork-details");
    this._names = [];
    this._about = new Map();
    this.detailsEl?.addEventListener("toggle", () => this.detailsEl.open && this._render());
    this.inputEl = $("picture-file-input");
    $("add-picture-button")?.addEventListener("click", () => this.inputEl?.click());
    this.inputEl?.addEventListener("change", () => this._add([...(this.inputEl.files || [])]));
  }

  // Scaled down like a snapshot before it's sent: she sees no more than 1024 px, and a full-size
  // render would cost her model's GPU memory every time she looks at it.
  async _add(files) {
    this.inputEl.value = ""; // otherwise re-picking the same file wouldn't fire "change" again
    for (const file of files) {
      try {
        const scaled = await downscaleImage(await readAsDataUrl(file));
        this._app.send({ type: "add_picture", name: file.name, mime: "image/jpeg", data_b64: scaled.split(",", 2)[1] });
      } catch {
        window.alert(`${file.name} isn't a picture she can open.`);
      }
    }
  }

  get handlers() {
    return {
      artwork: (data) => {
        this._names = data.names || [];
        this._about = new Map((data.pictures || []).map((p) => [p.name, p]));
        this._app.keepOnlyPictures(this._names);
        if (this.countEl) this.countEl.textContent = this._names.length ? ` (${this._names.length})` : "";
        if (this.detailsEl?.open) this._render();
      },
    };
  }

  _render() {
    if (!this.listEl) return;
    this.listEl.replaceChildren();
    if (!this._names.length) {
      this.listEl.appendChild(Object.assign(document.createElement("p"), { className: "settings-hint", textContent: "No pictures yet." }));
      return;
    }
    for (const name of this._names) {
      const tile = document.createElement("div");
      tile.className = "artwork-tile";
      const picture = this._about.get(name) || {};
      const img = Object.assign(document.createElement("img"), { alt: picture.about || "", title: picture.about || "" });
      img.addEventListener("click", () => tile.classList.toggle("expanded"));
      const about = Object.assign(document.createElement("p"), {
        className: "artwork-about",
        textContent: `${picture.by === "him" ? "From you" : "She made it"}${picture.about ? ` -- ${picture.about}` : picture.by === "him" ? " -- she hasn't described it yet" : ""}`,
      });
      // His label: who or what it is, so she doesn't take every picture for herself. Changing it has her
      // describe the picture again, going by it.
      const label = Object.assign(document.createElement("input"), {
        className: "settings-text-input artwork-label",
        value: picture.label || "",
        placeholder: "Who is it?",
        maxLength: 80,
        title: "Who or what this is, in a few words -- she goes by it",
      });
      label.addEventListener("change", () => this._app.send({ type: "label_picture", name, label: label.value.trim() }));
      this._app.loadPicture(name).then((url) => (url ? (img.src = url) : tile.remove()));
      const meta = document.createElement("div");
      meta.className = "artwork-tile-meta";
      meta.appendChild(Object.assign(document.createElement("span"), { textContent: when(name) }));
      const remove = Object.assign(document.createElement("button"), {
        className: "icon-button lesson-button danger",
        textContent: "🗑️",
        title: "Delete this picture",
      });
      remove.addEventListener("click", () => {
        if (window.confirm("Delete this picture for good? It goes from her chat too.")) {
          this._app.send({ type: "delete_picture", name });
        }
      });
      if (picture.by !== "him") meta.appendChild(this._askButton(name));
      meta.appendChild(remove);
      tile.append(img, label, about, meta);
      this.listEl.appendChild(tile);
    }
  }

  // ❓ asks her about one of her pictures in chat, by the words of its name ("glassy sea stone spire"), which is
  // how look_at_picture finds it -- so she opens and sees that one, with what she thought as she drew it.
  _askButton(name) {
    const ask = Object.assign(document.createElement("button"), {
      className: "icon-button lesson-button",
      textContent: "❓",
      title: "Ask her about this picture",
    });
    ask.addEventListener("click", () => {
      if (!this._app.canSendReply()) return;
      const words = name.replace(/^\d{8}-\d{6}-/, "").replace(/\.\w+$/, "").replaceAll("-", " ");
      const text = `Tell me about your "${words}" picture -- what were you thinking when you made it?`;
      this._app.sendForReply({ type: "user_text", text }, `sent user_text (${text.length} chars, gallery ❓)`);
      this._app.history.add("user", text);
      $("settings-panel")?.classList.remove("open"); // her answer is in the chat, not here
    });
    return ask;
  }
}

// "20261014-221906-cabin.png" -> "Oct 14, 10:19 PM" (pictures.py names them by when they were kept).
function when(name) {
  const m = /^(\d{4})(\d\d)(\d\d)-(\d\d)(\d\d)/.exec(name);
  if (!m) return "";
  const date = new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]);
  return date.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}
