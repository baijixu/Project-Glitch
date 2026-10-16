// Her room (assets/room.glb, built in Blender to Josh's floor plan): drawn around her in the same
// cel-shaded look as her VRM, with a camera that watches from the corner while she's idle and eases
// in to her face while she talks. Per device (Settings -> UI Style): it's drawn on the GPU she thinks on.

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

  update(delta, talking) {
    this._quietFor = talking ? 0 : this._quietFor + delta;
    const want = this._quietFor < HOLD_SEC ? "face" : "room";
    if (want !== this.shot) {
      this.shot = want;
      this._easing = true;
    }
    if (this._easing) this._ease(SHOTS[this.shot], 1 - Math.exp(-EASE_PER_SEC * delta));
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
