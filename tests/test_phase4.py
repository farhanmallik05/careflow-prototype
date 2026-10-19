from datetime import timedelta

import pytest

from careflow.persistence import ConcurrencyConflict, Consent, User
from tests.test_api import T0, login, make_client, open_case


def test_patient_case_projection_excludes_operational_fields(tmp_path):
    with make_client(tmp_path) as client:
        open_case(client, patient_user_id="demo-patient-001")
        login(client, "demo-patient-001")
        payload = client.get("/cases/CASE-1").json()
        assert payload["id"] == "CASE-1"
        assert "owner" not in payload
        assert "audit" not in payload
        assert "evidence" not in payload
        assert "patient_user_id" not in payload
        patient_page = client.get("/patient/demo-patient-001").json()
        assert "email" not in patient_page["patient"]
        assert client.get("/cases/CASE-1/audit").status_code == 403
        assert client.get("/message-drafts").status_code == 403


def test_same_role_non_owner_cannot_mutate_case(tmp_path):
    with make_client(tmp_path) as client:
        open_case(client)
        client.app.state.store.add_user(User("other-lab", "Other Lab", "lab", created_at=T0))
        login(client, "other-lab")
        assert client.get("/cases/CASE-1").status_code == 200
        assert client.post("/cases/CASE-1/transition", json={"action": "notify"}).status_code == 403
        assert client.post("/cases/CASE-1/assign", json={"owner": "demo-lab"}).status_code == 403
        assert client.post("/cases/CASE-1/message-drafts", json={"language": "en"}).status_code == 403


def test_route_alias_matrix_makes_the_same_patient_decision_before_serialization(tmp_path):
    with make_client(tmp_path) as client:
        open_case(client, patient_user_id="demo-patient-001")
        login(client, "demo-patient-001")
        protected = (
            "/worklist",
            "/metrics",
            "/message-drafts",
            "/drafts",
            "/cases/CASE-1/audit",
            "/audit/CASE-1",
            "/cases/CASE-1/access-logs",
            "/access-logs",
        )
        assert all(client.get(path).status_code == 403 for path in protected)
        assert client.get("/cases/CASE-1/audit", headers={"accept": "text/html"}).status_code == 403
        assert client.get("/audit/CASE-1", headers={"accept": "text/html"}).status_code == 403


def test_case_compare_and_swap_rejects_stale_mutation(tmp_path):
    with make_client(tmp_path) as client:
        open_case(client)
        store = client.app.state.store
        first = store.load_case("CASE-1", client.app.state.pathway)
        second = store.load_case("CASE-1", client.app.state.pathway)
        first.transition("notify", "demo-lab", T0)
        store.save_case(first, expected_version=first.version)
        second.transition("notify", "demo-lab", T0)
        with pytest.raises(ConcurrencyConflict, match="changed"):
            store.save_case(second, expected_version=second.version)


def test_evidence_and_approval_are_bound_to_verified_principal(tmp_path):
    with make_client(tmp_path) as client:
        open_case(client)
        login(client, "demo-lab")
        transition = client.post(
            "/cases/CASE-1/transition",
            json={"action": "notify", "approval": {"role": "clinician_on_call", "name": "attacker", "note": "x"}},
        )
        assert transition.status_code == 200
        login(client, "demo-screening")
        response = client.post(
            "/cases/CASE-1/transition",
            json={"action": "acknowledge", "evidence": ["ack_record"], "actor": "spoofed"},
        )
        assert response.status_code == 200
        evidence = client.app.state.store.list_evidence("CASE-1")[0]
        assert evidence.recorded_by == "demo-screening"
        assert evidence.recorded_role == "screening_program"
        assert evidence.state == "patient_notified"
        assert evidence.provenance["action"] == "acknowledge"

        login(client, "demo-screening")
        client.app.state.store.add_user(User("other-screening", "Other Screening", "screening_program", created_at=T0))
        denied = client.post(
            "/cases/CASE-1/transition",
            json={"action": "review", "approval": {"role": "clinician_on_call", "name": "attacker", "note": "x"}},
        )
        assert denied.status_code == 403


def test_consent_withdrawal_makes_approved_local_message_unavailable(tmp_path):
    with make_client(tmp_path) as client:
        open_case(client, patient_user_id="demo-patient-001")
        login(client, "demo-lab")
        draft = client.post("/cases/CASE-1/message-drafts", json={"language": "en"}).json()
        client.app.state.store.add_consent(
            Consent(
                id="consent-1",
                user_id="demo-patient-001",
                case_id="CASE-1",
                consent_type="care_coordination",
                status="granted",
                granted_at=T0,
                source="test",
            )
        )
        approved = client.post(f"/message-drafts/{draft['id']}/approve", json={"note": "reviewed"})
        assert approved.status_code == 200
        login(client, "demo-patient-001")
        withdrawn = client.post("/cases/CASE-1/consent", json={"status": "withdrawn"})
        assert withdrawn.status_code == 200
        assert client.app.state.store.get_message_draft(draft["id"]).status == "unavailable"
        assert client.app.state.store.list_outbox()[0]["status"] == "unavailable"
        login(client, "demo-lab")
        replay = client.post(f"/message-drafts/{draft['id']}/approve", json={"note": "retry"})
        assert replay.status_code == 409


def test_consent_expiry_is_immediate_and_retained_in_history(tmp_path):
    with make_client(tmp_path) as client:
        open_case(client, patient_user_id="demo-patient-001")
        store = client.app.state.store
        store.add_consent(
            Consent(
                id="consent-expiring",
                user_id="demo-patient-001",
                case_id="CASE-1",
                consent_type="care_coordination",
                status="granted",
                granted_at=T0,
                expires_at=T0 + timedelta(hours=1),
                source="test",
            )
        )
        assert not store.has_valid_consent("CASE-1", now=T0 + timedelta(hours=1))
        history = store.list_consent_history(case_id="CASE-1")
        assert history[-1].provenance["event"] == "expired"


def test_draft_and_outbox_operations_are_idempotent(tmp_path):
    with make_client(tmp_path) as client:
        open_case(client, patient_user_id="demo-patient-001")
        store = client.app.state.store
        store.add_consent(
            Consent(
                id="consent-1",
                user_id="demo-patient-001",
                case_id="CASE-1",
                consent_type="care_coordination",
                status="granted",
                granted_at=T0,
                source="test",
            )
        )
        first = store.enqueue_notification(
            event="case_attention",
            case_id="CASE-1",
            notification_id="notification-1",
            dedupe_key="case-1-attention",
            created_at=T0,
        )
        second = store.enqueue_notification(
            event="case_attention",
            case_id="CASE-1",
            notification_id="notification-1-replay",
            dedupe_key="case-1-attention",
            created_at=T0,
        )
        assert first == "notification-1"
        assert second == first
        assert len(store.list_outbox()) == 1

        login(client, "demo-lab")
        draft = client.post("/cases/CASE-1/message-drafts", json={"language": "en"}).json()
        first_approval = client.post(f"/message-drafts/{draft['id']}/approve", json={"note": "reviewed"})
        second_approval = client.post(f"/message-drafts/{draft['id']}/approve", json={"note": "reviewed"})
        assert first_approval.status_code == second_approval.status_code == 200
        assert first_approval.json()["notification"]["id"] == second_approval.json()["notification"]["id"]
        assert len([row for row in store.list_outbox() if row["event"] == "patient_message"]) == 1
