// The Neon UI style draws its own line icons instead of emoji buttons (ui-neon.css). Every
// button whose whole label is one of these emoji gets a data-icon naming its icon --
// buttons added later too (chat history, lessons). Classic ignores data-icon.

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
const SELECTOR = "button, .mic-icon";

function tag(el) {
  const icon = el.matches?.(SELECTOR) && ICONS[el.textContent.replace(/️/g, "").trim()];
  if (icon) el.dataset.icon = icon;
}

function tagAll(root) {
  tag(root);
  root.querySelectorAll?.(SELECTOR).forEach(tag);
}

tagAll(document.body);
new MutationObserver((changes) => {
  for (const change of changes) change.addedNodes.forEach(tagAll);
}).observe(document.body, { childList: true, subtree: true });
