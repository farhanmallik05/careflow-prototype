from datetime import datetime, timedelta
from pathlib import Path
from fastapi.testclient import TestClient

from careflow.api import create_app
from careflow.engine import Pathway
from careflow.persistence import User


PATHWAY = Pathway.load(Path(__file__).parent.parent / "careflow" / "pathways" / "screening_to_care.yaml")
T0 = datetime(2026, 11, 1, 9)

USERS = (
    ("demo-lab", "Demo Lab Service", "lab"),
    ("demo-screening", "Demo Screening Program", "screening_program"),
    ("demo-primary-care", "Demo Primary Care", "primary_care"),
    ("demo-coordinator", "Demo Care Coordinator", "care_coordinator"),
    ("demo-specialist", "Demo Specialist", "specialist"),
    ("demo-on-call", "Demo Clinician on Call", "clinician_on_call"),
    ("demo-patient-001", "Synthetic Patient 001", "patient"),
)


def make_client(tmp_path):
    app = create_app(
        tmp_path / "ehr_sync_test.sqlite",
        clock=lambda: T0,
        pathway=PATHWAY,
    )
    for user_id, name, role in USERS:
        app.state.store.add_user(User(user_id, name, role, created_at=T0))
    return TestClient(app)


def advance_case_to_booked(client, case_id="CASE-EHR-1"):
    client.post("/auth/login", json={"user_id": "demo-lab"})
    client.post("/cases", json={"id": case_id})
    client.post(f"/cases/{case_id}/transition", json={"action": "notify", "actor": "lab"})
    client.post("/auth/login", json={"user_id": "demo-screening"})
    client.post(f"/cases/{case_id}/transition", json={"action": "acknowledge", "actor": "screening", "evidence": ["ack_record"]})
    client.post("/auth/login", json={"user_id": "demo-primary-care"})
    client.post(f"/cases/{case_id}/transition", json={"action": "review", "actor": "pcp", "evidence": ["clinical_review"]})
    client.post("/auth/login", json={"user_id": "demo-coordinator"})
    client.post(f"/cases/{case_id}/transition", json={"action": "accept", "actor": "coord", "evidence": ["acceptance"]})
    client.post("/auth/login", json={"user_id": "demo-specialist"})
    client.post(f"/cases/{case_id}/transition", json={"action": "book", "actor": "spec", "evidence": ["appointment"]})


def test_ehr_appointment_cancellation_triggers_automated_reschedule_queue(tmp_path):
    with make_client(tmp_path) as client:
        advance_case_to_booked(client, "CASE-EHR-1")

        # Ingest EHR appointment cancellation webhook
        response = client.post(
            "/api/ehr/appointments/webhook",
            json={
                "resourceType": "Appointment",
                "appointment_id": "apt-9988",
                "status": "cancelled",
                "case_id": "CASE-EHR-1",
                "cancellation_reason": "Patient transportation conflict",
            },
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["status"] == "processed"
        assert data["action_taken"] == "cancelled_reschedule_queued"
        assert data["current_state"] == "referral_accepted"

        # Case is now back in referral_accepted and can be re-booked immediately
        rebook_resp = client.post(
            "/cases/CASE-EHR-1/transition",
            json={"action": "book", "actor": "spec", "evidence": ["appointment"]},
        )
        assert rebook_resp.status_code == 200
        assert rebook_resp.json()["state"] == "appointment_booked"


def test_ehr_appointment_rescheduled_in_place(tmp_path):
    with make_client(tmp_path) as client:
        advance_case_to_booked(client, "CASE-EHR-2")

        response = client.post(
            "/api/ehr/appointments/webhook",
            json={
                "resourceType": "Appointment",
                "status": "rescheduled",
                "case_id": "CASE-EHR-2",
                "rescheduled_start": "2026-11-20T09:30:00Z",
                "note": "Rescheduled per patient request via MyChart",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["action_taken"] == "rescheduled"
        assert data["current_state"] == "appointment_booked"


def test_ehr_appointment_noshow_escalates_to_clinician(tmp_path):
    with make_client(tmp_path) as client:
        advance_case_to_booked(client, "CASE-EHR-3")

        response = client.post(
            "/api/ehr/appointments/webhook",
            json={
                "resourceType": "Appointment",
                "status": "noshow",
                "case_id": "CASE-EHR-3",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["action_taken"] == "escalated_no_show"
        assert data["current_state"] == "escalated"
