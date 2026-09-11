import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
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

const canvas = document.getElementById("scene");
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
renderer.setPixelRatio(window.devicePixelRatio);
renderer.setSize(window.innerWidth, window.innerHeight);

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x6a87ad);

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
  new THREE.MeshStandardMaterial({ color: 0x2a2e3a })
);
floor.rotation.x = -Math.PI / 2;
scene.add(floor);

window.addEventListener("resize", () => {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
});

statusEl.textContent = "Loading avatar...";
const vrm = await loadAvatar("/Glitch.vrm");
scene.add(vrm.scene);
window.__vrm = vrm; // for console-driven verification while building
statusEl.textContent = "";

const idle = new IdleController(vrm);
idle.relaxPose();
window.__idle = idle;

const brain = new BrainClient({
  url: import.meta.env.VITE_BRAIN_WS_URL,
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
});
brain.connect();
window.__brain = brain; // for console-driven verification while building

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
