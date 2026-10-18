from datetime import timedelta

from tests.test_api import T0, login, make_client, open_case


HTML = {"Accept": "text/html"}


def test_html_operational_pages_require_signed_non_patient_session(tmp_path):
    with make_client(tmp_path) as client:
        unauthenticated_worklist = client.get("/worklist", headers=HTML)
        assert unauthenticated_worklist.status_code == 401
        assert "authentication required" in unauthenticated_worklist.text

        login(client, "demo-patient-001")
        patient_worklist = client.get("/worklist", headers=HTML)
        patient_metrics = client.get("/metrics", headers=HTML)
        assert patient_worklist.status_code == 403
        assert patient_metrics.status_code == 403
        assert "operational views" in patient_worklist.text


def test_html_pages_render_operational_context_and_keep_default_json(tmp_path):
    current = [T0]
    with make_client(tmp_path, clock=lambda: current[0]) as client:
        open_case(client, patient_user_id="demo-patient-001")
        current[0] = T0 + timedelta(hours=30)
        login(client, "demo-lab")

        worklist = client.get("/worklist", headers=HTML)
        assert worklist.status_code == 200
        assert worklist.headers["content-type"].startswith("text/html")
        assert "Worklist" in worklist.text
        assert "Demo Lab Service" in worklist.text
        assert "Elapsed / SLA" in worklist.text
        assert "Recommended next action" in worklist.text
        assert "Evidence needed" in worklist.text

        case = client.get("/cases/CASE-1", headers=HTML)
        assert case.status_code == 200
        assert all(section in case.text for section in ("Timeline", "Evidence", "Audit"))
        assert "CASE-1" in case.text

        metrics = client.get("/metrics", headers=HTML)
        assert metrics.status_code == 200
        assert "Total cases" in metrics.text
        assert "Audit-derived observed metrics" in metrics.text
        assert "Time to notification" in metrics.text
        assert "Audit-derived reason counts" in metrics.text
        assert "modelled from assumptions, not clinical evidence" in metrics.text

        default_worklist = client.get("/worklist")
        default_case = client.get("/cases/CASE-1")
        default_metrics = client.get("/metrics")
        assert default_worklist.headers["content-type"].startswith("application/json")
        assert default_case.headers["content-type"].startswith("application/json")
        assert default_metrics.headers["content-type"].startswith("application/json")
        assert isinstance(default_worklist.json(), list)
        assert default_case.json()["id"] == "CASE-1"
        assert default_metrics.json()["total_cases"] == 1


def test_landing_and_demo_login_are_click_through_friendly(tmp_path):
    with make_client(tmp_path) as client:
        landing = client.get("/")
        assert landing.status_code == 200
        assert landing.headers["content-type"].startswith("text/html")
        assert "/demo-login/demo-lab" in landing.text
        assert "No password" in landing.text

        redirect = client.get("/demo-login/demo-lab", follow_redirects=False)
        assert redirect.status_code == 303
        assert redirect.headers["location"] == "/worklist"
        assert client.get("/worklist", headers=HTML).status_code == 200


def test_patient_html_status_and_case_pages_are_isolated(tmp_path):
    with make_client(tmp_path) as client:
        login(client, "demo-lab")
        first = client.post(
            "/cases", json={"id": "CASE-1", "patient_user_id": "demo-patient-001"}
        )
        second = client.post(
            "/cases", json={"id": "CASE-2", "patient_user_id": "demo-patient-002"}
        )
        assert first.status_code == 201, first.text
        assert second.status_code == 201, second.text

        login(client, "demo-patient-001")
        own_status = client.get("/patient/demo-patient-001", headers=HTML)
        assert own_status.status_code == 200
        assert "CASE-1" in own_status.text
        assert "CASE-2" not in own_status.text
        assert "Your screening result is ready" in own_status.text

        foreign_status = client.get("/patient/demo-patient-002", headers=HTML)
        assert foreign_status.status_code == 403
        assert "only their own patient status" in foreign_status.text
        assert "CASE-2" not in foreign_status.text

        own_case = client.get("/cases/CASE-1", headers=HTML)
        foreign_case = client.get("/cases/CASE-2", headers=HTML)
        assert own_case.status_code == 200
        assert foreign_case.status_code == 403
        assert "patients may access only their own case" in foreign_case.text

        logs = client.app.state.store.list_access_logs(user_id="demo-patient-001")
        assert [log.case_id for log in logs] == ["CASE-1", "CASE-1"]
        assert all(log.action == "read" for log in logs)
