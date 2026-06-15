/**
 * PostToolUse Hook: AutoLint
 *
 * After Edit/Write, detects file type and runs the appropriate formatter.
 * Modifies the file on disk as a side effect — Claude sees the formatted version.
 *
 * Supported: .py (ruff), .ts/.tsx/.js/.jsx/.css/.json (prettier)
 * Skips: .md, generated dirs (node_modules, .venv, dist, build)
 * Fails open: formatter errors never block edits.
 *
 * Note: Uses execSync intentionally — file paths come from Claude Code's
 * tool_input (trusted internal source), not user input. Shell injection
 * is not a risk here.
 */

import { readFileSync, existsSync } from "fs";
import { extname, dirname, resolve, join } from "path";
import { execSync } from "child_process";
import { getDevDir } from "./lib/paths";

const devDir = getDevDir();

const SKIP_DIRS = [
  "node_modules",
  ".venv",
  "venv",
  "dist",
  "build",
  "__pycache__",
  ".next",
];

interface HookInput {
  tool_input?: {
    file_path?: string;
  };
}

function parseInput(): HookInput {
  try {
    const raw = readFileSync("/dev/stdin", "utf-8").trim();
    return JSON.parse(raw);
  } catch {
    return {};
  }
}

function isInSkipDir(filePath: string): boolean {
  const parts = filePath.split("/");
  return parts.some((p) => SKIP_DIRS.includes(p));
}

function isInDevDir(filePath: string): boolean {
  return resolve(filePath).startsWith(devDir);
}

function findRepoFile(filePath: string, targetFile: string): string | null {
  let dir = dirname(resolve(filePath));
  while (dir.length > 1) {
    const candidate = join(dir, targetFile);
    if (existsSync(candidate)) return dir;
    const parent = dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  return null;
}

function runFormat(filePath: string): string | null {
  const ext = extname(filePath).toLowerCase();
  const absPath = resolve(filePath);

  if (ext === ".py") {
    const repoDir = findRepoFile(absPath, "pyproject.toml");
    let ruffCmd: string;
    if (repoDir && existsSync(join(repoDir, ".venv", "bin", "ruff"))) {
      ruffCmd = `cd "${repoDir}" && uv run ruff`;
    } else {
      ruffCmd = "uvx ruff";
    }
    try {
      execSync(`${ruffCmd} check --fix "${absPath}" 2>/dev/null`, {
        timeout: 5000,
      });
    } catch {
      /* lint fix is best-effort */
    }
    try {
      execSync(`${ruffCmd} format "${absPath}" 2>/dev/null`, { timeout: 5000 });
      return "ruff";
    } catch {
      return null;
    }
  }

  if ([".ts", ".tsx", ".js", ".jsx", ".css", ".json"].includes(ext)) {
    const repoDir = findRepoFile(absPath, "package.json");
    let prettierCmd: string;
    if (
      repoDir &&
      existsSync(join(repoDir, "node_modules", ".bin", "prettier"))
    ) {
      prettierCmd = `"${join(repoDir, "node_modules", ".bin", "prettier")}"`;
    } else {
      prettierCmd = "npx prettier";
    }
    try {
      execSync(`${prettierCmd} --write "${absPath}" 2>/dev/null`, {
        timeout: 10000,
      });
      return "prettier";
    } catch {
      return null;
    }
  }

  return null;
}

// Main
const input = parseInput();
const filePath = input.tool_input?.file_path;

if (!filePath) process.exit(0);

const absPath = resolve(filePath);

if (!isInDevDir(absPath) || isInSkipDir(absPath)) process.exit(0);

const formatter = runFormat(absPath);

if (formatter) {
  const result = JSON.stringify({
    hookSpecificOutput: {
      hookEventName: "PostToolUse",
      additionalContext: `[AutoLint] Formatted with ${formatter}: ${filePath}`,
    },
  });
  process.stdout.write(result);
}
