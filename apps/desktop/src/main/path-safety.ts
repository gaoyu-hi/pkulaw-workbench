import fsp from "node:fs/promises";
import path from "node:path";

export async function ensureWritableDirectory(directory: string): Promise<string> {
  if (typeof directory !== "string" || !directory.trim()) {
    throw new Error("输出目录不能为空");
  }
  const resolved = path.resolve(directory.trim());
  const marker = path.join(
    resolved,
    `.pkulaw-ipc-write-${process.pid}-${Date.now()}-${Math.random()
      .toString(16)
      .slice(2)}`
  );
  try {
    await fsp.mkdir(resolved, { recursive: true });
    const stat = await fsp.stat(resolved);
    if (!stat.isDirectory()) {
      throw new Error("目标不是目录");
    }
    await fsp.writeFile(marker, "ok", "utf8");
    await fsp.unlink(marker);
    return resolved;
  } catch (error) {
    try {
      await fsp.unlink(marker);
    } catch {
      // The marker may not have been created.
    }
    throw new Error(
      `输出目录不可写：${resolved}。请选择本机可写目录。${
        error instanceof Error ? ` (${error.message})` : ""
      }`
    );
  }
}
