// WebSocket client connecting OUT to the Brain's server (protocol.md; the
// Renderer is always the client -- browser/WebView JS can't accept
// incoming connections). Message types not yet implemented Renderer-side
// (play_animation, viseme_stream) are logged, not silently dropped, so a
// gap is visible rather than looking like a working no-op.

const RECONNECT_DELAY_MS = 3000;
const SUBTITLE_DISPLAY_MS = 6000;

export class BrainClient {
  constructor({ url, vrm, statusEl, subtitleEl, inputEl, sendButtonEl }) {
    this.url = url;
    this.vrm = vrm;
    this.statusEl = statusEl;
    this.subtitleEl = subtitleEl;
    this.inputEl = inputEl;
    this.sendButtonEl = sendButtonEl;
    this.socket = null;
    this._subtitleTimer = null;

    this.sendButtonEl?.addEventListener("click", () => this._sendCurrentInput());
    this.inputEl?.addEventListener("keydown", (e) => {
      if (e.key === "Enter") this._sendCurrentInput();
    });
  }

  connect() {
    this.socket = new WebSocket(this.url);

    this.socket.addEventListener("open", () => {
      console.log("[brain] connected");
      this._setStatus("");
      this._send({ type: "ready", model: "Glitch.vrm" });
    });

    this.socket.addEventListener("close", () => {
      console.warn("[brain] disconnected, retrying...");
      this._setStatus("Brain: disconnected, retrying...");
      setTimeout(() => this.connect(), RECONNECT_DELAY_MS);
    });

    this.socket.addEventListener("error", (err) => {
      console.error("[brain] socket error:", err);
    });

    this.socket.addEventListener("message", (event) => this._handleMessage(event.data));
  }

  _sendCurrentInput() {
    const text = this.inputEl?.value.trim();
    if (!text) return;
    this._send({ type: "user_text", text });
    this.inputEl.value = "";
  }

  _send(message) {
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify(message));
    }
  }

  _setStatus(text) {
    if (this.statusEl) this.statusEl.textContent = text;
  }

  _showSubtitle(text) {
    if (!this.subtitleEl) return;
    this.subtitleEl.textContent = text;
    this.subtitleEl.classList.add("visible");
    clearTimeout(this._subtitleTimer);
    this._subtitleTimer = setTimeout(() => this.subtitleEl.classList.remove("visible"), SUBTITLE_DISPLAY_MS);
  }

  _handleMessage(raw) {
    let data;
    try {
      data = JSON.parse(raw);
    } catch {
      console.warn("[brain] ignoring non-JSON message:", raw);
      return;
    }

    switch (data.type) {
      case "ping":
        this._send({ type: "pong" });
        break;
      case "set_expression":
        this.vrm.expressionManager?.setValue(data.name, data.weight);
        break;
      case "speak_text":
        // Text-only stand-in for now -- no audio/lipsync until TTS lands
        // (viseme_stream), see protocol.md's note on this message type.
        this._showSubtitle(data.text);
        break;
      case "play_animation":
        console.warn("[brain] play_animation not yet implemented:", data);
        this._send({ type: "error", message: `play_animation not yet implemented: ${data.name}` });
        break;
      case "viseme_stream":
        console.warn("[brain] viseme_stream not yet implemented:", data);
        this._send({ type: "error", message: "viseme_stream not yet implemented" });
        break;
      default:
        console.warn("[brain] ignoring unknown message type:", data.type);
    }
  }
}
