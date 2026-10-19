from datetime import datetime
from pathlib import Path
from fastapi.testclient import TestClient

from careflow.api import create_app
from careflow.engine import Pathway
from careflow.persistence import User


PATHWAY = Pathway.load(Path(__file__).parent.parent / "careflow" / "pathways" / "screening_to_care.yaml")
T0 = datetime(2026, 11, 1, 9)


def make_client(tmp_path):
    app = create_app(
        tmp_path / "webhook_test.sqlite",
        clock=lambda: T0,
        pathway=PATHWAY,
    )
    app.state.store.add_user(User("coord-1", "Coordinator", "care_coordinator", created_at=T0))
    return TestClient(app), app.state.store


def test_carrier_delivery_receipt_success(tmp_path):
    client, store = make_client(tmp_path)
    # Seed an outbox notification row directly
    notif_id = store.enqueue_notification(
        channel="sms",
        recipient="+15551234567",
        payload={"message": "Your mammogram follow-up appointment is confirmed."},
        event="patient_reminder",
    )
    assert notif_id is not None

    # Ingest carrier delivery receipt for success
    response = client.post(
        "/webhooks/delivery-receipt",
        json={
            "MessageSid": notif_id,
            "MessageStatus": "delivered",
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "acknowledged"
    assert data["outbox_status"] == "sent"

    # Verify database state
    row = store.connection.execute(
        "SELECT status FROM notification_outbox WHERE id = ?", (notif_id,)
    ).fetchone()
    assert row["status"] == "sent"


def test_carrier_delivery_receipt_failure(tmp_path):
    client, store = make_client(tmp_path)
    notif_id = store.enqueue_notification(
        channel="sms",
        recipient="+15559876543",
        payload={"message": "Important breast screening follow-up notice."},
        event="patient_alert",
    )

    # Ingest carrier delivery receipt for rejection / failure
    response = client.post(
        "/webhooks/delivery-receipt",
        json={
            "notification_id": notif_id,
            "status": "undelivered",
            "ErrorCode": "30007",
            "ErrorMessage": "Carrier violation / 10DLC unregistered",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["outbox_status"] == "failed"
    assert data["error"] == "30007"

    row = store.connection.execute(
        "SELECT status FROM notification_outbox WHERE id = ?", (notif_id,)
    ).fetchone()
    assert row["status"] == "failed"


def test_carrier_delivery_receipt_missing_id_rejected(tmp_path):
    client, _ = make_client(tmp_path)
    response = client.post(
        "/webhooks/delivery-receipt",
        json={"MessageStatus": "delivered"},
    )
    assert response.status_code == 400
    assert "notification identifier is required" in response.json()["detail"]
