# /reflect — Memory Review and Cleanup

Review memory hygiene across both memory systems. Detect stale, duplicate, and contradictory entries. Propose conservative cleanup with approval.

Default mode is `review`. Use `/reflect clean` to execute approved fixes after a review.

## Subcommands

- `/reflect` or `/reflect review` — read-only audit of memory health
- `/reflect clean` — propose and execute fixes (each change requires approval)

---

## /reflect review

### Step 1: Scan Memory Files

Read all of the following:

1. `$MEMORY_DIR/memories/MEMORIES.md` — count lines, check sections
2. Active project files in `$MEMORY_DIR/projects/*.md` (skip archive/, skip `-notes.md` files)
   - Parse frontmatter for status
   - Check if active projects have recent Log entries (last 30 days)
4. `$MEMORY_DIR/known-issues/*.md` — check for content
5. Auto-memory files: `~/.claude/projects/*/memory/MEMORY.md` — check which exist and have content

### Step 2: Budget Compliance

Check against these budgets (tune to your own conventions):

| Source | Budget |
|--------|--------|
| MEMORIES.md | 200 lines max |
| Individual memory files | 30 lines max |
| Per-repo CLAUDE.md | 100 lines max |

Report: current count / budget for each, flag any over budget.

### Step 3: Staleness Detection

An entry is **potentially stale** if:
- A project file has `status: active` but its `updated:` frontmatter date is >30 days old (primary check)
- A project file has `status: active` but no `updated:` field AND its last Log entry is >30 days old (fallback)
- A project file has `status: complete` but is still in the main projects/ directory (should be in archive/)
- An auto-memory MEMORY.md references a file path that no longer exists
- A known-issues entry references an upstream issue that may be resolved (note: cannot verify — just flag for manual check)
- A project file is missing required structure: `updated:` in frontmatter, `## Next` section, or `## Log` section

**Project file size guidance:** Active project files over 200 lines should be flagged as "consider moving detail to -notes.md". This is advisory, not a hard budget.

### Step 4: Duplicate Detection

Check for entries that say substantially the same thing in different locations:
- Within MEMORIES.md (between sections)
- Between auto-memory MEMORY.md files and project memory

Only flag **clear duplicates** where the same fact/rule appears in two places with the same meaning. Do not flag cross-references or entries that cover related but distinct topics.

**`## Agent Calibration` is load-bearing — injected into the dev-team agents (Auddie/Vigil/Forge/Sentinel) at spawn.** Its entries are active, not stale: the loop reads the section fresh each run and feeds it to the agents. Do NOT propose consolidating, removing, or merging Agent Calibration entries (including against near-twins in Stated Preferences / Behavioral Corrections) without explicitly flagging that they feed the agents — a "duplicate" here may be a deliberately-generalized agent copy. Treat the section as active by definition; never flag it stale on age alone.

### Step 5: Contradiction Detection

**Very conservative.** Only flag when:
- Two entries about the **same clearly identified entity** (same file path, same tool name, same config key) make **directly opposing claims**.
- Example: one entry says "use port 8080" and another says "use port 9090" for the same service.

Do NOT flag:
- Entries that cover different aspects of the same topic
- Entries that evolved over time (the newer one supersedes)
- Vague or contextual differences

### Step 6: Missing Coverage

Note which repos lack known-issues files (check `$DEV_DIR/` subdirectories that are git repos).

### Step 7: Produce Report

```
## Memory Review

### Budget Status
- MEMORIES.md: <N>/200 lines <checkmark or warning>
- <other files over budget if any>

### Issues Found

**Stale (<count>):**
1. <file> — <reason>

**Duplicates (<count>):**
2. <entry A> duplicates <entry B> — <where>

**Contradictions (<count>):**
3. <entry A> vs <entry B> — <specific conflict>

**Missing Coverage:**
- No known-issues file for: <repo list>

### Summary
<N> issues found. Run /reflect clean to address.
```

If no issues found: "Memory is clean. No issues found."

**Output constraint:** Max 40 lines. If more issues than fit, prioritize: contradictions > duplicates > stale > missing coverage.

---

## /reflect clean

### Prerequisites

Run `/reflect review` first (or have review results from current conversation).

### Execution Model

1. Present each proposed fix individually with:
   - What will change
   - Why (the issue it addresses)
   - The exact edit (old text → new text, or "remove lines X-Y")
2. Wait for approval on each fix before executing.
3. Natural-language approval: "yes", "do it", "skip this one", "do all remaining", etc.

### Allowed Operations

- **Remove duplicate entries** from MEMORIES.md (keep the more specific or more recently updated version)
- **Remove stale auto-memory entries** that reference nonexistent files
- **Update MEMORIES.md** to remove or consolidate entries (with approval)

### NOT Allowed

- Editing known-issues files (manual only)
- Batch deletions (each removal individually approved)
- Creating new files or directories
- Adding frontmatter fields or restructuring project file sections (flag as advisory, don't fix)
- Consolidating or removing `## Agent Calibration` entries in MEMORIES.md — **load-bearing, injected into the dev-team agents at spawn.** Those entries are active (the loop reads them fresh each run), not stale, and may be deliberately-generalized copies of Stated Preferences / Behavioral Corrections entries. Never propose gutting them as duplicates/stale without flagging that they feed the agents.

### After Cleanup

Show a summary of what was changed:
```
## Cleanup Complete
- <N> changes made
- <list of specific edits>
```

## Constraints

- `/reflect review` is strictly read-only.
- `/reflect clean` requires approval for every write.
- Never touch: `~/.claude/CLAUDE.md`, `~/dev/CLAUDE.md`, `settings.json`, hook scripts, rules files.
- Be conservative. When in doubt, flag for manual review rather than proposing a fix.
- Contradiction detection must be very conservative — only flag direct conflicts on the same clearly identified entity.
- Respect the boundary between your project memory and Claude Code auto-memory (`~/.claude/projects/*/memory/`). Report on both but never merge entries between them.
