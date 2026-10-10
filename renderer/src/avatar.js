import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { VRMLoaderPlugin, VRMUtils } from "@pixiv/three-vrm";

// `source` is either a URL string (the shipped default, fetched locally
// with zero network round-trip to Brain) or an ArrayBuffer (a custom
// avatar's raw .vrm bytes, decoded from a WS-delivered base64 payload or
// read directly from a user-imported File) -- GLTFLoader's loadAsync
// only takes a URL, parseAsync is the ArrayBuffer equivalent.
export async function loadAvatar(source) {
  const loader = new GLTFLoader();
  loader.register((parser) => new VRMLoaderPlugin(parser));

  const gltf =
    typeof source === "string" ? await loader.loadAsync(source) : await loader.parseAsync(source, "");
  const vrm = gltf.userData.vrm;

  // VRM 0.x faces +Z; three-vrm (and VRM 1.0) face -Z, so only 0.x is turned around.
  if (vrm.meta?.metaVersion === "0") {
    VRMUtils.rotateVRM0(vrm);
  }

  return vrm;
}
