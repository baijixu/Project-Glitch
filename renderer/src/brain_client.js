// WebSocket client connecting OUT to the Brain's server (protocol.md; the
// Renderer is always the client -- browser/WebView JS can't accept incoming
// connections).
//
// BrainClient is the connection, the chat box, and her face and voice. Each
// Settings section and chat feature is its own module; each owns the messages
// it handles (its `handlers`), and BrainClient routes Brain's messages to them.
// What modules get from BrainClient (`app`): send, sendForReply, canSendReply,
// setStatus/flashStatus, log, connected/connectionState, inputEl and history.
//
//   characters_ui.js .. role-play toggle, profiles, souls
//   engines_ui.js ..... speech engines + voices, LLM engines, harnesses
//   avatars_ui.js ..... avatar picker, import, rename, delete
//   history_ui.js ..... History panel, over-avatar bubbles, resend/edit/rate
//   capture.js ........ camera/screen/file, push-to-talk and Always-On mic
//   settings_ui.js .... on/off switches, memory, Notes and soul/user editors, context meter
//   debug_ui.js ....... the debug log and Restart Brain
//   (lessons, training, sampling, chat logs, memory profiles, curiosity countdown: *_ui.js)

import { AvatarsUI } from "./avatars_ui.js";
import { Capture } from "./capture.js";
import { CharactersUI } from "./characters_ui.js";
import { ChatLogsUI } from "./chat_logs_ui.js";
import { CuriosityTimerUI } from "./curiosity_timer_ui.js";
import { DebugUI, describeSettingChange, deviceSummary } from "./debug_ui.js";
import { HarnessUI, LlmEnginesUI, SpeechEnginesUI } from "./engines_ui.js";
import { HistoryUI } from "./history_ui.js";
import { LessonsUI } from "./lessons_ui.js";
import { MemoryProfilesUI } from "./memory_profiles_ui.js";
import { SamplingUI } from "./sampling_ui.js";
import { SettingsUI } from "./settings_ui.js";
import { TrainingUI } from "./training_ui.js";
import { $, base64ToArrayBuffer } from "./ui.js";

const RECONNECT_DELAY_MS = 3000;
// The subtitle fades out this long after it finishes streaming in.
const SUBTITLE_FADE_DELAY_MS = 10000;
// How long a reply can take before the connection light goes yellow ("slow",
// not "unreachable") -- well above a normal reply: the local model has ranged
// from ~1.5s to 90s+ for the same kind of message.
const SLOW_REPLY_THRESHOLD_MS = 15000;
// How long the chat bar and side buttons stay up with no activity.
const CHAT_BAR_IDLE_MS = 10000;
const TOAST_DURATION_MS = 4000;

// This VRM's mood expressions (vrm.expressionManager.expressionMap). "neutral"
// isn't one -- it means all of these fade to 0. One at a time, like a face.
const MOODS = ["happy", "sad", "surprised", "angry", "relaxed"];
const EXPRESSION_FADE_SEC = 0.3;

// Everything that sends a message she replies to: off while a reply is on its
// way (so a second click can't pile up messages) and while not connected. A
// disabled button gets no pointer events, which also stops push-to-talk;
// Always-On checks canSendReply itself.
const REPLY_CONTROLS = [
  "send-button", "overlay-resend-button", "overlay-edit-button", "file-upload-button",
  "camera-vision-button", "desktop-vision-button", "mic-button",
];

export class BrainClient {
  constructor({ url, authToken, vrm, onAvatarSwap }) {
    this.url = url;
    this.authToken = authToken;
    this.vrm = vrm; // main.js reassigns this when the avatar changes
    this.socket = null;
    this.connectionState = "red"; // not connected yet
    this.inputEl = $("message-input");
    this.statusEl = $("status");
    this.subtitleEl = $("subtitle");
    this._awaitingReply = false;
    this._slowReplyTimer = null;
    this._replySentAt = null; // when the pending message went out, for the debug log's round trip
    this._pendingSpeakText = ""; // her reply's text, shown as a subtitle once its audio arrives

    this.audioContext = new (window.AudioContext || window.webkitAudioContext)();
    this.visemeFrames = [];
    this.playbackStartTime = 0;
    this.lipSyncActive = false;
    this.moodWeights = Object.fromEntries(MOODS.map((m) => [m, 0]));
    this.moodTargets = Object.fromEntries(MOODS.map((m) => [m, 0]));

    const send = (message) => this.send(message);
    this.debug = new DebugUI(this);
    this.characters = new CharactersUI(this);
    this.settings = new SettingsUI(this);
    this.history = new HistoryUI(this, {
      displayName: (role) => ({ user: "You", glitch: this.characters.glitchName })[role] || "",
      onClear: () => this.settings.resetContextMeter(),
    });
    this.capture = new Capture(this);
    this.avatars = new AvatarsUI(this, { onAvatarSwap });
    this.speechEngines = new SpeechEnginesUI(this);
    this.llmEngines = new LlmEnginesUI(this, { onEnginesChanged: (names) => this.characters.setEngineNames(names) });
    this.lessons = new LessonsUI({ send });
    this.training = new TrainingUI({ send, onNewProposals: (fact) => this._showToast(fact, "🧠 Memory proposed (review in Settings): ") });
    this.sampling = new SamplingUI({ send });
    this.chatLogs = new ChatLogsUI({ send });
    this.memoryProfiles = new MemoryProfilesUI({ send });
    this.curiosityTimer = new CuriosityTimerUI({ send });
    this.harness = new HarnessUI(this, {
      onLockChange: (locked) => {
        for (const ui of [this.characters, this.speechEngines, this.llmEngines, this.avatars, this.sampling, this.memoryProfiles]) {
          ui.setLocked(locked);
        }
      },
    });
    $("settings-button")?.addEventListener("click", () => this.chatLogs.refresh()); // its list is never stale

    this._handlers = {
      ping: () => this.send({ type: "pong" }),
      set_expression: (data) => this._setMood(data.name, data.weight),
      speak_text: (data) => {
        this._pendingSpeakText = data.text;
        this.history.add("glitch", data.text);
        this._replyArrived(`reply received (${data.text.length} chars)`);
      },
      // Sent instead of speak_text when she had nothing to say (a blank voice
      // message, an empty reply) -- said so, rather than looking broken.
      no_reply: () => {
        this.flashStatus("(no reply)", 4000);
        this._replyArrived("no reply (nothing to say)");
      },
      speak_audio: (data) =>
        this._playAudio(data.audio_b64).catch((err) => {
          console.warn("[brain] couldn't play audio:", err);
          this.log("audio", `playback failed: ${err.message || err}`);
        }),
      viseme_stream: (data) => (this.visemeFrames = data.frames || []),
      user_transcript: (data) => this.capture.handleTranscript(data.text),
      conversation_cleared: () => this.history.clear(),
      memory_learned: (data) => {
        this._showToast(data.fact);
        this.history.add("system", `🧠 Learned: ${data.fact}`);
      },
      lesson_event: (data) => {
        const labels = { applied: "📘 Lesson learned", proposed: "📝 Proposed lesson (approve in Settings)", noted: "👀 Noticed", error: "⚠️ Lessons" };
        const label = labels[data.kind] || "📘 Lesson";
        this._showToast(data.text, `${label}: `);
        if (data.kind !== "error") this.history.add("system", `${label}: ${data.text}`);
      },
      lessons_state: (data) => this.lessons.handleState(data),
      training_state: (data) => this.training.handleState(data),
      sampling_state: (data) => this.sampling.handleState(data),
      chat_logs: (data) => this.chatLogs.handleList(data),
      chat_log_content: (data) => this.chatLogs.handleContent(data),
      memory_profiles: (data) => this.memoryProfiles.handleState(data),
      memory_profile_content: (data) => this.memoryProfiles.handleContent(data),
      curiosity_timer: (data) => this.curiosityTimer.handleTimer(data),
      play_animation: (data) => {
        console.warn("[brain] play_animation not yet implemented:", data);
        this.send({ type: "error", message: `play_animation not yet implemented: ${data.name}` });
      },
      ...this.characters.handlers,
      ...this.settings.handlers,
      ...this.avatars.handlers,
      ...this.speechEngines.handlers,
      ...this.llmEngines.handlers,
      ...this.harness.handlers,
      ...this.debug.handlers,
    };

    $("send-button")?.addEventListener("click", () => this._sendInput());
    this.inputEl?.addEventListener("keydown", (e) => {
      if (e.key === "Enter") this._sendInput();
    });
    $("stop-button")?.addEventListener("click", () => this._stopReply());
    // Any activity anywhere brings the chat bar back and restarts its countdown.
    document.addEventListener("pointerdown", () => this._resetIdleTimer());
    document.addEventListener("keydown", () => this._resetIdleTimer());
    this._resetIdleTimer();
    this._setConnectionState(this.connectionState);
  }

  connect() {
    this.socket = new WebSocket(this.url);
    this.socket.addEventListener("open", () => {
      console.log("[brain] connected");
      this.setStatus("");
      this._setConnectionState("green");
      this.log("ws", "connected");
      // The token is only checked when Brain has one configured (server.py's
      // _authenticate); `device` names this device in Brain's debug log.
      this.send({ type: "ready", model: "Glitch.vrm", token: this.authToken, device: deviceSummary() });
      this.debug.handleConnected();
    });
    this.socket.addEventListener("close", (event) => {
      console.warn("[brain] disconnected, retrying...");
      this.setStatus("Brain: disconnected, retrying...");
      this._setConnectionState("red");
      this.log("ws", `disconnected (code ${event.code}${event.reason ? ", " + event.reason : ""}), retrying...`);
      this._setAwaitingReply(false); // a dropped connection can't leave Send stuck off
      setTimeout(() => this.connect(), RECONNECT_DELAY_MS);
    });
    this.socket.addEventListener("error", (err) => {
      console.error("[brain] socket error:", err);
      this._setConnectionState("red");
      this.log("ws", "socket error");
    });
    this.socket.addEventListener("message", (event) => this._handleMessage(event.data));
  }

  // Once per frame, from main.js: lipsync from the viseme stream by elapsed
  // playback time, and mood expressions easing toward their targets.
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

  // ---- What modules use ------------------------------------------------------

  get connected() {
    return this.connectionState !== "red";
  }

  send(message) {
    if (this.socket?.readyState !== WebSocket.OPEN) return;
    this.socket.send(JSON.stringify(message));
    const described = describeSettingChange(message);
    if (described) this.log("settings", described);
  }

  canSendReply() {
    return !this._awaitingReply && this.connected;
  }

  // Sends something she'll reply to, and waits for the reply.
  sendForReply(message, logText) {
    this.send(message);
    this._startSlowReplyTimer();
    this._setAwaitingReply(true);
    this._replySentAt = Date.now();
    this.log("ws", logText); // sizes only, never the text
  }

  setStatus(text) {
    if (this.statusEl) this.statusEl.textContent = text;
  }

  flashStatus(text, ms) {
    this.setStatus(text);
    setTimeout(() => this.setStatus(""), ms);
  }

  log(category, message, ms) {
    this.debug.log(category, message, ms);
  }

  // ---- Messages and replies ---------------------------------------------------

  _handleMessage(raw) {
    let data;
    try {
      data = JSON.parse(raw);
    } catch {
      console.warn("[brain] ignoring non-JSON message:", raw);
      this.log("ws", `received non-JSON message (${raw.length} chars)`);
      return;
    }
    const handler = this._handlers[data.type];
    if (handler) return handler(data);
    console.warn("[brain] ignoring unknown message type:", data.type);
    this.log("ws", `received unknown message type: ${data.type}`);
  }

  _sendInput() {
    if (this._awaitingReply) return;
    const text = this.inputEl?.value.trim() || "";
    if (this.capture.sendAttached(text)) return void (this.inputEl.value = "");
    if (!text) return;
    this.sendForReply({ type: "user_text", text }, `sent user_text (${text.length} chars)`);
    this.history.add("user", text);
    this.inputEl.value = "";
  }

  _replyArrived(logText) {
    this._clearSlowReplyTimer();
    this._setAwaitingReply(false);
    if (this._replySentAt != null) {
      this.log("ws", logText, Date.now() - this._replySentAt);
      this._replySentAt = null;
    }
  }

  // The Stop button (only there while a reply is pending): Brain drops the
  // reply and sends nothing, so the input comes back right away. Her side of
  // that exchange just never appears; Resend Last re-answers it.
  _stopReply() {
    if (!this._awaitingReply) return;
    this.send({ type: "stop_reply" });
    this._clearSlowReplyTimer();
    this._replySentAt = null;
    this._setAwaitingReply(false);
    this.log("ws", "stopped reply");
  }

  _setAwaitingReply(awaiting) {
    this._awaitingReply = awaiting;
    this._updateReplyControls();
    for (const id of ["stop-button", "processing-indicator"]) if ($(id)) $(id).hidden = !awaiting;
  }

  _updateReplyControls() {
    const disabled = !this.canSendReply();
    for (const id of REPLY_CONTROLS) if ($(id)) $(id).disabled = disabled;
  }

  _setConnectionState(state) {
    this.connectionState = state;
    this._updateReplyControls();
    const lightEl = $("connection-light");
    if (!lightEl) return;
    lightEl.classList.remove("red", "yellow", "green");
    lightEl.classList.add(state);
  }

  // If it fires first, Brain is connected (the light would be red otherwise),
  // just slow -- usually the model itself.
  _startSlowReplyTimer() {
    clearTimeout(this._slowReplyTimer);
    this._slowReplyTimer = setTimeout(() => {
      if (this.connected) this._setConnectionState("yellow");
    }, SLOW_REPLY_THRESHOLD_MS);
  }

  _clearSlowReplyTimer() {
    clearTimeout(this._slowReplyTimer);
    this._slowReplyTimer = null;
    if (this.connected) this._setConnectionState("green");
  }

  // The chat bar and side buttons fade after CHAT_BAR_IDLE_MS -- but not while
  // typing, recording or listening.
  _resetIdleTimer() {
    for (const id of ["chat-bar", "side-actions"]) $(id)?.classList.remove("idle");
    clearTimeout(this._idleTimer);
    this._idleTimer = setTimeout(() => this._hideIdleControls(), CHAT_BAR_IDLE_MS);
  }

  _hideIdleControls() {
    if (document.activeElement === this.inputEl || this.capture.busy) {
      this._idleTimer = setTimeout(() => this._hideIdleControls(), CHAT_BAR_IDLE_MS);
      return;
    }
    for (const id of ["chat-bar", "side-actions"]) $(id)?.classList.add("idle");
  }

  // A brief heads-up above the chat bar (a memory learned, a lesson) -- the
  // History panel keeps the lasting record.
  _showToast(text, prefix = "🧠 Learned: ") {
    const toastEl = $("memory-toast");
    if (!toastEl) return;
    toastEl.textContent = `${prefix}${text}`;
    toastEl.classList.add("visible");
    clearTimeout(this._toastTimer);
    this._toastTimer = setTimeout(() => toastEl.classList.remove("visible"), TOAST_DURATION_MS);
  }

  // ---- Her face and voice -------------------------------------------------------

  // One mood at a time: setting one fades the others; "neutral" fades them all.
  _setMood(name, weight) {
    for (const m of MOODS) this.moodTargets[m] = m === name ? weight : 0;
  }

  // Snaps back to neutral instead of easing: a hidden tab throttles
  // requestAnimationFrame, which would leave the last mood frozen on her face.
  _resetMoodImmediate() {
    for (const name of MOODS) {
      this.moodTargets[name] = 0;
      this.moodWeights[name] = 0;
      this.vrm.expressionManager?.setValue(name, 0);
    }
  }

  // Word by word, paced to the audio's real length. A little faster than an
  // even split (PACE_FACTOR): TTS has silence at both ends, and an emoji counts
  // as a word but takes no time to say -- an even split trailed her voice.
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
      this.subtitleEl.append(span, " ");
      this.subtitleEl.scrollTop = this.subtitleEl.scrollHeight; // keep the newest words in view
      i++;
      if (i >= words.length) {
        clearInterval(this._subtitleStreamTimer);
        this._subtitleTimer = setTimeout(() => this.subtitleEl.classList.remove("visible"), SUBTITLE_FADE_DELAY_MS);
      }
    };
    revealNext();
    this._subtitleStreamTimer = setInterval(revealNext, intervalMs);
  }

  async _playAudio(audioB64) {
    // Browsers (phones especially) start audio suspended until the page is
    // tapped, and then play nothing, silently -- which looks exactly like "she
    // never speaks". Resume it, and say so in the log.
    if (this.audioContext.state !== "running") {
      const before = this.audioContext.state;
      try {
        await this.audioContext.resume();
      } catch {
        // stays suspended; logged below
      }
      this.log(
        "audio",
        this.audioContext.state === "running"
          ? `audio was ${before} -- resumed`
          : `can't play her voice: audio is ${this.audioContext.state} (the browser wants a tap on the page first)`,
      );
    }
    const audioBuffer = await this.audioContext.decodeAudioData(base64ToArrayBuffer(audioB64));
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
}
