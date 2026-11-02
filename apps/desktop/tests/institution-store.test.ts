import fs from "node:fs";
import fsp from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { InstitutionStore } from "../src/main/institution-store";

describe("InstitutionStore", () => {
  let tempDirectory: string | null = null;

  afterEach(() => {
    if (tempDirectory) {
      fs.rmSync(tempDirectory, { recursive: true, force: true });
      tempDirectory = null;
    }
  });

  it("persists profiles with institution-specific session keys", async () => {
    tempDirectory = await fsp.mkdtemp(path.join(os.tmpdir(), "pkulaw-institutions-"));
    const store = new InstitutionStore(tempDirectory);
    const saved = await store.save({
      id: "test-university",
      name: "测试大学",
      entryUrl: "https://library.example.edu/pkulaw"
    });

    expect(saved.sessionKey).toBe("pkulaw-test-university");
    expect(saved.entryUrl).toBe("https://library.example.edu/pkulaw");
    expect(saved.browserZoomFactor).toBe(1);
    expect(await store.get("test-university")).toMatchObject({
      name: "测试大学",
      sessionKey: "pkulaw-test-university"
    });

    const reloaded = new InstitutionStore(tempDirectory);
    expect(await reloaded.list()).toHaveLength(1);
  });

  it("persists browser zoom per institution session", async () => {
    tempDirectory = await fsp.mkdtemp(path.join(os.tmpdir(), "pkulaw-institutions-"));
    const store = new InstitutionStore(tempDirectory);
    const first = await store.save({
      id: "first",
      name: "第一机构",
      entryUrl: "https://first.example.edu/pkulaw"
    });
    const second = await store.save({
      id: "second",
      name: "第二机构",
      entryUrl: "https://second.example.edu/pkulaw"
    });

    await store.setBrowserZoom(first.id, 1.25);

    expect((await store.get(first.id))?.browserZoomFactor).toBe(1.25);
    expect((await store.get(second.id))?.browserZoomFactor).toBe(1);
    expect((await new InstitutionStore(tempDirectory).get(first.id))?.browserZoomFactor).toBe(1.25);
  });

  it("rejects invalid protocols", async () => {
    tempDirectory = await fsp.mkdtemp(path.join(os.tmpdir(), "pkulaw-institutions-"));
    const store = new InstitutionStore(tempDirectory);

    await expect(
      store.save({
        name: "错误机构",
        entryUrl: "file:///C:/secret.txt"
      })
    ).rejects.toThrow("HTTP 或 HTTPS");
  });
});
