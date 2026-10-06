// What the side buttons send: a camera or screen snapshot, an attached picture
// or text file (📎), and voice -- push-to-talk, or Mic Always-On's simple
// volume-based listening. Also which of those buttons are shown (Settings ->
// Devices), a per-browser preference.

import { $, arrayBufferToBase64, readPref, writePref } from "./ui.js";

// Longest edge a snapshot is scaled down to -- vision models don't benefit
// from more, and it keeps the request small. Aspect ratio is kept.
const MAX_VISION_DIMENSION = 1024;
const VISION_JPEG_QUALITY = 0.7;
// A phone camera often returns black frames right after it opens (sensor
// warm-up), so a grab keeps sampling until one is bright enough (average
// 0-255, see sampleAvgBrightness), for at most VISION_FRAME_WAIT_MS, then takes
// the brightest. Under VISION_BLACK_BRIGHTNESS even the best frame counts as a
// failed capture. VISION_FRAME_TIMEOUT_MS bounds the whole grab.
const VISION_MIN_BRIGHTNESS = 12;
const VISION_BLACK_BRIGHTNESS = 3;
const VISION_FRAME_WAIT_MS = 2500;
const VISION_FRAME_TIMEOUT_MS = 6000;

// An attached text file goes into the message itself -- capped, so one huge
// file can't blow up a turn.
const MAX_UPLOADED_TEXT_CHARS = 20000;
const TEXT_FILE_EXTENSIONS = [".txt", ".md", ".csv", ".json", ".log", ".yaml", ".yml"];

// Mic Always-On's voice detection: RMS of the waveform over a threshold -- not
// a real speech detector, but enough to tell talking from a quiet room. A noisy
// room or a quiet mic may need the threshold retuned.
const MIC_VAD_POLL_MS = 100;
const MIC_VAD_SPEECH_RMS_THRESHOLD = 0.025;
const MIC_VAD_SILENCE_MS = 1200;

const DEVICE_BUTTONS = { camera: "camera-vision-button", desktop: "desktop-vision-button", mic: "mic-button" };
const DEVICE_TOGGLES = { camera: "camera-enabled-toggle", desktop: "desktop-capture-enabled-toggle", mic: "mic-enabled-toggle" };

export class Capture {
  // app: sendForReply, canSendReply, flashStatus, log, history (add/updateText), inputEl.
  constructor(app) {
    this._app = app;
    this.micButtonEl = $("mic-button");
    this.micLabelEl = $("mic-label");
    this.alwaysOnToggleEl = $("mic-always-on-toggle");
    this.fileInputEl = $("file-upload-input");
    this.mediaRecorder = null;
    this._chunks = [];
    this._alwaysOn = null; // {stream, audioCtx, analyser, timer, speaking, silenceSince} while listening
    this._pendingVoice = null; // the history entry waiting for its transcript
    this._attached = null; // send(caption) for a picture or file waiting on Send
    this._placeholder = app.inputEl?.placeholder || "";

    $("camera-vision-button")?.addEventListener("click", () => this._captureVision("camera"));
    $("desktop-vision-button")?.addEventListener("click", () => this._captureVision("desktop"));
    $("file-upload-button")?.addEventListener("click", () => this.fileInputEl?.click());
    this.fileInputEl?.addEventListener("change", () => {
      const file = this.fileInputEl.files?.[0];
      this.fileInputEl.value = ""; // so picking the same file again still fires "change"
      this._attachFile(file);
    });
    // Backspace in an empty text box (or Escape) drops an attached picture or file.
    app.inputEl?.addEventListener("keydown", (e) => {
      const remove = e.key === "Escape" || (e.key === "Backspace" && !app.inputEl.value);
      if (remove && this._attached) this._attach(null);
    });

    // Push-to-talk: hold, speak, release. Pointer events cover mouse and touch;
    // leaving or cancelling stops it, so it can't get stuck recording.
    this.micButtonEl?.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      this._startRecording();
    });
    for (const type of ["pointerup", "pointerleave", "pointercancel"]) this.micButtonEl?.addEventListener(type, () => this._stopRecording());
    this.alwaysOnToggleEl?.addEventListener("change", () => (this.alwaysOnToggleEl.checked ? this._startAlwaysOn() : this._stopAlwaysOn()));

    // Which buttons show -- applied before the first paint, so a hidden one
    // doesn't flash. Always-On is deliberately not remembered: silently
    // reopening the mic on page load would be a surprising thing to do.
    for (const kind of Object.keys(DEVICE_BUTTONS)) {
      const enabled = this._enabled(kind);
      if ($(DEVICE_TOGGLES[kind])) $(DEVICE_TOGGLES[kind]).checked = enabled;
      if ($(DEVICE_BUTTONS[kind])) $(DEVICE_BUTTONS[kind]).hidden = !enabled;
      $(DEVICE_TOGGLES[kind])?.addEventListener("change", (e) => this._setEnabled(kind, e.target.checked));
    }
    this._setAlwaysOnAvailable(this._enabled("mic"));
  }

  // Recording or listening -- the chat bar stays up meanwhile.
  get busy() {
    return this.mediaRecorder?.state === "recording" || !!this._alwaysOn;
  }

  // Send/Enter with a picture or file attached: it goes with `caption` (may be
  // empty). False when nothing's attached.
  sendAttached(caption) {
    const send = this._attached;
    if (!send) return false;
    this._attach(null);
    send(caption);
    return true;
  }

  // Holds a picture or file until Send, so there's a chance to type about it.
  // A newer one replaces it; null drops it.
  _attach(label, send) {
    this._attached = send || null;
    const input = this._app.inputEl;
    if (!input) return;
    input.placeholder = send ? `${label} attached -- add a message and send (backspace removes it)` : this._placeholder;
    if (send) input.focus();
  }

  // Brain's user_transcript: the voice message's placeholder becomes its words.
  handleTranscript(text) {
    this._app.history.updateText(this._pendingVoice, text);
    this._pendingVoice = null;
  }

  _enabled(kind) {
    return readPref(`glitch_${kind}_enabled`, "1") !== "0";
  }

  _setEnabled(kind, enabled) {
    writePref(`glitch_${kind}_enabled`, enabled ? "1" : "0");
    if ($(DEVICE_BUTTONS[kind])) $(DEVICE_BUTTONS[kind]).hidden = !enabled;
    if (kind === "mic") this._setAlwaysOnAvailable(enabled);
  }

  // Always-On only makes sense while the mic button exists: turning the mic off
  // stops it and greys its toggle out.
  _setAlwaysOnAvailable(available) {
    if (!available) {
      if (this.alwaysOnToggleEl) this.alwaysOnToggleEl.checked = false;
      this._stopAlwaysOn();
    }
    if (this.alwaysOnToggleEl) this.alwaysOnToggleEl.disabled = !available;
  }

  // navigator.mediaDevices doesn't exist at all outside a secure context (https,
  // or http on localhost) -- e.g. opening Glitch from a phone over plain http --
  // which would otherwise look exactly like the user clicking "block".
  // `label` prefixes the log line: "camera", "desktop", "always-on", or "" for push-to-talk.
  _mediaDevicesAvailable(label, logCategory) {
    if (navigator.mediaDevices) return true;
    this._app.log(logCategory, `${label ? label + " " : ""}unavailable: navigator.mediaDevices is undefined (needs HTTPS or localhost)`);
    return false;
  }

  // ---- Pictures -------------------------------------------------------------------

  // One frame from the camera (getUserMedia) or the screen (getDisplayMedia --
  // its picker is the permission prompt), attached until Send. The stream stops
  // straight away: a snapshot, not a feed.
  async _captureVision(source) {
    if (!this._app.canSendReply()) return;
    const desktop = source === "desktop";
    if (!this._mediaDevicesAvailable(source, "vision")) {
      return this._app.flashStatus(desktop ? "Screen share needs HTTPS or localhost" : "Camera needs HTTPS or localhost", 4000);
    }
    // getDisplayMedia is simply absent on most mobile browsers -- calling it
    // would throw a TypeError that reads like "denied".
    const apiName = desktop ? "getDisplayMedia" : "getUserMedia";
    if (typeof navigator.mediaDevices[apiName] !== "function") {
      this._app.log("vision", `${source} unavailable: navigator.mediaDevices.${apiName} doesn't exist on this browser`);
      return this._app.flashStatus(desktop ? "Screen sharing isn't supported on this browser" : "Camera isn't supported on this browser", 4000);
    }
    // "ideal", not "exact": prefers a phone's rear camera, still works with a laptop's only one.
    const openStream = () =>
      desktop
        ? navigator.mediaDevices.getDisplayMedia({ video: true })
        : navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: "environment" } } });

    // A camera grab that fails gets one retry on a fresh stream -- whatever used
    // the camera last can leave it in a bad state. A screen share isn't retried:
    // it would re-prompt the picker.
    const maxAttempts = desktop ? 1 : 2;
    let dataUrl;
    for (let attempt = 1; attempt <= maxAttempts && !dataUrl; attempt++) {
      let stream;
      try {
        stream = await openStream();
      } catch (err) {
        console.warn(`${source} access denied or unavailable:`, err);
        this._app.log("vision", `${source} getMedia failed: ${err.message || err}`);
        return this._app.flashStatus(desktop ? "Screen share denied" : "Camera access denied", 4000);
      }
      try {
        dataUrl = await this._grabFrame(stream);
      } catch (err) {
        console.warn(`${source} frame grab failed (attempt ${attempt}/${maxAttempts}):`, err);
        this._app.log("vision", `${source} frame grab failed (attempt ${attempt}/${maxAttempts}): ${err.message || err}`);
      } finally {
        stream.getTracks().forEach((track) => track.stop());
      }
      if (!dataUrl && attempt < maxAttempts) await new Promise((r) => setTimeout(r, 500)); // let the camera fully release
    }
    if (!dataUrl) return this._app.flashStatus(desktop ? "Couldn't capture the screen" : "Camera gave no picture -- try again", 4000);
    this._sendPicture(dataUrl, source);
  }

  // Plays the stream into an off-screen <video> (not zero-size: some phones skip
  // decoding an invisible one) just long enough to draw a bright enough frame,
  // and returns it as a downscaled JPEG data URL.
  _grabFrame(stream) {
    return new Promise((resolve, reject) => {
      const video = document.createElement("video");
      video.srcObject = stream;
      video.muted = true;
      video.playsInline = true;
      video.style.cssText = "position:fixed; left:-9999px; top:-9999px; pointer-events:none;";
      document.body.appendChild(video);

      const startedAt = performance.now();
      let settled = false;
      let best = null; // {canvas, brightness}
      let frames = 0;
      // Every exit comes through here, so the video is always removed and the
      // promise always settles (the caller's `finally` stops the stream).
      const finish = (settle, value) => {
        if (settled) return;
        settled = true;
        clearTimeout(timeoutId);
        video.remove();
        settle(value);
      };
      const timeoutId = setTimeout(
        () => finish(reject, new Error(`no camera frame within ${VISION_FRAME_TIMEOUT_MS}ms (readyState=${video.readyState}, frames=${frames})`)),
        VISION_FRAME_TIMEOUT_MS,
      );
      const track = stream.getVideoTracks()[0];
      const trackInfo = () =>
        track ? `track=${track.readyState}${track.muted ? "/muted" : ""} "${track.label}" ${JSON.stringify(track.getSettings?.() || {})}` : "no video track";

      const sampleFrame = () => {
        if (settled) return;
        try {
          frames++;
          const canvas = scaledCanvas(video, video.videoWidth, video.videoHeight);
          const brightness = sampleAvgBrightness(canvas.getContext("2d"), canvas.width, canvas.height);
          if (!best || brightness > best.brightness) best = { canvas, brightness };
          // -1: brightness couldn't be measured -- no evidence the frame is bad.
          const bright = brightness < 0 || brightness >= VISION_MIN_BRIGHTNESS;
          const waited = performance.now() - startedAt;
          if (!bright && waited < VISION_FRAME_WAIT_MS) return nextFrame();
          this._app.log(
            "vision",
            `captured ${best.canvas.width}x${best.canvas.height} from ${video.videoWidth}x${video.videoHeight} video ` +
              `after ${frames} frame(s)/${Math.round(waited)}ms, readyState=${video.readyState}, ` +
              `avgBrightness=${best.brightness}, ${trackInfo()}`,
          );
          if (best.brightness >= 0 && best.brightness < VISION_BLACK_BRIGHTNESS) {
            return finish(reject, new Error(`camera returned only black frames (best avgBrightness=${best.brightness}, ${frames} frames)`));
          }
          finish(resolve, best.canvas.toDataURL("image/jpeg", VISION_JPEG_QUALITY));
        } catch (err) {
          finish(reject, err);
        }
      };
      // requestVideoFrameCallback fires when a decoded frame is actually shown --
      // the one guarantee loadeddata/play() don't give. A timer where it's missing.
      const nextFrame = () =>
        typeof video.requestVideoFrameCallback === "function" ? video.requestVideoFrameCallback(() => sampleFrame()) : setTimeout(sampleFrame, 100);

      video.addEventListener("loadeddata", () => video.play().then(nextFrame).catch((err) => finish(reject, err)), { once: true });
      video.addEventListener("error", () => finish(reject, video.error), { once: true });
    });
  }

  _sendPicture(dataUrl, source) {
    const imageB64 = dataUrl.slice(dataUrl.indexOf(",") + 1);
    const label = { desktop: "🖥️ (screen)", upload: "📎 (photo)" }[source] || "📷 (photo)";
    this._attach(label, (text) => {
      this._app.sendForReply(
        { type: "user_text", text, image_b64: imageB64, image_mime: "image/jpeg" },
        `sent user_text with ${source} image (${Math.round(imageB64.length / 1024)} KB)`,
      );
      this._app.history.add("user", text || label, dataUrl);
    });
  }

  // 📎: a picture goes the same way as a snapshot; a text file's content goes
  // into the message so she can read it. Anything else is refused rather than
  // sent as garbage.
  async _attachFile(file) {
    if (!file || !this._app.canSendReply()) return;
    if (file.type.startsWith("image/")) {
      let dataUrl;
      try {
        dataUrl = await downscaleImage(await readAsDataUrl(file));
      } catch (err) {
        console.warn("failed to read/downscale image file:", err);
        this._app.log("upload", `image file failed: ${err.message || err}`);
        return this._app.flashStatus("Couldn't read that image", 4000);
      }
      return this._sendPicture(dataUrl, "upload");
    }
    // file.type is often empty or generic for text files, so the extension counts too.
    const isText =
      file.type.startsWith("text/") || file.type === "application/json" || TEXT_FILE_EXTENSIONS.some((ext) => file.name.toLowerCase().endsWith(ext));
    if (!isText) return this._app.flashStatus("Unsupported file type", 4000);
    let content;
    try {
      content = await file.text();
    } catch (err) {
      console.warn("failed to read text file:", err);
      this._app.log("upload", `text file failed: ${err.message || err}`);
      return this._app.flashStatus("Couldn't read that file", 4000);
    }
    const truncated = content.length > MAX_UPLOADED_TEXT_CHARS;
    if (truncated) content = content.slice(0, MAX_UPLOADED_TEXT_CHARS);
    const attachment = `Attached file "${file.name}"${truncated ? " (truncated)" : ""}:\n\n${content}`;
    this._attach(`📎 ${file.name}`, (caption) => {
      this._app.sendForReply(
        { type: "user_text", text: caption ? `${caption}\n\n${attachment}` : attachment },
        `sent user_text with attached text file (${content.length} chars${truncated ? ", truncated" : ""})`,
      );
      this._app.history.add("user", `${caption ? caption + " " : ""}📎 ${file.name}`);
    });
  }

  // ---- Voice ----------------------------------------------------------------------

  // label: "" for push-to-talk, "always-on" for Always-On (for the log).
  async _openMic(label) {
    if (!this._mediaDevicesAvailable(label, "mic")) {
      this._app.flashStatus("Microphone needs HTTPS or localhost", 4000);
      return null;
    }
    try {
      return await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
      console.warn("Microphone access denied or unavailable:", err);
      this._app.log("mic", `${label ? label + " " : ""}getUserMedia failed: ${err.message || err}`);
      this._app.flashStatus("Microphone access denied", 4000);
      return null;
    }
  }

  _setMicLabel(text) {
    if (this.micLabelEl) this.micLabelEl.textContent = ` ${text}`;
  }

  // Starts recording one utterance on `stream`; `onStop` runs when it ends.
  _record(stream, onStop) {
    this._chunks = [];
    this.mediaRecorder = new MediaRecorder(stream);
    this.mediaRecorder.addEventListener("dataavailable", (e) => {
      if (e.data.size > 0) this._chunks.push(e.data);
    });
    this.mediaRecorder.addEventListener("stop", onStop);
    this.mediaRecorder.start();
  }

  // Push-to-talk opens and closes its own stream on every press. (With
  // Always-On the button is just a listening light.)
  async _startRecording() {
    if (this._alwaysOn || this.mediaRecorder?.state === "recording") return;
    this._app.interruptSpeech(); // pressing 🎤 while she talks cuts her off
    this._held = true;
    const stream = await this._openMic("");
    if (!stream) return;
    // Let go before the mic opened (always, behind the first permission prompt):
    // recording now would never be stopped.
    if (!this._held || this.mediaRecorder?.state === "recording") return stream.getTracks().forEach((track) => track.stop());
    this._record(stream, () => {
      stream.getTracks().forEach((track) => track.stop());
      this._sendRecording();
    });
    this.micButtonEl?.classList.add("recording");
    this._setMicLabel("Recording...");
  }

  _stopRecording() {
    this._held = false;
    if (this._alwaysOn || this.mediaRecorder?.state !== "recording") return;
    this.mediaRecorder.stop();
    this.micButtonEl?.classList.remove("recording");
    this._setMicLabel("Hold to talk");
  }

  // Always-On keeps one stream open across utterances, so the mic light only
  // comes on once per session; _pollVad starts and stops a recording on it.
  async _startAlwaysOn() {
    if (this._alwaysOn) return;
    const stream = await this._openMic("always-on");
    if (!stream) {
      if (this.alwaysOnToggleEl) this.alwaysOnToggleEl.checked = false;
      return;
    }
    const audioCtx = new AudioContext();
    const analyser = audioCtx.createAnalyser();
    analyser.fftSize = 512;
    audioCtx.createMediaStreamSource(stream).connect(analyser);
    this._alwaysOn = { stream, audioCtx, analyser, speaking: false, silenceSince: null };
    this._alwaysOn.timer = setInterval(() => this._pollVad(), MIC_VAD_POLL_MS);
    this.micButtonEl?.classList.add("recording");
    this._setMicLabel("Listening...");
    this._app.log("mic", "always-on: listening");
  }

  _stopAlwaysOn() {
    const listening = this._alwaysOn;
    if (!listening) return;
    clearInterval(listening.timer);
    this._alwaysOn = null;
    // Mid-utterance: send it first, then close the stream (closing it straight
    // away could cut off its end).
    const teardown = () => {
      listening.stream.getTracks().forEach((track) => track.stop());
      listening.audioCtx.close();
    };
    if (this.mediaRecorder?.state === "recording") {
      this.mediaRecorder.addEventListener("stop", teardown, { once: true });
      this.mediaRecorder.stop();
    } else {
      teardown();
    }
    this.micButtonEl?.classList.remove("recording");
    this._setMicLabel("Hold to talk");
    this._app.log("mic", "always-on: stopped");
  }

  // Every MIC_VAD_POLL_MS: starts recording when the volume crosses the
  // threshold, and sends once it's stayed under it for MIC_VAD_SILENCE_MS. Not
  // while she's still answering the last one (like the button being disabled),
  // nor while she's speaking -- her voice from the speakers would start a recording.
  _pollVad() {
    const listening = this._alwaysOn;
    if (!listening || !this._app.canSendReply() || this._app.lipSyncActive) return;
    const data = new Uint8Array(listening.analyser.fftSize);
    listening.analyser.getByteTimeDomainData(data);
    let sumSquares = 0;
    for (const sample of data) sumSquares += ((sample - 128) / 128) ** 2;
    const rms = Math.sqrt(sumSquares / data.length);

    if (rms > MIC_VAD_SPEECH_RMS_THRESHOLD) {
      listening.silenceSince = null;
      if (!listening.speaking) {
        listening.speaking = true;
        this._record(listening.stream, () => this._sendRecording()); // the stream stays open for the next one
        this._setMicLabel("Recording...");
      }
    } else if (listening.speaking) {
      if (listening.silenceSince === null) {
        listening.silenceSince = Date.now();
      } else if (Date.now() - listening.silenceSince >= MIC_VAD_SILENCE_MS) {
        listening.speaking = false;
        listening.silenceSince = null;
        this.mediaRecorder.stop();
        this._setMicLabel("Listening...");
      }
    }
  }

  // The transcript only exists Brain-side, so the history shows a placeholder
  // until user_transcript arrives (or keeps it, if speech-to-text gave nothing).
  async _sendRecording() {
    if (this._chunks.length === 0) return;
    const mimeType = this.mediaRecorder.mimeType || "audio/webm";
    const blob = new Blob(this._chunks, { type: mimeType });
    const audioB64 = arrayBufferToBase64(await blob.arrayBuffer());
    this._app.sendForReply({ type: "user_audio", audio_b64: audioB64, mime_type: mimeType }, `sent user_audio (${blob.size} bytes, ${mimeType})`);
    this._pendingVoice = this._app.history.add("user", "🎤 (voice message)");
  }
}

function scaledCanvas(source, width, height) {
  const scale = Math.min(1, MAX_VISION_DIMENSION / Math.max(width, height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(width * scale);
  canvas.height = Math.round(height * scale);
  canvas.getContext("2d").drawImage(source, 0, 0, canvas.width, canvas.height);
  return canvas;
}

function readAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(reader.error || new Error("FileReader failed"));
    reader.readAsDataURL(file);
  });
}

// Same downscale as a snapshot -- a phone photo can be 10+ MB at full size.
function downscaleImage(dataUrl) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(scaledCanvas(img, img.naturalWidth, img.naturalHeight).toDataURL("image/jpeg", VISION_JPEG_QUALITY));
    img.onerror = () => reject(new Error("failed to decode image"));
    img.src = dataUrl;
  });
}

// A cheap sampled average brightness of a canvas, 0 (black) to 255 (white), or
// -1 if it can't be read -- every ~200th pixel is plenty to tell black apart.
function sampleAvgBrightness(ctx, width, height) {
  try {
    const { data } = ctx.getImageData(0, 0, width, height);
    const stride = 4 * Math.max(1, Math.floor((width * height) / 200));
    let sum = 0;
    let samples = 0;
    for (let i = 0; i < data.length; i += stride) {
      sum += (data[i] + data[i + 1] + data[i + 2]) / 3;
      samples++;
    }
    return samples ? Math.round(sum / samples) : -1;
  } catch {
    return -1;
  }
}
