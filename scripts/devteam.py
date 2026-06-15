#!/usr/bin/env python3
"""Dev-team loop helper — the thin plumbing layer.

Two jobs the conversation is bad at:

  1. Append a Decision Log entry in the fixed format, so the format cannot
     drift. Append-only; existing entries are never rewritten.
  2. Read / write minimal slice state, so a `/devteam` run survives across
     sessions and `/devteam resume` can pick up.

This is the PORTABLE plumbing layer: a plain library with NO Claude Code
dependency, standard library only. Every path is a parameter — nothing about the
workspace or the repo is hardcoded here, because paths are instance config, not
framework. Any runtime can call it.

CLI (so the conversation can shell out without writing Python) — run
`python3 devteam.py --help` (and `<command> --help`) for the full surface. The
subcommands: `log` / `log-section` (the markdown formats), `state-init` /
`state-write` / `state-read` / `state-import` (slice state), `advance` / `next`
(the reducer), `decisions` / `ground` (the grounding layer), `accept` (the
acceptance registry), and `memory-extract` (the inject bridge).

The Decision Log (`log`) and the run log (`log-section`) target the SAME project
file by default (one file per piece of work). Splitting the run log into a
separate `-data` doc is the massive-build exception; because the path is a
parameter, splitting is just pointing the appends at a different file.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# The CLI layer — the `_cmd_*` handlers + `_build_parser` — lives in cli.py (to
# keep both files under the repo_quality 1000-line cap; the same split
# discipline as reducer.py). cli.py imports the LIBRARY names below
# from this module; `main()` imports `_build_parser` from cli LAZILY (inside the
# function) so the devteam ⇄ cli cycle never runs at import time. The subprocess
# entry point stays `python3 devteam.py` with dispatch via set_defaults(func=…).

# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------

VALID_STATUSES = ("firm", "revised", "deferred", "executed")

DECISION_LOG_HEADER = "## Decision Log"
RUN_LOG_HEADER = "## Run Log"


def _now_date() -> str:
    """Today's date as YYYY-MM-DD (local). Decision Log entries use the local date —
    a "<author> call <date>" stamp is local, not UTC, so a late-evening decision logs
    as today, not yesterday."""
    return datetime.now().strftime("%Y-%m-%d")


def _now_iso() -> str:
    """UTC ISO8601 with a Z suffix. Used for slice-state timestamps."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _atomic_write(path: Path, text: str) -> None:
    """Write text atomically via tmp + fsync + rename. Crash-safe on POSIX.

    A crash mid-write leaves the original file intact rather than a truncated
    one. The rename is atomic on POSIX, so a reader never sees a half-written
    state file.
    """
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)  # atomic on POSIX


# ---------------------------------------------------------------------------
# Job 1 — Decision Log append
# ---------------------------------------------------------------------------

# Fixed format (helper-enforced so it cannot drift):
#   YYYY-MM-DD — [firm|revised|deferred|executed] — <decision> — <rationale> — <author> call
# Em-dash (U+2014) separators (the Decision Log line convention). The
# author is a parameter so each teammate's decisions attribute to them — this is
# a shared framework, not one person's repo.


def format_decision(
    status: str,
    decision: str,
    rationale: str,
    date: str | None = None,
    author: str = "user",
) -> str:
    """Render one Decision Log line in the fixed format. No I/O.

    The `author` becomes the `<author> call` suffix so the entry
    attributes to whoever made the call — generic per-user, never hardcoded.

    Raises ValueError if the status is not one of the four allowed, or if the
    decision/rationale contain a newline (a log entry is one line).
    """
    if status not in VALID_STATUSES:
        raise ValueError(f"status must be one of {VALID_STATUSES}, got {status!r}")
    decision = decision.strip()
    rationale = rationale.strip()
    author = author.strip()
    if not decision:
        raise ValueError("decision must not be empty")
    if not rationale:
        raise ValueError("rationale must not be empty")
    if not author:
        raise ValueError("author must not be empty")
    if "\n" in decision or "\n" in rationale:
        raise ValueError("a Decision Log entry is one line — no newlines")
    date = date or _now_date()
    return f"{date} — [{status}] — {decision} — {rationale} — {author} call"


def append_decision(
    path: str | Path,
    status: str,
    decision: str,
    rationale: str,
    date: str | None = None,
    author: str = "user",
) -> str:
    """Append a Decision Log entry to the project file at `path`. Append-only.

    The target file (the project file by default — the same file the run log
    appends to) is a PARAMETER — never hardcoded. If a `## Decision Log` header is
    present, the entry is inserted at the END of that section (before the next
    `## ` header, or at end of file). If no header exists, the header plus the
    entry are appended at end of file.

    The `author` becomes the `<author> call` suffix — generic per-user.

    Existing entries are never rewritten — this only adds a line. Returns the
    formatted entry that was written.
    """
    path = Path(path)
    line = format_decision(status, decision, rationale, date, author)

    if not path.exists():
        raise FileNotFoundError(
            f"project file not found: {path} (the caller must point at an "
            f"existing project file — the helper does not create it)"
        )

    original = path.read_text()
    lines = original.splitlines()

    # Find the Decision Log section header.
    header_idx = None
    for i, ln in enumerate(lines):
        if ln.strip() == DECISION_LOG_HEADER:
            header_idx = i
            break

    if header_idx is None:
        # No section yet — append the header and the entry at end of file.
        suffix = "" if original.endswith("\n") or original == "" else "\n"
        new_text = original + f"{suffix}\n{DECISION_LOG_HEADER}\n\n{line}\n"
        _atomic_write(path, new_text)
        return line

    # Section exists — find where it ends (next `## ` header, or EOF).
    insert_idx = len(lines)
    for i in range(header_idx + 1, len(lines)):
        if lines[i].startswith("## "):
            insert_idx = i
            break

    # Walk back over trailing blank lines inside the section so the new entry
    # sits directly under the existing entries, not after a gap.
    while insert_idx > header_idx + 1 and lines[insert_idx - 1].strip() == "":
        insert_idx -= 1

    new_lines = lines[:insert_idx] + [line] + lines[insert_idx:]
    new_text = "\n".join(new_lines) + "\n"
    _atomic_write(path, new_text)
    return line


# ---------------------------------------------------------------------------
# Job 1b — Run-log section append (one file by default)
# ---------------------------------------------------------------------------

# The run log lives in the SAME project file as the Decision Log (one file per
# piece of work). The orchestrator appends one concise section per agent
# step — Auddie #1, Vigil, Forge, Auddie #2 cycle N, Sentinel — as a dated,
# labelled `### ` subsection under a single `## Run Log` header. Append-only,
# same as append_decision: existing sections are never rewritten.


def format_log_section(label: str, body: str, date: str | None = None) -> str:
    """Render one run-log section in the fixed shape. No I/O.

    Shape: a `### <YYYY-MM-DD> · <label>` heading, then a blank line, then the
    body. The middle-dot separator (U+00B7) is the run-log heading convention. The
    body MAY span multiple lines (a findings table, a blast map) — unlike a
    Decision Log entry, which is one line.

    Raises ValueError if the label is empty or contains a newline (a heading is
    one line), or if the body is empty.
    """
    label = label.strip()
    if not label:
        raise ValueError("label must not be empty")
    if "\n" in label:
        raise ValueError("a run-log section label is one line — no newlines")
    body = body.strip("\n")
    if not body.strip():
        raise ValueError("body must not be empty")
    date = date or _now_date()
    return f"### {date} · {label}\n\n{body}"


def append_log_section(
    path: str | Path,
    label: str,
    body: str,
    date: str | None = None,
) -> str:
    """Append a run-log section to the project file at `path`. Append-only.

    The project file is a PARAMETER — never hardcoded. The new section
    goes at the END of the `## Run Log` section:

    - If `## Run Log` is absent, the header is created. It is placed BEFORE
      `## Decision Log` if one exists (so the run log and Decision Log coexist in
      the right order), otherwise appended at end of file.
    - If `## Run Log` is present, the new `### ` section is inserted at the end of
      that section — before the next top-level `## ` header (e.g. `## Decision
      Log`), or at end of file.

    Existing sections are never rewritten — this only adds. Returns the formatted
    section that was written.
    """
    path = Path(path)
    section = format_log_section(label, body, date)

    if not path.exists():
        raise FileNotFoundError(
            f"project file not found: {path} (the caller must point at an "
            f"existing project file — the helper does not create it)"
        )

    original = path.read_text()
    lines = original.splitlines()

    # Find the Run Log section header.
    header_idx = None
    for i, ln in enumerate(lines):
        if ln.strip() == RUN_LOG_HEADER:
            header_idx = i
            break

    if header_idx is None:
        # No run-log section yet — create it. Place it before `## Decision Log`
        # if present, so run log and Decision Log end up in the right order.
        block = f"{RUN_LOG_HEADER}\n\n{section}\n"
        decision_idx = None
        for i, ln in enumerate(lines):
            if ln.strip() == DECISION_LOG_HEADER:
                decision_idx = i
                break

        if decision_idx is None:
            # No Decision Log either — append the run-log block at end of file.
            suffix = "" if original.endswith("\n") or original == "" else "\n"
            new_text = original + f"{suffix}\n{block}"
            _atomic_write(path, new_text)
            return section

        # Insert the run-log block just before the Decision Log header, keeping a
        # single blank line between the two top-level sections.
        new_lines = (
            lines[:decision_idx] + [block.rstrip("\n"), ""] + lines[decision_idx:]
        )
        new_text = "\n".join(new_lines) + "\n"
        _atomic_write(path, new_text)
        return section

    # Run-log section exists — find where it ends (next `## ` header, or EOF).
    insert_idx = len(lines)
    for i in range(header_idx + 1, len(lines)):
        if lines[i].startswith("## "):
            insert_idx = i
            break

    # Walk back over trailing blank lines inside the section so the new section
    # sits directly under the existing ones, not after a gap.
    while insert_idx > header_idx + 1 and lines[insert_idx - 1].strip() == "":
        insert_idx -= 1

    # A blank line before the new `### ` section keeps subsections separated.
    new_lines = lines[:insert_idx] + ["", section] + lines[insert_idx:]
    new_text = "\n".join(new_lines) + "\n"
    _atomic_write(path, new_text)
    return section


# ---------------------------------------------------------------------------
# Job 1c — Section extraction (read a `## <header>` section body)
# ---------------------------------------------------------------------------

# Read-only counterpart to the append jobs: pull the body of a `## <header>`
# section out of a markdown file. This is the inject bridge — the loop reads the
# live `## Agent Calibration` section from MEMORIES.md fresh each run and feeds it
# into each agent's spawn prompt (subagents don't inherit the session's memory).
# The path is a PARAMETER (instance config), never hardcoded.


def extract_section(path: str | Path, header: str) -> str:
    """Return the body of the `## <header>` section in the file at `path`.

    "Body" is everything between the `## <header>` line and the next top-level
    `## ` header (or EOF), with surrounding blank lines stripped. Returns the
    empty string if the section is absent (or the file does not exist) — the
    caller decides whether an absent section is fatal; an empty inject block is a
    valid "no live calibration yet" state, not an error.

    Reuses the section-finding pattern from `append_log_section`: locate the
    `## <header>` line, then scan forward to the next `## ` (or EOF).
    """
    path = Path(path)
    if not path.exists():
        return ""

    target = f"## {header.strip()}"
    lines = path.read_text().splitlines()

    # Find the section header.
    header_idx = None
    for i, ln in enumerate(lines):
        if ln.strip() == target:
            header_idx = i
            break

    if header_idx is None:
        return ""

    # Find where the section ends — the next top-level `## ` header, or EOF.
    end_idx = len(lines)
    for i in range(header_idx + 1, len(lines)):
        if lines[i].startswith("## "):
            end_idx = i
            break

    body = "\n".join(lines[header_idx + 1 : end_idx])
    return body.strip("\n").strip()


# ---------------------------------------------------------------------------
# Job 2 — Slice state (minimal)
# ---------------------------------------------------------------------------
# The loop vocabulary (LOOP_PHASES / AGENT_KEYS) + the reducer (`advance` /
# `next_action`) live in reducer.py — see the top-of-file import.


def new_state(slice_n: int, spec_path: str) -> dict:
    """A fresh slice-state dict at the start of a run. Pure; no I/O.

    Minimal by design — a small JSON capturing where a run is:
    which slice, current phase, and last verdict per judge. (`open_escapes` is a
    legacy field kept for back-compat round-tripping; the live human-touchpoint
    state — escapes included — is `pending_interrupt`, set by the reducer.)
    """
    now = _now_iso()
    return {
        "slice": slice_n,
        "spec_path": spec_path,
        "phase": "idle",
        "verdicts": {"vigil": None, "sentinel": None},
        "vigil_rounds": 0,
        "open_escapes": [],
        "created_at": now,
        "updated_at": now,
    }


def main(argv: list[str] | None = None) -> int:
    # Lazy import breaks the devteam ⇄ cli cycle (cli imports the library names
    # from this module at its top level; we import the parser only when main runs).
    from cli import _build_parser

    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (ValueError, FileNotFoundError, TypeError, KeyError, OverflowError) as e:
        # Map every expected input-shape failure to a clean `error: …`/rc-1 (the
        # tool's fail-clean contract — a refusal is a surfaced message, not a
        # traceback). ValueError covers json.JSONDecodeError; TypeError/KeyError
        # catch malformed (non-dict / missing-key) JSON state objects; OverflowError
        # catches an out-of-range --slice before it reaches SQLite.
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
