from fastapi.testclient import TestClient

from careflow.api import create_app
from careflow.persistence import User
from careflow.seed import seed_demo
from tests.test_api import T0, login, make_client, open_case


def advance_to_care_received(client, case_id="CASE-1"):
    for user_id, action, details in (
        ("demo-lab", "notify", {}),
        ("demo-screening", "acknowledge", {"evidence": ["ack_record"]}),
        ("demo-primary-care", "review", {"evidence": ["clinical_review"]}),
        ("demo-coordinator", "accept", {"evidence": ["acceptance"]}),
        ("demo-specialist", "book", {"evidence": ["appointment"]}),
        ("demo-specialist", "attend", {"evidence": ["attendance"]}),
    ):
        login(client, user_id)
        response = client.post(f"/cases/{case_id}/transition", json={"action": action, **details})
        assert response.status_code == 200, response.text


def test_signed_login_current_user_logout_and_invalid_cookie(tmp_path):
    with make_client(tmp_path, session_secret="test-secret") as client:
        assert client.get("/auth/current-user").status_code == 401

        payload = login(client, "demo-lab")
        assert payload["user"]["id"] == "demo-lab"
        assert payload["user"]["role"] == "lab"
        assert client.get("/auth/current-user").json()["id"] == "demo-lab"

        client.cookies.set("careflow_session", "not-a-valid-signature")
        invalid = client.get("/auth/current-user")
        assert invalid.status_code == 401
        assert "invalid" in invalid.json()["detail"]

        login(client, "demo-lab")
        assert client.post("/auth/logout").json() == {"ok": True}
        assert client.get("/auth/current-user").status_code == 401


def test_seeded_demo_users_can_use_signed_session_login(tmp_path):
    db_path = tmp_path / "seeded.sqlite"
    seed_demo(db_path, now=T0)
    with TestClient(create_app(db_path, clock=lambda: T0)) as client:
        response = client.post("/auth/login", json={"user_id": "demo-patient-001"})
        assert response.status_code == 200, response.text
        assert response.json()["role"] == "patient"
        assert client.get("/auth/current-user").json()["id"] == "demo-patient-001"


def test_transition_denies_non_owner_non_approver_role(tmp_path):
    with make_client(tmp_path) as client:
        open_case(client)
        login(client, "demo-screening")
        response = client.post("/cases/CASE-1/transition", json={"action": "notify"})
        assert response.status_code == 403
        assert client.get("/cases/CASE-1").json()["state"] == "result_issued"


def test_authorized_approver_can_close_with_matching_documented_approval(tmp_path):
    with make_client(tmp_path) as client:
        open_case(client)
        advance_to_care_received(client)
        login(client, "demo-on-call")
        response = client.post(
            "/cases/CASE-1/transition",
            json={
                "action": "close",
                "approval": {
                    "role": "clinician_on_call",
                    "note": "Reviewed closure documentation",
                },
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["closed"] is True
        assert response.json()["owner"] is None


def test_patient_isolation_allows_only_own_case(tmp_path):
    with make_client(tmp_path) as client:
        login(client, "demo-lab")
        first = client.post("/cases", json={"id": "CASE-1", "patient_user_id": "demo-patient-001"})
        second = client.post("/cases", json={"id": "CASE-2", "patient_user_id": "demo-patient-002"})
        assert first.status_code == 201, first.text
        assert second.status_code == 201, second.text

        login(client, "demo-patient-001")
        assert client.get("/cases/CASE-1").status_code == 200
        assert client.get("/cases/CASE-2").status_code == 403
        assert client.post("/cases/CASE-1/transition", json={"action": "notify"}).status_code == 403

        logs = client.app.state.store.list_access_logs(user_id="demo-patient-001")
        assert [log.action for log in logs] == ["read"]
        assert [log.case_id for log in logs] == ["CASE-1"]


def test_access_logs_cover_read_mutation_worklist_and_metrics(tmp_path):
    with make_client(tmp_path) as client:
        open_case(client)
        login(client, "demo-lab")
        assert client.get("/cases/CASE-1").status_code == 200
        assert client.post("/cases/CASE-1/transition", json={"action": "notify"}).status_code == 200
        assert client.get("/worklist").status_code == 200
        assert client.get("/metrics").status_code == 200
        case = client.app.state.store.load_case("CASE-1", client.app.state.pathway)
        assert case.audit[-1]["actor"] == "demo-lab"

        logs = client.app.state.store.list_access_logs(case_id="CASE-1", user_id="demo-lab")
        assert {log.action for log in logs} == {"read", "mutation", "worklist", "metrics"}
        assert sum(log.action == "mutation" for log in logs) == 2


def test_assignment_requires_active_user_with_current_state_role(tmp_path):
    with make_client(tmp_path) as client:
        open_case(client)
        login(client, "demo-lab")
        assert client.post("/cases/CASE-1/transition", json={"action": "notify"}).status_code == 200
        client.app.state.store.add_user(
            User("inactive-screening", "Inactive Screening", "screening_program", is_active=False, created_at=T0)
        )

        login(client, "demo-screening")
        wrong_role = client.post("/cases/CASE-1/assign", json={"owner": "demo-lab"})
        inactive = client.post("/cases/CASE-1/assign", json={"owner": "inactive-screening"})
        assigned = client.post("/cases/CASE-1/assign", json={"owner": "demo-screening"})
        assert wrong_role.status_code == 422
        assert inactive.status_code == 422
        assert assigned.status_code == 200
        assert assigned.json()["owner"] == "demo-screening"
        assert client.app.state.store.get_case_record("CASE-1")["owner"] == "demo-screening"
