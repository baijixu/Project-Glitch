import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { VRMUtils } from "@pixiv/three-vrm";
import { loadAvatar } from "./avatar.js";
import { IdleController } from "./idle.js";
import { BrainClient } from "./brain_client.js";

const statusEl = document.getElementById("status");
const historyButtonEl = document.getElementById("history-button");
const historyPanelEl = document.getElementById("history-panel");
const settingsButtonEl = document.getElementById("settings-button");
const settingsPanelEl = document.getElementById("settings-panel");
const avatarImageEl = document.getElementById("avatar-image");

const canvas = document.getElementById("scene");
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
renderer.setPixelRatio(window.devicePixelRatio);
renderer.setSize(window.innerWidth, window.innerHeight);
// Shadows: she casts one on the floor and on herself (hair on her face, arms on
// her body). Soft-edged, from the one directional light below.
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;

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

// Her toon (MToon) shading shows lit areas at texture color x light strength,
// plus the ambient light on top -- past 100% bright colors clip toward white.
// At 1.0 ambient + 1.5 directional she looked washed out; these keep her
// texture colors close to how they were painted.
scene.add(new THREE.AmbientLight(0xffffff, 0.5));
const dirLight = new THREE.DirectionalLight(0xffffff, 1.1);
dirLight.position.set(1.5, 3, 2);
dirLight.castShadow = true;
// The shadow only needs to cover her and the floor around her feet; a tight box
// keeps it sharp. The biases stop the toon shading from speckling itself.
dirLight.shadow.mapSize.set(2048, 2048);
Object.assign(dirLight.shadow.camera, { left: -1.2, right: 1.2, top: 2.2, bottom: -0.4, near: 0.5, far: 8 });
dirLight.shadow.bias = -0.0005;
dirLight.shadow.normalBias = 0.02;
dirLight.shadow.radius = 3;
scene.add(dirLight);

// A faint glow on the floor under her, fading to the black background, so her
// shadow has somewhere to show (a shadow on a black floor is invisible).
const floor = new THREE.Mesh(new THREE.PlaneGeometry(4, 4), new THREE.MeshStandardMaterial({ map: floorGlowTexture() }));
floor.rotation.x = -Math.PI / 2;
floor.receiveShadow = true;
scene.add(floor);

function floorGlowTexture() {
  const size = 256;
  const glowCanvas = document.createElement("canvas");
  glowCanvas.width = glowCanvas.height = size;
  const ctx = glowCanvas.getContext("2d");
  const gradient = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
  gradient.addColorStop(0, "#2a2d38");
  gradient.addColorStop(0.35, "#15161c");
  gradient.addColorStop(1, "#000000");
  ctx.fillStyle = gradient;
  ctx.fillRect(0, 0, size, size);
  const texture = new THREE.CanvasTexture(glowCanvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}

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
  newVrm.scene.traverse((object) => {
    if (object.isMesh) {
      object.castShadow = true;
      object.receiveShadow = true;
    }
  });
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
  onAvatarSwap: setActiveAvatar,
});
brain.connect();
window.__brain = brain; // for console-driven verification while building

// Page-wide errors into the debug log (when it's on), so a Renderer crash
// shows up in a downloaded log instead of only in the browser's console.
// Message and location only.
window.addEventListener("error", (event) => {
  const where = event.filename ? ` at ${event.filename.split("/").pop()}:${event.lineno}` : "";
  brain.log("client", `error: ${event.message}${where}`);
});
// The first tap or key press unlocks sound (see BrainClient._playClip), so her
// first reply isn't silent on a browser that starts audio suspended.
const unlockAudio = () => {
  if (brain.audioContext?.state === "suspended") brain.audioContext.resume().catch(() => {});
};
window.addEventListener("pointerdown", unlockAudio, { once: true, capture: true });
window.addEventListener("keydown", unlockAudio, { once: true, capture: true });
window.addEventListener("unhandledrejection", (event) => {
  const reason = event.reason instanceof Error ? event.reason.message : String(event.reason);
  brain.log("client", `unhandled promise rejection: ${reason.slice(0, 200)}`);
});

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
