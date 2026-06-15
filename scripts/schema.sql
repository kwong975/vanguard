-- claude-harness — durable substrate schema.
--
-- The project's machine-checkable source of truth. Raw SQL, no ORM (repo
-- invariant). Applied by store.py:init_db, which also enables WAL. All 7
-- entities are created here so every slice reads a schema that already has the
-- right shape.
--
-- Production callers: only `project` and `slice_state` are written today. The
-- other five tables are CREATE-only scaffolds, wired by their own feature. They
-- are deliberately NOT wired ahead of need (no speculative hooks): an unwired
-- table is honest deferral, a speculative caller is over-engineering.
--
-- Migration note: this file is the fresh-DB authority (CREATE TABLE IF NOT
-- EXISTS for new tables). store.py:init_db ALSO applies additive column
-- migrations (store.py:_COLUMN_MIGRATIONS), so a DB built on an older schema
-- gains columns added later (e.g. decision.supersedes) — additive + nullable
-- only; a destructive change still needs a fresh DB (the store lives outside any
-- repo at <workspace-store-dir>/<slug>.db, so dropping it is safe). The one-time
-- JSON import (store.py:import_json_state) migrates a JSON slice-state file into
-- this schema; it is idempotent.

-- project — one row per /devteam project (the spec/slug this store serves).
CREATE TABLE IF NOT EXISTS project (
    slug          TEXT PRIMARY KEY,
    spec_path     TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

-- slice — one row per slice of a project. The orchestrator declares a slice's
-- owned paths (`file_ownership`), dependencies (`depends_on`), and human-legible
-- `title` at firming; `acquire_claim` reads them for the file-conflict +
-- dependency gates. (Slice lifecycle lives in `slice_state.phase` + the markdown
-- spec markings, and ownership lives in `claim.owner`.)
CREATE TABLE IF NOT EXISTS slice (
    project_slug    TEXT NOT NULL,
    number          INTEGER NOT NULL,
    title           TEXT,
    depends_on      TEXT,        -- JSON array of slice numbers
    file_ownership  TEXT,        -- JSON array of owned paths/dir-prefixes
    PRIMARY KEY (project_slug, number)
);

-- decision — the decision graph. devteam.py log inserts a structured row here
-- (the AUTHORITATIVE record) AND renders the markdown Decision Log line from the
-- same args (lossless structured→text; no parse-back). The settled-query + the
-- ground inject read this table.
CREATE TABLE IF NOT EXISTS decision (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_slug    TEXT NOT NULL,
    date            TEXT,
    status          TEXT,        -- firm|revised|deferred|executed
    decision        TEXT,
    rationale       TEXT,
    author          TEXT,
    settled         INTEGER DEFAULT 0,   -- boolean flag (derived from status)
    supersedes      INTEGER      -- id of the prior decision a `revised` row replaces
);

-- acceptance — machine-checkable criteria.
CREATE TABLE IF NOT EXISTS acceptance (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_slug    TEXT NOT NULL,
    slice_number    INTEGER NOT NULL,
    criterion       TEXT,
    passed          INTEGER      -- NULL=unchecked, 0=fail, 1=pass
);

-- slice_state — the slice-state table. The externally-observed JSON object is
-- preserved verbatim in `state_json` (the invisibility + partial-accept
-- invariant); the named fields are also broken out into columns so later slices
-- can query them without parsing JSON. One row per (project_slug, slice_number).
CREATE TABLE IF NOT EXISTS slice_state (
    project_slug    TEXT NOT NULL,
    slice_number    INTEGER NOT NULL,
    phase           TEXT,
    verdicts        TEXT,        -- JSON object {vigil, sentinel}
    vigil_rounds    INTEGER,
    open_escapes    TEXT,        -- JSON array
    state_json      TEXT NOT NULL,   -- the full external JSON object, verbatim
    created_at      TEXT,
    updated_at      TEXT NOT NULL,
    PRIMARY KEY (project_slug, slice_number)
);

-- integration_status — the project-level integration gate record.
CREATE TABLE IF NOT EXISTS integration_status (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_slug    TEXT NOT NULL,
    slice_number    INTEGER,
    status          TEXT
);

-- claim — multi-session slice claims: acquire/release/list + atomic,
-- cooperative slice locking. `acquire_claim` refuses on a file-ownership overlap
-- with a different owner's claim, or an unsatisfied dependency.
CREATE TABLE IF NOT EXISTS claim (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_slug    TEXT NOT NULL,
    slice_number    INTEGER NOT NULL,
    owner           TEXT,        -- session/owner id
    acquired_at     TEXT
);

-- Atomicity is enforced BY THE DB: one active claim per (project, slice). A
-- SEPARATE index statement (NOT a table-level UNIQUE inside CREATE TABLE —
-- `CREATE TABLE IF NOT EXISTS` above skips the already-created `claim` table, so
-- a table-level constraint would never retrofit existing DBs). init_db's
-- executescript re-applies this on every connect, so it retrofits old DBs —
-- safe because `claim` is empty everywhere (the table was created unwired). A
-- claim row present ⟺ an active claim; a colliding insert raises IntegrityError,
-- which acquire_claim catches and re-raises as ValueError for a clean rc-1.
CREATE UNIQUE INDEX IF NOT EXISTS idx_claim_project_slice
    ON claim (project_slug, slice_number);
