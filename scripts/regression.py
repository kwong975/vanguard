#!/usr/bin/env python3
"""The regression corpus + runner — a LEAF module.

Formalises the regression net we run by hand (re-run the suite each slice) into a
permanent, **perturbed**, behaviour-level corpus: the one load-bearing real-path
invariant each shipped slice must never lose, each checked across a deliberate set
of **input perturbations + the matching negative case** — catching regressions a
single hard-coded example misses. A curated, perturbed, behaviour-level net, not
more unit tests.

LEAF discipline (precedent: reducer.py). This module imports only the stdlib and
drives the **real assembled CLI** (`python3 devteam.py …`) in a subprocess against
a throwaway temp DB — exactly like test_integration.py's `run_cli` /
`TemporaryDirectory` pattern. It does NOT import store/reducer/cli/devteam at the
top level, so there is no back-dependency and no import cycle: `cli.py` imports
THIS module's thin `run_corpus`/`list_entries` for the `regression` CLI; this
module imports neither cli nor devteam.

The corpus is a flat registry (`CORPUS`) of named invariant functions tagged with
the slice each regresses — the reducer.py dict/tuple-registry style, NOT a DSL,
fixture engine, or test-discovery framework. Each invariant runs its positive
perturbations (every variation must hold) AND its one negative case (the input
that must be refused / fail exactly as specified). Perturbation is hand-rolled
stdlib (loops over a list of inputs) — not a property-testing framework, not a
fuzzer. Report-only: the corpus passing IS the proof; no new store table, no
durable record, no new dependency.

GROWTH CONTRACT: when a slice passes (Sentinel PASS + merge) its key real-path
invariant is added to `CORPUS` here — a documented duty, analogous to the
orchestrator committing acceptance criteria at firming and the integration record
at merge.

Dual-mode: `regression run` replays the whole corpus standalone and its **exit
code is the gate** (0 iff every invariant holds across its perturbations, non-0
otherwise); test_regression.py re-runs the same corpus under pytest so it fires on
every slice. The runner is deterministic — fixed inputs, a sorted/insertion-ordered
registry, throwaway DBs.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, NamedTuple

DEVTEAM_PATH = Path(__file__).parent / "devteam.py"


# ---------------------------------------------------------------------------
# The subprocess harness — drives the REAL assembled CLI (test_integration.py
# pattern) so the corpus is a behaviour-level check against the wired system,
# not a unit test of an imported function.
# ---------------------------------------------------------------------------


class CLIResult(NamedTuple):
    rc: int
    stdout: str
    stderr: str


def _run_cli(*args: str) -> CLIResult:
    """Invoke `python3 devteam.py <args>` and capture rc/stdout/stderr."""
    p = subprocess.run(
        [sys.executable, str(DEVTEAM_PATH), *args],
        capture_output=True,
        text=True,
    )
    return CLIResult(p.returncode, p.stdout, p.stderr)


def _ok(r: CLIResult):
    """Assert a CLI call succeeded; return its parsed JSON stdout (if any)."""
    if r.rc != 0:
        raise AssertionError(f"expected success, got rc={r.rc}: {r.stderr.strip()}")
    return json.loads(r.stdout) if r.stdout.strip() else None


def _refused(r: CLIResult) -> None:
    """Assert a CLI call was refused cleanly: non-zero rc AND no Python
    traceback (a refusal is a surfaced `error:`/usage message, not a crash)."""
    if r.rc == 0:
        raise AssertionError(f"expected refusal, got rc=0: {r.stdout.strip()}")
    if "Traceback" in r.stderr:
        raise AssertionError(f"refusal leaked a traceback:\n{r.stderr}")


def _state_path(tmp: Path, slug: str) -> Path:
    """A state-file path whose stem is `slug` (the store DB derives `<slug>.db`
    from it — the slug seam every store-DB command shares)."""
    return tmp / f"{slug}.state.json"


# ---------------------------------------------------------------------------
# S1 — substrate round-trip + partial-merge carry-forward.
# POSITIVE: state-init → partial state-write → state-read carries omitted fields
#   forward (across varied slugs / slice numbers / which field is written).
# NEGATIVE: a partial write that omits `slice` to a never-initialised project is
#   refused (no row to infer the slice from) — rc!=0, clean error, no traceback.
# (Grounded: a partial write WITH --slice to a fresh slice succeeds — it stamps a
#  new row — so the reachable raise is the omit-slice/no-row path.)
# ---------------------------------------------------------------------------


def s1_substrate_round_trip(tmp: Path) -> None:
    spec = tmp / "spec.md"
    spec.write_text("# spec\n")
    # POSITIVE perturbations: vary slug, slice number, and the field carried.
    cases = [
        ("alpha", 1, {"phase": "forge"}),
        ("beta-proj", 7, {"vigil_rounds": 2}),
        ("g3", 42, {"phase": "sentinel", "vigil_rounds": 1}),
    ]
    for slug, n, partial in cases:
        st = _state_path(tmp, slug)
        init = _ok(_run_cli("state-init", "--path", str(st), "--slice", str(n), "--spec", str(spec)))  # fmt: skip
        assert init["slice"] == n and init["phase"] == "idle", f"{slug}: init"
        _ok(_run_cli("state-write", "--path", str(st), "--slice", str(n), "--json", json.dumps(partial)))  # fmt: skip
        read = _ok(_run_cli("state-read", "--path", str(st), "--slice", str(n)))
        # The written fields landed.
        for k, v in partial.items():
            assert read[k] == v, f"{slug}: {k} not written"
        # The omitted init fields carried FORWARD (the partial-merge invariant).
        assert read["spec_path"] == str(spec), f"{slug}: spec_path lost"
        assert "verdicts" in read, f"{slug}: verdicts (init field) dropped"
        assert read["slice"] == n, f"{slug}: slice changed"

    # NEGATIVE: a partial write omitting `slice` to a never-initialised project
    # is refused (no existing row to infer the slice from).
    fresh = _state_path(tmp, "never-inited")
    _refused(_run_cli("state-write", "--path", str(fresh), "--json", json.dumps({"phase": "forge"})))  # fmt: skip


# ---------------------------------------------------------------------------
# S2 — settled-decision query honours supersession.
# POSITIVE: log a firm decision, then a `revised` one --supersedes it; `decisions`
#   returns the settler, NOT the superseded (across varied slugs / orderings of
#   surrounding settled decisions).
# NEGATIVE: the superseded decision is NOT in the settled set.
# ---------------------------------------------------------------------------


def s2_settled_supersession(tmp: Path) -> None:
    cases = [
        ("dec-a", []),  # plain supersede
        ("dec-b", ["executed"]),  # a surrounding settled decision before
        ("dec-c", ["firm", "deferred"]),  # noise: extra settled + a non-settled
    ]
    for slug, prefix in cases:
        st = _state_path(tmp, slug)
        proj = tmp / f"{slug}.md"
        proj.write_text("# p\n\n## Decision Log\n")

        def _log(status, decision, supersedes=None):
            # `log` prints the rendered markdown LINE (text, not JSON), so check
            # rc only — do not parse stdout as JSON (only `decisions` is JSON).
            args = ["log", "--path", str(proj), "--state-path", str(st), "--status", status, "--decision", decision, "--rationale", "r"]  # fmt: skip
            if supersedes is not None:
                args += ["--supersedes", str(supersedes)]
            r = _run_cli(*args)
            assert r.rc == 0, f"{slug}: log {status!r} {r.stderr.strip()}"

        # Surrounding decisions (the perturbation: the settled set is not just one).
        for i, status in enumerate(prefix):
            _log(status, f"noise-{i}")
        # The decision that will be superseded.
        _log("firm", "original call")
        listed = _ok(_run_cli("decisions", "--state-path", str(st)))
        orig_id = next(d["id"] for d in listed if d["decision"] == "original call")
        # The revised decision supersedes it.
        _log("revised", "revised call", supersedes=orig_id)

        settled = _ok(_run_cli("decisions", "--state-path", str(st)))
        decisions = {d["decision"] for d in settled}
        # POSITIVE: the settler IS settled.
        assert "revised call" in decisions, f"{slug}: settler missing from settled set"
        # NEGATIVE: the superseded original is NOT settled anymore.
        assert "original call" not in decisions, f"{slug}: superseded still settled"
        # A `deferred` noise entry (if any) is never settled.
        assert "noise-1" not in decisions or "deferred" not in prefix, slug


# ---------------------------------------------------------------------------
# S3 — reducer transitions + interrupt durability across a re-read.
# POSITIVE: advance through phases lands the right phase; a pending interrupt
#   (vigil-adjustment / output) survives a FRESH `next` read from the DB
#   (simulated compaction), across varied slugs / slice numbers.
# NEGATIVE: an unknown --event is rejected (the vocabulary is enforced) — rc!=0,
#   no traceback. (A known transition landing the right phase is the positive.)
# ---------------------------------------------------------------------------


def s3_reducer_and_interrupt_durability(tmp: Path) -> None:
    spec = tmp / "spec.md"
    spec.write_text("# spec\n")
    cases = [
        ("r3a", 3),
        ("r3b", 11),
        ("r3c", 88),
    ]
    for slug, n in cases:
        st = _state_path(tmp, slug)
        _ok(_run_cli("state-init", "--path", str(st), "--slice", str(n), "--spec", str(spec)))  # fmt: skip
        # A pure transition lands the right phase.
        adv = _ok(_run_cli("advance", "--path", str(st), "--slice", str(n), "--event", "auddie1-done"))  # fmt: skip
        assert adv["phase"] == "vigil", f"{slug}: auddie1-done → {adv['phase']}"
        # Open a durable interrupt (vigil-needs-adjustment), then prove it survives
        # a FRESH next read (a separate process re-reading the DB = compaction).
        _ok(_run_cli("advance", "--path", str(st), "--slice", str(n), "--event", "vigil-needs-adjustment"))  # fmt: skip
        nxt = _ok(_run_cli("next", "--path", str(st), "--slice", str(n)))
        assert nxt["kind"] == "interrupt", f"{slug}: interrupt not surfaced first"
        assert nxt["interrupt"]["kind"] == "vigil-adjustment", f"{slug}: wrong interrupt"  # fmt: skip
        # NEGATIVE: an unknown event is rejected (vocabulary enforced at the CLI).
        _refused(_run_cli("advance", "--path", str(st), "--slice", str(n), "--event", "made-up-event"))  # fmt: skip


# ---------------------------------------------------------------------------
# S5 — acceptance floor blocks on any 0/NULL and clears only when all-passed.
# POSITIVE: across varied slugs / slice numbers / criterion counts, the floor is
#   False while any criterion is unchecked (NULL) or 0, and flips True only when
#   EVERY criterion is passed=1.
# NEGATIVE: the floor is False with a 0 or an unchecked criterion present.
# ---------------------------------------------------------------------------


def s5_acceptance_floor(tmp: Path) -> None:
    spec = tmp / "spec.md"
    spec.write_text("# spec\n")
    cases = [
        ("acc-a", 1, 1),  # one criterion
        ("acc-b", 5, 2),  # two criteria
        ("acc-c", 20, 3),  # three criteria
    ]
    for slug, n, count in cases:
        st = _state_path(tmp, slug)
        _ok(_run_cli("state-init", "--path", str(st), "--slice", str(n), "--spec", str(spec)))  # fmt: skip
        ids = []
        for i in range(count):
            row = _ok(_run_cli("accept", "add", "--state-path", str(st), "--slice", str(n), "--criterion", f"c{i}"))  # fmt: skip
            ids.append(row["id"])

        def _floor():
            return _ok(_run_cli("accept", "list", "--state-path", str(st), "--slice", str(n)))["floor_met"]  # fmt: skip

        # NEGATIVE: with every criterion unchecked (NULL), the floor is False.
        assert _floor() is False, f"{slug}: floor met with unchecked criteria"
        # Pass all but the last; mark the last 0 — a 0 keeps the floor False.
        for cid in ids[:-1]:
            _ok(_run_cli("accept", "result", "--state-path", str(st), "--slice", str(n), "--id", str(cid), "--passed", "1"))  # fmt: skip
        _ok(_run_cli("accept", "result", "--state-path", str(st), "--slice", str(n), "--id", str(ids[-1]), "--passed", "0"))  # fmt: skip
        assert _floor() is False, f"{slug}: floor met with a 0 criterion"
        # POSITIVE: flip the last to 1 — now ALL passed, the floor is True.
        _ok(_run_cli("accept", "result", "--state-path", str(st), "--slice", str(n), "--id", str(ids[-1]), "--passed", "1"))  # fmt: skip
        assert _floor() is True, f"{slug}: floor not met with all passed"


# ---------------------------------------------------------------------------
# S6 — claim atomicity + segment-overlap conflict + dependency gate.
# POSITIVE: an acquire grants; a same-owner re-acquire is idempotent; a DISJOINT
#   path (segment-rule: store.py vs store.py.bak / agents vs agents-v2) is granted
#   to a different owner; a satisfied dependency grants.
# NEGATIVE (three reachable refusals, each rc!=0 / no traceback):
#   - a different-owner re-acquire of a held slice,
#   - an overlapping-path claim (segment-prefix: agents/ vs agents/forge.md) by a
#     different owner,
#   - a claim whose depends_on slice has not passed.
# ---------------------------------------------------------------------------


def s6_claim_atomicity_overlap_dependency(tmp: Path) -> None:
    spec = tmp / "spec.md"
    spec.write_text("# spec\n")

    def _setup(slug):
        st = _state_path(tmp, slug)
        return st

    # --- atomicity: grant, same-owner idempotent, different-owner refused ---
    for slug, n in [("clm-a", 1), ("clm-b", 9)]:
        st = _setup(slug)
        _ok(_run_cli("state-init", "--path", str(st), "--slice", str(n), "--spec", str(spec)))  # fmt: skip
        _ok(_run_cli("slice", "set", "--state-path", str(st), "--slice", str(n), "--file-ownership", json.dumps([f"{slug}.py"]), "--depends-on", "[]"))  # fmt: skip
        _ok(_run_cli("claim", "acquire", "--state-path", str(st), "--slice", str(n), "--owner", "alice"))  # fmt: skip
        # same-owner re-acquire is idempotent (no error, no duplicate).
        _ok(_run_cli("claim", "acquire", "--state-path", str(st), "--slice", str(n), "--owner", "alice"))  # fmt: skip
        claims = _ok(_run_cli("claim", "list", "--state-path", str(st)))
        assert len([c for c in claims if c["slice_number"] == n]) == 1, f"{slug}: dup claim"  # fmt: skip
        # NEGATIVE: a different owner is refused.
        _refused(_run_cli("claim", "acquire", "--state-path", str(st), "--slice", str(n), "--owner", "bob"))  # fmt: skip

    # --- overlap: segment-prefix conflict refused; disjoint granted ---
    # Perturb the overlapping pair AND the disjoint pair.
    overlap_pairs = [
        ("agents/", "agents/forge.md"),  # segment-prefix → conflict
        ("docs", "docs/usage.md"),  # segment-prefix → conflict
    ]
    disjoint_pairs = [
        ("store.py", "store.py.bak"),  # different final segment → disjoint
        ("agents", "agents-v2"),  # startswith false-positive excluded → disjoint
    ]
    for i, (held, other) in enumerate(overlap_pairs):
        st = _setup(f"ov-{i}")
        _ok(_run_cli("state-init", "--path", str(st), "--slice", "1", "--spec", str(spec)))  # fmt: skip
        _ok(_run_cli("slice", "set", "--state-path", str(st), "--slice", "1", "--file-ownership", json.dumps([held]), "--depends-on", "[]"))  # fmt: skip
        _ok(_run_cli("slice", "set", "--state-path", str(st), "--slice", "2", "--file-ownership", json.dumps([other]), "--depends-on", "[]"))  # fmt: skip
        _ok(_run_cli("claim", "acquire", "--state-path", str(st), "--slice", "1", "--owner", "alice"))  # fmt: skip
        # NEGATIVE: an overlapping-path claim by a different owner is refused.
        _refused(_run_cli("claim", "acquire", "--state-path", str(st), "--slice", "2", "--owner", "bob"))  # fmt: skip
    for i, (held, other) in enumerate(disjoint_pairs):
        st = _setup(f"dj-{i}")
        _ok(_run_cli("state-init", "--path", str(st), "--slice", "1", "--spec", str(spec)))  # fmt: skip
        _ok(_run_cli("slice", "set", "--state-path", str(st), "--slice", "1", "--file-ownership", json.dumps([held]), "--depends-on", "[]"))  # fmt: skip
        _ok(_run_cli("slice", "set", "--state-path", str(st), "--slice", "2", "--file-ownership", json.dumps([other]), "--depends-on", "[]"))  # fmt: skip
        _ok(_run_cli("claim", "acquire", "--state-path", str(st), "--slice", "1", "--owner", "alice"))  # fmt: skip
        # POSITIVE: a disjoint path is granted to a different owner.
        _ok(_run_cli("claim", "acquire", "--state-path", str(st), "--slice", "2", "--owner", "bob"))  # fmt: skip

    # --- dependency gate: refused while unsatisfied, granted once `done` ---
    st = _setup("dep")
    _ok(_run_cli("state-init", "--path", str(st), "--slice", "10", "--spec", str(spec)))  # fmt: skip
    _ok(_run_cli("slice", "set", "--state-path", str(st), "--slice", "11", "--file-ownership", json.dumps(["dep11.py"]), "--depends-on", "[10]"))  # fmt: skip
    # NEGATIVE: dep slice 10 is not `done` yet → refused.
    _refused(_run_cli("claim", "acquire", "--state-path", str(st), "--slice", "11", "--owner", "alice"))  # fmt: skip
    # Drive slice 10 to `done`, then the claim is granted (POSITIVE).
    for ev in ("auddie1-done", "vigil-holds", "forge-done", "auddie2-done", "sentinel-pass"):  # fmt: skip
        _ok(_run_cli("advance", "--path", str(st), "--slice", "10", "--event", ev))
    _ok(_run_cli("claim", "acquire", "--state-path", str(st), "--slice", "11", "--owner", "alice"))  # fmt: skip


# ---------------------------------------------------------------------------
# S7 — integration record + end-to-end composition.
# This entry OVERLAPS test_integration.py's end-to-end test (Vigil concern), so
# its PERTURBATION axis is what earns its place: it drives a FULL loop on varied
# slugs / slice numbers / event orderings (a re-fail-then-pass loop on one),
# records the integration outcome, and asserts cross-table consistency — NOT a
# single hard-coded scenario.
# NEGATIVE: a bad --status is rejected (the passed/failed vocabulary, enforced).
# ---------------------------------------------------------------------------


def s7_integration_and_composition(tmp: Path) -> None:
    spec = tmp / "spec.md"
    spec.write_text("# spec\n")
    # Perturbation: vary slug, slice number, AND the loop path (one straight pass,
    # one that FAILs back to forge before passing) — the axis that distinguishes
    # this from test_integration.py's single straight-through scenario.
    cases = [
        (
            "int-a",
            1,
            [
                "auddie1-done",
                "vigil-holds",
                "forge-done",
                "auddie2-done",
                "sentinel-pass",
            ],
        ),  # fmt: skip
        (
            "int-b",
            7,
            [
                "auddie1-done",
                "vigil-holds",
                "forge-done",
                "auddie2-done",
                "sentinel-fail",
                "forge-done",
                "auddie2-done",
                "sentinel-pass",
            ],
        ),  # fmt: skip
        (
            "int-c",
            30,
            [
                "auddie1-done",
                "vigil-needs-adjustment",
                "vigil-holds",
                "forge-done",
                "auddie2-done",
                "sentinel-pass",
            ],
        ),  # fmt: skip
    ]
    for slug, n, events in cases:
        st = _state_path(tmp, slug)
        proj = tmp / f"{slug}.md"
        proj.write_text("# p\n\n## Decision Log\n")
        _ok(_run_cli("state-init", "--path", str(st), "--slice", str(n), "--spec", str(spec)))  # fmt: skip
        crit = _ok(_run_cli("accept", "add", "--state-path", str(st), "--slice", str(n), "--criterion", "wired"))  # fmt: skip
        _ok(_run_cli("slice", "set", "--state-path", str(st), "--slice", str(n), "--file-ownership", json.dumps([f"{slug}.py"]), "--depends-on", "[]"))  # fmt: skip
        _ok(_run_cli("claim", "acquire", "--state-path", str(st), "--slice", str(n), "--owner", "session-x"))  # fmt: skip
        for ev in events:
            _ok(_run_cli("advance", "--path", str(st), "--slice", str(n), "--event", ev))  # fmt: skip
        _ok(_run_cli("accept", "result", "--state-path", str(st), "--slice", str(n), "--id", str(crit["id"]), "--passed", "1"))  # fmt: skip
        logged = _run_cli("log", "--path", str(proj), "--state-path", str(st), "--status", "executed", "--decision", f"{slug} loop", "--rationale", "r")  # fmt: skip
        assert logged.rc == 0, f"{slug}: log {logged.stderr.strip()}"
        _ok(_run_cli("claim", "release", "--state-path", str(st), "--slice", str(n), "--owner", "session-x"))  # fmt: skip
        rec = _ok(_run_cli("integration", "record", "--state-path", str(st), "--slice", str(n), "--status", "passed"))  # fmt: skip
        assert rec["status"] == "passed", f"{slug}: integration status"

        # Cross-table consistency after a full, perturbed loop.
        final = _ok(_run_cli("state-read", "--path", str(st), "--slice", str(n)))
        assert final["phase"] == "done", f"{slug}: not done"
        floor = _ok(_run_cli("accept", "list", "--state-path", str(st), "--slice", str(n)))  # fmt: skip
        assert floor["floor_met"] is True, f"{slug}: floor not met"
        claims = _ok(_run_cli("claim", "list", "--state-path", str(st)))
        assert all(c["slice_number"] != n for c in claims), f"{slug}: claim not released"  # fmt: skip
        integ = _ok(_run_cli("integration", "list", "--state-path", str(st)))
        assert any(r["slice_number"] == n and r["status"] == "passed" for r in integ), f"{slug}: no integration row"  # fmt: skip

    # NEGATIVE: a bad --status is rejected (passed/failed vocabulary enforced).
    st = _state_path(tmp, "int-neg")
    _refused(_run_cli("integration", "record", "--state-path", str(st), "--slice", "1", "--status", "bogus"))  # fmt: skip


# ---------------------------------------------------------------------------
# S9 — a `done` slice is terminal; the reducer guards source phases.
# POSITIVE: a normal full loop still reaches `done` (the guard never blocks a
#   VALID transition), across varied slugs / slice numbers.
# NEGATIVE: from `done`, EVERY event is refused (rc!=0, no traceback) and the
#   slice STAYS done (no in-place revive — the pre-S9 behaviour, now locked); and
#   a wrong-phase event (auddie2-done fired from `forge`) is refused without
#   mutating the phase.
# ---------------------------------------------------------------------------


def s9_done_is_terminal_and_phase_guarded(tmp: Path) -> None:
    spec = tmp / "spec.md"
    spec.write_text("# spec\n")
    full_loop = ("auddie1-done", "vigil-holds", "forge-done", "auddie2-done", "sentinel-pass")  # fmt: skip
    cases = [("term-a", 2), ("term-b", 13), ("term-c", 50)]
    for slug, n in cases:
        st = _state_path(tmp, slug)
        _ok(_run_cli("state-init", "--path", str(st), "--slice", str(n), "--spec", str(spec)))  # fmt: skip
        # POSITIVE: the full loop still reaches done (no valid transition blocked).
        for ev in full_loop:
            _ok(_run_cli("advance", "--path", str(st), "--slice", str(n), "--event", ev))  # fmt: skip
        read = _ok(_run_cli("state-read", "--path", str(st), "--slice", str(n)))
        assert read["phase"] == "done", f"{slug}: full loop did not reach done"
        # NEGATIVE: from done, every event is refused AND the slice stays done.
        for ev in ("auddie1-done", "vigil-holds", "forge-done", "auddie2-done", "sentinel-pass", "sentinel-fail"):  # fmt: skip
            _refused(_run_cli("advance", "--path", str(st), "--slice", str(n), "--event", ev))  # fmt: skip
            still = _ok(_run_cli("state-read", "--path", str(st), "--slice", str(n)))
            assert still["phase"] == "done", f"{slug}: {ev} revived a done slice"

    # NEGATIVE (source-phase guard): auddie2-done is valid only from auddie_2;
    # fired from `forge` it is refused and must NOT mutate the phase.
    st = _state_path(tmp, "wrong-phase")
    _ok(_run_cli("state-init", "--path", str(st), "--slice", "1", "--spec", str(spec)))
    _ok(_run_cli("advance", "--path", str(st), "--slice", "1", "--event", "auddie1-done"))  # idle→vigil  # fmt: skip
    _ok(_run_cli("advance", "--path", str(st), "--slice", "1", "--event", "vigil-holds"))  # vigil→forge  # fmt: skip
    _refused(_run_cli("advance", "--path", str(st), "--slice", "1", "--event", "auddie2-done"))  # fmt: skip
    cur = _ok(_run_cli("state-read", "--path", str(st), "--slice", "1"))
    assert cur["phase"] == "forge", f"wrong-phase event mutated phase to {cur['phase']}"


# ---------------------------------------------------------------------------
# The corpus registry — one curated, perturbed real-path invariant per shipped
# slice that ADDS assembled-CLI behaviour (S1–S7 + S9 reducer lock). Two shipped
# slices have no entry here, by design: S8 IS the runner itself; and S4 (the
# audit-gate fan-out) added no assembled-CLI behaviour — an external,
# operator-launched workflow + a prose skill offer + a distribution step — so its
# load-bearing invariant (install.sh distributes the workflow byte-identical) is a
# REPO-level test (tests/test_install.py), NOT a corpus entry. This keeps the
# corpus portable: every entry drives only `devteam.py`, so `regression run` holds
# wherever the lib copy is installed, not just in a repo checkout.
# A flat tuple registry (reducer.py style), NOT a discovery engine. Insertion order
# is the run order (deterministic).
#
# GROWTH SEAM: when a slice passes Sentinel + merges, append its key real-path
# invariant here IF it is assembled-CLI behaviour (the documented post-merge growth
# duty); a slice whose invariant is repo-level (install/layout) grows a repo test
# instead.
# ---------------------------------------------------------------------------


class CorpusEntry(NamedTuple):
    # `slice_tag` groups invariants by the build stage that introduced the
    # behaviour (each tag's behaviour is named in its section comment above:
    # substrate round-trip, settled-decision supersession, reducer transitions,
    # acceptance floor, claim/lock, integration). It is a stable grouping label,
    # not load-bearing logic.
    slice_tag: str  # e.g. "S1" — the build stage whose behaviour this regresses
    name: str  # the invariant's name (shown by `regression list`)
    fn: Callable[[Path], None]  # drives the real CLI; raises AssertionError on fail


CORPUS: tuple[CorpusEntry, ...] = (
    CorpusEntry("S1", "s1_substrate_round_trip", s1_substrate_round_trip),
    CorpusEntry("S2", "s2_settled_supersession", s2_settled_supersession),
    CorpusEntry(
        "S3", "s3_reducer_and_interrupt_durability", s3_reducer_and_interrupt_durability
    ),  # fmt: skip
    CorpusEntry("S5", "s5_acceptance_floor", s5_acceptance_floor),
    CorpusEntry(
        "S6",
        "s6_claim_atomicity_overlap_dependency",
        s6_claim_atomicity_overlap_dependency,
    ),  # fmt: skip
    CorpusEntry(
        "S7", "s7_integration_and_composition", s7_integration_and_composition
    ),  # fmt: skip
    CorpusEntry(
        "S9",
        "s9_done_is_terminal_and_phase_guarded",
        s9_done_is_terminal_and_phase_guarded,
    ),  # fmt: skip
)


# ---------------------------------------------------------------------------
# The runner — replays the corpus, prints a behaviour-level pass/fail summary,
# returns 0 iff every invariant holds (the exit code is the gate). Each invariant
# runs in its OWN throwaway temp DB dir, so entries are isolated and the run is
# deterministic. This is the body `regression run` / standalone / pytest all call.
# ---------------------------------------------------------------------------


def run_corpus(verbose: bool = True) -> int:
    """Run every corpus entry over its perturbations; print a summary; return 0
    iff all hold (non-0 otherwise). The return value is the gate."""
    green, red, reset = "\033[92m", "\033[91m", "\033[0m"
    passed = 0
    for entry in CORPUS:
        label = f"[{entry.slice_tag}] {entry.name}"
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                entry.fn(Path(tmpdir))
        except AssertionError as e:
            if verbose:
                detail = f" — {e}" if str(e) else ""
                print(f"  [{red}FAIL{reset}] {label}{detail}")
        except Exception as e:  # an unexpected error is a corpus failure, surfaced
            if verbose:
                print(f"  [{red}ERROR{reset}] {label} — {type(e).__name__}: {e}")
        else:
            if verbose:
                print(f"  [{green}PASS{reset}] {label}")
            passed += 1

    total = len(CORPUS)
    if verbose:
        colour = green if passed == total else red
        print(f"\n{'=' * 50}")
        print(f"{colour}regression corpus: {passed}/{total} invariants hold{reset}")
    return 0 if passed == total else 1


def list_entries() -> list[dict]:
    """Enumerate the corpus entries with their slice tags (for `regression list`)."""
    return [{"slice": e.slice_tag, "name": e.name} for e in CORPUS]


if __name__ == "__main__":
    raise SystemExit(run_corpus())
