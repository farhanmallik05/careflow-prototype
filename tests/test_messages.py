from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from careflow.ai import LLMProvider, REVIEW_FLAG, RuleBasedProvider
from careflow.persistence import Consent

from tests.test_api import T0, login, make_client, open_case


def patient_case(client: TestClient, case_id: str = "CASE-MESSAGE"):
    login(client, "demo-lab")
    response = client.post(
        "/cases",
        json={"id": case_id, "patient_user_id": "demo-patient-001"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def grant_consent(client: TestClient, case_id: str = "CASE-MESSAGE", *, expires_at=None) -> None:
    client.app.state.store.add_consent(
        Consent(
            id=f"consent-{case_id}",
            user_id="demo-patient-001",
            case_id=case_id,
            consent_type="care_coordination",
            status="granted",
            granted_at=T0,
            expires_at=expires_at,
            source="test",
        )
    )


def test_rule_provider_is_deterministic_and_flags_non_english_languages():
    provider = RuleBasedProvider()
    context = {"case_id": "CASE-1", "state": "result_issued", "patient_name": "Asha"}

    english = provider.draft(context, language="en")
    assert english == provider.draft(context, language="en")
    assert english.message.startswith("Hello Asha.")
    assert english.review_required is False
    assert english.review_flag is None

    hindi = provider.draft(context, language="hi")
    bengali = provider.draft(context, language="bn")
    assert hindi.message != english.message
    assert bengali.message != english.message
    assert hindi.review_required is True
    assert bengali.review_required is True
    assert hindi.review_flag == REVIEW_FLAG
    assert bengali.review_flag == REVIEW_FLAG


def test_drafts_persist_pending_status_for_all_languages_without_engine_mutation(tmp_path):
    with make_client(tmp_path) as client:
        patient_case(client)
        before = client.app.state.store.load_case("CASE-MESSAGE", client.app.state.pathway)
        before_audit = list(before.audit)

        login(client, "demo-lab")
        drafts = []
        for language in ("en", "hi", "bn"):
            response = client.post(
                "/cases/CASE-MESSAGE/message-drafts",
                json={"language": language},
            )
            assert response.status_code == 201, response.text
            drafts.append(response.json())

        after = client.app.state.store.load_case("CASE-MESSAGE", client.app.state.pathway)
        assert after.state == before.state
        assert after.audit == before_audit
        assert client.app.state.store.count("audit_events") == len(before_audit)
        assert client.app.state.store.count("message_drafts") == 3
        assert {draft["language"] for draft in drafts} == {"en", "hi", "bn"}
        assert all(draft["status"] == "pending_approval" for draft in drafts)
        assert all(draft["status_label"] == "pending approval" for draft in drafts)
        assert drafts[1]["review_flag"] == REVIEW_FLAG
        assert drafts[2]["review_flag"] == REVIEW_FLAG
        listed = client.get("/cases/CASE-MESSAGE/message-drafts").json()
        assert {draft["id"] for draft in listed} == {draft["id"] for draft in drafts}
        assert {draft["status"] for draft in listed} == {"pending_approval"}


def test_patient_and_non_owner_staff_cannot_draft_or_approve(tmp_path):
    with make_client(tmp_path) as client:
        patient_case(client)
        login(client, "demo-patient-001")
        patient_draft = client.post("/cases/CASE-MESSAGE/message-drafts", json={"language": "en"})
        assert patient_draft.status_code == 403

        login(client, "demo-lab")
        created = client.post("/cases/CASE-MESSAGE/message-drafts", json={"language": "en"})
        assert created.status_code == 201, created.text
        draft_id = created.json()["id"]

        login(client, "demo-screening")
        denied = client.post(f"/message-drafts/{draft_id}/approve", json={"note": "Not my case role"})
        assert denied.status_code == 403
        assert "cannot approve" in denied.json()["detail"]

        login(client, "demo-patient-001")
        patient_approval = client.post(f"/message-drafts/{draft_id}/approve", json={"note": "No"})
        assert patient_approval.status_code == 403


def test_human_approval_requires_consent_and_queues_only_an_outbox_row(tmp_path):
    with make_client(tmp_path) as client:
        patient_case(client)
        login(client, "demo-lab")
        created = client.post("/cases/CASE-MESSAGE/message-drafts", json={"language": "en"})
        assert created.status_code == 201, created.text
        draft_id = created.json()["id"]

        denied = client.post(
            f"/message-drafts/{draft_id}/approve",
            json={"note": "Reviewed by lab"},
        )
        assert denied.status_code == 403
        assert "granted, unexpired" in denied.json()["detail"]
        assert client.app.state.store.list_outbox() == []
        assert client.app.state.store.get_message_draft(draft_id).status == "pending_approval"

        grant_consent(client)
        approved = client.post(
            f"/message-drafts/{draft_id}/approve",
            json={"note": "Reviewed by lab"},
        )
        assert approved.status_code == 200, approved.text
        payload = approved.json()
        assert payload["status"] == "approved"
        assert payload["approved_by"] == "demo-lab"
        assert payload["approval_note"] == "Reviewed by lab"
        assert payload["notification"]["event"] == "patient_message"
        assert payload["notification"]["status"] == "queued"
        assert payload["notification"]["sent_at"] is None
        assert len(client.app.state.store.pending_notifications(now=T0)) == 1


def test_expired_consent_cannot_approve_a_draft(tmp_path):
    current = [T0]
    with make_client(tmp_path, clock=lambda: current[0]) as client:
        patient_case(client)
        grant_consent(client, expires_at=T0 + timedelta(hours=1))
        login(client, "demo-lab")
        created = client.post("/cases/CASE-MESSAGE/message-drafts", json={"language": "en"})
        assert created.status_code == 201, created.text

        current[0] = T0 + timedelta(hours=2)
        denied = client.post(
            f"/message-drafts/{created.json()['id']}/approve",
            json={"note": "Too late"},
        )
        assert denied.status_code == 403
        assert "unexpired" in denied.json()["detail"]
        assert client.app.state.store.list_outbox() == []


def test_optional_llm_boundary_is_selected_only_by_environment_and_stays_offline(tmp_path, monkeypatch):
    monkeypatch.setenv("CAREFLOW_MESSAGE_PROVIDER", "llm")
    with make_client(tmp_path) as client:
        assert isinstance(client.app.state.message_provider, LLMProvider)
        open_case(client, "CASE-LLM")
        response = client.post("/cases/CASE-LLM/message-drafts", json={"language": "en"})
        assert response.status_code == 503
        assert "not configured" in response.json()["detail"]
        assert client.app.state.store.count("message_drafts") == 0


def test_multilingual_credential_gate_restricts_uncertified_draft_approvals(tmp_path):
    with make_client(tmp_path) as client:
        patient_case(client, "CASE-HI")
        grant_consent(client, "CASE-HI")
        login(client, "demo-lab")
        created = client.post("/cases/CASE-HI/message-drafts", json={"language": "hi"})
        assert created.status_code == 201, created.text
        draft_id = created.json()["id"]

        # 1. Uncertified staff member (demo-lab defaults to ("en",)) is rejected
        denied = client.post(
            f"/message-drafts/{draft_id}/approve",
            json={"note": "Attempted uncertified Hindi approval"},
        )
        assert denied.status_code == 403
        assert "requires certified language qualification" in denied.json()["detail"]

        # 2. Staff member with Hindi language credential can approve
        from careflow.persistence import User
        certified_staff = User(
            id="certified-interpreter",
            name="Asha Sharma",
            role="care_coordinator",
            certified_languages=("en", "hi"),
        )
        client.app.state.store.add_user(certified_staff)
        login(client, "certified-interpreter")

        approved = client.post(
            f"/message-drafts/{draft_id}/approve",
            json={"note": "Verified by certified medical interpreter"},
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["status"] == "approved"
        assert approved.json()["approved_by"] == "certified-interpreter"



