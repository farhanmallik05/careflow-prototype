"""SQLite persistence for CareFlow's pathway state and operational records.

The engine remains the source of truth for transition rules.  ``SQLiteStore``
stores a validated ``Case`` snapshot and can rehydrate it for later API work,
while keeping operational records (users, consents, evidence, approvals and
notifications) in separate tables.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Mapping

from careflow.engine import Case, Pathway


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

CREATE INDEX IF NOT EXISTS idx_cases_state ON cases(state);
CREATE INDEX IF NOT EXISTS idx_cases_owner ON cases(owner);
CREATE INDEX IF NOT EXISTS idx_audit_case ON audit_events(case_id, sequence);
CREATE INDEX IF NOT EXISTS idx_outbox_ready ON notification_outbox(status, available_at);
"""


@dataclass(frozen=True)
class User:
    """A person or service account that can participate in a case."""

    id: str
    name: str
    role: str
    email: str | None = None
    is_active: bool = True
    created_at: datetime | None = None


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


def _datetime(value: datetime | str | None) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    raise TypeError("a datetime or ISO datetime string is required")


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
        "users",
        "cases",
        "consents",
        "evidence",
        "approvals",
        "audit_events",
        "notification_outbox",
    }

    def __init__(self, path: str | Path = "careflow.db") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        # FastAPI's synchronous TestClient and request handlers may run on a
        # worker thread different from the one that created the application.
        # The store still owns one connection and serializes writes with
        # SQLite transactions, so allowing that connection to cross the
        # request boundary keeps the existing :memory: fixture useful too.
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.initialize()

    def initialize(self) -> None:
        """Create the current schema without changing existing records."""

        self.connection.executescript(SCHEMA)
        self.connection.commit()

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

    def list_users(self) -> list[User]:
        rows = self.connection.execute("SELECT * FROM users ORDER BY id").fetchall()
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

        row = self.connection.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
        if row is None:
            raise KeyError(f"case not found: {case_id}")
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

        events = self.connection.execute(
            "SELECT * FROM audit_events WHERE case_id = ? ORDER BY sequence", (case_id,)
        ).fetchall()
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
    ) -> str:
        now = _datetime(created_at) if created_at is not None else datetime.now(timezone.utc)
        available_at = available_at or now
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
    def _outbox_row(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["payload"] = _from_json(result.pop("payload_json"), {})
        return result


# ``Database`` is a useful compatibility spelling for later API modules.
Database = SQLiteStore
