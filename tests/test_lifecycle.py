import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from careflow.api import create_app
from careflow.engine import Case, Pathway
from careflow.ops import backup_database, restore_database, verify_database
from careflow.persistence import (
    CURRENT_SCHEMA_VERSION,
    SCHEMA,
    Consent,
    DatabaseIntegrityError,
    DatabaseLifecycleError,
    SQLiteStore,
    User,
)
from careflow.seed import DEMO_NOW, seed_demo

PATHWAY = Pathway.load("careflow/pathways/screening_to_care.yaml")
T0 = datetime(2026, 11, 1, 9)


def test_fresh_init_and_repeated_migration_are_versioned_and_stable(tmp_path):
    db_path = tmp_path / "fresh.sqlite"

    with SQLiteStore(db_path) as store:
        assert store.schema_version == CURRENT_SCHEMA_VERSION
        first_rows = store.connection.execute(
            "SELECT version, name FROM schema_migrations ORDER BY version"
        ).fetchall()
        assert [row[0] for row in first_rows] == [1, 2]

    with SQLiteStore(db_path) as store:
        store.initialize()
        second_rows = store.connection.execute(
            "SELECT version, name FROM schema_migrations ORDER BY version"
        ).fetchall()
        assert [tuple(row) for row in second_rows] == [tuple(row) for row in first_rows]
        store.verify_readiness()


def test_legacy_baseline_is_upgraded_without_losing_case_or_audit(tmp_path):
    db_path = tmp_path / "legacy.sqlite"
    connection = sqlite3.connect(db_path)
    connection.executescript(SCHEMA)
    connection.execute(
        "INSERT INTO users (id, name, role, created_at) VALUES (?, ?, ?, ?)",
        ("legacy-patient", "Legacy Synthetic Patient", "patient", "2026-11-01T09:00:00"),
    )
    connection.execute(
        """
        INSERT INTO cases
            (id, patient_user_id, pathway, state, entered_at, owner, urgent, closed, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "LEGACY-001",
            "legacy-patient",
            PATHWAY.name,
            PATHWAY.start,
            "2026-11-01T09:00:00",
            "legacy-lab",
            0,
            0,
            "2026-11-01T09:00:00",
            "2026-11-01T09:00:00",
        ),
    )
    connection.execute(
        """
        INSERT INTO audit_events
            (case_id, sequence, occurred_at, actor, action, from_state, to_state, evidence_json, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "LEGACY-001",
            0,
            "2026-11-01T09:00:00",
            "legacy-system",
            "open",
            None,
            PATHWAY.start,
            "[]",
            "{}",
        ),
    )
    connection.commit()
    connection.close()

    with SQLiteStore(db_path) as store:
        assert store.schema_version == CURRENT_SCHEMA_VERSION
        loaded = store.load_case("LEGACY-001", PATHWAY)
        assert loaded.id == "LEGACY-001"
        assert loaded.audit[0]["ts"].tzinfo == timezone.utc
        assert store.count("audit_events") == 1


def test_readiness_fails_for_behind_and_unsupported_versions(tmp_path):
    db_path = tmp_path / "version.sqlite"
    with SQLiteStore(db_path):
        pass

    connection = sqlite3.connect(db_path)
    connection.execute("PRAGMA user_version = 1")
    connection.commit()
    connection.close()
    with SQLiteStore(db_path, auto_migrate=False) as store:
        with pytest.raises(DatabaseLifecycleError, match="behind"):
            store.verify_readiness()

    connection = sqlite3.connect(db_path)
    connection.execute("PRAGMA user_version = 999")
    connection.commit()
    connection.close()
    app = create_app(db_path, clock=T0, pathway=PATHWAY)
    with TestClient(app) as client:
        ready = client.get("/health/ready")
        assert ready.status_code == 503
        assert ready.json()["checks"]["reason"] == "unsupportedschemaerror"


def test_readiness_fails_for_foreign_key_integrity_and_corruption(tmp_path):
    db_path = tmp_path / "integrity.sqlite"
    with SQLiteStore(db_path) as store:
        store.connection.execute("PRAGMA foreign_keys = OFF")
        store.connection.execute(
            "INSERT INTO evidence (id, case_id, kind, recorded_at) VALUES (?, ?, ?, ?)",
            ("orphan", "missing-case", "test", "2026-11-01T09:00:00+00:00"),
        )
        store.connection.commit()
    with SQLiteStore(db_path, auto_migrate=False) as store:
        with pytest.raises(DatabaseIntegrityError, match="foreign-key"):
            store.verify_readiness()

    corrupt_path = tmp_path / "corrupt.sqlite"
    corrupt_path.write_bytes(b"not a sqlite database")
    app = create_app(corrupt_path, clock=T0, pathway=PATHWAY)
    with TestClient(app) as client:
        ready = client.get("/health/ready")
        assert ready.status_code == 503
        assert ready.json()["status"] == "not_ready"


def test_writable_path_check_is_explicit_and_safe(tmp_path, monkeypatch):
    db_path = tmp_path / "writable.sqlite"
    with SQLiteStore(db_path) as store:
        store.verify_readiness()
        monkeypatch.setattr("careflow.persistence.os.access", lambda *_: False)
        with pytest.raises(DatabaseLifecycleError, match="writable"):
            store.check_writable_path()


def test_backup_restore_preserves_cases_audits_consent_drafts_and_outbox(tmp_path):
    source = tmp_path / "source.sqlite"
    backup = tmp_path / "backup.sqlite"
    restored = tmp_path / "restored.sqlite"
    seed_demo(source, now=DEMO_NOW)
    with SQLiteStore(source) as store:
        store.add_message_draft(
            case_id="DEMO-001",
            patient_user_id="demo-patient-001",
            language="en",
            template="care_update",
            message="Synthetic draft",
            provider="rule_based",
            review_required=False,
            review_flag=None,
            created_by="demo-lab",
            created_at=DEMO_NOW,
            draft_id="draft-backup-1",
        )

    source_report = verify_database(source)
    backup_report = backup_database(source, backup)
    restored_report = restore_database(backup, restored)
    for report in (backup_report, restored_report):
        assert report.schema_version == CURRENT_SCHEMA_VERSION
        assert report.cases == source_report.cases
        assert report.audit_events == source_report.audit_events
        assert report.consents == source_report.consents
        assert report.message_drafts == 1
        assert report.notification_outbox == source_report.notification_outbox
    with SQLiteStore(restored) as store:
        assert store.get_message_draft("draft-backup-1").message == "Synthetic draft"
        assert store.list_outbox()


def test_utc_normalization_and_exact_consent_expiry_boundary(tmp_path):
    db_path = tmp_path / "utc.sqlite"
    with SQLiteStore(db_path) as store:
        store.add_user(User("patient-1", "Synthetic Patient", "patient", created_at=T0))
        case = Case("UTC-1", PATHWAY, T0, owner="demo-lab")
        store.save_case(case, patient_user_id="patient-1")
        expires_at = T0 + timedelta(hours=1)
        store.add_consent(
            Consent(
                id="consent-utc",
                user_id="patient-1",
                case_id="UTC-1",
                consent_type="care_coordination",
                status="granted",
                granted_at=T0,
                expires_at=expires_at,
                source="test",
            )
        )
        record = store.get_case_record("UTC-1")
        assert record["created_at"].endswith("+00:00")
        loaded = store.load_case("UTC-1", PATHWAY)
        assert loaded.entered.tzinfo == timezone.utc
        assert store.has_valid_consent("UTC-1", now=expires_at - timedelta(microseconds=1))
        assert not store.has_valid_consent("UTC-1", now=expires_at)


def test_operator_commands_return_bounded_json_and_docs_describe_topology(tmp_path, capsys):
    db_path = tmp_path / "cli.sqlite"
    seed_demo(db_path, now=DEMO_NOW)
    report = verify_database(db_path)
    assert json.dumps(report.__dict__, sort_keys=True)
    readme = open("README.md", encoding="utf-8").read()
    assert "one process and one Uvicorn worker" in readme
    assert "make backup" in readme
    assert "UTC" in readme
