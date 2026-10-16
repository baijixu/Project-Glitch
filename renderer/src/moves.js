// Her moves: Mixamo animations (FBX, "Without Skin") from assets/animations/ -- gitignored, since
// Mixamo's files can't be passed on -- retargeted onto her VRM the way three-vrm's own humanoid
// animation example does it, and played over her procedural idle (idle.js), which they replace while
// they run. A clip that isn't there just doesn't play: she stands and breathes as before.

import * as THREE from "three";
import { FBXLoader } from "three/addons/loaders/FBXLoader.js";

// Mixamo's bone names -> VRM humanoid bones (three-vrm's mixamoVRMRigMap).
const RIG = {
  mixamorigHips: "hips", mixamorigSpine: "spine", mixamorigSpine1: "chest", mixamorigSpine2: "upperChest",
  mixamorigNeck: "neck", mixamorigHead: "head",
  mixamorigLeftShoulder: "leftShoulder", mixamorigLeftArm: "leftUpperArm", mixamorigLeftForeArm: "leftLowerArm", mixamorigLeftHand: "leftHand",
  mixamorigRightShoulder: "rightShoulder", mixamorigRightArm: "rightUpperArm", mixamorigRightForeArm: "rightLowerArm", mixamorigRightHand: "rightHand",
  mixamorigLeftUpLeg: "leftUpperLeg", mixamorigLeftLeg: "leftLowerLeg", mixamorigLeftFoot: "leftFoot", mixamorigLeftToeBase: "leftToes",
  mixamorigRightUpLeg: "rightUpperLeg", mixamorigRightLeg: "rightLowerLeg", mixamorigRightFoot: "rightFoot", mixamorigRightToeBase: "rightToes",
};
for (const side of ["Left", "Right"]) {
  for (const [mixamo, vrm] of [["Thumb", "Thumb"], ["Index", "Index"], ["Middle", "Middle"], ["Ring", "Ring"], ["Pinky", "Little"]]) {
    const segments = mixamo === "Thumb" ? ["Metacarpal", "Proximal", "Distal"] : ["Proximal", "Intermediate", "Distal"];
    segments.forEach((segment, i) => (RIG[`mixamorig${side}Hand${mixamo}${i + 1}`] = `${side.toLowerCase()}${vrm}${segment}`));
  }
}

const FADE_SEC = 0.5;
// Clips that would carry her across the floor: kept on the spot (the hips' sideways and forward drift
// taken out), since Walker moves her body itself, along the path it chose.
const IN_PLACE = new Set(["Walking", "Running"]);

export class Moves {
  constructor(vrm) {
    this.vrm = vrm;
    this.mixer = new THREE.AnimationMixer(vrm.scene);
    this.clips = new Map(); // name -> its AnimationClip (null if it isn't there), as a promise while it loads
    this.action = null;
    this.name = null;
    // Her hips' height standing, measured now, before any clip has moved them: a clip's hips are
    // scaled to it (measured mid-clip, a walk loaded while she sat put her feet through the floor).
    const v = new THREE.Vector3();
    this.hipsHeight = Math.abs(vrm.humanoid.getNormalizedBoneNode("hips").getWorldPosition(v).y - vrm.scene.getWorldPosition(v).y);
  }

  get playing() {
    return this.action !== null;
  }

  // Plays `name` (a file in assets/animations/, without .fbx), crossfading from whatever played before.
  // False if there's no such clip.
  async play(name, { once = false } = {}) {
    if (name === this._want) return true;
    this._want = name;
    const clip = await this._clip(name);
    if (!clip || this._want !== name) return false; // not there, or asked for something else meanwhile
    const action = this.mixer.clipAction(clip);
    action.reset().setLoop(once ? THREE.LoopOnce : THREE.LoopRepeat, Infinity);
    action.clampWhenFinished = once;
    action.fadeIn(FADE_SEC).play();
    this.action?.fadeOut(FADE_SEC);
    this.action = action;
    this.name = name;
    return true;
  }

  // Back to her procedural idle: the clip lets go, her bones go back to rest, and idle.js
  // (relaxPose) takes over again.
  stop(idle) {
    this._want = null;
    if (!this.action) return;
    this.mixer.stopAllAction();
    this.action = this.name = null;
    this.vrm.humanoid.resetNormalizedPose();
    idle.relaxPose();
  }

  // Whether `name` is there to play (loads it).
  async has(name) {
    return !!(await this._clip(name));
  }

  // After idle.update: a playing clip wins over the procedural pose.
  update(delta) {
    if (this.action) this.mixer.update(delta);
  }

  _clip(name) {
    if (!this.clips.has(name)) {
      this.clips.set(name, new FBXLoader().loadAsync(`/animations/${encodeURIComponent(name)}.fbx`).then(
        (asset) => retarget(asset, this.vrm, this.hipsHeight, IN_PLACE.has(name)),
        () => null, // not downloaded: don't ask again
      ));
    }
    return this.clips.get(name);
  }
}

// A Mixamo clip as one for her normalized bones: each rotation is taken out of Mixamo's rest pose
// into hers, and the hips' movement scaled to her height (three-vrm's loadMixamoAnimation).
function retarget(asset, vrm, vrmHips, inPlace = false) {
  const clip = THREE.AnimationClip.findByName(asset.animations, "mixamo.com") || asset.animations[0];
  const restInverse = new THREE.Quaternion();
  const parentRest = new THREE.Quaternion();
  const q = new THREE.Quaternion();
  const hipsScale = vrmHips / asset.getObjectByName("mixamorigHips").position.y;
  const v0 = vrm.meta?.metaVersion === "0"; // VRM 0.x faces the other way
  const tracks = [];
  for (const track of clip.tracks) {
    const [rigName, property] = track.name.split(".");
    const node = vrm.humanoid.getNormalizedBoneNode(RIG[rigName])?.name;
    const rig = asset.getObjectByName(rigName);
    if (!node || !rig) continue;
    rig.getWorldQuaternion(restInverse).invert();
    rig.parent.getWorldQuaternion(parentRest);
    if (track instanceof THREE.QuaternionKeyframeTrack) {
      const values = track.values.slice();
      for (let i = 0; i < values.length; i += 4) {
        q.fromArray(values, i).premultiply(parentRest).multiply(restInverse).toArray(values, i);
      }
      tracks.push(new THREE.QuaternionKeyframeTrack(`${node}.${property}`, track.times, values.map((x, i) => (v0 && i % 2 === 0 ? -x : x))));
    } else if (track instanceof THREE.VectorKeyframeTrack) {
      const values = track.values.map((x, i) => (inPlace && i % 3 !== 1 ? track.values[i % 3] : x)); // first frame's x/z throughout
      tracks.push(new THREE.VectorKeyframeTrack(`${node}.${property}`, track.times, values.map((x, i) => (v0 && i % 3 !== 1 ? -x : x) * hipsScale)));
    }
  }
  return new THREE.AnimationClip(clip.name, clip.duration, tracks);
}
