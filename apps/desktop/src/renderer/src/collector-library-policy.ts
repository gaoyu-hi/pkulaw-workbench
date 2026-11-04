export type CollectorLibrary = "central" | "local" | "both";

export const ALL_PROVINCES = "全部省份";

export function showsProvinceControl(
  library: CollectorLibrary
): boolean {
  return library !== "central";
}

export function effectiveProvince(
  library: CollectorLibrary,
  selectedProvince: string
): string {
  if (library === "central") {
    return ALL_PROVINCES;
  }
  return selectedProvince || ALL_PROVINCES;
}
