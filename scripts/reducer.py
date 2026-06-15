#!/usr/bin/env python3
"""The reducer — a THIN event→state function + `next`.

The driver is the loop's state-of-record: the conversation reports a loop EVENT,
the reducer applies it to the slice state deterministically, and the new state
is persisted (by the devteam.py CLI). This is a **reducer, not an engine** —
most transitions are a pure function of the event (a table lookup); the ONE
exception is the escape/escalation re-entry, where the human supplies the
re-entry target (`--reenter vigil|forge`) which the reducer **validates** against
`LOOP_PHASES` but does **not** compute (computing it from loop history is exactly
the engine the design refuses).

Split out of devteam.py (the decision-format + state-plumbing layer) as a
distinct concern — both to keep each file under the repo_quality 1000-line limit
(the same split discipline used for test_grounding.py) and because the loop
vocabulary (`LOOP_PHASES` / `AGENT_KEYS`) belongs WITH its one real consumer, the
reducer (defined-and-used, not defined-and-unused).

Portable plumbing, like devteam.py and store.py: standard library only, no
Claude Code dependency, no I/O (pure functions — the CLI persists the result).
The glyph-robust verdict parsing (PASS/FAIL/ESCAPE, variable dashes) stays on
the skill side; by the time an event reaches `advance` it is already a clean
token from the vocabulary below.
"""

from __future__ import annotations

from datetime import datetime, timezone

# Loop phases the state tracks. `idle` and `done` bracket the run. The skill
# sets `phase` to whichever it is about to enter. Wired here (the reducer is the
# real consumer): `next` maps a phase to the next action, and `advance`
# validates the operator's --reenter against these.
LOOP_PHASES = (
    "idle",
    "auddie_1",
    "vigil",
    "forge",
    "auddie_2",
    "sentinel",
    "done",
)

# Agents whose last verdict the state carries (the judges). Wired here as the
# verdict-bearing keys the reducer records on the relevant events.
AGENT_KEYS = ("vigil", "sentinel")

# Vigil's own cap: at most 2 design rounds. A 3rd `vigil-needs-adjustment` is
# never started — the loop hands both positions to the human instead.
# `sentinel-fail` → forge is deliberately UNCAPPED — the cap asymmetry the
# reducer encodes.
VIGIL_MAX_ROUNDS = 2


def _now_iso() -> str:
    """UTC ISO8601 with a Z suffix — matches devteam.py / store.py `_now_iso`.
    Duplicated as a one-line stdlib leaf primitive rather than imported, so the
    reducer stays a leaf module with no devteam/store dependency (same call the
    other two modules make; a timestamp is not a shared abstraction)."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# The loop events. Each maps to the phase the loop enters next. The two
# escape/escalation events are handled separately (they need --reenter) and so
# are NOT in this pure table.
PURE_TRANSITIONS = {
    "auddie1-done": "vigil",  # Auddie #1 facts done → Vigil judges
    "vigil-holds": "forge",  # HOLDS proceeds to build (no human gate)
    "forge-done": "auddie_2",  # Forge built → Auddie #2 re-audits the diff
    "auddie2-done": "sentinel",  # actual-impact map done → Sentinel gate
    "sentinel-fail": "forge",  # FAIL—CODE → back to Forge (UNCAPPED)
    "sentinel-pass": "done",  # PASS → slice done (output touchpoint)
    "vigil-needs-adjustment": "vigil",  # re-spawn Vigil after the human revises
}

# Events that REQUIRE an operator-supplied --reenter target.
# The reducer validates the target is a real phase; it does not compute it.
ESCAPE_EVENTS = ("forge-escalate", "sentinel-escape")

ALL_EVENTS = tuple(PURE_TRANSITIONS) + ESCAPE_EVENTS

# Which judge's verdict an event records, and the verdict value to record.
EVENT_VERDICTS = {
    "vigil-holds": ("vigil", "HOLDS"),
    "vigil-needs-adjustment": ("vigil", "NEEDS-ADJUSTMENT"),
    "sentinel-pass": ("sentinel", "PASS"),
    "sentinel-fail": ("sentinel", "FAIL—CODE"),
    "sentinel-escape": ("sentinel", "ESCAPE—DESIGN"),
}

# Re-entry targets the human may choose for an escape (--reenter vigil|forge).
REENTER_CHOICES = ("vigil", "forge")

# Source-phase guard: the phase(s) each event is allowed to fire FROM. An event
# from any other phase is rejected with a clean ValueError before the state is
# touched — so a stray or duplicated event (e.g. after a compaction) cannot drive
# the slice from a phase where the transition is meaningless. `done` is absent
# from every value list, so `done` is TERMINAL: no event fires from it (the
# explicit terminal check below gives the clearer message).
#
# `auddie1-done` is allowed from BOTH `idle` and `auddie_1`: the loop opens a
# slice at `idle` (the first audit fires before the phase advances) AND re-enters
# at `auddie_1` on a resume — both are real entry points, so both are valid.
# The escape events fire from the phase the escaping judge runs in; the operator
# then re-enters at `--reenter`, and the NEXT event fires from that target phase
# (this map is keyed on the phase, so re-entry is handled with no extra rule).
ALLOWED_FROM = {
    "auddie1-done": ("idle", "auddie_1"),
    "vigil-holds": ("vigil",),
    "vigil-needs-adjustment": ("vigil",),
    "forge-done": ("forge",),
    "auddie2-done": ("auddie_2",),
    "sentinel-fail": ("sentinel",),
    "sentinel-pass": ("sentinel",),
    "forge-escalate": ("forge",),
    "sentinel-escape": ("sentinel",),
}


def advance(state: dict, event: str, reenter: str | None = None) -> dict:
    """Apply a loop `event` to the slice `state`, returning the new state.

    Pure (no I/O) — the CLI persists the result. A reducer, not an engine: the
    transition is a table lookup for every event except the escape/escalation
    pair, whose re-entry target is operator-supplied (`reenter`) and only
    VALIDATED here, never computed.

    Rules encoded:
      - Unknown event → ValueError (the vocabulary is enforced).
      - `done` is TERMINAL → any event applied to a `done` slice raises a clean
        ValueError; the slice is never silently revived (revisiting finished
        work is a NEW slice). The guard raises BEFORE the state is copied, so a
        rejected event leaves the stored state (incl. `pending_interrupt`)
        untouched.
      - Source-phase guard → each event fires only from a phase where it is
        valid (`ALLOWED_FROM`); an event from a wrong phase raises a clean
        ValueError (also before any mutation).
      - `vigil-needs-adjustment` increments `vigil_rounds` and opens a Vigil
        adjustment interrupt; once `vigil_rounds` reaches VIGIL_MAX_ROUNDS the
        interrupt is flagged cap-reached (no 3rd round — hand both positions to
        the human). `sentinel-fail` → forge is UNCAPPED (the cap asymmetry).
      - `forge-escalate` / `sentinel-escape` REQUIRE a valid `reenter`
        (member of LOOP_PHASES, one of REENTER_CHOICES) and open an escape
        interrupt; phase becomes the operator's `reenter` target.
      - `sentinel-pass` → done + an output interrupt.

    Applying any event first CLEARS a prior `pending_interrupt` (the event IS
    the human's resolution of it), then sets a fresh one if the new event opens
    a touchpoint. So resolving an interrupt is just the next `advance`.
    """
    if event not in ALL_EVENTS:
        raise ValueError(f"unknown event {event!r} — must be one of {ALL_EVENTS}")

    # Source-phase guard — raise BEFORE copying/mutating state, so a rejected
    # event preserves the stored state (incl. an open pending_interrupt).
    phase = state.get("phase", "idle")
    if phase == "done":
        raise ValueError(
            "slice is done (terminal); start a new slice to revisit — "
            f"event {event!r} rejected"
        )
    allowed = ALLOWED_FROM.get(event, ())
    if phase not in allowed:
        raise ValueError(
            f"event {event!r} cannot fire from phase {phase!r} (allowed from {allowed})"
        )

    if reenter is not None and event not in ESCAPE_EVENTS:
        # --reenter is the operator's escape re-entry target; for any other event
        # it would be silently ignored, so surface the mistake instead.
        raise ValueError(
            f"--reenter is only valid for the escape/escalation events "
            f"{ESCAPE_EVENTS}; event {event!r} does not take a re-entry target"
        )

    new = dict(state)
    # The event resolves any prior touchpoint. Set to None (not pop): the store's
    # partial-merge write (store.write_slice_state) carries omitted keys
    # FORWARD, so omitting the key would resurrect a stale interrupt from the
    # stored row. An explicit None overrides it on the merge; readers treat a
    # falsy `pending_interrupt` as "no open touchpoint" (next_action does).
    new["pending_interrupt"] = None

    # Record the judge verdict this event carries (if any). The agent must be one
    # of the judges the state tracks (AGENT_KEYS) — wiring that vocabulary as the
    # validated key set so a future EVENT_VERDICTS typo for a non-judge agent is
    # caught, not silently written.
    if event in EVENT_VERDICTS:
        agent, verdict = EVENT_VERDICTS[event]
        if agent not in AGENT_KEYS:
            raise ValueError(
                f"event {event!r} records a verdict for {agent!r}, which is not a "
                f"tracked judge (must be one of {AGENT_KEYS})"
            )
        verdicts = dict(new.get("verdicts") or {})
        verdicts[agent] = verdict
        new["verdicts"] = verdicts

    if event in ESCAPE_EVENTS:
        if reenter is None:
            raise ValueError(
                f"event {event!r} requires --reenter (the operator's "
                f"resume-from-the-right-point call — one of {REENTER_CHOICES}); "
                f"the reducer validates it, it does not compute it"
            )
        if reenter not in LOOP_PHASES:
            raise ValueError(
                f"--reenter {reenter!r} is not a valid phase "
                f"(must be one of {LOOP_PHASES})"
            )
        if reenter not in REENTER_CHOICES:
            raise ValueError(
                f"--reenter {reenter!r} is not a valid escape re-entry target "
                f"(must be one of {REENTER_CHOICES})"
            )
        new["phase"] = reenter
        new["pending_interrupt"] = {
            "kind": "escape",
            "event": event,
            "reenter": reenter,
            "raised_at": _now_iso(),
        }
        return new

    if event == "vigil-needs-adjustment":
        rounds = (new.get("vigil_rounds") or 0) + 1
        new["vigil_rounds"] = rounds
        new["phase"] = PURE_TRANSITIONS[event]
        new["pending_interrupt"] = {
            "kind": "vigil-adjustment",
            "event": event,
            "vigil_rounds": rounds,
            "cap_reached": rounds >= VIGIL_MAX_ROUNDS,
            "raised_at": _now_iso(),
        }
        return new

    new["phase"] = PURE_TRANSITIONS[event]

    if event == "sentinel-pass":
        new["pending_interrupt"] = {
            "kind": "output",
            "event": event,
            "raised_at": _now_iso(),
        }

    return new


# What `next` tells the conversation to do per phase: which agent to spawn, and
# the human-readable reminder. Every spawn is preceded by the grounding inject.
PHASE_ACTIONS = {
    "idle": ("spawn", "auddie_1", "Spawn Auddie #1 (predicted blast radius)."),
    "auddie_1": ("spawn", "auddie_1", "Spawn Auddie #1 (predicted blast radius)."),
    "vigil": ("spawn", "vigil", "Spawn Vigil (design gate)."),
    "forge": ("spawn", "forge", "Spawn Forge (build the slice)."),
    "auddie_2": ("spawn", "auddie_2", "Spawn Auddie #2 (actual blast radius)."),
    "sentinel": ("spawn", "sentinel", "Spawn Sentinel (build gate)."),
    "done": ("done", None, "Slice done — nothing to spawn."),
}


def next_action(state: dict) -> dict:
    """Return the next action for `state` — the conversation asks this instead
    of remembering where it is, so position survives a compaction. Pure.

    An open `pending_interrupt` is surfaced FIRST: after a compaction / resume
    the pending human decision is re-presented, not dropped. Otherwise the phase
    decides which agent to spawn next. Every spawn carries a reminder to inject
    the grounding block first (`ground`).
    """
    interrupt = state.get("pending_interrupt")
    if interrupt:
        return {
            "kind": "interrupt",
            "interrupt": interrupt,
            "phase": state.get("phase"),
            "ground": True,
            "reminder": (
                "Open human touchpoint — surface this and wait for the human's "
                "decision before spawning the next agent."
            ),
        }

    phase = state.get("phase", "idle")
    if phase not in PHASE_ACTIONS:
        raise ValueError(
            f"unknown phase {phase!r} — must be one of {tuple(PHASE_ACTIONS)}"
        )
    kind, agent, reminder = PHASE_ACTIONS[phase]
    return {
        "kind": kind,
        "agent": agent,
        "phase": phase,
        "ground": kind == "spawn",
        "reminder": (
            reminder
            + (
                " Inject the grounding block (devteam.py ground) first."
                if kind == "spawn"
                else ""
            )
        ),
    }
