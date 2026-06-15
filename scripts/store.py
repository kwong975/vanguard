#!/usr/bin/env python3
"""Durable substrate — the SQLite store.

The dev-team loop externalises its state into a SQLite store so it survives
sessions and context compaction. This module is the **thin store API**: a small
set of reducer primitives (init a project, upsert/read each entity, read/write
slice_state). The schema lives in `schema.sql` (raw SQL, no ORM — repo
invariant); this module applies it and reads/writes it with raw parameterised
queries only.

Portable plumbing, like devteam.py: standard library only, no Claude Code
dependency, every path a parameter. The store path is instance config — default
`<workspace-store-dir>/<slug>.db` (e.g. `~/dev/.devteam/<slug>.db`), outside any
repo, never hardcoded.

Only `project` and `slice_state` get production callers today. The other five
tables (slice, decision, acceptance, integration_status, claim) are created by
schema.sql as scaffolds and are intentionally not wired here.

The 3 CLI subcommands (`state-init`/`state-write`/`state-read`) live in
devteam.py and are re-backed onto `read_slice_state` / `write_slice_state`
here; their external JSON object I/O is preserved unchanged, including the
**partial-object-accept** invariant.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# Facade re-export of the higher-level registries (decision / acceptance /
# slice / claim / integration), split into store_registries.py to keep both
# files under the repo_quality 1000-line cap (same split-by-concern move that
# took the CLI handlers into cli.py). store.py KEEPS the core substrate
# (connect/init_db/migrations/project/slice_state/import); the registries move
# out but are RE-EXPORTED here so every `store.<fn>` caller (cli.py + the
# tests) keeps working unchanged — pure code movement, zero behaviour change.
#
# store_registries is a LEAF (it imports nothing from store), so this re-export
# is acyclic. The dual-mode test loader loads store.py by EXPLICIT PATH (not via
# sys.path), so the repo root is not importable by name — add store.py's own
# directory to sys.path before importing the sibling, so `import store_registries`
# resolves whether store.py was loaded by name (production) or by path (tests).
_THIS_DIR = str(Path(__file__).resolve().parent)
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

from store_registries import (  # noqa: E402
    _dep_passed,
    _is_settled,
    _ownerships_overlap,
    _path_segments,
    _paths_conflict,
    acquire_claim,
    add_criterion,
    criteria_floor_met,
    insert_decision,
    list_criteria,
    list_integration,
    list_slices,
    read_claims,
    read_criterion,
    read_decision,
    read_integration,
    read_slice,
    record_integration,
    release_claim,
    set_criterion_result,
    set_slice_attrs,
    settled_decisions,
)

# Re-export the moved names so `from store import *` and static checkers see
# them as part of store's public surface (the facade contract).
__all__ = [
    "_dep_passed",
    "_is_settled",
    "_ownerships_overlap",
    "_path_segments",
    "_paths_conflict",
    "acquire_claim",
    "add_criterion",
    "criteria_floor_met",
    "insert_decision",
    "list_criteria",
    "list_integration",
    "list_slices",
    "read_claims",
    "read_criterion",
    "read_decision",
    "read_integration",
    "read_slice",
    "record_integration",
    "release_claim",
    "set_criterion_result",
    "set_slice_attrs",
    "settled_decisions",
]

# The named queryable fields of slice_state, broken out into columns alongside
# the verbatim `state_json`. The JSON object remains the external contract; the
# columns are derived from it on write so later slices can query without parsing.
SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def _now_iso() -> str:
    """UTC ISO8601 with a Z suffix — matches devteam.py:_now_iso (the settled
    timestamp primitive). Duplicated here as a one-line stdlib call rather than
    importing devteam, because devteam imports this module (state CLI), and a
    timestamp is a leaf primitive, not a shared abstraction worth a cycle."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def default_db_path(slug: str, store_dir: str | Path | None = None) -> Path:
    """Resolve the default store path for a project slug: `<store_dir>/<slug>.db`.

    `store_dir` is a PARAMETER (instance config); it defaults to `~/dev/.devteam`
    — the workspace store dir, outside any repo. Nothing is hardcoded at a call
    site: a caller that wants a different location passes `store_dir`.
    """
    base = (
        Path(store_dir) if store_dir is not None else Path.home() / "dev" / ".devteam"
    )
    return base / f"{slug}.db"


def slug_and_db_from_state_path(state_path: str | Path) -> tuple[str, Path]:
    """Derive `(slug, db_path)` from the `--path` the /devteam skill passes.

    This is the re-backing seam: the skill still passes a state
    `--path` (e.g. `~/dev/.devteam/<slug>.state.json`); the SQLite store lives at
    a sibling `<slug>.db` in the SAME directory (already outside any repo), so
    the swap is invisible to the skill and nothing new is hardcoded. The slug is
    the file stem with a trailing `.state` stripped:
    `claude-harness.state.json` → slug `claude-harness`, db `<dir>/<slug>.db`.
    """
    p = Path(state_path)
    stem = p.stem  # drops the final suffix (.json)
    if stem.endswith(".state"):
        stem = stem[: -len(".state")]
    slug = stem
    return slug, p.with_name(f"{slug}.db")


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open a WAL-mode connection to the store at `db_path` (a plain path, not a
    URI — we do not rely on the sqlite URI default).

    WAL is enabled on every connection (repo invariant: WAL always). The parent
    directory is created if absent so a fresh project can be initialised in one
    call. Foreign-key-free by design (the scaffold tables are unwired).
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    # WAL always (repo invariant). journal_mode returns the mode it set.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# Additive schema migrations for a DB created on an older schema version.
# `schema.sql` is the fresh-DB authority (CREATE TABLE IF NOT EXISTS), so new
# *tables* need no entry here. A *column* added to an existing table later gets
# one entry below; `init_db` adds it only if it is absent (idempotent — a no-op
# on a fresh DB that already has it). Additive + nullable only (the migration
# rule); a destructive change still needs a fresh DB. The identifiers are
# hardcoded constants, never user input — the f-string ALTER is not an
# injection surface (the no-interpolation rule governs user *data*).
_COLUMN_MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    # (table, column, column_def) — column_def must be nullable or have a default
    ("decision", "supersedes", "INTEGER"),  # table-authoritative graph
    ("integration_status", "checked_at", "TEXT"),  # integration gate
)


def init_db(conn: sqlite3.Connection) -> None:
    """Create the 7-entity schema from schema.sql if absent, then reconcile any
    additive column migrations for a DB built on an older schema. Idempotent
    (CREATE TABLE IF NOT EXISTS + guarded ALTER). Note: `executescript` issues an
    implicit COMMIT, so the `with conn:` block is not an all-or-nothing rollback
    boundary; safety here rests on every schema.sql statement being
    `… IF NOT EXISTS` (no statement can fail mid-script). A future schema.sql
    statement that can fail would need its own guard."""
    ddl = SCHEMA_PATH.read_text()
    with conn:  # transaction: commit on success, rollback on error
        conn.executescript(ddl)
    _apply_column_migrations(conn)


def _apply_column_migrations(conn: sqlite3.Connection) -> None:
    """Add any `_COLUMN_MIGRATIONS` column missing from its (existing) table.

    Reconciles a DB created on an older schema (the column is absent) up to the
    current shape; a no-op on a fresh DB where the column already exists from
    schema.sql. Additive only — never drops or alters an existing column.
    """
    for table, column, coldef in _COLUMN_MIGRATIONS:
        existing = {
            row["name"] for row in conn.execute(f'PRAGMA table_info("{table}")')
        }
        if column not in existing:
            with conn:
                conn.execute(f'ALTER TABLE "{table}" ADD COLUMN {column} {coldef}')


# ---------------------------------------------------------------------------
# project — one of the two production entities (alongside slice_state)
# ---------------------------------------------------------------------------


def init_project(conn: sqlite3.Connection, slug: str, spec_path: str) -> dict:
    """Insert or update the project row for `slug`. Returns the stored row.

    Upsert (crash-safe transaction): a re-init of an existing project refreshes
    the spec path and `updated_at` but preserves `created_at`.
    """
    now = _now_iso()
    with conn:
        conn.execute(
            """
            INSERT INTO project (slug, spec_path, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(slug) DO UPDATE SET
                spec_path = excluded.spec_path,
                updated_at = excluded.updated_at
            """,
            (slug, spec_path, now, now),
        )
    return read_project(conn, slug)


def read_project(conn: sqlite3.Connection, slug: str) -> dict | None:
    """Return the project row for `slug` as a dict, or None if absent."""
    row = conn.execute(
        "SELECT slug, spec_path, created_at, updated_at FROM project WHERE slug = ?",
        (slug,),
    ).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# slice_state — the JSON slice-state entity (the other production entity)
# ---------------------------------------------------------------------------


def write_slice_state(
    conn: sqlite3.Connection,
    slug: str,
    state: dict,
) -> dict:
    """Upsert slice_state for `(slug, state['slice'])` and return the stored
    object (the external JSON shape, with `updated_at` stamped).

    Crash-safe (single transaction). **Partial-object-accept invariant**:
    `state` may be a partial object. The existing row's stored
    object is loaded and the incoming partial is merged on top of it, so fields
    the caller did not send are carried forward — then `updated_at` is stamped.
    `created_at` is preserved from the existing row (or stamped on first write).

    The merged object is persisted verbatim in `state_json`; the named fields
    (phase/verdicts/vigil_rounds/open_escapes) are also written to columns,
    derived from the merged object, so later slices can query them.

    The slice number comes from `state['slice']`; on a partial write that omits
    it, the single existing row for the project is used (raises if ambiguous or
    absent — a partial write to a slice that was never initialised is a caller
    error, surfaced, not silently swallowed).
    """
    slice_n = state.get("slice")
    existing = _load_state_row(conn, slug, slice_n)

    if slice_n is None:
        if existing is None:
            raise ValueError(
                f"partial slice_state write for project {slug!r} omits 'slice' "
                f"and no existing slice_state row exists to infer it from — "
                f"initialise the slice first (state-init)"
            )
        slice_n = existing["slice_number"]

    base = json.loads(existing["state_json"]) if existing else {}
    merged = {**base, **state}
    merged["slice"] = slice_n  # ensure the resolved slice is in the object
    now = _now_iso()
    if existing:
        merged["created_at"] = base.get("created_at", existing["created_at"]) or now
    else:
        merged.setdefault("created_at", now)
    merged["updated_at"] = now

    verdicts = merged.get("verdicts")
    open_escapes = merged.get("open_escapes")
    with conn:
        conn.execute(
            """
            INSERT INTO slice_state (
                project_slug, slice_number, phase, verdicts, vigil_rounds,
                open_escapes, state_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_slug, slice_number) DO UPDATE SET
                phase = excluded.phase,
                verdicts = excluded.verdicts,
                vigil_rounds = excluded.vigil_rounds,
                open_escapes = excluded.open_escapes,
                state_json = excluded.state_json,
                updated_at = excluded.updated_at
            """,
            (
                slug,
                slice_n,
                merged.get("phase"),
                json.dumps(verdicts, ensure_ascii=False)
                if verdicts is not None
                else None,
                merged.get("vigil_rounds"),
                json.dumps(open_escapes, ensure_ascii=False)
                if open_escapes is not None
                else None,
                json.dumps(merged, ensure_ascii=False),
                merged["created_at"],
                merged["updated_at"],
            ),
        )
    return merged


def read_slice_state(
    conn: sqlite3.Connection,
    slug: str,
    slice_n: int | None = None,
) -> dict:
    """Return the stored slice_state object for `(slug, slice_n)` (external JSON
    shape). If `slice_n` is None, return the single row for the project.

    Raises FileNotFoundError when there is no matching row — the
    `read_state` contract (no run to resume), so the CLI keeps its rc-1 behaviour.
    """
    row = _load_state_row(conn, slug, slice_n)
    if row is None:
        raise FileNotFoundError(
            f"no slice_state for project {slug!r}"
            + (f" slice {slice_n}" if slice_n is not None else "")
            + " — nothing to resume; start a run first"
        )
    return json.loads(row["state_json"])


def _load_state_row(
    conn: sqlite3.Connection,
    slug: str,
    slice_n: int | None,
) -> sqlite3.Row | None:
    """Fetch the raw slice_state row. With `slice_n` None, return the single row
    for the project (None if there are zero rows; raises if more than one — an
    ambiguous omit-slice read/write is a caller error, surfaced not guessed)."""
    if slice_n is not None:
        return conn.execute(
            "SELECT * FROM slice_state WHERE project_slug = ? AND slice_number = ?",
            (slug, slice_n),
        ).fetchone()
    rows = conn.execute(
        "SELECT * FROM slice_state WHERE project_slug = ?",
        (slug,),
    ).fetchall()
    if len(rows) > 1:
        raise ValueError(
            f"project {slug!r} has {len(rows)} slice_state rows — pass an explicit "
            f"slice number to disambiguate"
        )
    return rows[0] if rows else None


# NOTE: the higher-level registries (decision / acceptance / slice / claim /
# integration) and their private helpers were split into store_registries.py to
# keep this file under the repo_quality 1000-line cap; they are re-exported at
# the top of this module so every `store.<fn>` caller is unchanged. See the
# facade block above. What remains below is the migration importer (the last
# core-substrate function, which depends on slice_state writes here).


# ---------------------------------------------------------------------------
# Migration — one-time import of a JSON slice-state file
# ---------------------------------------------------------------------------


def import_json_state(
    conn: sqlite3.Connection,
    slug: str,
    json_path: str | Path,
) -> dict:
    """Import an existing JSON slice-state file at `json_path` into the store,
    once. **First import wins:** if the slice row already exists, the import is a
    no-op and returns the stored row unchanged — even if the source JSON has since
    been edited (it is NOT a converging upsert). This suits a one-time migration;
    to overwrite an existing row, use `write_slice_state`.

    Every field of the JSON object round-trips: the object is stored verbatim in
    `state_json` and re-read by `read_slice_state`. The project row is created
    from the imported object's `spec_path` if the object carries one.

    Raises FileNotFoundError if the JSON state file is absent, or ValueError if it
    is not a JSON object (both surfaced, not swallowed).
    """
    json_path = Path(json_path)
    if not json_path.exists():
        raise FileNotFoundError(f"no JSON slice-state to import at {json_path}")
    state = json.loads(json_path.read_text())
    if not isinstance(state, dict):
        raise ValueError(
            f"JSON slice-state at {json_path} must be a JSON object, got "
            f"{type(state).__name__}"
        )

    spec_path = state.get("spec_path")
    if spec_path:
        init_project(conn, slug, spec_path)

    # Preserve the imported created_at/updated_at verbatim (this is a faithful
    # import, not a fresh write) by inserting the row directly when absent.
    slice_n = state["slice"]
    existing = _load_state_row(conn, slug, slice_n)
    if existing is None:
        verdicts = state.get("verdicts")
        open_escapes = state.get("open_escapes")
        with conn:
            conn.execute(
                """
                INSERT INTO slice_state (
                    project_slug, slice_number, phase, verdicts, vigil_rounds,
                    open_escapes, state_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    slug,
                    slice_n,
                    state.get("phase"),
                    json.dumps(verdicts, ensure_ascii=False)
                    if verdicts is not None
                    else None,
                    state.get("vigil_rounds"),
                    json.dumps(open_escapes, ensure_ascii=False)
                    if open_escapes is not None
                    else None,
                    json.dumps(state, ensure_ascii=False),
                    state.get("created_at"),
                    state.get("updated_at") or _now_iso(),
                ),
            )
    # Idempotent: if the row already exists, the import is a no-op (the same
    # object is already stored). Return what is now in the store.
    return read_slice_state(conn, slug, slice_n)
