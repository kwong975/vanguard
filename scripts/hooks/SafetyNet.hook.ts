/**
 * PreToolUse Hook: SafetyNet
 *
 * Intercepts Bash commands before execution.
 * - Hard BLOCK (exit 2): destructive patterns (rm -rf broad, force push, push main)
 * - WARN (exit 0 + context): preference violations (pip, npm, yarn)
 * - Fails open: parse errors or unknown patterns → allow.
 */

import { readFileSync } from "fs";
import { logEvent } from "./lib/log";

interface HookInput {
  tool_input?: {
    command?: string;
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

interface BlockRule {
  pattern: RegExp;
  message: string;
}

interface WarnRule {
  pattern: RegExp;
  message: string;
}

// Hard block: destructive, irreversible actions
const BLOCK_RULES: BlockRule[] = [
  // rm -rf with broad/dangerous targets
  // Block: rm -rf /, rm -rf ~, rm -rf ., rm -rf .. , rm -rf (no path), rm -rf $VAR
  // Allow: rm -rf ./specific-dir/, rm -rf /Users/.../build/
  {
    pattern:
      /\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f|(-[a-zA-Z]*f[a-zA-Z]*r))\s+(\/\s|\/\s*$|~|\.\.?\/?\s|\.\.?\s*$|\$)/,
    message:
      "Blocked: broad `rm -rf` on dangerous target. Use a specific path: `rm -rf ./build/`",
  },
  // rm -rf with no path argument at all
  {
    pattern: /\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f|(-[a-zA-Z]*f[a-zA-Z]*r))\s*$/,
    message:
      "Blocked: `rm -rf` with no target path. Specify the exact directory.",
  },
  // git push to main/master (direct push, not via PR)
  {
    pattern: /\bgit\s+push\b.*\b(main|master)\b/,
    message:
      "Blocked: direct push to main/master. Push to a feature branch and open a PR.",
  },
  // git push --force (any target)
  {
    pattern: /\bgit\s+push\s+(-[a-zA-Z]*f|--force)(?!-with-lease)\b/,
    message:
      "Blocked: force push. Use `--force-with-lease` if you need to overwrite remote.",
  },
  // git reset --hard
  {
    pattern: /\bgit\s+reset\s+--hard\b/,
    message:
      "Blocked: `git reset --hard` discards changes. Use `git stash` or create a backup branch.",
  },
  // git checkout . or git restore . (discard all changes)
  {
    pattern: /\bgit\s+(checkout|restore)\s+\.\s*$/,
    message:
      "Blocked: discarding all changes. Restore specific files: `git restore <file>`",
  },
  // git clean -f (remove untracked files)
  {
    pattern: /\bgit\s+clean\s+-[a-zA-Z]*f/,
    message:
      "Blocked: `git clean -f` removes untracked files. Be specific about what to remove.",
  },

  // --- Package managers: enforce uv / bun (the workspace standard) ---
  {
    pattern: /\bpip\d?\s+install\b/,
    message:
      "Blocked: use `uv add` (or `uv pip install` inside a uv env) — never `pip install`. The workspace standard is uv (lockfile-driven, reproducible).",
  },
  {
    pattern: /\bnpm\s+(install|i|add|ci)\b/,
    message:
      "Blocked: use `bun add` (or `bun install`) — never npm. The workspace standard is bun.",
  },
  {
    pattern: /\byarn\s+(add|install)\b/,
    message:
      "Blocked: use `bun add` — never yarn. The workspace standard is bun.",
  },
];

// Argument-scoped block rules: these match file / staging ARGUMENTS, so they are
// tested against the command with the commit-message VALUE stripped (prose in
// `-m`/`--message` is not a file path) while quotes are otherwise preserved (a
// quoted secret filename must still be caught). Keeps detection strong; only the
// free-text message field — the documented false-positive source — is excluded.
const ARG_BLOCK_RULES: BlockRule[] = [
  // --- Secrets: never let a secret reach git ---
  // A secret file named explicitly in a git add/commit
  {
    // The leading separator class (not a bare \b) so a LEADING-DOT secret file
    // — `.env`, `.pem` — anchors too; a bare \b fails before a dotfile's `.`.
    pattern:
      /\bgit\s+(add|commit)\b[^\n]*(?:^|[\s"'=:/])([\w./-]*\.env(\.\w+)?|credentials(\.\w+)?|[\w./-]*\.(pem|key|p12|pfx)|id_rsa)\b/,
    message:
      "Blocked: that looks like a secret file (.env / credentials / .pem / .key / id_rsa) in a git command. Secrets must never be committed — keep them in config/env and gitignored.",
  },
  // Broad staging — sweeps untracked files (the secret-leak vector) and violates
  // the "stage explicit paths" convention.
  {
    pattern: /\bgit\s+add\s+(-A\b|--all\b|\.(\s|$))/,
    message:
      "Blocked: broad `git add` (-A / --all / .) sweeps every file — a secret-leak risk. Stage explicit paths: `git add <file> <file>`.",
  },
  {
    pattern: /\bgit\s+commit\b[^\n]*\s-[a-zA-Z]*a/,
    message:
      "Blocked: `git commit -a` stages all tracked changes blindly. Stage explicit paths, then commit.",
  },
];

// Warn: softer preference nudges (the install/add forms are hard-blocked above).
const WARN_RULES: WarnRule[] = [
  {
    pattern: /\bnpm\s+run\b/,
    message: "⚠️ Prefer `bun run` over `npm run`.",
  },
  {
    pattern: /\byarn\s+run\b/,
    message: "⚠️ Prefer `bun run` over `yarn run`.",
  },
];

// Main
const input = parseInput();
const command = input.tool_input?.command?.trim();

if (!command) process.exit(0);

// Strip tokens, keys, passwords from commands before logging
function sanitizeForLog(cmd: string): string {
  return cmd
    .replace(
      /\b(token|key|password|secret|credential)s?\s*[=:]\s*\S+/gi,
      "$1=***",
    )
    .replace(/\b(Bearer|Basic)\s+\S+/gi, "$1 ***")
    .replace(/\b[A-Za-z0-9_-]{20,}\b/g, (match) =>
      /^(node_modules|force-with-lease|pyproject|package)/.test(match)
        ? match
        : "***",
    );
}

// Blank out the CONTENTS of quoted strings (keep the quote marks) so prose inside
// a commit message / echo / `claude -p "…"` can't trigger a destructive-VERB rule.
// A real destructive command is unquoted shell syntax, so this never hides one.
function stripQuotedContent(cmd: string): string {
  return cmd.replace(/"(\\.|[^"\\])*"/g, '""').replace(/'[^']*'/g, "''");
}

// Blank out the VALUE of a git commit message flag (-m / -am / --message), the one
// free-text field in a git command — so prose there can't trip an argument rule —
// while leaving other quoted args (e.g. a quoted secret filename) intact.
function stripGitMessage(cmd: string): string {
  return cmd.replace(
    /(-[a-zA-Z]*m|--message)\s*=?\s*("(\\.|[^"\\])*"|'[^']*'|\S+)/g,
    '$1 ""',
  );
}

const unquoted = stripQuotedContent(command);
const noMessage = stripGitMessage(command);

// Hard block — destructive VERB rules match shell syntax, so test the
// quote-stripped command; argument rules match file/staging args, so test the
// command with only the commit-message value removed.
for (const rule of [...BLOCK_RULES, ...ARG_BLOCK_RULES]) {
  const target = ARG_BLOCK_RULES.includes(rule) ? noMessage : unquoted;
  if (rule.pattern.test(target)) {
    logEvent("SafetyNet", "block", rule.message, {
      command: sanitizeForLog(command),
    });
    process.stderr.write(rule.message);
    process.exit(2);
  }
}

// Check warn rules (preference nudges) against the quote-stripped command too.
for (const rule of WARN_RULES) {
  if (rule.pattern.test(unquoted)) {
    logEvent("SafetyNet", "warn", rule.message, {
      command: sanitizeForLog(command),
    });
    const result = JSON.stringify({
      hookSpecificOutput: {
        hookEventName: "PreToolUse",
        additionalContext: rule.message,
      },
    });
    process.stdout.write(result);
    process.exit(0);
  }
}

// No match — allow silently
process.exit(0);
