import { appendFileSync, statSync, renameSync } from "fs";
import { homedir } from "os";
import { join } from "path";

const LOG_FILE = join(homedir(), ".claude", "hook-calibration.jsonl");
const MAX_LOG_BYTES = 512 * 1024; // 512KB — rotate when exceeded

interface LogEntry {
  timestamp: string;
  hook: string;
  event: "warn" | "block";
  detail: string;
  [key: string]: unknown;
}

function rotateIfNeeded(): void {
  try {
    const stats = statSync(LOG_FILE);
    if (stats.size > MAX_LOG_BYTES) {
      renameSync(LOG_FILE, LOG_FILE + ".1");
    }
  } catch {
    // File doesn't exist yet or stat failed — no rotation needed
  }
}

export function logEvent(
  hook: string,
  event: "warn" | "block",
  detail: string,
  extra?: Record<string, unknown>,
): void {
  try {
    rotateIfNeeded();
    const entry: LogEntry = {
      timestamp: new Date().toISOString(),
      hook,
      event,
      detail,
      ...extra,
    };
    appendFileSync(LOG_FILE, JSON.stringify(entry) + "\n");
  } catch {
    // Fail silently — logging must never break hooks
  }
}
