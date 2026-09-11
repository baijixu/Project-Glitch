// WebSocket client connecting OUT to the Brain's server (protocol.md; the
// Renderer is always the client -- browser/WebView JS can't accept
// incoming connections). Message types not yet implemented Renderer-side
// (play_animation) are logged, not silently dropped, so a gap is visible
// rather than looking like a working no-op.

const RECONNECT_DELAY_MS = 3000;
// Fades out 10s after the text finishes streaming in, not 10s from when it
// starts -- otherwise a long reply would only stay fully visible for a few
// seconds after the last word appears, which reads as rushed.
const SUBTITLE_FADE_DELAY_MS = 10000;

// This VRM's actual mood expression presets (confirmed via
// vrm.expressionManager.expressionMap) -- "neutral" isn't in this list on
// purpose, it means "fade all of these to 0" rather than being a settable
// expression of its own. Treated as mutually exclusive: setting one fades
// the rest out, matching how a face can only show one mood at a time.
const MOODS = ["happy", "sad", "surprised", "angry", "relaxed"];
const EXPRESSION_FADE_SEC = 0.3;

export class BrainClient {
  constructor({
    url,
    vrm,
    statusEl,
    subtitleEl,
    inputEl,
    sendButtonEl,
    micButtonEl,
    micLabelEl,
    historyButtonEl,
    historyPanelEl,
    historyListEl,
  }) {
    this.url = url;
    this.vrm = vrm;
    this.statusEl = statusEl;
    this.subtitleEl = subtitleEl;
    this.inputEl = inputEl;
    this.sendButtonEl = sendButtonEl;
    this.micButtonEl = micButtonEl;
    this.micLabelEl = micLabelEl;
    this.historyButtonEl = historyButtonEl;
    this.historyPanelEl = historyPanelEl;
    this.historyListEl = historyListEl;
    this.socket = null;
    this._subtitleTimer = null;
    this._subtitleStreamTimer = null;
    this._pendingSpeakText = "";

    this.audioContext = new (window.AudioContext || window.webkitAudioContext)();
    this.visemeFrames = [];
    this.playbackStartTime = 0;
    this.lipSyncActive = false;

    this.moodWeights = Object.fromEntries(MOODS.map((m) => [m, 0]));
    this.moodTargets = Object.fromEntries(MOODS.map((m) => [m, 0]));

    this.mediaRecorder = null;
    this.recordedChunks = [];

    this.sendButtonEl?.addEventListener("click", () => this._sendCurrentInput());
    this.inputEl?.addEventListener("keydown", (e) => {
      if (e.key === "Enter") this._sendCurrentInput();
    });

    // Push-to-talk: hold the button, speak, release. Pointer events (not
    // just mouse) so it works for touch too; pointerleave/pointercancel so
    // dragging off the button while held doesn't leave it stuck recording.
    const startRecording = (e) => {
      e.preventDefault();
      this._startRecording();
    };
    const stopRecording = () => this._stopRecording();
    this.micButtonEl?.addEventListener("pointerdown", startRecording);
    this.micButtonEl?.addEventListener("pointerup", stopRecording);
    this.micButtonEl?.addEventListener("pointerleave", stopRecording);
    this.micButtonEl?.addEventListener("pointercancel", stopRecording);

    this.historyButtonEl?.addEventListener("click", () => {
      this.historyPanelEl?.classList.toggle("open");
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

  // Called once per render frame from main.js's animate() loop -- drives
  // lipsync by indexing the current viseme_stream by elapsed playback time,
  // same approach as timing any other audio-synced animation off
  // audioContext.currentTime rather than wall-clock time. Also crossfades
  // mood expression weights toward their targets (set by _setMood) instead
  // of snapping instantly, so a mood change reads as an actual expression
  // change rather than a face slamming into place.
  update(delta) {
    if (this.lipSyncActive) {
      const elapsed = this.audioContext.currentTime - this.playbackStartTime;
      const frame = this.visemeFrames.find((f, i) => {
        const next = this.visemeFrames[i + 1];
        return elapsed >= f.t && (!next || elapsed < next.t);
      });
      this.vrm.expressionManager?.setValue("aa", frame ? frame.weight : 0);
    }

    for (const name of MOODS) {
      const target = this.moodTargets[name];
      let current = this.moodWeights[name];
      if (current === target) continue;
      const step = delta / EXPRESSION_FADE_SEC;
      current = target > current ? Math.min(current + step, target) : Math.max(current - step, target);
      this.moodWeights[name] = current;
      this.vrm.expressionManager?.setValue(name, current);
    }
  }

  // Mutually exclusive: setting one mood fades every other mood to 0.
  // "neutral" (or anything else outside MOODS) means "fade them all out".
  _setMood(name, weight) {
    for (const m of MOODS) {
      this.moodTargets[m] = m === name ? weight : 0;
    }
  }

  // Snaps straight back to neutral rather than just retargeting for
  // update() to crossfade toward -- if the tab loses focus/visibility
  // while she's talking, requestAnimationFrame gets throttled or paused by
  // the browser, so a retarget-only reset can leave the last mood's
  // expression visibly frozen (e.g. stuck "surprised") until the tab
  // regains focus, however long that takes.
  _resetMoodImmediate() {
    for (const name of MOODS) {
      this.moodTargets[name] = 0;
      this.moodWeights[name] = 0;
      this.vrm.expressionManager?.setValue(name, 0);
    }
  }

  _sendCurrentInput() {
    const text = this.inputEl?.value.trim();
    if (!text) return;
    this._send({ type: "user_text", text });
    this._addHistoryEntry("user", text);
    this.inputEl.value = "";
  }

  async _startRecording() {
    if (this.mediaRecorder && this.mediaRecorder.state === "recording") return;
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
      console.warn("Microphone access denied or unavailable:", err);
      this._setStatus("Microphone access denied");
      setTimeout(() => this._setStatus(""), 4000);
      return;
    }

    this.recordedChunks = [];
    this.mediaRecorder = new MediaRecorder(stream);
    this.mediaRecorder.addEventListener("dataavailable", (e) => {
      if (e.data.size > 0) this.recordedChunks.push(e.data);
    });
    this.mediaRecorder.addEventListener("stop", () => {
      stream.getTracks().forEach((track) => track.stop());
      this._sendRecording();
    });
    this.mediaRecorder.start();
    this.micButtonEl?.classList.add("recording");
    if (this.micLabelEl) this.micLabelEl.textContent = " Recording...";
  }

  _stopRecording() {
    if (!this.mediaRecorder || this.mediaRecorder.state !== "recording") return;
    this.mediaRecorder.stop();
    this.micButtonEl?.classList.remove("recording");
    if (this.micLabelEl) this.micLabelEl.textContent = " Hold to talk";
  }

  async _sendRecording() {
    if (this.recordedChunks.length === 0) return;
    const mimeType = this.mediaRecorder.mimeType || "audio/webm";
    const blob = new Blob(this.recordedChunks, { type: mimeType });
    const audioB64 = _arrayBufferToBase64(await blob.arrayBuffer());
    this._send({ type: "user_audio", audio_b64: audioB64, mime_type: mimeType });
    // The transcript itself only exists Brain-side (STT runs there) --
    // protocol.md has no message that sends it back, so this is a
    // placeholder rather than the actual words, just to keep the history
    // showing a turn happened here.
    this._addHistoryEntry("user", "🎤 (voice message)");
  }

  _send(message) {
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify(message));
    }
  }

  _setStatus(text) {
    if (this.statusEl) this.statusEl.textContent = text;
  }

  _addHistoryEntry(role, text) {
    if (!this.historyListEl) return;
    const group = document.createElement("div");
    group.className = `history-group history-${role}`;

    const bubble = document.createElement("div");
    bubble.className = "history-bubble";
    bubble.textContent = text;
    group.appendChild(bubble);

    const time = document.createElement("div");
    time.className = "history-time";
    time.textContent = new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    group.appendChild(time);

    this.historyListEl.appendChild(group);
    this.historyListEl.scrollTop = this.historyListEl.scrollHeight;
  }

  // Reveals text word by word, paced to the actual audio duration (decoded
  // before this is called, in _playAudio) rather than a guessed interval,
  // so it roughly tracks her speech. Each word is its own span with a
  // fade-in (CSS) instead of a flat textContent append, so words ease in
  // instead of popping in instantly.
  //
  // Paced faster than a literal duration/wordCount split (PACE_FACTOR < 1):
  // TTS audio has leading/trailing silence and per-word count doesn't match
  // spoken syllable count well (an emoji counts as a full "word" but takes
  // near-zero time to speak), so an even split over the *whole* clip
  // consistently landed behind actual speech -- confirmed by users noticing
  // the text trailing the voice, not just a guess.
  _startSubtitleStream(text, durationSec) {
    if (!this.subtitleEl) return;
    clearInterval(this._subtitleStreamTimer);
    clearTimeout(this._subtitleTimer);

    const words = text.split(/\s+/).filter(Boolean);
    this.subtitleEl.replaceChildren();
    this.subtitleEl.classList.add("visible");
    if (words.length === 0) return;

    const PACE_FACTOR = 0.75;
    const intervalMs = Math.max((durationSec * 1000 * PACE_FACTOR) / words.length, 30);
    let i = 0;
    const revealNext = () => {
      const span = document.createElement("span");
      span.className = "word";
      span.textContent = words[i];
      this.subtitleEl.appendChild(span);
      this.subtitleEl.appendChild(document.createTextNode(" "));
      this.subtitleEl.scrollTop = this.subtitleEl.scrollHeight; // pin to newest text once it wraps past max-height
      i++;
      if (i >= words.length) {
        clearInterval(this._subtitleStreamTimer);
        // The 10s fade-out countdown starts once the full reply is
        // actually visible, not from when streaming began.
        this._subtitleTimer = setTimeout(() => this.subtitleEl.classList.remove("visible"), SUBTITLE_FADE_DELAY_MS);
      }
    };
    revealNext();
    this._subtitleStreamTimer = setInterval(revealNext, intervalMs);
  }

  async _playAudio(audioB64) {
    const binary = atob(audioB64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);

    const audioBuffer = await this.audioContext.decodeAudioData(bytes.buffer.slice(0));
    const source = this.audioContext.createBufferSource();
    source.buffer = audioBuffer;
    source.connect(this.audioContext.destination);

    this.playbackStartTime = this.audioContext.currentTime;
    this.lipSyncActive = true;
    this._startSubtitleStream(this._pendingSpeakText, audioBuffer.duration);
    source.onended = () => {
      this.lipSyncActive = false;
      this.vrm.expressionManager?.setValue("aa", 0);
      this._resetMoodImmediate();
    };
    source.start();
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
        this._setMood(data.name, data.weight);
        break;
      case "speak_text":
        // Held until speak_audio arrives with a decoded duration to pace
        // the streaming reveal against -- see _startSubtitleStream.
        this._pendingSpeakText = data.text;
        this._addHistoryEntry("glitch", data.text);
        break;
      case "speak_audio":
        this._playAudio(data.audio_b64);
        break;
      case "viseme_stream":
        this.visemeFrames = data.frames || [];
        break;
      case "play_animation":
        console.warn("[brain] play_animation not yet implemented:", data);
        this._send({ type: "error", message: `play_animation not yet implemented: ${data.name}` });
        break;
      default:
        console.warn("[brain] ignoring unknown message type:", data.type);
    }
  }
}

function _arrayBufferToBase64(buffer) {
  let binary = "";
  const bytes = new Uint8Array(buffer);
  for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
  return btoa(binary);
}
