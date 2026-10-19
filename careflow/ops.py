"""Safe offline database lifecycle and recovery operations for CareFlow.

The commands in this module intentionally operate outside request handling.  A
backup is made with SQLite's online backup API, written to a temporary file,
verified, and atomically renamed.  Restore always targets a separate path and
is verified before the new file is selected by an operator.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
from uuid import uuid4

from careflow.engine import Pathway
from careflow.persistence import (
    CURRENT_SCHEMA_VERSION,
    DatabaseLifecycleError,
    SQLITE_BUSY_TIMEOUT_MS,
    SQLiteStore,
)


PATHWAY_FILE = Path(__file__).parent / "pathways" / "screening_to_care.yaml"


@dataclass(frozen=True)
class DatabaseReport:
    """Safe verification summary with counts, never row contents."""

    path: str
    schema_version: int
    supported_schema_version: int
    cases: int
    audit_events: int
    consents: int
    access_logs: int
    message_drafts: int
    notification_outbox: int
    verified_at: str


def _existing_file(path: str | Path) -> Path:
    resolved = Path(path).expanduser()
    if not resolved.is_file():
        raise DatabaseLifecycleError(f"database file does not exist: {resolved}")
    return resolved


def _pathway(pathway: str | Path | None) -> Pathway:
    return Pathway.load(pathway or PATHWAY_FILE)


def _report(store: SQLiteStore, path: Path) -> DatabaseReport:
    return DatabaseReport(
        path=str(path),
        schema_version=store.schema_version,
        supported_schema_version=CURRENT_SCHEMA_VERSION,
        cases=store.count("cases"),
        audit_events=store.count("audit_events"),
        consents=store.count("consents"),
        access_logs=store.count("access_logs"),
        message_drafts=store.count("message_drafts"),
        notification_outbox=store.count("notification_outbox"),
        verified_at=datetime.now(timezone.utc).isoformat(timespec="microseconds"),
    )


def verify_database(
    db_path: str | Path,
    *,
    pathway: str | Path | None = None,
) -> DatabaseReport:
    """Verify schema, SQLite integrity, pathway loading, and case history."""

    source = _existing_file(db_path)
    store = SQLiteStore(source, auto_migrate=False)
    try:
        store.verify_readiness()
        store.verify_cases(_pathway(pathway))
        return _report(store, source)
    finally:
        store.close()


def migrate_database(
    db_path: str | Path,
    *,
    pathway: str | Path | None = None,
) -> DatabaseReport:
    """Apply all ordered migrations, then verify the resulting database."""

    target = Path(db_path).expanduser()
    store = SQLiteStore(target)
    try:
        if store.startup_error is not None:
            raise store.startup_error
    finally:
        store.close()
    return verify_database(target, pathway=pathway)


def _backup_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / f".{destination.name}.{uuid4().hex}.tmp"
    source_store = SQLiteStore(source, auto_migrate=False)
    destination_connection: sqlite3.Connection | None = None
    try:
        source_store.verify_readiness()
        destination_connection = sqlite3.connect(
            temporary,
            timeout=SQLITE_BUSY_TIMEOUT_MS / 1_000,
        )
        destination_connection.execute("PRAGMA foreign_keys = ON")
        destination_connection.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
        source_store.connection.backup(destination_connection, pages=100, sleep=0.05)
        destination_connection.commit()
    finally:
        if destination_connection is not None:
            destination_connection.close()
        source_store.close()

    try:
        temporary.replace(destination)
    except Exception:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def backup_database(
    db_path: str | Path,
    backup_path: str | Path,
    *,
    pathway: str | Path | None = None,
) -> DatabaseReport:
    """Create and verify a consistent online backup without changing the source."""

    source = _existing_file(db_path)
    destination = Path(backup_path).expanduser()
    if source.resolve() == destination.resolve():
        raise ValueError("backup destination must differ from the source database")
    verify_database(source, pathway=pathway)
    _backup_file(source, destination)
    return verify_database(destination, pathway=pathway)


def restore_database(
    backup_path: str | Path,
    target_path: str | Path,
    *,
    pathway: str | Path | None = None,
) -> DatabaseReport:
    """Restore a verified backup into a new path and verify it before use."""

    backup = _existing_file(backup_path)
    target = Path(target_path).expanduser()
    if target.exists():
        raise FileExistsError(f"restore target already exists: {target}")
    backup_report = verify_database(backup, pathway=pathway)
    _backup_file(backup, target)
    restored = verify_database(target, pathway=pathway)
    expected = (
        "cases",
        "audit_events",
        "consents",
        "access_logs",
        "message_drafts",
        "notification_outbox",
    )
    if any(getattr(restored, name) != getattr(backup_report, name) for name in expected):
        target.unlink(missing_ok=True)
        raise DatabaseLifecycleError("restored database record counts do not match the backup")
    return replace(restored, path=str(target))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CareFlow SQLite lifecycle and recovery operations")
    subparsers = parser.add_subparsers(dest="command", required=True)

    migrate = subparsers.add_parser("migrate", help="apply ordered schema migrations and verify")
    migrate.add_argument("--db", default="careflow.db")
    migrate.add_argument("--pathway", default=None)

    verify = subparsers.add_parser("verify", help="verify schema, integrity, pathway, and case history")
    verify.add_argument("--db", required=True)
    verify.add_argument("--pathway", default=None)

    backup = subparsers.add_parser("backup", help="create a verified online backup")
    backup.add_argument("--db", required=True)
    backup.add_argument("--output", required=True)
    backup.add_argument("--pathway", default=None)

    restore = subparsers.add_parser("restore", help="restore and verify a backup to a new path")
    restore.add_argument("--backup", required=True)
    restore.add_argument("--target", required=True)
    restore.add_argument("--pathway", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "migrate":
            report = migrate_database(args.db, pathway=args.pathway)
        elif args.command == "verify":
            report = verify_database(args.db, pathway=args.pathway)
        elif args.command == "backup":
            report = backup_database(args.db, args.output, pathway=args.pathway)
        else:
            report = restore_database(args.backup, args.target, pathway=args.pathway)
    except (DatabaseLifecycleError, FileExistsError, OSError, sqlite3.Error, ValueError) as exc:
        print(json.dumps({"status": "failed", "error": type(exc).__name__}, sort_keys=True), file=sys.stderr)
        return 1
    print(json.dumps({"status": "ok", **asdict(report)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DatabaseReport",
    "backup_database",
    "main",
    "migrate_database",
    "restore_database",
    "verify_database",
]
