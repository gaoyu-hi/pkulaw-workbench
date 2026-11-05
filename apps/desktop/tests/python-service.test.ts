import fs from "node:fs";
import fsp from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { EventEmitter } from "node:events";
import { afterEach, describe, expect, it, vi } from "vitest";
import { JsonLineLogger } from "../src/main/logger";
import { PythonService } from "../src/main/python-service";

const ENGINE_DIRECTORY = path.resolve(import.meta.dirname, "../../../engine");
const PYTHON_PATH = process.env.PKULAW_PYTHON || "D:\\Python\\python.exe";

describe("PythonService integration", () => {
  let service: PythonService | null = null;
  let tempDirectory: string | null = null;

  afterEach(async () => {
    await service?.stop();
    service = null;
    if (tempDirectory) {
      fs.rmSync(tempDirectory, { recursive: true, force: true });
      tempDirectory = null;
    }
  });

  it("handshakes, handles requests and errors, and exits cleanly", async () => {
    tempDirectory = await fsp.mkdtemp(path.join(os.tmpdir(), "pkulaw-service-"));
    const logger = new JsonLineLogger(path.join(tempDirectory, "logs", "desktop.jsonl"));
    service = new PythonService({
      engineDirectory: ENGINE_DIRECTORY,
      pythonPath: PYTHON_PATH,
      dataDirectory: tempDirectory,
      appVersion: "0.1.0-test",
      logger
    });

    const statuses: string[] = [];
    service.on("state", (state) => statuses.push(state.status));
    await service.start();

    expect(service.getState()).toMatchObject({
      status: "ready",
      protocolVersion: "1.0.0"
    });
    expect(service.pid).toBeTypeOf("number");

    const ping = await service.request("system.ping");
    expect(ping.status).toBe("ok");

    const created = (await service.request("task.create", {
      name: "桌面协议集成任务",
      mode: "batch",
      input: { keywords: ["绿色矿山"] },
      outputDir: "D:/pkulaw-test-output"
    })) as {
      task: { id: string; name: string; mode: string };
    };
    expect(created.task.name).toBe("桌面协议集成任务");
    expect(created.task.mode).toBe("batch");

    const listed = (await service.request("task.list")) as {
      tasks: Array<{ id: string }>;
    };
    expect(listed.tasks[0].id).toBe(created.task.id);

    await expect(service.request("unknown.method")).rejects.toMatchObject({
      code: "METHOD_NOT_FOUND"
    });

    const pid = service.pid;
    await service.stop();
    expect(service.getState().status).toBe("stopped");
    expect(service.pid).toBeNull();
    expect(pid).not.toBeNull();
    expect(statuses).toContain("ready");
    expect(fs.existsSync(path.join(tempDirectory, "pkulaw.sqlite3"))).toBe(true);
  }, 20_000);

  it("uses the configured force signal after graceful shutdown times out", async () => {
    tempDirectory = await fsp.mkdtemp(path.join(os.tmpdir(), "pkulaw-service-"));
    const logger = new JsonLineLogger(path.join(tempDirectory, "logs", "desktop.jsonl"));
    service = new PythonService({
      engineDirectory: ENGINE_DIRECTORY,
      pythonPath: PYTHON_PATH,
      dataDirectory: tempDirectory,
      appVersion: "0.1.0-test",
      forceKillSignal: "SIGKILL",
      logger
    });
    const signals: Array<NodeJS.Signals | undefined> = [];
    const child = new EventEmitter() as EventEmitter & {
      exitCode: number | null;
      kill: (signal?: NodeJS.Signals) => boolean;
      stdin: { destroyed: boolean };
    };
    child.exitCode = null;
    child.stdin = { destroyed: false };
    child.kill = vi.fn((signal?: NodeJS.Signals) => {
      signals.push(signal);
      queueMicrotask(() => child.emit("exit", null, signal));
      return true;
    });
    Object.assign(service as unknown as Record<string, unknown>, {
      child
    });

    await service.stop();

    expect(signals).toEqual(["SIGKILL"]);
  }, 5_000);
});
