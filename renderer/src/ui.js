// Small helpers every Settings/chat module shares: element lookup, dropdowns,
// modals, per-browser preferences, file downloads and base64.

export const $ = (id) => document.getElementById(id);

// Rebuilds a <select>'s options from `names` and selects `activeName`.
export function renderDropdown(selectEl, names, activeName) {
  if (!selectEl) return;
  selectEl.replaceChildren();
  for (const name of names) {
    const option = document.createElement("option");
    option.value = name;
    option.textContent = name;
    selectEl.appendChild(option);
  }
  if (activeName) selectEl.value = activeName;
}

// A modal dialog: its backdrop element, shown/hidden with `hidden`. Clicking
// the backdrop itself (not the dialog inside it) or pressing Escape closes it.
// `onClose` runs on every close, for whatever the dialog was holding on to.
const openModals = new Set();
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  for (const modal of [...openModals]) modal.close();
});

export class Modal {
  constructor(backdropId, { onClose } = {}) {
    this.el = $(backdropId);
    this._onClose = onClose;
    this.el?.addEventListener("click", (e) => {
      if (e.target === this.el) this.close();
    });
  }

  get isOpen() {
    return !!this.el && !this.el.hidden;
  }

  open() {
    if (!this.el) return;
    this.el.hidden = false;
    openModals.add(this);
  }

  close() {
    openModals.delete(this);
    this._onClose?.();
    if (this.el) this.el.hidden = true;
  }
}

// Per-browser preferences (localStorage). A private window or blocked site
// data can throw on either read or write; losing a preference is harmless, so
// a failure just means the default.
export function readPref(key, fallback) {
  try {
    return localStorage.getItem(key) ?? fallback;
  } catch {
    return fallback;
  }
}

export function writePref(key, value) {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    // Nothing to fall back to -- it just won't survive a reload.
  }
}

export function downloadText(filename, text) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url); // click() is synchronous, so the URL is no longer needed
}

export function arrayBufferToBase64(buffer) {
  // Chunked rather than one String.fromCharCode call per byte -- a
  // multi-megabyte VRM import (avatars can be 15MB+) made the naive per-byte
  // version a noticeable UI freeze. 0x8000 stays well under
  // String.fromCharCode.apply's argument-count limit.
  const bytes = new Uint8Array(buffer);
  const CHUNK_SIZE = 0x8000;
  let binary = "";
  for (let i = 0; i < bytes.length; i += CHUNK_SIZE) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK_SIZE));
  }
  return btoa(binary);
}

export function base64ToArrayBuffer(base64) {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes.buffer;
}
