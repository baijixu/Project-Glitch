import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { VRMLoaderPlugin, VRMUtils } from "@pixiv/three-vrm";

export async function loadAvatar(url) {
  const loader = new GLTFLoader();
  loader.register((parser) => new VRMLoaderPlugin(parser));

  const gltf = await loader.loadAsync(url);
  const vrm = gltf.userData.vrm;

  // VRM 0.x models author their "forward" as +Z; three-vrm's own convention
  // (matching VRM 1.0 and three.js's default camera-facing direction) is
  // -Z, so only 0.x needs the flip. Check the actual parsed metaVersion
  // rather than assume -- this model was previously (incorrectly) assumed
  // to be 0.x; it's genuinely VRM 1.0 (metaVersion "1"), confirmed by
  // inspecting the file's own VRMC_vrm.specVersion directly.
  if (vrm.meta?.metaVersion === "0") {
    VRMUtils.rotateVRM0(vrm);
  }

  return vrm;
}
