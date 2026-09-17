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

// "Chat Bubbles Over Avatar" setting -- an alternative to #subtitle's single
// evolving line: both sides of the conversation as bubbles stacked over the
// avatar, so a phone doesn't have to open the (much wider) history panel
// just to see what's being said. Bubbles are otherwise permanent (no
// timer) -- only pushed out once the stack itself grows past "her
// bustline", see _trimOverlayStack. MAX_HEIGHT_PX must roughly match
// style.css's own sense of that cutoff (there's no single CSS value to
// read back from, so this is the source of truth); MAX_BUBBLES is just a
// backstop for a run of very short messages that might never trip the
// height check.
const OVERLAY_STACK_MAX_HEIGHT_PX = 260;
const MAX_OVERLAY_BUBBLES = 6;
const OVERLAY_CHAT_STORAGE_KEY = "glitch_overlay_chat_active";

// How long a reply can be outstanding before the connection light goes
// yellow ("Brain's slow", not "Brain's unreachable"). Set well above a
// normal reply, not just above the fastest one seen -- this session's own
// local LLM has ranged from ~1.5s to 90s+ for the exact same kind of
// message, so anything much tighter would spend most of a conversation
// sitting in "slow" for completely ordinary latency.
const SLOW_REPLY_THRESHOLD_MS = 15000;

// This VRM's actual mood expression presets (confirmed via
// vrm.expressionManager.expressionMap) -- "neutral" isn't in this list on
// purpose, it means "fade all of these to 0" rather than being a settable
// expression of its own. Treated as mutually exclusive: setting one fades
// the rest out, matching how a face can only show one mood at a time.
const MOODS = ["happy", "sad", "surprised", "angry", "relaxed"];
const EXPRESSION_FADE_SEC = 0.3;

// Profiles are stored Brain-side now (brain/profiles/*.md, see
// protocol.md's save_profile/load_profile/profiles) rather than in
// localStorage -- Brain persists the active one across its own restarts
// via user.md, so the Renderer doesn't need to resend anything on
// reconnect the way an earlier, single-profile version of this feature
// did.

// Reserved names for "no profile"/"no custom soul" -- mirrors
// brain/profiles.py's DEFAULT_PROFILE_NAME and brain/souls.py's
// DEFAULT_SOUL_NAME exactly. Never a real saved file; always prepended to
// the dropdown client-side (same pattern the avatar picker already uses
// for "Glitch") so there's always something selected, and can't be
// deleted or edited.
const DEFAULT_PROFILE_NAME = "Default";
const DEFAULT_SOUL_NAME = "Default";
// Same reserved-name pattern, for speech engines (brain/tts_engines.py's
// NONE_NAME) -- means nothing configured/selected yet (no audio at all),
// not a saved engine with its own endpoint/api_key; always prepended to
// the dropdown, never sent by Brain in the `tts_engines` list.
const NONE_TTS_ENGINE_NAME = "None";
// The voice picker's own reserved entry -- means "clear the saved
// `voice` field", i.e. RemoteTTS.DEFAULT_VOICE. Purely a Renderer-side
// label; Brain never sees this string, see _setTtsVoice (sends "" for
// it instead, brain/main.py's _handle_set_tts_voice's own documented
// meaning for an empty voice).
const TTS_VOICE_DEFAULT_NAME = "Default";
// Same reserved-name pattern, for LLM engines (brain/llm_engines.py's
// NONE_NAME) -- means "config.yaml's own now-optional brain.llm block, or
// genuinely no LLM if that's empty too", not a saved engine with its own
// endpoint/model/api_key.
const NONE_LLM_ENGINE_NAME = "None";

// Same reserved-name pattern, for harnesses (brain/harness.py's
// NONE_NAME) -- means "not plugged into any harness, using her own
// profile/soul/LLM engine", not a saved harness with its own endpoint/
// model/api_key. Always prepended client-side to the dropdown (like
// DEFAULT_PROFILE_NAME etc. above) -- harness_state's own `available`
// list (see _renderHarnessDropdown) never includes it.
const NONE_HARNESS_NAME = "None";

// How often to send a debug_ping while the Debugging toggle is on --
// only runs then, not all the time, since it's purely a diagnostic RTT
// probe (see protocol.md's debug_ping) nobody needs otherwise.
const DEBUG_PING_INTERVAL_MS = 5000;
// Caps the in-memory debug log so a long debugging session can't grow
// this unboundedly -- FIFO once full, matching the same defensive-cap
// pattern Brain's own code uses (MAX_HISTORY_MESSAGES etc.). 5000 entries
// is far more than a normal debugging session needs before download.
const MAX_DEBUG_LOG_ENTRIES = 5000;

// Safety net for the Restart Brain button's gray-out: normally cleared
// the moment the socket reopens after Brain's process comes back (a few
// seconds), but if a restart fails outright (e.g. Brain crashes on
// startup instead of coming back up), the reconnect loop would otherwise
// leave the whole settings panel grayed out and unusable forever with no
// indication why. This just un-grays it after a generous timeout either
// way -- it doesn't mean the restart succeeded, just that the UI stops
// pretending to know.
const RESTART_TIMEOUT_MS = 30000;

// Longest edge a camera/desktop snapshot is scaled down to before encoding
// -- vision-capable local models don't benefit from more than this, and it
// keeps the base64 payload (and the request sent to the LLM) reasonably
// sized. Aspect ratio is preserved; a frame already smaller than this is
// left alone.
const MAX_VISION_DIMENSION = 1024;
const VISION_JPEG_QUALITY = 0.7;

// A text file attached via the 📎 button gets its content embedded
// straight into the message text (see _sendTextFile) -- capped so one
// huge file can't blow up a single turn's context/latency the way an
// unbounded conversation history already got capped for (MAX_HISTORY_MESSAGES).
const MAX_UPLOADED_TEXT_CHARS = 20000;
const TEXT_FILE_EXTENSIONS = [".txt", ".md", ".csv", ".json", ".log", ".yaml", ".yml"];

// How long #chat-bar and #side-actions stay visible with no activity
// before fading out -- see _resetIdleTimer.
const CHAT_BAR_IDLE_MS = 10000;

// Mic Always-On's simple volume-based VAD (voice activity detection) --
// not a real speech detector, just an RMS-over-threshold heuristic against
// getByteTimeDomainData (0-255, centered at 128). Good enough to tell
// "talking" from "quiet room" for a companion app; a noisy environment or
// a very quiet mic may need this threshold retuned. Polled on an interval
// rather than driven by AudioWorklet/ScriptProcessor -- simpler, and
// 100ms resolution is plenty for deciding when someone stopped talking.
const MIC_VAD_POLL_MS = 100;
const MIC_VAD_SPEECH_RMS_THRESHOLD = 0.025;
const MIC_VAD_SILENCE_MS = 1200;

const MEMORY_TOAST_DURATION_MS = 4000;

// Persists the chat history panel across a page reload -- see
// _addHistoryEntry's comment for why this exists at all.
const CHAT_HISTORY_STORAGE_KEY = "glitch_chat_history";
const MAX_STORED_HISTORY_ENTRIES = 100;

export class BrainClient {
  constructor({
    url,
    authToken,
    vrm,
    statusEl,
    subtitleEl,
    bubbleOverlayEl,
    overlayChatToggleInputEl,
    memoryToastEl,
    inputEl,
    chatBarEl,
    sideActionsEl,
    sendButtonEl,
    overlayResendButtonEl,
    fileUploadButtonEl,
    fileUploadInputEl,
    cameraVisionButtonEl,
    desktopVisionButtonEl,
    micButtonEl,
    micLabelEl,
    voiceToggleInputEl,
    cameraEnabledToggleInputEl,
    desktopCaptureEnabledToggleInputEl,
    micEnabledToggleInputEl,
    micAlwaysOnToggleInputEl,
    memoryToggleInputEl,
    memoryProviderSelectEl,
    hindsightConfigFieldsEl,
    hindsightApiUrlEl,
    hindsightApiKeyEl,
    hindsightBankIdEl,
    saveHindsightConfigButtonEl,
    downloadMemoryButtonEl,
    clearMemoryButtonEl,
    openSoulUserEditorButtonEl,
    soulUserEditorModalBackdropEl,
    soulUserEditorSoulEl,
    soulUserEditorUserEl,
    soulUserEditorSaveButtonEl,
    soulUserEditorCancelButtonEl,
    historyListEl,
    resendLastButtonEl,
    clearHistoryButtonEl,
    connectionLightEl,
    harnessLightEl,
    ttsLightEl,
    roleplayToggleInputEl,
    roleplayBadgeEl,
    profileOptionsEl,
    soulSectionEl,
    profileSelectEl,
    editProfileButtonEl,
    newProfileButtonEl,
    deleteProfileButtonEl,
    profileModalBackdropEl,
    profileModalTitleEl,
    profileNameEl,
    profileContentEl,
    profileSaveButtonEl,
    profileCancelButtonEl,
    soulSelectEl,
    editSoulButtonEl,
    newSoulButtonEl,
    deleteSoulButtonEl,
    soulModalBackdropEl,
    soulModalTitleEl,
    soulNameEl,
    soulDescriptionEl,
    soulExamplesEl,
    soulSaveButtonEl,
    soulCancelButtonEl,
    avatarSelectEl,
    importAvatarButtonEl,
    avatarFileInputEl,
    importAvatarPngButtonEl,
    avatarPngFileInputEl,
    ttsEngineSelectEl,
    editTtsEngineButtonEl,
    newTtsEngineButtonEl,
    deleteTtsEngineButtonEl,
    ttsEngineModalBackdropEl,
    ttsEngineModalTitleEl,
    ttsEngineNameEl,
    ttsEngineEndpointEl,
    ttsEngineApiKeyEl,
    ttsEngineVoiceEl,
    ttsEngineModelEl,
    ttsEngineVoicesDirEl,
    ttsEngineSaveButtonEl,
    ttsEngineCancelButtonEl,
    ttsVoiceSelectEl,
    createTtsVoiceButtonEl,
    kokoroBlendModalBackdropEl,
    kokoroBlendNameEl,
    kokoroBlendSpecEl,
    kokoroBlendCancelButtonEl,
    kokoroBlendCreateButtonEl,
    llmEngineSelectEl,
    editLlmEngineButtonEl,
    newLlmEngineButtonEl,
    deleteLlmEngineButtonEl,
    llmEngineModalBackdropEl,
    llmEngineModalTitleEl,
    llmEngineNameEl,
    llmEngineProviderEl,
    llmEngineEndpointEl,
    llmEngineModelEl,
    llmEngineModelOptionsEl,
    fetchLlmModelsButtonEl,
    llmEngineApiKeyEl,
    llmEngineThinkRowEl,
    llmEngineThinkHintEl,
    llmEngineThinkToggleEl,
    llmEngineSaveButtonEl,
    llmEngineCancelButtonEl,
    harnessToggleInputEl,
    harnessSelectEl,
    editHarnessButtonEl,
    newHarnessButtonEl,
    deleteHarnessButtonEl,
    harnessLockableEl,
    harnessConfirmModalBackdropEl,
    harnessConfirmModalTextEl,
    harnessConfirmCancelButtonEl,
    harnessConfirmConnectButtonEl,
    harnessModalBackdropEl,
    harnessModalTitleEl,
    harnessNameEl,
    harnessEndpointEl,
    harnessModelEl,
    harnessApiKeyEl,
    harnessModalCancelButtonEl,
    harnessModalSaveButtonEl,
    roleplayConfirmModalBackdropEl,
    roleplayConfirmThinkToggleEl,
    roleplayConfirmCancelButtonEl,
    roleplayConfirmEnableButtonEl,
    harnessSwitchKeyEl,
    saveHarnessKeyButtonEl,
    debugToggleInputEl,
    downloadDebugLogButtonEl,
    restartBrainButtonEl,
    openNotesButtonEl,
    notesModalBackdropEl,
    notesTextareaEl,
    notesSaveButtonEl,
    notesCancelButtonEl,
    settingsContentEl,
    onAvatarSwap,
  }) {
    this.url = url;
    this.authToken = authToken;
    this.vrm = vrm;
    this.statusEl = statusEl;
    this.subtitleEl = subtitleEl;
    this.bubbleOverlayEl = bubbleOverlayEl;
    this.overlayChatToggleInputEl = overlayChatToggleInputEl;
    // Purely a per-browser display preference -- nothing Brain-side knows
    // or cares about this, same reasoning as the camera/desktop/mic button
    // prefs. Defaults OFF (unlike those) since it changes how her replies
    // are presented, not just which buttons show up.
    this._overlayChatActive = this._readOverlayChatPref();
    this._applyOverlayChatActive(this._overlayChatActive); // paints the toggle + body class before any message arrives
    this.overlayChatToggleInputEl?.addEventListener("change", () => {
      this._overlayChatActive = this.overlayChatToggleInputEl.checked;
      this._writeOverlayChatPref(this._overlayChatActive);
      this._applyOverlayChatActive(this._overlayChatActive);
    });
    this.memoryToastEl = memoryToastEl;
    this.inputEl = inputEl;
    this.chatBarEl = chatBarEl;
    this.sideActionsEl = sideActionsEl;
    this.sendButtonEl = sendButtonEl;
    this.overlayResendButtonEl = overlayResendButtonEl;
    this.overlayResendButtonEl?.addEventListener("click", () => this._resendLastUserMessage());
    this.fileUploadButtonEl = fileUploadButtonEl;
    this.fileUploadInputEl = fileUploadInputEl;
    this.cameraVisionButtonEl = cameraVisionButtonEl;
    this.desktopVisionButtonEl = desktopVisionButtonEl;
    this.micButtonEl = micButtonEl;
    this.voiceToggleInputEl = voiceToggleInputEl;
    this.cameraEnabledToggleInputEl = cameraEnabledToggleInputEl;
    this.desktopCaptureEnabledToggleInputEl = desktopCaptureEnabledToggleInputEl;
    this.micEnabledToggleInputEl = micEnabledToggleInputEl;
    this.micAlwaysOnToggleInputEl = micAlwaysOnToggleInputEl;
    this.memoryToggleInputEl = memoryToggleInputEl;
    this.memoryProviderSelectEl = memoryProviderSelectEl;
    this.hindsightConfigFieldsEl = hindsightConfigFieldsEl;
    this.hindsightApiUrlEl = hindsightApiUrlEl;
    this.hindsightApiKeyEl = hindsightApiKeyEl;
    this.hindsightBankIdEl = hindsightBankIdEl;
    this.saveHindsightConfigButtonEl = saveHindsightConfigButtonEl;
    this.memoryProviderSelectEl?.addEventListener("change", () => {
      const provider = this.memoryProviderSelectEl.value;
      this._renderMemoryProviderVisibility(provider);
      this._send({ type: "set_memory_provider", provider });
    });
    this.saveHindsightConfigButtonEl?.addEventListener("click", () => this._saveHindsightConfig());
    this.downloadMemoryButtonEl = downloadMemoryButtonEl;
    this.clearMemoryButtonEl = clearMemoryButtonEl;
    this._pendingMemoryDownload = false;
    this.openSoulUserEditorButtonEl = openSoulUserEditorButtonEl;
    this.soulUserEditorModalBackdropEl = soulUserEditorModalBackdropEl;
    this.soulUserEditorSoulEl = soulUserEditorSoulEl;
    this.soulUserEditorUserEl = soulUserEditorUserEl;
    this.soulUserEditorSaveButtonEl = soulUserEditorSaveButtonEl;
    this.soulUserEditorCancelButtonEl = soulUserEditorCancelButtonEl;
    this._pendingSoulUserEditorOpen = false;
    this._micAlwaysOn = false;
    this._micVadTimer = null;
    this._micVadState = "silence"; // "silence" | "speaking" -- see _pollMicVad
    this._micSilenceStartedAt = null;
    this._micAlwaysOnStream = null;
    this._micAudioCtx = null;
    this._micAnalyser = null;
    this.micLabelEl = micLabelEl;
    this.historyListEl = historyListEl;
    this.resendLastButtonEl = resendLastButtonEl;
    this.clearHistoryButtonEl = clearHistoryButtonEl;
    this._historyEntries = [];
    this._loadStoredHistory();
    this.resendLastButtonEl?.addEventListener("click", () => this._resendLastUserMessage());
    this.clearHistoryButtonEl?.addEventListener("click", () => this._clearHistory());
    this.connectionLightEl = connectionLightEl;
    this._connectionState = "red"; // haven't connected yet
    this._slowReplyTimer = null;
    this.harnessToggleInputEl = harnessToggleInputEl;
    this.harnessSelectEl = harnessSelectEl;
    this.editHarnessButtonEl = editHarnessButtonEl;
    this.newHarnessButtonEl = newHarnessButtonEl;
    this.deleteHarnessButtonEl = deleteHarnessButtonEl;
    this.harnessLockableEl = harnessLockableEl;
    this.harnessConfirmModalBackdropEl = harnessConfirmModalBackdropEl;
    this.harnessConfirmModalTextEl = harnessConfirmModalTextEl;
    this.harnessConfirmCancelButtonEl = harnessConfirmCancelButtonEl;
    this.harnessConfirmConnectButtonEl = harnessConfirmConnectButtonEl;
    this.harnessModalBackdropEl = harnessModalBackdropEl;
    this.harnessModalTitleEl = harnessModalTitleEl;
    this.harnessNameEl = harnessNameEl;
    this.harnessEndpointEl = harnessEndpointEl;
    this.harnessModelEl = harnessModelEl;
    this.harnessApiKeyEl = harnessApiKeyEl;
    this.harnessModalCancelButtonEl = harnessModalCancelButtonEl;
    this.harnessModalSaveButtonEl = harnessModalSaveButtonEl;
    this._pendingEditHarnessName = null;
    this.roleplayConfirmModalBackdropEl = roleplayConfirmModalBackdropEl;
    this.roleplayConfirmThinkToggleEl = roleplayConfirmThinkToggleEl;
    this.roleplayConfirmCancelButtonEl = roleplayConfirmCancelButtonEl;
    this.roleplayConfirmEnableButtonEl = roleplayConfirmEnableButtonEl;
    this.harnessSwitchKeyEl = harnessSwitchKeyEl;
    this.saveHarnessKeyButtonEl = saveHarnessKeyButtonEl;
    this._harnessActive = false;
    this._activeHarnessName = "";
    // What the dropdown shows as picked while inactive (learned from
    // harness_state's own `selected`, see protocol.py) -- distinct from
    // _activeHarnessName so turning the harness off doesn't also make
    // the dropdown forget which one was chosen.
    this._selectedHarnessName = "";
    // No reasonable guess to paint before harness_state arrives -- unlike
    // profiles/souls/LLM/TTS engines, there's no fixed known list anymore
    // (harnesses are fully open-ended now, see harness.py), so this just
    // starts empty and _renderHarnessDropdown always prepends None itself.
    this._harnessAvailable = [];
    this.harnessLightEl = harnessLightEl;
    this.ttsLightEl = ttsLightEl;
    // Same purpose as _harnessReachableByName, for saved speech engines.
    this._ttsReachableByName = {};
    // name -> last-known reachability (harness_health's `reachable`) --
    // separate from _harnessActive, which means "plugged in right now",
    // not "could be". Undefined until the first harness_health for that
    // name arrives; see _renderHarnessLight.
    this._harnessReachableByName = {};
    // The current harness-switch secret, learned from harness_state (same
    // "reasonable guess, corrected on confirm" pattern as _connectionState
    // painting red before connect() resolves anything -- "" until then).
    // Sent automatically with every set_harness_active(active: true), so
    // the user only ever types it once into the settings field
    // (_saveHarnessKey) rather than on every connection attempt -- Brain
    // still validates it fresh on every single request regardless (see
    // main.py's _handle_set_harness_active).
    this._harnessSwitchKey = "";
    // Which harness the confirm-and-connect modal is currently open for,
    // so its Connect button knows what to actually send -- null while
    // the modal is closed.
    this._pendingHarnessConnectName = null;
    this.debugToggleInputEl = debugToggleInputEl;
    this.downloadDebugLogButtonEl = downloadDebugLogButtonEl;
    // Defaulted ON (not the usual off) while this app is still being
    // shaken out pre-share -- flip back to false once that's done. Doesn't
    // persist across reloads on its own either way (see the toggle's own
    // "never reopen the mic silently" reasoning elsewhere), it's just this
    // starting value that's temporarily flipped.
    this._debugActive = true;
    // {ts: Date.now(), category, message, ms?}[] -- category/message/ms
    // only, never conversation content (see _logDebug). Capped FIFO via
    // MAX_DEBUG_LOG_ENTRIES. Survives a toggle-off (so turning it off
    // mid-session doesn't lose what was already captured), cleared only
    // by a full page reload.
    this._debugLog = [];
    this._debugPingTimer = null;
    // Set on every user_text/user_audio send, read back when speak_text
    // arrives to log the whole round trip's wall-clock time from this
    // client's own perspective -- a cross-check against Brain's own
    // separately-logged llm/tts timings (the two won't match exactly:
    // this also includes WS transit time both ways).
    this._pendingReplySentAt = null;
    this.restartBrainButtonEl = restartBrainButtonEl;
    this.openNotesButtonEl = openNotesButtonEl;
    this.notesModalBackdropEl = notesModalBackdropEl;
    this.notesTextareaEl = notesTextareaEl;
    this.notesSaveButtonEl = notesSaveButtonEl;
    this.notesCancelButtonEl = notesCancelButtonEl;
    // Set the moment the Notes button asks Brain for the current content,
    // cleared once the reply actually opens the modal -- same "only act on
    // the reply I actually asked for" pattern as _pendingSoulUserEditorOpen.
    this._pendingNotesOpen = false;
    this.settingsContentEl = settingsContentEl;
    // Set while a requested restart is in flight -- purely a UI-timeout
    // safety net (see _restartBrain), not load-bearing state anything
    // else reads.
    this._restartTimeoutTimer = null;
    this.roleplayToggleInputEl = roleplayToggleInputEl;
    this.roleplayBadgeEl = roleplayBadgeEl;
    this.profileOptionsEl = profileOptionsEl;
    this.soulSectionEl = soulSectionEl;
    // Defaults to on, matching Brain's own default (profiles.py's
    // read_roleplay_active) -- corrected the moment the real `roleplay_state`
    // message arrives, same pattern as _connectionState painting red before
    // connect() confirms anything.
    this._roleplayActive = true;
    this.profileSelectEl = profileSelectEl;
    this.editProfileButtonEl = editProfileButtonEl;
    this.newProfileButtonEl = newProfileButtonEl;
    this.deleteProfileButtonEl = deleteProfileButtonEl;
    this.profileModalBackdropEl = profileModalBackdropEl;
    this.profileModalTitleEl = profileModalTitleEl;
    this.profileNameEl = profileNameEl;
    this.profileContentEl = profileContentEl;
    this.profileSaveButtonEl = profileSaveButtonEl;
    this.profileCancelButtonEl = profileCancelButtonEl;
    this._profileNames = [];
    this._activeProfileName = DEFAULT_PROFILE_NAME;
    this._pendingEditName = null;
    this.soulSelectEl = soulSelectEl;
    this.editSoulButtonEl = editSoulButtonEl;
    this.newSoulButtonEl = newSoulButtonEl;
    this.deleteSoulButtonEl = deleteSoulButtonEl;
    this.soulModalBackdropEl = soulModalBackdropEl;
    this.soulModalTitleEl = soulModalTitleEl;
    this.soulNameEl = soulNameEl;
    this.soulDescriptionEl = soulDescriptionEl;
    this.soulExamplesEl = soulExamplesEl;
    this.soulSaveButtonEl = soulSaveButtonEl;
    this.soulCancelButtonEl = soulCancelButtonEl;
    this._soulNames = [];
    this._activeSoulName = DEFAULT_SOUL_NAME;
    this._pendingEditSoulName = null;
    this.avatarSelectEl = avatarSelectEl;
    this.importAvatarButtonEl = importAvatarButtonEl;
    this.avatarFileInputEl = avatarFileInputEl;
    this.importAvatarPngButtonEl = importAvatarPngButtonEl;
    this.avatarPngFileInputEl = avatarPngFileInputEl;
    this.ttsEngineSelectEl = ttsEngineSelectEl;
    this.editTtsEngineButtonEl = editTtsEngineButtonEl;
    this.newTtsEngineButtonEl = newTtsEngineButtonEl;
    this.deleteTtsEngineButtonEl = deleteTtsEngineButtonEl;
    this.ttsEngineModalBackdropEl = ttsEngineModalBackdropEl;
    this.ttsEngineModalTitleEl = ttsEngineModalTitleEl;
    this.ttsEngineNameEl = ttsEngineNameEl;
    this.ttsEngineEndpointEl = ttsEngineEndpointEl;
    this.ttsEngineApiKeyEl = ttsEngineApiKeyEl;
    this.ttsEngineVoiceEl = ttsEngineVoiceEl;
    this.ttsEngineModelEl = ttsEngineModelEl;
    this.ttsEngineVoicesDirEl = ttsEngineVoicesDirEl;
    this.ttsEngineSaveButtonEl = ttsEngineSaveButtonEl;
    this.ttsEngineCancelButtonEl = ttsEngineCancelButtonEl;
    this.ttsVoiceSelectEl = ttsVoiceSelectEl;
    this.createTtsVoiceButtonEl = createTtsVoiceButtonEl;
    this.kokoroBlendModalBackdropEl = kokoroBlendModalBackdropEl;
    this.kokoroBlendNameEl = kokoroBlendNameEl;
    this.kokoroBlendSpecEl = kokoroBlendSpecEl;
    this.kokoroBlendCancelButtonEl = kokoroBlendCancelButtonEl;
    this.kokoroBlendCreateButtonEl = kokoroBlendCreateButtonEl;
    // Learned from tts_voices (see _handleMessage) -- which saved engine
    // the current voice info is actually for, so a reply that arrives
    // after the user has already switched engines again doesn't paint
    // the wrong one's voices into the picker.
    this._ttsVoicesForEngine = "";
    this._ttsEngineNames = [];
    this._activeTtsEngineName = NONE_TTS_ENGINE_NAME;
    this._pendingEditTtsEngineName = null;
    this.llmEngineSelectEl = llmEngineSelectEl;
    this.editLlmEngineButtonEl = editLlmEngineButtonEl;
    this.newLlmEngineButtonEl = newLlmEngineButtonEl;
    this.deleteLlmEngineButtonEl = deleteLlmEngineButtonEl;
    this.llmEngineModalBackdropEl = llmEngineModalBackdropEl;
    this.llmEngineModalTitleEl = llmEngineModalTitleEl;
    this.llmEngineNameEl = llmEngineNameEl;
    this.llmEngineProviderEl = llmEngineProviderEl;
    this.llmEngineEndpointEl = llmEngineEndpointEl;
    this.llmEngineModelEl = llmEngineModelEl;
    this.llmEngineModelOptionsEl = llmEngineModelOptionsEl;
    this.fetchLlmModelsButtonEl = fetchLlmModelsButtonEl;
    this.llmEngineApiKeyEl = llmEngineApiKeyEl;
    this.llmEngineThinkRowEl = llmEngineThinkRowEl;
    this.llmEngineThinkHintEl = llmEngineThinkHintEl;
    this.llmEngineThinkToggleEl = llmEngineThinkToggleEl;
    this.llmEngineSaveButtonEl = llmEngineSaveButtonEl;
    this.llmEngineCancelButtonEl = llmEngineCancelButtonEl;
    this.llmEngineProviderEl?.addEventListener("change", () => {
      this._updateLlmEngineThinkVisibility();
      // Ollama's native API is its own base URL, no /v1 -- worth swapping
      // the placeholder so the shape difference is obvious right when it
      // matters, not just documented in llm_engine_think_hint text.
      if (this.llmEngineEndpointEl) {
        this.llmEngineEndpointEl.placeholder =
          this.llmEngineProviderEl.value === "ollama" ? "e.g. http://localhost:11434" : "e.g. http://localhost:1234/v1";
      }
    });
    this._llmEngineNames = [];
    this._activeLlmEngineName = NONE_LLM_ENGINE_NAME;
    this._pendingEditLlmEngineName = null;
    // Tracks which endpoint the last get_llm_models request was for, so a
    // reply for an endpoint the user has since edited away from (still
    // typing in the same open modal) gets discarded instead of
    // overwriting the model list with stale/mismatched options.
    this._pendingModelFetchEndpoint = null;
    // Called with a VRM source (a URL string for the shipped default, or
    // an ArrayBuffer of raw .vrm bytes) whenever the active avatar should
    // change -- main.js owns the actual THREE.Scene/IdleController swap,
    // BrainClient only decides *when* one should happen.
    this._onAvatarSwap = onAvatarSwap;
    // Each entry is {name, kind} (kind: "vrm" | "png") -- see avatars.py's
    // list_avatars docstring. Kind isn't needed to *select* one (load_avatar
    // only sends the name, Brain looks up its own saved kind and echoes it
    // back in avatar_data), just to build the dropdown's option list.
    this._customAvatars = [];
    this._activeAvatarName = "Glitch"; // matches what main.js boots with by default
    this.socket = null;
    this._subtitleTimer = null;
    this._subtitleStreamTimer = null;
    this._pendingSpeakText = "";
    // {bubbleEl, entry} for the most recent voice message's placeholder,
    // set by _sendRecording and consumed by "user_transcript" -- null
    // whenever there's no outstanding voice message waiting on its
    // transcript (including right after it's been swapped in).
    this._pendingAudioEntry = null;
    // True from the moment a user_text/user_audio is sent until Brain's
    // reply (speak_text) comes back -- disables the send button for that
    // stretch so a second click can't queue up a pile of messages while
    // she's still answering the first one. Cleared on speak_text *or* on
    // disconnect (see the "close" handler below), so a dropped connection
    // mid-reply can't leave the button stuck disabled forever.
    this._awaitingReply = false;

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

    this.fileUploadButtonEl?.addEventListener("click", () => this.fileUploadInputEl?.click());
    this.fileUploadInputEl?.addEventListener("change", () => {
      const file = this.fileUploadInputEl.files?.[0];
      this.fileUploadInputEl.value = ""; // clears the picked file so choosing the exact same one again still fires "change"
      this._handleFileUpload(file);
    });

    this.cameraVisionButtonEl?.addEventListener("click", () => this._captureAndSendVision("camera"));
    this.desktopVisionButtonEl?.addEventListener("click", () => this._captureAndSendVision("desktop"));

    // Voice is a real Brain-side behavior switch (skips TTS synthesis
    // entirely -- see voice_settings.py), so it's persisted Brain-side and
    // sent over the wire, same as roleplay's toggle. Camera/desktop/mic are
    // purely "does this button exist" preferences with nothing for Brain
    // to know or care about, so those three just live in localStorage and
    // toggle the button's `hidden` -- no round trip needed.
    this.voiceToggleInputEl?.addEventListener("change", () => this._send({ type: "set_voice_active", active: this.voiceToggleInputEl.checked }));
    this.cameraEnabledToggleInputEl?.addEventListener("change", () => this._setDeviceButtonEnabled("camera", this.cameraEnabledToggleInputEl.checked));
    this.desktopCaptureEnabledToggleInputEl?.addEventListener("change", () =>
      this._setDeviceButtonEnabled("desktop", this.desktopCaptureEnabledToggleInputEl.checked),
    );
    this.micEnabledToggleInputEl?.addEventListener("change", () => this._setDeviceButtonEnabled("mic", this.micEnabledToggleInputEl.checked));
    this.micAlwaysOnToggleInputEl?.addEventListener("change", () => {
      if (this.micAlwaysOnToggleInputEl.checked) this._startMicAlwaysOn();
      else this._stopMicAlwaysOn();
    });

    this.memoryToggleInputEl?.addEventListener("change", () => this._send({ type: "set_memory_active", active: this.memoryToggleInputEl.checked }));
    this.downloadMemoryButtonEl?.addEventListener("click", () => {
      this._pendingMemoryDownload = true;
      this._send({ type: "get_memory_content" });
    });
    this.clearMemoryButtonEl?.addEventListener("click", () => this._clearMemory());

    this.openSoulUserEditorButtonEl?.addEventListener("click", () => {
      this._pendingSoulUserEditorOpen = true;
      this._send({ type: "get_soul_and_user" });
    });
    this.soulUserEditorCancelButtonEl?.addEventListener("click", () => this._closeSoulUserEditorModal());
    this.soulUserEditorSaveButtonEl?.addEventListener("click", () => this._saveSoulUserEditor());
    this.soulUserEditorModalBackdropEl?.addEventListener("click", (e) => {
      if (e.target === this.soulUserEditorModalBackdropEl) this._closeSoulUserEditorModal();
    });
    this._initDeviceButtonToggles();

    // Auto-hide #chat-bar/#side-actions after CHAT_BAR_IDLE_MS of no
    // activity -- any interaction anywhere on the page (not just those
    // elements themselves) counts, so orbiting the camera or just tapping
    // the screen brings them back too, not only typing. pointerdown
    // covers touch and mouse alike (unified pointer events); keydown
    // covers typing without a fresh pointerdown (e.g. holding a key down).
    document.addEventListener("pointerdown", () => this._resetIdleTimer());
    document.addEventListener("keydown", () => this._resetIdleTimer());
    this._resetIdleTimer(); // arms the initial countdown from page load

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

    this._renderRoleplayToggle(); // paints the switch's initial on/off position before any `roleplay_state` message arrives
    this.roleplayToggleInputEl?.addEventListener("change", () => {
      const turningOn = this.roleplayToggleInputEl.checked;
      // Revert the native checkbox immediately, only re-render for real
      // once the user actually confirms -- turning on means switching LLM
      // engines (see the confirm modal), too big a side effect to apply
      // optimistically just because the checkbox was clicked. Turning off
      // has no such side effect, so it still applies
      // immediately below.
      this.roleplayToggleInputEl.checked = this._roleplayActive;
      if (turningOn) this._openRoleplayConfirmModal();
      else this._setRoleplayActive(false);
    });
    this.roleplayConfirmCancelButtonEl?.addEventListener("click", () => this._closeRoleplayConfirmModal());
    this.roleplayConfirmModalBackdropEl?.addEventListener("click", (e) => {
      if (e.target === this.roleplayConfirmModalBackdropEl) this._closeRoleplayConfirmModal();
    });
    this.roleplayConfirmEnableButtonEl?.addEventListener("click", () => {
      const think = !!this.roleplayConfirmThinkToggleEl?.checked;
      this._closeRoleplayConfirmModal();
      this._setRoleplayActive(true, think);
    });

    this._renderProfileList([], DEFAULT_PROFILE_NAME); // shows just "Default" immediately, before any `profiles` message arrives
    this.profileSelectEl?.addEventListener("change", () => this._loadProfile(this.profileSelectEl.value));
    this.editProfileButtonEl?.addEventListener("click", () => {
      if (this.profileSelectEl?.value) this._editProfile(this.profileSelectEl.value);
    });
    this.newProfileButtonEl?.addEventListener("click", () => this._openProfileModal());
    this.deleteProfileButtonEl?.addEventListener("click", () => this._deleteProfile(this.profileSelectEl?.value));
    this.profileCancelButtonEl?.addEventListener("click", () => this._closeProfileModal());
    this.profileSaveButtonEl?.addEventListener("click", () => this._saveProfile());
    this.profileModalBackdropEl?.addEventListener("click", (e) => {
      if (e.target === this.profileModalBackdropEl) this._closeProfileModal();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !this.profileModalBackdropEl?.hidden) this._closeProfileModal();
      if (e.key === "Escape" && !this.soulModalBackdropEl?.hidden) this._closeSoulModal();
      if (e.key === "Escape" && !this.ttsEngineModalBackdropEl?.hidden) this._closeTtsEngineModal();
      if (e.key === "Escape" && !this.llmEngineModalBackdropEl?.hidden) this._closeLlmEngineModal();
      if (e.key === "Escape" && !this.harnessConfirmModalBackdropEl?.hidden) this._closeHarnessConfirmModal();
      if (e.key === "Escape" && !this.harnessModalBackdropEl?.hidden) this._closeHarnessModal();
      if (e.key === "Escape" && !this.roleplayConfirmModalBackdropEl?.hidden) this._closeRoleplayConfirmModal();
      if (e.key === "Escape" && !this.soulUserEditorModalBackdropEl?.hidden) this._closeSoulUserEditorModal();
      if (e.key === "Escape" && !this.notesModalBackdropEl?.hidden) this._closeNotesModal();
    });

    this._renderSoulList([], DEFAULT_SOUL_NAME); // shows just "Default" immediately, before any `souls` message arrives
    this.soulSelectEl?.addEventListener("change", () => this._loadSoul(this.soulSelectEl.value));
    this.editSoulButtonEl?.addEventListener("click", () => {
      if (this.soulSelectEl?.value) this._editSoul(this.soulSelectEl.value);
    });
    this.newSoulButtonEl?.addEventListener("click", () => this._openSoulModal());
    this.deleteSoulButtonEl?.addEventListener("click", () => this._deleteSoul(this.soulSelectEl?.value));
    this.soulCancelButtonEl?.addEventListener("click", () => this._closeSoulModal());
    this.soulSaveButtonEl?.addEventListener("click", () => this._saveSoul());
    this.soulModalBackdropEl?.addEventListener("click", (e) => {
      if (e.target === this.soulModalBackdropEl) this._closeSoulModal();
    });

    this._renderAvatarList([]); // shows the always-available "Glitch" option immediately, before any `avatars` message arrives
    this.avatarSelectEl?.addEventListener("change", () => this._loadAvatarByName(this.avatarSelectEl.value));
    this.importAvatarButtonEl?.addEventListener("click", () => this.avatarFileInputEl?.click());
    this.avatarFileInputEl?.addEventListener("change", () => {
      const file = this.avatarFileInputEl.files?.[0];
      if (file) this._importAvatarFile(file);
      this.avatarFileInputEl.value = ""; // otherwise re-picking the same file wouldn't fire "change" again
    });
    this.importAvatarPngButtonEl?.addEventListener("click", () => this.avatarPngFileInputEl?.click());
    this.avatarPngFileInputEl?.addEventListener("change", () => {
      const file = this.avatarPngFileInputEl.files?.[0];
      if (file) this._importAvatarPngFile(file);
      this.avatarPngFileInputEl.value = ""; // otherwise re-picking the same file wouldn't fire "change" again
    });

    this._renderTtsEngineList([], NONE_TTS_ENGINE_NAME); // shows just "None" immediately, before any `tts_engines` message arrives
    this.ttsEngineSelectEl?.addEventListener("change", () => {
      this._loadTtsEngine(this.ttsEngineSelectEl.value);
      this._requestTtsVoices(this.ttsEngineSelectEl.value);
    });
    this.editTtsEngineButtonEl?.addEventListener("click", () => {
      if (this.ttsEngineSelectEl?.value) this._editTtsEngine(this.ttsEngineSelectEl.value);
    });
    this.newTtsEngineButtonEl?.addEventListener("click", () => this._openTtsEngineModal());
    this.deleteTtsEngineButtonEl?.addEventListener("click", () => this._deleteTtsEngine(this.ttsEngineSelectEl?.value));
    this.ttsEngineCancelButtonEl?.addEventListener("click", () => this._closeTtsEngineModal());
    this.ttsEngineSaveButtonEl?.addEventListener("click", () => this._saveTtsEngine());
    this.ttsEngineModalBackdropEl?.addEventListener("click", (e) => {
      if (e.target === this.ttsEngineModalBackdropEl) this._closeTtsEngineModal();
    });
    this.ttsVoiceSelectEl?.addEventListener("change", () => this._setTtsVoice(this.ttsVoiceSelectEl.value));
    this.createTtsVoiceButtonEl?.addEventListener("click", () => this._openKokoroBlendModal());
    this.kokoroBlendCancelButtonEl?.addEventListener("click", () => this._closeKokoroBlendModal());
    this.kokoroBlendCreateButtonEl?.addEventListener("click", () => this._createKokoroBlendVoice());
    this.kokoroBlendModalBackdropEl?.addEventListener("click", (e) => {
      if (e.target === this.kokoroBlendModalBackdropEl) this._closeKokoroBlendModal();
    });

    this._renderLlmEngineList([], NONE_LLM_ENGINE_NAME); // shows just "None" immediately, before any `llm_engines` message arrives
    this.llmEngineSelectEl?.addEventListener("change", () => this._loadLlmEngine(this.llmEngineSelectEl.value));
    this.editLlmEngineButtonEl?.addEventListener("click", () => {
      if (this.llmEngineSelectEl?.value) this._editLlmEngine(this.llmEngineSelectEl.value);
    });
    this.newLlmEngineButtonEl?.addEventListener("click", () => this._openLlmEngineModal());
    this.deleteLlmEngineButtonEl?.addEventListener("click", () => this._deleteLlmEngine(this.llmEngineSelectEl?.value));
    this.llmEngineCancelButtonEl?.addEventListener("click", () => this._closeLlmEngineModal());
    this.llmEngineSaveButtonEl?.addEventListener("click", () => this._saveLlmEngine());
    this.llmEngineModalBackdropEl?.addEventListener("click", (e) => {
      if (e.target === this.llmEngineModalBackdropEl) this._closeLlmEngineModal();
    });
    this.fetchLlmModelsButtonEl?.addEventListener("click", () => this._fetchLlmModels());
    // Auto-fetch on blur too, not just the explicit button -- typing an
    // endpoint and tabbing straight to Model should already have real
    // choices waiting, not require noticing there's a separate button.
    this.llmEngineEndpointEl?.addEventListener("blur", () => this._fetchLlmModels());

    this._renderHarnessDropdown(); // shows just "None" immediately, before any `harness_state` message arrives
    // Two separate controls again: the dropdown just picks *which* saved
    // harness is selected (no side effect -- browsing to a different one
    // to Edit/Delete it doesn't attempt anything), the toggle is the only
    // thing that actually connects/disconnects. Toggling on with "None"
    // selected doesn't make sense, so the toggle stays disabled for that
    // case (see _renderHarnessState/this change listener both keeping it
    // in sync with the dropdown's current value).
    this.harnessSelectEl?.addEventListener("change", () => {
      this._updateHarnessEditControls();
      if (this.harnessToggleInputEl) this.harnessToggleInputEl.disabled = this.harnessSelectEl.value === NONE_HARNESS_NAME;
      // Not active yet (just picked, not toggled on) -- _renderHarnessLight
      // already knows to fall back to whatever's selected in this dropdown
      // when nothing's active, it just needs telling the selection changed.
      this._renderHarnessLight();
      // Tells Brain to remember this pick (harness.py's
      // selected_harness_name) even though nothing's connecting -- without
      // this, picking None here never reaches Brain at all (no other
      // message fires just from browsing the dropdown), so the old
      // connected-or-last-connected harness would keep coming back after
      // every refresh no matter what the dropdown was left showing.
      this._send({ type: "select_harness", name: this.harnessSelectEl.value });
    });
    this.harnessToggleInputEl?.addEventListener("change", () => {
      const turningOn = this.harnessToggleInputEl.checked;
      // The native checkbox already flipped on click -- always revert it
      // to whatever's actually true right now. Turning off is applied
      // immediately below; turning on only takes effect once Brain's own
      // harness_state reply confirms it (see _setHarnessActive, via the
      // confirm modal), since a wrong/missing switch key can refuse the
      // request -- so the toggle has nothing real to show yet.
      this.harnessToggleInputEl.checked = this._harnessActive;
      if (turningOn) {
        const name = this.harnessSelectEl?.value;
        if (name && name !== NONE_HARNESS_NAME) this._openHarnessConfirmModal(name);
      } else {
        this._setHarnessActive(false, "");
      }
    });
    this.editHarnessButtonEl?.addEventListener("click", () => {
      const name = this.harnessSelectEl?.value;
      if (name && name !== NONE_HARNESS_NAME) this._editHarness(name);
    });
    this.newHarnessButtonEl?.addEventListener("click", () => this._openHarnessModal());
    this.deleteHarnessButtonEl?.addEventListener("click", () => this._deleteHarness(this.harnessSelectEl?.value));
    this.harnessModalCancelButtonEl?.addEventListener("click", () => this._closeHarnessModal());
    this.harnessModalBackdropEl?.addEventListener("click", (e) => {
      if (e.target === this.harnessModalBackdropEl) this._closeHarnessModal();
    });
    this.harnessModalSaveButtonEl?.addEventListener("click", () => this._saveHarness());
    this.harnessConfirmCancelButtonEl?.addEventListener("click", () => this._closeHarnessConfirmModal());
    this.harnessConfirmModalBackdropEl?.addEventListener("click", (e) => {
      if (e.target === this.harnessConfirmModalBackdropEl) this._closeHarnessConfirmModal();
    });
    this.harnessConfirmConnectButtonEl?.addEventListener("click", () => {
      const name = this._pendingHarnessConnectName;
      this._closeHarnessConfirmModal();
      if (name) this._setHarnessActive(true, name);
    });
    this.saveHarnessKeyButtonEl?.addEventListener("click", () => this._saveHarnessKey());

    this.debugToggleInputEl?.addEventListener("change", () => this._setDebugActive(this.debugToggleInputEl.checked));
    this.downloadDebugLogButtonEl?.addEventListener("click", () => this._downloadDebugLog());

    this.restartBrainButtonEl?.addEventListener("click", () => this._restartBrain());

    // Notes -- a free-form scratchpad file (brain/notes.md), same
    // request-then-open pattern as the Soul & User editor above: nothing
    // is cached client-side, so opening it always reflects the real
    // current file even if it was edited some other way since.
    this.openNotesButtonEl?.addEventListener("click", () => {
      this._pendingNotesOpen = true;
      this._send({ type: "get_notes" });
    });
    this.notesCancelButtonEl?.addEventListener("click", () => this._closeNotesModal());
    this.notesSaveButtonEl?.addEventListener("click", () => this._saveNotes());
    this.notesModalBackdropEl?.addEventListener("click", (e) => {
      if (e.target === this.notesModalBackdropEl) this._closeNotesModal();
    });

    this._setConnectionState(this._connectionState); // paints the light red immediately, before connect() even runs
  }

  connect() {
    this.socket = new WebSocket(this.url);

    this.socket.addEventListener("open", () => {
      console.log("[brain] connected");
      this._setStatus("");
      this._setConnectionState("green");
      this._logDebug("ws", "connected");
      this._setSettingsRestarting(false); // a fresh connection means Brain (restarted or not) is back up and listening
      // token is only checked Brain-side when brain.auth_token is
      // configured (see main.py's _authenticate) -- sending it
      // unconditionally is harmless when Brain has no token to check
      // against, so there's no need to branch on whether authToken is set.
      this._send({ type: "ready", model: "Glitch.vrm", token: this.authToken });
      // A fresh socket means Brain has no record of this connection's
      // debug preference anymore (it's per-connection state, see
      // main.py's _DEBUG_CONNECTIONS) -- re-declare it so debug_events
      // keep flowing across a reconnect if the toggle was already on.
      if (this._debugActive) this._send({ type: "set_debug_active", active: true });
    });

    this.socket.addEventListener("close", (event) => {
      console.warn("[brain] disconnected, retrying...");
      this._setStatus("Brain: disconnected, retrying...");
      this._setConnectionState("red");
      this._logDebug("ws", `disconnected (code ${event.code}${event.reason ? ", " + event.reason : ""}), retrying...`);
      this._setAwaitingReply(false); // don't leave Send stuck disabled across a dropped connection
      setTimeout(() => this.connect(), RECONNECT_DELAY_MS);
    });

    this.socket.addEventListener("error", (err) => {
      console.error("[brain] socket error:", err);
      this._setConnectionState("red");
      this._logDebug("ws", "socket error");
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
    if (this._awaitingReply) return; // already waiting on a reply -- ignore a stray Enter/click
    const text = this.inputEl?.value.trim();
    if (!text) return;
    this._send({ type: "user_text", text });
    this._addHistoryEntry("user", text);
    this._startSlowReplyTimer();
    this._setAwaitingReply(true);
    this._pendingReplySentAt = Date.now();
    this._logDebug("ws", `sent user_text (${text.length} chars)`); // length only, never the text itself
    this.inputEl.value = "";
  }

  // The retry (↻) button on a user history bubble, and the History
  // panel's Resend Last button, both funnel through here. `entry` is the
  // specific user history entry being retried -- when it's the *most
  // recent* user message (true for Resend Last always, and for the
  // per-bubble button when clicked on the latest exchange), this
  // regenerates in place: her stale reply to it is removed and Brain
  // re-answers the same prompt fresh, rather than just piling a duplicate
  // question on top of the old one (which read as her "carrying on" from
  // a repeated question instead of a clean second attempt -- see
  // _regenerateLast). Retrying an *older* bubble, further back than the
  // latest exchange, falls back to asking again as a plain new message
  // instead: popping a mid-conversation turn out from under everything
  // that came after it would corrupt the history's actual order, so this
  // deliberately doesn't attempt that.
  _retryUserMessage(entry) {
    if (this._awaitingReply || this._connectionState === "red") return;
    if (this._isLastUserEntry(entry)) {
      this._regenerateLast(entry);
      return;
    }
    const text = entry.text;
    this._send({ type: "user_text", text });
    this._addHistoryEntry("user", text);
    this._startSlowReplyTimer();
    this._setAwaitingReply(true);
    this._pendingReplySentAt = Date.now();
    this._logDebug("ws", `sent user_text (${text.length} chars, retry)`);
  }

  // True when `entry` is the most recent user-role entry in history --
  // i.e. nothing she or the user has said since. Used to decide whether
  // a retry can safely regenerate in place (see _retryUserMessage).
  _isLastUserEntry(entry) {
    const index = this._historyEntries.indexOf(entry);
    if (index === -1) return false;
    return !this._historyEntries.slice(index + 1).some((e) => e.role === "user");
  }

  // Removes her stale reply to `entry` (the DOM bubble, the persisted
  // entry, and localStorage) if one exists, then asks Brain to
  // regenerate: pop the matching turn from its own conversation history
  // (llm/client.py's pop_last_exchange) and answer the same prompt fresh.
  // No no_reply case to worry about here -- that leaves no glitch entry
  // to remove in the first place, so this is a no-op past the removal
  // check and just goes straight to asking again.
  _regenerateLast(entry) {
    const index = this._historyEntries.indexOf(entry);
    if (index !== -1 && this._historyEntries[index + 1]?.role === "glitch") {
      this._historyEntries.splice(index + 1, 1);
      this.historyListEl?.lastElementChild?.remove();
      // Overlay bubbles (Chat Bubbles Over Avatar) have no timer of their
      // own -- they stay put until _trimOverlayStack pushes them out, see
      // that method's comment -- so without this the stale reply's bubble
      // just kept floating there and the regenerated reply stacked right
      // on top of it, reading as if she'd repeated/duplicated herself
      // instead of actually trying again. Not tracked per-entry the way
      // the History panel's DOM is (only the in-flight voice-transcript
      // one is, via _pendingAudioEntry), so this relies on the stale
      // reply being the most recently added overlay bubble too -- true
      // here since this is always the *latest* exchange, unless overlay
      // chat was switched on only after that reply already went out
      // without one, which is not worth guarding for.
      if (this._overlayChatActive) this.bubbleOverlayEl?.lastElementChild?.remove();
      try {
        localStorage.setItem(CHAT_HISTORY_STORAGE_KEY, JSON.stringify(this._historyEntries));
      } catch {
        // Nothing to fall back to -- the DOM/in-memory removal above already happened either way.
      }
    }
    const text = entry.text;
    this._send({ type: "regenerate_last", text });
    this._startSlowReplyTimer();
    this._setAwaitingReply(true);
    this._pendingReplySentAt = Date.now();
    this._logDebug("ws", `sent regenerate_last (${text.length} chars)`);
  }

  // The History panel's "Resend Last" button -- a bigger, easier-to-hit
  // target than the per-bubble ↻ for the same recovery action, since that
  // one is deliberately small/subtle and not always practical to land a
  // thumb on. Walks backwards to the most recent user entry rather than
  // tracking a separate "last sent text" field, so it stays correct
  // through a voice message's placeholder-to-transcript swap (same
  // entry.text mutation _retryUserMessage relies on) and always resolves
  // to the same entry _retryUserMessage's own last-entry check would find
  // anyway, so this always regenerates in place, never piles on a
  // duplicate question.
  _resendLastUserMessage() {
    for (let i = this._historyEntries.length - 1; i >= 0; i--) {
      const entry = this._historyEntries[i];
      if (entry.role === "user" && entry.text) {
        this._retryUserMessage(entry);
        return;
      }
    }
  }

  // Disables the send button for the stretch between sending something
  // and Brain's speak_text reply -- see _awaitingReply's own comment.
  _setAwaitingReply(awaiting) {
    this._awaitingReply = awaiting;
    this._updateSendButtonDisabled();
  }

  // Send (and the two vision buttons -- same reply pipeline, same reasons
  // to be unavailable) has two independent reasons to be disabled --
  // already waiting on a reply, or not connected to Brain at all (nothing
  // to send it to) -- so this re-derives the combined result from both
  // flags every time either one changes, instead of the setters fighting
  // over each element's .disabled write.
  _updateSendButtonDisabled() {
    const disabled = this._awaitingReply || this._connectionState === "red";
    if (this.sendButtonEl) this.sendButtonEl.disabled = disabled;
    if (this.overlayResendButtonEl) this.overlayResendButtonEl.disabled = disabled;
    if (this.fileUploadButtonEl) this.fileUploadButtonEl.disabled = disabled;
    if (this.cameraVisionButtonEl) this.cameraVisionButtonEl.disabled = disabled;
    if (this.desktopVisionButtonEl) this.desktopVisionButtonEl.disabled = disabled;
    // Disabling the button also naturally blocks its pointerdown/pointerup
    // handlers (a disabled <button> receives no pointer events), so this
    // alone stops push-to-talk from starting a new recording here -- Mic
    // Always-On's VAD loop is a separate path with its own guard, see
    // _pollMicVad.
    if (this.micButtonEl) this.micButtonEl.disabled = disabled;
  }

  // Purely a per-browser UI preference (does this button even show up) --
  // nothing Brain-side depends on it, so localStorage is enough; wrapped in
  // try/catch since a private window or blocked site data can throw on
  // either read or write, and losing this preference is harmless (defaults
  // back to "enabled" next load) rather than something worth erroring over.
  _readDeviceButtonPref(kind) {
    try {
      return localStorage.getItem(`glitch_${kind}_enabled`) !== "0";
    } catch {
      return true;
    }
  }

  _writeDeviceButtonPref(kind, enabled) {
    try {
      localStorage.setItem(`glitch_${kind}_enabled`, enabled ? "1" : "0");
    } catch {
      // Nothing to fall back to -- the toggle just won't survive a reload.
    }
  }

  _deviceButtonEl(kind) {
    if (kind === "camera") return this.cameraVisionButtonEl;
    if (kind === "desktop") return this.desktopVisionButtonEl;
    return this.micButtonEl;
  }

  _deviceToggleInputEl(kind) {
    if (kind === "camera") return this.cameraEnabledToggleInputEl;
    if (kind === "desktop") return this.desktopCaptureEnabledToggleInputEl;
    return this.micEnabledToggleInputEl;
  }

  _setDeviceButtonEnabled(kind, enabled) {
    this._writeDeviceButtonPref(kind, enabled);
    const button = this._deviceButtonEl(kind);
    if (button) button.hidden = !enabled;
    if (kind === "mic") this._setMicAlwaysOnAvailable(enabled);
  }

  // Always-On only makes sense while the mic button itself exists -- turning
  // the main Microphone toggle off mid-session forcibly stops any live
  // Always-On session (there'd be no visible red indicator for it anymore)
  // and grays out its toggle so it can't be turned back on until Microphone
  // is too.
  _setMicAlwaysOnAvailable(available) {
    if (!available) {
      if (this.micAlwaysOnToggleInputEl) this.micAlwaysOnToggleInputEl.checked = false;
      this._stopMicAlwaysOn();
    }
    if (this.micAlwaysOnToggleInputEl) this.micAlwaysOnToggleInputEl.disabled = !available;
  }

  // Applies each saved preference to both the toggle switch and the actual
  // button before the first render paints -- otherwise a disabled button
  // would flash visible for a frame on every load. Mic Always-On is
  // deliberately excluded from the saved prefs below and always starts
  // unchecked/off -- same reasoning as the Debugging toggle not persisting
  // across a reload: silently reopening the microphone on page load with
  // no user action right before it would be a surprising thing for this
  // app to do on its own.
  _initDeviceButtonToggles() {
    for (const kind of ["camera", "desktop", "mic"]) {
      const enabled = this._readDeviceButtonPref(kind);
      const toggle = this._deviceToggleInputEl(kind);
      if (toggle) toggle.checked = enabled;
      const button = this._deviceButtonEl(kind);
      if (button) button.hidden = !enabled;
    }
    this._setMicAlwaysOnAvailable(this._readDeviceButtonPref("mic"));
  }

  // source is "camera" (getUserMedia) or "desktop" (getDisplayMedia,
  // browser's own share picker -- that prompt IS the permission gate,
  // same reasoning as why _startRecording needs no extra confirmation
  // beyond getUserMedia's own browser dialog). Grabs exactly one frame,
  // sends it as this turn's user_text (with whatever's currently typed as
  // the caption, or a default prompt if the box is empty), and stops the
  // stream immediately -- this is a snapshot, not a live/continuous feed,
  // so there's no reason to keep the camera light on or the "sharing your
  // screen" browser indicator up a moment longer than it takes to grab
  // the one frame.
  async _captureAndSendVision(source) {
    if (this._awaitingReply || this._connectionState === "red") return;
    // navigator.mediaDevices is entirely undefined (not just its methods)
    // outside a secure context -- https, or http on localhost/127.0.0.1.
    // Confirmed via a real debug log: opening Glitch from another device on
    // the LAN over plain http (matching config.yaml's brain.host: 0.0.0.0)
    // hits this every time, and left unguarded it throws into the catch
    // below looking identical to the user clicking "block", which it isn't.
    if (!navigator.mediaDevices) {
      this._logDebug("vision", `${source} unavailable: navigator.mediaDevices is undefined (needs HTTPS or localhost)`);
      this._setStatus(source === "desktop" ? "Screen share needs HTTPS or localhost" : "Camera needs HTTPS or localhost");
      setTimeout(() => this._setStatus(""), 4000);
      return;
    }
    // getDisplayMedia specifically (not just mediaDevices as a whole) is
    // simply absent on most mobile browsers -- iOS Safari has never
    // implemented it at all, and Android Chrome's support is spotty by
    // version. Calling a missing method throws a plain TypeError that the
    // catch below would otherwise report as "denied", which is wrong and
    // misleading: there's no permission prompt to allow here, the browser
    // just can't do this at all. getUserMedia (camera) is universally
    // supported on mobile by comparison, but checked the same way for
    // consistency and because "checked, not assumed" is cheap here.
    const apiName = source === "desktop" ? "getDisplayMedia" : "getUserMedia";
    if (typeof navigator.mediaDevices[apiName] !== "function") {
      this._logDebug("vision", `${source} unavailable: navigator.mediaDevices.${apiName} doesn't exist on this browser`);
      this._setStatus(source === "desktop" ? "Screen sharing isn't supported on this browser" : "Camera isn't supported on this browser");
      setTimeout(() => this._setStatus(""), 4000);
      return;
    }
    let stream;
    try {
      stream =
        source === "desktop"
          ? await navigator.mediaDevices.getDisplayMedia({ video: true })
          : // "ideal" (not "exact") -- prefers the rear/back camera on a
            // phone (front-facing "selfie" camera is the default
            // otherwise) but still degrades gracefully to whatever's
            // available on a laptop with only one webcam, rather than
            // getUserMedia failing outright with OverconstrainedError.
            await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: "environment" } } });
    } catch (err) {
      console.warn(`${source} access denied or unavailable:`, err);
      this._logDebug("vision", `${source} getMedia failed: ${err.message || err}`);
      this._setStatus(source === "desktop" ? "Screen share denied" : "Camera access denied");
      setTimeout(() => this._setStatus(""), 4000);
      return;
    }

    let dataUrl;
    try {
      dataUrl = await this._grabFrameFromStream(stream);
    } catch (err) {
      console.warn(`${source} frame grab failed:`, err);
      this._logDebug("vision", `${source} frame grab failed: ${err.message || err}`);
      return;
    } finally {
      stream.getTracks().forEach((track) => track.stop());
    }

    this._sendVision(dataUrl, source);
  }

  // Plays the stream into a <video> just long enough to draw one frame to
  // a <canvas>, then returns that canvas as a downscaled JPEG data URL.
  // The video sits off-screen (not zero-size/zero-opacity -- some mobile
  // browsers skip decoding an effectively-invisible video entirely as a
  // power optimization, which is suspected to be exactly what kept
  // producing a solid black capture on a phone even once this was
  // attached to the page at all) but IS attached to the page for the
  // brief moment this takes. Removed again the instant the frame is
  // grabbed (or the attempt fails), so there's nothing left for the user
  // to see either way.
  _grabFrameFromStream(stream) {
    return new Promise((resolve, reject) => {
      const video = document.createElement("video");
      video.srcObject = stream;
      video.muted = true;
      video.playsInline = true;
      video.style.cssText = "position:fixed; left:-9999px; top:-9999px; pointer-events:none;";
      document.body.appendChild(video);
      const cleanup = () => video.remove();

      const capture = () => {
        try {
          const scale = Math.min(1, MAX_VISION_DIMENSION / Math.max(video.videoWidth, video.videoHeight));
          const canvas = document.createElement("canvas");
          canvas.width = Math.round(video.videoWidth * scale);
          canvas.height = Math.round(video.videoHeight * scale);
          const ctx = canvas.getContext("2d");
          ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
          // Diagnostic only (Debugging log, "vision" category) -- every
          // fix attempted for a real black-capture report so far has been
          // a guess about *why* the frame might be black without actually
          // checking whether it is. This settles that directly: readyState
          // should be >= 2 (HAVE_CURRENT_DATA) by the time this runs, and
          // avgBrightness near 0 means the canvas itself is genuinely
          // black (a capture-side bug) rather than the frame being fine
          // and something downstream (the model, the thumbnail render)
          // being the actual problem.
          this._logDebug(
            "vision",
            `captured ${canvas.width}x${canvas.height} from ${video.videoWidth}x${video.videoHeight} video, ` +
              `readyState=${video.readyState}, avgBrightness=${_sampleAvgBrightness(ctx, canvas.width, canvas.height)}`,
          );
          cleanup();
          resolve(canvas.toDataURL("image/jpeg", VISION_JPEG_QUALITY));
        } catch (err) {
          cleanup();
          reject(err);
        }
      };

      // "loadeddata", not "loadedmetadata" -- metadata only means the
      // video's dimensions are known, not that any actual frame has been
      // decoded yet, and play() resolving only means playback *started*,
      // not that a frame has rendered.
      video.addEventListener(
        "loadeddata",
        () => {
          video
            .play()
            .then(() => {
              // requestVideoFrameCallback fires exactly when a real
              // decoded frame has actually been presented for
              // compositing -- the one guarantee loadeddata/play()
              // resolving still don't give (both fired right on schedule
              // while the capture kept coming back solid black on a
              // phone). Falls back to capturing right away for a browser
              // that doesn't support it (older Safari) -- no worse than
              // what this already did before.
              if (typeof video.requestVideoFrameCallback === "function") {
                video.requestVideoFrameCallback(() => capture());
              } else {
                capture();
              }
            })
            .catch((err) => {
              cleanup();
              reject(err);
            });
        },
        { once: true },
      );
      video.addEventListener(
        "error",
        () => {
          cleanup();
          reject(video.error);
        },
        { once: true },
      );
    });
  }

  _sendVision(dataUrl, source) {
    const imageB64 = dataUrl.slice(dataUrl.indexOf(",") + 1);
    const text = this.inputEl?.value.trim() || "";
    const placeholders = { desktop: "🖥️ (screen)", upload: "📎 (photo)" };
    this._send({ type: "user_text", text, image_b64: imageB64, image_mime: "image/jpeg" });
    this._addHistoryEntry("user", text || placeholders[source] || "📷 (photo)", dataUrl);
    this._startSlowReplyTimer();
    this._setAwaitingReply(true);
    this._pendingReplySentAt = Date.now();
    this._logDebug("ws", `sent user_text with ${source} image (${Math.round(imageB64.length / 1024)} KB)`);
    this.inputEl.value = "";
  }

  // 📎 button entry point -- an image reuses the exact same pipeline as
  // the camera/desktop buttons (downscaled, sent as an image_b64); a text
  // file gets its content embedded straight into the message text so she
  // can actually read and respond to it (a deliberate choice over just
  // showing "file attached" with nothing sent -- see this feature's own
  // design discussion). Anything else is rejected rather than guessed at:
  // reading an arbitrary binary file as text would send garbage tokens to
  // the LLM for no benefit.
  _handleFileUpload(file) {
    if (!file || this._awaitingReply || this._connectionState === "red") return;
    if (file.type.startsWith("image/")) {
      this._sendImageFile(file);
    } else if (this._looksLikeTextFile(file)) {
      this._sendTextFile(file);
    } else {
      this._setStatus("Unsupported file type");
      setTimeout(() => this._setStatus(""), 4000);
    }
  }

  // file.type is often empty/generic (application/octet-stream) for plain
  // text files depending on the OS, so the extension is checked too --
  // relying on MIME type alone missed real .yaml/.log files in testing.
  _looksLikeTextFile(file) {
    if (file.type.startsWith("text/") || file.type === "application/json") return true;
    const name = file.name.toLowerCase();
    return TEXT_FILE_EXTENSIONS.some((ext) => name.endsWith(ext));
  }

  async _sendImageFile(file) {
    let dataUrl;
    try {
      dataUrl = await this._readFileAsDataUrl(file);
      dataUrl = await this._downscaleImageDataUrl(dataUrl);
    } catch (err) {
      console.warn("failed to read/downscale image file:", err);
      this._logDebug("upload", `image file failed: ${err.message || err}`);
      this._setStatus("Couldn't read that image");
      setTimeout(() => this._setStatus(""), 4000);
      return;
    }
    this._sendVision(dataUrl, "upload");
  }

  _readFileAsDataUrl(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(reader.error || new Error("FileReader failed"));
      reader.readAsDataURL(file);
    });
  }

  // Same max-dimension/quality downscale as the camera/desktop capture
  // path (_grabFrameFromStream) -- a picked photo from a phone's camera
  // roll can easily be 10+ MB at full resolution, same reasoning for
  // capping it applies equally here.
  _downscaleImageDataUrl(dataUrl) {
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => {
        const scale = Math.min(1, MAX_VISION_DIMENSION / Math.max(img.naturalWidth, img.naturalHeight));
        const canvas = document.createElement("canvas");
        canvas.width = Math.round(img.naturalWidth * scale);
        canvas.height = Math.round(img.naturalHeight * scale);
        canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
        resolve(canvas.toDataURL("image/jpeg", VISION_JPEG_QUALITY));
      };
      img.onerror = () => reject(new Error("failed to decode image"));
      img.src = dataUrl;
    });
  }

  async _sendTextFile(file) {
    let content;
    try {
      content = await file.text();
    } catch (err) {
      console.warn("failed to read text file:", err);
      this._logDebug("upload", `text file failed: ${err.message || err}`);
      this._setStatus("Couldn't read that file");
      setTimeout(() => this._setStatus(""), 4000);
      return;
    }
    const truncated = content.length > MAX_UPLOADED_TEXT_CHARS;
    if (truncated) content = content.slice(0, MAX_UPLOADED_TEXT_CHARS);
    const caption = this.inputEl?.value.trim() || "";
    const attachment = `Attached file "${file.name}"${truncated ? " (truncated)" : ""}:\n\n${content}`;
    const text = caption ? `${caption}\n\n${attachment}` : attachment;
    this._send({ type: "user_text", text });
    this._addHistoryEntry("user", `${caption ? caption + " " : ""}📎 ${file.name}`);
    this._startSlowReplyTimer();
    this._setAwaitingReply(true);
    this._pendingReplySentAt = Date.now();
    this._logDebug("ws", `sent user_text with attached text file (${content.length} chars${truncated ? ", truncated" : ""})`);
    this.inputEl.value = "";
  }

  async _startRecording() {
    if (this._micAlwaysOn) return; // the mic button is just a listening indicator in this mode, not a push-to-talk control
    if (this.mediaRecorder && this.mediaRecorder.state === "recording") return;
    // see _captureAndSendVision's comment -- mediaDevices is undefined
    // outside a secure context, not just denied.
    if (!navigator.mediaDevices) {
      this._logDebug("mic", "unavailable: navigator.mediaDevices is undefined (needs HTTPS or localhost)");
      this._setStatus("Microphone needs HTTPS or localhost");
      setTimeout(() => this._setStatus(""), 4000);
      return;
    }
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
      console.warn("Microphone access denied or unavailable:", err);
      this._logDebug("mic", `getUserMedia failed: ${err.message || err}`);
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
    if (this._micAlwaysOn) return;
    if (!this.mediaRecorder || this.mediaRecorder.state !== "recording") return;
    this.mediaRecorder.stop();
    this.micButtonEl?.classList.remove("recording");
    if (this.micLabelEl) this.micLabelEl.textContent = " Hold to talk";
  }

  // Opens one persistent mic stream and leaves it open across many
  // utterances -- unlike push-to-talk's _startRecording/_stopRecording,
  // which open a fresh getUserMedia stream and tear it down every single
  // press. A simple volume-based VAD (_pollMicVad) decides when to start
  // and stop a MediaRecorder segment on top of that one stream, so the mic
  // permission indicator/light only has to turn on once per Always-On
  // session, not once per sentence.
  async _startMicAlwaysOn() {
    if (this._micAlwaysOn) return;
    // see _captureAndSendVision's comment -- mediaDevices is undefined
    // outside a secure context, not just denied.
    if (!navigator.mediaDevices) {
      this._logDebug("mic", "always-on unavailable: navigator.mediaDevices is undefined (needs HTTPS or localhost)");
      this._setStatus("Microphone needs HTTPS or localhost");
      setTimeout(() => this._setStatus(""), 4000);
      if (this.micAlwaysOnToggleInputEl) this.micAlwaysOnToggleInputEl.checked = false;
      return;
    }
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
      console.warn("Microphone access denied or unavailable:", err);
      this._logDebug("mic", `always-on getUserMedia failed: ${err.message || err}`);
      this._setStatus("Microphone access denied");
      setTimeout(() => this._setStatus(""), 4000);
      if (this.micAlwaysOnToggleInputEl) this.micAlwaysOnToggleInputEl.checked = false;
      return;
    }

    this._micAlwaysOnStream = stream;
    this._micAudioCtx = new AudioContext();
    this._micAnalyser = this._micAudioCtx.createAnalyser();
    this._micAnalyser.fftSize = 512;
    this._micAudioCtx.createMediaStreamSource(stream).connect(this._micAnalyser);

    this._micAlwaysOn = true;
    this._micVadState = "silence";
    this._micSilenceStartedAt = null;
    this.micButtonEl?.classList.add("recording");
    if (this.micLabelEl) this.micLabelEl.textContent = " Listening...";
    this._logDebug("mic", "always-on: listening");
    this._micVadTimer = setInterval(() => this._pollMicVad(), MIC_VAD_POLL_MS);
  }

  _stopMicAlwaysOn() {
    if (!this._micAlwaysOn) return;
    clearInterval(this._micVadTimer);
    this._micVadTimer = null;
    // Stopping the stream's tracks immediately after calling
    // mediaRecorder.stop() (rather than after its "stop" event actually
    // fires) risks cutting off the tail of whatever was mid-recording --
    // so when there's an active segment, tear the stream down only once
    // its "stop" handler (_sendRecording, via the listener _pollMicVad
    // attached) has run.
    const stream = this._micAlwaysOnStream;
    const audioCtx = this._micAudioCtx;
    const teardownStream = () => {
      stream?.getTracks().forEach((track) => track.stop());
      audioCtx?.close();
    };
    if (this.mediaRecorder && this.mediaRecorder.state === "recording") {
      this.mediaRecorder.addEventListener("stop", teardownStream, { once: true });
      this.mediaRecorder.stop(); // triggers _sendRecording via the listener already attached in _pollMicVad
    } else {
      teardownStream();
    }
    this._micAlwaysOnStream = null;
    this._micAudioCtx = null;
    this._micAnalyser = null;
    this._micAlwaysOn = false;
    this.micButtonEl?.classList.remove("recording");
    if (this.micLabelEl) this.micLabelEl.textContent = " Hold to talk";
    this._logDebug("mic", "always-on: stopped");
  }

  // Runs every MIC_VAD_POLL_MS while Always-On is active. Starts a
  // MediaRecorder segment the moment volume crosses the speech threshold,
  // and stops (sends) it once volume has stayed below threshold for
  // MIC_VAD_SILENCE_MS straight -- then goes back to waiting for the next
  // utterance on the same still-open stream.
  _pollMicVad() {
    // Mirrors the mic button's own disabled state (_updateSendButtonDisabled)
    // -- don't start listening for a new utterance while she's still
    // working on the last one or the connection's down. An utterance
    // already mid-recording when this flips true is left to finish
    // naturally rather than cut off (in practice this doesn't happen: this
    // guard's own state only ever becomes true right as the previous
    // recording finishes and sends, never mid-recording).
    if (this._awaitingReply || this._connectionState === "red") return;
    const data = new Uint8Array(this._micAnalyser.fftSize);
    this._micAnalyser.getByteTimeDomainData(data);
    let sumSquares = 0;
    for (let i = 0; i < data.length; i++) {
      const v = (data[i] - 128) / 128;
      sumSquares += v * v;
    }
    const rms = Math.sqrt(sumSquares / data.length);

    if (rms > MIC_VAD_SPEECH_RMS_THRESHOLD) {
      this._micSilenceStartedAt = null;
      if (this._micVadState === "silence") {
        this._micVadState = "speaking";
        this.recordedChunks = [];
        this.mediaRecorder = new MediaRecorder(this._micAlwaysOnStream);
        this.mediaRecorder.addEventListener("dataavailable", (e) => {
          if (e.data.size > 0) this.recordedChunks.push(e.data);
        });
        // Deliberately doesn't stop the stream's tracks on "stop" (unlike
        // push-to-talk's) -- Always-On keeps the same stream open for the
        // next utterance too.
        this.mediaRecorder.addEventListener("stop", () => this._sendRecording());
        this.mediaRecorder.start();
        if (this.micLabelEl) this.micLabelEl.textContent = " Recording...";
      }
    } else if (this._micVadState === "speaking") {
      if (this._micSilenceStartedAt === null) {
        this._micSilenceStartedAt = Date.now();
      } else if (Date.now() - this._micSilenceStartedAt >= MIC_VAD_SILENCE_MS) {
        this._micVadState = "silence";
        this._micSilenceStartedAt = null;
        this.mediaRecorder.stop();
        if (this.micLabelEl) this.micLabelEl.textContent = " Listening...";
      }
    }
  }

  async _sendRecording() {
    if (this.recordedChunks.length === 0) return;
    const mimeType = this.mediaRecorder.mimeType || "audio/webm";
    const blob = new Blob(this.recordedChunks, { type: mimeType });
    const audioB64 = _arrayBufferToBase64(await blob.arrayBuffer());
    this._send({ type: "user_audio", audio_b64: audioB64, mime_type: mimeType });
    // Starts as a placeholder since the transcript only exists Brain-side
    // (STT runs there) -- swapped for the real words when "user_transcript"
    // arrives (see _handleMessage), or left as-is if STT fails/comes back
    // blank (no_reply path never sends one).
    this._pendingAudioEntry = this._addHistoryEntry("user", "🎤 (voice message)");
    this._startSlowReplyTimer();
    this._setAwaitingReply(true); // same reply pipeline as text -- Send stays disabled until she answers this too
    this._pendingReplySentAt = Date.now();
    this._logDebug("ws", `sent user_audio (${blob.size} bytes, ${mimeType})`);
  }

  _send(message) {
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify(message));
    }
  }

  _setStatus(text) {
    if (this.statusEl) this.statusEl.textContent = text;
  }

  // Reveals #chat-bar and #side-actions (if hidden) and restarts the idle
  // countdown for both together -- they're really one "chat controls"
  // cluster, just split into two floating groups (bottom bar + right-side
  // column). Called on every pointerdown/keydown anywhere on the page
  // (see the constructor) as well as anywhere internally that counts as
  // "the user is doing something with the chat controls" (sending,
  // recording, etc.).
  _resetIdleTimer() {
    this.chatBarEl?.classList.remove("idle");
    this.sideActionsEl?.classList.remove("idle");
    clearTimeout(this._idleTimer);
    this._idleTimer = setTimeout(() => this._maybeHideActionBars(), CHAT_BAR_IDLE_MS);
  }

  // Doesn't hide out from under active use: typing (input focused), an
  // in-progress push-to-talk hold, or Mic Always-On (which deliberately
  // needs to stay visible/red the whole time it's listening -- see that
  // feature's own design). Each of those cases just checks again after
  // another full idle window rather than hiding immediately once they end.
  _maybeHideActionBars() {
    const activelyUsing =
      document.activeElement === this.inputEl ||
      this.mediaRecorder?.state === "recording" ||
      this._micAlwaysOn;
    if (activelyUsing) {
      this._idleTimer = setTimeout(() => this._maybeHideActionBars(), CHAT_BAR_IDLE_MS);
      return;
    }
    this.chatBarEl?.classList.add("idle");
    this.sideActionsEl?.classList.add("idle");
  }

  // Brief toast above the chat bar for a memory_learned message -- separate
  // from the permanent chat-history entry _handleMessage also adds for the
  // same event (see its "memory_learned" case); this one is just a
  // transient heads-up, not part of the conversation record.
  _showMemoryToast(fact) {
    if (!this.memoryToastEl) return;
    this.memoryToastEl.textContent = `🧠 Learned: ${fact}`;
    this.memoryToastEl.classList.add("visible");
    clearTimeout(this._memoryToastTimer);
    this._memoryToastTimer = setTimeout(() => this.memoryToastEl.classList.remove("visible"), MEMORY_TOAST_DURATION_MS);
  }

  _setConnectionState(state) {
    this._connectionState = state;
    this._updateSendButtonDisabled(); // red (not connected at all) means nothing to send to -- gray it out same as an in-flight reply
    if (!this.connectionLightEl) return;
    this.connectionLightEl.classList.remove("red", "yellow", "green");
    this.connectionLightEl.classList.add(state);
  }

  // Started whenever we send something expecting a reply (user_text,
  // user_audio); cleared the moment speak_text actually arrives (see
  // _handleMessage). If it fires first, Brain's still connected -- the
  // socket would already be "red" otherwise -- just slow to reply, most
  // often the local LLM itself taking a while, not a broken connection.
  _startSlowReplyTimer() {
    clearTimeout(this._slowReplyTimer);
    this._slowReplyTimer = setTimeout(() => {
      if (this._connectionState !== "red") this._setConnectionState("yellow");
    }, SLOW_REPLY_THRESHOLD_MS);
  }

  _clearSlowReplyTimer() {
    clearTimeout(this._slowReplyTimer);
    this._slowReplyTimer = null;
    if (this._connectionState !== "red") this._setConnectionState("green");
  }

  // On/off switch for whether the selected profile actually role-plays
  // Glitch (folded into the LLM's system prompt) or she just uses her
  // default personality as-is -- independent of *which* profile is
  // selected in the dropdown below, so toggling this off and back on
  // doesn't lose the pick (Brain keeps it queued, see protocol.md's
  // load_profile row).
  // think is only meaningful when turning on -- it's what the role-play
  // confirm modal's own toggle was set to, telling Brain whether to
  // enable or disable thinking on the engine it's about to switch to (see
  // main.py's _switch_to_roleplay_engine). Turning off always re-enables
  // thinking on that same engine server-side (mirrors the "on" path,
  // just with think forced true) rather than leaving whatever fast/
  // no-think mode RP needed quietly on afterward -- see
  // _handle_set_roleplay_active's own "else" branch.
  _setRoleplayActive(active, think) {
    this._roleplayActive = active;
    this._renderRoleplayToggle();
    this._send({ type: "set_roleplay_active", active, think: !!think });
    // Optimistic, same reasoning as _loadLlmEngine -- Brain doesn't echo
    // back an llm_engines update after either switch, so without this the
    // Settings LLM Engine dropdown would keep showing whatever was active
    // before, even though the switch already happened server-side.
    // "Ollama" is deliberately hardcoded, not a guess -- it must match
    // main.py's own ROLEPLAY_LLM_ENGINE_NAME exactly, since that's the one
    // fixed engine role-play always switches to (see that constant's own
    // comment for why it's fixed rather than user-configurable).
    this._activeLlmEngineName = "Ollama";
    this._renderLlmEngineList(this._llmEngineNames, this._activeLlmEngineName);
  }

  _openRoleplayConfirmModal() {
    if (this.roleplayConfirmThinkToggleEl) this.roleplayConfirmThinkToggleEl.checked = false;
    if (this.roleplayConfirmModalBackdropEl) this.roleplayConfirmModalBackdropEl.hidden = false;
  }

  _closeRoleplayConfirmModal() {
    if (this.roleplayConfirmModalBackdropEl) this.roleplayConfirmModalBackdropEl.hidden = true;
  }

  _renderRoleplayToggle() {
    if (this.roleplayToggleInputEl) {
      this.roleplayToggleInputEl.checked = this._roleplayActive;
      this.roleplayToggleInputEl.disabled = this._harnessActive;
    }
    // The profile picker and the soul picker are both moot while
    // role-play is off (neither currently affects what the LLM does), so
    // hide both entirely rather than leaving inert controls visible.
    if (this.profileOptionsEl) this.profileOptionsEl.hidden = !this._roleplayActive;
    if (this.soulSectionEl) this.soulSectionEl.hidden = !this._roleplayActive;
    this.roleplayBadgeEl?.classList.toggle("active", this._roleplayActive);
  }

  // The master override: while active, Brain relays straight to an
  // external agent harness (brain/harness.py) instead of using anything
  // below (profile/soul/LLM engine) -- see protocol.md's
  // set_harness_active. Turning it on requires the confirm-and-connect
  // modal (_openHarnessConfirmModal, from the toggle's own change
  // listener) since it's a bigger behavioral switch than any other
  // settings-panel control -- and, unlike every other setting here, Brain
  // can outright refuse it (wrong/missing switch key, unknown or
  // unconfigured harness). So this deliberately does NOT optimistically
  // flip _harnessActive/re-render the way the rest of this class's
  // setters do -- it just sends the request and waits for Brain's own
  // harness_state reply (see _handleMessage) to say what actually
  // happened. The toggle itself was already reverted synchronously by
  // the change listener, so there's nothing stale left showing in the
  // meantime. `key` is only meaningful (and only sent) when turning on --
  // it's this._harnessSwitchKey, learned from a prior harness_state, not
  // anything the user typed just now (see _saveHarnessKey for the one
  // place they actually type it).
  _setHarnessActive(active, name) {
    this._send({ type: "set_harness_active", active, name, key: active ? this._harnessSwitchKey : "" });
  }

  _openHarnessConfirmModal(name) {
    this._pendingHarnessConnectName = name;
    if (this.harnessConfirmModalTextEl) {
      this.harnessConfirmModalTextEl.textContent =
        `Connect Glitch to the "${name}" harness? She'll use it as her brain instead of her ` +
        "profile/soul/LLM settings below until you disconnect.";
    }
    if (this.harnessConfirmModalBackdropEl) this.harnessConfirmModalBackdropEl.hidden = false;
  }

  _closeHarnessConfirmModal() {
    this._pendingHarnessConnectName = null;
    if (this.harnessConfirmModalBackdropEl) this.harnessConfirmModalBackdropEl.hidden = true;
  }

  // Requests the saved endpoint/model/api_key for the Edit button --
  // pre-fills the New/Edit Harness modal once harness_content arrives
  // (see _handleMessage), same pattern as _editLlmEngine/_editTtsEngine.
  _editHarness(name) {
    this._pendingEditHarnessName = name;
    this._send({ type: "get_harness", name });
  }

  _openHarnessModal(name = "", endpoint = "", model = "", apiKey = "") {
    if (!this.harnessModalBackdropEl) return;
    if (this.harnessModalTitleEl) this.harnessModalTitleEl.textContent = name ? "Edit Harness" : "New Harness";
    if (this.harnessNameEl) this.harnessNameEl.value = name;
    if (this.harnessEndpointEl) this.harnessEndpointEl.value = endpoint;
    if (this.harnessModelEl) this.harnessModelEl.value = model;
    if (this.harnessApiKeyEl) this.harnessApiKeyEl.value = apiKey;
    this.harnessModalBackdropEl.hidden = false;
  }

  _closeHarnessModal() {
    if (this.harnessModalBackdropEl) this.harnessModalBackdropEl.hidden = true;
  }

  _saveHarness() {
    const name = this.harnessNameEl?.value.trim() || "";
    const endpoint = this.harnessEndpointEl?.value.trim() || "";
    const model = this.harnessModelEl?.value.trim() || "";
    const apiKey = this.harnessApiKeyEl?.value.trim() || "";
    if (!name || !endpoint) return;
    this._send({ type: "save_harness", name, endpoint, model, api_key: apiKey });
    this._closeHarnessModal();
  }

  _deleteHarness(name) {
    if (!name || name === NONE_HARNESS_NAME) return;
    if (!window.confirm(`Delete the saved harness "${name}"? This can't be undone.`)) return;
    this._send({ type: "delete_harness", name });
  }

  // The Harness settings' own "Switch key" field/button -- typed once
  // here, not on every connection attempt (see _setHarnessActive's own
  // comment). An empty field clears the key entirely (config.yaml's
  // brain.harness_switch_key/harness.py's set_switch_key both treat ""
  // the same way: no extra gate beyond the confirm dialog).
  _saveHarnessKey() {
    const key = this.harnessSwitchKeyEl?.value.trim() || "";
    this._send({ type: "save_harness_key", key });
  }

  // Everything in the Memory section that depends on which backend is
  // picked. The connection fields are shown only for "hindsight" --
  // picking "local" hides them again without clearing whatever was
  // typed, so switching back and forth doesn't lose a half-filled-in
  // server URL. Download/Clear Memory are the inverse: local-only --
  // Hindsight has its own server-side tools for that, and "Clear Memory"
  // there means deleting and recreating the whole bank (memory.py's
  // clear()), a bigger and more destructive action than clearing a local
  // flat file, so this deliberately doesn't offer it as a casual
  // Settings-panel button for that backend.
  _renderMemoryProviderVisibility(provider) {
    const isHindsight = provider === "hindsight";
    if (this.hindsightConfigFieldsEl) this.hindsightConfigFieldsEl.hidden = !isHindsight;
    if (this.downloadMemoryButtonEl) this.downloadMemoryButtonEl.hidden = isHindsight;
    if (this.clearMemoryButtonEl) this.clearMemoryButtonEl.hidden = isHindsight;
  }

  _saveHindsightConfig() {
    const apiUrl = this.hindsightApiUrlEl?.value.trim() || "";
    const apiKey = this.hindsightApiKeyEl?.value.trim() || "";
    const bankId = this.hindsightBankIdEl?.value.trim() || "";
    if (!apiUrl) return;
    this._send({ type: "save_hindsight_config", api_url: apiUrl, api_key: apiKey, bank_id: bankId });
  }

  // Always prepends None (never sent by Brain, see harness.py's
  // NONE_NAME) and always sets .value, defaulting to None when nothing's
  // active -- the toggle (see _renderHarnessState) is what actually means
  // on/off; this dropdown just says which one.
  _renderHarnessDropdown() {
    if (!this.harnessSelectEl) return;
    this.harnessSelectEl.replaceChildren();
    const noneOption = document.createElement("option");
    noneOption.value = NONE_HARNESS_NAME;
    noneOption.textContent = NONE_HARNESS_NAME;
    this.harnessSelectEl.appendChild(noneOption);
    for (const name of this._harnessAvailable) {
      const option = document.createElement("option");
      option.value = name;
      option.textContent = name;
      this.harnessSelectEl.appendChild(option);
    }
    this.harnessSelectEl.value = this._activeHarnessName || this._selectedHarnessName || NONE_HARNESS_NAME;
  }

  // Edit/Delete only make sense for a real saved harness, not the
  // reserved None entry -- same "isDefault-style" disabled pattern as
  // _updateLlmEngineEditControls etc. Re-run on every dropdown change
  // (not just when harness_state arrives), so browsing to a different
  // entry updates these immediately.
  _updateHarnessEditControls() {
    const disabled = !this.harnessSelectEl?.value || this.harnessSelectEl.value === NONE_HARNESS_NAME;
    if (this.editHarnessButtonEl) this.editHarnessButtonEl.disabled = disabled;
    if (this.deleteHarnessButtonEl) this.deleteHarnessButtonEl.disabled = disabled;
  }

  // Applies harness state everywhere it needs to show up: the toggle
  // itself, the dropdown (locked once connected -- switching harnesses
  // live isn't a thing, disconnect first), the Edit/Delete buttons, and
  // every control in #harness-lockable (profile/soul/avatar/speech-engine/
  // LLM sections) -- a real .disabled on each, not just a CSS dim, so
  // this can't be fought by some other render re-enabling one of them
  // (see _updateProfileEditControls and friends, which all fold
  // this._harnessActive into their own disabled computation for exactly
  // that reason).
  _renderHarnessState() {
    this._renderHarnessDropdown();
    if (this.harnessToggleInputEl) {
      this.harnessToggleInputEl.checked = this._harnessActive;
      this.harnessToggleInputEl.disabled = this._harnessActive ? false : this.harnessSelectEl?.value === NONE_HARNESS_NAME;
    }
    if (this.harnessSelectEl) this.harnessSelectEl.disabled = this._harnessActive;
    this._updateHarnessEditControls();
    this.harnessLockableEl?.classList.toggle("harness-locked", this._harnessActive);
    for (const el of this.harnessLockableEl?.querySelectorAll("select, button, input") || []) {
      el.disabled = this._harnessActive;
    }
    // Re-derive each section's own disabled logic (isDefault, etc.) now
    // that the blanket harness lock above may have just been lifted --
    // otherwise turning the harness off would leave everything disabled
    // until something else happened to re-render one of these.
    this._updateProfileEditControls();
    this._updateSoulEditControls();
    this._updateTtsEngineEditControls();
    this._updateLlmEngineEditControls();
    this._renderHarnessLight(); // active may have just flipped -- green overrides whatever reachability last said
  }

  // green: active (mirrors #connection-light's own green -- plugged in
  // and presumably working). blue: not active, but the last
  // harness_health check found it reachable. red: not active and
  // unreachable. Uncolored (base grey): no harness_health for the
  // relevant name yet -- same "nothing painted yet" convention as
  // _connectionState before connect() resolves anything, rather than
  // guessing at a color with no data behind it. "Relevant name" is
  // whichever harness is active, or (not active) whichever is currently
  // selected in the dropdown -- now genuinely open-ended (harness.py),
  // so this reads whatever's actually selected rather than assuming one.
  _renderHarnessLight() {
    if (!this.harnessLightEl) return;
    this.harnessLightEl.classList.remove("red", "blue", "green");
    if (this._harnessActive) {
      this.harnessLightEl.classList.add("green");
      return;
    }
    const name = this._activeHarnessName || this.harnessSelectEl?.value;
    const reachable = name ? this._harnessReachableByName[name] : undefined;
    if (reachable === true) this.harnessLightEl.classList.add("blue");
    else if (reachable === false) this.harnessLightEl.classList.add("red");
  }

  // Same idea as _renderHarnessLight, adapted for speech engines: there's
  // no separate on/off (some engine is always "active"), so green means
  // "this is the currently active one" full stop -- reachability
  // (blue/red) only ever shows for a name that isn't the active one,
  // e.g. while just glancing at a saved engine's last-known health.
  // NONE_TTS_ENGINE_NAME is the one exception to "active = green": it
  // means no speech engine at all, so it stays grey even while active --
  // same "unconfigured" meaning grey carries for the harness light.
  _renderTtsLight() {
    if (!this.ttsLightEl) return;
    this.ttsLightEl.classList.remove("red", "blue", "green");
    const name = this.ttsEngineSelectEl?.value || this._activeTtsEngineName;
    if (name === NONE_TTS_ENGINE_NAME) return;
    if (name && name === this._activeTtsEngineName) {
      this.ttsLightEl.classList.add("green");
      return;
    }
    const reachable = name ? this._ttsReachableByName[name] : undefined;
    if (reachable === true) this.ttsLightEl.classList.add("blue");
    else if (reachable === false) this.ttsLightEl.classList.add("red");
  }

  // A dropdown, same quick-pick pattern as the avatar picker -- selecting
  // an option loads it immediately via the select's own change event, no
  // separate confirm click. Edit/Delete act on whatever's currently
  // selected (single external icon buttons, not one per entry -- a
  // <select>'s options can't contain buttons of their own). "Default"
  // (DEFAULT_PROFILE_NAME) is always the first option, same way the
  // avatar picker always offers "Glitch" -- it's not in Brain's own
  // `profiles` list, prepended here instead. Called whenever a `profiles`
  // message arrives -- right after connect, again after every save, and
  // again after every delete (see _handleMessage) -- `active` is Brain's
  // own record of the current selection (profiles.py's
  // read_active_profile_name), not just whatever this client last picked,
  // so a reconnect or a delete-triggered fallback to Default both show up
  // correctly here.
  _renderProfileList(names, active) {
    this._profileNames = names;
    this._activeProfileName = active;
    _renderDropdown(this.profileSelectEl, [DEFAULT_PROFILE_NAME, ...names], active);
    this._updateProfileEditControls();
  }

  _loadProfile(name) {
    if (!name) return;
    this._send({ type: "load_profile", name });
    this._activeProfileName = name;
    this._updateProfileEditControls();
  }

  // Asks Brain for the profile's current content (the Renderer only ever
  // has names from the `profiles` list, not content) so the editor can
  // be pre-filled -- see _handleMessage's "profile_content" case for
  // where the response actually opens the modal.
  _editProfile(name) {
    this._pendingEditName = name;
    this._send({ type: "get_profile", name });
  }

  // Edit/Delete only make sense for a real saved profile -- Default has
  // no file behind it (nothing to read for Edit, nothing to remove for
  // Delete, and brain/profiles.py's delete_profile refuses it anyway).
  _updateProfileEditControls() {
    // this._harnessActive folded in, not just isDefault -- otherwise a
    // stray profiles broadcast arriving while the harness is active could
    // re-enable these out from under _renderHarnessState's blanket lock.
    const disabled = this._harnessActive || this.profileSelectEl?.value === DEFAULT_PROFILE_NAME;
    if (this.editProfileButtonEl) this.editProfileButtonEl.disabled = disabled;
    if (this.deleteProfileButtonEl) this.deleteProfileButtonEl.disabled = disabled;
  }

  _deleteProfile(name) {
    if (!name || name === DEFAULT_PROFILE_NAME) return;
    if (!window.confirm(`Delete the saved profile "${name}"? This can't be undone.`)) return;
    this._send({ type: "delete_profile", name });
  }

  // name/content prefill the fields (both editing an existing profile and
  // creating a new one -- Cancel/Save both just look at whatever's in the
  // fields, so no separate "mode" needs tracking beyond the modal title).
  _openProfileModal(name = "", content = "") {
    if (!this.profileModalBackdropEl) return;
    if (this.profileModalTitleEl) this.profileModalTitleEl.textContent = name ? "Edit Profile" : "New Profile";
    if (this.profileNameEl) this.profileNameEl.value = name;
    if (this.profileContentEl) this.profileContentEl.value = content;
    this.profileModalBackdropEl.hidden = false;
  }

  _closeProfileModal() {
    if (this.profileModalBackdropEl) this.profileModalBackdropEl.hidden = true;
  }

  _saveProfile() {
    const name = this.profileNameEl?.value.trim() || "";
    const content = this.profileContentEl?.value.trim() || "";
    if (!name || !content) return;
    // Saving under the same name Edit opened with overwrites that
    // profile; changing the name instead saves as a new one alongside
    // it -- both are just save_profile, no separate "update" message.
    this._send({ type: "save_profile", name, content });
    this._closeProfileModal();
  }

  // Mirrors the profile dropdown above, for souls -- who Glitch is
  // (brain/souls.py), kept as two separate fields (description + example
  // dialogue) end to end rather than combined, unlike profiles. Same
  // DEFAULT_SOUL_NAME prepend/active-tracking/fallback-on-delete pattern.
  _renderSoulList(names, active) {
    this._soulNames = names;
    this._activeSoulName = active;
    _renderDropdown(this.soulSelectEl, [DEFAULT_SOUL_NAME, ...names], active);
    this._updateSoulEditControls();
  }

  _loadSoul(name) {
    if (!name) return;
    this._send({ type: "load_soul", name });
    this._activeSoulName = name;
    this._updateSoulEditControls();
  }

  _editSoul(name) {
    this._pendingEditSoulName = name;
    this._send({ type: "get_soul", name });
  }

  _updateSoulEditControls() {
    const disabled = this._harnessActive || this.soulSelectEl?.value === DEFAULT_SOUL_NAME;
    if (this.editSoulButtonEl) this.editSoulButtonEl.disabled = disabled;
    if (this.deleteSoulButtonEl) this.deleteSoulButtonEl.disabled = disabled;
  }

  _deleteSoul(name) {
    if (!name || name === DEFAULT_SOUL_NAME) return;
    if (!window.confirm(`Delete the saved soul "${name}"? This can't be undone.`)) return;
    this._send({ type: "delete_soul", name });
  }

  _openSoulModal(name = "", description = "", examples = "") {
    if (!this.soulModalBackdropEl) return;
    if (this.soulModalTitleEl) this.soulModalTitleEl.textContent = name ? "Edit Soul" : "New Soul";
    if (this.soulNameEl) this.soulNameEl.value = name;
    if (this.soulDescriptionEl) this.soulDescriptionEl.value = description;
    if (this.soulExamplesEl) this.soulExamplesEl.value = examples;
    this.soulModalBackdropEl.hidden = false;
  }

  _closeSoulModal() {
    if (this.soulModalBackdropEl) this.soulModalBackdropEl.hidden = true;
  }

  _saveSoul() {
    const name = this.soulNameEl?.value.trim() || "";
    const description = this.soulDescriptionEl?.value.trim() || "";
    const examples = this.soulExamplesEl?.value.trim() || "";
    if (!name || !description) return;
    this._send({ type: "save_soul", name, description, examples });
    this._closeSoulModal();
  }

  // Raw manual-edit escape hatch for soul.md/user.md -- separate from the
  // named saved souls/profiles above (_openSoulModal etc.). Opened via a
  // get_soul_and_user round trip (see the click listener in the
  // constructor) rather than reading anything cached client-side, so it
  // always shows the actual current file content even if it drifted from
  // whatever named soul/profile was last loaded.
  _openSoulUserEditorModal(soul, user) {
    if (!this.soulUserEditorModalBackdropEl) return;
    if (this.soulUserEditorSoulEl) this.soulUserEditorSoulEl.value = soul;
    if (this.soulUserEditorUserEl) this.soulUserEditorUserEl.value = user;
    this.soulUserEditorModalBackdropEl.hidden = false;
  }

  _closeSoulUserEditorModal() {
    if (this.soulUserEditorModalBackdropEl) this.soulUserEditorModalBackdropEl.hidden = true;
  }

  _saveSoulUserEditor() {
    const soul = this.soulUserEditorSoulEl?.value ?? "";
    const user = this.soulUserEditorUserEl?.value ?? "";
    this._send({ type: "save_soul_and_user", soul, user });
    this._closeSoulUserEditorModal();
  }

  _openNotesModal(content) {
    if (!this.notesModalBackdropEl) return;
    if (this.notesTextareaEl) this.notesTextareaEl.value = content;
    this.notesModalBackdropEl.hidden = false;
  }

  _closeNotesModal() {
    if (this.notesModalBackdropEl) this.notesModalBackdropEl.hidden = true;
  }

  _saveNotes() {
    const content = this.notesTextareaEl?.value ?? "";
    this._send({ type: "save_notes", content });
    this._closeNotesModal();
  }

  // A dropdown rather than the list-with-Load-button pattern
  // profiles/souls use -- picking an option loads it immediately (the
  // `change` listener in the constructor), no separate confirm action.
  // "Glitch" (the shipped default) is always the first option even
  // though it never appears in Brain's own `avatars` list -- it loads
  // locally with no WS round trip at all (see avatars.py's module
  // docstring), unlike every other option here. `avatars` is
  // avatars.py's list_avatars()'s own [{name, kind}, ...] shape.
  _renderAvatarList(avatars) {
    this._customAvatars = avatars;
    _renderDropdown(this.avatarSelectEl, ["Glitch", ...avatars.map((a) => a.name)], this._activeAvatarName);
  }

  _loadAvatarByName(name) {
    if (name === "Glitch") {
      // No bytes to fetch -- the shipped default loads locally, and it's
      // always a .vrm (there's no flat-image version of the shipped model).
      this._onAvatarSwap?.("/Glitch.vrm", "vrm");
      this._logDebug("avatar", "switched to avatar 'Glitch' (vrm)");
      this._activeAvatarName = name;
      this._renderAvatarList(this._customAvatars);
    }
    // Bookkeeping either way, so Brain remembers this choice across its
    // own restarts (avatars.py's active_avatar.txt) -- for a non-default
    // name, Brain's avatar_data reply (carrying the saved kind) is what
    // actually performs the swap (see _handleMessage), not this call itself.
    this._send({ type: "load_avatar", name });
  }

  async _importAvatarFile(file) {
    const buffer = await file.arrayBuffer();
    // Swap immediately, client-side -- no reason to wait on a round trip
    // to Brain when the bytes are already sitting right here.
    this._onAvatarSwap?.(buffer, "vrm");
    const name = file.name.replace(/\.vrm$/i, "");
    this._activeAvatarName = name;
    this._renderAvatarList(this._customAvatars);
    this._send({ type: "save_avatar", name, data_b64: _arrayBufferToBase64(buffer), kind: "vrm" });
  }

  // Mirrors _importAvatarFile exactly, for a flat .png reference image
  // instead of a 3D .vrm model -- see main.js's setActiveAvatar for how
  // "png" changes what actually gets rendered (a static image instead of
  // the 3D scene, with camera panning disabled).
  async _importAvatarPngFile(file) {
    const buffer = await file.arrayBuffer();
    this._onAvatarSwap?.(buffer, "png");
    const name = file.name.replace(/\.png$/i, "");
    this._activeAvatarName = name;
    this._renderAvatarList(this._customAvatars);
    this._send({ type: "save_avatar", name, data_b64: _arrayBufferToBase64(buffer), kind: "png" });
  }

  // Same dropdown/edit/delete pattern as profiles/souls above, for saved
  // speech (TTS) engines (brain/tts_engines.py) -- "None" means no speech
  // engine at all (silence), any other entry is an HTTP endpoint (e.g.
  // Kokoro run via Docker) with an optional API key.
  _renderTtsEngineList(names, active) {
    this._ttsEngineNames = names;
    this._activeTtsEngineName = active;
    _renderDropdown(this.ttsEngineSelectEl, [NONE_TTS_ENGINE_NAME, ...names], active);
    this._updateTtsEngineEditControls();
    this._renderTtsLight();
    this._requestTtsVoices(active);
  }

  _loadTtsEngine(name) {
    if (!name) return;
    this._send({ type: "load_tts_engine", name });
    this._activeTtsEngineName = name;
    this._updateTtsEngineEditControls();
    this._renderTtsLight();
  }

  // Asks Brain for the engine's saved endpoint/api_key so the editor can
  // be pre-filled -- see _handleMessage's "tts_engine_content" case.
  _editTtsEngine(name) {
    this._pendingEditTtsEngineName = name;
    this._send({ type: "get_tts_engine", name });
  }

  _updateTtsEngineEditControls() {
    const disabled = this._harnessActive || this.ttsEngineSelectEl?.value === NONE_TTS_ENGINE_NAME;
    if (this.editTtsEngineButtonEl) this.editTtsEngineButtonEl.disabled = disabled;
    if (this.deleteTtsEngineButtonEl) this.deleteTtsEngineButtonEl.disabled = disabled;
  }

  _deleteTtsEngine(name) {
    if (!name || name === NONE_TTS_ENGINE_NAME) return;
    if (!window.confirm(`Delete the saved speech engine "${name}"? This can't be undone.`)) return;
    this._send({ type: "delete_tts_engine", name });
  }

  _openTtsEngineModal(name = "", endpoint = "", apiKey = "", voice = "", voicesDir = "", model = "") {
    if (!this.ttsEngineModalBackdropEl) return;
    if (this.ttsEngineModalTitleEl) {
      this.ttsEngineModalTitleEl.textContent = name ? "Edit Speech Engine" : "New Speech Engine";
    }
    if (this.ttsEngineNameEl) this.ttsEngineNameEl.value = name;
    if (this.ttsEngineEndpointEl) this.ttsEngineEndpointEl.value = endpoint;
    if (this.ttsEngineApiKeyEl) this.ttsEngineApiKeyEl.value = apiKey;
    if (this.ttsEngineVoiceEl) this.ttsEngineVoiceEl.value = voice;
    if (this.ttsEngineModelEl) this.ttsEngineModelEl.value = model;
    if (this.ttsEngineVoicesDirEl) this.ttsEngineVoicesDirEl.value = voicesDir;
    this.ttsEngineModalBackdropEl.hidden = false;
  }

  _closeTtsEngineModal() {
    if (this.ttsEngineModalBackdropEl) this.ttsEngineModalBackdropEl.hidden = true;
  }

  _saveTtsEngine() {
    const name = this.ttsEngineNameEl?.value.trim() || "";
    const endpoint = this.ttsEngineEndpointEl?.value.trim() || "";
    const apiKey = this.ttsEngineApiKeyEl?.value.trim() || "";
    const voice = this.ttsEngineVoiceEl?.value.trim() || "";
    const model = this.ttsEngineModelEl?.value.trim() || "";
    const voicesDir = this.ttsEngineVoicesDirEl?.value.trim() || "";
    if (!name || !endpoint) return;
    this._send({ type: "save_tts_engine", name, endpoint, api_key: apiKey, voice, model, voices_dir: voicesDir });
    this._closeTtsEngineModal();
  }

  // Asks Brain which custom voices exist for `name` (brain/kokoro_voices.py)
  // and whether creating another is even possible for it -- called
  // whenever the dropdown selection changes and whenever a fresh
  // `tts_engines` message names the real active engine, so the picker/
  // create-voice button always reflect whatever's actually selected
  // rather than the last one asked about. A no-op for "" or
  // NONE_TTS_ENGINE_NAME -- nothing to ask about.
  _requestTtsVoices(name) {
    if (!name || name === NONE_TTS_ENGINE_NAME) {
      this._renderTtsVoices({ name: name || "", voices: [], active_voice: "", can_create_voice: false });
      return;
    }
    this._send({ type: "get_tts_voices", name });
  }

  // Picker shown whenever at least one custom voice has been created for
  // this engine (an always-implied "Default" option plus each one);
  // hidden with zero -- no reason to show a picker with only one real
  // choice in it. Create-voice button shown/hidden independently, based
  // only on whether this engine has a voices folder configured at all
  // (data.can_create_voice), regardless of picker visibility.
  _renderTtsVoices(data) {
    this._ttsVoicesForEngine = data.name;
    if (this.createTtsVoiceButtonEl) this.createTtsVoiceButtonEl.hidden = !data.can_create_voice;
    if (!this.ttsVoiceSelectEl) return;
    if (!data.voices || data.voices.length === 0) {
      this.ttsVoiceSelectEl.hidden = true;
      return;
    }
    _renderDropdown(this.ttsVoiceSelectEl, [TTS_VOICE_DEFAULT_NAME, ...data.voices], data.active_voice || TTS_VOICE_DEFAULT_NAME);
    this.ttsVoiceSelectEl.hidden = false;
  }

  _setTtsVoice(voice) {
    const name = this._ttsVoicesForEngine;
    if (!name) return;
    this._send({ type: "set_tts_voice", name, voice: voice === TTS_VOICE_DEFAULT_NAME ? "" : voice });
  }

  _openKokoroBlendModal() {
    if (!this.kokoroBlendModalBackdropEl) return;
    if (this.kokoroBlendNameEl) this.kokoroBlendNameEl.value = "";
    if (this.kokoroBlendSpecEl) this.kokoroBlendSpecEl.value = "";
    this.kokoroBlendModalBackdropEl.hidden = false;
  }

  _closeKokoroBlendModal() {
    if (this.kokoroBlendModalBackdropEl) this.kokoroBlendModalBackdropEl.hidden = true;
  }

  // Sends Kokoro's own blend syntax straight through, unparsed -- Brain
  // relays it verbatim to the engine's own /v1/audio/voices/combine, so
  // whatever that server accepts (or rejects, via tts_voices' `error`)
  // is exactly what gets typed here.
  _createKokoroBlendVoice() {
    const name = this._ttsVoicesForEngine;
    const voiceName = this.kokoroBlendNameEl?.value.trim() || "";
    const spec = this.kokoroBlendSpecEl?.value.trim() || "";
    if (!name || !voiceName || !spec) return;
    this._send({ type: "combine_kokoro_voice", name, voice_name: voiceName, spec });
    this._closeKokoroBlendModal();
  }

  // Same dropdown/edit/delete pattern as speech engines above, for saved
  // LLM engines (brain/llm_engines.py) -- "Default" means config.yaml's
  // own brain.llm block, any other entry is an HTTP endpoint with an
  // optional model/api_key.
  _renderLlmEngineList(names, active) {
    this._llmEngineNames = names;
    this._activeLlmEngineName = active;
    _renderDropdown(this.llmEngineSelectEl, [NONE_LLM_ENGINE_NAME, ...names], active);
    this._updateLlmEngineEditControls();
  }

  _loadLlmEngine(name) {
    if (!name) return;
    this._send({ type: "load_llm_engine", name });
    this._activeLlmEngineName = name;
    this._updateLlmEngineEditControls();
  }

  // Asks Brain for the engine's saved endpoint/model/api_key so the
  // editor can be pre-filled -- see _handleMessage's "llm_engine_content" case.
  _editLlmEngine(name) {
    this._pendingEditLlmEngineName = name;
    this._send({ type: "get_llm_engine", name });
  }

  _updateLlmEngineEditControls() {
    const disabled = this._harnessActive || this.llmEngineSelectEl?.value === NONE_LLM_ENGINE_NAME;
    if (this.editLlmEngineButtonEl) this.editLlmEngineButtonEl.disabled = disabled;
    if (this.deleteLlmEngineButtonEl) this.deleteLlmEngineButtonEl.disabled = disabled;
  }

  _deleteLlmEngine(name) {
    if (!name || name === NONE_LLM_ENGINE_NAME) return;
    if (!window.confirm(`Delete the saved LLM engine "${name}"? This can't be undone.`)) return;
    this._send({ type: "delete_llm_engine", name });
  }

  _openLlmEngineModal(name = "", endpoint = "", model = "", apiKey = "", provider = "openai", think = false) {
    if (!this.llmEngineModalBackdropEl) return;
    if (this.llmEngineModalTitleEl) {
      this.llmEngineModalTitleEl.textContent = name ? "Edit LLM Engine" : "New LLM Engine";
    }
    if (this.llmEngineNameEl) this.llmEngineNameEl.value = name;
    if (this.llmEngineProviderEl) this.llmEngineProviderEl.value = provider;
    if (this.llmEngineEndpointEl) {
      this.llmEngineEndpointEl.value = endpoint;
      this.llmEngineEndpointEl.placeholder = provider === "ollama" ? "e.g. http://localhost:11434" : "e.g. http://localhost:1234/v1";
    }
    if (this.llmEngineModelEl) this.llmEngineModelEl.value = model;
    if (this.llmEngineApiKeyEl) this.llmEngineApiKeyEl.value = apiKey;
    if (this.llmEngineThinkToggleEl) this.llmEngineThinkToggleEl.checked = think;
    this._updateLlmEngineThinkVisibility();
    this._populateModelOptions([]); // clears stale options from whatever was open before
    this.llmEngineModalBackdropEl.hidden = false;
    if (endpoint) this._fetchLlmModels(); // editing an existing engine -- its endpoint is already known, no need to wait for blur
  }

  _closeLlmEngineModal() {
    if (this.llmEngineModalBackdropEl) this.llmEngineModalBackdropEl.hidden = true;
  }

  // Think only means anything for Ollama (see OllamaLLM's docstring --
  // every other provider here is OpenAI-compatible and silently ignores
  // it), so the row stays hidden unless that provider is actually picked,
  // rather than showing a control that would quietly do nothing.
  _updateLlmEngineThinkVisibility() {
    const isOllama = this.llmEngineProviderEl?.value === "ollama";
    if (this.llmEngineThinkRowEl) this.llmEngineThinkRowEl.hidden = !isOllama;
    if (this.llmEngineThinkHintEl) this.llmEngineThinkHintEl.hidden = !isOllama;
  }

  // Queries Brain for the models actually loaded at whatever endpoint is
  // currently typed (not necessarily saved yet) so the Model field can
  // offer real choices via its <datalist> instead of the user having to
  // guess a string -- see llm/client.py's list_models docstring for the
  // live crash (a guessed/empty value serializing to JSON null) this
  // replaces. Always re-sends (no "already fetched this one" guard) --
  // the explicit button is meant to work as a manual refresh too, e.g.
  // after loading a different model in LM Studio since the modal opened.
  // _pendingModelFetchEndpoint exists only so the *reply* can tell a
  // now-stale request apart from the latest one, not to skip requests.
  _fetchLlmModels() {
    const endpoint = this.llmEngineEndpointEl?.value.trim() || "";
    if (!endpoint) return;
    this._pendingModelFetchEndpoint = endpoint;
    this._send({
      type: "get_llm_models",
      endpoint,
      api_key: this.llmEngineApiKeyEl?.value.trim() || "",
      provider: this.llmEngineProviderEl?.value || "openai",
    });
  }

  _populateModelOptions(models) {
    if (!this.llmEngineModelOptionsEl) return;
    this.llmEngineModelOptionsEl.replaceChildren();
    for (const model of models) {
      const option = document.createElement("option");
      option.value = model;
      this.llmEngineModelOptionsEl.appendChild(option);
    }
  }

  _saveLlmEngine() {
    const name = this.llmEngineNameEl?.value.trim() || "";
    const provider = this.llmEngineProviderEl?.value || "openai";
    const endpoint = this.llmEngineEndpointEl?.value.trim() || "";
    const model = this.llmEngineModelEl?.value.trim() || "";
    const apiKey = this.llmEngineApiKeyEl?.value.trim() || "";
    const think = provider === "ollama" && !!this.llmEngineThinkToggleEl?.checked;
    if (!name || !endpoint) return;
    this._send({ type: "save_llm_engine", name, provider, endpoint, model, api_key: apiKey, think });
    this._closeLlmEngineModal();
  }

  // thumbnailUrl (a data URL) is only ever passed for a vision snapshot --
  // shown above the caption text so a "what is this" history entry still
  // means something on a later look back, unlike the voice message's
  // plain-text placeholder (there's nothing to show for that one).
  // Returns {bubbleEl, entry} so a caller can later swap in real text once
  // it's known asynchronously (see _sendRecording/user_transcript) --
  // right now only a voice message's placeholder needs this, since every
  // other entry already has its final text at the moment it's added.
  _addHistoryEntry(role, text, thumbnailUrl) {
    const timeText = new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    // Built before rendering (not after, the way this used to work) so the
    // retry button _renderHistoryEntry adds for a user entry can close
    // over this same object and read entry.text live at click time --
    // _updateHistoryEntryText mutates entry.text in place once a voice
    // message's real transcript arrives, and the button picks that up for
    // free instead of resending the stale "🎤 (voice message)" placeholder.
    const entry = { role, text, timeText };
    const bubbleEl = this._renderHistoryEntry(entry, thumbnailUrl);
    // Chat history lived in historyListEl's DOM only, with nothing behind
    // it -- a page refresh rebuilds that DOM from scratch, wiping the
    // panel even though Brain itself hasn't forgotten anything (its own
    // conversation memory is a separate, still-alive Python object; only a
    // Brain restart clears that). Persisting here keeps the visible panel
    // in sync with what she still actually remembers across an ordinary
    // reload. Thumbnails are deliberately NOT persisted (see
    // _persistHistoryEntry) -- restored image entries fall back to their
    // caption/placeholder text only.
    this._persistHistoryEntry(entry);
    const overlayBubbleEl = this._overlayChatActive ? this._addOverlayBubble(role, text) : null;
    return { bubbleEl, overlayBubbleEl, entry };
  }

  _readOverlayChatPref() {
    try {
      return localStorage.getItem(OVERLAY_CHAT_STORAGE_KEY) === "1";
    } catch {
      return false;
    }
  }

  _writeOverlayChatPref(active) {
    try {
      localStorage.setItem(OVERLAY_CHAT_STORAGE_KEY, active ? "1" : "0");
    } catch {
      // Nothing to fall back to -- the toggle just won't survive a reload.
    }
  }

  // Toggles the body class #subtitle/.overlay-bubble's CSS keys off of
  // (see style.css) and syncs the checkbox itself -- called both from the
  // toggle's own change handler and once up front at construction, so the
  // very first render already matches whatever was saved last time.
  _applyOverlayChatActive(active) {
    if (this.overlayChatToggleInputEl) this.overlayChatToggleInputEl.checked = active;
    document.body.classList.toggle("overlay-chat-active", active);
    // Whatever's currently floating has no other way to go away -- new
    // bubbles stop arriving the moment this is off (_addHistoryEntry only
    // calls _addOverlayBubble while active), and _trimOverlayStack only
    // ever runs when a new one is added, so without this, turning the
    // setting off mid-conversation would leave existing bubbles stuck on
    // screen forever instead of actually disappearing.
    if (!active && this.bubbleOverlayEl) this.bubbleOverlayEl.replaceChildren();
  }

  // Purely additive to the history panel, never a replacement for it --
  // the panel (and its persisted entries) is unaffected by any of this.
  // New bubbles stay put (no timer, no motion) until pushed out by
  // _trimOverlayStack once the stack grows past "her bustline".
  _addOverlayBubble(role, text) {
    if (!this.bubbleOverlayEl || !text) return null;
    const bubble = document.createElement("div");
    bubble.className = `overlay-bubble ${role}`;
    bubble.textContent = text;
    this.bubbleOverlayEl.appendChild(bubble);
    requestAnimationFrame(() => bubble.classList.add("visible")); // next frame, so the opacity transition actually plays instead of snapping straight to visible
    this._trimOverlayStack();
    return bubble;
  }

  // Fades out (then removes) the single oldest bubble once the stack's
  // rendered height exceeds OVERLAY_STACK_MAX_HEIGHT_PX, or its count
  // exceeds MAX_OVERLAY_BUBBLES -- stationary otherwise, nothing moves or
  // expires on its own. Re-invokes itself once that fade actually finishes
  // (transitionend), so a stack that's well over the limit (e.g. one very
  // long new message pushed it way past) cascades through however many
  // old bubbles need to go, one at a time, rather than just the first.
  // The `visible` class doubles as "not already fading out" -- checking it
  // here stops a bubble already on its way out from being targeted twice.
  _trimOverlayStack() {
    if (!this.bubbleOverlayEl) return;
    const oldest = this.bubbleOverlayEl.firstElementChild;
    if (!oldest || !oldest.classList.contains("visible")) return;
    const overCount = this.bubbleOverlayEl.children.length > MAX_OVERLAY_BUBBLES;
    const overHeight = this.bubbleOverlayEl.scrollHeight > OVERLAY_STACK_MAX_HEIGHT_PX;
    if (!overCount && !overHeight) return;
    oldest.classList.remove("visible");
    oldest.addEventListener(
      "transitionend",
      () => {
        oldest.remove();
        this._trimOverlayStack();
      },
      { once: true },
    );
  }

  _renderHistoryEntry(entry, thumbnailUrl) {
    if (!this.historyListEl) return null;
    const { role, text, timeText } = entry;
    const group = document.createElement("div");
    group.className = `history-group history-${role}`;

    const bubble = document.createElement("div");
    bubble.className = "history-bubble";
    if (thumbnailUrl) {
      const img = document.createElement("img");
      img.className = "history-thumbnail";
      img.src = thumbnailUrl;
      img.alt = "";
      bubble.appendChild(img);
    }
    if (text) bubble.appendChild(document.createTextNode(text));
    group.appendChild(bubble);

    const meta = document.createElement("div");
    meta.className = "history-meta";

    const time = document.createElement("div");
    time.className = "history-time";
    time.textContent = timeText;
    meta.appendChild(time);

    // Retry only makes sense for a real resendable prompt -- a thumbnail
    // means the original turn included an image, and only the caption
    // text (not the image itself) survives to resend, so this is left
    // off those rather than offering a control that quietly drops half
    // of what was actually asked. See _retryUserMessage's own comment for
    // why this lives on the user's bubble rather than her reply.
    if (role === "user" && text && !thumbnailUrl) {
      const retryButton = document.createElement("button");
      retryButton.className = "history-retry-button";
      retryButton.textContent = "↻";
      retryButton.title = "Resend this message";
      // Passes the entry object itself, not just its text -- _retryUserMessage
      // reads entry.text live (so a voice message's placeholder-to-transcript
      // swap, which mutates this same object, is picked up automatically even
      // if the swap happens after this button was already created) and also
      // needs the entry's identity/position to tell whether it's still the
      // most recent user message.
      retryButton.addEventListener("click", () => this._retryUserMessage(entry));
      meta.appendChild(retryButton);
    }

    group.appendChild(meta);

    this.historyListEl.appendChild(group);
    this.historyListEl.scrollTop = this.historyListEl.scrollHeight;
    return bubble;
  }

  // Keeps only the last MAX_STORED_HISTORY_ENTRIES entries (bounds both
  // localStorage size and how much re-render work a reload does) and
  // never stores the thumbnail data URL -- a handful of vision snapshots
  // at ~50-150KB each would risk blowing localStorage's ~5-10MB quota and
  // silently breaking persistence for plain text messages too. Wrapped in
  // try/catch same as the device-button prefs: losing this is harmless
  // (panel just starts empty next reload), not worth erroring the actual
  // send/reply flow over.
  _persistHistoryEntry(entry) {
    try {
      this._historyEntries.push(entry);
      while (this._historyEntries.length > MAX_STORED_HISTORY_ENTRIES) this._historyEntries.shift();
      localStorage.setItem(CHAT_HISTORY_STORAGE_KEY, JSON.stringify(this._historyEntries));
    } catch {
      // Nothing to fall back to -- history just won't survive this reload.
    }
  }

  _loadStoredHistory() {
    try {
      this._historyEntries = JSON.parse(localStorage.getItem(CHAT_HISTORY_STORAGE_KEY) || "[]");
    } catch {
      this._historyEntries = [];
    }
    for (const entry of this._historyEntries) this._renderHistoryEntry(entry, null);
  }

  // Swaps a voice message's "🎤 (voice message)" placeholder for the real
  // transcript once Brain's STT finishes and sends it back (see
  // _sendRecording, which hands back the {bubbleEl, entry} this expects,
  // and the "user_transcript" case in _handleMessage). Updates the live
  // DOM bubble and the persisted entry in place, then re-saves -- not
  // another _persistHistoryEntry call, that would push a second entry
  // instead of correcting this one.
  _updateHistoryEntryText(pending, text) {
    if (!pending) return;
    const { bubbleEl, overlayBubbleEl, entry } = pending;
    entry.text = text;
    if (bubbleEl) bubbleEl.textContent = text;
    // May already be gone (evicted by _trimOverlayStack while STT/LLM were
    // still working) -- updating a detached node is a harmless no-op, so
    // no need to check whether it's still attached.
    if (overlayBubbleEl) overlayBubbleEl.textContent = text;
    try {
      localStorage.setItem(CHAT_HISTORY_STORAGE_KEY, JSON.stringify(this._historyEntries));
    } catch {
      // Nothing to fall back to -- the visible bubble is already updated either way.
    }
  }

  // Clears the History panel (DOM + localStorage) and the floating
  // overlay bubbles (Chat Bubbles Over Avatar) together -- they're both
  // just views of the same conversation, so "clear chat" leaving one of
  // them still showing the old conversation would look like it half-
  // worked. Brain's own in-memory conversation history (client.py's
  // self._history, what she actually uses to keep the conversation
  // coherent) is untouched either way, same as an ordinary page reload
  // already leaves it untouched (see _addHistoryEntry's comment). This is
  // "tidy up what I'm looking at", not "make her forget" -- that's what
  // Restart Brain is for.
  _clearHistory() {
    this._historyEntries = [];
    if (this.historyListEl) this.historyListEl.innerHTML = "";
    if (this.bubbleOverlayEl) this.bubbleOverlayEl.innerHTML = "";
    try {
      localStorage.removeItem(CHAT_HISTORY_STORAGE_KEY);
    } catch {
      // Nothing to fall back to -- the DOM/in-memory clear above already happened either way.
    }
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
    const audioBuffer = await this.audioContext.decodeAudioData(_base64ToArrayBuffer(audioB64));
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
      this._logDebug("ws", `received non-JSON message (${raw.length} chars)`);
      return;
    }

    switch (data.type) {
      case "ping":
        this._send({ type: "pong" });
        break;
      case "set_expression":
        this._setMood(data.name, data.weight);
        break;
      case "user_transcript":
        this._updateHistoryEntryText(this._pendingAudioEntry, data.text);
        this._pendingAudioEntry = null;
        break;
      case "speak_text":
        // Held until speak_audio arrives with a decoded duration to pace
        // the streaming reveal against -- see _startSubtitleStream.
        this._pendingSpeakText = data.text;
        this._addHistoryEntry("glitch", data.text);
        this._clearSlowReplyTimer();
        this._setAwaitingReply(false); // her reply (or a "couldn't reach the LLM" stand-in, see main.py's _reply_to) has arrived either way
        if (this._pendingReplySentAt != null) {
          // Length only, never the reply text -- this is a timing
          // cross-check against Brain's own separately-logged llm/tts
          // debug_events, not a transcript.
          this._logDebug("ws", `reply received (${data.text.length} chars)`, Date.now() - this._pendingReplySentAt);
          this._pendingReplySentAt = null;
        }
        break;
      // Sent instead of speak_text when Brain had nothing to say (a blank
      // voice message, empty text) -- without this, _awaitingReply never
      // clears and Send/camera/desktop/mic stay grayed out forever with
      // nothing actually happening. Same cleanup as speak_text's case,
      // minus anything that assumes there's an actual reply to show.
      case "no_reply":
        this._clearSlowReplyTimer();
        this._setAwaitingReply(false);
        // Without this, no_reply was completely silent -- no bubble, no
        // status, nothing -- which reads exactly like the app is broken,
        // especially after a long wait (a slow empty LLM reply is the
        // most common real cause; see main.py's _reply_to). A brief
        // status is honest about what actually happened instead of
        // leaving it looking like nothing did.
        this._setStatus("(no reply)");
        setTimeout(() => this._setStatus(""), 4000);
        if (this._pendingReplySentAt != null) {
          this._logDebug("ws", "no reply (nothing to say)", Date.now() - this._pendingReplySentAt);
          this._pendingReplySentAt = null;
        }
        break;
      case "speak_audio":
        // Not awaited (the switch itself isn't async) -- catch here
        // instead, so malformed/undecodable audio surfaces as a console
        // warning rather than an unhandled promise rejection.
        this._playAudio(data.audio_b64).catch((err) => {
          console.warn("[brain] couldn't play audio:", err);
          this._logDebug("audio", `playback failed: ${err.message || err}`);
        });
        break;
      case "viseme_stream":
        this.visemeFrames = data.frames || [];
        break;
      case "roleplay_state":
        this._roleplayActive = !!data.active;
        this._renderRoleplayToggle();
        break;
      case "voice_state":
        if (this.voiceToggleInputEl) this.voiceToggleInputEl.checked = !!data.active;
        break;
      case "memory_state":
        if (this.memoryToggleInputEl) this.memoryToggleInputEl.checked = !!data.active;
        break;
      case "memory_provider_state":
        if (this.memoryProviderSelectEl) this.memoryProviderSelectEl.value = data.provider || "local";
        this._renderMemoryProviderVisibility(data.provider || "local");
        break;
      case "hindsight_config":
        // Pre-fills the fields, but not while the user has one of them
        // focused (mid-edit, about to Save) -- same reasoning as
        // harness_state's own switch-key field, so a config broadcast
        // triggered by another device can't yank out what's being typed.
        if (this.hindsightApiUrlEl && document.activeElement !== this.hindsightApiUrlEl) this.hindsightApiUrlEl.value = data.api_url || "";
        if (this.hindsightApiKeyEl && document.activeElement !== this.hindsightApiKeyEl) this.hindsightApiKeyEl.value = data.api_key || "";
        if (this.hindsightBankIdEl && document.activeElement !== this.hindsightBankIdEl) this.hindsightBankIdEl.value = data.bank_id || "";
        break;
      case "memory_content":
        if (this._pendingMemoryDownload) {
          this._pendingMemoryDownload = false;
          this._downloadMemory(data.entries || []);
        }
        break;
      case "soul_and_user_content":
        if (this._pendingSoulUserEditorOpen) {
          this._pendingSoulUserEditorOpen = false;
          this._openSoulUserEditorModal(data.soul || "", data.user || "");
        }
        break;
      case "notes_content":
        if (this._pendingNotesOpen) {
          this._pendingNotesOpen = false;
          this._openNotesModal(data.content || "");
        }
        break;
      case "memory_learned":
        this._showMemoryToast(data.fact);
        this._addHistoryEntry("system", `🧠 Learned: ${data.fact}`);
        break;
      case "profiles":
        this._renderProfileList(data.names || [], data.active || DEFAULT_PROFILE_NAME);
        break;
      case "profile_content":
        if (data.name === this._pendingEditName) {
          this._pendingEditName = null;
          this._openProfileModal(data.name, data.content);
        }
        break;
      case "souls":
        this._renderSoulList(data.names || [], data.active || DEFAULT_SOUL_NAME);
        break;
      case "soul_content":
        if (data.name === this._pendingEditSoulName) {
          this._pendingEditSoulName = null;
          this._openSoulModal(data.name, data.description, data.examples);
        }
        break;
      case "avatars":
        this._renderAvatarList(data.avatars || []);
        break;
      case "avatar_data":
        // Sent either in reply to our own load_avatar, or unprompted
        // right after `ready` to restore a previously active custom
        // avatar (protocol.md) -- both cases handled the same way.
        // `data.kind` ("vrm" | "png") is Brain's own saved record, not
        // something this client has to infer from the file it originally
        // uploaded.
        this._onAvatarSwap?.(_base64ToArrayBuffer(data.data_b64), data.kind);
        this._logDebug("avatar", `switched to avatar '${data.name}' (${data.kind})`);
        this._activeAvatarName = data.name;
        this._renderAvatarList(this._customAvatars);
        break;
      case "tts_engines":
        this._renderTtsEngineList(data.names || [], data.active || NONE_TTS_ENGINE_NAME);
        break;
      case "tts_engine_content":
        if (data.name === this._pendingEditTtsEngineName) {
          this._pendingEditTtsEngineName = null;
          this._openTtsEngineModal(data.name, data.endpoint, data.api_key, data.voice, data.voices_dir, data.model);
        }
        break;
      case "tts_voices":
        // Stale replies (the user already switched engines by the time
        // this arrived) are dropped -- only paint the picker/upload
        // button for whichever engine is currently selected.
        if (data.name === (this.ttsEngineSelectEl?.value || NONE_TTS_ENGINE_NAME)) {
          this._renderTtsVoices(data);
        }
        if (data.error) {
          this._setStatus(data.error);
          setTimeout(() => this._setStatus(""), 6000);
        }
        break;
      case "llm_engines":
        this._renderLlmEngineList(data.names || [], data.active || NONE_LLM_ENGINE_NAME);
        break;
      case "llm_engine_content":
        if (data.name === this._pendingEditLlmEngineName) {
          this._pendingEditLlmEngineName = null;
          this._openLlmEngineModal(data.name, data.endpoint, data.model, data.api_key, data.provider || "openai", !!data.think);
        }
        break;
      case "harness_state":
        this._harnessActive = !!data.active;
        this._activeHarnessName = data.name || "";
        this._selectedHarnessName = data.selected || "";
        this._harnessAvailable = data.available || [];
        this._harnessSwitchKey = data.switch_key || "";
        // Pre-fill the settings field with the current value -- but not
        // while the user has it focused (mid-edit, about to Save their
        // own change), so a harness_state arriving from some other cause
        // (e.g. toggling on/off) can't yank out what they're typing.
        if (this.harnessSwitchKeyEl && document.activeElement !== this.harnessSwitchKeyEl) {
          this.harnessSwitchKeyEl.value = this._harnessSwitchKey;
        }
        this._renderHarnessState(); // also re-renders the dropdown itself, see its own comment
        break;
      case "harness_content":
        if (data.name === this._pendingEditHarnessName) {
          this._pendingEditHarnessName = null;
          this._openHarnessModal(data.name, data.endpoint, data.model, data.api_key);
        }
        break;
      case "harness_health":
        // Not !!data.reachable -- that would coerce null (not configured
        // at all, see protocol.py's harness_health) into false (checked
        // and it's down), collapsing grey into red. Store it as-is so
        // _renderHarnessLight's own === true/=== false/undefined-or-null
        // checks can actually tell the three states apart.
        this._harnessReachableByName[data.name] = data.reachable;
        this._renderHarnessLight();
        break;
      case "tts_health":
        this._ttsReachableByName[data.name] = data.reachable;
        this._renderTtsLight();
        break;
      case "llm_models":
        if (data.endpoint === this._pendingModelFetchEndpoint) {
          this._populateModelOptions(data.models || []);
        }
        break;
      case "play_animation":
        console.warn("[brain] play_animation not yet implemented:", data);
        this._send({ type: "error", message: `play_animation not yet implemented: ${data.name}` });
        break;
      case "debug_pong":
        // Real Renderer<->Brain round-trip time -- distinct from the
        // existing ping/pong (Brain-initiated, measures its own send
        // interval, not actual network latency). Only meaningful while
        // debugging is on, which is also the only time this ever gets sent.
        this._logDebug("ws", "round-trip time", Date.now() - data.ts);
        break;
      case "debug_event":
        // Brain's own account of its external calls (LLM/TTS/STT/harness)
        // -- category/message/ms only, see protocol.md's debug_event.
        this._logDebug(data.category, data.message, data.ms);
        break;
      default:
        console.warn("[brain] ignoring unknown message type:", data.type);
        this._logDebug("ws", `received unknown message type: ${data.type}`);
    }
  }

  // The Settings panel's Debugging toggle -- see index.html's
  // debug-section and this._debugLog's own comment. Turning it on tells
  // Brain to start sending this connection debug_events (main.py's
  // _DEBUG_CONNECTIONS) and starts a periodic debug_ping for
  // Renderer<->Brain round-trip time; turning it off stops both, but
  // deliberately does NOT clear anything already captured -- the whole
  // point is being able to toggle it off right after reproducing a
  // problem and then still download what was caught.
  _setDebugActive(active) {
    this._debugActive = active;
    this._send({ type: "set_debug_active", active });
    this._logDebug("client", active ? "debugging enabled" : "debugging disabled");
    if (active) {
      this._startDebugPingLoop();
    } else {
      this._stopDebugPingLoop();
    }
  }

  _startDebugPingLoop() {
    this._stopDebugPingLoop(); // never double-schedule if this is somehow called twice
    this._debugPingTimer = setInterval(() => this._send({ type: "debug_ping", ts: Date.now() }), DEBUG_PING_INTERVAL_MS);
  }

  _stopDebugPingLoop() {
    clearInterval(this._debugPingTimer);
    this._debugPingTimer = null;
  }

  // Appends one entry to the debug log -- a no-op whenever debugging is
  // off, so none of the call sites sprinkled through this file need their
  // own "only if debugging" check. `message` must never be conversation
  // content (user_text/speak_text text, profile/soul content, etc) --
  // only connection state, message types, sizes/lengths, timing, and
  // error text, matching exactly what the Settings panel's hint text
  // promises this toggle does and doesn't capture.
  _logDebug(category, message, ms) {
    if (!this._debugActive) return;
    this._debugLog.push({ ts: Date.now(), category, message, ms });
    if (this._debugLog.length > MAX_DEBUG_LOG_ENTRIES) this._debugLog.shift();
  }

  _clearMemory() {
    if (!window.confirm("Clear everything Glitch remembers about you? This can't be undone.")) return;
    this._send({ type: "clear_memory" });
  }

  _downloadMemory(entries) {
    const header = [`Glitch's memory of you -- exported ${new Date().toISOString()}`, `${entries.length} fact${entries.length === 1 ? "" : "s"}`, ""];
    const blob = new Blob([header.concat(entries.map((e) => `- ${e}`)).join("\n") + "\n"], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `glitch-memory-${Date.now()}.txt`;
    a.click();
    URL.revokeObjectURL(url);
  }

  // Serializes the captured log to a plain-text file and triggers a
  // normal browser download -- works even if debugging is currently off
  // (downloads whatever was captured before it was turned off) or if the
  // log is empty (downloads a near-empty file, harmless).
  _downloadDebugLog() {
    const header = [
      `Glitch debug log -- exported ${new Date().toISOString()}`,
      `Connection state at export: ${this._connectionState}`,
      `${this._debugLog.length} entr${this._debugLog.length === 1 ? "y" : "ies"}`,
      "",
    ];
    const lines = this._debugLog.map((entry) => {
      const time = new Date(entry.ts).toISOString();
      const ms = entry.ms != null ? ` (${entry.ms.toFixed(1)}ms)` : "";
      return `[${time}] [${entry.category}] ${entry.message}${ms}`;
    });
    const blob = new Blob([header.concat(lines).join("\n") + "\n"], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `glitch-debug-log-${Date.now()}.txt`;
    a.click();
    URL.revokeObjectURL(url); // the click() above is synchronous, so the blob URL is no longer needed the instant it returns
  }

  // The Settings panel's Restart Brain button. Confirmed first (like the
  // profile/soul/engine delete buttons) since it drops her current
  // conversation history -- unlike those, there's nothing to undo by
  // picking a different option afterward, just a several-second wait.
  // Grays out the whole settings panel for the duration (see
  // _setSettingsRestarting) since every setting in it reads from the
  // Brain that's about to disappear and come back fresh.
  _restartBrain() {
    if (!window.confirm("Restart Glitch's Brain? This ends her current conversation history and takes a few seconds -- she'll reconnect automatically.")) {
      return;
    }
    this._logDebug("brain", "restart requested");
    this._setSettingsRestarting(true);
    this._send({ type: "restart_brain" });
  }

  _setSettingsRestarting(restarting) {
    this.settingsContentEl?.classList.toggle("restarting", restarting);
    clearTimeout(this._restartTimeoutTimer);
    this._restartTimeoutTimer = null;
    // Only arm the safety-net timeout when actually starting a restart --
    // clearing it here too (restarting: false) means a normal reconnect
    // finishing on time doesn't leave a stale timer waiting to fire a
    // redundant no-op later.
    if (restarting) {
      this._restartTimeoutTimer = setTimeout(() => this._setSettingsRestarting(false), RESTART_TIMEOUT_MS);
    }
  }
}

// Shared by the profile/soul/avatar dropdowns -- building the <option>
// list and selecting the active one is identical for all three (each
// already guarantees a non-empty names list itself, via its own reserved
// "Default"/"Glitch" first entry, so there's no empty-list case to
// special-case here).
function _renderDropdown(selectEl, names, activeName) {
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

// Diagnostic helper for _grabFrameFromStream's own debug log line -- a
// cheap sampled average (not every pixel) of how bright the just-captured
// canvas actually is, 0 (solid black) to 255 (solid white). Sampling
// every ~200th pixel is plenty to tell "genuinely black" apart from "has
// real image content" without the cost of walking a full-resolution frame.
function _sampleAvgBrightness(ctx, width, height) {
  try {
    const { data } = ctx.getImageData(0, 0, width, height);
    const stride = 4 * Math.max(1, Math.floor(width * height / 200));
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

function _arrayBufferToBase64(buffer) {
  // Chunked rather than one String.fromCharCode call per byte -- fine for
  // a few seconds of mic audio, but a multi-megabyte VRM import (avatars
  // can be 15MB+) made the naive per-byte version a real, noticeable UI
  // freeze. 0x8000 stays well under String.fromCharCode.apply's argument-
  // count limit while still batching most of the work into large appends.
  const bytes = new Uint8Array(buffer);
  const CHUNK_SIZE = 0x8000;
  let binary = "";
  for (let i = 0; i < bytes.length; i += CHUNK_SIZE) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK_SIZE));
  }
  return btoa(binary);
}

function _base64ToArrayBuffer(base64) {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes.buffer;
}
