import * as THREE from "three";

// Idle behavior: relax the T-pose bind into a natural stance, then a small
// looping idle (breathing, weight-shift sway, head turn, blink).

const BREATH_AMPLITUDE = 0.008;
const BREATH_SPEED = 0.35;
const SWAY_AMPLITUDE = (1.2 * Math.PI) / 180;
const SWAY_SPEED = 0.12;
const HEAD_TURN_AMPLITUDE = (3.0 * Math.PI) / 180;
const HEAD_TURN_SPEED = 0.09;
// While she talks her head dips a little with her voice -- a small nod on the louder
// syllables, eased so it reads as emphasis rather than a twitch.
const TALK_NOD_AMPLITUDE = (4.0 * Math.PI) / 180;
const TALK_NOD_EASE_SEC = 0.15;

// Gestures, picked from what she's saying (pickGesture) as each sentence starts: moves
// layered over the idle, one at a time. [seconds, move at t (0-1 through it, side +-1)]
// -> degrees of head pitch (+ = down), yaw, roll, and shoulder lift.
const GESTURES = {
  nod: [0.9, (t) => ({ pitch: 9 * Math.sin(2 * Math.PI * t) ** 2 })],
  shake: [1.1, (t) => ({ yaw: 11 * Math.sin(4 * Math.PI * t) * Math.sin(Math.PI * t) })],
  tilt: [1.6, (t, side) => ({ roll: 9 * side * hold(t) })],
  shrug: [1.3, (t, side) => ({ shoulders: 10 * hold(t), roll: 4 * side * hold(t), pitch: -3 * hold(t) })],
};
// First match wins. ponytail: plain word matching -- a "[gesture: x]" tag in her prompt if it reads mechanical.
const GESTURE_WORDS = [
  ["shrug", /\b(?:I don['’]?t know|dunno|no idea|who knows|not sure|I guess)\b/i],
  ["shake", /^\W*(?:no|nah|nope|not really)\b/i],
  ["nod", /^\W*(?:yes|yeah|yep|yup|sure|of course|absolutely|exactly|definitely|totally|right)\b/i],
  ["tilt", /\?\W*$/],
];

export function pickGesture(text) {
  return GESTURE_WORDS.find(([, words]) => words.test(text))?.[0] ?? null;
}

// Eases in over the first quarter, holds, eases out over the last.
function hold(t) {
  const x = Math.min(1, t / 0.25, (1 - t) / 0.25);
  return x * x * (3 - 2 * x);
}

const BLINK_MIN_DELAY = 2.0;
const BLINK_MAX_DELAY = 5.0;
const BLINK_CLOSE_TIME = 0.08;
const BLINK_HOLD_TIME = 0.03;
const BLINK_OPEN_TIME = 0.12;

export class IdleController {
  constructor(vrm) {
    this.vrm = vrm;
    this.humanoid = vrm.humanoid;
    this.expressionManager = vrm.expressionManager;

    this.breathPhase = 0;
    this.swayPhase = 0;
    this.headPhase = 0;
    this.talk = 0; // her voice level, eased (see update)
    this.gestureName = null;
    this.gestureTime = 0;
    this.gestureSide = 1;

    this.blinkTimer = this._randomBlinkDelay();
    this.blinkPhase = null; // null | "closing" | "holding" | "opening"
    this.blinkElapsed = 0;

    this.chestBone = this.humanoid.getNormalizedBoneNode("chest");
    this.headBone = this.humanoid.getNormalizedBoneNode("head");
    this.chestRestY = this.chestBone ? this.chestBone.position.y : 0;
    this.chestRestQuat = this.chestBone ? this.chestBone.quaternion.clone() : null;
    this.headRestQuat = this.headBone ? this.headBone.quaternion.clone() : null;
    // [bone, side sign, rest] -- a model without shoulder bones just shrugs with its head
    this.shoulders = [["left", 1], ["right", -1]]
      .map(([side, sign]) => [this.humanoid.getNormalizedBoneNode(`${side}Shoulder`), sign])
      .filter(([bone]) => bone)
      .map(([bone, sign]) => [bone, sign, bone.quaternion.clone()]);
  }

  gesture(name) {
    if (!GESTURES[name] || this.gestureName) return; // a new sentence doesn't cut one short
    this.gestureName = name;
    this.gestureTime = 0;
    this.gestureSide = Math.random() < 0.5 ? -1 : 1;
  }

  _gestureMove(delta) {
    if (!this.gestureName) return {};
    const [seconds, move] = GESTURES[this.gestureName];
    this.gestureTime += delta;
    if (this.gestureTime < seconds) return move(this.gestureTime / seconds, this.gestureSide);
    this.gestureName = null;
    return {};
  }

  relaxPose() {
    // Arms down close to her sides, not a T-pose bind: the upper arm drops to
    // about 11 degrees from vertical (at 75 her hands hung ~21 cm out from her
    // legs), and the elbow bends forward, not outward, so the hands rest just in
    // front of her hips instead of sinking into them.
    const upperArmAngle = (-79 * Math.PI) / 180;
    const elbowForward = (-18 * Math.PI) / 180;

    for (const side of ["left", "right"]) {
      const sign = side === "left" ? 1 : -1;
      const upperArm = this.humanoid.getNormalizedBoneNode(`${side}UpperArm`);
      if (upperArm) upperArm.rotation.z = sign * upperArmAngle;

      const lowerArm = this.humanoid.getNormalizedBoneNode(`${side}LowerArm`);
      if (lowerArm) lowerArm.rotation.y = sign * elbowForward;
    }

    this._curlFingers();
  }

  _curlFingers() {
    // More curl at the knuckle, less at the fingertip; mirrored per side.
    const fingerCurlDeg = { Proximal: 22, Intermediate: 18, Distal: 12 };
    // The thumb swings in toward the index finger (y) as well as curling (z),
    // so it rests along the fingers instead of jutting out forward.
    const thumbDeg = { Metacarpal: [-25, 5], Proximal: [-10, 10], Distal: [-5, 6] };

    for (const side of ["left", "right"]) {
      const sign = side === "left" ? -1 : 1;
      for (const finger of ["index", "middle", "ring", "little"]) {
        for (const [segment, deg] of Object.entries(fingerCurlDeg)) {
          this._curlJoint(`${side}${finger[0].toUpperCase()}${finger.slice(1)}${segment}`, deg * sign);
        }
      }
      for (const [segment, [swingDeg, curlDeg]] of Object.entries(thumbDeg)) {
        const bone = this.humanoid.getNormalizedBoneNode(`${side}Thumb${segment}`);
        if (bone) bone.rotation.set(0, (swingDeg * sign * Math.PI) / 180, (curlDeg * sign * Math.PI) / 180);
      }
    }
  }

  _curlJoint(boneName, deg) {
    const bone = this.humanoid.getNormalizedBoneNode(boneName);
    if (bone) bone.rotation.z = (deg * Math.PI) / 180;
  }

  // speechLevel: how open her mouth is right now, 0-1 (BrainClient.speechLevel).
  update(delta, speechLevel = 0) {
    const move = this._gestureMove(delta);
    if (this.chestBone) {
      this.breathPhase += delta * BREATH_SPEED * Math.PI * 2;
      this.chestBone.position.y = this.chestRestY + Math.sin(this.breathPhase) * BREATH_AMPLITUDE;

      this.swayPhase += delta * SWAY_SPEED * Math.PI * 2;
      const swayAngle = Math.sin(this.swayPhase) * SWAY_AMPLITUDE;
      this.chestBone.quaternion
        .copy(this.chestRestQuat)
        .multiply(_swayQuat.setFromAxisAngle(_axisZ, swayAngle));
    }

    if (this.headBone) {
      this.headPhase += delta * HEAD_TURN_SPEED * Math.PI * 2;
      const turnAngle = Math.sin(this.headPhase) * HEAD_TURN_AMPLITUDE;
      this.talk += (speechLevel - this.talk) * Math.min(delta / TALK_NOD_EASE_SEC, 1);
      this.headBone.quaternion
        .copy(this.headRestQuat)
        .multiply(_swayQuat.setFromAxisAngle(_axisY, turnAngle))
        .multiply(_nodQuat.setFromAxisAngle(_axisX, this.talk * TALK_NOD_AMPLITUDE))
        .multiply(_nodQuat.setFromEuler(_euler.set(deg(move.pitch), deg(move.yaw), deg(move.roll))));
    }
    for (const [bone, sign, rest] of this.shoulders) {
      bone.quaternion.copy(rest).multiply(_swayQuat.setFromAxisAngle(_axisZ, sign * deg(move.shoulders)));
    }

    this._updateBlink(delta);
  }

  _updateBlink(delta) {
    if (this.blinkPhase === null) {
      this.blinkTimer -= delta;
      if (this.blinkTimer <= 0) {
        this.blinkPhase = "closing";
        this.blinkElapsed = 0;
      }
      return;
    }

    this.blinkElapsed += delta;
    let weight = 0;
    if (this.blinkPhase === "closing") {
      weight = Math.min(this.blinkElapsed / BLINK_CLOSE_TIME, 1);
      if (this.blinkElapsed >= BLINK_CLOSE_TIME) {
        this.blinkPhase = "holding";
        this.blinkElapsed = 0;
      }
    } else if (this.blinkPhase === "holding") {
      weight = 1;
      if (this.blinkElapsed >= BLINK_HOLD_TIME) {
        this.blinkPhase = "opening";
        this.blinkElapsed = 0;
      }
    } else if (this.blinkPhase === "opening") {
      weight = Math.max(1 - this.blinkElapsed / BLINK_OPEN_TIME, 0);
      if (this.blinkElapsed >= BLINK_OPEN_TIME) {
        this.blinkPhase = null;
        this.blinkTimer = this._randomBlinkDelay();
        weight = 0;
      }
    }

    this.expressionManager?.setValue("blink", weight);
  }

  _randomBlinkDelay() {
    return BLINK_MIN_DELAY + Math.random() * (BLINK_MAX_DELAY - BLINK_MIN_DELAY);
  }
}

const deg = (d = 0) => (d * Math.PI) / 180;
const _euler = new THREE.Euler();
const _swayQuat = new THREE.Quaternion();
const _nodQuat = new THREE.Quaternion();
const _axisX = new THREE.Vector3(1, 0, 0);
const _axisY = new THREE.Vector3(0, 1, 0);
const _axisZ = new THREE.Vector3(0, 0, 1);
