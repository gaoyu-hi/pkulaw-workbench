import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { EventEmitter } from "node:events";
import path from "node:path";
import readline, { type Interface } from "node:readline";
import {
  PROTOCOL_VERSION,
  type BackendState,
  type BackendStatus,
  type EngineEvent,
  type EngineResponse,
  type ProtocolError
} from "../shared/protocol";
import type { JsonLineLogger } from "./logger";

interface PythonServiceOptions {
  engineDirectory: string;
  pythonPath: string;
  backendExecutable?: boolean;
  forceKillSignal?: NodeJS.Signals;
  dataDirectory: string;
  appVersion: string;
  logger: JsonLineLogger;
}

interface PendingRequest {
  resolve: (value: Record<string, unknown>) => void;
  reject: (reason: Error) => void;
  timer: NodeJS.Timeout;
}

interface ReadyWaiter {
  resolve: () => void;
  reject: (reason: Error) => void;
  timer: NodeJS.Timeout;
}

export class EngineRequestError extends Error {
  constructor(readonly code: string, message: string) {
    super(message);
    this.name = "EngineRequestError";
  }
}

export class PythonService extends EventEmitter {
  private child: ChildProcessWithoutNullStreams | null = null;
  private stdoutReader: Interface | null = null;
  private stderrReader: Interface | null = null;
  private readonly pending = new Map<string, PendingRequest>();
  private readyWaiter: ReadyWaiter | null = null;
  private requestCounter = 0;
  private state: BackendState = {
    status: "stopped",
    pid: null,
    protocolVersion: PROTOCOL_VERSION,
    engineVersion: "",
    message: "后端尚未启动"
  };
  private intentionallyStopping = false;
  private handshakeStarted = false;

  constructor(private readonly options: PythonServiceOptions) {
    super();
  }

  getState(): BackendState {
    return { ...this.state };
  }

  get pid(): number | null {
    return this.child?.pid ?? null;
  }

  async start(): Promise<void> {
    if (this.child) {
      throw new Error("Python backend is already running");
    }

    this.intentionallyStopping = false;
    this.handshakeStarted = false;
    this.setState("starting", "正在启动 Python 后端");
    const bundled = Boolean(this.options.backendExecutable);
    const args = bundled
      ? [
          "--data-dir",
          this.options.dataDirectory,
          "--app-version",
          this.options.appVersion
        ]
      : [
          "-X",
          "utf8",
          "-m",
          "pkulaw_engine",
          "--data-dir",
          this.options.dataDirectory,
          "--app-version",
          this.options.appVersion
        ];
    const child = spawn(
      this.options.pythonPath,
      args,
      {
        cwd: bundled
          ? path.dirname(this.options.pythonPath)
          : this.options.engineDirectory,
        env: {
          ...process.env,
          PYTHONIOENCODING: "utf-8",
          PYTHONUNBUFFERED: "1",
          PYTHONUTF8: "1"
        },
        windowsHide: true,
        stdio: ["pipe", "pipe", "pipe"]
      }
    );
    this.child = child;
    this.state.pid = child.pid ?? null;

    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    this.stdoutReader = readline.createInterface({ input: child.stdout });
    this.stderrReader = readline.createInterface({ input: child.stderr });
    this.stdoutReader.on("line", (line) => this.handleLine(line));
    this.stderrReader.on("line", (line) => {
      this.options.logger.write("warn", "engine.stderr", line);
    });

    const readyPromise = new Promise<void>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.readyWaiter = null;
        reject(new Error("Python backend handshake timed out"));
      }, bundled ? 20_000 : 30_000);
      this.readyWaiter = { resolve, reject, timer };
    });

    child.once("error", (error) => {
      this.failBackend(`Python 进程启动失败：${error.message}`);
    });
    child.once("exit", (code, signal) => {
      this.handleExit(code, signal);
    });
    this.options.logger.write("info", "backend.spawned", "Python backend spawned", {
      pid: child.pid,
      engineDirectory: this.options.engineDirectory,
      backendMode: bundled ? "bundled" : "source"
    });

    await readyPromise;
  }

  async request(
    method: string,
    params: Record<string, unknown> = {},
    timeoutMs = 8_000
  ): Promise<Record<string, unknown>> {
    if (this.state.status !== "ready") {
      throw new Error(`Python backend is not ready (${this.state.status})`);
    }
    return this.sendRequest(method, params, timeoutMs);
  }

  notify(event: string, data: Record<string, unknown> = {}): void {
    const child = this.child;
    if (!child || child.stdin.destroyed) {
      throw new Error("Python backend stdin is unavailable");
    }
    child.stdin.write(
      `${JSON.stringify({
        type: "notification",
        protocolVersion: PROTOCOL_VERSION,
        event,
        data,
        timestamp: new Date().toISOString()
      })}\n`
    );
  }

  async stop(): Promise<void> {
    const child = this.child;
    if (!child) {
      this.setState("stopped", "后端已停止");
      return;
    }

    this.intentionallyStopping = true;
    this.setState("stopping", "正在停止 Python 后端");
    const exitPromise = new Promise<void>((resolve) => child.once("exit", () => resolve()));

    if (this.state.status === "stopping" || this.state.status === "ready") {
      try {
        await this.sendRequest("system.shutdown", {}, 1_500);
      } catch (error) {
        this.options.logger.write("warn", "backend.shutdown_request_failed", String(error));
      }
    }

    await Promise.race([
      exitPromise,
      new Promise<void>((resolve) => setTimeout(resolve, 1_500))
    ]);
    if (this.child && this.child.exitCode === null) {
      this.child.kill(this.options.forceKillSignal || "SIGTERM");
      await new Promise<void>((resolve) => {
        const timer = setTimeout(resolve, 1_500);
        this.child?.once("exit", () => {
          clearTimeout(timer);
          resolve();
        });
      });
    }
    this.releaseReaders();
  }

  private async performHandshake(readyEvent: EngineEvent): Promise<void> {
    if (this.handshakeStarted) {
      return;
    }
    this.handshakeStarted = true;
    if (readyEvent.data.protocolVersion !== PROTOCOL_VERSION) {
      this.failBackend(
        `协议版本不兼容：应用 ${PROTOCOL_VERSION}，后端 ${String(readyEvent.data.protocolVersion)}`
      );
      return;
    }

    try {
      const result = await this.sendRequest(
        "system.handshake",
        {
          protocolVersion: PROTOCOL_VERSION,
          client: {
            name: "pkulaw-desktop",
            version: this.options.appVersion
          },
          capabilities: [
            "tasks.v1",
            "tasks.pagination.v1",
            "system.ping"
          ]
        },
        5_000
      );
      if (result.protocolVersion !== PROTOCOL_VERSION) {
        throw new Error("backend returned an unexpected protocol version");
      }
      this.state.engineVersion = String(result.engineVersion || readyEvent.data.engineVersion || "");
      this.setState("ready", "Python 后端已连接");
      const waiter = this.readyWaiter;
      if (waiter) {
        clearTimeout(waiter.timer);
        this.readyWaiter = null;
        waiter.resolve();
      }
      this.options.logger.write("info", "protocol.ready", "Protocol handshake completed", {
        protocolVersion: PROTOCOL_VERSION,
        engineVersion: this.state.engineVersion
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      this.failBackend(`协议握手失败：${message}`);
    }
  }

  private sendRequest(
    method: string,
    params: Record<string, unknown>,
    timeoutMs: number
  ): Promise<Record<string, unknown>> {
    const child = this.child;
    if (!child || child.stdin.destroyed) {
      return Promise.reject(new Error("Python backend stdin is unavailable"));
    }

    const requestId = `desktop-${Date.now()}-${++this.requestCounter}`;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(requestId);
        reject(new Error(`Python request timed out: ${method}`));
      }, timeoutMs);
      this.pending.set(requestId, { resolve, reject, timer });
      const message = {
        type: "request",
        protocolVersion: PROTOCOL_VERSION,
        requestId,
        method,
        params,
        timestamp: new Date().toISOString()
      };
      child.stdin.write(`${JSON.stringify(message)}\n`);
    });
  }

  private handleLine(line: string): void {
    let message: EngineEvent | EngineResponse;
    try {
      message = JSON.parse(line) as EngineEvent | EngineResponse;
    } catch (error) {
      this.options.logger.write("error", "protocol.invalid_json", "Engine emitted invalid JSON", {
        line,
        error: String(error)
      });
      return;
    }

    if (message.type === "event") {
      this.emit("engineEvent", message);
      if (message.event === "engine.ready") {
        void this.performHandshake(message);
      }
      return;
    }

    if (message.type !== "response") {
      this.options.logger.write("warn", "protocol.unknown_message", "Unknown engine message");
      return;
    }

    const pending = this.pending.get(message.requestId);
    if (!pending) {
      this.options.logger.write("warn", "protocol.orphan_response", "Response had no pending request", {
        requestId: message.requestId
      });
      return;
    }
    clearTimeout(pending.timer);
    this.pending.delete(message.requestId);
    if (message.ok) {
      pending.resolve(message.result || {});
    } else {
      const error: ProtocolError = message.error || {
        code: "UNKNOWN_ENGINE_ERROR",
        message: "Engine request failed without an error payload"
      };
      pending.reject(new EngineRequestError(error.code, error.message));
    }
  }

  private handleExit(code: number | null, signal: NodeJS.Signals | null): void {
    this.options.logger.write("info", "backend.exited", "Python backend exited", { code, signal });
    this.releaseReaders();
    this.child = null;
    this.state.pid = null;
    this.rejectPending(new Error(`Python backend exited (code=${code}, signal=${signal})`));
    if (this.readyWaiter) {
      clearTimeout(this.readyWaiter.timer);
      this.readyWaiter.reject(new Error("Python backend exited before handshake"));
      this.readyWaiter = null;
    }
    if (this.intentionallyStopping) {
      this.setState("stopped", "Python 后端已停止");
    } else {
      this.setState("failed", `Python 后端异常退出（code=${code ?? "unknown"}）`);
    }
  }

  private failBackend(message: string): void {
    this.options.logger.write("error", "backend.failed", message);
    this.rejectPending(new Error(message));
    if (this.readyWaiter) {
      clearTimeout(this.readyWaiter.timer);
      this.readyWaiter.reject(new Error(message));
      this.readyWaiter = null;
    }
    this.setState("failed", message);
  }

  private rejectPending(error: Error): void {
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer);
      pending.reject(error);
    }
    this.pending.clear();
  }

  private releaseReaders(): void {
    this.stdoutReader?.close();
    this.stderrReader?.close();
    this.stdoutReader = null;
    this.stderrReader = null;
  }

  private setState(status: BackendStatus, message: string): void {
    this.state = {
      ...this.state,
      status,
      pid: this.child?.pid ?? null,
      message
    };
    this.emit("state", this.getState());
  }
}
