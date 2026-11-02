import { EventEmitter } from "node:events";
import fs from "node:fs";
import path from "node:path";
import type { LogRecord } from "../shared/protocol";

export class JsonLineLogger extends EventEmitter {
  private readonly recent: LogRecord[] = [];
  private readonly maxRecent = 300;

  constructor(private readonly filePath: string) {
    super();
    fs.mkdirSync(path.dirname(filePath), { recursive: true });
  }

  write(
    level: LogRecord["level"],
    event: string,
    message: string,
    data: Record<string, unknown> = {}
  ): LogRecord {
    const record: LogRecord = {
      timestamp: new Date().toISOString(),
      level,
      source: "desktop",
      event,
      message,
      data
    };
    fs.appendFileSync(this.filePath, `${JSON.stringify(record)}\n`, "utf8");
    this.recent.push(record);
    if (this.recent.length > this.maxRecent) {
      this.recent.splice(0, this.recent.length - this.maxRecent);
    }
    this.emit("record", record);
    return record;
  }

  getRecent(): LogRecord[] {
    return [...this.recent];
  }
}
