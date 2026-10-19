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
LEGACY_PHASE3_SCHEMA_VERSION = 3
SCHEMA_VERSION = CURRENT_SCHEMA_VERSION
SQLITE_BUSY_TIMEOUT_MS = 5_000
SQLITE_BUSY_TIMEOUT_SECONDS = SQLITE_BUSY_TIMEOUT_MS / 1_000


class DatabaseLifecycleError(RuntimeError):
    """Raised when a SQLite database cannot safely be used by this release."""


class UnsupportedSchemaError(DatabaseLifecycleError):
    """Raised when a database is newer than the application understands."""


class DatabaseIntegrityError(DatabaseLifecycleError):
    """Raised when SQLite or application-level integrity checks fail."""


class ConcurrencyConflict(RuntimeError):
    """Raised when a compare-and-swap write lost to a newer case version."""


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
    status TEXT NOT NULL CHECK (status IN ('generated', 'pending_approval', 'approved', 'queued', 'failed', 'unavailable', 'rejected')),
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


MIGRATION_IDENTITY_SESSION_SCHEMA = """
CREATE TABLE IF NOT EXISTS oidc_identity_links (
    id TEXT PRIMARY KEY,
    issuer TEXT NOT NULL,
    subject TEXT NOT NULL,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    approved INTEGER NOT NULL DEFAULT 1 CHECK (approved IN (0, 1)),
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_oidc_identity_lookup
    ON oidc_identity_links(issuer, subject);
CREATE INDEX IF NOT EXISTS idx_oidc_identity_user
    ON oidc_identity_links(user_id);

CREATE TABLE IF NOT EXISTS oidc_login_states (
    state TEXT PRIMARY KEY,
    nonce TEXT NOT NULL,
    code_verifier TEXT NOT NULL,
    redirect_uri TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    consumed_at TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    handle_hash TEXT NOT NULL UNIQUE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    csrf_token_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    idle_expires_at TEXT NOT NULL,
    absolute_expires_at TEXT NOT NULL,
    revoked_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions(idle_expires_at, absolute_expires_at);
"""

MIGRATION_AUTHZ_INTEGRITY_SCHEMA = """
ALTER TABLE cases ADD COLUMN version INTEGER NOT NULL DEFAULT 0;
ALTER TABLE evidence ADD COLUMN recorded_role TEXT;
ALTER TABLE evidence ADD COLUMN state TEXT;
ALTER TABLE evidence ADD COLUMN action TEXT;
ALTER TABLE evidence ADD COLUMN provenance_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE approvals ADD COLUMN principal_id TEXT;
ALTER TABLE approvals ADD COLUMN state TEXT;
ALTER TABLE approvals ADD COLUMN provenance_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE notification_outbox ADD COLUMN provenance_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE message_drafts ADD COLUMN created_role TEXT;
ALTER TABLE message_drafts ADD COLUMN created_state TEXT;
ALTER TABLE message_drafts ADD COLUMN created_provenance_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE message_drafts ADD COLUMN approved_role TEXT;
ALTER TABLE message_drafts ADD COLUMN approved_principal_id TEXT;
ALTER TABLE message_drafts ADD COLUMN approved_state TEXT;
ALTER TABLE message_drafts ADD COLUMN approved_provenance_json TEXT NOT NULL DEFAULT '{}';

CREATE TABLE IF NOT EXISTS consent_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    consent_id TEXT NOT NULL,
    user_id TEXT NOT NULL REFERENCES users(id),
    case_id TEXT REFERENCES cases(id),
    consent_type TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'granted', 'withdrawn')),
    occurred_at TEXT NOT NULL,
    expires_at TEXT,
    source TEXT NOT NULL,
    principal_id TEXT,
    provenance_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_consent_history_case ON consent_history(case_id, occurred_at, id);
CREATE INDEX IF NOT EXISTS idx_consent_history_user ON consent_history(user_id, occurred_at, id);
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


def _apply_sql(connection: sqlite3.Connection, sql: str) -> None:
    for statement in sql.split(";"):
        if statement.strip():
            connection.execute(statement)


def _apply_authz_integrity(connection: sqlite3.Connection) -> None:
    """Add Phase 4 bindings while preserving every existing operational row."""

    column_additions = {
        "cases": (("version", "INTEGER NOT NULL DEFAULT 0"),),
        "evidence": (
            ("recorded_role", "TEXT"),
            ("state", "TEXT"),
            ("action", "TEXT"),
            ("provenance_json", "TEXT NOT NULL DEFAULT '{}'"),
        ),
        "approvals": (
            ("principal_id", "TEXT"),
            ("state", "TEXT"),
            ("provenance_json", "TEXT NOT NULL DEFAULT '{}'"),
        ),
        "notification_outbox": (("provenance_json", "TEXT NOT NULL DEFAULT '{}'"),),
        "message_drafts": (
            ("created_role", "TEXT"),
            ("created_state", "TEXT"),
            ("created_provenance_json", "TEXT NOT NULL DEFAULT '{}'"),
            ("approved_role", "TEXT"),
            ("approved_principal_id", "TEXT"),
            ("approved_state", "TEXT"),
            ("approved_provenance_json", "TEXT NOT NULL DEFAULT '{}'"),
        ),
    }
    for table, additions in column_additions.items():
        existing_columns = {
            str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        for column, definition in additions:
            if column not in existing_columns:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    connection.execute("DROP INDEX IF EXISTS idx_outbox_ready")
    connection.execute("ALTER TABLE notification_outbox RENAME TO notification_outbox_legacy")
    connection.execute(
        """
        CREATE TABLE notification_outbox (
            id TEXT PRIMARY KEY,
            case_id TEXT REFERENCES cases(id) ON DELETE CASCADE,
            event TEXT NOT NULL,
            channel TEXT NOT NULL,
            recipient TEXT,
            payload_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL CHECK (status IN ('queued', 'processing', 'sent', 'failed', 'unavailable')),
            available_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            sent_at TEXT,
            attempts INTEGER NOT NULL DEFAULT 0,
            dedupe_key TEXT UNIQUE,
            provenance_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    connection.execute(
        """
        INSERT INTO notification_outbox
            (id, case_id, event, channel, recipient, payload_json, status,
             available_at, created_at, sent_at, attempts, dedupe_key, provenance_json)
        SELECT id, case_id, event, channel, recipient, payload_json, status,
               available_at, created_at, sent_at, attempts, dedupe_key, provenance_json
        FROM notification_outbox_legacy
        """
    )
    connection.execute("DROP TABLE notification_outbox_legacy")
    connection.execute("CREATE INDEX idx_outbox_ready ON notification_outbox(status, available_at)")

    connection.execute("DROP INDEX IF EXISTS idx_message_drafts_case")
    connection.execute("DROP INDEX IF EXISTS idx_message_drafts_status")
    connection.execute("ALTER TABLE message_drafts RENAME TO message_drafts_legacy")
    connection.execute(
        """
        CREATE TABLE message_drafts (
            id TEXT PRIMARY KEY,
            case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
            patient_user_id TEXT REFERENCES users(id),
            language TEXT NOT NULL,
            template TEXT NOT NULL,
            message TEXT NOT NULL,
            provider TEXT NOT NULL,
            review_required INTEGER NOT NULL CHECK (review_required IN (0, 1)),
            review_flag TEXT,
            status TEXT NOT NULL CHECK (status IN ('generated', 'pending_approval', 'approved', 'queued', 'failed', 'unavailable', 'rejected')),
            created_by TEXT NOT NULL REFERENCES users(id),
            created_at TEXT NOT NULL,
            approved_by TEXT REFERENCES users(id),
            approved_at TEXT,
            approval_note TEXT,
            notification_id TEXT,
            created_role TEXT,
            created_state TEXT,
            created_provenance_json TEXT NOT NULL DEFAULT '{}',
            approved_role TEXT,
            approved_principal_id TEXT,
            approved_state TEXT,
            approved_provenance_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    connection.execute(
        """
        INSERT INTO message_drafts
            (id, case_id, patient_user_id, language, template, message, provider,
             review_required, review_flag, status, created_by, created_at, approved_by,
             approved_at, approval_note, notification_id, created_role, created_state,
             created_provenance_json, approved_role, approved_principal_id, approved_state,
             approved_provenance_json)
        SELECT id, case_id, patient_user_id, language, template, message, provider,
               review_required, review_flag, status, created_by, created_at, approved_by,
               approved_at, approval_note, notification_id, created_role, created_state,
               created_provenance_json, approved_role, approved_principal_id, approved_state,
               approved_provenance_json
        FROM message_drafts_legacy
        """
    )
    connection.execute("DROP TABLE message_drafts_legacy")
    connection.execute("CREATE INDEX idx_message_drafts_case ON message_drafts(case_id, created_at, id)")
    connection.execute("CREATE INDEX idx_message_drafts_status ON message_drafts(status, created_at, id)")
    _apply_sql(connection, """
        CREATE TABLE IF NOT EXISTS consent_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            consent_id TEXT NOT NULL,
            user_id TEXT NOT NULL REFERENCES users(id),
            case_id TEXT REFERENCES cases(id),
            consent_type TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('pending', 'granted', 'withdrawn')),
            occurred_at TEXT NOT NULL,
            expires_at TEXT,
            source TEXT NOT NULL,
            principal_id TEXT,
            provenance_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_consent_history_case ON consent_history(case_id, occurred_at, id);
        CREATE INDEX IF NOT EXISTS idx_consent_history_user ON consent_history(user_id, occurred_at, id);
    """)


MIGRATIONS = (
    _Migration(1, "baseline-careflow-schema", SCHEMA + MIGRATION_METADATA_SCHEMA, _apply_baseline),
    _Migration(
        2,
        "runtime-lifecycle-metadata",
        MIGRATION_RUNTIME_METADATA + MIGRATION_IDENTITY_SESSION_SCHEMA + MIGRATION_AUTHZ_INTEGRITY_SCHEMA,
        lambda connection: (
            _apply_runtime_metadata(connection),
            _apply_sql(connection, MIGRATION_IDENTITY_SESSION_SCHEMA),
            _apply_authz_integrity(connection),
        ),
    ),
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

IDENTITY_SESSION_TABLES = frozenset(
    {"oidc_identity_links", "oidc_login_states", "sessions"}
)

AUTHZ_INTEGRITY_TABLES = frozenset({"consent_history"})

USER_ROLES = frozenset(
    {
        "lab",
        "screening_program",
        "primary_care",
        "specialist",
        "care_coordinator",
        "clinician_on_call",
        "clinical_supervisor",
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
    certified_languages: tuple[str, ...] = ("en",)

    def __post_init__(self) -> None:
        if self.role and self.role not in USER_ROLES:
            raise ValueError(f"unknown user role: {self.role}")


@dataclass(frozen=True)
class OIDCIdentityLink:
    """An approved immutable external identity to local-user mapping."""

    id: str
    issuer: str
    subject: str
    user_id: str
    approved: bool = True
    created_at: datetime | None = None


@dataclass(frozen=True)
class OIDCLoginState:
    """A short-lived server-side authorization-code transaction."""

    state: str
    nonce: str
    code_verifier: str
    redirect_uri: str
    created_at: datetime
    expires_at: datetime
    consumed_at: datetime | None = None


@dataclass(frozen=True)
class SessionRecord:
    """Server-side session metadata; the browser only receives its handle."""

    id: str
    handle_hash: str
    user_id: str
    csrf_token_hash: str
    created_at: datetime
    last_seen_at: datetime
    idle_expires_at: datetime
    absolute_expires_at: datetime
    revoked_at: datetime | None = None


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
    principal_id: str | None = None
    provenance: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EvidenceRecord:
    id: str
    case_id: str
    kind: str
    recorded_at: datetime
    recorded_by: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)
    recorded_role: str | None = None
    state: str | None = None
    action: str | None = None
    provenance: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ApprovalRecord:
    id: str
    case_id: str
    action: str
    role: str
    note: str
    approver_name: str | None = None
    recorded_at: datetime | None = None
    principal_id: str | None = None
    state: str | None = None
    provenance: Mapping[str, Any] = field(default_factory=dict)


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
    created_role: str | None = None
    created_state: str | None = None
    created_provenance: Mapping[str, Any] = field(default_factory=dict)
    approved_role: str | None = None
    approved_principal_id: str | None = None
    approved_state: str | None = None
    approved_provenance: Mapping[str, Any] = field(default_factory=dict)


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


def _secret_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


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
        "oidc_identity_links",
        "oidc_login_states",
        "sessions",
        "consent_history",
    }

    def __init__(self, path: str | Path = "careflow.db", *, auto_migrate: bool = True) -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self.startup_error: DatabaseLifecycleError | None = None
        self._user_certified_languages: dict[str, tuple[str, ...]] = {}
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
            if self.path != ":memory:":
                self.connection.execute("PRAGMA journal_mode = WAL")
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
            self.connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_cases_worklist_active ON cases(closed, owner, urgent, entered_at)"
            )
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
            "current": self.schema_version == CURRENT_SCHEMA_VERSION or self._is_legacy_phase3_schema(),
        }

    def _table_names(self) -> set[str]:
        rows = self.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        return {str(row[0]) for row in rows}

    def _has_authz_columns(self) -> bool:
        case_rows = self.connection.execute("PRAGMA table_info(cases)").fetchall()
        evidence_rows = self.connection.execute("PRAGMA table_info(evidence)").fetchall()
        return "version" in {str(row[1]) for row in case_rows} and "action" in {
            str(row[1]) for row in evidence_rows
        }

    def _is_legacy_phase3_schema(self) -> bool:
        """Recognize the prior Phase 3 identity/session revision in-place."""

        if self.schema_version != LEGACY_PHASE3_SCHEMA_VERSION:
            return False
        rows = self.connection.execute(
            "SELECT version, name FROM schema_migrations ORDER BY version"
        ).fetchall()
        return (
            [int(row[0]) for row in rows] == [1, 2, 3]
            and str(rows[-1][1]) == "oidc-identity-and-server-sessions"
            and IDENTITY_SESSION_TABLES.issubset(self._table_names())
        )

    def _validate_schema_contract(self) -> None:
        version = self.schema_version
        legacy_phase3 = self._is_legacy_phase3_schema()
        if version > CURRENT_SCHEMA_VERSION and not legacy_phase3:
            raise UnsupportedSchemaError(
                f"database schema version {version} is newer than supported version {CURRENT_SCHEMA_VERSION}"
            )
        if version < CURRENT_SCHEMA_VERSION:
            raise DatabaseLifecycleError(
                f"database schema version {version} is behind supported version {CURRENT_SCHEMA_VERSION}"
            )
        missing = (
            LEGACY_TABLES
            | IDENTITY_SESSION_TABLES
            | AUTHZ_INTEGRITY_TABLES
            | {"schema_migrations", "database_metadata"}
        ) - self._table_names()
        if missing:
            raise DatabaseLifecycleError("database schema is missing required tables")
        rows = self.connection.execute(
            "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
        ).fetchall()
        expected_versions = [1, 2, 3] if legacy_phase3 else sorted(MIGRATIONS_BY_VERSION)
        if [int(row[0]) for row in rows] != expected_versions:
            raise DatabaseLifecycleError("database migration history is incomplete")
        for row in rows:
            if legacy_phase3:
                continue
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
        if version == LEGACY_PHASE3_SCHEMA_VERSION and self._is_legacy_phase3_schema():
            if not self._has_authz_columns():
                with self.connection:
                    self.connection.execute("BEGIN IMMEDIATE")
                    _apply_authz_integrity(self.connection)
            return
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
                if migration is None or row[1] != migration.name:
                    if self._is_legacy_phase3_schema() and int(row[0]) == 3:
                        continue
                    raise DatabaseLifecycleError("database migration history does not match this release")
                if self._is_legacy_phase3_schema() and int(row[0]) == 2:
                    continue
                # Phase 2 databases already at revision 2 used the runtime
                # metadata checksum before identity/session tables were added.
                # Upgrade that known shape in place without rewriting records.
                if int(row[0]) == 2 and row[2] != migration.checksum and (
                    not IDENTITY_SESSION_TABLES.issubset(tables) or not self._has_authz_columns()
                ):
                    continue
                if row[2] != migration.checksum:
                    raise DatabaseLifecycleError("database migration history does not match this release")

            if version == 2 and (
                not IDENTITY_SESSION_TABLES.issubset(tables) or not self._has_authz_columns()
            ):
                migration = MIGRATIONS_BY_VERSION[2]
                with self.connection:
                    self.connection.execute("BEGIN IMMEDIATE")
                    if not IDENTITY_SESSION_TABLES.issubset(tables):
                        _apply_sql(self.connection, MIGRATION_IDENTITY_SESSION_SCHEMA)
                    if not self._has_authz_columns():
                        _apply_authz_integrity(self.connection)
                    self.connection.execute(
                        "UPDATE schema_migrations SET checksum = ? WHERE version = 2",
                        (migration.checksum,),
                    )
                tables = self._table_names()

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
                "oidc_login_states",
                "sessions",
                "oidc_identity_links",
                "audit_events",
                "approvals",
                "evidence",
                "consents",
                "consent_history",
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
        if getattr(user, "certified_languages", None):
            self._user_certified_languages[user.id] = tuple(user.certified_languages)
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
            certified_languages=self._user_certified_languages.get(user_id, ("en",)),
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
                certified_languages=self._user_certified_languages.get(row["id"], ("en",)),
            )
            for row in rows
        ]

    def add_identity_link(self, link: OIDCIdentityLink) -> None:
        """Approve one exact ``(issuer, subject)`` mapping to a local user."""

        if not link.issuer.strip() or not link.subject.strip() or not link.user_id.strip():
            raise ValueError("identity issuer, subject, and user_id are required")
        if self.get_user(link.user_id) is None:
            raise KeyError(f"user not found: {link.user_id}")
        created_at = link.created_at or datetime.now(timezone.utc)
        with self._lock, self.connection:
            self.connection.execute(
                """
                INSERT INTO oidc_identity_links
                    (id, issuer, subject, user_id, approved, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    issuer = excluded.issuer,
                    subject = excluded.subject,
                    user_id = excluded.user_id,
                    approved = excluded.approved
                """,
                (
                    link.id,
                    link.issuer.strip(),
                    link.subject.strip(),
                    link.user_id.strip(),
                    int(link.approved),
                    _iso(created_at),
                ),
            )

    def link_identity(
        self,
        issuer: str,
        subject: str,
        user_id: str,
        *,
        approved: bool = True,
        link_id: str | None = None,
        created_at: datetime | str | None = None,
    ) -> str:
        """Convenience API for provisioning an approved OIDC identity link."""

        identity_id = link_id or f"oidc:{_secret_hash(issuer + chr(0) + subject)[:32]}"
        self.add_identity_link(
            OIDCIdentityLink(
                id=identity_id,
                issuer=issuer,
                subject=subject,
                user_id=user_id,
                approved=approved,
                created_at=_datetime(created_at) if created_at is not None else None,
            )
        )
        return identity_id

    def resolve_identity(self, issuer: str, subject: str) -> User | None:
        """Return a uniquely approved local user for an external principal."""

        rows = self.connection.execute(
            """
            SELECT user_id FROM oidc_identity_links
            WHERE issuer = ? AND subject = ? AND approved = 1
            ORDER BY id
            """,
            (issuer, subject),
        ).fetchall()
        if len(rows) != 1:
            return None
        return self.get_user(str(rows[0]["user_id"]))

    def list_identity_links(
        self,
        *,
        issuer: str | None = None,
        subject: str | None = None,
        user_id: str | None = None,
    ) -> list[OIDCIdentityLink]:
        clauses: list[str] = []
        args: list[str] = []
        for name, value in (("issuer", issuer), ("subject", subject), ("user_id", user_id)):
            if value is not None:
                clauses.append(f"{name} = ?")
                args.append(value)
        query = "SELECT * FROM oidc_identity_links"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY id"
        rows = self.connection.execute(query, args).fetchall()
        return [
            OIDCIdentityLink(
                id=row["id"],
                issuer=row["issuer"],
                subject=row["subject"],
                user_id=row["user_id"],
                approved=bool(row["approved"]),
                created_at=_datetime(row["created_at"]),
            )
            for row in rows
        ]

    def create_oidc_login_state(
        self,
        *,
        state: str,
        nonce: str,
        code_verifier: str,
        redirect_uri: str,
        created_at: datetime | str,
        expires_at: datetime | str,
    ) -> OIDCLoginState:
        record = OIDCLoginState(
            state=state,
            nonce=nonce,
            code_verifier=code_verifier,
            redirect_uri=redirect_uri,
            created_at=_datetime(created_at),
            expires_at=_datetime(expires_at),
        )
        with self._lock, self.connection:
            self.connection.execute(
                """
                INSERT INTO oidc_login_states
                    (state, nonce, code_verifier, redirect_uri, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    record.state,
                    record.nonce,
                    record.code_verifier,
                    record.redirect_uri,
                    _iso(record.created_at),
                    _iso(record.expires_at),
                ),
            )
        return record

    def consume_oidc_login_state(
        self, state: str, *, consumed_at: datetime | str
    ) -> OIDCLoginState | None:
        """Atomically consume a state value so callback replay is rejected."""

        timestamp = _datetime(consumed_at)
        with self._lock, self.connection:
            row = self.connection.execute(
                "SELECT * FROM oidc_login_states WHERE state = ? AND consumed_at IS NULL",
                (state,),
            ).fetchone()
            if row is None:
                return None
            updated = self.connection.execute(
                """
                UPDATE oidc_login_states SET consumed_at = ?
                WHERE state = ? AND consumed_at IS NULL
                """,
                (_iso(timestamp), state),
            )
            if updated.rowcount != 1:
                return None
        return OIDCLoginState(
            state=row["state"],
            nonce=row["nonce"],
            code_verifier=row["code_verifier"],
            redirect_uri=row["redirect_uri"],
            created_at=_datetime(row["created_at"]),
            expires_at=_datetime(row["expires_at"]),
            consumed_at=timestamp,
        )

    def create_session(
        self,
        *,
        user_id: str,
        created_at: datetime | str,
        idle_expires_at: datetime | str,
        absolute_expires_at: datetime | str,
        handle: str | None = None,
        csrf_token: str | None = None,
        revoke_existing: bool = True,
    ) -> tuple[str, str, SessionRecord]:
        """Create a high-entropy opaque session and return its raw handle once."""

        if self.get_user(user_id) is None:
            raise KeyError(f"user not found: {user_id}")
        handle = handle or uuid4().hex + uuid4().hex
        csrf_token = csrf_token or uuid4().hex + uuid4().hex
        record = SessionRecord(
            id=f"session:{uuid4().hex}",
            handle_hash=_secret_hash(handle),
            user_id=user_id,
            csrf_token_hash=_secret_hash(csrf_token),
            created_at=_datetime(created_at),
            last_seen_at=_datetime(created_at),
            idle_expires_at=_datetime(idle_expires_at),
            absolute_expires_at=_datetime(absolute_expires_at),
        )
        with self._lock, self.connection:
            if revoke_existing:
                self.connection.execute(
                    "UPDATE sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
                    (_iso(record.created_at), user_id),
                )
            self.connection.execute(
                """
                INSERT INTO sessions
                    (id, handle_hash, user_id, csrf_token_hash, created_at, last_seen_at,
                     idle_expires_at, absolute_expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.handle_hash,
                    record.user_id,
                    record.csrf_token_hash,
                    _iso(record.created_at),
                    _iso(record.last_seen_at),
                    _iso(record.idle_expires_at),
                    _iso(record.absolute_expires_at),
                ),
            )
        return handle, csrf_token, record

    def get_session(self, handle: str) -> SessionRecord | None:
        row = self.connection.execute(
            "SELECT * FROM sessions WHERE handle_hash = ?", (_secret_hash(handle),)
        ).fetchone()
        if row is None:
            return None
        return SessionRecord(
            id=row["id"],
            handle_hash=row["handle_hash"],
            user_id=row["user_id"],
            csrf_token_hash=row["csrf_token_hash"],
            created_at=_datetime(row["created_at"]),
            last_seen_at=_datetime(row["last_seen_at"]),
            idle_expires_at=_datetime(row["idle_expires_at"]),
            absolute_expires_at=_datetime(row["absolute_expires_at"]),
            revoked_at=_datetime(row["revoked_at"]) if row["revoked_at"] else None,
        )

    def touch_session(
        self,
        session_id: str,
        *,
        last_seen_at: datetime | str,
        idle_expires_at: datetime | str,
    ) -> None:
        with self._lock, self.connection:
            self.connection.execute(
                """
                UPDATE sessions SET last_seen_at = ?, idle_expires_at = ?
                WHERE id = ? AND revoked_at IS NULL
                """,
                (_iso(last_seen_at), _iso(idle_expires_at), session_id),
            )

    def revoke_session(self, handle: str, *, revoked_at: datetime | str) -> bool:
        with self._lock, self.connection:
            updated = self.connection.execute(
                """
                UPDATE sessions SET revoked_at = ?
                WHERE handle_hash = ? AND revoked_at IS NULL
                """,
                (_iso(revoked_at), _secret_hash(handle)),
            )
        return updated.rowcount == 1

    def revoke_user_sessions(self, user_id: str, *, revoked_at: datetime | str) -> int:
        with self._lock, self.connection:
            updated = self.connection.execute(
                """
                UPDATE sessions SET revoked_at = ?
                WHERE user_id = ? AND revoked_at IS NULL
                """,
                (_iso(revoked_at), user_id),
            )
        return int(updated.rowcount)

    def add_consent(self, consent: Consent) -> None:
        """Record the current consent and append an immutable history event."""

        if consent.status not in {"pending", "granted", "withdrawn"}:
            raise ValueError("consent status must be pending, granted, or withdrawn")
        if self.get_user(consent.user_id) is None:
            raise KeyError(f"user not found: {consent.user_id}")
        occurred_at = consent.granted_at
        with self._lock, self.connection:
            existing = self.connection.execute(
                """
                SELECT id FROM consents
                WHERE user_id = ? AND case_id IS ? AND consent_type = ?
                """,
                (consent.user_id, consent.case_id, consent.consent_type),
            ).fetchone()
            consent_id = str(existing["id"]) if existing is not None else consent.id
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
                    consent_id,
                    consent.user_id,
                    consent.case_id,
                    consent.consent_type,
                    consent.status,
                    _iso(consent.granted_at),
                    _optional_iso(consent.expires_at),
                    consent.source,
                ),
            )
            self.connection.execute(
                """
                INSERT INTO consent_history
                    (consent_id, user_id, case_id, consent_type, status, occurred_at,
                     expires_at, source, principal_id, provenance_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    consent_id,
                    consent.user_id,
                    consent.case_id,
                    consent.consent_type,
                    consent.status,
                    _iso(occurred_at),
                    _optional_iso(consent.expires_at),
                    consent.source,
                    consent.principal_id,
                    _json(consent.provenance),
                ),
            )
            if consent.status == "withdrawn" and consent.case_id:
                self.connection.execute(
                    """
                    UPDATE notification_outbox
                    SET status = 'unavailable',
                        provenance_json = ?
                    WHERE case_id = ? AND status IN ('queued', 'processing')
                    """,
                    (_json({"reason": "consent_withdrawn", "withdrawn_at": _iso(occurred_at)}), consent.case_id),
                )
                self.connection.execute(
                    """
                    UPDATE message_drafts
                    SET status = 'unavailable'
                    WHERE case_id = ? AND status IN ('approved', 'queued')
                    """,
                    (consent.case_id,),
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
                principal_id=None,
                provenance={},
            )
            for row in rows
        ]

    def list_consent_history(
        self, *, user_id: str | None = None, case_id: str | None = None
    ) -> list[Consent]:
        """Return append-only grant/withdraw/expiry decisions in order."""

        query, args = "SELECT * FROM consent_history", []
        clauses = []
        if user_id is not None:
            clauses.append("user_id = ?")
            args.append(user_id)
        if case_id is not None:
            clauses.append("case_id = ?")
            args.append(case_id)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY occurred_at, id"
        rows = self.connection.execute(query, args).fetchall()
        return [
            Consent(
                id=row["consent_id"],
                user_id=row["user_id"],
                case_id=row["case_id"],
                consent_type=row["consent_type"],
                status=row["status"],
                granted_at=_datetime(row["occurred_at"]),
                expires_at=_datetime(row["expires_at"]) if row["expires_at"] else None,
                source=row["source"],
                principal_id=row["principal_id"],
                provenance=_from_json(row["provenance_json"], {}),
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
        recorded_role: str | None = None,
        state: str | None = None,
        action: str | None = None,
        provenance: Mapping[str, Any] | None = None,
    ) -> str:
        evidence_id = evidence_id or f"{case_id}:{kind}"
        recorded_at = recorded_at or datetime.now(timezone.utc)
        with self._lock, self.connection:
            existing = self.connection.execute(
                "SELECT case_id, kind, recorded_by, recorded_role, state, action, provenance_json, details_json FROM evidence WHERE id = ?",
                (evidence_id,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["case_id"] != case_id
                    or existing["kind"] != kind
                    or existing["recorded_by"] != recorded_by
                    or existing["action"] != action
                ):
                    raise ValueError("evidence provenance is immutable")
                return evidence_id
            self.connection.execute(
                """
                INSERT INTO evidence
                    (id, case_id, kind, recorded_at, recorded_by, recorded_role, state,
                     action, provenance_json, details_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evidence_id,
                    case_id,
                    kind,
                    _iso(recorded_at),
                    recorded_by,
                    recorded_role,
                    state,
                    action,
                    _json(provenance or {}),
                    _json(details or {}),
                ),
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
                recorded_role=row["recorded_role"],
                state=row["state"],
                action=row["action"],
                provenance=_from_json(row["provenance_json"], {}),
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
        principal_id: str | None = None,
        state: str | None = None,
        provenance: Mapping[str, Any] | None = None,
    ) -> str:
        if not note:
            raise ValueError("an approval note is required")
        recorded_at = recorded_at or datetime.now(timezone.utc)
        approval_id = approval_id or f"{case_id}:approval:{action}:{_iso(recorded_at)}"
        with self._lock, self.connection:
            existing = self.connection.execute(
                "SELECT case_id, action, role, principal_id, note FROM approvals WHERE id = ?",
                (approval_id,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["case_id"] != case_id
                    or existing["action"] != action
                    or existing["role"] != role
                    or existing["principal_id"] != principal_id
                    or existing["note"] != note
                ):
                    raise ValueError("approval provenance is immutable")
                return approval_id
            self.connection.execute(
                """
                INSERT INTO approvals
                    (id, case_id, action, role, approver_name, note, recorded_at,
                     principal_id, state, provenance_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval_id,
                    case_id,
                    action,
                    role,
                    approver_name,
                    note,
                    _iso(recorded_at),
                    principal_id,
                    state,
                    _json(provenance or {}),
                ),
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
                principal_id=row["principal_id"],
                state=row["state"],
                provenance=_from_json(row["provenance_json"], {}),
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
        created_role: str | None = None,
        created_state: str | None = None,
        created_provenance: Mapping[str, Any] | None = None,
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
                     review_required, review_flag, status, created_by, created_at,
                     created_role, created_state, created_provenance_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending_approval', ?, ?, ?, ?, ?)
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
                    created_role,
                    created_state,
                    _json(created_provenance or {}),
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
            if status not in {"generated", "pending_approval", "approved", "queued", "failed", "unavailable", "rejected"}:
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
        approved_role: str | None = None,
        approved_state: str | None = None,
        approved_provenance: Mapping[str, Any] | None = None,
    ) -> tuple[MessageDraftRecord, dict[str, Any]]:
        """Approve one draft and queue one outbox row atomically.

        Consent is checked immediately before the outbox insert and the check
        lives in the same store operation as the draft status update.  No
        delivery worker is invoked here; the resulting row remains ``queued``.
        """

        if not approval_note.strip():
            raise ValueError("an approval note is required")
        approved_at = _datetime(approved_at)
        with self._lock, self.connection:
            row = self.connection.execute(
                "SELECT * FROM message_drafts WHERE id = ?", (draft_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"message draft not found: {draft_id}")
            draft = self._message_draft_row(row)
            notification_id = f"notification:patient_message:{draft.case_id}:{draft.id}"
            if draft.status in {"approved", "queued"}:
                existing = self.connection.execute(
                    "SELECT * FROM notification_outbox WHERE id = ?", (notification_id,)
                ).fetchone()
                if existing is not None:
                    return draft, self._outbox_row(existing)
            if draft.status == "unavailable":
                raise ValueError("message draft is unavailable after consent withdrawal")
            if draft.status != "pending_approval":
                raise ValueError(f"message draft is already {draft.status}")
            if not self.has_valid_consent(
                draft.case_id,
                user_id=consent_user_id or draft.patient_user_id,
                now=approved_at,
            ):
                raise ValueError("case notification requires a granted, unexpired case/user consent")

            notification_payload = dict(payload or {})
            notification_payload.setdefault("draft_id", draft.id)
            notification_payload.setdefault("message", draft.message)
            notification_payload.setdefault("language", draft.language)
            notification_payload.setdefault("review_flag", draft.review_flag)
            provenance = dict(approved_provenance or {})
            provenance.setdefault("principal_id", approved_by)
            provenance.setdefault("role", approved_role)
            provenance.setdefault("state", approved_state)
            self.connection.execute(
                """
                INSERT INTO notification_outbox
                    (id, case_id, event, channel, recipient, payload_json, status,
                     available_at, created_at, dedupe_key, provenance_json)
                VALUES (?, ?, 'patient_message', ?, ?, ?, 'queued', ?, ?, ?, ?)
                ON CONFLICT(id) DO NOTHING
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
                    _json(provenance),
                ),
            )
            updated_count = self.connection.execute(
                """
                UPDATE message_drafts
                SET status = 'approved', approved_by = ?, approved_at = ?, approval_note = ?,
                    notification_id = ?, approved_role = ?, approved_principal_id = ?,
                    approved_state = ?, approved_provenance_json = ?
                WHERE id = ? AND status = 'pending_approval'
                """,
                (
                    approved_by,
                    _iso(approved_at),
                    approval_note,
                    notification_id,
                    approved_role,
                    approved_by,
                    approved_state,
                    _json(provenance),
                    draft.id,
                ),
            ).rowcount
            if updated_count != 1:
                current = self.connection.execute(
                    "SELECT * FROM message_drafts WHERE id = ?", (draft.id,)
                ).fetchone()
                if current is None or current["status"] not in {"approved", "queued"}:
                    raise ValueError("message draft approval conflict; retry with the current draft")
                draft = self._message_draft_row(current)
            else:
                draft_row = self.connection.execute(
                    "SELECT * FROM message_drafts WHERE id = ?", (draft.id,)
                ).fetchone()
                assert draft_row is not None
                draft = self._message_draft_row(draft_row)
            row = self.connection.execute(
                "SELECT * FROM notification_outbox WHERE id = ?", (notification_id,)
            ).fetchone()
            assert row is not None
            return draft, self._outbox_row(row)

    def save_case(
        self,
        case: Case,
        *,
        patient_user_id: str | None = None,
        expected_version: int | None = None,
    ) -> None:
        """Persist a case with an optimistic compare-and-swap boundary.

        Existing audit/evidence/approval provenance is append-only.  A stale
        aggregate raises :class:`ConcurrencyConflict` before any row is changed.
        """

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
        new_version: int
        with self._lock, self.connection:
            current = self.connection.execute(
                "SELECT version, patient_user_id FROM cases WHERE id = ?", (case.id,)
            ).fetchone()
            if current is None:
                if expected_version not in (None, 0) or case.version not in (0,):
                    raise ConcurrencyConflict(f"case {case.id} was created by another request")
                new_version = 1
                self.connection.execute(
                """
                INSERT INTO cases
                    (id, patient_user_id, pathway, state, entered_at, owner, urgent, closed,
                      created_at, updated_at, version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    new_version,
                ),
            )
                existing_count = 0
            else:
                current_version = int(current["version"])
                compare_version = case.version if expected_version is None else expected_version
                if current_version != compare_version:
                    raise ConcurrencyConflict(
                        f"case {case.id} changed from version {compare_version} to {current_version}; reload and retry"
                    )
                existing_count = int(
                    self.connection.execute(
                        "SELECT COUNT(*) FROM audit_events WHERE case_id = ?", (case.id,)
                    ).fetchone()[0]
                )
                if existing_count > len(case.audit):
                    raise ConcurrencyConflict(f"case {case.id} has newer audit history")
                new_version = current_version + 1
                updated = self.connection.execute(
                    """
                    UPDATE cases
                    SET patient_user_id = COALESCE(?, patient_user_id), pathway = ?, state = ?,
                        entered_at = ?, owner = ?, urgent = ?, closed = ?, created_at = ?,
                        updated_at = ?, version = version + 1
                    WHERE id = ? AND version = ?
                    """,
                    (
                        patient_user_id,
                        case.pw.name,
                        case.state,
                        _iso(case.entered),
                        case.owner,
                        int(case.urgent),
                        int(case.closed),
                        _iso(created_at),
                        _iso(updated_at),
                        case.id,
                        current_version,
                    ),
                )
                if updated.rowcount != 1:
                    raise ConcurrencyConflict(f"case {case.id} changed during save; retry with a fresh case")

            for sequence, event in enumerate(case.audit[existing_count:], existing_count):
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
                    ON CONFLICT(case_id, sequence) DO NOTHING
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
                recorded_at, recorded_by, recorded_role, state, evidence_metadata = provenance.get(
                    kind, (created_at, "system", None, None, {})
                )
                self.connection.execute(
                    """
                    INSERT INTO evidence
                        (id, case_id, kind, recorded_at, recorded_by, recorded_role, state,
                         action, provenance_json, details_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '{}')
                    ON CONFLICT(id) DO NOTHING
                    """,
                    (
                        f"{case.id}:{kind}",
                        case.id,
                        kind,
                        _iso(recorded_at),
                        recorded_by,
                        recorded_role,
                        state,
                        evidence_metadata.get("action"),
                        _json(evidence_metadata),
                    ),
                )
        case.version = new_version

    def _save_audit_approval(self, case_id: str, sequence: int, event: Mapping[str, Any]) -> str | None:
        approval = event.get("approval")
        if not isinstance(approval, Mapping):
            return None
        role = str(approval.get("role", ""))
        note = str(approval.get("note", ""))
        approval_id = f"{case_id}:approval:{sequence}"
        approval_provenance = dict(approval.get("provenance") or {})
        principal_id = str(approval.get("principal_id") or event.get("actor"))
        state = str(approval.get("state") or event.get("from") or "")
        self.connection.execute(
            """
            INSERT INTO approvals
                (id, case_id, action, role, approver_name, note, recorded_at,
                 principal_id, state, provenance_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO NOTHING
            """,
            (
                approval_id,
                case_id,
                str(event["action"]),
                role,
                approval.get("name"),
                note,
                _iso(event["ts"]),
                principal_id,
                state,
                _json(approval_provenance),
            ),
        )
        return approval_id

    @staticmethod
    def _evidence_provenance(case: Case) -> dict[str, tuple[datetime, str, str | None, str | None, Mapping[str, Any]]]:
        result: dict[str, tuple[datetime, str, str | None, str | None, Mapping[str, Any]]] = {}
        for event in case.audit:
            for kind in event.get("evidence", []):
                details = next(
                    (
                        item
                        for item in event.get("evidence_provenance", [])
                        if isinstance(item, Mapping) and item.get("kind") == kind
                    ),
                    {},
                )
                result.setdefault(
                    kind,
                    (
                        _datetime(event["ts"]),
                        str(details.get("principal_id") or event["actor"]),
                        details.get("role") or event.get("actor_role"),
                        details.get("state") or event.get("from"),
                        {
                            **dict(details.get("provenance") or {}),
                            "action": details.get("action") or "transition",
                        },
                    ),
                )
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
        case.version = int(row["version"])

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

    def get_worklist_cases(
        self,
        *,
        owner: str | None = None,
        urgent: bool | None = None,
        state: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Query open cases directly via indexed SQL query with pagination."""
        query = "SELECT * FROM cases WHERE closed = 0"
        params: list[Any] = []
        if owner is not None:
            query += " AND owner = ?"
            params.append(owner)
        if urgent is not None:
            query += " AND urgent = ?"
            params.append(1 if urgent else 0)
        if state is not None:
            query += " AND state = ?"
            params.append(state)
        query += " ORDER BY urgent DESC, entered_at ASC LIMIT ? OFFSET ?"
        params.extend([max(1, min(limit, 200)), max(0, offset)])
        rows = self.connection.execute(query, tuple(params)).fetchall()
        return [dict(row) for row in rows]

    def count_worklist_cases(
        self,
        *,
        owner: str | None = None,
        urgent: bool | None = None,
        state: str | None = None,
    ) -> int:
        """Count active open cases in the indexed SQL worklist."""
        query = "SELECT COUNT(*) FROM cases WHERE closed = 0"
        params: list[Any] = []
        if owner is not None:
            query += " AND owner = ?"
            params.append(owner)
        if urgent is not None:
            query += " AND urgent = ?"
            params.append(1 if urgent else 0)
        if state is not None:
            query += " AND state = ?"
            params.append(state)
        row = self.connection.execute(query, tuple(params)).fetchone()
        return int(row[0]) if row else 0

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
                self._record_consent_expiry(consent, check_at)
                continue
            return True
        return False

    def _record_consent_expiry(self, consent: Consent, occurred_at: datetime) -> None:
        """Retain the first observed expiry without changing the current grant row."""

        with self._lock, self.connection:
            rows = self.connection.execute(
                "SELECT provenance_json FROM consent_history WHERE consent_id = ?",
                (consent.id,),
            ).fetchall()
            if any(_from_json(row["provenance_json"], {}).get("event") == "expired" for row in rows):
                return
            self.connection.execute(
                """
                INSERT INTO consent_history
                    (consent_id, user_id, case_id, consent_type, status, occurred_at,
                     expires_at, source, principal_id, provenance_json)
                VALUES (?, ?, ?, ?, 'withdrawn', ?, ?, 'consent_expiry', NULL, ?)
                """,
                (
                    consent.id,
                    consent.user_id,
                    consent.case_id,
                    consent.consent_type,
                    _iso(consent.expires_at or occurred_at),
                    _optional_iso(consent.expires_at),
                    _json({"event": "expired", "expired_at": _iso(consent.expires_at or occurred_at)}),
                ),
            )

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
        provenance: Mapping[str, Any] | None = None,
    ) -> str:
        now = _datetime(created_at) if created_at is not None else datetime.now(timezone.utc)
        available_at = available_at or now
        notification_id = notification_id or f"notification:{event}:{case_id or 'global'}:{_iso(now)}"
        with self._lock, self.connection:
            if case_id is not None and not self.has_valid_consent(
                case_id,
                user_id=consent_user_id,
                now=now,
            ):
                raise ValueError("case notification requires a granted, unexpired case/user consent")
            existing = self.connection.execute(
                "SELECT * FROM notification_outbox WHERE id = ?", (notification_id,)
            ).fetchone()
            if existing is not None:
                return notification_id
            if dedupe_key:
                duplicate = self.connection.execute(
                    "SELECT id FROM notification_outbox WHERE dedupe_key = ?", (dedupe_key,)
                ).fetchone()
                if duplicate is not None:
                    return str(duplicate["id"])
            self.connection.execute(
                """
                INSERT INTO notification_outbox
                    (id, case_id, event, channel, recipient, payload_json, status,
                     available_at, created_at, dedupe_key, provenance_json)
                VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?)
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
                    _json(provenance or {}),
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
            created_role=row["created_role"],
            created_state=row["created_state"],
            created_provenance=_from_json(row["created_provenance_json"], {}),
            approved_role=row["approved_role"],
            approved_principal_id=row["approved_principal_id"],
            approved_state=row["approved_state"],
            approved_provenance=_from_json(row["approved_provenance_json"], {}),
        )

    @staticmethod
    def _outbox_row(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["payload"] = _from_json(result.pop("payload_json"), {})
        result["provenance"] = _from_json(result.get("provenance_json", "{}"), {})
        return result


# ``Database`` is a useful compatibility spelling for later API modules.
Database = SQLiteStore
