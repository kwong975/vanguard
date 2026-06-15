#!/usr/bin/env python3
"""The CLI layer — command handlers + the argparse parser.

Split out of devteam.py (the decision-format + state-plumbing LIBRARY layer) so
both files stay under the repo_quality 1000-line cap (the same split discipline
as reducer.py / test_grounding.py). This module holds the `_cmd_*` subcommand
handlers and `_build_parser`; devteam.py keeps `main()` (the entry point + the
`(ValueError, FileNotFoundError) -> rc 1` contract) and the library helpers the
handlers call (`append_decision`, `new_state`, `extract_section`,
`format_decision`, …).

Import direction: this module imports the library names from `devteam` and the
event vocabulary from `reducer`. `devteam.main()` imports `_build_parser` from
here LAZILY (inside the function) so the cycle (devteam ⇄ cli) never executes at
import time — the subprocess entry point stays `python3 devteam.py`, dispatch via
`set_defaults(func=...)` is unchanged, and every existing CLI signature is
preserved.

`import store` stays LOCAL to each handler that needs it (as before the split),
so this module — like devteam.py — imports without sqlite at the top level.
"""

from __future__ import annotations

import argparse
import json
import os

from devteam import (
    VALID_STATUSES,
    _now_date,
    append_decision,
    append_log_section,
    extract_section,
    format_decision,
    new_state,
)
from reducer import (
    ALL_EVENTS,
    REENTER_CHOICES,
    advance,
    next_action,
)


def _cmd_log(args: argparse.Namespace) -> int:
    # The decision table is the AUTHORITATIVE record; the markdown line is a
    # rendered view of the SAME structured args. Both derive from
    # one input — `args` — and there is NO parse-back: the markdown is never read
    # to rebuild the row. The row goes in first (it is the source of truth); the
    # markdown line is then appended, rendered from the identical fields.
    if args.state_path is None and args.supersedes is not None:
        raise ValueError(
            "--supersedes requires --state-path (the supersedes reference is a "
            "structural link in the decision table; markdown-only mode has no row "
            "to link)"
        )
    if args.state_path is not None:
        import store

        date = args.date or _now_date()  # pin one date for BOTH writes
        slug, db_path = store.slug_and_db_from_state_path(args.state_path)
        conn = store.connect(db_path)
        try:
            store.init_db(conn)
            store.insert_decision(
                conn,
                slug,
                date=date,
                status=args.status,
                decision=args.decision,
                rationale=args.rationale,
                author=args.author,
                supersedes=args.supersedes,
            )
        finally:
            conn.close()
        line = append_decision(
            args.path,
            status=args.status,
            decision=args.decision,
            rationale=args.rationale,
            date=date,
            author=args.author,
        )
    else:
        # No store handle (markdown-only mode): append the
        # markdown line exactly as before.
        line = append_decision(
            args.path,
            status=args.status,
            decision=args.decision,
            rationale=args.rationale,
            date=args.date,
            author=args.author,
        )
    print(line)
    return 0


def _cmd_decisions(args: argparse.Namespace) -> int:
    # Read the settled decisions from the TABLE — no markdown
    # parse. The slug/db come from the same state-path seam as `log` and the
    # state commands. Output is JSON so the orchestrator can compose the inject.
    import store

    slug, db_path = store.slug_and_db_from_state_path(args.state_path)
    conn = store.connect(db_path)
    try:
        store.init_db(conn)
        rows = store.settled_decisions(conn, slug, status=args.status)
    finally:
        conn.close()
    print(json.dumps(rows, indent=2, ensure_ascii=False))
    return 0


def _accept_db(state_path: str) -> tuple[object, str]:
    """Open the acceptance store off `--state-path`; return (connection, slug).

    Same slug/db seam as `decisions`/`ground`/the state commands — no new
    path-resolution decision."""
    import store

    slug, db_path = store.slug_and_db_from_state_path(state_path)
    conn = store.connect(db_path)
    store.init_db(conn)
    return conn, slug


def _cmd_accept_add(args: argparse.Namespace) -> int:
    # Orchestrator commits one firm criterion at touchpoint #1;
    # append-only, mirrors `log`.
    import store

    conn, slug = _accept_db(args.state_path)
    try:
        row = store.add_criterion(conn, slug, args.slice, args.criterion)
    finally:
        conn.close()
    print(json.dumps(row, indent=2, ensure_ascii=False))
    return 0


def _cmd_accept_result(args: argparse.Namespace) -> int:
    # Sentinel records a per-criterion pass/fail; address by --id or
    # --criterion text — the store raises if neither/both/uncommitted.
    import store

    conn, slug = _accept_db(args.state_path)
    try:
        row = store.set_criterion_result(
            conn,
            slug,
            args.slice,
            passed=args.passed,
            criterion_id=args.id,
            criterion=args.criterion,
        )
    finally:
        conn.close()
    print(json.dumps(row, indent=2, ensure_ascii=False))
    return 0


def _cmd_accept_list(args: argparse.Namespace) -> int:
    # List a slice's criteria + `floor_met` (all-passed). The floor is a REQUIRED
    # but NOT sufficient gate for PASS — Sentinel's holistic verdict stays live
    # above it. JSON out for programmatic reads.
    import store

    conn, slug = _accept_db(args.state_path)
    try:
        rows = store.list_criteria(conn, slug, args.slice)
        floor_met = store.criteria_floor_met(conn, slug, args.slice)
    finally:
        conn.close()
    print(
        json.dumps(
            {"slice": args.slice, "floor_met": floor_met, "criteria": rows},
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


def _cmd_claim_acquire(args: argparse.Namespace) -> int:
    # Acquire a slice claim for --owner. The store refuses (clean
    # ValueError → rc-1) on a file-ownership overlap with a different owner's
    # claim or an unsatisfied dependency; a same-owner re-acquire is idempotent.
    import store

    conn, slug = _claim_db(args.state_path)
    try:
        row = store.acquire_claim(conn, slug, args.slice, args.owner)
    finally:
        conn.close()
    print(json.dumps(row, indent=2, ensure_ascii=False))
    return 0


def _cmd_claim_release(args: argparse.Namespace) -> int:
    # Release a slice claim. DELETEs the row. --force breaks a stale
    # claim held by another owner (the operator's lever; no TTL).
    import store

    conn, slug = _claim_db(args.state_path)
    try:
        row = store.release_claim(conn, slug, args.slice, args.owner, force=args.force)
    finally:
        conn.close()
    print(json.dumps(row, indent=2, ensure_ascii=False))
    return 0


def _cmd_claim_list(args: argparse.Namespace) -> int:
    # List the project's active claims. JSON out.
    import store

    conn, slug = _claim_db(args.state_path)
    try:
        rows = store.read_claims(conn, slug)
    finally:
        conn.close()
    print(json.dumps(rows, indent=2, ensure_ascii=False))
    return 0


def _claim_db(state_path: str) -> tuple[object, str]:
    """Open the claim store off `--state-path`; return (connection, slug).

    Same slug/db seam as `accept`/`decisions`/`ground`/the state commands (the
    store-DB-deriving command family, not the `state-*` `--path`)."""
    import store

    slug, db_path = store.slug_and_db_from_state_path(state_path)
    conn = store.connect(db_path)
    store.init_db(conn)
    return conn, slug


def _cmd_slice_set(args: argparse.Namespace) -> int:
    # Orchestrator declares a slice's owned paths + dependencies + title at
    # touchpoint #1. Writes ONLY file_ownership/depends_on/title. JSON arrays
    # parsed from the CLI.
    import store

    conn, slug = _claim_db(args.state_path)
    try:
        row = store.set_slice_attrs(
            conn,
            slug,
            args.slice,
            file_ownership=args.file_ownership,
            depends_on=args.depends_on,
            title=args.title,
        )
    finally:
        conn.close()
    print(json.dumps(row, indent=2, ensure_ascii=False))
    return 0


def _cmd_slice_get(args: argparse.Namespace) -> int:
    # Read one slice's declared attributes. Raises (rc-1) if the
    # slice has no declared row yet.
    import store

    conn, slug = _claim_db(args.state_path)
    try:
        row = store.read_slice(conn, slug, args.slice)
    finally:
        conn.close()
    if row is None:
        raise ValueError(
            f"no declared attributes for slice {args.slice} — `slice set` first"
        )
    print(json.dumps(row, indent=2, ensure_ascii=False))
    return 0


def _cmd_slice_list(args: argparse.Namespace) -> int:
    # List every declared slice for the project. JSON out.
    import store

    conn, slug = _claim_db(args.state_path)
    try:
        rows = store.list_slices(conn, slug)
    finally:
        conn.close()
    print(json.dumps(rows, indent=2, ensure_ascii=False))
    return 0


def _cmd_integration_record(args: argparse.Namespace) -> int:
    # Record the integration-gate outcome for a slice. The
    # orchestrator runs the dual gate (end-to-end scenario + full suite) at merge
    # time and records the result here — the CLI does NOT run the suite (no heavy
    # runner). Status is enforced passed/failed at BOTH the CLI (argparse choices)
    # and the store (record_integration re-check). Append-only; --slice-addressed.
    import store

    conn, slug = _claim_db(args.state_path)
    try:
        row = store.record_integration(conn, slug, args.slice, args.status)
    finally:
        conn.close()
    print(json.dumps(row, indent=2, ensure_ascii=False))
    return 0


def _cmd_integration_list(args: argparse.Namespace) -> int:
    # List the project's recorded integration outcomes. JSON out.
    import store

    conn, slug = _claim_db(args.state_path)
    try:
        rows = store.list_integration(conn, slug)
    finally:
        conn.close()
    print(json.dumps(rows, indent=2, ensure_ascii=False))
    return 0


def _render_settled_block(rows: list[dict]) -> str:
    """Render settled decisions as Decision Log lines for the inject block.

    Reuses `format_decision` so the inject view matches the markdown format
    exactly — same lossless structured→text rendering as `log`. Empty list →
    empty string (a valid "no settled decisions yet" state)."""
    if not rows:
        return ""
    return "\n".join(
        format_decision(
            r["status"],
            r["decision"],
            r["rationale"],
            date=r["date"],
            author=r["author"] or "user",
        )
        for r in rows
    )


def _cmd_ground(args: argparse.Namespace) -> int:
    # The inject bridge: emit North Star + settled
    # decisions + Agent Calibration as one block injected into EVERY agent spawn.
    # Each source path is a parameter (instance config). The North Star and
    # calibration are read with `extract_section`; the settled decisions come
    # from the TABLE via the state-path seam — reusing the settled lower layer.
    import store

    north_star = extract_section(args.north_star_path, "North Star")

    slug, db_path = store.slug_and_db_from_state_path(args.state_path)
    conn = store.connect(db_path)
    try:
        store.init_db(conn)
        rows = store.settled_decisions(conn, slug)
    finally:
        conn.close()
    settled = _render_settled_block(rows)

    calibration = extract_section(args.memories_path, "Agent Calibration")

    parts = []
    if north_star:
        parts.append(f"## North Star\n\n{north_star}")
    if settled:
        parts.append(f"## Settled Decisions\n\n{settled}")
    if calibration:
        parts.append(f"## Agent Calibration\n\n{calibration}")
    print("\n\n".join(parts))
    return 0


def _cmd_regression_run(args: argparse.Namespace) -> int:
    # Replay the WHOLE perturbed corpus and return its result as the exit code —
    # 0 iff every invariant holds across its perturbations, non-0 otherwise (the
    # exit code is the gate). The corpus + runner logic
    # lives in the regression.py leaf; this handler is a thin caller.
    import regression

    return regression.run_corpus()


def _cmd_regression_list(args: argparse.Namespace) -> int:
    # Enumerate the corpus entries with their slice tags. JSON.
    import regression

    print(json.dumps(regression.list_entries(), indent=2, ensure_ascii=False))
    return 0


def _cmd_log_section(args: argparse.Namespace) -> int:
    section = append_log_section(
        args.path,
        label=args.label,
        body=args.body,
        date=args.date,
    )
    print(section)
    return 0


# The state CLI is re-backed onto the SQLite store (the store is the single
# source AND the single read path — there is no separate JSON projection). The
# external JSON object I/O and partial-object
# -accept are preserved unchanged, so the SQLite swap stays invisible to the
# /devteam skill (STATUS / resume read through `state-read`). `import store` is
# local to these commands so the module imports without sqlite at the top level.
# `new_state` (in devteam.py) is still used by state-init.


def _state_db(path: str) -> tuple[object, str]:
    """Open the store backing the state `--path`; return (open connection, slug)."""
    import store

    slug, db_path = store.slug_and_db_from_state_path(path)
    conn = store.connect(db_path)
    store.init_db(conn)
    return conn, slug


def _cmd_state_write(args: argparse.Namespace) -> int:
    import store

    state = json.loads(args.json)
    if not isinstance(state, dict):
        raise ValueError(
            f"--json must be a JSON object (the slice-state dict), got "
            f"{type(state).__name__}"
        )
    # Slice-addressing: an explicit --slice resolves the
    # target row in a multi-slice project DB. It is stamped into the state object
    # so the store's existing slice-resolution (state['slice']) picks the right
    # row. Absent --slice, single-row behaviour is preserved (back-compat).
    if args.slice is not None:
        state["slice"] = args.slice
    conn, slug = _state_db(args.path)
    try:
        written = store.write_slice_state(conn, slug, state)
    finally:
        conn.close()
    print(json.dumps(written, indent=2, ensure_ascii=False))
    return 0


def _cmd_state_read(args: argparse.Namespace) -> int:
    import store

    conn, slug = _state_db(args.path)
    try:
        # Explicit --slice addresses one row; absent, the single-row read is
        # preserved (the store raises a clear error if >1 row and no --slice).
        state = store.read_slice_state(conn, slug, args.slice)
    finally:
        conn.close()
    print(json.dumps(state, indent=2, ensure_ascii=False))
    return 0


def _cmd_advance(args: argparse.Namespace) -> int:
    # The reducer: read the slice state, apply the loop
    # event, persist the new state, print it. --slice addresses the row in a
    # multi-slice DB; --reenter is the operator's escape re-entry target (only
    # required + validated for the escape/escalation events).
    import store

    conn, slug = _state_db(args.path)
    try:
        state = store.read_slice_state(conn, slug, args.slice)
        new = advance(state, args.event, reenter=args.reenter)
        if args.slice is not None:
            new["slice"] = args.slice
        written = store.write_slice_state(conn, slug, new)
    finally:
        conn.close()
    print(json.dumps(written, indent=2, ensure_ascii=False))
    return 0


def _cmd_next(args: argparse.Namespace) -> int:
    # `next`: read the slice state, return the next
    # action — which agent to spawn (with the grounding reminder) or which open
    # interrupt to surface first. The conversation asks this instead of
    # remembering where it is, so position survives a compaction.
    import store

    conn, slug = _state_db(args.path)
    try:
        state = store.read_slice_state(conn, slug, args.slice)
    finally:
        conn.close()
    print(json.dumps(next_action(state), indent=2, ensure_ascii=False))
    return 0


def _cmd_state_init(args: argparse.Namespace) -> int:
    import store

    state = new_state(args.slice, args.spec)
    conn, slug = _state_db(args.path)
    try:
        store.init_project(conn, slug, args.spec)
        written = store.write_slice_state(conn, slug, state)
    finally:
        conn.close()
    print(json.dumps(written, indent=2, ensure_ascii=False))
    return 0


def _cmd_state_import(args: argparse.Namespace) -> int:
    # One-time migration: import an existing JSON slice-state file
    # into the store. Idempotent — safe to re-run. Operator-run, not
    # loop-driven. The slug is derived from the same --path seam as the state
    # commands, so the import lands in the project's own store.
    import store

    conn, slug = _state_db(args.path)
    try:
        imported = store.import_json_state(conn, slug, args.json_path)
    finally:
        conn.close()
    print(json.dumps(imported, indent=2, ensure_ascii=False))
    return 0


def _cmd_memory_extract(args: argparse.Namespace) -> int:
    print(extract_section(args.path, args.section))
    return 0


def _json_int_list(raw: str) -> list[int]:
    """argparse type for --depends-on: a JSON array of slice numbers.

    Raises argparse.ArgumentTypeError on a non-array / non-int element so a
    malformed value is a clean usage error, not a traceback."""
    try:
        val = json.loads(raw)
    except json.JSONDecodeError as e:
        raise argparse.ArgumentTypeError(f"not valid JSON: {raw!r} ({e})") from e
    if not isinstance(val, list) or not all(isinstance(x, int) for x in val):
        raise argparse.ArgumentTypeError(
            f"--depends-on must be a JSON array of slice numbers, got {raw!r}"
        )
    return val


def _json_str_list(raw: str) -> list[str]:
    """argparse type for --file-ownership: a JSON array of path strings."""
    try:
        val = json.loads(raw)
    except json.JSONDecodeError as e:
        raise argparse.ArgumentTypeError(f"not valid JSON: {raw!r} ({e})") from e
    if not isinstance(val, list) or not all(isinstance(x, str) for x in val):
        raise argparse.ArgumentTypeError(
            f"--file-ownership must be a JSON array of paths, got {raw!r}"
        )
    return val


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="devteam.py",
        description="Dev-team loop helper (Decision Log + slice state)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_log = sub.add_parser("log", help="append a Decision Log entry")
    p_log.add_argument("--path", required=True, help="project file to append to")
    p_log.add_argument("--status", required=True, choices=VALID_STATUSES)
    p_log.add_argument("--decision", required=True)
    p_log.add_argument("--rationale", required=True)
    p_log.add_argument("--date", default=None, help="YYYY-MM-DD (default: today UTC)")
    p_log.add_argument(
        "--author",
        default=os.environ.get("USER") or "user",
        help="decision author for the '<author> call' suffix "
        "(default: $USER, else 'user')",
    )
    p_log.add_argument(
        "--state-path",
        default=None,
        help="state file path — the store DB is derived from it (same seam as "
        "state-*). When given, the decision is inserted as the AUTHORITATIVE "
        "row in the store AND rendered to the markdown line from the same args. "
        "Omit for markdown-only mode.",
    )
    p_log.add_argument(
        "--supersedes",
        type=int,
        default=None,
        help="id of the prior decision a `revised` entry replaces (the "
        "superseded one stops being settled). Requires --state-path.",
    )
    p_log.set_defaults(func=_cmd_log)

    p_dec = sub.add_parser(
        "decisions",
        help="print the settled decisions (from the store table) as JSON",
    )
    p_dec.add_argument(
        "--state-path",
        required=True,
        help="state file path (the store DB is derived from it, as for state-*)",
    )
    p_dec.add_argument(
        "--status",
        default=None,
        choices=VALID_STATUSES,
        help="optional filter to a single status (default: all settled)",
    )
    p_dec.set_defaults(func=_cmd_decisions)

    # accept — the acceptance registry. Nested subcommands (add/result/list),
    # all --slice-addressed + --state-path slug seam (same seam as
    # decisions/ground/state-*).
    p_acc = sub.add_parser(
        "accept",
        help="acceptance registry: commit/record/query per-slice firm criteria",
    )
    acc_sub = p_acc.add_subparsers(dest="accept_command", required=True)

    def _accept_common(p):
        # --state-path (slug seam) + --slice (addressing) are on every accept
        # subcommand — factored here (three real callers earn the helper).
        p.add_argument(
            "--state-path",
            required=True,
            help="state file path (the store DB is derived from it, as for state-*)",
        )
        p.add_argument("--slice", type=int, required=True, help="slice number")
        return p

    p_acc_add = _accept_common(
        acc_sub.add_parser(
            "add",
            help="commit one firm criterion (orchestrator, at touchpoint #1)",
        )
    )
    p_acc_add.add_argument(
        "--criterion", required=True, help="the firm acceptance criterion text"
    )
    p_acc_add.set_defaults(func=_cmd_accept_add)

    p_acc_res = _accept_common(
        acc_sub.add_parser(
            "result", help="record a pass/fail for one criterion (Sentinel, at PASS)"
        )
    )
    p_acc_res.add_argument(
        "--passed",
        type=int,
        required=True,
        choices=(0, 1),
        help="1=pass, 0=fail for this criterion",
    )
    p_acc_res.add_argument(
        "--id",
        type=int,
        default=None,
        help="address the criterion by its id (from `accept list`)",
    )
    p_acc_res.add_argument(
        "--criterion",
        default=None,
        help="address the criterion by its exact text (alternative to --id)",
    )
    p_acc_res.set_defaults(func=_cmd_accept_result)

    p_acc_list = _accept_common(
        acc_sub.add_parser(
            "list", help="list a slice's criteria + whether the all-passed floor is met"
        )
    )
    p_acc_list.set_defaults(func=_cmd_accept_list)

    # claim — multi-session slice claims. Nested subcommands
    # (acquire/release/list), --slice-addressed + --owner + the --state-path slug
    # seam (same store-DB-deriving family as accept/decisions/ground).
    p_clm = sub.add_parser(
        "claim",
        help="multi-session slice claims: acquire/release/list (atomic + cooperative)",
    )
    clm_sub = p_clm.add_subparsers(dest="claim_command", required=True)

    def _claim_state_arg(p):
        p.add_argument(
            "--state-path",
            required=True,
            help="state file path (the store DB is derived from it, as for state-*)",
        )
        p.add_argument("--slice", type=int, required=True, help="slice number")
        return p

    p_clm_acq = _claim_state_arg(
        clm_sub.add_parser("acquire", help="claim a slice for --owner (the lock)")
    )
    p_clm_acq.add_argument(
        "--owner",
        required=True,
        help="opaque owner/session id — explicit, never auto-derived",
    )
    p_clm_acq.set_defaults(func=_cmd_claim_acquire)

    p_clm_rel = _claim_state_arg(
        clm_sub.add_parser("release", help="release a slice claim (deletes the row)")
    )
    p_clm_rel.add_argument(
        "--owner",
        required=True,
        help="owner releasing the claim",
    )
    p_clm_rel.add_argument(
        "--force",
        action="store_true",
        help="break a stale claim held by a different owner (the operator's lever)",
    )
    p_clm_rel.set_defaults(func=_cmd_claim_release)

    p_clm_list = clm_sub.add_parser("list", help="list the project's active claims")
    p_clm_list.add_argument(
        "--state-path",
        required=True,
        help="state file path (the store DB is derived from it, as for state-*)",
    )
    p_clm_list.set_defaults(func=_cmd_claim_list)

    # slice — declared attributes. Nested subcommands (set/get/list),
    # --slice-addressed + --state-path slug seam. Writes file_ownership +
    # depends_on + title only.
    p_slc = sub.add_parser(
        "slice",
        help="declared slice attributes: set/get/list file_ownership + depends_on + title",
    )
    slc_sub = p_slc.add_subparsers(dest="slice_command", required=True)

    p_slc_set = slc_sub.add_parser(
        "set",
        help="declare a slice's file_ownership + depends_on + title (touchpoint #1)",
    )
    p_slc_set.add_argument(
        "--state-path",
        required=True,
        help="state file path (the store DB is derived from it, as for state-*)",
    )
    p_slc_set.add_argument("--slice", type=int, required=True, help="slice number")
    p_slc_set.add_argument(
        "--file-ownership",
        type=_json_str_list,
        default=None,
        dest="file_ownership",
        help='JSON array of owned concrete paths / dir prefixes, e.g. \'["store.py","agents/"]\'',
    )
    p_slc_set.add_argument(
        "--depends-on",
        type=_json_int_list,
        default=None,
        dest="depends_on",
        help="JSON array of slice numbers this depends on, e.g. '[3,5]'",
    )
    p_slc_set.add_argument(
        "--title", default=None, help="human-legible slice name (shown by list/get)"
    )
    p_slc_set.set_defaults(func=_cmd_slice_set)

    p_slc_get = slc_sub.add_parser("get", help="read one slice's declared attributes")
    p_slc_get.add_argument(
        "--state-path",
        required=True,
        help="state file path (the store DB is derived from it, as for state-*)",
    )
    p_slc_get.add_argument("--slice", type=int, required=True, help="slice number")
    p_slc_get.set_defaults(func=_cmd_slice_get)

    p_slc_list = slc_sub.add_parser("list", help="list every declared slice")
    p_slc_list.add_argument(
        "--state-path",
        required=True,
        help="state file path (the store DB is derived from it, as for state-*)",
    )
    p_slc_list.set_defaults(func=_cmd_slice_list)

    # integration — the project-level integration gate. Nested subcommands
    # (record/list), --slice-addressed + the --state-path slug seam (same
    # store-DB-deriving family as accept/claim/slice). `record` enforces the
    # passed/failed vocabulary at the CLI via argparse choices (the store
    # re-checks — defence at both layers).
    p_int = sub.add_parser(
        "integration",
        help="integration gate: record/list per-slice integration outcomes",
    )
    int_sub = p_int.add_subparsers(dest="integration_command", required=True)

    p_int_rec = int_sub.add_parser(
        "record",
        help="record an integration-gate outcome (orchestrator, post-merge)",
    )
    p_int_rec.add_argument(
        "--state-path",
        required=True,
        help="state file path (the store DB is derived from it, as for state-*)",
    )
    p_int_rec.add_argument("--slice", type=int, required=True, help="slice number")
    p_int_rec.add_argument(
        "--status",
        required=True,
        choices=("passed", "failed"),
        help="the integration-gate outcome (enforced at the CLI AND the store)",
    )
    p_int_rec.set_defaults(func=_cmd_integration_record)

    p_int_list = int_sub.add_parser(
        "list", help="list the project's recorded integration outcomes"
    )
    p_int_list.add_argument(
        "--state-path",
        required=True,
        help="state file path (the store DB is derived from it, as for state-*)",
    )
    p_int_list.set_defaults(func=_cmd_integration_list)

    # regression — the perturbed real-path corpus runner. Nested
    # subcommands (run/list). NO --state-path/store: the corpus makes its own
    # throwaway DBs (regression.py). `run`'s exit code is the gate; `list`
    # enumerates entries with slice tags. Logic lives in regression.py (leaf).
    p_reg = sub.add_parser(
        "regression",
        help="regression corpus: run the perturbed real-path corpus / list its entries",
    )
    reg_sub = p_reg.add_subparsers(dest="regression_command", required=True)
    p_reg_run = reg_sub.add_parser(
        "run", help="replay the whole corpus; exit 0 iff every invariant holds"
    )
    p_reg_run.set_defaults(func=_cmd_regression_run)
    p_reg_list = reg_sub.add_parser(
        "list", help="enumerate the corpus entries with their slice tags"
    )
    p_reg_list.set_defaults(func=_cmd_regression_list)

    p_gr = sub.add_parser(
        "ground",
        help="emit the inject block: North Star + settled decisions + calibration",
    )
    p_gr.add_argument(
        "--north-star-path",
        required=True,
        help="markdown file holding the `## North Star` section (instance config)",
    )
    p_gr.add_argument(
        "--memories-path",
        required=True,
        help="markdown file holding the `## Agent Calibration` section",
    )
    p_gr.add_argument(
        "--state-path",
        required=True,
        help="state file path — the store DB (and its settled decisions) derive "
        "from it (same seam as state-*)",
    )
    p_gr.set_defaults(func=_cmd_ground)

    p_ls = sub.add_parser("log-section", help="append a run-log section")
    p_ls.add_argument("--path", required=True, help="project file to append to")
    p_ls.add_argument(
        "--label",
        required=True,
        help='section label, e.g. "Vigil — HOLDS" or "Auddie #2 cycle 1"',
    )
    p_ls.add_argument("--body", required=True, help="the concise section body")
    p_ls.add_argument("--date", default=None, help="YYYY-MM-DD (default: today UTC)")
    p_ls.set_defaults(func=_cmd_log_section)

    p_sw = sub.add_parser("state-write", help="write slice state from a JSON object")
    p_sw.add_argument("--path", required=True, help="state file path")
    p_sw.add_argument("--json", required=True, help="state as a JSON object string")
    p_sw.add_argument(
        "--slice",
        type=int,
        default=None,
        help="slice number to address in a multi-slice project DB. "
        "Absent: single-row behaviour (back-compat).",
    )
    p_sw.set_defaults(func=_cmd_state_write)

    p_sr = sub.add_parser("state-read", help="read slice state")
    p_sr.add_argument("--path", required=True, help="state file path")
    p_sr.add_argument(
        "--slice",
        type=int,
        default=None,
        help="slice number to address in a multi-slice project DB. "
        "Absent: single-row read (raises if >1 row).",
    )
    p_sr.set_defaults(func=_cmd_state_read)

    p_adv = sub.add_parser(
        "advance",
        help="apply a loop event to the slice state (the reducer)",
    )
    p_adv.add_argument("--path", required=True, help="state file path")
    p_adv.add_argument(
        "--slice",
        type=int,
        default=None,
        help="slice number to address in a multi-slice project DB",
    )
    p_adv.add_argument(
        "--event",
        required=True,
        choices=ALL_EVENTS,
        help="the loop event (a clean token — the skill normalises glyphs)",
    )
    p_adv.add_argument(
        "--reenter",
        default=None,
        choices=REENTER_CHOICES,
        help="escape/escalation re-entry target (the operator's "
        "resume-from-the-right-point call). REQUIRED for forge-escalate / "
        "sentinel-escape; validated, not computed.",
    )
    p_adv.set_defaults(func=_cmd_advance)

    p_nx = sub.add_parser(
        "next",
        help="report the next action for the slice (which agent / which "
        "interrupt to surface)",
    )
    p_nx.add_argument("--path", required=True, help="state file path")
    p_nx.add_argument(
        "--slice",
        type=int,
        default=None,
        help="slice number to address in a multi-slice project DB",
    )
    p_nx.set_defaults(func=_cmd_next)

    p_si = sub.add_parser("state-init", help="initialise a fresh slice state")
    p_si.add_argument("--path", required=True, help="state file path")
    p_si.add_argument("--slice", required=True, type=int, help="slice number")
    p_si.add_argument("--spec", required=True, help="living spec path")
    p_si.set_defaults(func=_cmd_state_init)

    p_im = sub.add_parser(
        "state-import",
        help="one-time import of an existing JSON slice-state file into the "
        "store (idempotent)",
    )
    p_im.add_argument(
        "--path",
        required=True,
        help="state file path (the store DB is derived from it, as for state-*)",
    )
    p_im.add_argument(
        "--json-path",
        required=True,
        help="path to the existing JSON slice-state file to import",
    )
    p_im.set_defaults(func=_cmd_state_import)

    p_me = sub.add_parser(
        "memory-extract",
        help="print a `## <section>` body from a markdown file (the inject bridge)",
    )
    p_me.add_argument(
        "--path",
        required=True,
        help="markdown file to read (e.g. the MEMORIES.md path — instance config)",
    )
    p_me.add_argument(
        "--section",
        default="Agent Calibration",
        help='section header without the `## ` prefix (default: "Agent Calibration")',
    )
    p_me.set_defaults(func=_cmd_memory_extract)

    return parser
