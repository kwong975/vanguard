# /resume — Re-entry Briefing

Assemble a structured re-entry briefing for the current repo/project. Read-only, no side effects.

## Instructions

You are producing a concise re-entry briefing. Prioritize signal density — omit empty sections, never pad.

### Step 1: Determine Context

1. Run `git rev-parse --show-toplevel` to get current repo root. Extract repo name.
2. Run these git commands in parallel:
   - `git log --oneline -10`
   - `git status --short`
   - `git branch --list`
   - `git stash list` (only if stash exists)
3. If an argument was provided (e.g., `/resume aureus`), use that as the project name. Otherwise, infer from the repo name.

### Step 2: Find Project File

1. Scan `$MEMORY_DIR/projects/` for an active project file matching the repo or argument.
   - Read frontmatter: only use files with `status: active`.
   - Match by: filename contains repo name, or title in `# heading` matches.
2. If found, extract:
   - Project title
   - Status
   - Next items (unchecked `- [ ]` items, up to 5)
   - Last log entry (most recent `- [date]` line)
3. If not found, skip this section entirely.

### Step 3: Check Known Issues

1. Check if `$MEMORY_DIR/known-issues/<repo-name>.md` exists.
2. If it exists and is non-empty, extract issue headers (`## [date] Title` lines).
3. If it doesn't exist or is empty, skip this section entirely.

### Step 4: Check Repo CLAUDE.md

1. Check if `<repo-root>/CLAUDE.md` exists.
2. If it exists, read and extract a 2-3 line summary of key guardrails.
3. If it doesn't exist, skip this section entirely.

### Step 5: Check Auto-Memory

1. Determine the auto-memory path: `~/.claude/projects/-<cwd-with-dashes>/memory/MEMORY.md`
   - Replace `/` with `-` in the cwd path, strip leading `-`.
2. Only read it if:
   - The file exists
   - The file has more than 3 lines of actual content (not just a heading)
3. If substantive, extract a brief summary of key points.
4. If trivial or missing, skip entirely.

### Step 6: Produce Output

Format the briefing as follows. **Omit any section that has no content.**

```
## Resume: <repo-name>

### Repo State
Branch: <current-branch> (<ahead/behind if relevant>)
Last commit: <relative-time> — "<subject>"
Uncommitted: <count modified, count untracked — or "clean">
<Stash: N entries — only if stash exists>

### Active Project: <title>
Next:
  -> <item 1>
  -> <item 2>
  -> <item 3>
Last log: <date> — <one-liner>

### Known Issues
- <issue title 1>
- <issue title 2>

### Repo Guardrails
<2-3 line summary from CLAUDE.md>

### Session Memory
<brief summary from auto-memory>
```

## Constraints

- Read-only. Never modify any file.
- Never run tests or builds. This skill must be fast (<15s).
- Never hallucinate content. If a section has no data, omit it.
- Maximum 30 lines of output. If hitting the limit, prioritize: Repo State > Active Project > Known Issues > Guardrails > Session Memory.
- Quote git output verbatim (branch names, commit messages). Summarize project/memory content.
- If not in a git repo AND no project file found, say: "Not in a git repo and no active project found. Nothing to resume."
