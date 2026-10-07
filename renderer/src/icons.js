// The Neon UI style draws its own line icons instead of emoji (ui-neon.css). Every button
// whose whole label is one of these emoji gets a data-icon naming its icon, and these emoji
// in text the app writes ("🧠 Learned: ...", "📎 photo.png") are wrapped in a
// <span class="text-icon" data-icon> -- still the emoji, so Classic and copied text are
// unchanged. Covers what's added later too (chat history, lessons, toasts).

const ICONS = {
  "🌸": "chat",
  "⚙": "settings",
  "🗑": "trash",
  "✏": "pencil",
  "➕": "plus",
  "↻": "retry",
  "👍": "up",
  "👎": "down",
  "📷": "camera",
  "🖥": "screen",
  "🎤": "mic",
  "📎": "clip",
  "📦": "archive",
};
// In text, only the app's own markers -- not, say, a 🌸 she puts in a reply.
const TEXT_ICONS = {
  "🧠": "memory",
  "📘": "book",
  "📝": "note",
  "👀": "eye",
  "⚠": "warning",
  "🖥": "screen",
  "📎": "clip",
  "📷": "camera",
  "🎤": "mic",
  "👍": "up",
  "👎": "down",
  "➕": "plus",
};
const BUTTONS = "button, .mic-icon";
const TEXT_EMOJI = new RegExp(`((?:${Object.keys(TEXT_ICONS).join("|")})\\uFE0F?)`, "u");
const NOT_TEXT = "button, .mic-icon, .text-icon, script, style, textarea, title, option";

const bare = (emoji) => emoji.replace(/️/g, "").trim();

function tag(el) {
  const icon = el.matches?.(BUTTONS) && ICONS[bare(el.textContent)];
  if (icon) el.dataset.icon = icon;
}

function wrapText(node) {
  if (!TEXT_EMOJI.test(node.data) || node.parentElement?.closest(NOT_TEXT)) return;
  // Split around each emoji: the capture group keeps them, at the odd indexes.
  const parts = node.data.split(new RegExp(TEXT_EMOJI.source, "gu")).map((part, i) => {
    if (i % 2 === 0) return part;
    const span = Object.assign(document.createElement("span"), { className: "text-icon", textContent: part });
    span.dataset.icon = TEXT_ICONS[bare(part)];
    return span;
  });
  node.replaceWith(...parts.filter((part) => part !== ""));
}

function iconize(root) {
  if (root.nodeType === Node.TEXT_NODE) return wrapText(root);
  if (root.nodeType !== Node.ELEMENT_NODE) return;
  tag(root);
  root.querySelectorAll(BUTTONS).forEach(tag);
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const texts = [];
  while (walker.nextNode()) texts.push(walker.currentNode);
  texts.forEach(wrapText);
}

iconize(document.body);
new MutationObserver((changes) => {
  for (const change of changes) change.addedNodes.forEach(iconize);
}).observe(document.body, { childList: true, subtree: true });
