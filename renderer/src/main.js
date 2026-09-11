import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { VRMUtils } from "@pixiv/three-vrm";
import { loadAvatar } from "./avatar.js";
import { IdleController } from "./idle.js";
import { BrainClient } from "./brain_client.js";

const statusEl = document.getElementById("status");
const subtitleEl = document.getElementById("subtitle");
const inputEl = document.getElementById("message-input");
const sendButtonEl = document.getElementById("send-button");
const micButtonEl = document.getElementById("mic-button");
const micLabelEl = document.getElementById("mic-label");
const historyButtonEl = document.getElementById("history-button");
const historyPanelEl = document.getElementById("history-panel");
const historyListEl = document.getElementById("history-list");
const connectionLightEl = document.getElementById("connection-light");
const settingsButtonEl = document.getElementById("settings-button");
const settingsPanelEl = document.getElementById("settings-panel");
const profileSelectEl = document.getElementById("profile-select");
const editProfileButtonEl = document.getElementById("edit-profile-button");
const newProfileButtonEl = document.getElementById("new-profile-button");
const profileModalBackdropEl = document.getElementById("profile-modal-backdrop");
const profileModalTitleEl = document.getElementById("profile-modal-title");
const profileNameEl = document.getElementById("profile-name");
const profileContentEl = document.getElementById("profile-content");
const profileSaveButtonEl = document.getElementById("profile-save-button");
const profileCancelButtonEl = document.getElementById("profile-cancel-button");
const soulSelectEl = document.getElementById("soul-select");
const editSoulButtonEl = document.getElementById("edit-soul-button");
const newSoulButtonEl = document.getElementById("new-soul-button");
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

// `source` matches loadAvatar's own contract: a URL string (the shipped
// default) or an ArrayBuffer (a custom avatar's raw .vrm bytes, from
// either a WS-delivered avatar_data message or a user-imported File).
// Used for the initial boot load below and again by BrainClient's
// onAvatarSwap callback whenever the user picks or imports a different
// one -- a completely different model can arrive mid-session, so this
// has to fully replace the old scene graph and animation state, not
// just mutate the existing vrm in place.
async function setActiveAvatar(source) {
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

brain = new BrainClient({
  url: import.meta.env.VITE_BRAIN_WS_URL,
  vrm,
  statusEl,
  subtitleEl,
  inputEl,
  sendButtonEl,
  micButtonEl,
  micLabelEl,
  historyListEl,
  connectionLightEl,
  profileSelectEl,
  editProfileButtonEl,
  newProfileButtonEl,
  profileModalBackdropEl,
  profileModalTitleEl,
  profileNameEl,
  profileContentEl,
  profileSaveButtonEl,
  profileCancelButtonEl,
  soulSelectEl,
  editSoulButtonEl,
  newSoulButtonEl,
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
  idle.update(delta);
  brain.update(delta);
  vrm.update(delta);
  controls.update();
  renderer.render(scene, camera);
}
animate();
