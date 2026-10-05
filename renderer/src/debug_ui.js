// Settings -> Debugging: a log of connection events, timings, settings changes
// and errors -- never conversation content -- that can be downloaded, plus
// Brain's own account of its calls (debug_event) while it's on. And Restart Brain.

import { $, downloadText } from "./ui.js";

const PING_INTERVAL_MS = 5000; // debug_ping, for the round-trip time -- only while on
const MAX_ENTRIES = 5000; // oldest dropped past this
// Un-greys Settings if a restarted Brain never comes back -- the reconnect loop
// would otherwise leave it greyed out with no explanation.
const RESTART_TIMEOUT_MS = 30000;

export class DebugUI {
  // app: send, connectionState.
  constructor(app) {
    this._app = app;
    // On by default while the app is being shaken out -- not remembered across
    // a reload either way.
    this.active = true;
    this._entries = []; // {ts, category, message, ms?} -- kept when turned off, until a reload
    this._pingTimer = null;
    this._restartTimer = null;
    this._setPinging(this.active);
    $("debug-toggle")?.addEventListener("change", (e) => this._setActive(e.target.checked));
    $("download-debug-log-button")?.addEventListener("click", () => this._download());
    $("restart-brain-button")?.addEventListener("click", () => this._restartBrain());
  }

  get handlers() {
    return {
      // The real Renderer<->Brain round trip (unlike ping/pong, which is Brain's keepalive).
      debug_pong: (data) => this.log("ws", "round-trip time", Date.now() - data.ts),
      debug_event: (data) => this.log(data.category, data.message, data.ms),
    };
  }

  // A no-op while off, so callers never need to check. `message` must never be
  // conversation content -- only states, types, sizes, timings and error text.
  log(category, message, ms) {
    if (!this.active) return;
    this._entries.push({ ts: Date.now(), category, message, ms });
    if (this._entries.length > MAX_ENTRIES) this._entries.shift();
  }

  // A fresh connection: Brain doesn't remember this device was debugging, and a
  // restart has finished.
  handleConnected() {
    this.setRestarting(false);
    if (this.active) this._app.send({ type: "set_debug_active", active: true });
  }

  setRestarting(restarting) {
    $("settings-content")?.classList.toggle("restarting", restarting);
    clearTimeout(this._restartTimer);
    this._restartTimer = restarting ? setTimeout(() => this.setRestarting(false), RESTART_TIMEOUT_MS) : null;
  }

  // Turning it off stops Brain's events and the pings but keeps what was
  // captured -- turn it off right after reproducing a problem, then download.
  _setActive(active) {
    this.active = active;
    this._app.send({ type: "set_debug_active", active });
    this.log("client", active ? "debugging enabled" : "debugging disabled");
    this._setPinging(active);
  }

  _setPinging(on) {
    clearInterval(this._pingTimer);
    this._pingTimer = on ? setInterval(() => this._app.send({ type: "debug_ping", ts: Date.now() }), PING_INTERVAL_MS) : null;
  }

  _download() {
    // Times are UTC; the local time and zone are in the header to match against
    // her chat log (brain/chat_logs/, local time).
    const now = new Date();
    const offsetMin = -now.getTimezoneOffset();
    const pad = (n) => String(n).padStart(2, "0");
    const offset = `UTC${offsetMin >= 0 ? "+" : "-"}${pad(Math.floor(Math.abs(offsetMin) / 60))}:${pad(Math.abs(offsetMin) % 60)}`;
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone || "unknown zone";
    const count = this._entries.length;
    const header = [
      `Glitch debug log -- exported ${now.toISOString()} (local ${now.toLocaleString()}, ${zone}, ${offset})`,
      `Device: ${deviceSummary()}`,
      `Hardware: ${deviceHardware()}`,
      `Window: ${window.innerWidth}x${window.innerHeight}, touch ${navigator.maxTouchPoints > 0 ? "yes" : "no"}, opened at ${location.protocol}//${location.host}`,
      `Browser details: ${navigator.userAgent}`,
      `Connection state at export: ${this._app.connectionState}`,
      `${count} entr${count === 1 ? "y" : "ies"} (times are UTC; [setup] lines describe Brain's settings when debugging was turned on)`,
      "",
    ];
    const lines = this._entries.map(
      (e) => `[${new Date(e.ts).toISOString()}] [${e.category}] ${e.message}${e.ms != null ? ` (${e.ms.toFixed(1)}ms)` : ""}`,
    );
    downloadText(`glitch-debug-log-${Date.now()}.txt`, header.concat(lines).join("\n") + "\n");
  }

  // Greys out Settings until Brain is back -- everything in it comes from the
  // Brain that's about to restart.
  _restartBrain() {
    if (!window.confirm("Restart Glitch's Brain? It takes a few seconds and she'll reconnect automatically -- her conversation is saved and picked back up.")) {
      return;
    }
    this.log("brain", "restart requested");
    this.setRestarting(true);
    this._app.send({ type: "restart_brain" });
  }
}

// What a Settings change this device sent looks like in the log: its kind and
// name or on/off, never content (chat, notes, soul or profile text, reasons,
// API keys). null for anything that isn't a settings change.
const LOGGED_SETTING_PREFIXES = ["set_", "load_", "save_", "delete_", "rename_", "clear_", "resolve_"];

export function describeSettingChange(message) {
  const type = message?.type || "";
  if (type === "set_debug_active" || type === "save_notes") return null;
  if (type === "rate_reply") return `rate_reply: ${message.rating} (reason ${(message.note || "").length} chars)`;
  if (type === "restart_brain") return "restart_brain";
  if (!LOGGED_SETTING_PREFIXES.some((prefix) => type.startsWith(prefix))) return null;
  const parts = [];
  if (typeof message.active === "boolean") parts.push(message.active ? "on" : "off");
  if (typeof message.think === "boolean") parts.push(`think ${message.think ? "on" : "off"}`);
  for (const key of ["name", "new_name", "provider", "level", "value"]) {
    if (typeof message[key] === "string" && message[key]) parts.push(`${key} ${JSON.stringify(message[key])}`);
  }
  if (typeof message.approve === "boolean") parts.push(message.approve ? "approved" : "rejected");
  return parts.length ? `${type}: ${parts.join(", ")}` : type;
}

// "Android phone · Chrome 140", "Windows PC · Chrome 152 · app window" -- a best
// guess from the browser's identification string, for the log and for Brain's
// list of connected devices.
export function deviceSummary() {
  const ua = navigator.userAgent || "";
  let system = "unknown system";
  if (/iPhone/.test(ua)) system = "iPhone";
  else if (/iPad/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1)) system = "iPad";
  else if (/Android/.test(ua)) system = /Mobile/.test(ua) ? "Android phone" : "Android tablet";
  else if (/Windows/.test(ua)) system = "Windows PC";
  else if (/Macintosh|Mac OS X/.test(ua)) system = "Mac";
  else if (/CrOS/.test(ua)) system = "Chromebook";
  else if (/Linux/.test(ua)) system = "Linux PC";
  const browsers = [
    ["Edge", /Edg\/(\d+)/],
    ["Opera", /OPR\/(\d+)/],
    ["Samsung Internet", /SamsungBrowser\/(\d+)/],
    ["Firefox", /(?:Firefox|FxiOS)\/(\d+)/],
    ["Chrome", /(?:Chrome|CriOS)\/(\d+)/],
    ["Safari", /Version\/(\d+).*Safari/],
  ];
  const match = browsers.map(([name, re]) => [name, ua.match(re)]).find(([, m]) => m);
  const browser = match ? `${match[0]} ${match[1][1]}` : "unknown browser";
  return `${system} · ${browser}${window.pywebview ? " · app window" : ""}`;
}

// The hardware details a browser will tell.
function deviceHardware() {
  const parts = [`screen ${screen.width}x${screen.height} at ${window.devicePixelRatio || 1}x`];
  if (navigator.hardwareConcurrency) parts.push(`${navigator.hardwareConcurrency} CPU threads`);
  if (navigator.deviceMemory) parts.push(`~${navigator.deviceMemory} GB memory`);
  if (navigator.connection?.effectiveType) parts.push(`network ${navigator.connection.effectiveType}`);
  parts.push(`language ${navigator.language}`);
  return parts.join(", ");
}
