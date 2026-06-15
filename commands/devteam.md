# /devteam — Dev-Team Loop Driver

The Claude Code surface for the conversation-driven dev-team loop. It drives the
**post-design** loop — the part you used to narrate by hand.
The front half (Explore recon + authoring the design in conversation) you still
do by talking; `/devteam` takes over once a slice's spec section is ready and
drives Auddie → Vigil → Forge → Auddie → Sentinel, stopping at each human
touchpoint.

**Core principle:** the conversation orchestrates; the agents are
tools it calls via the Agent tool; the workspace holds the living artifacts. This
skill spawns Auddie/Vigil/Forge/Sentinel and feeds each one's output to the
next — agents never spawn each other. The thin helper (`devteam.py`) owns the
things the conversation is bad at: the run-log section format, the Decision Log
format, and slice state.

**One file by default.** Auddie, Vigil, and Sentinel do **not** write
files — they *return* their findings/verdict to you (the orchestrator), and you
append a **concise** section to the one project-file run log via
`devteam.py log-section`. Only Forge writes, and Forge writes *code*, not logs.
The run log and the Decision Log both live in the project file
(`<workspace>/<slug>.md`) by default — one file you read top to bottom, no
per-agent or per-cycle file sprawl. Splitting the run log into a separate `-data`
doc is the very-large-build exception; because the log path is a parameter, the
split is just pointing the appends at a different file — no behavior change here.

This is the SURFACE layer — it MAY reference the Agent tool, the
workspace, and Claude Code mechanics. The agents and the helper stay
runtime-agnostic; this file does not.

## Instructions

### Step 0: Resolve config (paths are instance config, not hardcoded)

Read these from the conversation context or the repo's config, never hardcode a
workspace path (paths are instance config):

- **target repo** — where Forge builds. Default: the repo the conversation is
  in (`git rev-parse --show-toplevel`).
- **project file** — the **one file by default**. Default:
  `<workspace>/<slug>.md`. Both the **run log** and the **Decision
  Log** live here — the helper appends run-log sections (`log-section`) and
  Decision Log entries (`log`) to this same file. You read it top to bottom.
- **spec path** — the living spec. Default: a section *inside* the project file,
  or `<workspace>/specs/<slug>-spec.md` for a substantial design.
- **state file** — the slice-state JSON, a separate **transient** file so a run
  survives across sessions. Pick a path the operator controls (e.g. alongside the
  project file). Pass it to every `devteam.py` state call.
- **MEMORIES path** — the memory file holding the live `## Agent Calibration`
  section that gets injected into each agent at spawn (instance config, never
  hardcoded — same as every other path here). Default:
  `<workspace>/MEMORIES.md` (or wherever you keep the calibration file). Pass it to the
  `devteam.py memory-extract` call in the injection step below. If absent, the
  inject block is empty (a valid "no live calibration yet" state, not an error).

Splitting to `-data` / `-notes` / a sequence doc is **very-large-build-only**.
Default is one project file. If a run is that large, point
the `log-section` appends at `<workspace>/<slug>-data.md` instead — same command,
different `--path`, no other change.

If any required path is unknown, ask once — do not guess a workspace location.

### Step 1: Detect intent

| Mode | Triggers | Action |
|------|----------|--------|
| RUN | "devteam", "run the loop", "build this slice", a slice description | Drive the loop from Auddie #1 |
| RESUME | "resume", "continue", "pick up" | Read slice state, re-enter at the saved phase |
| STATUS | "status", "where are we", no argument with an active state file | Read slice state, report position + last verdicts + open escapes |

Default with no argument: STATUS if a state file exists, else explain how to start.

### Step 2 (RUN): Initialise slice state, then let the driver advance it

Before the first agent, create the slice state via the helper:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/devteam.py state-init --path <state_file> --slice <N> --spec <spec_path>
```

**Commit the firm acceptance criteria to the registry (at firming, touchpoint
#1).** When you authored this firm slice in conversation (touchpoint #1, which
this skill does not drive), you named its firm acceptance criteria. As the
**orchestrator**, commit each one to the store now — one `accept add` per firm
criterion — so Sentinel has a machine-checkable definition of done to check off:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/devteam.py accept add --state-path <state_file> --slice <N> \
  --criterion "<one firm acceptance criterion>"
```

The markdown spec keeps the human-readable criteria; the registry holds the
checkable rows (the dual-form split applied to acceptance). `accept add` is
append-only — re-firming a slice appends fresh rows rather than mutating prior
ones. Do this once, here, before the loop drives Auddie.

Then drive the loop below. **The driver — not this conversation — computes the
transitions and persists state** (externalise the state, keep control thin). You
do **not** hand-write a state object any more. Instead:

- **Ask the driver where you are** with `next` (it reads durable state, so your
  position survives a compaction):
  ```bash
  python3 ${CLAUDE_PLUGIN_ROOT}/scripts/devteam.py next --path <state_file> --slice <N>
  ```
  `next` returns `{kind, agent, phase, ground, reminder, ...}`. If `kind` is
  `interrupt`, an open human touchpoint is surfaced **first** — handle it before
  spawning anything. If `kind` is `spawn`, spawn `agent` (after the grounding
  inject, Step 3). If `kind` is `done`, the slice is finished.

- **Report each loop event to the driver** with `advance` — it computes the new
  phase, records the verdict, opens/clears interrupts, and persists, so a mid-run
  interruption is recoverable:
  ```bash
  python3 ${CLAUDE_PLUGIN_ROOT}/scripts/devteam.py advance --path <state_file> --slice <N> --event <event>
  ```
  The event vocabulary (a clean token — you normalise the agent's glyph-y verdict
  to it, see below):

  | After… | event | the driver does |
  |--------|-------|-----------------|
  | Auddie #1 returns | `auddie1-done` | → Vigil |
  | Vigil `HOLDS` | `vigil-holds` | → Forge (no gate) |
  | Vigil `NEEDS-ADJUSTMENT` | `vigil-needs-adjustment` | +1 `vigil_rounds`, opens a Vigil-adjustment interrupt; **caps at 2** (`cap_reached`) |
  | Forge handoff | `forge-done` | → Auddie #2 |
  | Forge escalation | `forge-escalate --reenter vigil\|forge` | opens an escape interrupt; phase ← your `--reenter` |
  | Auddie #2 returns | `auddie2-done` | → Sentinel |
  | Sentinel `PASS` | `sentinel-pass` | → done, opens the output interrupt |
  | Sentinel `FAIL—CODE` | `sentinel-fail` | → Forge (**uncapped**) |
  | Sentinel `ESCAPE—DESIGN` | `sentinel-escape --reenter vigil\|forge` | opens an escape interrupt; phase ← your `--reenter` |

  **The escape/escalation events REQUIRE `--reenter vigil|forge`** — your
  "resume-from-the-right-point" call. The driver validates it is a real phase; it
  does **not** compute it (computing the re-entry from loop history is the engine
  the thin-reducer design refuses). Resolving an interrupt is just the next
  `advance` (the new event clears the prior interrupt).

**Normalise the verdict to the clean `--event` here, skill-side.** Vigil and
Sentinel return glyph-y verdicts (`HOLDS` / `NEEDS-ADJUSTMENT`; `PASS` /
`FAIL—CODE` / `ESCAPE—DESIGN`, with the dash glyph varying). Parse the leading
keyword glyph-robustly (rules below at Step 5) and map it to the clean event
token above before calling `advance`. `advance` is a pure reducer over the clean
vocabulary — it never sees a glyph.

### Step 3: Inject the grounding block (before spawning EACH agent)

Subagents do **not** inherit the session's memory — the Agent tool spawns each
one with a fresh context. So the project's North Star, its settled decisions, and
the live calibration never reach Auddie/Vigil/Forge/Sentinel unless you put them
in the spawn prompt. This step is the **grounding bridge** that makes the agents
ground-aware — so the loop stops re-litigating settled calls across sessions and
compaction.

Before spawning **each** agent (Auddie #1, Vigil, Forge, Auddie #2, Sentinel),
build the grounding block **fresh** and include it in that agent's spawn prompt:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/devteam.py ground \
  --north-star-path <project_file> \
  --memories-path <MEMORIES_path> \
  --state-path <state_file>
```

`ground` emits one block with three parts (any empty part is omitted):

- `## North Star` — the project's durable thesis (from the `## North Star`
  section of the project file).
- `## Settled Decisions` — the settled decisions from the store table (firm /
  executed / revised, supersession resolved), rendered in the Decision Log line
  format.
- `## Agent Calibration` — the live calibration (from the `## Agent Calibration`
  section of MEMORIES.md).

Take the printed block and add it to the spawn prompt as a clearly-marked block:

```
GROUNDING (live — North Star + settled decisions + calibration):
<the ground output>
```

Rules:

- **Build it fresh each spawn — never cache.** A new settled decision, an edited
  North Star, or an edited `## Agent Calibration` must reach the next spawn
  without a redeploy. Run `ground` per agent spawn, not once per run.
- **Empty parts are fine.** If a section (or its file) is absent and there are no
  settled decisions yet, `ground` prints nothing for that part — spawn the agent
  with whatever parts exist (possibly none). No error.
- **Paths are instance config** — pass the resolved project file, `<MEMORIES_path>`,
  and `<state_file>` from Step 0; never hardcode a workspace location.

The earlier calibration-only inject (`memory-extract --section "Agent
Calibration"`) is generalised by `ground`; `memory-extract` remains available for
reading any single section.

This block is in addition to each agent's normal inputs (the spec section, the
audit/diff, etc.) described per step below — it does not replace them.

---

## The loop (drive each step, stop at the four human touchpoints)

### 1. Auddie #1 — predicted blast radius

Spawn **Auddie** via the Agent tool with: the spec section for this slice + the
target repo. Auddie's job is gate #1 — facts about the current system the design
depends on, plus the **predicted** blast-radius map.

- Auddie **returns** its findings to you (it does not write a file). Append a
  **concise** section (a summary, not Auddie's full dump) to the project-file run
  log:
  ```bash
  python3 ${CLAUDE_PLUGIN_ROOT}/scripts/devteam.py log-section --path <project_file> \
    --label "Auddie #1 — predicted impact" \
    --body "<concise summary of the key facts + predicted blast radius>"
  ```
- Report the event: `advance --event auddie1-done` (→ Vigil).
- Auddie produces FACTS only — no verdict to branch on. Feed it to Vigil.

**When the predicted blast radius is BIG, OFFER the fan-out workflow.** This is
Auddie's own effort-scaling heuristic (`agents/auddie.md` — "few calls" for a
contained change vs "fan out" for a schema / shared-module / large-surface
change). When the slice touches a schema, a shared module, or a wide surface,
**offer the operator the saved `audit-gates` workflow** instead of a single
Auddie spawn. The workflow fans Auddie's audit by surface (Review-by-surface →
Verify-each-finding adversarially → Synthesis into ONE coherent blast-radius map,
same FACT/INFERENCE contract) — it never fans the builder. A skill or subagent
**cannot launch a workflow**; you only OFFER it, and the **operator opts in**
(e.g. `ultracode`, or launching the saved `/audit-gates` workflow by name). The
operator's opt-in is the human-in-the-loop trigger. A **small / contained slice
keeps the single-spined Auddie spawn above unchanged** (today's behavior — do not
offer the workflow for a local, hop-1 change).

### 2. Vigil — design gate

Spawn **Vigil** with: the spec + Auddie #1's audit + the grounding block from
Step 3 (which carries the **settled decisions** — Vigil treats them as binding,
re-opening one only on a new contradicting Auddie fact). Vigil returns a binary
verdict.

Parse the verdict by its keyword (Vigil returns `HOLDS` or `NEEDS-ADJUSTMENT`):

After parsing, append a **concise** section to the project-file run log:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/devteam.py log-section --path <project_file> \
  --label "Vigil — HOLDS | NEEDS-ADJUSTMENT" \
  --body "<the verdict + the deciding findings, summarised>"
```

- **`HOLDS`** → proceed straight to Forge. **No human gate** (HOLDS proceeds to
  build on its own). Report `advance --event vigil-holds` (records
  `verdicts.vigil = "HOLDS"`, → Forge), continue.

- **`NEEDS-ADJUSTMENT`** → **HUMAN TOUCHPOINT #2 (Vigil adjustment).**
  Report `advance --event vigil-needs-adjustment` — the driver increments
  `vigil_rounds`, records the verdict, and opens a Vigil-adjustment interrupt
  (`next` will surface it). Then stop. Surface Vigil's findings table and the
  concrete change wanted for each blocking finding. The human revises the spec
  in conversation. Then:
  1. Log the decision via the helper. Pass `--state-path` so the decision lands
     in the **authoritative store table** (and is rendered to the markdown
     Decision Log from the same args). The table is
     what the grounding inject reads:
     ```bash
     python3 ${CLAUDE_PLUGIN_ROOT}/scripts/devteam.py log --path <project_file> --state-path <state_file> \
       --status revised \
       --decision "<what changed in the spec>" \
       --rationale "<Vigil finding that triggered it>"
     ```
     If the revision supersedes a prior decision, add `--supersedes <id>` (the
     `id` from `devteam.py decisions`) so the superseded one stops being settled.
  2. Re-spawn **Vigil** with the revised spec + Auddie #1's audit (re-run
     Auddie #1 too if the revision changed which elements the design touches).
     The next `advance` clears the adjustment interrupt.
  3. **Max 2 rounds** (Vigil's own cap, enforced by the driver:
     `cap_reached` is set on the interrupt once `vigil_rounds` hits 2). If round 2
     still returns `NEEDS-ADJUSTMENT`, stop and hand both positions — Vigil's and
     the design's — to the human to decide. Do not loop a third time.

### 3. Forge — build the slice

**Claim the slice before the build (the claim gate is skill-side).**
If a second session might touch this project store, `acquire` the slice claim
before spawning Forge:
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/devteam.py claim acquire --state-path <state_file> \
  --slice <N> --owner <session-id>
```
The store **refuses** (clean `error: …` / rc-1, no row inserted) if this slice's
declared `file_ownership` overlaps a slice another owner already holds, or if a
`depends_on` slice has not passed (`slice_state.phase != 'done'`). A refusal is a
**HUMAN TOUCHPOINT** — surface it; the human serialises (waits / picks another
slice) or breaks a stale claim with `claim release --force`. A same-owner
re-acquire is idempotent. This gate is **skill-side, around the build — the
reducer stays pure** (claims are not loop events, so `advance`/`next` are
untouched; `next` stays loop-position only). The owner id is supplied explicitly
by the operator/skill — never auto-derived.

Spawn **Forge** with: the firm spec section, the Decision Log, Auddie #1's map,
and the codebase. Forge builds on a feature branch, **commits its work to that
branch** (never merging, pushing, or touching `main`), and hands off — the human
reviews, merges, and pulls. Committing to the branch is mandatory: uncommitted
work in a shared repo can be absorbed by a concurrent session's `git add`. Forge
is the only agent that writes — it writes *code* (and
its branch commit), not the run log.

- Append a **concise** handoff summary to the project-file run log:
  ```bash
  python3 ${CLAUDE_PLUGIN_ROOT}/scripts/devteam.py log-section --path <project_file> \
    --label "Forge — handoff" \
    --body "<files changed + key decisions, summarised from Forge's handoff>"
  ```
- If Forge returns a **blocked / design-level escalation** (a firm section is
  contradictory, the spec is wrong, or a fix would break existing behavior) →
  treat it like an escape: **HUMAN TOUCHPOINT #3.** Report
  `advance --event forge-escalate --reenter <vigil|forge>` — **you** pick the
  re-entry target (back to Vigil if the design changed, else back to Forge); the
  driver validates it and opens an escape interrupt. Surface it, the human revises
  the spec, log the decision, then the next `advance` re-enters at your target.
- Otherwise report `advance --event forge-done` (→ Auddie #2) and continue.

### 4. Auddie #2 — actual blast radius (every cycle)

Spawn **Auddie** with: the **actual diff** (`git diff` on the feature branch) +
the predicted map from Auddie #1. Auddie #2 compares actual to predicted and
calls out the delta (`as-predicted` / `wider-than-predicted` /
`narrower-than-predicted`).

- Auddie **returns** its findings to you (it does not write a file). Append a
  **concise** section per cycle to the project-file run log — put the cycle
  number `N` in the label so the audit trail is preserved without separate files
  (cycle 1 on the first build, cycle 2 after the first `FAIL—CODE` fix, and so
  on):
  ```bash
  python3 ${CLAUDE_PLUGIN_ROOT}/scripts/devteam.py log-section --path <project_file> \
    --label "Auddie #2 cycle <N> — actual vs predicted" \
    --body "<the delta: as-predicted / wider / narrower, summarised>"
  ```
  Each cycle is its own dated section appended in order — no overwrite, no
  numbered file.
- **Re-run on EVERY Forge↔Sentinel cycle** — each code change can shift the
  blast radius. Do not reuse a prior cycle's map.
- **When the actual blast radius is BIG, OFFER the fan-out workflow here too**
  (same effort-scaling heuristic as Auddie #1). The saved `audit-gates` workflow
  runs in its `actual` mode — it audits the diff and compares against the
  predicted map from Auddie #1, calling out the delta. As at Auddie #1, you only
  OFFER; a skill/subagent cannot launch it, the **operator opts in**, and a
  small / contained diff keeps the single-spined Auddie #2 spawn unchanged.
- Report `advance --event auddie2-done` (→ Sentinel), feed Auddie #2's
  actual-impact map to Sentinel.

### 5. Sentinel — build gate

Spawn **Sentinel** with: Forge's diff + the spec + Auddie #2's actual-impact
map + the grounding block from Step 3 (which carries the **settled decisions** —
Sentinel treats them as binding, re-opening one only on a new contradicting
Auddie fact). Sentinel runs deterministic → static → dynamic passes and
**returns** one of three verdicts to you (it does not write a file).

**Sentinel checks the acceptance registry.** It reads the slice's
committed criteria (`accept list --state-path <state_file> --slice <N>`) and
records a result per criterion as it verifies them (`accept result ... --passed
0|1`). All-`passed=1` (`floor_met: true`) is a **REQUIRED FLOOR** for PASS — any
`0` or unchecked `NULL` **blocks** PASS. But the floor is **necessary, not
sufficient**: a green registry **does not force** a PASS. Sentinel's holistic
`FAIL—CODE` / `ESCAPE—DESIGN` over an *unenumerated* issue (a regression, an
unwired substrate, a wider-than-designed blast radius, a security bug) stays live
**above** the floor — the registry never muzzles the judge.

**Parse the verdict by its LEADING KEYWORD, glyph-robustly.** Sentinel returns
`FAIL—CODE` and `ESCAPE—DESIGN` with an em-dash, but the dash glyph varies
(em-dash `—`, en-dash `–`, hyphen `-`, with or without surrounding spaces). Do
**not** match the whole string. Take the first whitespace-or-dash-delimited
token, uppercase it, and branch on it:

- token starts with `PASS` → **PASS** → event `sentinel-pass`
- token starts with `FAIL` → **FAIL—CODE** (any dash/spacing) → event `sentinel-fail`
- token starts with `ESCAPE` → **ESCAPE—DESIGN** (any dash/spacing) → event
  `sentinel-escape` (+ `--reenter`)

Concretely: strip the verdict line, split on the first run of `[-–—\s]+`, take
element 0, uppercase, compare the prefix. This is robust to `FAIL—CODE`,
`FAIL-CODE`, `FAIL — CODE`, `ESCAPE–DESIGN`, etc. This glyph normalisation is the
**skill's** job — `advance` only ever sees the clean event token.

After parsing, append a **concise** section to the project-file run log:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/devteam.py log-section --path <project_file> \
  --label "Sentinel — PASS | FAIL—CODE | ESCAPE—DESIGN" \
  --body "<the verdict + the evidence/bugs/named escape, summarised>"
```

Branch:

- **`PASS`** → **HUMAN TOUCHPOINT #4 (Output).** Report
  `advance --event sentinel-pass` — the driver records `verdicts.sentinel =
  "PASS"`, sets `phase = "done"`, and opens the output interrupt (`next` surfaces
  it). The slice is done. Surface the result to the human, then update the tracker
  section of the **project file** (PASS → output + update the project log) — a
  one-liner in the tracker; the per-step detail is already in the run log. The
  human commits Forge's diff. **Release the slice claim** — release is an
  **explicit operator action surfaced at this PASS
  touchpoint**, not an automatic skill step, so the human controls when the lock
  frees (and on an operator abort, the human releases too):
  ```bash
  python3 ${CLAUDE_PLUGIN_ROOT}/scripts/devteam.py claim release --state-path <state_file> \
    --slice <N> --owner <session-id>
  ```

  **After the human merges, run the integration gate** (see below).

- **`FAIL—CODE`** → back to **Forge** with Sentinel's filed bugs. Report
  `advance --event sentinel-fail` (records the verdict, → Forge). Forge fixes,
  then **re-run Auddie #2 on the new diff** (step 4), then Sentinel again (step
  5). **No cycle cap** — grind code-level bugs for as many rounds as it takes
  (the driver leaves this loop **uncapped**, unlike Vigil).

  - If Forge disputes a bug, route the dispute back to Sentinel for adjudication
    (`CONFIRMED` / `WITHDRAWN`) before continuing. Sentinel's adjudication
    stands.

- **`ESCAPE—DESIGN`** → **HUMAN TOUCHPOINT #3 (Escape).** Stop. This is a
  design-level event (spec wrong, a firm decision must change, existing behavior
  broke, or Auddie #2 shows a blast radius wider than designed). Report
  `advance --event sentinel-escape --reenter <vigil|forge>` — **you** pick the
  re-entry target (see step 3 below); the driver validates it and opens an escape
  interrupt (which generalises the old `open_escapes` — `next` surfaces it
  first). Surface the named escape. The human revises the spec. Then:
  1. Log the decision. Pass `--state-path` so it lands in the **authoritative
     store table** (rendered to markdown from the same args); add `--supersedes
     <id>` if it replaces a prior settled call:
     ```bash
     python3 ${CLAUDE_PLUGIN_ROOT}/scripts/devteam.py log --path <project_file> --state-path <state_file> \
       --status revised \
       --decision "<what changed>" --rationale "<the escape Sentinel named>"
     ```
  2. The next `advance` clears the escape interrupt automatically.
  3. **Re-enter at the right point** (raise-and-revise) — this is the
     `--reenter` you passed: if the design changed, go back to **Vigil**
     (re-judge the revised design); if only an acceptance criterion was clarified,
     go back to **Forge**. Use judgment —
     the rule is "resume from the right point," not always the top.

---

## The four human touchpoints

The loop is **interrupt-driven**: drive to a touchpoint, stop, let the human
respond in conversation, then continue. The human is never pulled in to
rubber-stamp — only on a real decision.

1. **Design** — authored in conversation *before* `/devteam` runs (not driven by
   this skill). The orchestrator commits the firm acceptance criteria to the
   registry here (`accept add`, Step 2) so Sentinel has a checkable
   definition-of-done floor.
2. **Vigil adjustment** — only on `NEEDS-ADJUSTMENT`.
3. **Escape** — only on Forge's design-level escalation or Sentinel's
   `ESCAPE—DESIGN`.
4. **Output** — on `PASS`.

`HOLDS` proceeds to build on its own. No gate there.

## Raise-and-revise (cross-cutting, not a phase)

Any agent that finds the plan diverges from reality returns it as a flagged
finding (Vigil `NEEDS-ADJUSTMENT`, Forge escalation, Sentinel `ESCAPE—DESIGN`).
The handling is always the same shape:

1. Surface the divergence to the human.
2. The human revises the living spec.
3. Log the decision via `devteam.py log` (so the rationale is preserved and the
   format cannot drift).
4. Resume from the right point.

This is the mechanism behind every "op reframed at execute time" in a multi-slice
campaign — the thing a static plan cannot do.

---

## The integration gate (post-merge, project-level)

The per-slice loop ends at `done` on Sentinel's `PASS`, and the human merges
Forge's branch. Each slice passed its **own** Sentinel on its **own** branch —
but that does not answer whether the **assembled, merged system composes
end-to-end**. The **integration gate** does, as a **project-level axis run after
the merge** — **not** an in-loop reducer phase (the reducer stays a pure function
of loop events; integration is not a loop event, the same stance as the
skill-side claim gate).

It is an **explicit orchestrator step at merge** (not an automatic skill action).
After the human merges the slice into the assembled branch:

1. **Run the dual gate — it passes iff BOTH halves are green:**
   - **(a) the end-to-end composition scenario** — `python3 tests/test_integration.py`
     (also collected by `uv run pytest`): drives a synthetic project through a
     complete loop via the **real assembled CLI** on one DB (`state-init` →
     `accept add` → `slice set` → `ground` → `claim acquire`/`release` →
     `advance`/`next` through every phase → `accept result` → `log` →
     `integration record`) and asserts **cross-table consistency** (the slice is
     `done`, the acceptance floor is met, the decision is settled, the claim is
     released, the integration row is recorded). This proves "merged slices
     compose" — no per-slice gate performs it.
   - **(b) the existing full suite** — `uv run pytest` (the regression half).
2. **Read the addressed slice's acceptance floor** (`accept list --slice <N>` →
   `criteria_floor_met`, already met at the PASS).
3. **Record the outcome** (the gate *records*; it does not run the suite itself —
   no heavy runner in the tool):
   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/devteam.py integration record --state-path <state_file> \
     --slice <N> --status passed   # or failed
   ```

The "merged slices compose" property is proven by the **scenario** exercising
every command surface together — **not** by re-aggregating prior slices' floors
(cross-slice re-verification is the regression corpus's job). The status vocabulary is
`passed`/`failed`, enforced at both the CLI and the store.

---

## The regression corpus — grow it on every pass

The **regression corpus** is the permanent, perturbed, behaviour-level net that
re-runs the load-bearing real-path invariant of **every shipped slice** on every
new slice — the cross-slice re-verification the integration gate defers here. It
lives in `regression.py` (a leaf module driving the real assembled CLI) and runs
two ways:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/devteam.py regression run    # replay the corpus; exit 0 iff all hold
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/devteam.py regression list   # enumerate entries with slice tags
```

`regression run`'s **exit code is the gate** (0 iff every invariant holds across
its perturbations). The corpus is also dual-mode — `uv run pytest` collects it
(`tests/test_regression.py`), so it fires on every slice's suite run too.

**The growth duty (an explicit step at merge, like the integration record).**
When a slice passes Sentinel and the human merges it, **add that slice's key
real-path invariant to the corpus** — append a tagged entry to `regression.py`'s
`CORPUS` (its positive perturbations + its reachable negative case) at the growth
seam, then confirm `regression run` still exits 0. This is the same kind of
documented duty as the orchestrator committing acceptance criteria at firming
and recording `integration record` at merge — a convention named here, not loop
machinery. An unbuilt slice gets **no** entry until it passes; its slot is the
seam marked in `regression.py`.

---

## The macro convention — firm-one-execute-one (convention, not machinery)

`/devteam` drives **one slice** (the micro-loop). A multi-slice build is run by
*convention*, not by anything this skill does:

1. The slices are listed in the project file, each marked `firm` / `sketched` /
   `deferred`. Only the **next** slice is firmed in full.
2. You run `/devteam` on that next firm slice and drive it to `PASS`.
3. You mark the slice done in the project file (a one-liner in the tracker).
4. What that slice taught you reshapes the next one — firm it now, then repeat.

There is **no campaign machinery to invoke here.** This skill knows only about a
single slice; the firm-one-execute-one cadence across slices is the operator's
convention, recorded in the project file's slice list and Decision Log. Do not
add multi-slice orchestration to this skill — the macro-loop is convention
layered on the working micro-loop.

---

## RESUME mode (`/devteam resume` picks up from slice state)

**Ask the driver where you are** — `next` re-derives the position from durable
state, so it survives a compaction:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/devteam.py next --path <state_file> --slice <N>
```

Branch on the `kind` it returns:

- `kind = "interrupt"` → the run is paused at a human touchpoint. **Surface the
  `interrupt` first** and wait for the human's revision before re-entering (this
  generalises the old `open_escapes` check — a Vigil adjustment, a Forge/Sentinel
  escape, or a PASS output, all re-presented after a compaction, not dropped).
- `kind = "spawn"` → spawn the named `agent` (after the grounding inject, Step 3).
  The `agent` maps directly: `auddie_1` / `vigil` / `forge` / `auddie_2` /
  `sentinel`. Honour the Vigil cap — if a surfaced adjustment interrupt has
  `cap_reached: true`, do not start a 3rd round; hand both positions to the human.
- `kind = "done"` → the slice finished; report the PASS, nothing to resume.

`next` reads through the store, so you never hand-parse the saved `phase` — the
driver is the state-of-record.

## STATUS mode

Read the state (`state-read`) and ask the driver for the next action (`next`),
then report, in a few lines:

- which slice, current `phase`
- last verdict per judge (`verdicts.vigil`, `verdicts.sentinel`)
- `vigil_rounds` used
- any open `pending_interrupt` (the thing blocking the human — `next` surfaces it
  first; this generalises the old `open_escapes`)
- the single next action (`next` returns it: spawn `agent` / surface interrupt /
  done)

If no state file exists: "No active run. Author the design in conversation, then
run `/devteam` to drive the post-design loop."

---

## Behavioral rules

1. **The conversation drives** — spawn agents via the Agent tool; never let
   agents spawn each other.
2. **Auddie feeds the judges** — Auddie #1 → Vigil, Auddie #2 → Sentinel. Facts
   stay separate from judgment.
3. **Auddie #2 runs every cycle** — re-audit the new diff each Forge↔Sentinel
   round. Never reuse a stale map.
4. **The helper owns format + state** — append run-log sections (`log-section`)
   and Decision Log entries (`log`), and read/write slice state, only via
   `devteam.py`. Never hand-format a run-log section or a Decision Log entry; the
   fixed format must not drift.
5. **One file by default; agents do not write logs** — Auddie, Vigil, and
   Sentinel **return** their findings/verdict to you; *you* append a concise
   section to the project-file run log. Only Forge writes (code, not logs). The
   run log and the Decision Log share the one project file.
6. **Paths are parameters** — pass the project file, the spec, and the state file
   explicitly. Never hardcode a workspace location. Splitting the run log
   to `-data` is massive-build-only — same command, different `--path`.
7. **Never edit code or the spec** — Forge edits code; the human revises the
   spec. This skill orchestrates and reads.
8. **Forge commits to its branch; the human merges** — Forge commits its named
   changes to its feature branch (never merging/pushing/touching `main`). The
   human reviews the branch, then merges and pulls. The orchestrator never
   commits, merges, or pushes.
9. **Parse verdicts glyph-robustly** — Vigil: `HOLDS` / `NEEDS-ADJUSTMENT`.
   Sentinel: leading keyword `PASS` / `FAIL` / `ESCAPE`, dash-agnostic.
10. **Stop at the four touchpoints** — and only there. `HOLDS` proceeds on its
    own.
11. **Let the driver advance the state** — report each loop event with
    `devteam.py advance` (the driver computes the transition, records the
    verdict, opens/clears interrupts, and persists). Never hand-write a state
    object or hand-compute a transition — the driver is the state-of-record, so
    any interruption is recoverable by `/devteam resume` (which reads `next`).
    The escape/escalation events carry your `--reenter vigil|forge` call; the
    driver validates it, it does not compute it.
12. **Footer-rotation discipline** — Decision Log entries (`devteam.py log`) are
    append-only; the audit trail never rewrites. Spec-body footers split two
    ways. **FINDING amendments** (what Auddie / Vigil / Sentinel found and what
    was decided about it) stay as footers in the spec body — audit-trail
    honesty. **SPEC-SKETCH corrections** (a later round's fix to a sketched
    signature, SQL shape, or contract detail) fold back into the canonical spec
    paragraph in the same cycle that lands the fix, and the superseded footer
    is dropped. Forge owns the consolidation step (carve-out from rule 7: Forge
    may edit the canonical paragraph the spec sketched, only to land the
    already-decided correction). At Step 3, Forge consolidates as part of the
    build. At the `ESCAPE—DESIGN` branch of Step 5, the human's spec revision
    rotates any superseded SPEC-SKETCH footers in the same edit.
13. **Block HOLDS on unverifiable-claim findings** — Vigil at Step 2 returns
    `NEEDS-ADJUSTMENT` (not `HOLDS`) when the spec asserts a behavior in
    ships-with-X / guard-on-Y form where X or Y is a default or a future, with
    no NAMED CODE (`file:func` reference that implements it) and no NAMED
    FUTURE (Phase N+1 §M trigger that will). This is an **UNVERIFIABLE-CLAIM**
    finding and it blocks. Distinguish from **UNVERIFIABLE-AUDIT** (an
    audit-coverage gap — the auditor cannot reach the evidence; non-blocking,
    existing behavior). Sentinel mirrors at Step 5: an unverifiable-claim
    discovered after build returns `ESCAPE—DESIGN`, not `PASS`. The skill is
    the gate — Vigil flags, the skill blocks HOLDS, the human at touchpoint #2
    revises (either NAMED CODE or NAMED FUTURE language), Vigil re-judges.
    This skill never edits the spec itself.
14. **Wired-substrate PASS criterion** — Sentinel at Step 5 includes, in its
    deterministic pass, a grep of the diff plus the repo for **production**
    call sites of every table, directory, env var, and behavioral guard the
    spec section names. Test fixtures and scaffold `.gitkeep` entries do not
    count. If a named substrate has zero production callers, Sentinel returns
    `ESCAPE—DESIGN` (not `PASS` and not `FAIL—CODE`) — the route is to the
    human, who decides wire-it now or amend the spec to honest-deferral
    language. "Named substrate ships unwired" is the **fourth** `ESCAPE—DESIGN`
    trigger alongside the three at Step 5 (spec wrong, firm decision must
    change, existing behavior broke, blast radius wider than designed).
15. **End-to-end demo trace PASS criterion** — every firm success criterion
    that mentions partner UI (React page, Office Add-in surface, browser
    click, "partner does X") requires an end-to-end trace at Step 5: UI click
    → SDK / network layer → backend → DB → return-trip. A curl-sequence
    script, a Playwright run, or equivalent counts; component-isolation smoke
    tests do not. Sub-slice scoping under the macro convention (firm-one
    -execute-one) bounds **the build**, not the gate — a partner-UI success
    criterion that stays in the slice keeps its full end-to-end evidence
    burden. If the trace is missing or only exercises a component in
    isolation, Sentinel returns `ESCAPE—DESIGN`.
16. **Acceptance-registry floor** — the orchestrator commits each
    firm acceptance criterion to the store at touchpoint #1 (`accept add`,
    Step 2). At Step 5 Sentinel records a result per criterion (`accept
    result`); all-`passed=1` is a **REQUIRED FLOOR** for PASS (any `0`/`NULL`
    **blocks** PASS) but is **necessary, not sufficient** — a green registry
    **does not force** a PASS, and never muzzles a `FAIL—CODE` / `ESCAPE—DESIGN`
    Sentinel reaches on an unenumerated issue. The registry makes the enumerated
    criteria machine-checkable; the holistic judge stays live above it.

## Failure handling

- **Spec section missing or unreadable** → stop, tell the human the design isn't
  ready; this skill drives the post-design loop, it does not author the design.
- **An agent returns an unparseable verdict** → do not guess. Surface the raw
  verdict to the human and ask how to proceed.
- **State file corrupted** → surface it; offer to re-init with `state-init`
  (loses position) or let the human inspect the JSON.
- **`devteam.py` errors** (missing project file, empty run-log body, bad status
  keyword) → it exits non-zero with a message on stderr; surface that message,
  fix the path or input, retry. A helper failure never silently advances the
  loop.
```
