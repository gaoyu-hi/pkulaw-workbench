import path from "node:path";

export function assertMacArm64Host(platform, arch) {
  if (platform !== "darwin" || arch !== "arm64") {
    throw new Error(
      `MAC_ARM64_BUILD_HOST_REQUIRED: expected darwin/arm64, received ${platform}/${arch}`
    );
  }
}

export function macDmgArtifacts(releaseRoot, version) {
  const dmgName = `PkulawWorkbench-${version}-macOS-arm64.dmg`;
  const dmg = path.join(releaseRoot, dmgName);
  return {
    dmg,
    checksum: `${dmg}.sha256`
  };
}
