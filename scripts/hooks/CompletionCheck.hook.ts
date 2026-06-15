/**
 * Stop Hook: CompletionCheck
 *
 * Blocks when the assistant claims completion but produced no tangible output.
 * Deterministic heuristic — no LLM call, no JSON parsing risk.
 *
 * Always outputs valid JSON. Fails open.
 */

const input = process.env.CLAUDE_STOP_HOOK_BODY || process.argv[2] || "";

// If no input, allow
if (!input.trim()) {
  process.stdout.write(
    JSON.stringify({ ok: true, reason: "no output to check" }),
  );
  process.exit(0);
}

const lower = input.toLowerCase();

// Completion claims
const claimsComplete =
  /\b(done|finished|all set|completed|that's it|implemented|all changes applied)\b/.test(
    lower,
  );

// Tangible output signals (tool use leaves traces in the output)
const hasTangibleOutput =
  /\b(created|wrote|edited|updated|committed|pushed|ran |executed|installed|modified|deleted|moved|renamed)\b/.test(
    lower,
  ) ||
  /```/.test(input) ||
  /file_path|\.harness\/|\.md|\.py|\.ts|\.js|\.json/.test(input);

// Disclosed limitation or asked for input
const hasDisclosure =
  /\b(blocker|limitation|can't|cannot|unable|error|failed|ambiguit|unclear|not sure)\b/.test(
    lower,
  ) ||
  /\?\s*$/.test(input.trim()) ||
  /\b(want me to|should I|would you|do you want|let me know)\b/.test(lower);

if (claimsComplete && !hasTangibleOutput && !hasDisclosure) {
  process.stdout.write(
    JSON.stringify({
      ok: false,
      reason:
        "Assistant claimed completion but produced no tangible output and disclosed no blockers.",
    }),
  );
} else {
  process.stdout.write(JSON.stringify({ ok: true, reason: "pass" }));
}
