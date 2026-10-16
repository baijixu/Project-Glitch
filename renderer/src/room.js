// Her room (assets/room.glb, built in Blender to Josh's floor plan): drawn around her in the same
// cel-shaded look as her VRM, with a camera that watches from the corner while she's idle and eases
// in to her face while she talks. She goes where her Brain's work takes her -- the desk to read or
// write, the bed to daydream (Life) -- with Mixamo clips if they're there (moves.js). Per device
// (Settings -> UI Style): it's drawn on the GPU she thinks on.

import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";

export const ROOM_PREF = "glitch_room";
// The rug she stands on, in the room's own coordinates -- the room is moved so it's under her.
const HER_SPOT = new THREE.Vector3(0.65, 0, -0.8);
// Camera shots, around her: the room's near-right corner looking across to the bed and desk, and the
// close-up the page always had.
const SHOTS = {
  room: { position: new THREE.Vector3(1.1, 1.75, 2.65), target: new THREE.Vector3(-0.6, 0.95, -1.0), fov: 55 },
  face: { position: new THREE.Vector3(0, 1.25, 1.7), target: new THREE.Vector3(0, 1.3, 0), fov: 30 },
};
// Where she can be, around her spot on the rug: where she stands, sits or lies, which way she turns,
// her clip there and while talking (none: her procedural idle), what the corner camera looks at while
// she's there, and her close-up there -- at the desk from just above the laptop, like its webcam.
export const SPOTS = {
  rug: { at: new THREE.Vector3(0, 0, 0), turn: 0, idle: null, talk: null, view: SHOTS.room.target, face: SHOTS.face },
  desk: {
    at: new THREE.Vector3(-1.4, 0, -1.35), turn: Math.PI, idle: "Sitting", talk: "Sitting Talking", view: new THREE.Vector3(-1.1, 0.9, -1.5),
    face: { position: new THREE.Vector3(-1.4, 1.04, -2.05), target: new THREE.Vector3(-1.4, 1.14, -1.4), fov: 45 },
  },
  bed: {
    at: new THREE.Vector3(-1.69, -0.3, 1.35), turn: Math.PI / 2, idle: "Laying Idle", talk: "Laying Hand Gesture", view: new THREE.Vector3(-1.5, 0.6, 0.9),
    face: { position: new THREE.Vector3(-1.45, 1.45, 0.75), target: new THREE.Vector3(-2.15, 0.68, 1.35), fov: 40 },
  },
};
// Her Brain's status line (hub.doing) -> where she goes to do it.
const AT_DESK = /^(Reading|Writing|Picking a book|Asking|Hearing back|Working out|Taking her|Tidying|Going over|Noting|Remembering)/;
const ON_BED = /^(Daydreaming|Choosing|Looking|Thinking about)/;
const BACK_TO_RUG_SEC = 180; // nothing to do this long, and she gets up

// Where she is: the spot her latest work took her to, until she's had nothing to do for a while.
export class Life {
  constructor() {
    this.spot = "rug";
    this._busy = false;
    this._idleFor = 0;
  }

  // Her Brain's status line changed ("" when she's done). Overlapping jobs are joined with " · ".
  doing(text) {
    const first = text.split(" · ")[0];
    const spot = AT_DESK.test(first) ? "desk" : ON_BED.test(first) ? "bed" : null;
    if (spot) this.spot = spot;
    this._busy = !!spot;
    this._idleFor = 0;
  }

  update(delta) {
    if (this._busy || this.spot === "rug") return;
    this._idleFor += delta;
    if (this._idleFor > BACK_TO_RUG_SEC) this.spot = "rug";
  }
}

const EASE_PER_SEC = 2.5; // about a second to settle into a shot
const HOLD_SEC = 4; // quiet this long after talking before the camera pulls back -- not between sentences

export async function loadRoom(scene) {
  const room = (await new GLTFLoader().loadAsync("/room.glb")).scene;
  const gradientMap = toonGradient();
  room.traverse((object) => {
    if (!object.isMesh) return;
    const { color, emissive, emissiveIntensity } = object.material;
    object.material = new THREE.MeshToonMaterial({ color, emissive, emissiveIntensity, gradientMap });
    object.receiveShadow = true; // her shadow lands on the floor and the rug
  });
  // The nightstand lamp: warm and close, no shadow of its own (one shadow-casting light is enough work).
  const lamp = new THREE.PointLight(0xffc98a, 0.5, 2.5, 2);
  lamp.position.set(-1.75, 0.95, -0.55);
  room.add(lamp);
  room.position.copy(HER_SPOT).negate();
  scene.add(room);
  return room;
}

// Three light steps, like her MToon shading, instead of a smooth falloff.
function toonGradient() {
  const texture = new THREE.DataTexture(new Uint8Array([110, 185, 255]), 3, 1, THREE.RedFormat);
  texture.minFilter = texture.magFilter = THREE.NearestFilter;
  texture.needsUpdate = true;
  return texture;
}

// Moves the camera only while it's easing into a new shot, so he can still orbit in between.
export class RoomCamera {
  constructor(camera, controls) {
    this.camera = camera;
    this.controls = controls;
    this.shot = null;
    this._easing = false;
    this._quietFor = Infinity;
  }

  update(delta, talking, spot = "rug") {
    // She moved: cut to the new view, as she did -- easing across the room flies through the furniture.
    const cut = spot !== this._spot;
    this._spot = spot;
    this._quietFor = talking ? 0 : this._quietFor + delta;
    const want = this._quietFor < HOLD_SEC ? spot : `room:${spot}`;
    if (want !== this.shot) {
      this.shot = want;
      this._easing = true;
    }
    const shot = want === spot ? SPOTS[spot].face : { ...SHOTS.room, target: SPOTS[spot].view };
    if (this._easing) this._ease(shot, cut ? 1 : 1 - Math.exp(-EASE_PER_SEC * delta));
  }

  // Straight to the close-up, e.g. when the room is switched off.
  reset() {
    this.shot = null;
    this._easing = false;
    this._ease(SHOTS.face, 1);
  }

  _ease(shot, k) {
    this.camera.position.lerp(shot.position, k);
    this.controls.target.lerp(shot.target, k);
    this.camera.fov += (shot.fov - this.camera.fov) * k;
    this.camera.updateProjectionMatrix();
    if (this.camera.position.distanceTo(shot.position) < 0.005 && Math.abs(shot.fov - this.camera.fov) < 0.05) this._easing = false;
  }
}
