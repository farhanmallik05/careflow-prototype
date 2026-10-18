from datetime import datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from careflow.api import create_app
from careflow.engine import Pathway


PATHWAY = Pathway.load(Path(__file__).parent.parent / "careflow" / "pathways" / "screening_to_care.yaml")
T0 = datetime(2026, 11, 1, 9)


def make_client(tmp_path, clock=None):
    return TestClient(create_app(tmp_path / "careflow.sqlite", clock=clock or (lambda: T0), pathway=PATHWAY))


def open_case(client, case_id="CASE-1", **extra):
    response = client.post("/cases", json={"id": case_id, **extra})
    assert response.status_code == 201, response.text
    return response.json()


def test_case_happy_paths_create_get_transition_assign_and_metrics(tmp_path):
    with make_client(tmp_path) as client:
        created = open_case(client, urgent=True)
        assert created["state"] == "result_issued"
        assert created["owner"] == "lab"

        transitioned = client.post(
            "/cases/CASE-1/transition",
            json={"action": "notify", "actor": "lab-system"},
        )
        assert transitioned.status_code == 200, transitioned.text
        assert transitioned.json()["state"] == "patient_notified"

        assigned = client.post(
            "/cases/CASE-1/assign",
            json={"owner": "screening-team", "actor": "coordinator"},
        )
        assert assigned.status_code == 200, assigned.text
        assert assigned.json()["owner"] == "screening-team"

        fetched = client.get("/cases/CASE-1")
        assert fetched.status_code == 200
        assert fetched.json()["state"] == "patient_notified"
        assert fetched.json()["owner"] == "screening-team"

        metrics = client.get("/metrics")
        assert metrics.status_code == 200
        assert metrics.json()["total_cases"] == 1
        assert metrics.json()["open_cases"] == 1


def test_invalid_transition_is_a_clear_four_xx_response(tmp_path):
    with make_client(tmp_path) as client:
        open_case(client)
        response = client.post(
            "/cases/CASE-1/transition",
            json={"action": "close", "actor": "coordinator"},
        )
        assert response.status_code == 409
        assert response.json()["error"] == "transition_error"
        assert "not allowed from result_issued" in response.json()["detail"]


def test_closure_requires_evidence_or_documented_authorized_approval(tmp_path):
    with make_client(tmp_path) as client:
        open_case(client)
        steps = (
            ("notify", {}),
            ("acknowledge", {"evidence": ["ack_record"]}),
            ("review", {"evidence": ["clinical_review"]}),
            ("accept", {"evidence": ["acceptance"]}),
            ("book", {"evidence": ["appointment"]}),
            ("attend", {"evidence": ["attendance"]}),
        )
        for action, details in steps:
            response = client.post(
                "/cases/CASE-1/transition",
                json={"action": action, "actor": "staff", **details},
            )
            assert response.status_code == 200, response.text

        missing = client.post(
            "/cases/CASE-1/transition",
            json={"action": "close", "actor": "staff"},
        )
        assert missing.status_code == 422
        assert missing.json()["error"] == "transition_error"
        assert "missing evidence" in missing.json()["detail"]
        assert client.get("/cases/CASE-1").json()["state"] == "care_received"

        closed = client.post(
            "/cases/CASE-1/transition",
            json={
                "action": "close",
                "actor": "coordinator",
                "approval": {
                    "role": "care_coordinator",
                    "name": "Coordinator",
                    "note": "Reviewed closure documentation",
                },
            },
        )
        assert closed.status_code == 200, closed.text
        assert closed.json()["closed"] is True
        assert closed.json()["owner"] is None


def test_worklist_uses_engine_ordering_urgent_before_most_overdue(tmp_path):
    current = [T0]

    def clock():
        return current[0]

    with make_client(tmp_path, clock=clock) as client:
        open_case(client, "CASE-A")
        open_case(client, "CASE-B", urgent=True)
        current[0] = T0 + timedelta(hours=30)

        response = client.get("/worklist")
        assert response.status_code == 200
        rows = response.json()
        assert [row["case"] for row in rows] == ["CASE-B", "CASE-A"]
        assert all(row["overdue"] for row in rows)
