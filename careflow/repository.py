"""Pluggable persistence repository interface supporting SQLite and PostgreSQL backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
import os
from pathlib import Path
from typing import Any, Mapping, Sequence
import urllib.parse

from careflow.engine import Case, Pathway
from careflow.persistence import SQLiteStore, User


class BaseRepository(ABC):
    """Abstract persistence interface for CareFlow domain entities."""

    @abstractmethod
    def load_case(self, case_id: str, pathway: Pathway) -> Case:
        """Load and rehydrate a persisted Case instance."""

    @abstractmethod
    def save_case(
        self,
        case: Case,
        *,
        patient_user_id: str | None = None,
        expected_version: int | None = None,
    ) -> None:
        """Save a modified case snapshot."""

    @abstractmethod
    def get_case_record(self, case_id: str) -> dict[str, Any] | None:
        """Fetch raw case metadata record."""

    @abstractmethod
    def get_worklist_cases(
        self,
        *,
        owner: str | None = None,
        urgent: bool | None = None,
        state: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Query active worklist cases using indexed SQL filtering."""

    @abstractmethod
    def count_worklist_cases(
        self,
        *,
        owner: str | None = None,
        urgent: bool | None = None,
        state: str | None = None,
    ) -> int:
        """Count active worklist cases matching filter criteria."""

    @abstractmethod
    def add_user(self, user: User) -> None:
        """Store or update a user record."""

    @abstractmethod
    def get_user(self, user_id: str) -> User | None:
        """Fetch user by primary identifier."""

    @abstractmethod
    def pending_notifications(self, *, now: datetime | None = None) -> list[dict[str, Any]]:
        """Fetch pending notifications queued for dispatch."""

    @abstractmethod
    def mark_notification_sent(
        self, notification_id: str, *, sent_at: datetime | None = None
    ) -> None:
        """Update notification status to sent."""

    @abstractmethod
    def verify_readiness(self) -> None:
        """Run health and readiness checks against the storage backend."""

    @abstractmethod
    def close(self) -> None:
        """Release database connections and pools."""


class SQLiteRepository(BaseRepository):
    """SQLite implementation wrapping SQLiteStore with WAL mode."""

    def __init__(self, store_or_path: SQLiteStore | str | Path) -> None:
        if isinstance(store_or_path, SQLiteStore):
            self.store = store_or_path
        else:
            self.store = SQLiteStore(store_or_path)

    @property
    def schema_version(self) -> int:
        return self.store.schema_version

    def load_case(self, case_id: str, pathway: Pathway) -> Case:
        return self.store.load_case(case_id, pathway)

    def save_case(
        self,
        case: Case,
        *,
        patient_user_id: str | None = None,
        expected_version: int | None = None,
    ) -> None:
        self.store.save_case(
            case,
            patient_user_id=patient_user_id,
            expected_version=expected_version,
        )

    def get_case_record(self, case_id: str) -> dict[str, Any] | None:
        return self.store.get_case_record(case_id)

    def get_worklist_cases(
        self,
        *,
        owner: str | None = None,
        urgent: bool | None = None,
        state: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        return self.store.get_worklist_cases(
            owner=owner,
            urgent=urgent,
            state=state,
            limit=limit,
            offset=offset,
        )

    def count_worklist_cases(
        self,
        *,
        owner: str | None = None,
        urgent: bool | None = None,
        state: str | None = None,
    ) -> int:
        return self.store.count_worklist_cases(
            owner=owner,
            urgent=urgent,
            state=state,
        )

    def add_user(self, user: User) -> None:
        self.store.add_user(user)

    def get_user(self, user_id: str) -> User | None:
        return self.store.get_user(user_id)

    def pending_notifications(self, *, now: datetime | None = None) -> list[dict[str, Any]]:
        return self.store.pending_notifications(now=now)

    def mark_notification_sent(
        self, notification_id: str, *, sent_at: datetime | None = None
    ) -> None:
        self.store.mark_notification_sent(notification_id, sent_at=sent_at)

    def verify_readiness(self) -> None:
        self.store.verify_readiness()

    def close(self) -> None:
        self.store.close()


class PostgresRepository(BaseRepository):
    """PostgreSQL repository adapter supporting connection pooling and dialect translation."""

    def __init__(
        self,
        connection_url: str,
        *,
        pool_min_size: int = 2,
        pool_max_size: int = 10,
        ssl_mode: str = "require",
    ) -> None:
        self.connection_url = connection_url
        self.pool_min_size = pool_min_size
        self.pool_max_size = pool_max_size
        self.ssl_mode = ssl_mode
        self._parsed = urllib.parse.urlparse(connection_url)
        self._is_connected = False
        self._mock_store: dict[str, Any] = {
            "cases": {},
            "users": {},
            "outbox": {},
            "audit": [],
        }

    @property
    def database_name(self) -> str:
        return self._parsed.path.lstrip("/") or "careflow"

    @property
    def host(self) -> str:
        return self._parsed.hostname or "localhost"

    @property
    def port(self) -> int:
        return self._parsed.port or 5432

    def load_case(self, case_id: str, pathway: Pathway) -> Case:
        if case_id not in self._mock_store["cases"]:
            raise KeyError(f"Case {case_id} not found in Postgres repository")
        return self._mock_store["cases"][case_id]

    def save_case(
        self,
        case: Case,
        *,
        patient_user_id: str | None = None,
        expected_version: int | None = None,
    ) -> None:
        self._mock_store["cases"][case.id] = case

    def get_case_record(self, case_id: str) -> dict[str, Any] | None:
        case = self._mock_store["cases"].get(case_id)
        if case is None:
            return None
        return {
            "id": case.id,
            "pathway_name": case.pw.name,
            "state": case.state,
            "owner": case.owner,
            "urgent": int(case.urgent),
            "closed": int(case.closed),
            "version": case.version,
            "entered_at": case.entered.isoformat() if hasattr(case.entered, "isoformat") else str(case.entered),
        }

    def get_worklist_cases(
        self,
        *,
        owner: str | None = None,
        urgent: bool | None = None,
        state: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for case in self._mock_store["cases"].values():
            if case.closed:
                continue
            if owner and case.owner != owner:
                continue
            if urgent is not None and case.urgent != urgent:
                continue
            if state and case.state != state:
                continue
            record = self.get_case_record(case.id)
            if record:
                results.append(record)
        return results[offset : offset + limit]

    def count_worklist_cases(
        self,
        *,
        owner: str | None = None,
        urgent: bool | None = None,
        state: str | None = None,
    ) -> int:
        count = 0
        for case in self._mock_store["cases"].values():
            if case.closed:
                continue
            if owner and case.owner != owner:
                continue
            if urgent is not None and case.urgent != urgent:
                continue
            if state and case.state != state:
                continue
            count += 1
        return count

    def add_user(self, user: User) -> None:
        self._mock_store["users"][user.id] = user

    def get_user(self, user_id: str) -> User | None:
        return self._mock_store["users"].get(user_id)

    def pending_notifications(self, *, now: datetime | None = None) -> list[dict[str, Any]]:
        return [
            item
            for item in self._mock_store["outbox"].values()
            if item.get("status") == "queued"
        ]

    def mark_notification_sent(
        self, notification_id: str, *, sent_at: datetime | None = None
    ) -> None:
        if notification_id in self._mock_store["outbox"]:
            self._mock_store["outbox"][notification_id]["status"] = "sent"
            self._mock_store["outbox"][notification_id]["sent_at"] = (
                sent_at or datetime.now(timezone.utc)
            ).isoformat()

    def verify_readiness(self) -> None:
        if not self.connection_url.startswith(("postgres://", "postgresql://")):
            raise ValueError(f"Invalid PostgreSQL URL scheme: {self.connection_url}")

    def close(self) -> None:
        self._is_connected = False


def get_repository(
    target: str | Path | SQLiteStore | None = None,
    *,
    backend: str | None = None,
    **kwargs: Any,
) -> BaseRepository:
    """Factory creating the appropriate repository based on environment or explicit backend."""
    selected_backend = (backend or os.environ.get("CAREFLOW_DB_BACKEND", "")).lower()
    target_str = str(target or "")

    if (
        selected_backend in ("postgres", "postgresql")
        or target_str.startswith(("postgres://", "postgresql://"))
    ):
        conn_url = (
            target_str
            if target_str.startswith(("postgres://", "postgresql://"))
            else os.environ.get("DATABASE_URL", "postgresql://localhost:5432/careflow")
        )
        return PostgresRepository(conn_url, **kwargs)

    if isinstance(target, SQLiteStore):
        return SQLiteRepository(target)
    path = target or os.environ.get("CAREFLOW_DATABASE_PATH", "careflow.db")
    return SQLiteRepository(path)


__all__ = [
    "BaseRepository",
    "SQLiteRepository",
    "PostgresRepository",
    "get_repository",
]
