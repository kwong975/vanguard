#!/usr/bin/env python3
"""Repo-quality deterministic checks — Sentinel's deterministic pass.

Sentinel's post-build gate runs three passes; the FIRST is deterministic (no LLM):
linters, type checker, secret scan, AND this repo-quality lint. This module is
that lint.

Six check categories:

  code_quality      compressed/minified lines, oversized files
  repo_coherence    README links pointing at files that do not exist
  test_visibility   no tests at all, or tests buried off the beaten path
  schema_safety     schema DDL with no migration logic and no "fresh DB" note
  documentation     missing README, or a README with no usage / no limitations
  heuristic_honesty threshold constants not labelled as tunable heuristics

Plus a production-readiness classifier (prototype / local_tool / production_ready)
from five positive signals: tests, migrations, CI, config, error handling.

This is the PORTABLE plumbing layer: a plain library with NO Claude Code
dependency, standard library only, runtime-agnostic. The repo to scan is a
PARAMETER — nothing is hardcoded. Any runtime calls it; Sentinel's deterministic
pass shells out to the CLI:

    python3 repo_quality.py --repo <path>          # human-readable report
    python3 repo_quality.py --repo <path> --json   # machine-readable findings

Exit code is 1 when there is a blocking (CRITICAL/MAJOR) finding, 0 otherwise,
so a caller can branch on the gate verdict without parsing output.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Thresholds and scan config (heuristics — tunable, not universal truths)
# ---------------------------------------------------------------------------

# A non-comment source line longer than this is treated as compressed/minified.
MAX_LINE_LENGTH = 500
# A source file longer than this is flagged as a candidate for splitting.
MAX_FILE_LINES = 1000

# Source file extensions the scan considers.
SOURCE_EXTENSIONS = {".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".java"}

# Directories never scanned (vendored, generated, or VCS internals).
SKIP_DIRS = {
    ".git",
    ".harness",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "dist",
    "build",
}

# Severities. CRITICAL/MAJOR block the gate; MINOR passes with a note.
CRITICAL = "CRITICAL"
MAJOR = "MAJOR"
MINOR = "MINOR"

# Readiness tiers, by count of the five positive signals.
PRODUCTION_READY = "production_ready"
LOCAL_TOOL = "local_tool"
PROTOTYPE = "prototype"


# ---------------------------------------------------------------------------
# File walking
# ---------------------------------------------------------------------------


def _should_skip(path: Path) -> bool:
    """True if any path component is a skip dir (vendored/generated/VCS)."""
    return any(part in SKIP_DIRS for part in path.parts)


def _source_files(repo: Path) -> list[Path]:
    """All scannable source files under `repo`, skipping vendored/generated dirs."""
    return [
        f
        for f in repo.rglob("*")
        if f.is_file() and f.suffix in SOURCE_EXTENSIONS and not _should_skip(f)
    ]


def _read(path: Path) -> str | None:
    """Read a file as text, tolerating encoding noise. None on OS error."""
    try:
        return path.read_text(errors="ignore")
    except OSError:
        return None


def _finding(category: str, severity: str, message: str, file: str = "") -> dict:
    """One finding record. Shape is stable — callers and tests depend on it."""
    return {
        "category": category,
        "severity": severity,
        "message": message,
        "file": file,
    }


# ---------------------------------------------------------------------------
# Category 1 — code quality
# ---------------------------------------------------------------------------


def check_code_quality(repo: Path) -> list[dict]:
    """Flag compressed/minified lines (CRITICAL) and oversized files (MAJOR)."""
    findings: list[dict] = []

    for f in _source_files(repo):
        content = _read(f)
        if content is None:
            continue
        lines = content.split("\n")
        rel = str(f.relative_to(repo))

        # Compressed / minified: any non-comment source line over the limit.
        # One finding per file — the first offending line is enough signal.
        for i, line in enumerate(lines):
            if len(line) > MAX_LINE_LENGTH and not line.strip().startswith("#"):
                findings.append(
                    _finding(
                        "code_quality",
                        CRITICAL,
                        f"Compressed/minified code: line {i + 1} is {len(line)} "
                        f"chars — expand to readable format",
                        rel,
                    )
                )
                break

        if len(lines) > MAX_FILE_LINES:
            findings.append(
                _finding(
                    "code_quality",
                    MAJOR,
                    f"File has {len(lines)} lines (limit {MAX_FILE_LINES}) — "
                    f"consider splitting by concern",
                    rel,
                )
            )

    return findings


# ---------------------------------------------------------------------------
# Category 2 — repo coherence
# ---------------------------------------------------------------------------


def _find_readme(repo: Path) -> Path | None:
    """The first README the repo has, by the usual names; None if absent."""
    for name in ("README.md", "readme.md", "README.rst", "README"):
        candidate = repo / name
        if candidate.exists():
            return candidate
    return None


def check_repo_coherence(repo: Path) -> list[dict]:
    """Flag relative README links whose target file does not exist (MAJOR)."""
    findings: list[dict] = []
    readme = _find_readme(repo)
    if not readme:
        return findings  # absence of a README is the documentation check's job

    content = _read(readme) or ""
    rel_readme = str(readme.relative_to(repo))

    for match in re.finditer(r"\[([^\]]+)\]\(([^)]+)\)", content):
        link_text, link_path = match.group(1), match.group(2)
        # Skip URLs, in-page anchors, and absolute paths — only local files.
        if link_path.startswith(("http://", "https://", "#", "/")):
            continue
        link_path = link_path.split("#")[0]  # strip any fragment
        if not link_path:
            continue
        if not (repo / link_path).exists():
            findings.append(
                _finding(
                    "repo_coherence",
                    MAJOR,
                    f"README references '{link_path}' ('{link_text}') but file "
                    f"does not exist",
                    rel_readme,
                )
            )

    return findings


# ---------------------------------------------------------------------------
# Category 3 — test visibility
# ---------------------------------------------------------------------------


def _is_test_file(path: Path) -> bool:
    name = path.name
    return (
        name.startswith("test_") or name.endswith("_test.py") or "tests" in path.parts
    )


def check_test_visibility(repo: Path) -> list[dict]:
    """Flag a repo with source but no tests (CRITICAL), or buried tests (MINOR)."""
    findings: list[dict] = []

    test_files: list[Path] = []
    source_files: list[Path] = []
    for f in _source_files(repo):
        if _is_test_file(f):
            test_files.append(f)
        elif f.suffix == ".py" and not f.name.startswith("_"):
            source_files.append(f)

    if not test_files and source_files:
        findings.append(
            _finding(
                "test_visibility",
                CRITICAL,
                "No test files found in repository — tests required for any "
                "non-trivial project",
            )
        )
        return findings

    # Tests that live neither in a test-named dir nor at the repo root.
    for tf in test_files:
        if "test" not in str(tf.parent).lower() and tf.parent != repo:
            findings.append(
                _finding(
                    "test_visibility",
                    MINOR,
                    "Test file in non-standard location — consider moving to "
                    "tests/ directory",
                    str(tf.relative_to(repo)),
                )
            )

    return findings


# ---------------------------------------------------------------------------
# Category 4 — schema safety
# ---------------------------------------------------------------------------

_DDL_KEYWORDS = (
    "CREATE TABLE",
    "ALTER TABLE",
    "ADD COLUMN",
    "DROP COLUMN",
    "DROP TABLE",
)
_MIGRATION_KEYWORDS = (
    "migration",
    "migrate",
    "alembic",
    "flyway",
    "rollback",
    "upgrade()",
    "downgrade()",
)
_MIGRATION_DIRS = ("migrations", "alembic", "migrate")
_FRESH_DB_PHRASES = (
    "fresh db",
    "fresh database",
    "new database",
    "schema reset",
    "incompatible",
)
# This checker's own source and test define the DDL/migration keywords above as
# string literals. Scanning them would self-suppress the gate — the word
# "migration" in this file counted as migration evidence, so any repo that
# vendors the checker passed schema_safety regardless. Exclude them so the
# signal comes from the AUDITED repo's code, not the checker's own definitions.
_SELF_FILES = {Path(__file__).name, "test_" + Path(__file__).name}


def check_schema_safety(repo: Path) -> list[dict]:
    """Flag schema DDL with no migration logic and no 'fresh DB' note (CRITICAL).

    Suppressed when the repo has a migration dir/keyword, or the README admits a
    fresh database is required on upgrade.
    """
    findings: list[dict] = []
    has_ddl = False
    has_migration = False
    ddl_files: list[str] = []

    # schema_safety also scans .sql files — DDL's natural home, which
    # `_source_files` (SOURCE_EXTENSIONS) excludes. Without this, a schema file
    # like `schema.sql` is invisible to the gate. Scoped to this check only; the
    # other checks (minified lines, README links, etc.) do not want .sql.
    sql_files = [f for f in repo.rglob("*.sql") if not _should_skip(f)]
    for f in _source_files(repo) + sql_files:
        if f.name in _SELF_FILES:
            continue  # the checker's own keyword definitions are not evidence
        content = _read(f)
        if content is None:
            continue
        upper = content.upper()
        lower = content.lower()
        if any(kw in upper for kw in _DDL_KEYWORDS):
            has_ddl = True
            ddl_files.append(str(f.relative_to(repo)))
        if any(kw in lower for kw in _MIGRATION_KEYWORDS):
            has_migration = True

    if any((repo / d).is_dir() for d in _MIGRATION_DIRS):
        has_migration = True

    has_fresh_db_doc = False
    readme = _find_readme(repo)
    if readme:
        readme_lower = (_read(readme) or "").lower()
        has_fresh_db_doc = any(p in readme_lower for p in _FRESH_DB_PHRASES)

    if has_ddl and not has_migration and not has_fresh_db_doc:
        for df in ddl_files:
            findings.append(
                _finding(
                    "schema_safety",
                    CRITICAL,
                    "Schema DDL found without migration logic or explicit "
                    "'fresh DB required' documentation",
                    df,
                )
            )

    return findings


# ---------------------------------------------------------------------------
# Category 5 — documentation honesty
# ---------------------------------------------------------------------------

_USAGE_KEYWORDS = ("usage", "setup", "install", "getting started", "quick start")
_LIMITATION_KEYWORDS = (
    "limitation",
    "known issue",
    "caveat",
    "does not",
    "not supported",
    "out of scope",
)


def check_documentation_honesty(repo: Path) -> list[dict]:
    """Flag a missing README (CRITICAL), or one with no usage / no limitations (MAJOR)."""
    findings: list[dict] = []
    readme = _find_readme(repo)

    if not readme:
        findings.append(
            _finding(
                "documentation",
                CRITICAL,
                "No README found — every project must document what it does, its "
                "limitations, and setup instructions",
            )
        )
        return findings

    lower = (_read(readme) or "").lower()
    rel_readme = str(readme.relative_to(repo))

    if not any(kw in lower for kw in _USAGE_KEYWORDS):
        findings.append(
            _finding(
                "documentation",
                MAJOR,
                "Missing usage/setup instructions in README",
                rel_readme,
            )
        )
    if not any(kw in lower for kw in _LIMITATION_KEYWORDS):
        findings.append(
            _finding(
                "documentation",
                MAJOR,
                "Missing limitations/caveats section — README must be honest "
                "about what v1 does NOT do",
                rel_readme,
            )
        )

    return findings


# ---------------------------------------------------------------------------
# Category 6 — heuristic honesty
# ---------------------------------------------------------------------------

_THRESHOLD_PATTERNS = ("threshold", "cutoff", "magic number", "hardcoded")
_HONESTY_LABELS = (
    "heuristic",
    "rule of thumb",
    "approximat",
    "tunable",
    "configurable",
    "v1 rule",
    "arbitrary",
)


def check_heuristic_honesty(repo: Path) -> list[dict]:
    """Flag threshold-style logic not labelled as a tunable heuristic (MINOR)."""
    findings: list[dict] = []

    for f in _source_files(repo):
        content = _read(f)
        if content is None:
            continue
        lower = content.lower()
        has_threshold = any(p in lower for p in _THRESHOLD_PATTERNS)
        has_label = any(label in lower for label in _HONESTY_LABELS)
        if has_threshold and not has_label:
            findings.append(
                _finding(
                    "heuristic_honesty",
                    MINOR,
                    "Threshold-based logic found without heuristic labeling — add "
                    "a comment noting this is a v1 heuristic, not a universal truth",
                    str(f.relative_to(repo)),
                )
            )

    return findings


# ---------------------------------------------------------------------------
# Production-readiness classifier
# ---------------------------------------------------------------------------

_CI_MARKERS = (".github/workflows", ".gitlab-ci.yml", "Jenkinsfile", ".circleci")
_CONFIG_MARKERS = ("config.yaml", "config.json", ".env.example", "settings.py")


def classify_readiness(repo: Path) -> tuple[str, str]:
    """Classify the repo as prototype / local_tool / production_ready.

    Five positive signals, one point each:
      has_tests, has_migration, has_ci, has_config, has_error_handling.
    >= 4 → production_ready, 2-3 → local_tool, <= 1 → prototype.
    Returns (level, human-readable justification).

    NOTE: has_error_handling is a slot that always scores false (the flag is
    never set). The signal is listed for transparency but does not currently
    fire. Wiring it to a real detector is a future change, deliberately not made
    here.
    """
    signals = {
        "has_tests": False,
        "has_migration": False,
        "has_ci": False,
        "has_config": False,
        "has_error_handling": False,
    }

    for f in _source_files(repo):
        if f.name.startswith("test_") or f.name.endswith("_test.py"):
            signals["has_tests"] = True
            break

    if any((repo / d).is_dir() for d in _MIGRATION_DIRS):
        signals["has_migration"] = True
    if any((repo / marker).exists() for marker in _CI_MARKERS):
        signals["has_ci"] = True
    if any((repo / marker).exists() for marker in _CONFIG_MARKERS):
        signals["has_config"] = True

    present = [k for k, v in signals.items() if v]
    missing = [k for k, v in signals.items() if not v]
    score = len(present)

    if score >= 4:
        level = PRODUCTION_READY
        reason = f"Has {score}/5 production signals: {', '.join(present)}"
    elif score >= 2:
        level = LOCAL_TOOL
        reason = (
            f"Has {score}/5 production signals: {', '.join(present)}. "
            f"Missing: {', '.join(missing)}"
        )
    else:
        level = PROTOTYPE
        reason = (
            f"Only {score}/5 production signals present. Missing: {', '.join(missing)}"
        )

    return level, reason


# ---------------------------------------------------------------------------
# Aggregation, verdict, reporting
# ---------------------------------------------------------------------------

_ALL_CHECKS = (
    check_code_quality,
    check_repo_coherence,
    check_test_visibility,
    check_schema_safety,
    check_documentation_honesty,
    check_heuristic_honesty,
)


def run_all(repo: Path) -> tuple[list[dict], str, str]:
    """Run all six checks and the readiness classifier.

    Returns (findings, readiness_level, readiness_justification).
    """
    findings: list[dict] = []
    for check in _ALL_CHECKS:
        findings.extend(check(repo))
    level, reason = classify_readiness(repo)
    return findings, level, reason


def has_blockers(findings: list[dict]) -> bool:
    """True if any finding is CRITICAL or MAJOR — these block the gate."""
    return any(f["severity"] in (CRITICAL, MAJOR) for f in findings)


def format_report(findings: list[dict], level: str, reason: str) -> str:
    """Render findings + readiness + verdict as a human-readable markdown report."""
    lines = ["# Repo Quality Report\n"]

    for severity in (CRITICAL, MAJOR, MINOR):
        items = [f for f in findings if f["severity"] == severity]
        if items:
            lines.append(f"\n## {severity} ({len(items)})\n")
            for item in items:
                file_ref = f" — `{item['file']}`" if item.get("file") else ""
                lines.append(f"- [{item['category']}]{file_ref}: {item['message']}")

    if not findings:
        lines.append("\nNo issues found.\n")

    lines.append(f"\n## Production Readiness: {level}\n")
    lines.append(reason)

    verdict = "FAIL" if has_blockers(findings) else "PASS"
    lines.append(f"\n## Verdict: {verdict}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI — Sentinel's deterministic pass shells out to this
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="repo_quality.py",
        description="Deterministic repo-quality lint (Sentinel's deterministic pass)",
    )
    parser.add_argument(
        "--repo",
        required=True,
        help="path to the repo to scan (a parameter — never hardcoded)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit machine-readable JSON instead of the markdown report",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    repo = Path(args.repo).expanduser().resolve()
    if not repo.is_dir():
        print(f"error: not a directory: {repo}", file=sys.stderr)
        return 2

    findings, level, reason = run_all(repo)
    blocking = has_blockers(findings)

    if args.json:
        payload = {
            "repo": str(repo),
            "verdict": "FAIL" if blocking else "PASS",
            "readiness": level,
            "readiness_reason": reason,
            "blockers": sum(1 for f in findings if f["severity"] in (CRITICAL, MAJOR)),
            "minors": sum(1 for f in findings if f["severity"] == MINOR),
            "findings": findings,
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(format_report(findings, level, reason))

    # Exit non-zero on a blocking verdict so a caller can branch without parsing.
    return 1 if blocking else 0


if __name__ == "__main__":
    raise SystemExit(main())
