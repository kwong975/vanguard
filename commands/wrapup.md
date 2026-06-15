# /wrapup — Session Closure

Package session work into a structured proposal: facts about what changed, proposed project-file updates, and explicit learnings. All writes require approval.

## Instructions

### Step 1: Gather Facts

1. Run these git commands in parallel:
   - `git diff --stat` (uncommitted changes)
   - `git log --oneline -10` (recent commits)
   - `git status --short`
   - `git branch --show-current`
2. From the conversation context, identify:
   - What was worked on (topic, goal)
   - Key decisions made
   - Problems encountered and resolved

### Step 2: Find Active Project File

1. Scan `$MEMORY_DIR/projects/` for the active project matching current work.
2. If found, read current Log and Next sections.
3. If not found, note this — project updates will be skipped.

### Step 3: Build Proposals

**Project Log entry:**
- Format: `- [YYYY-MM-DD] <one-liner summarizing what was accomplished>`
- Only factual — what was done, not what might be done.
- Maximum 2 log entries per wrapup.

**Project Next updates:**
- Mark completed items: `- [ ]` → `- [x]`
- Add newly discovered items as `- [ ]`
- Maximum 5 next-item changes per wrapup.

### Step 4: Identify Learnings

**Two categories — keep them distinct:**

1. **Explicit learnings** — things the user directly stated as corrections or preferences during this session. These are eligible for write proposals.
   - Must be a direct quote or clear paraphrase of something the user said.
   - Format: `[correction]` or `[preference]` tag + the learning.

2. **Possible inferred learnings** — patterns you noticed but the user didn't explicitly state. These are shown for awareness only, NOT eligible for write proposals in V1.
   - Format: `[observed, not confirmed]` tag + what you noticed.

**Routing — where an explicit learning lands (same approval-gating as today):**

- **Agent-relevant lessons** → MEMORIES.md `## Agent Calibration`. A lesson about *how a dev-team agent should audit / critique / build / QA* — e.g. a Vigil false-positive pattern, an Auddie blast-radius miss, a Forge build pitfall, a Sentinel verification gap. These feed the agents: the loop injects `## Agent Calibration` into Auddie/Vigil/Forge/Sentinel at spawn, so a lesson about agent behaviour belongs here to actually reach them.
- **the user's general corrections / preferences** → `## Behavioral Corrections` / `## Stated Preferences` as today. A correction or preference about how the user wants work done generally (not specific to an agent's audit/build/QA behaviour) stays in its existing section.

When a lesson reads as agent-relevant, propose it for `## Agent Calibration`; otherwise route to Behavioral Corrections / Stated Preferences. Tag the proposed destination so the approval is explicit.

Before proposing any learning for write, check `$MEMORY_DIR/memories/MEMORIES.md` for duplicates. Skip if already captured.

### Step 5: Check for Unresolved Items

Note any:
- Open questions that weren't answered
- Decisions that were deferred
- Work started but not completed

### Step 6: Produce Output

```
## Wrapup

### Facts
- <what was done — files, commits, branch, objective summary>
- <key decisions or outcomes>

### Proposals

**Project Log** (<project-name>):
- [YYYY-MM-DD] <one-liner>

**Next Updates:**
- [x] <completed item>
- [ ] <new discovered item>

### Explicit Learnings
- [correction] "<what the user said>"
- [preference] "<what the user said>"

### Observed (not confirmed)
- [observed] <pattern noticed — for awareness, no write proposed>

### Unresolved
- <open question or deferred decision>

Approve? You can say: approve all, approve specific items by name/number, skip a section, or skip entirely.
```

**Output constraints:**
- Facts: max 5 lines.
- Proposals: max 10 lines.
- Learnings: max 3 explicit + 3 observed.
- Unresolved: max 3 items.
- If a section is empty, omit it.
- Total output must fit one screen (~35 lines).

### Step 7: Execute Approved Writes

On approval, write ONLY what was approved:

1. **Project file updates:** Edit the Log section (append entry) and Next section (check off / add items) of the active project file.
2. **MEMORIES.md updates:** Only for approved explicit learnings. Append to the section the learning was routed to in Step 4: **`## Agent Calibration`** for agent-relevant lessons (how an agent should audit/critique/build/QA — these get injected into the dev-team agents at spawn), or **`## Behavioral Corrections`** / **`## Stated Preferences`** for the user's general corrections/preferences. Never edit existing entries.
3. **Project notes file:** If the session involved substantive technical reasoning, findings, or design decisions worth preserving, append a dated entry to the project's `-notes.md` file. Only if the content would be useful for future sessions — skip for routine work.

After writing, show a brief confirmation of what was written and where.

## Constraints

- Never write anything without explicit approval.
- Natural-language approval: "approve all", "approve 1 and 3", "skip memory updates", "just the log entry", etc.
- Never write to: `~/.claude/CLAUDE.md`, `~/dev/CLAUDE.md`, `settings.json`, hook scripts, rules files, known-issues files.
- If no meaningful work happened (no commits, no file edits, no significant discussion), say "Nothing significant to wrap up" and stop.
- Distinguish facts (objective, verifiable) from proposals (suggested updates) from learnings (corrections/preferences). Never mix them.
- Only explicit learnings (things the user directly said) are eligible for MEMORIES.md writes. Inferred/observed items are shown for awareness only.
