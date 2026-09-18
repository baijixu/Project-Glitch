import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { VRMUtils } from "@pixiv/three-vrm";
import { loadAvatar } from "./avatar.js";
import { IdleController } from "./idle.js";
import { BrainClient } from "./brain_client.js";

const statusEl = document.getElementById("status");
const subtitleEl = document.getElementById("subtitle");
const bubbleOverlayEl = document.getElementById("bubble-overlay");
const overlayChatToggleInputEl = document.getElementById("overlay-chat-toggle");
const memoryToastEl = document.getElementById("memory-toast");
const inputEl = document.getElementById("message-input");
const chatBarEl = document.getElementById("chat-bar");
const sideActionsEl = document.getElementById("side-actions");
const sendButtonEl = document.getElementById("send-button");
const overlayResendButtonEl = document.getElementById("overlay-resend-button");
const fileUploadButtonEl = document.getElementById("file-upload-button");
const fileUploadInputEl = document.getElementById("file-upload-input");
const cameraVisionButtonEl = document.getElementById("camera-vision-button");
const desktopVisionButtonEl = document.getElementById("desktop-vision-button");
const micButtonEl = document.getElementById("mic-button");
const voiceToggleInputEl = document.getElementById("voice-toggle");
const webSearchToggleInputEl = document.getElementById("web-search-toggle");
const cameraEnabledToggleInputEl = document.getElementById("camera-enabled-toggle");
const desktopCaptureEnabledToggleInputEl = document.getElementById("desktop-capture-enabled-toggle");
const micEnabledToggleInputEl = document.getElementById("mic-enabled-toggle");
const micAlwaysOnToggleInputEl = document.getElementById("mic-always-on-toggle");
const memoryToggleInputEl = document.getElementById("memory-toggle");
const memoryProviderSelectEl = document.getElementById("memory-provider-select");
const hindsightConfigFieldsEl = document.getElementById("hindsight-config-fields");
const hindsightApiUrlEl = document.getElementById("hindsight-api-url");
const hindsightApiKeyEl = document.getElementById("hindsight-api-key");
const hindsightBankIdEl = document.getElementById("hindsight-bank-id");
const saveHindsightConfigButtonEl = document.getElementById("save-hindsight-config-button");
const downloadMemoryButtonEl = document.getElementById("download-memory-button");
const clearMemoryButtonEl = document.getElementById("clear-memory-button");
const openSoulUserEditorButtonEl = document.getElementById("open-soul-user-editor-button");
const soulUserEditorModalBackdropEl = document.getElementById("soul-user-editor-modal-backdrop");
const soulUserEditorSoulEl = document.getElementById("soul-user-editor-soul");
const soulUserEditorUserEl = document.getElementById("soul-user-editor-user");
const soulUserEditorSaveButtonEl = document.getElementById("soul-user-editor-save-button");
const soulUserEditorCancelButtonEl = document.getElementById("soul-user-editor-cancel-button");
const micLabelEl = document.getElementById("mic-label");
const historyButtonEl = document.getElementById("history-button");
const historyPanelEl = document.getElementById("history-panel");
const historyListEl = document.getElementById("history-list");
const resendLastButtonEl = document.getElementById("resend-last-button");
const clearHistoryButtonEl = document.getElementById("clear-chat-history-button");
const connectionLightEl = document.getElementById("connection-light");
const harnessLightEl = document.getElementById("harness-light");
const ttsLightEl = document.getElementById("tts-light");
const settingsButtonEl = document.getElementById("settings-button");
const settingsPanelEl = document.getElementById("settings-panel");
const settingsContentEl = document.getElementById("settings-content");
const roleplayToggleInputEl = document.getElementById("roleplay-toggle");
const roleplayBadgeEl = document.getElementById("roleplay-badge");
const profileOptionsEl = document.getElementById("profile-options");
const soulSectionEl = document.getElementById("soul-section");
const profileSelectEl = document.getElementById("profile-select");
const editProfileButtonEl = document.getElementById("edit-profile-button");
const newProfileButtonEl = document.getElementById("new-profile-button");
const deleteProfileButtonEl = document.getElementById("delete-profile-button");
const profileModalBackdropEl = document.getElementById("profile-modal-backdrop");
const profileModalTitleEl = document.getElementById("profile-modal-title");
const profileNameEl = document.getElementById("profile-name");
const profileIdentityEl = document.getElementById("profile-identity");
const profileScenarioEl = document.getElementById("profile-scenario");
const profileSaveButtonEl = document.getElementById("profile-save-button");
const profileCancelButtonEl = document.getElementById("profile-cancel-button");
const soulSelectEl = document.getElementById("soul-select");
const editSoulButtonEl = document.getElementById("edit-soul-button");
const newSoulButtonEl = document.getElementById("new-soul-button");
const deleteSoulButtonEl = document.getElementById("delete-soul-button");
const soulModalBackdropEl = document.getElementById("soul-modal-backdrop");
const soulModalTitleEl = document.getElementById("soul-modal-title");
const soulNameEl = document.getElementById("soul-name");
const soulDescriptionEl = document.getElementById("soul-description");
const soulExamplesEl = document.getElementById("soul-examples");
const soulSaveButtonEl = document.getElementById("soul-save-button");
const soulCancelButtonEl = document.getElementById("soul-cancel-button");
const avatarSelectEl = document.getElementById("avatar-select");
const importAvatarButtonEl = document.getElementById("import-avatar-button");
const avatarFileInputEl = document.getElementById("avatar-file-input");
const importAvatarPngButtonEl = document.getElementById("import-avatar-png-button");
const avatarPngFileInputEl = document.getElementById("avatar-png-file-input");
const avatarImageEl = document.getElementById("avatar-image");
const ttsEngineSelectEl = document.getElementById("tts-engine-select");
const editTtsEngineButtonEl = document.getElementById("edit-tts-engine-button");
const newTtsEngineButtonEl = document.getElementById("new-tts-engine-button");
const deleteTtsEngineButtonEl = document.getElementById("delete-tts-engine-button");
const ttsEngineModalBackdropEl = document.getElementById("tts-engine-modal-backdrop");
const ttsEngineModalTitleEl = document.getElementById("tts-engine-modal-title");
const ttsEngineNameEl = document.getElementById("tts-engine-name");
const ttsEngineEndpointEl = document.getElementById("tts-engine-endpoint");
const ttsEngineApiKeyEl = document.getElementById("tts-engine-api-key");
const ttsEngineVoiceEl = document.getElementById("tts-engine-voice");
const ttsEngineModelEl = document.getElementById("tts-engine-model");
const ttsEngineVoicesDirEl = document.getElementById("tts-engine-voices-dir");
const ttsEngineSaveButtonEl = document.getElementById("tts-engine-save-button");
const ttsEngineCancelButtonEl = document.getElementById("tts-engine-cancel-button");
const ttsVoiceSelectEl = document.getElementById("tts-voice-select");
const createTtsVoiceButtonEl = document.getElementById("create-tts-voice-button");
const kokoroBlendModalBackdropEl = document.getElementById("kokoro-blend-modal-backdrop");
const kokoroBlendNameEl = document.getElementById("kokoro-blend-name");
const kokoroBlendSpecEl = document.getElementById("kokoro-blend-spec");
const kokoroBlendCancelButtonEl = document.getElementById("kokoro-blend-cancel-button");
const kokoroBlendCreateButtonEl = document.getElementById("kokoro-blend-create-button");
const llmEngineSelectEl = document.getElementById("llm-engine-select");
const editLlmEngineButtonEl = document.getElementById("edit-llm-engine-button");
const newLlmEngineButtonEl = document.getElementById("new-llm-engine-button");
const deleteLlmEngineButtonEl = document.getElementById("delete-llm-engine-button");
const llmEngineModalBackdropEl = document.getElementById("llm-engine-modal-backdrop");
const llmEngineModalTitleEl = document.getElementById("llm-engine-modal-title");
const llmEngineNameEl = document.getElementById("llm-engine-name");
const llmEngineProviderEl = document.getElementById("llm-engine-provider");
const llmEngineEndpointEl = document.getElementById("llm-engine-endpoint");
const llmEngineModelEl = document.getElementById("llm-engine-model");
const llmEngineModelOptionsEl = document.getElementById("llm-engine-model-options");
const fetchLlmModelsButtonEl = document.getElementById("fetch-llm-models-button");
const llmEngineApiKeyEl = document.getElementById("llm-engine-api-key");
const llmEngineThinkRowEl = document.getElementById("llm-engine-think-row");
const llmEngineThinkHintEl = document.getElementById("llm-engine-think-hint");
const llmEngineThinkToggleEl = document.getElementById("llm-engine-think-toggle");
const llmEngineSaveButtonEl = document.getElementById("llm-engine-save-button");
const llmEngineCancelButtonEl = document.getElementById("llm-engine-cancel-button");
const harnessToggleInputEl = document.getElementById("harness-toggle");
const harnessSelectEl = document.getElementById("harness-select");
const editHarnessButtonEl = document.getElementById("edit-harness-button");
const newHarnessButtonEl = document.getElementById("new-harness-button");
const deleteHarnessButtonEl = document.getElementById("delete-harness-button");
const harnessLockableEl = document.getElementById("harness-lockable");
const harnessConfirmModalBackdropEl = document.getElementById("harness-confirm-modal-backdrop");
const harnessConfirmModalTextEl = document.getElementById("harness-confirm-modal-text");
const harnessConfirmCancelButtonEl = document.getElementById("harness-confirm-cancel-button");
const harnessConfirmConnectButtonEl = document.getElementById("harness-confirm-connect-button");
const harnessModalBackdropEl = document.getElementById("harness-modal-backdrop");
const harnessModalTitleEl = document.getElementById("harness-modal-title");
const harnessNameEl = document.getElementById("harness-name");
const harnessEndpointEl = document.getElementById("harness-endpoint");
const harnessModelEl = document.getElementById("harness-model");
const harnessApiKeyEl = document.getElementById("harness-api-key");
const harnessModalCancelButtonEl = document.getElementById("harness-modal-cancel-button");
const harnessModalSaveButtonEl = document.getElementById("harness-modal-save-button");
const roleplayConfirmModalBackdropEl = document.getElementById("roleplay-confirm-modal-backdrop");
const roleplayConfirmThinkToggleEl = document.getElementById("roleplay-confirm-think-toggle");
const roleplayConfirmCancelButtonEl = document.getElementById("roleplay-confirm-cancel-button");
const roleplayConfirmEnableButtonEl = document.getElementById("roleplay-confirm-enable-button");
const debugToggleInputEl = document.getElementById("debug-toggle");
const downloadDebugLogButtonEl = document.getElementById("download-debug-log-button");
const restartBrainButtonEl = document.getElementById("restart-brain-button");
const openNotesButtonEl = document.getElementById("open-notes-button");
const notesModalBackdropEl = document.getElementById("notes-modal-backdrop");
const notesTextareaEl = document.getElementById("notes-textarea");
const notesSaveButtonEl = document.getElementById("notes-save-button");
const notesCancelButtonEl = document.getElementById("notes-cancel-button");

const canvas = document.getElementById("scene");
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
renderer.setPixelRatio(window.devicePixelRatio);
renderer.setSize(window.innerWidth, window.innerHeight);

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x000000);

const camera = new THREE.PerspectiveCamera(30, window.innerWidth / window.innerHeight, 0.1, 20);
camera.position.set(0, 1.25, 1.7);
camera.lookAt(0, 1.3, 0);

const controls = new OrbitControls(camera, renderer.domElement);
controls.target.set(0, 1.3, 0);
controls.enableDamping = true;
controls.dampingFactor = 0.1;
controls.minDistance = 0.3;
controls.maxDistance = 4.5;
controls.maxPolarAngle = Math.PI / 2 + 0.3;
controls.update();

scene.add(new THREE.AmbientLight(0xffffff, 1.0));
const dirLight = new THREE.DirectionalLight(0xffffff, 1.5);
dirLight.position.set(1.5, 3, 2);
scene.add(dirLight);

const floor = new THREE.Mesh(
  new THREE.PlaneGeometry(4, 4),
  new THREE.MeshStandardMaterial({ color: 0x000000 })
);
floor.rotation.x = -Math.PI / 2;
scene.add(floor);

function handleViewportResize() {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
}
window.addEventListener("resize", handleViewportResize);
// window's own "resize" doesn't fire reliably for a phone's on-screen
// keyboard opening/closing on every browser -- visualViewport's is the
// event actually meant for this (paired with index.html's
// interactive-widget=resizes-content, which is what makes
// window.innerWidth/innerHeight report the real post-keyboard size in
// the first place; without it this would just be listening for an
// accurate signal that never arrives).
window.visualViewport?.addEventListener("resize", handleViewportResize);

// vrm/idle are mutable (not const) -- setActiveAvatar reassigns both on
// every swap, and animate()'s closure below always reads the current
// value since it's a plain outer-scope reference, not a snapshot taken
// once at startup.
let vrm = null;
let idle = null;
// Declared before BrainClient exists (setActiveAvatar is used for the
// very first, pre-BrainClient load too) and guarded inside the function
// below -- there's nothing to retarget yet on that first call.
let brain = null;

// "vrm" (the 3D scene, driven every frame below) or "png" (a flat
// reference image -- nothing to animate, orbit, or lipsync/mood-express
// against). Read by animate() to decide whether this frame does any
// idle/lipsync/expression work and orbit/render at all, and by the
// OrbitControls setup below to decide whether panning is even possible.
let avatarKind = "vrm";
// The object URL backing avatarImageEl.src for an imported (not
// server-restored-by-path) .png -- revoked and replaced on every swap so
// repeated avatar changes don't leak one blob URL per import the way an
// unrevoked one would.
let avatarImageObjectUrl = null;

// `source`/`kind` match loadAvatar's own contract for the "vrm" case (a
// URL string for the shipped default, or an ArrayBuffer of raw .vrm bytes
// from either a WS-delivered avatar_data message or a user-imported
// File) -- "png" instead takes the same two source shapes but for raw
// image bytes/a path, displayed directly rather than parsed as a model.
// Used for the initial boot load below and again by BrainClient's
// onAvatarSwap callback whenever the user picks or imports a different
// avatar -- a completely different one can arrive mid-session, so this
// has to fully replace whatever's currently showing, not just mutate it
// in place.
async function setActiveAvatar(source, kind = "vrm") {
  avatarKind = kind;
  // A flat image has nothing to orbit around, and the user shouldn't be
  // able to pan it either way -- OrbitControls stays fully disabled
  // rather than just not being useful, so it can't intercept pointer
  // events meant for anything else layered underneath.
  controls.enabled = kind === "vrm";

  if (kind === "png") {
    if (avatarImageObjectUrl) URL.revokeObjectURL(avatarImageObjectUrl);
    avatarImageObjectUrl = typeof source === "string" ? null : URL.createObjectURL(new Blob([source], { type: "image/png" }));
    avatarImageEl.src = avatarImageObjectUrl || source;
    avatarImageEl.hidden = false;
    canvas.hidden = true;
    // Deliberately NOT touching vrm/idle/scene here -- whatever 3D avatar
    // was loaded before stays fully intact underneath, just not rendered
    // (see animate()'s own avatarKind check), so switching back to a .vrm
    // later doesn't have to reload anything if it's the same one.
    return;
  }

  if (avatarImageObjectUrl) {
    URL.revokeObjectURL(avatarImageObjectUrl);
    avatarImageObjectUrl = null;
  }
  avatarImageEl.hidden = true;
  canvas.hidden = false;

  statusEl.textContent = "Loading avatar...";
  const newVrm = await loadAvatar(source);

  if (vrm) {
    scene.remove(vrm.scene);
    VRMUtils.deepDispose(vrm.scene); // frees the old model's GPU geometry/textures -- repeated swaps would otherwise leak
  }
  scene.add(newVrm.scene);
  vrm = newVrm;

  idle = new IdleController(vrm);
  idle.relaxPose();

  if (brain) brain.vrm = vrm; // BrainClient reads this.vrm fresh each call (lipsync/mood) -- a plain reassignment is enough to retarget it
  window.__vrm = vrm; // for console-driven verification while building
  window.__idle = idle;
  statusEl.textContent = "";
}

await setActiveAvatar("/Glitch.vrm");

// Defaults to Vite's own dev-server WebSocket proxy (see vite.config.js's
// "/brain-ws" entry) at whatever host/protocol this page was actually
// loaded from, rather than a hardcoded LAN IP that needed hand-editing in
// .env every time the machine's address changed. This also means a wss://
// page (required for camera/mic access from any device but localhost --
// see vite.config.js) never has to open a separate, un-clickable-through
// insecure ws:// connection to Brain directly. VITE_BRAIN_WS_URL still
// works as an explicit override (e.g. Brain running on a different,
// unproxied machine).
const defaultBrainWsUrl = `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/brain-ws`;

brain = new BrainClient({
  url: import.meta.env.VITE_BRAIN_WS_URL || defaultBrainWsUrl,
  authToken: import.meta.env.VITE_BRAIN_AUTH_TOKEN,
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
  webSearchToggleInputEl,
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
  profileIdentityEl,
  profileScenarioEl,
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
  debugToggleInputEl,
  downloadDebugLogButtonEl,
  restartBrainButtonEl,
  openNotesButtonEl,
  notesModalBackdropEl,
  notesTextareaEl,
  notesSaveButtonEl,
  notesCancelButtonEl,
  settingsContentEl,
  onAvatarSwap: setActiveAvatar,
});
brain.connect();
window.__brain = brain; // for console-driven verification while building

// Only one of the two slide-out panels should be open at a time -- they'd
// otherwise physically overlap in the same corner of the screen.
function togglePanel(panelEl, otherPanelEl) {
  const opening = !panelEl.classList.contains("open");
  panelEl.classList.toggle("open", opening);
  if (opening) otherPanelEl.classList.remove("open");
}
historyButtonEl?.addEventListener("click", () => togglePanel(historyPanelEl, settingsPanelEl));
settingsButtonEl?.addEventListener("click", () => togglePanel(settingsPanelEl, historyPanelEl));

// Tapping her closes whichever slide-panel is open -- there's no
// swipe-to-dismiss here the way a phone's own sheets work, and tapping
// the content behind an open panel is the next most natural instinct to
// reach for instead. A modal's own backdrop already has this same
// click-outside-to-close behavior; the slide-panels never did, since
// they have no backdrop element of their own to click on -- whichever of
// canvas/avatarImageEl is actually visible stands in for one here.
// Both get the listener (only one is ever shown at a time, see
// setActiveAvatar's canvas.hidden/avatarImageEl.hidden toggle) rather
// than just canvas, which is fully hidden -- and so never receives any
// clicks at all -- for a flat .png avatar.
function closeOpenPanels() {
  historyPanelEl?.classList.remove("open");
  settingsPanelEl?.classList.remove("open");
}
canvas.addEventListener("click", closeOpenPanels);
avatarImageEl?.addEventListener("click", closeOpenPanels);

// Deliberately NOT using THREE.Timer's Page Visibility integration
// (timer.connect(document)): it zeroes delta to exactly 0 for the entire
// time document.hidden is true, which doesn't just "avoid a huge jump on
// refocus" -- it freezes every delta-driven animation (idle breathing/sway,
// mood expression crossfade) for as long as the page is backgrounded, and
// forever if it never receives a visibilitychange transition back to
// visible. Confirmed hitting this directly: mood weight stayed pinned at
// exactly 0 across 2s of active playback while testing in an automated
// browser context that reports document.hidden = true persistently -- the
// same failure mode a real pywebview window losing OS focus could hit.
// Capping delta directly gets the one thing that integration was actually
// for (no runaway jump after a real long pause) without silently pausing
// her.
const MAX_DELTA_SEC = 0.1;
const timer = new THREE.Timer();
function animate() {
  requestAnimationFrame(animate);
  timer.update();
  const delta = Math.min(timer.getDelta(), MAX_DELTA_SEC);
  // A .png avatar has no bones/expressions/orbit to animate at all -- skip
  // idle/lipsync-mood/orbit/render entirely rather than doing that work
  // against a hidden canvas nobody sees (see setActiveAvatar's "png" case).
  if (avatarKind === "vrm") {
    idle.update(delta);
    brain.update(delta);
    vrm.update(delta);
    controls.update();
    renderer.render(scene, camera);
  }
}
animate();
