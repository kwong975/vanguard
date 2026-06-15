# /audit — System Health Check

Diagnose Claude Code system health: repo coverage, memory consistency, budget compliance, missing artifacts.

Read-only. No file writes. No side effects.

## Modes

- `/audit` — full audit (all areas)
- `/audit repos` — repo CLAUDE.md coverage only
- `/audit memory` — memory file consistency only
- `/audit budgets` — budget compliance only
- `/audit hooks` — hook configuration status only
- `/audit skills` — skill availability and completeness only

When a scoped mode is given, ONLY audit that area. Do not run the full audit.

## Instructions

### Area: Repos

Check every git repo under `$DEV_DIR/`:

1. Does `<repo>/CLAUDE.md` exist?
2. If yes: line count, check ≤100 lines
3. Does `$MEMORY_DIR/known-issues/<repo>.md` exist?
4. If yes: is it a stub ("No known issues") or has substantive entries?

Output:
```
### Repo Coverage
- [x] <repo> (NL) ✓
- [ ] <repo> — CLAUDE.md MISSING
- [x] <repo> known-issues: substantive / stub / MISSING
```

### Area: Memory

Scan memory files for consistency:

1. `$MEMORY_DIR/projects/*.md` (non-archive, non-notes):
   - Has frontmatter with workspace, status, updated?
   - Has `## Next` section?
   - Has `## Log` section?
   - Next appears before Log?
   - Status is active/paused/reference? (flag if complete — should be archived)
2. `$MEMORY_DIR/memories/MEMORIES.md`: line count, section structure
3. Auto-memory `~/.claude/projects/*/memory/MEMORY.md`: which exist, sizes

Output:
```
### Memory Consistency
- Project files: N active, M reference
- Structure issues: <list any files missing frontmatter fields, sections, or with wrong section order>
- MEMORIES.md: N/200 lines
- Auto-memory: N projects with MEMORY.md files
```

### Area: Budgets

Check against defined budgets:

| Source | Budget | How to Check |
|--------|--------|-------------|
| `~/.claude/CLAUDE.md` | 80 lines | `wc -l` |
| `~/dev/CLAUDE.md` | 300 lines (soft-warn 250) | `wc -l` |
| Per-repo CLAUDE.md | 100 lines | `wc -l` each |
| MEMORIES.md | 200 lines | `wc -l` |
| Individual memory files | 30 lines | `wc -l` each in `~/.claude/projects/*/memory/` |

Output:
```
### Budget Compliance
- ~/.claude/CLAUDE.md: N/80 lines ✓/⚠
- ~/dev/CLAUDE.md: N/300 lines ✓/⚠ (soft-warn at 250)
- <repo>/CLAUDE.md: N/100 lines ✓/⚠ (each)
- MEMORIES.md: N/200 lines ✓/⚠
- Over-budget files: <list any>
```

### Area: Hooks

Read `~/.claude/settings.json` and report:

1. All configured hooks (name, event, matcher, mode)
2. Whether hook script files exist on disk
3. Any hooks in warn mode vs enforce mode

Output:
```
### Hooks
- <hook>: <event> (<mode>) ✓/⚠
```

### Area: Skills

Check `~/.claude/commands/` for installed skills:

1. List all `.md` files
2. For each: name, whether it's read-only or write-capable (from file content)

Output:
```
### Skills
- /<name>: <mode> ✓
```

## Full Audit

When running `/audit` without arguments, run ALL areas and produce a combined report:

```
## System Audit

### Repo Coverage
...

### Memory Consistency
...

### Budget Compliance
...

### Hooks
...

### Skills
...

### Summary
N critical, M warnings, K clean
```

**Severity classification:**
- **Critical:** Missing CLAUDE.md for active repo, budget exceeded, hook script missing
- **Warning:** Stub known-issues, complete project not archived, memory file over budget, warn-mode hook
- **Info:** Everything else

## Constraints

- Read-only. Never modify any file.
- `/audit` checks infrastructure and coverage. It does NOT check memory content (that's `/reflect`).
- Maximum 50 lines of output for scoped modes, 80 lines for full audit.
- Run the relevant commands (wc -l, ls, grep) directly — do not delegate to subagents.
