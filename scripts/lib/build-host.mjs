export function assertMacArm64Host(platform, arch) {
  if (platform !== "darwin" || arch !== "arm64") {
    throw new Error(
      `MAC_ARM64_BUILD_HOST_REQUIRED: expected darwin/arm64, received ${platform}/${arch}`
    );
  }
}
