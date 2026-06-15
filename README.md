# Vanguard

A disciplined **dev-team loop** for [Claude Code](https://claude.com/claude-code) — plus safety guardrails and a project-memory system, in one plugin.

The core idea: **the conversation drives, the agents are instruments, and the plan bends to reality.** You design a change in conversation; four specialized agents audit, critique, build, and QA it against the *real* system. Anything that diverges from reality routes back to you for a decision — you're pulled in to decide, never to rubber-stamp.

---

## Install

In Claude Code:

```
/plugin marketplace add kwong975/vanguard
/plugin install vanguard@vanguard
```

You'll be prompted for an optional **memory directory** (see [Project memory](#project-memory)) — leave it blank to default to `~/.claude/vanguard-memory`.

**Prerequisites**
- **[Bun](https://bun.sh)** — runs the hooks (`bun` on your `PATH`)
- **Python 3** — runs the `/devteam` engine

---

## What you get

| | |
|---|---|
| **`/devteam` loop** | Four agents audit → critique → build → QA each change against the real system, with human gates |
| **Guardrail hooks** | Block dangerous commands; auto-format edits |
| **Project memory** | Load your active projects into context; skills to review/close sessions |

---

## Using it

### `/devteam` — the dev-team loop

1. **Design in conversation.** Talk through what you're building until you have a living spec.
2. **Run `/devteam`.** It drives the rest, pausing only at real decisions:

```
DESIGN (you, in conversation)
   │
   ▼
DESIGN GATE   Auddie → facts + blast-radius   ·   Vigil → HOLDS | NEEDS-ADJUSTMENT
   │
   ▼  (HOLDS proceeds on its own)
Forge builds the slice on a branch
   │
   ▼
BUILD GATE    Auddie → actual vs predicted impact   ·   Sentinel → PASS | FAIL—CODE | ESCAPE—DESIGN
   │
   ▼
output to you (you commit)
```

| Agent | Role |
|-------|------|
| **Auddie** | Ground-truth auditor — facts + predicted/actual blast radius |
| **Vigil** | Design critic — `HOLDS` or `NEEDS-ADJUSTMENT` |
| **Forge** | Builder — implements one slice on a branch |
| **Sentinel** | QA gate — deterministic → static → dynamic on the real entry path |

You touch it at four points: author the design, adjust if Vigil says `NEEDS-ADJUSTMENT`, resolve an `ESCAPE—DESIGN`, and review the output.

### Guardrail hooks (automatic)

- **SafetyNet** (`PreToolUse:Bash`) — blocks push to main, force-push, `reset --hard`, risky `rm -rf`, and dotfile secret leaks.
- **AutoLint** (`PostToolUse:Edit|Write`) — auto-formats files after edits.

### Project memory

SessionStart hooks load your active projects and identity into each session; `Stop`/`UserPromptSubmit` hooks help close sessions cleanly. Five skills work the memory:

`/resume` (re-entry briefing) · `/wrapup` (session closure) · `/reflect` (memory review) · `/audit` (system health) · `/design` (structured design)

Point the plugin's **`memoryDir`** at a directory laid out like this (each part is optional; skills use what's present):

```
<memoryDir>/
├── identity/        # profile that renders into your session context
├── projects/        # one markdown file per active piece of work
├── memories/        # MEMORIES.md — durable corrections / preferences
└── known-issues/    # one file per repo
```

Unset or empty? Memory features default to `~/.claude/vanguard-memory` and no-op gracefully until the directory exists.

---

## Configuration

`/devteam` resolves paths — your workspace, and where project files live — **in conversation**: it asks once if they aren't obvious, and never hardcodes them. No config file is required; the engine takes every path as an explicit argument. `scripts/config.yaml.example` just documents the path defaults if you'd rather write them down.

---

## Contributing

Issues and PRs welcome at <https://github.com/kwong975/vanguard>. Run `claude plugin validate .` before submitting.

## License

MIT © Kelly Wong
