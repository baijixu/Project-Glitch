import * as THREE from "three";

// Idle behavior: relax the T-pose bind into a natural stance, then a small
// looping idle (breathing, weight-shift sway, head turn, blink).

const BREATH_AMPLITUDE = 0.008;
const BREATH_SPEED = 0.35;
const SWAY_AMPLITUDE = (1.2 * Math.PI) / 180;
const SWAY_SPEED = 0.12;
const HEAD_TURN_AMPLITUDE = (3.0 * Math.PI) / 180;
const HEAD_TURN_SPEED = 0.09;

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

    this.blinkTimer = this._randomBlinkDelay();
    this.blinkPhase = null; // null | "closing" | "holding" | "opening"
    this.blinkElapsed = 0;

    this.chestBone = this.humanoid.getNormalizedBoneNode("chest");
    this.headBone = this.humanoid.getNormalizedBoneNode("head");
    this.chestRestY = this.chestBone ? this.chestBone.position.y : 0;
    this.chestRestQuat = this.chestBone ? this.chestBone.quaternion.clone() : null;
    this.headRestQuat = this.headBone ? this.headBone.quaternion.clone() : null;
  }

  relaxPose() {
    // Arms down and slightly forward, not a T-pose bind.
    const upperArmAngle = (-75 * Math.PI) / 180;
    const elbowAngle = (20 * Math.PI) / 180;

    for (const side of ["left", "right"]) {
      const sign = side === "left" ? 1 : -1;
      const upperArm = this.humanoid.getNormalizedBoneNode(`${side}UpperArm`);
      if (upperArm) upperArm.rotation.z = sign * upperArmAngle;

      const lowerArm = this.humanoid.getNormalizedBoneNode(`${side}LowerArm`);
      if (lowerArm) lowerArm.rotation.z = sign * elbowAngle;
    }

    this._curlFingers();
  }

  _curlFingers() {
    // More curl at the knuckle, less at the fingertip; mirrored per side.
    const fingerCurlDeg = { Proximal: 22, Intermediate: 18, Distal: 12 };
    const thumbCurlDeg = { Metacarpal: 5, Proximal: 10, Distal: 6 };

    for (const side of ["left", "right"]) {
      const sign = side === "left" ? -1 : 1;
      for (const finger of ["index", "middle", "ring", "little"]) {
        for (const [segment, deg] of Object.entries(fingerCurlDeg)) {
          this._curlJoint(`${side}${finger[0].toUpperCase()}${finger.slice(1)}${segment}`, deg * sign);
        }
      }
      for (const [segment, deg] of Object.entries(thumbCurlDeg)) {
        this._curlJoint(`${side}Thumb${segment}`, deg * sign);
      }
    }
  }

  _curlJoint(boneName, deg) {
    const bone = this.humanoid.getNormalizedBoneNode(boneName);
    if (bone) bone.rotation.z = (deg * Math.PI) / 180;
  }

  update(delta) {
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
      this.headBone.quaternion
        .copy(this.headRestQuat)
        .multiply(_swayQuat.setFromAxisAngle(_axisY, turnAngle));
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

const _swayQuat = new THREE.Quaternion();
const _axisY = new THREE.Vector3(0, 1, 0);
const _axisZ = new THREE.Vector3(0, 0, 1);
