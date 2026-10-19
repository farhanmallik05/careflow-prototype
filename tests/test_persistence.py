from datetime import datetime, timedelta

import pytest

from careflow.engine import Case, Pathway
from careflow.persistence import Consent, SQLiteStore, User
from careflow.seed import DEMO_NOW, seed_demo

PW = Pathway(
    {
        "name": "test_pathway",
        "start": "start",
        "terminal": ["closed"],
        "approvers": ["care_coordinator"],
        "states": {
            "start": {"owner": "lab", "sla_hours": 24, "next": {"finish": {"to": "closed"}}},
        },
    }
)
T0 = datetime(2026, 11, 1, 9)


def test_sqlite_round_trip_preserves_case_audit_and_operational_records(tmp_path):
    db_path = tmp_path / "careflow.sqlite"
    with SQLiteStore(db_path) as store:
        store.add_user(User("patient-1", "Synthetic Patient", "patient", created_at=T0))
        case = Case("CASE-1", PW, T0)
        case.transition("finish", "care-coordinator", T0 + timedelta(hours=2), evidence=["closure_note"])
        store.save_case(case, patient_user_id="patient-1")
        store.add_consent(
            Consent(
                id="consent-1",
                user_id="patient-1",
                case_id="CASE-1",
                consent_type="care_coordination",
                status="granted",
                granted_at=T0,
                source="test",
            )
        )

        loaded = store.load_case("CASE-1", PW)
        assert loaded.state == "closed"
        assert loaded.owner is None
        assert loaded.evidence == {"closure_note"}
        assert loaded.audit == case.audit
        assert store.get_case_record("CASE-1")["patient_user_id"] == "patient-1"
        assert store.list_consents(case_id="CASE-1")[0].status == "granted"
        assert [item.kind for item in store.list_evidence("CASE-1")] == ["closure_note"]
        assert store.count("audit_events") == 2


def test_approval_and_notification_outbox_are_persisted(tmp_path):
    with SQLiteStore(tmp_path / "careflow.sqlite") as store:
        store.add_user(User("patient-1", "Synthetic Patient", "patient", created_at=T0))
        case = Case("CASE-1", PW, T0)
        case.transition(
            "finish",
            "care-coordinator",
            T0 + timedelta(hours=1),
            approval={"role": "care_coordinator", "name": "Demo", "note": "Reviewed"},
        )
        store.save_case(case, patient_user_id="patient-1")
        approvals = store.list_approvals("CASE-1")
        assert len(approvals) == 1
        assert approvals[0].role == "care_coordinator"
        assert approvals[0].note == "Reviewed"
        store.add_consent(
            Consent(
                id="consent-1",
                user_id="patient-1",
                case_id="CASE-1",
                consent_type="care_coordination",
                status="granted",
                granted_at=T0,
                source="test",
            )
        )

        notification_id = store.enqueue_notification(
            event="case_closed",
            case_id="CASE-1",
            recipient="demo-coordinator",
            payload={"synthetic": True},
            notification_id="notification-1",
            available_at=T0,
            created_at=T0,
            dedupe_key="case-1-closed",
        )
        assert store.pending_notifications(now=T0 + timedelta(minutes=1))[0]["id"] == notification_id
        store.mark_notification_sent(notification_id, sent_at=T0 + timedelta(hours=1))
        assert store.pending_notifications(now=T0 + timedelta(hours=2)) == []
        assert store.list_outbox()[0]["status"] == "sent"
        assert store.list_outbox()[0]["payload"] == {"synthetic": True}


def test_case_notification_requires_granted_unexpired_consent(tmp_path):
    with SQLiteStore(tmp_path / "careflow.sqlite") as store:
        store.add_user(User("patient-1", "Synthetic Patient", "patient", created_at=T0))
        case = Case("CASE-1", PW, T0)
        store.save_case(case, patient_user_id="patient-1")

        with pytest.raises(ValueError, match="consent"):
            store.enqueue_notification(event="case_attention", case_id="CASE-1", created_at=T0)

        store.add_consent(
            Consent(
                id="consent-expired",
                user_id="patient-1",
                case_id="CASE-1",
                consent_type="care_coordination",
                status="granted",
                granted_at=T0,
                expires_at=T0 + timedelta(hours=1),
                source="test",
            )
        )
        with pytest.raises(ValueError, match="consent"):
            store.enqueue_notification(
                event="case_attention",
                case_id="CASE-1",
                created_at=T0 + timedelta(hours=2),
            )


def test_seed_is_deterministic_and_covers_pathway_stages(tmp_path):
    db_path = tmp_path / "demo.sqlite"
    first_summary = seed_demo(db_path, now=DEMO_NOW)
    with SQLiteStore(db_path) as store:
        first_cases = store.list_case_records()
        first_outbox = store.list_outbox()
        first_states = {row["state"] for row in first_cases}

    second_summary = seed_demo(db_path, now=DEMO_NOW)
    with SQLiteStore(db_path) as store:
        second_cases = store.list_case_records()
        second_outbox = store.list_outbox()

    assert first_summary == second_summary
    assert first_summary["cases"] == 40
    assert first_summary["users"] == 46
    assert first_summary["consents"] == 40
    assert first_summary["approvals"] == 2
    assert len(first_states) == 10
    assert first_summary["evidence"] > 0
    assert first_summary["notification_outbox"] > 0
    assert first_cases == second_cases
    assert first_outbox == second_outbox


def test_sql_worklist_filtering_and_pagination(tmp_path):
    db_path = tmp_path / "worklist_test.sqlite"
    with SQLiteStore(db_path) as store:
        # Check WAL mode
        journal_mode = store.connection.execute("PRAGMA journal_mode").fetchone()[0]
        assert journal_mode.lower() == "wal"

        # Add cases
        store.add_user(User("p1", "Patient One", "patient", created_at=T0))
        c1 = Case("C-ACTIVE-1", PW, T0, urgent=False)
        c2 = Case("C-ACTIVE-2", PW, T0, urgent=True)
        c3 = Case("C-CLOSED", PW, T0)
        c3.transition("finish", "care_coordinator", T0 + timedelta(hours=1), evidence=["closure_note"])

        store.save_case(c1, patient_user_id="p1")
        store.save_case(c2, patient_user_id="p1")
        store.save_case(c3, patient_user_id="p1")

        # SQL level worklist should filter WHERE closed = 0 and sort urgent first
        active = store.get_worklist_cases(limit=10, offset=0)
        assert len(active) == 2
        assert active[0]["id"] == "C-ACTIVE-2"
        assert active[1]["id"] == "C-ACTIVE-1"
        assert store.count_worklist_cases() == 2

        # Filter by urgent=True
        urgent_only = store.get_worklist_cases(urgent=True)
        assert len(urgent_only) == 1
        assert urgent_only[0]["id"] == "C-ACTIVE-2"

        # Pagination test
        page1 = store.get_worklist_cases(limit=1, offset=0)
        page2 = store.get_worklist_cases(limit=1, offset=1)
        assert len(page1) == 1 and page1[0]["id"] == "C-ACTIVE-2"
        assert len(page2) == 1 and page2[0]["id"] == "C-ACTIVE-1"

