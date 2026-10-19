"""SQLite persistence for CareFlow's pathway state and operational records.

The engine remains the source of truth for transition rules.  ``SQLiteStore``
stores a validated ``Case`` snapshot and can rehydrate it for later API work,
while keeping operational records (users, consents, evidence, approvals,
message drafts and notifications) in separate tables.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
from typing import Any, Callable, Mapping
from uuid import uuid4

from careflow.engine import Case, Pathway


CURRENT_SCHEMA_VERSION = 2
SCHEMA_VERSION = CURRENT_SCHEMA_VERSION
SQLITE_BUSY_TIMEOUT_MS = 5_000
SQLITE_BUSY_TIMEOUT_SECONDS = SQLITE_BUSY_TIMEOUT_MS / 1_000


class DatabaseLifecycleError(RuntimeError):
    """Raised when a SQLite database cannot safely be used by this release."""


class UnsupportedSchemaError(DatabaseLifecycleError):
    """Raised when a database is newer than the application understands."""


class DatabaseIntegrityError(DatabaseLifecycleError):
    """Raised when SQLite or application-level integrity checks fail."""


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    role TEXT NOT NULL,
    email TEXT,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cases (
    id TEXT PRIMARY KEY,
    patient_user_id TEXT REFERENCES users(id),
    pathway TEXT NOT NULL,
    state TEXT NOT NULL,
    entered_at TEXT NOT NULL,
    owner TEXT,
    urgent INTEGER NOT NULL CHECK (urgent IN (0, 1)),
    closed INTEGER NOT NULL CHECK (closed IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS consents (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    case_id TEXT REFERENCES cases(id),
    consent_type TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'granted', 'withdrawn')),
    granted_at TEXT NOT NULL,
    expires_at TEXT,
    source TEXT NOT NULL,
    UNIQUE (user_id, case_id, consent_type)
);

CREATE TABLE IF NOT EXISTS evidence (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    recorded_by TEXT,
    details_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE (case_id, kind)
);

CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    action TEXT NOT NULL,
    role TEXT NOT NULL,
    approver_name TEXT,
    note TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL,
    occurred_at TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    from_state TEXT,
    to_state TEXT NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '[]',
    approval_id TEXT REFERENCES approvals(id),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE (case_id, sequence)
);

CREATE TABLE IF NOT EXISTS notification_outbox (
    id TEXT PRIMARY KEY,
    case_id TEXT REFERENCES cases(id) ON DELETE CASCADE,
    event TEXT NOT NULL,
    channel TEXT NOT NULL,
    recipient TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL CHECK (status IN ('queued', 'processing', 'sent', 'failed')),
    available_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    sent_at TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    dedupe_key TEXT UNIQUE
);

CREATE TABLE IF NOT EXISTS message_drafts (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    patient_user_id TEXT REFERENCES users(id),
    language TEXT NOT NULL,
    template TEXT NOT NULL,
    message TEXT NOT NULL,
    provider TEXT NOT NULL,
    review_required INTEGER NOT NULL CHECK (review_required IN (0, 1)),
    review_flag TEXT,
    status TEXT NOT NULL CHECK (status IN ('pending_approval', 'approved', 'rejected')),
    created_by TEXT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    approved_by TEXT REFERENCES users(id),
    approved_at TEXT,
    approval_note TEXT,
    notification_id TEXT
);

CREATE TABLE IF NOT EXISTS access_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id),
    case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    action TEXT NOT NULL CHECK (action IN ('read', 'mutation', 'worklist', 'metrics')),
    endpoint TEXT,
    occurred_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_cases_state ON cases(state);
CREATE INDEX IF NOT EXISTS idx_cases_owner ON cases(owner);
CREATE INDEX IF NOT EXISTS idx_audit_case ON audit_events(case_id, sequence);
CREATE INDEX IF NOT EXISTS idx_outbox_ready ON notification_outbox(status, available_at);
CREATE INDEX IF NOT EXISTS idx_message_drafts_case ON message_drafts(case_id, created_at, id);
CREATE INDEX IF NOT EXISTS idx_message_drafts_status ON message_drafts(status, created_at, id);
CREATE INDEX IF NOT EXISTS idx_access_case ON access_logs(case_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_access_user ON access_logs(user_id, occurred_at);
"""

MIGRATION_METADATA_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL
);
"""

MIGRATION_RUNTIME_METADATA = """
CREATE TABLE IF NOT EXISTS database_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
INSERT INTO database_metadata (key, value)
VALUES ('timestamp_convention', 'UTC-aware ISO-8601 values')
ON CONFLICT(key) DO UPDATE SET value = excluded.value;
INSERT INTO database_metadata (key, value)
VALUES ('topology', 'one-process-one-worker-local-filesystem')
ON CONFLICT(key) DO UPDATE SET value = excluded.value;
"""


@dataclass(frozen=True)
class _Migration:
    version: int
    name: str
    sql: str
    apply: Callable[[sqlite3.Connection], None]

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()


def _apply_baseline(connection: sqlite3.Connection) -> None:
    for statement in (SCHEMA + MIGRATION_METADATA_SCHEMA).split(";"):
        if statement.strip():
            connection.execute(statement)


def _apply_runtime_metadata(connection: sqlite3.Connection) -> None:
    for statement in MIGRATION_RUNTIME_METADATA.split(";"):
        if statement.strip():
            connection.execute(statement)


MIGRATIONS = (
    _Migration(1, "baseline-careflow-schema", SCHEMA + MIGRATION_METADATA_SCHEMA, _apply_baseline),
    _Migration(2, "runtime-lifecycle-metadata", MIGRATION_RUNTIME_METADATA, _apply_runtime_metadata),
)
MIGRATIONS_BY_VERSION = {migration.version: migration for migration in MIGRATIONS}
LEGACY_TABLES = frozenset(
    {
        "users",
        "cases",
        "consents",
        "evidence",
        "approvals",
        "audit_events",
        "notification_outbox",
        "message_drafts",
        "access_logs",
    }
)

USER_ROLES = frozenset(
    {
        "lab",
        "screening_program",
        "primary_care",
        "specialist",
        "care_coordinator",
        "clinician_on_call",
        "patient",
    }
)
ROLES = USER_ROLES


@dataclass(frozen=True)
class User:
    """A person or service account that can participate in a case."""

    id: str
    name: str
    role: str
    email: str | None = None
    is_active: bool = True
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.role not in USER_ROLES:
            raise ValueError(f"unknown user role: {self.role}")


@dataclass(frozen=True)
class Consent:
    """A patient's consent record, optionally scoped to one case."""

    id: str
    user_id: str
    consent_type: str
    status: str
    granted_at: datetime
    case_id: str | None = None
    expires_at: datetime | None = None
    source: str = "recorded"


@dataclass(frozen=True)
class EvidenceRecord:
    id: str
    case_id: str
    kind: str
    recorded_at: datetime
    recorded_by: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ApprovalRecord:
    id: str
    case_id: str
    action: str
    role: str
    note: str
    approver_name: str | None = None
    recorded_at: datetime | None = None


@dataclass(frozen=True)
class MessageDraftRecord:
    """Persisted patient-message draft awaiting human approval."""

    id: str
    case_id: str
    patient_user_id: str | None
    language: str
    template: str
    message: str
    provider: str
    review_required: bool
    review_flag: str | None
    status: str
    created_by: str
    created_at: datetime
    approved_by: str | None = None
    approved_at: datetime | None = None
    approval_note: str | None = None
    notification_id: str | None = None


@dataclass(frozen=True)
class AccessLog:
    """A successful authenticated access to a case or case collection."""

    id: int
    user_id: str
    case_id: str
    action: str
    occurred_at: datetime
    endpoint: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


def _datetime(value: datetime | str | None) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise TypeError("a datetime or ISO datetime string is required")
    return _instant(parsed)


def _instant(value: datetime) -> datetime:
    """Normalize naive and timezone-aware timestamps to UTC-aware values.

    Legacy databases stored naive values.  They are interpreted as UTC during
    upgrade/rehydration so ordering and expiry checks remain deterministic.
    """

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime | str | None) -> str:
    return _datetime(value).isoformat(timespec="microseconds")


def _optional_iso(value: datetime | str | None) -> str | None:
    return None if value is None else _iso(value)


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _from_json(value: str, default: Any) -> Any:
    if not value:
        return default
    return json.loads(value)


class SQLiteStore:
    """Small, synchronous SQLite repository used by the prototype and seed.

    A store owns one connection.  This keeps ``:memory:`` databases useful in
    tests and makes each public write atomic without adding an ORM dependency.
    """

    TABLES = {
        "schema_migrations",
        "database_metadata",
        "users",
        "cases",
        "consents",
        "evidence",
        "approvals",
        "audit_events",
        "notification_outbox",
        "message_drafts",
        "access_logs",
    }

    def __init__(self, path: str | Path = "careflow.db", *, auto_migrate: bool = True) -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self.startup_error: DatabaseLifecycleError | None = None
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        # FastAPI's synchronous TestClient and request handlers may run on a
        # worker thread different from the one that created the application.
        # The store still owns one connection and serializes writes with
        # SQLite transactions, so allowing that connection to cross the
        # request boundary keeps the existing :memory: fixture useful too.
        self.connection = sqlite3.connect(
            self.path,
            check_same_thread=False,
            timeout=SQLITE_BUSY_TIMEOUT_SECONDS,
        )
        self.connection.row_factory = sqlite3.Row
        try:
            self.connection.execute("PRAGMA foreign_keys = ON")
            self.connection.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
            self.connection.execute("PRAGMA synchronous = FULL")
        except sqlite3.DatabaseError as exc:
            self.startup_error = DatabaseLifecycleError(f"database connection is unreadable: {exc}")
            return
        if auto_migrate:
            try:
                self.initialize()
            except DatabaseLifecycleError as exc:
                self.startup_error = exc
            except sqlite3.DatabaseError as exc:
                self.startup_error = DatabaseLifecycleError(f"database initialization failed: {exc}")

    def initialize(self) -> None:
        """Create or upgrade the schema through ordered, forward-only migrations."""

        with self._lock:
            self._run_migrations()
            self._validate_schema_contract()

    @property
    def schema_version(self) -> int:
        row = self.connection.execute("PRAGMA user_version").fetchone()
        return int(row[0]) if row is not None else 0

    def migration_status(self) -> dict[str, int | bool]:
        """Return safe schema revision information for operators and probes."""

        return {
            "version": self.schema_version,
            "supported_version": CURRENT_SCHEMA_VERSION,
            "current": self.schema_version == CURRENT_SCHEMA_VERSION,
        }

    def _table_names(self) -> set[str]:
        rows = self.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        return {str(row[0]) for row in rows}

    def _validate_schema_contract(self) -> None:
        version = self.schema_version
        if version > CURRENT_SCHEMA_VERSION:
            raise UnsupportedSchemaError(
                f"database schema version {version} is newer than supported version {CURRENT_SCHEMA_VERSION}"
            )
        if version < CURRENT_SCHEMA_VERSION:
            raise DatabaseLifecycleError(
                f"database schema version {version} is behind supported version {CURRENT_SCHEMA_VERSION}"
            )
        missing = (LEGACY_TABLES | {"schema_migrations", "database_metadata"}) - self._table_names()
        if missing:
            raise DatabaseLifecycleError("database schema is missing required tables")
        rows = self.connection.execute(
            "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
        ).fetchall()
        if [int(row[0]) for row in rows] != sorted(MIGRATIONS_BY_VERSION):
            raise DatabaseLifecycleError("database migration history is incomplete")
        for row in rows:
            migration = MIGRATIONS_BY_VERSION[int(row[0])]
            if row[1] != migration.name or row[2] != migration.checksum:
                raise DatabaseLifecycleError("database migration history does not match this release")

    def _record_migration(self, migration: _Migration) -> None:
        self.connection.execute(
            """
            INSERT INTO schema_migrations (version, name, checksum, applied_at)
            VALUES (?, ?, ?, ?)
            """,
            (migration.version, migration.name, migration.checksum, _iso(datetime.now(timezone.utc))),
        )

    def _legacy_baseline_present(self) -> bool:
        tables = self._table_names()
        return LEGACY_TABLES.issubset(tables) and "schema_migrations" not in tables

    def _run_migrations(self) -> None:
        version = self.schema_version
        tables = self._table_names()
        if version > CURRENT_SCHEMA_VERSION:
            raise UnsupportedSchemaError(
                f"database schema version {version} is newer than supported version {CURRENT_SCHEMA_VERSION}"
            )

        if version == 0 and not tables:
            pass
        elif version == 0 and self._legacy_baseline_present():
            migration = MIGRATIONS_BY_VERSION[1]
            with self.connection:
                self.connection.execute("BEGIN IMMEDIATE")
                self.connection.execute(MIGRATION_METADATA_SCHEMA.split(";")[0])
                self._record_migration(migration)
                self.connection.execute("PRAGMA user_version = 1")
            version = 1
        elif version == 0 and "schema_migrations" in tables:
            raise DatabaseLifecycleError("database migration appears partially applied")
        elif version == 0:
            raise DatabaseLifecycleError("database contains an unrecognized or partial schema")
        else:
            rows = self.connection.execute(
                "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
            ).fetchall()
            recorded = [int(row[0]) for row in rows]
            if recorded != list(range(1, version + 1)):
                raise DatabaseLifecycleError("database migration history is incomplete")
            for row in rows:
                migration = MIGRATIONS_BY_VERSION.get(int(row[0]))
                if migration is None or row[1] != migration.name or row[2] != migration.checksum:
                    raise DatabaseLifecycleError("database migration history does not match this release")

        for migration in MIGRATIONS:
            if migration.version <= version:
                continue
            with self.connection:
                self.connection.execute("BEGIN IMMEDIATE")
                migration.apply(self.connection)
                self._record_migration(migration)
                self.connection.execute(f"PRAGMA user_version = {migration.version}")
            version = migration.version

        self._validate_schema_contract()

    def verify_integrity(self) -> None:
        """Run SQLite integrity and foreign-key checks without changing data."""

        if self.startup_error is not None:
            raise self.startup_error
        integrity = self.connection.execute("PRAGMA integrity_check").fetchone()
        if integrity is None or integrity[0] != "ok":
            raise DatabaseIntegrityError("database integrity check failed")
        foreign_keys = self.connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_keys:
            raise DatabaseIntegrityError("database foreign-key check failed")

    def check_writable_path(self) -> None:
        """Verify the database file and its parent can accept a write probe."""

        if self.path == ":memory:":
            return
        database_path = Path(self.path).expanduser()
        parent = database_path.resolve().parent
        if not parent.exists() or not os.access(parent, os.W_OK):
            raise DatabaseLifecycleError("database directory is not writable")
        if database_path.exists() and not os.access(database_path, os.W_OK):
            raise DatabaseLifecycleError("database file is not writable")
        probe = parent / f".careflow-write-probe-{uuid4().hex}"
        try:
            with probe.open("xb"):
                pass
        except OSError as exc:
            raise DatabaseLifecycleError("database directory is not writable") from exc
        finally:
            try:
                probe.unlink()
            except FileNotFoundError:
                pass

    def verify_readiness(self) -> None:
        """Fail closed unless schema, storage, SQLite, and app-level data are ready."""

        if self.startup_error is not None:
            raise self.startup_error
        self._validate_schema_contract()
        self.verify_integrity()
        self.check_writable_path()

    def verify_cases(self, pathway: Pathway) -> None:
        """Validate every persisted case against its pathway and audit contract."""

        self.verify_readiness()
        for record in self.list_case_records():
            self.load_case(str(record["id"]), pathway)

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "SQLiteStore":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    def clear(self) -> None:
        """Delete all records, intended for deterministic fixture generation."""

        with self.connection:
            for table in (
                "access_logs",
                "message_drafts",
                "notification_outbox",
                "audit_events",
                "approvals",
                "evidence",
                "consents",
                "cases",
                "users",
            ):
                self.connection.execute(f"DELETE FROM {table}")

    def count(self, table: str) -> int:
        if table not in self.TABLES:
            raise ValueError(f"unknown table: {table}")
        row = self.connection.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
        return int(row["count"])

    def add_user(self, user: User) -> None:
        created_at = user.created_at or datetime.now(timezone.utc)
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO users (id, name, role, email, is_active, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    role = excluded.role,
                    email = excluded.email,
                    is_active = excluded.is_active
                """,
                (user.id, user.name, user.role, user.email, int(user.is_active), _iso(created_at)),
            )

    def get_user(self, user_id: str) -> User | None:
        row = self.connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            return None
        return User(
            id=row["id"],
            name=row["name"],
            role=row["role"],
            email=row["email"],
            is_active=bool(row["is_active"]),
            created_at=_datetime(row["created_at"]),
        )

    def list_users(self, *, role: str | None = None, active_only: bool = False) -> list[User]:
        query, args = "SELECT * FROM users", []
        clauses = []
        if role is not None:
            clauses.append("role = ?")
            args.append(role)
        if active_only:
            clauses.append("is_active = 1")
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY id"
        rows = self.connection.execute(query, args).fetchall()
        return [
            User(
                id=row["id"],
                name=row["name"],
                role=row["role"],
                email=row["email"],
                is_active=bool(row["is_active"]),
                created_at=_datetime(row["created_at"]),
            )
            for row in rows
        ]

    def add_consent(self, consent: Consent) -> None:
        if consent.status not in {"pending", "granted", "withdrawn"}:
            raise ValueError("consent status must be pending, granted, or withdrawn")
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO consents
                    (id, user_id, case_id, consent_type, status, granted_at, expires_at, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    user_id = excluded.user_id,
                    case_id = excluded.case_id,
                    consent_type = excluded.consent_type,
                    status = excluded.status,
                    granted_at = excluded.granted_at,
                    expires_at = excluded.expires_at,
                    source = excluded.source
                """,
                (
                    consent.id,
                    consent.user_id,
                    consent.case_id,
                    consent.consent_type,
                    consent.status,
                    _iso(consent.granted_at),
                    _optional_iso(consent.expires_at),
                    consent.source,
                ),
            )

    def list_consents(self, *, user_id: str | None = None, case_id: str | None = None) -> list[Consent]:
        query, args = "SELECT * FROM consents", []
        clauses = []
        if user_id is not None:
            clauses.append("user_id = ?")
            args.append(user_id)
        if case_id is not None:
            clauses.append("case_id = ?")
            args.append(case_id)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY id"
        rows = self.connection.execute(query, args).fetchall()
        return [
            Consent(
                id=row["id"],
                user_id=row["user_id"],
                case_id=row["case_id"],
                consent_type=row["consent_type"],
                status=row["status"],
                granted_at=_datetime(row["granted_at"]),
                expires_at=_datetime(row["expires_at"]) if row["expires_at"] else None,
                source=row["source"],
            )
            for row in rows
        ]

    def record_evidence(
        self,
        case_id: str,
        kind: str,
        *,
        evidence_id: str | None = None,
        recorded_at: datetime | str | None = None,
        recorded_by: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> str:
        evidence_id = evidence_id or f"{case_id}:{kind}"
        recorded_at = recorded_at or datetime.now(timezone.utc)
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO evidence (id, case_id, kind, recorded_at, recorded_by, details_json)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    case_id = excluded.case_id,
                    kind = excluded.kind,
                    recorded_at = excluded.recorded_at,
                    recorded_by = excluded.recorded_by,
                    details_json = excluded.details_json
                """,
                (evidence_id, case_id, kind, _iso(recorded_at), recorded_by, _json(details or {})),
            )
        return evidence_id

    def list_evidence(self, case_id: str) -> list[EvidenceRecord]:
        rows = self.connection.execute(
            "SELECT * FROM evidence WHERE case_id = ? ORDER BY recorded_at, id", (case_id,)
        ).fetchall()
        return [
            EvidenceRecord(
                id=row["id"],
                case_id=row["case_id"],
                kind=row["kind"],
                recorded_at=_datetime(row["recorded_at"]),
                recorded_by=row["recorded_by"],
                details=_from_json(row["details_json"], {}),
            )
            for row in rows
        ]

    def add_approval(
        self,
        case_id: str,
        action: str,
        role: str,
        note: str,
        *,
        approval_id: str | None = None,
        approver_name: str | None = None,
        recorded_at: datetime | str | None = None,
    ) -> str:
        if not note:
            raise ValueError("an approval note is required")
        recorded_at = recorded_at or datetime.now(timezone.utc)
        approval_id = approval_id or f"{case_id}:approval:{action}:{_iso(recorded_at)}"
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO approvals
                    (id, case_id, action, role, approver_name, note, recorded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    case_id = excluded.case_id,
                    action = excluded.action,
                    role = excluded.role,
                    approver_name = excluded.approver_name,
                    note = excluded.note,
                    recorded_at = excluded.recorded_at
                """,
                (approval_id, case_id, action, role, approver_name, note, _iso(recorded_at)),
            )
        return approval_id

    def list_approvals(self, case_id: str) -> list[ApprovalRecord]:
        rows = self.connection.execute(
            "SELECT * FROM approvals WHERE case_id = ? ORDER BY recorded_at, id", (case_id,)
        ).fetchall()
        return [
            ApprovalRecord(
                id=row["id"],
                case_id=row["case_id"],
                action=row["action"],
                role=row["role"],
                note=row["note"],
                approver_name=row["approver_name"],
                recorded_at=_datetime(row["recorded_at"]),
            )
            for row in rows
        ]

    def add_message_draft(
        self,
        *,
        case_id: str,
        patient_user_id: str | None,
        language: str,
        template: str,
        message: str,
        provider: str,
        review_required: bool,
        review_flag: str | None,
        created_by: str,
        created_at: datetime | str,
        draft_id: str | None = None,
    ) -> str:
        """Persist a generated draft without touching the case snapshot."""

        if self.get_case_record(case_id) is None:
            raise KeyError(f"case not found: {case_id}")
        if not message.strip():
            raise ValueError("a message draft must not be blank")
        draft_id = draft_id or f"message-draft:{uuid4().hex}"
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO message_drafts
                    (id, case_id, patient_user_id, language, template, message, provider,
                     review_required, review_flag, status, created_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending_approval', ?, ?)
                """,
                (
                    draft_id,
                    case_id,
                    patient_user_id,
                    language,
                    template,
                    message,
                    provider,
                    int(review_required),
                    review_flag,
                    created_by,
                    _iso(created_at),
                ),
            )
        return draft_id

    def get_message_draft(self, draft_id: str) -> MessageDraftRecord | None:
        row = self.connection.execute(
            "SELECT * FROM message_drafts WHERE id = ?", (draft_id,)
        ).fetchone()
        return None if row is None else self._message_draft_row(row)

    def list_message_drafts(
        self,
        *,
        case_id: str | None = None,
        status: str | None = None,
    ) -> list[MessageDraftRecord]:
        query, args = "SELECT * FROM message_drafts", []
        clauses = []
        if case_id is not None:
            clauses.append("case_id = ?")
            args.append(case_id)
        if status is not None:
            status = status.strip().lower().replace(" ", "_")
            if status not in {"pending_approval", "approved", "rejected"}:
                raise ValueError("unknown message draft status")
            clauses.append("status = ?")
            args.append(status)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at, id"
        rows = self.connection.execute(query, args).fetchall()
        return [self._message_draft_row(row) for row in rows]

    def approve_message_draft(
        self,
        draft_id: str,
        *,
        approved_by: str,
        approved_at: datetime | str,
        approval_note: str,
        recipient: str | None,
        payload: Mapping[str, Any] | None = None,
        consent_user_id: str | None = None,
        channel: str = "internal",
    ) -> tuple[MessageDraftRecord, dict[str, Any]]:
        """Approve one draft and queue one outbox row atomically.

        Consent is checked immediately before the outbox insert and the check
        lives in the same store operation as the draft status update.  No
        delivery worker is invoked here; the resulting row remains ``queued``.
        """

        draft = self.get_message_draft(draft_id)
        if draft is None:
            raise KeyError(f"message draft not found: {draft_id}")
        if draft.status != "pending_approval":
            raise ValueError(f"message draft is already {draft.status}")
        if not approval_note.strip():
            raise ValueError("an approval note is required")
        approved_at = _datetime(approved_at)
        if not self.has_valid_consent(
            draft.case_id,
            user_id=consent_user_id or draft.patient_user_id,
            now=approved_at,
        ):
            raise ValueError("case notification requires a granted, unexpired case/user consent")

        notification_id = f"notification:patient_message:{draft.case_id}:{draft.id}"
        notification_payload = dict(payload or {})
        notification_payload.setdefault("draft_id", draft.id)
        notification_payload.setdefault("message", draft.message)
        notification_payload.setdefault("language", draft.language)
        notification_payload.setdefault("review_flag", draft.review_flag)
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO notification_outbox
                    (id, case_id, event, channel, recipient, payload_json, status,
                     available_at, created_at, dedupe_key)
                VALUES (?, ?, 'patient_message', ?, ?, ?, 'queued', ?, ?, ?)
                """,
                (
                    notification_id,
                    draft.case_id,
                    channel,
                    recipient,
                    _json(notification_payload),
                    _iso(approved_at),
                    _iso(approved_at),
                    f"message-draft:{draft.id}",
                ),
            )
            self.connection.execute(
                """
                UPDATE message_drafts
                SET status = 'approved', approved_by = ?, approved_at = ?, approval_note = ?, notification_id = ?
                WHERE id = ? AND status = 'pending_approval'
                """,
                (approved_by, _iso(approved_at), approval_note, notification_id, draft.id),
            )

        updated = self.get_message_draft(draft.id)
        assert updated is not None
        row = self.connection.execute(
            "SELECT * FROM notification_outbox WHERE id = ?", (notification_id,)
        ).fetchone()
        assert row is not None
        return updated, self._outbox_row(row)

    def save_case(self, case: Case, *, patient_user_id: str | None = None) -> None:
        """Persist a case and its complete audit snapshot atomically."""

        if case.state not in case.pw.states and case.state not in case.pw.terminal:
            raise ValueError(f"unknown case state: {case.state}")
        if not case.audit:
            raise ValueError("a case must contain at least its opening audit event")
        if case.closed and case.owner is not None:
            raise ValueError("a closed case cannot have an owner")
        if not case.closed and not case.owner:
            raise ValueError("an open case must always have an owner")

        created_at = _datetime(case.audit[0]["ts"])
        updated_at = _datetime(case.audit[-1]["ts"])
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO cases
                    (id, patient_user_id, pathway, state, entered_at, owner, urgent, closed,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    patient_user_id = COALESCE(excluded.patient_user_id, cases.patient_user_id),
                    pathway = excluded.pathway,
                    state = excluded.state,
                    entered_at = excluded.entered_at,
                    owner = excluded.owner,
                    urgent = excluded.urgent,
                    closed = excluded.closed,
                    created_at = excluded.created_at,
                    updated_at = excluded.updated_at
                """,
                (
                    case.id,
                    patient_user_id,
                    case.pw.name,
                    case.state,
                    _iso(case.entered),
                    case.owner,
                    int(case.urgent),
                    int(case.closed),
                    _iso(created_at),
                    _iso(updated_at),
                ),
            )

            self.connection.execute(
                "DELETE FROM audit_events WHERE case_id = ? AND sequence >= ?",
                (case.id, len(case.audit)),
            )
            for sequence, event in enumerate(case.audit):
                approval_id = self._save_audit_approval(case.id, sequence, event)
                evidence = list(event.get("evidence", []))
                metadata = {
                    key: value
                    for key, value in event.items()
                    if key not in {"ts", "actor", "action", "from", "to", "evidence"}
                }
                metadata["_has_evidence"] = "evidence" in event
                self.connection.execute(
                    """
                    INSERT INTO audit_events
                        (case_id, sequence, occurred_at, actor, action, from_state, to_state,
                         evidence_json, approval_id, metadata_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(case_id, sequence) DO UPDATE SET
                        occurred_at = excluded.occurred_at,
                        actor = excluded.actor,
                        action = excluded.action,
                        from_state = excluded.from_state,
                        to_state = excluded.to_state,
                        evidence_json = excluded.evidence_json,
                        approval_id = excluded.approval_id,
                        metadata_json = excluded.metadata_json
                    """,
                    (
                        case.id,
                        sequence,
                        _iso(event["ts"]),
                        event["actor"],
                        event["action"],
                        event.get("from"),
                        event["to"],
                        _json(evidence),
                        approval_id,
                        _json(metadata),
                    ),
                )

            provenance = self._evidence_provenance(case)
            for kind in sorted(case.evidence):
                recorded_at, recorded_by = provenance.get(kind, (created_at, "system"))
                self.connection.execute(
                    """
                    INSERT INTO evidence (id, case_id, kind, recorded_at, recorded_by, details_json)
                    VALUES (?, ?, ?, ?, ?, '{}')
                    ON CONFLICT(id) DO UPDATE SET
                        case_id = excluded.case_id,
                        kind = excluded.kind,
                        recorded_at = excluded.recorded_at,
                        recorded_by = excluded.recorded_by
                    """,
                    (f"{case.id}:{kind}", case.id, kind, _iso(recorded_at), recorded_by),
                )

    def _save_audit_approval(self, case_id: str, sequence: int, event: Mapping[str, Any]) -> str | None:
        approval = event.get("approval")
        if not isinstance(approval, Mapping):
            return None
        role = str(approval.get("role", ""))
        note = str(approval.get("note", ""))
        approval_id = f"{case_id}:approval:{sequence}"
        self.connection.execute(
            """
            INSERT INTO approvals (id, case_id, action, role, approver_name, note, recorded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                action = excluded.action,
                role = excluded.role,
                approver_name = excluded.approver_name,
                note = excluded.note,
                recorded_at = excluded.recorded_at
            """,
            (
                approval_id,
                case_id,
                str(event["action"]),
                role,
                approval.get("name"),
                note,
                _iso(event["ts"]),
            ),
        )
        return approval_id

    @staticmethod
    def _evidence_provenance(case: Case) -> dict[str, tuple[datetime, str]]:
        result: dict[str, tuple[datetime, str]] = {}
        for event in case.audit:
            for kind in event.get("evidence", []):
                result.setdefault(kind, (_datetime(event["ts"]), str(event["actor"])))
        return result

    def load_case(self, case_id: str, pathway: Pathway) -> Case:
        """Rehydrate a case without replaying transitions or bypassing rules."""

        if self.startup_error is not None:
            raise self.startup_error
        row = self.connection.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
        if row is None:
            raise KeyError(f"case not found: {case_id}")
        if row["pathway"] != pathway.name:
            raise ValueError(f"persisted case belongs to pathway {row['pathway']!r}, not {pathway.name!r}")
        case = Case(row["id"], pathway, _datetime(row["created_at"]), urgent=bool(row["urgent"]))
        case.state = row["state"]
        case.entered = _datetime(row["entered_at"])
        case.owner = row["owner"]

        if case.state not in pathway.states and case.state not in pathway.terminal:
            raise ValueError(f"persisted case has unknown state: {case.state}")
        if case.closed and case.owner is not None:
            raise ValueError("persisted closed case has an owner")
        if not case.closed and not case.owner:
            raise ValueError("persisted open case has no owner")
        if bool(row["closed"]) != case.closed:
            raise ValueError("persisted case closed flag does not match its state")

        events = self.connection.execute(
            "SELECT * FROM audit_events WHERE case_id = ? ORDER BY sequence", (case_id,)
        ).fetchall()
        if not events:
            raise ValueError("persisted case has no audit history")
        sequences = [int(event_row["sequence"]) for event_row in events]
        if sequences != list(range(len(sequences))):
            raise ValueError("persisted case audit sequence is not contiguous")
        case.audit = []
        for event_row in events:
            event: dict[str, Any] = {
                "ts": _datetime(event_row["occurred_at"]),
                "actor": event_row["actor"],
                "action": event_row["action"],
                "from": event_row["from_state"],
                "to": event_row["to_state"],
            }
            metadata = _from_json(event_row["metadata_json"], {})
            has_evidence = bool(metadata.pop("_has_evidence", False))
            if has_evidence:
                event["evidence"] = _from_json(event_row["evidence_json"], [])
            event.update(metadata)
            case.audit.append(event)

        evidence_rows = self.connection.execute(
            "SELECT kind FROM evidence WHERE case_id = ? ORDER BY kind", (case_id,)
        ).fetchall()
        case.evidence = {row["kind"] for row in evidence_rows}
        return case

    def get_case_record(self, case_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
        return None if row is None else dict(row)

    def list_case_records(self) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT * FROM cases ORDER BY id").fetchall()
        return [dict(row) for row in rows]

    def has_valid_consent(
        self,
        case_id: str,
        *,
        user_id: str | None = None,
        now: datetime | str | None = None,
    ) -> bool:
        """Return whether the case's user has a currently granted consent.

        Case notifications are authorized by the patient consent attached to the
        case.  A caller may provide ``user_id`` for repositories that need to
        check another explicitly scoped case user; the default is the case's
        ``patient_user_id``.
        """

        record = self.get_case_record(case_id)
        if record is None:
            return False
        consent_user_id = user_id or record.get("patient_user_id")
        if not consent_user_id:
            return False
        check_at = _datetime(now) if now is not None else datetime.now(timezone.utc)
        for consent in self.list_consents(user_id=consent_user_id, case_id=case_id):
            if consent.status != "granted":
                continue
            if _instant(consent.granted_at) > _instant(check_at):
                continue
            if consent.expires_at is not None and _instant(consent.expires_at) <= _instant(check_at):
                continue
            return True
        return False

    def record_access(
        self,
        *,
        user_id: str,
        case_id: str,
        action: str,
        occurred_at: datetime | str | None = None,
        endpoint: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> int:
        """Record a successful authenticated case access."""

        if action not in {"read", "mutation", "worklist", "metrics"}:
            raise ValueError("unknown access-log action")
        occurred_at = occurred_at or datetime.now(timezone.utc)
        with self.connection:
            cursor = self.connection.execute(
                """
                INSERT INTO access_logs
                    (user_id, case_id, action, endpoint, occurred_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (user_id, case_id, action, endpoint, _iso(occurred_at), _json(metadata or {})),
            )
        assert cursor.lastrowid is not None
        return int(cursor.lastrowid)

    def list_access_logs(
        self,
        *,
        user_id: str | None = None,
        case_id: str | None = None,
        action: str | None = None,
    ) -> list[AccessLog]:
        query, args = "SELECT * FROM access_logs", []
        clauses = []
        if user_id is not None:
            clauses.append("user_id = ?")
            args.append(user_id)
        if case_id is not None:
            clauses.append("case_id = ?")
            args.append(case_id)
        if action is not None:
            clauses.append("action = ?")
            args.append(action)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY id"
        rows = self.connection.execute(query, args).fetchall()
        return [
            AccessLog(
                id=int(row["id"]),
                user_id=row["user_id"],
                case_id=row["case_id"],
                action=row["action"],
                endpoint=row["endpoint"],
                occurred_at=_datetime(row["occurred_at"]),
                metadata=_from_json(row["metadata_json"], {}),
            )
            for row in rows
        ]

    def enqueue_notification(
        self,
        *,
        event: str,
        case_id: str | None = None,
        channel: str = "internal",
        recipient: str | None = None,
        payload: Mapping[str, Any] | None = None,
        notification_id: str | None = None,
        available_at: datetime | str | None = None,
        created_at: datetime | str | None = None,
        dedupe_key: str | None = None,
        consent_user_id: str | None = None,
    ) -> str:
        now = _datetime(created_at) if created_at is not None else datetime.now(timezone.utc)
        available_at = available_at or now
        if case_id is not None and not self.has_valid_consent(
            case_id,
            user_id=consent_user_id,
            now=now,
        ):
            raise ValueError("case notification requires a granted, unexpired case/user consent")
        notification_id = notification_id or f"notification:{event}:{case_id or 'global'}:{_iso(now)}"
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO notification_outbox
                    (id, case_id, event, channel, recipient, payload_json, status,
                     available_at, created_at, dedupe_key)
                VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    case_id = excluded.case_id,
                    event = excluded.event,
                    channel = excluded.channel,
                    recipient = excluded.recipient,
                    payload_json = excluded.payload_json,
                    available_at = excluded.available_at,
                    dedupe_key = excluded.dedupe_key
                """,
                (
                    notification_id,
                    case_id,
                    event,
                    channel,
                    recipient,
                    _json(payload or {}),
                    _iso(available_at),
                    _iso(now),
                    dedupe_key,
                ),
            )
        return notification_id

    def pending_notifications(self, *, now: datetime | str | None = None) -> list[dict[str, Any]]:
        now = now or datetime.now(timezone.utc)
        rows = self.connection.execute(
            """
            SELECT * FROM notification_outbox
            WHERE status = 'queued' AND available_at <= ?
            ORDER BY created_at, id
            """,
            (_iso(now),),
        ).fetchall()
        return [self._outbox_row(row) for row in rows]

    def mark_notification_sent(
        self, notification_id: str, *, sent_at: datetime | str | None = None
    ) -> None:
        sent_at = sent_at or datetime.now(timezone.utc)
        with self.connection:
            self.connection.execute(
                """
                UPDATE notification_outbox
                SET status = 'sent', sent_at = ?, attempts = attempts + 1
                WHERE id = ?
                """,
                (_iso(sent_at), notification_id),
            )

    def list_outbox(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM notification_outbox ORDER BY created_at, id"
        ).fetchall()
        return [self._outbox_row(row) for row in rows]

    @staticmethod
    def _message_draft_row(row: sqlite3.Row) -> MessageDraftRecord:
        return MessageDraftRecord(
            id=row["id"],
            case_id=row["case_id"],
            patient_user_id=row["patient_user_id"],
            language=row["language"],
            template=row["template"],
            message=row["message"],
            provider=row["provider"],
            review_required=bool(row["review_required"]),
            review_flag=row["review_flag"],
            status=row["status"],
            created_by=row["created_by"],
            created_at=_datetime(row["created_at"]),
            approved_by=row["approved_by"],
            approved_at=_datetime(row["approved_at"]) if row["approved_at"] else None,
            approval_note=row["approval_note"],
            notification_id=row["notification_id"],
        )

    @staticmethod
    def _outbox_row(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["payload"] = _from_json(result.pop("payload_json"), {})
        return result


# ``Database`` is a useful compatibility spelling for later API modules.
Database = SQLiteStore
