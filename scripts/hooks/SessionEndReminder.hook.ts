/**
 * UserPromptSubmit Hook: Session End Reminder
 *
 * When the user signals they're done, injects a reminder to update
 * the active project file and ~/dev/CLAUDE.md if anything structural changed.
 */

import { readFileSync } from "fs";

const input = readFileSync("/dev/stdin", "utf-8").trim();

let userMessage = "";
try {
  const data = JSON.parse(input);
  userMessage = (data.prompt || data.message || "").toLowerCase().trim();
} catch {
  process.exit(0);
}

const donePatterns = [
  /^(that'?s?\s+all|we'?re\s+done|i'?m\s+done|done\s+for\s+(now|today)|let'?s?\s+(stop|wrap|call\s+it)|wrap\s+(it\s+)?up|that'?s?\s+it|nothing\s+else|all\s+good|good\s+for\s+(now|today)|signing\s+off|end\s+session)/i,
  /^(ok\s+)?(i\s+think\s+)?we'?re?\s+(good|finished|wrapped)/i,
];

if (donePatterns.some((p) => p.test(userMessage))) {
  const result = {
    additionalContext:
      "SESSION END CHECKLIST — Before closing:\n" +
      "1. Active project file in vault _system/claude/projects/ — update Log (what was done) and Next (remaining steps).\n" +
      "2. ~/dev/CLAUDE.md — update the Projects table if a new project was added or a project completed.\n" +
      "3. If a project is complete: set status: complete in its frontmatter (flat structure — no separate archive folder).\n" +
      "Remind the user of any pending updates.",
  };
  process.stdout.write(JSON.stringify(result));
} else {
  process.exit(0);
}
