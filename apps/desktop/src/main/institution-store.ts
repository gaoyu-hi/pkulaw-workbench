import fs from "node:fs";
import fsp from "node:fs/promises";
import path from "node:path";
import type { InstitutionProfile } from "../shared/protocol";
import {
  DEFAULT_BROWSER_ZOOM_FACTOR,
  normalizeBrowserZoomFactor
} from "../shared/browser-zoom";

interface InstitutionFile {
  version: 1;
  institutions: InstitutionProfile[];
}

function summarizeError(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export class InstitutionStore {
  private readonly filePath: string;

  constructor(dataDirectory: string) {
    this.filePath = path.join(dataDirectory, "config", "institutions.json");
  }

  async list(): Promise<InstitutionProfile[]> {
    const file = await this.read();
    return file.institutions;
  }

  async get(id: string): Promise<InstitutionProfile | null> {
    return (await this.list()).find((item) => item.id === id) || null;
  }

  async save(input: {
    id?: string;
    name: string;
    entryUrl: string;
  }): Promise<InstitutionProfile> {
    const name = String(input.name || "").trim();
    const entryUrl = String(input.entryUrl || "").trim();
    if (!name || name.length > 80) {
      throw new Error("机构名称不能为空，且不能超过 80 个字符");
    }
    let parsed: URL;
    try {
      parsed = new URL(entryUrl);
    } catch {
      throw new Error("机构入口必须是有效的 HTTP 或 HTTPS 地址");
    }
    if (!["http:", "https:"].includes(parsed.protocol)) {
      throw new Error("机构入口必须使用 HTTP 或 HTTPS");
    }

    const file = await this.read();
    const id = input.id?.trim() || `institution-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;
    const existing = file.institutions.find((item) => item.id === id);
    const now = new Date().toISOString();
    const profile: InstitutionProfile = {
      id,
      name,
      entryUrl: parsed.toString(),
      sessionKey: `pkulaw-${id.replace(/[^a-zA-Z0-9_-]/g, "-")}`,
      browserZoomFactor:
        existing?.browserZoomFactor || DEFAULT_BROWSER_ZOOM_FACTOR,
      createdAt: existing?.createdAt || now,
      updatedAt: now
    };
    const next = file.institutions.filter((item) => item.id !== id);
    next.unshift(profile);
    await this.write({ version: 1, institutions: next });
    return profile;
  }

  async setBrowserZoom(
    institutionId: string,
    factor: number
  ): Promise<InstitutionProfile> {
    const file = await this.read();
    const existing = file.institutions.find(
      (item) => item.id === institutionId
    );
    if (!existing) {
      throw new Error(`机构 ${institutionId} 不存在`);
    }
    const updated: InstitutionProfile = {
      ...existing,
      browserZoomFactor: normalizeBrowserZoomFactor(factor),
      updatedAt: new Date().toISOString()
    };
    await this.write({
      version: 1,
      institutions: file.institutions.map((item) =>
        item.id === institutionId ? updated : item
      )
    });
    return updated;
  }

  async saveFixture(entryUrl: string): Promise<InstitutionProfile> {
    return this.save({
      id: "local-fixture",
      name: "本地测试机构",
      entryUrl
    });
  }

  private async read(): Promise<InstitutionFile> {
    try {
      const raw = await fsp.readFile(this.filePath, "utf8");
      const parsed = JSON.parse(raw) as Partial<InstitutionFile>;
      if (parsed.version !== 1 || !Array.isArray(parsed.institutions)) {
        throw new Error("unsupported institution config format");
      }
      return {
        version: 1,
        institutions: parsed.institutions
          .filter(this.isProfile)
          .map((item) => ({
            ...item,
            browserZoomFactor:
              item.browserZoomFactor === undefined
                ? DEFAULT_BROWSER_ZOOM_FACTOR
                : normalizeBrowserZoomFactor(item.browserZoomFactor)
          }))
      };
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") {
        throw new Error(`读取机构配置失败：${summarizeError(error)}`);
      }
      return { version: 1, institutions: [] };
    }
  }

  private async write(file: InstitutionFile): Promise<void> {
    await fsp.mkdir(path.dirname(this.filePath), { recursive: true });
    const temporaryPath = `${this.filePath}.${process.pid}.${Date.now()}.tmp`;
    await fsp.writeFile(temporaryPath, JSON.stringify(file, null, 2), "utf8");
    if (fs.existsSync(this.filePath)) {
      await fsp.rm(this.filePath);
    }
    await fsp.rename(temporaryPath, this.filePath);
  }

  private isProfile(value: unknown): value is InstitutionProfile {
    if (!value || typeof value !== "object") {
      return false;
    }
    const item = value as Partial<InstitutionProfile>;
    return Boolean(
      item.id &&
        item.name &&
        item.entryUrl &&
        item.sessionKey &&
        item.createdAt &&
        item.updatedAt
    );
  }
}
