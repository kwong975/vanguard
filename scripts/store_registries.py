#!/usr/bin/env python3
"""Higher-level store registries — split out of store.py.

This module holds the higher-level registries (decision, acceptance, slice,
claim, integration) and their private helpers, leaving store.py with the core
substrate (connect, init_db, project, slice_state, migrations, import). The
split keeps each module under the repo_quality line cap and groups the store
code by concern, the same way the CLI handlers live in cli.py rather than
devteam.py.

LEAF discipline (precedent: reducer.py). This module imports nothing from
store: every registry function takes a `conn` parameter, so it needs no
`connect`/`init_db`; the one-line `_now_iso` is DUPLICATED here (the reducer.py
leaf precedent — a timestamp is a leaf primitive, not a shared abstraction
worth a cross-module dependency); and `_dep_passed` reads `slice_state`
directly via the passed `conn` rather than calling `store.read_slice_state`.
Being a leaf is what lets store.py re-export these names safely when store.py
is loaded by explicit path (the dual-mode test loader) — store_registries has
no back-edge to store, so there is no cycle and no sys.path dependency at this
module's import time.

Raw parameterised SQL only (repo invariant: no ORM, no string-interpolated
values). The schema lives in schema.sql; this module reads/writes the tables
schema.sql defines.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone


def _now_iso() -> str:
    """UTC ISO8601 with a Z suffix — matches devteam.py / store.py `_now_iso`.
    Duplicated here as a one-line stdlib leaf primitive rather than imported, so
    this module stays a leaf with no store dependency (the reducer.py precedent):
    a timestamp is a leaf primitive, not a shared abstraction worth a cycle."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# decision — the decision graph. The table is the AUTHORITATIVE record;
# the markdown Decision Log line is rendered from the same structured args by
# the caller (devteam.py log). There is NO parse-back: text is never read into
# columns. `settled` is derived from `status` here (firm/executed settle;
# deferred does not), and a `revised` row supersedes a prior decision by id —
# the superseded one stops being settled.
# ---------------------------------------------------------------------------


def _is_settled(status: str) -> int:
    """Map a decision status to its settled flag.

    firm / executed → settled (1); deferred → not settled (0). `revised` is a
    settled call in its own right (1) — it carries a new decision — but it also
    unsettles the prior decision it supersedes (handled in insert_decision).
    """
    return 1 if status in ("firm", "executed", "revised") else 0


def insert_decision(
    conn: sqlite3.Connection,
    slug: str,
    date: str,
    status: str,
    decision: str,
    rationale: str,
    author: str,
    supersedes: int | None = None,
) -> dict:
    """Insert one decision row (the authoritative record) and return it.

    Append-only, mirroring the markdown Decision Log: each call is a new row
    (plain INSERT, no upsert — there is no natural unique key; a project has
    many decisions per day). `settled` is derived from `status` via
    `_is_settled`. When `supersedes` is given (a `revised` row replacing a prior
    decision), the prior row's `settled` flag is cleared in the SAME transaction
    so the settled-query resolves supersession structurally, never by parsing
    text.

    Raises ValueError if `supersedes` references a decision id that does not
    exist for this project — a dangling supersedes reference is a caller error,
    surfaced not swallowed.
    """
    with conn:
        if supersedes is not None:
            prior = conn.execute(
                "SELECT id FROM decision WHERE id = ? AND project_slug = ?",
                (supersedes, slug),
            ).fetchone()
            if prior is None:
                raise ValueError(
                    f"supersedes={supersedes} references no decision for project "
                    f"{slug!r} — cannot supersede a decision that does not exist"
                )
            conn.execute(
                "UPDATE decision SET settled = 0 WHERE id = ?",
                (supersedes,),
            )
        cur = conn.execute(
            """
            INSERT INTO decision (
                project_slug, date, status, decision, rationale, author,
                settled, supersedes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                slug,
                date,
                status,
                decision,
                rationale,
                author,
                _is_settled(status),
                supersedes,
            ),
        )
        new_id = cur.lastrowid
    return read_decision(conn, new_id)


def read_decision(conn: sqlite3.Connection, decision_id: int) -> dict | None:
    """Return one decision row by id as a dict, or None if absent."""
    row = conn.execute(
        "SELECT * FROM decision WHERE id = ?",
        (decision_id,),
    ).fetchone()
    return dict(row) if row else None


def settled_decisions(
    conn: sqlite3.Connection,
    slug: str,
    status: str | None = None,
) -> list[dict]:
    """Return the settled decisions for `slug`, oldest first.

    A decision is settled when its `settled` flag is 1 — firm/executed/revised,
    minus any row a later `revised` superseded (its flag was cleared on
    supersession). Optionally filter to a single `status`. Reads the TABLE; no
    text parsing.
    """
    if status is not None:
        rows = conn.execute(
            "SELECT * FROM decision WHERE project_slug = ? AND settled = 1 "
            "AND status = ? ORDER BY id",
            (slug, status),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM decision WHERE project_slug = ? AND settled = 1 ORDER BY id",
            (slug,),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# acceptance — the per-slice machine-checkable definition of done. Mirrors
# the decision layer above (insert_decision/read_decision/settled_decisions):
# the table is the AUTHORITATIVE registry of a slice's firm criteria; the
# markdown spec keeps the human-readable criteria (the dual-form split). The
# orchestrator commits each firm criterion at touchpoint #1 (add_criterion);
# Sentinel records a result per criterion at PASS (set_criterion_result). The
# all-passed predicate (criteria_floor_met) is a FLOOR for PASS — necessary, not
# sufficient: Sentinel's holistic FAIL/ESCAPE stays live above it. The scaffold
# table already has `criterion` + `passed` (NULL/0/1).
# ---------------------------------------------------------------------------


def add_criterion(
    conn: sqlite3.Connection,
    slug: str,
    slice_n: int,
    criterion: str,
) -> dict:
    """Insert one acceptance criterion for `(slug, slice_n)` and return the row.

    Append-only, mirroring `insert_decision`: each call is a new row (plain
    INSERT, no upsert — re-firming a slice appends a fresh criterion rather than
    mutating a prior one, so the registry is an honest log of what was committed
    and when; de-duplication is the orchestrator's call, not the store's). The
    `passed` flag starts NULL (unchecked) — Sentinel sets it later via
    `set_criterion_result`. Returns the stored row including its `id`.
    """
    with conn:
        cur = conn.execute(
            """
            INSERT INTO acceptance (project_slug, slice_number, criterion, passed)
            VALUES (?, ?, ?, NULL)
            """,
            (slug, slice_n, criterion),
        )
        new_id = cur.lastrowid
    return read_criterion(conn, new_id)


def read_criterion(conn: sqlite3.Connection, criterion_id: int) -> dict | None:
    """Return one acceptance row by id as a dict, or None if absent."""
    row = conn.execute(
        "SELECT * FROM acceptance WHERE id = ?",
        (criterion_id,),
    ).fetchone()
    return dict(row) if row else None


def set_criterion_result(
    conn: sqlite3.Connection,
    slug: str,
    slice_n: int,
    passed: int,
    criterion_id: int | None = None,
    criterion: str | None = None,
) -> dict:
    """Set the `passed` flag (0/1) for one criterion of `(slug, slice_n)`.

    Address the target row either by `criterion_id` (exact) or by `criterion`
    text (the human-readable criterion Sentinel just verified). Exactly one of
    the two must be given. Returns the updated row.

    Raises ValueError if neither/both addressors are given, if `passed` is not
    0/1, or if the addressed criterion does not exist for this slice (a result
    for a criterion that was never committed is a caller error — surfaced, not
    silently swallowed).
    """
    if passed not in (0, 1):
        raise ValueError(f"passed must be 0 or 1, got {passed!r}")
    if (criterion_id is None) == (criterion is None):
        raise ValueError(
            "set_criterion_result needs exactly one of criterion_id / criterion"
        )
    with conn:
        if criterion_id is not None:
            row = conn.execute(
                "SELECT id FROM acceptance WHERE id = ? AND project_slug = ? "
                "AND slice_number = ?",
                (criterion_id, slug, slice_n),
            ).fetchone()
            if row is None:
                raise ValueError(
                    f"no acceptance criterion id={criterion_id} for project "
                    f"{slug!r} slice {slice_n} — cannot record a result for a "
                    f"criterion that was never committed"
                )
            target_id = criterion_id
        else:
            rows = conn.execute(
                "SELECT id FROM acceptance WHERE project_slug = ? "
                "AND slice_number = ? AND criterion = ?",
                (slug, slice_n, criterion),
            ).fetchall()
            if not rows:
                raise ValueError(
                    f"no acceptance criterion matching {criterion!r} for project "
                    f"{slug!r} slice {slice_n} — cannot record a result for a "
                    f"criterion that was never committed"
                )
            if len(rows) > 1:
                raise ValueError(
                    f"{len(rows)} acceptance criteria match {criterion!r} for "
                    f"project {slug!r} slice {slice_n} — address by id to "
                    f"disambiguate"
                )
            target_id = rows[0]["id"]
        conn.execute(
            "UPDATE acceptance SET passed = ? WHERE id = ?",
            (passed, target_id),
        )
    return read_criterion(conn, target_id)


def list_criteria(
    conn: sqlite3.Connection,
    slug: str,
    slice_n: int,
) -> list[dict]:
    """Return the acceptance criteria for `(slug, slice_n)`, oldest first."""
    rows = conn.execute(
        "SELECT * FROM acceptance WHERE project_slug = ? AND slice_number = ? "
        "ORDER BY id",
        (slug, slice_n),
    ).fetchall()
    return [dict(r) for r in rows]


def criteria_floor_met(
    conn: sqlite3.Connection,
    slug: str,
    slice_n: int,
) -> bool:
    """Return True iff the slice has at least one criterion AND every criterion
    has `passed == 1` — the REQUIRED FLOOR for a Sentinel PASS.

    Any `0` (fail) or `NULL` (unchecked) returns False → the floor blocks PASS.
    The floor is **necessary, not sufficient**: a True result clears the floor
    but does NOT force PASS — Sentinel's holistic FAIL—CODE / ESCAPE—DESIGN
    (unenumerated regression, unwired substrate, wider blast radius, security)
    stays live above it. An empty registry (no criteria) returns False — a slice
    with no committed criteria has not cleared a definition-of-done floor.
    """
    rows = list_criteria(conn, slug, slice_n)
    if not rows:
        return False
    return all(r["passed"] == 1 for r in rows)


# ---------------------------------------------------------------------------
# slice — declared attributes. Wires the `slice` scaffold table. The
# orchestrator declares, at touchpoint #1, a slice's owned paths
# (`file_ownership`), its dependencies (`depends_on`), and its human-legible
# `title` — the three columns the table carries. (Slice lifecycle lives in
# `slice_state.phase` + the markdown markings, ownership in `claim.owner`.) The
# JSON-array columns store a JSON array verbatim; `acquire_claim`'s
# overlap/dependency checks parse them back.
# ---------------------------------------------------------------------------


def set_slice_attrs(
    conn: sqlite3.Connection,
    slug: str,
    slice_n: int,
    file_ownership: list[str] | None = None,
    depends_on: list[int] | None = None,
    title: str | None = None,
) -> dict:
    """Upsert a slice's declared attributes for `(slug, slice_n)`; return the row.

    Upsert (crash-safe transaction) on the `(project_slug, number)` primary key.
    Only three columns are written — `file_ownership` (JSON array of concrete
    paths / directory prefixes), `depends_on` (JSON array of slice numbers), and
    `title`. A `None` argument leaves that column at its current value on an
    existing row (carry-forward, like the partial slice_state write), or NULL on
    a first insert. These three are the only columns the `slice` table carries.
    """
    existing = conn.execute(
        "SELECT title, depends_on, file_ownership FROM slice "
        "WHERE project_slug = ? AND number = ?",
        (slug, slice_n),
    ).fetchone()

    # Reject empty / whitespace-only ownership entries: an empty path has no
    # segments, so `_paths_conflict("", x)` is True for every x — a slice owning
    # `[""]` would block every other slice's claim. The segment-overlap design
    # wants disjoint paths grantable, so a malformed empty entry is a caller error.
    if file_ownership is not None:
        for p in file_ownership:
            if not isinstance(p, str) or not p.strip():
                raise ValueError(
                    "file_ownership entries must be non-empty path strings "
                    f"(got {p!r}); an empty entry would conflict with every slice"
                )

    new_title = (
        title if title is not None else (existing["title"] if existing else None)
    )
    new_depends = (
        json.dumps(depends_on, ensure_ascii=False)
        if depends_on is not None
        else (existing["depends_on"] if existing else None)
    )
    new_ownership = (
        json.dumps(file_ownership, ensure_ascii=False)
        if file_ownership is not None
        else (existing["file_ownership"] if existing else None)
    )
    with conn:
        conn.execute(
            """
            INSERT INTO slice (project_slug, number, title, depends_on, file_ownership)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(project_slug, number) DO UPDATE SET
                title = excluded.title,
                depends_on = excluded.depends_on,
                file_ownership = excluded.file_ownership
            """,
            (slug, slice_n, new_title, new_depends, new_ownership),
        )
    return read_slice(conn, slug, slice_n)


def read_slice(conn: sqlite3.Connection, slug: str, slice_n: int) -> dict | None:
    """Return a slice's declared attributes (the three fields parsed
    from their JSON columns), or None if no row exists.

    `file_ownership` → list[str], `depends_on` → list[int]; a NULL column
    decodes to an empty list (a slice with no declared owns / deps).
    """
    row = conn.execute(
        "SELECT number, title, depends_on, file_ownership FROM slice "
        "WHERE project_slug = ? AND number = ?",
        (slug, slice_n),
    ).fetchone()
    if row is None:
        return None
    return {
        "slice": row["number"],
        "title": row["title"],
        "depends_on": json.loads(row["depends_on"]) if row["depends_on"] else [],
        "file_ownership": (
            json.loads(row["file_ownership"]) if row["file_ownership"] else []
        ),
    }


def list_slices(conn: sqlite3.Connection, slug: str) -> list[dict]:
    """Return every declared slice for `slug`, by number — the three declared
    fields each (same shape as `read_slice`)."""
    rows = conn.execute(
        "SELECT number FROM slice WHERE project_slug = ? ORDER BY number",
        (slug,),
    ).fetchall()
    return [read_slice(conn, slug, r["number"]) for r in rows]


# ---------------------------------------------------------------------------
# claim — multi-session slice claims. Cooperative
# coordination through the substrate: a session claims a slice before building
# it; the claim is refused on a file-ownership overlap with a DIFFERENT owner's
# active claim, or an unsatisfied dependency. Atomicity is enforced by the DB
# (the UNIQUE index in schema.sql). Honestly advisory — it gates well-behaved
# sessions at the claim call; no security boundary, no TTL (the human spine
# breaks stale locks via `release --force`).
# ---------------------------------------------------------------------------


def _path_segments(path: str) -> list[str]:
    """Split a path into its non-empty segments (overlap is computed over
    SEGMENTS, never raw string `startswith`).

    Split on `/`, drop empty/trailing-slash segments: `agents/` → `["agents"]`,
    `agents/forge.md` → `["agents", "forge.md"]`, `store.py` → `["store.py"]`,
    `store.py.bak` → `["store.py.bak"]` (a different final segment).
    """
    return [seg for seg in path.split("/") if seg]


def _paths_conflict(a: str, b: str) -> bool:
    """Two paths conflict iff one's segment list EQUALS or is a PREFIX of the
    other's.

    So `agents/` conflicts with `agents/forge.md` (`[agents]` is a segment-prefix
    of `[agents, forge.md]`), `store.py` conflicts with `store.py`, but `store.py`
    and `store.py.bak` are DISJOINT (different final segment) and `agents` and
    `agents-v2` are DISJOINT (the `startswith` false-positives this excludes).
    No glob matching.
    """
    sa, sb = _path_segments(a), _path_segments(b)
    shorter, longer = (sa, sb) if len(sa) <= len(sb) else (sb, sa)
    return longer[: len(shorter)] == shorter


def _ownerships_overlap(own_a: list[str], own_b: list[str]) -> bool:
    """True iff any path in `own_a` conflicts (segment-prefix) with any in `own_b`."""
    return any(_paths_conflict(pa, pb) for pa in own_a for pb in own_b)


def _dep_passed(conn: sqlite3.Connection, slug: str, dep_slice: int) -> bool:
    """True iff dependency slice `dep_slice` has PASSED — read as
    `slice_state.phase == 'done'` (the reducer's terminal phase as the single
    source of pass-state).

    A dependency with no `slice_state` row has not started, so it has not passed
    (the row is absent — a legitimate not-yet-started state, not an error).
    Reads `slice_state.phase` directly via the passed `conn` rather than calling
    `store.read_slice_state`, so this module stays a leaf with no store import
    (the leaf discipline that keeps this module free of a store back-edge).
    """
    row = conn.execute(
        "SELECT phase FROM slice_state WHERE project_slug = ? AND slice_number = ?",
        (slug, dep_slice),
    ).fetchone()
    if row is None:
        return False
    return row["phase"] == "done"


def acquire_claim(
    conn: sqlite3.Connection,
    slug: str,
    slice_n: int,
    owner: str,
) -> dict:
    """Acquire a claim on `(slug, slice_n)` for `owner`; return the claim row.

    The lock — refuses with a clean `ValueError` (no row
    inserted) when EITHER:
      - a `depends_on` slice has not passed (`slice_state.phase != 'done'`,
        including the no-`slice_state`-row case), or
      - this slice's `file_ownership` overlaps (segment-prefix) the ownership of
        any slice CURRENTLY CLAIMED BY A DIFFERENT OWNER.

    A SAME-OWNER re-acquire of a slice it already holds is IDEMPOTENT (returns
    the existing row, no duplicate, no error) — handled by a guarded read-first
    against the UNIQUE-index holder, NOT a bare INSERT. A DIFFERENT-owner holder
    on the same slice is the refusal. A colliding INSERT (race) raises
    sqlite3.IntegrityError, caught and re-raised as ValueError so `main()`
    surfaces a clean `error: …`/rc-1 (IntegrityError is otherwise uncaught).
    """
    # Idempotent same-owner re-acquire / different-owner refusal on THIS slice.
    held = conn.execute(
        "SELECT * FROM claim WHERE project_slug = ? AND slice_number = ?",
        (slug, slice_n),
    ).fetchone()
    if held is not None:
        if held["owner"] == owner:
            return dict(held)  # idempotent — already held by this owner
        raise ValueError(
            f"slice {slice_n} of project {slug!r} is already claimed by "
            f"{held['owner']!r} — release it (or `release --force`) first"
        )

    # Dependency gate: every depends_on slice must have passed.
    this_slice = read_slice(conn, slug, slice_n)
    depends_on = this_slice["depends_on"] if this_slice else []
    for dep in depends_on:
        if not _dep_passed(conn, slug, dep):
            raise ValueError(
                f"cannot claim slice {slice_n} of project {slug!r}: dependency "
                f"slice {dep} has not passed (slice_state.phase != 'done')"
            )

    # File-conflict gate: overlap with any DIFFERENT owner's active claim.
    own = this_slice["file_ownership"] if this_slice else []
    if own:
        for other in read_claims(conn, slug):
            if other["owner"] == owner:
                continue
            other_slice = read_slice(conn, slug, other["slice_number"])
            other_own = other_slice["file_ownership"] if other_slice else []
            if _ownerships_overlap(own, other_own):
                raise ValueError(
                    f"cannot claim slice {slice_n} of project {slug!r}: its "
                    f"file_ownership overlaps slice {other['slice_number']} held "
                    f"by {other['owner']!r}"
                )

    # Insert the claim. The UNIQUE index makes a race-colliding insert raise
    # IntegrityError; re-raise as ValueError for a clean rc-1.
    now = _now_iso()
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO claim (project_slug, slice_number, owner, acquired_at) "
                "VALUES (?, ?, ?, ?)",
                (slug, slice_n, owner, now),
            )
            new_id = cur.lastrowid
    except sqlite3.IntegrityError as e:
        raise ValueError(
            f"slice {slice_n} of project {slug!r} was claimed concurrently — "
            f"retry after a `claim list` ({e})"
        ) from e
    row = conn.execute("SELECT * FROM claim WHERE id = ?", (new_id,)).fetchone()
    return dict(row)


def release_claim(
    conn: sqlite3.Connection,
    slug: str,
    slice_n: int,
    owner: str,
    force: bool = False,
) -> dict:
    """Release the claim on `(slug, slice_n)`; DELETE the row (no released_at
    history — the Decision Log carries the narrative). Returns the released row.

    Refuses with a `ValueError` if the claim is held by a DIFFERENT owner, unless
    `force=True` — the operator's lever to break a stale claim (a session that
    died holding one; there is no TTL). Raises `ValueError` if there is no claim
    to release (a release of an unheld slice is a caller error — surfaced).
    """
    held = conn.execute(
        "SELECT * FROM claim WHERE project_slug = ? AND slice_number = ?",
        (slug, slice_n),
    ).fetchone()
    if held is None:
        raise ValueError(f"no claim on slice {slice_n} of project {slug!r} to release")
    if held["owner"] != owner and not force:
        raise ValueError(
            f"slice {slice_n} of project {slug!r} is held by {held['owner']!r}, "
            f"not {owner!r} — use --force to break a stale claim"
        )
    with conn:
        conn.execute(
            "DELETE FROM claim WHERE project_slug = ? AND slice_number = ?",
            (slug, slice_n),
        )
    return dict(held)


def read_claims(conn: sqlite3.Connection, slug: str) -> list[dict]:
    """Return every active claim for `slug`, by slice number."""
    rows = conn.execute(
        "SELECT * FROM claim WHERE project_slug = ? ORDER BY slice_number",
        (slug,),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# integration_status — the project-level integration gate record. Wires the
# `integration_status` scaffold table. After a slice merges, the orchestrator
# runs the dual gate (end-to-end scenario + full suite) and records the outcome
# here — a project-level axis, NOT a loop event (the reducer stays pure, like
# the claim gate). Mirrors the acceptance layer in SHAPE: append-only INSERT,
# raw parameterised SQL, reuses connect/init_db. Status is enforced
# passed/failed at BOTH layers — argparse choices at the CLI AND the re-check
# below. `checked_at` is stamped from _now_iso (its column is a
# _COLUMN_MIGRATIONS entry only, never in schema.sql's CREATE TABLE).
# ---------------------------------------------------------------------------

_INTEGRATION_STATUSES: tuple[str, ...] = ("passed", "failed")


def record_integration(
    conn: sqlite3.Connection,
    slug: str,
    slice_n: int,
    status: str,
) -> dict:
    """Record one integration-gate outcome for `(slug, slice_n)`; return the row.

    Append-only (like `add_criterion`): each call is a fresh row, `checked_at`
    stamped to `_now_iso`. The store-side defence: `status` must be `passed` or
    `failed` (a ValueError otherwise — mirroring `set_criterion_result`'s 0/1
    re-check); the CLI's argparse `choices` is the first enforcement layer.
    """
    if status not in _INTEGRATION_STATUSES:
        raise ValueError(
            f"status must be one of {_INTEGRATION_STATUSES}, got {status!r}"
        )
    now = _now_iso()
    with conn:
        cur = conn.execute(
            """
            INSERT INTO integration_status (
                project_slug, slice_number, status, checked_at
            ) VALUES (?, ?, ?, ?)
            """,
            (slug, slice_n, status, now),
        )
        new_id = cur.lastrowid
    return read_integration(conn, slug, slice_n, integration_id=new_id)


def read_integration(
    conn: sqlite3.Connection,
    slug: str,
    slice_n: int,
    integration_id: int | None = None,
) -> dict | None:
    """Return one integration_status row as a dict, or None if absent.

    By default returns the MOST RECENT row for `(slug, slice_n)` (the latest
    integration outcome — append-only, so the highest id is newest). Pass
    `integration_id` to address an exact row (used by `record_integration` to
    return the row it just inserted).
    """
    if integration_id is not None:
        row = conn.execute(
            "SELECT * FROM integration_status WHERE id = ?",
            (integration_id,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM integration_status WHERE project_slug = ? "
            "AND slice_number = ? ORDER BY id DESC LIMIT 1",
            (slug, slice_n),
        ).fetchone()
    return dict(row) if row else None


def list_integration(conn: sqlite3.Connection, slug: str) -> list[dict]:
    """Return every integration_status row for `slug`, oldest first."""
    rows = conn.execute(
        "SELECT * FROM integration_status WHERE project_slug = ? ORDER BY id",
        (slug,),
    ).fetchall()
    return [dict(r) for r in rows]
