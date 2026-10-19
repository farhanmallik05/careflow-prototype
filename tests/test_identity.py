from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from careflow.api import create_app
from careflow.config import CareFlowSettings
from careflow.engine import Pathway
from careflow.oidc import FakeOIDCProvider
from careflow.persistence import User

PATHWAY = Pathway.load("careflow/pathways/screening_to_care.yaml")
T0 = datetime(2026, 11, 1, 9, tzinfo=timezone.utc)
ORIGIN = "https://careflow.example.test"


def pilot_client(tmp_path, provider, clock):
    settings = CareFlowSettings(
        mode="pilot",
        database_path=tmp_path / "careflow.sqlite",
        session_secret="a pilot-only test secret that is long enough",
        session_max_age=3600,
        session_idle_age=60,
        session_absolute_age=300,
        trusted_origins=(ORIGIN,),
        oidc_issuer=provider.issuer,
        oidc_client_id=provider.client_id,
        oidc_client_secret="provider-secret",
        oidc_redirect_uri=provider.redirect_uri,
        synthetic_only=True,
        allow_demo_login=False,
    )
    app = create_app(
        settings.database_path,
        clock=clock,
        pathway=PATHWAY,
        settings=settings,
        oidc_provider=provider,
    )
    app.state.store.add_user(User("local-lab", "Approved Lab", "lab", created_at=T0))
    app.state.store.link_identity(provider.issuer, "subject-1", "local-lab", created_at=T0)
    return TestClient(app, base_url=ORIGIN)


def login_pilot(client, provider, subject="subject-1"):
    start = client.get("/auth/login/start", follow_redirects=False)
    assert start.status_code == 303
    request = provider.authorization_requests[-1]
    code = provider.issue_authorization_code(
        subject=subject,
        nonce=request["nonce"],
        code_challenge=request["code_challenge"],
    )
    return client.get(
        f"/auth/callback?code={code}&state={request['state']}",
    )


def test_fake_oidc_pkce_mapping_and_server_session_logout(tmp_path):
    provider = FakeOIDCProvider()
    with pilot_client(tmp_path, provider, lambda: T0) as client:
        callback = login_pilot(client, provider)
        assert callback.status_code == 200, callback.text
        assert callback.json()["id"] == "local-lab"
        assert client.cookies.get("careflow_session")
        assert "Secure" in callback.headers["set-cookie"]
        assert "HttpOnly" in callback.headers["set-cookie"]
        assert "SameSite=lax" in callback.headers["set-cookie"]
        assert client.get("/auth/current-user").json()["id"] == "local-lab"

        csrf = client.cookies.get("careflow_csrf")
        logged_out = client.post(
            "/auth/logout",
            headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
        )
        assert logged_out.status_code == 200
        assert client.get("/auth/current-user").status_code == 401


def test_oidc_state_nonce_and_code_replay_are_rejected(tmp_path):
    provider = FakeOIDCProvider()
    with pilot_client(tmp_path, provider, lambda: T0) as client:
        _ = client.get("/auth/login/start", follow_redirects=False)
        request = provider.authorization_requests[-1]
        code = provider.issue_authorization_code(
            subject="subject-1",
            nonce="wrong-nonce",
            code_challenge=request["code_challenge"],
        )
        bad_nonce = client.get(
            f"/auth/callback?code={code}&state={request['state']}"
        )
        assert bad_nonce.status_code == 401

        replay = client.get(
            f"/auth/callback?code={code}&state={request['state']}"
        )
        assert replay.status_code == 401

        _ = client.get("/auth/login/start", follow_redirects=False)
        request = provider.authorization_requests[-1]
        code = provider.issue_authorization_code(
            subject="subject-1",
            nonce=request["nonce"],
            code_challenge=request["code_challenge"],
        )
        wrong_state = client.get(f"/auth/callback?code={code}&state=wrong")
        assert wrong_state.status_code == 401


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"issuer": "https://other.example.test"}, "issuer"),
        ({"audience": "other-client"}, "audience"),
        ({"expires_at": int(T0.timestamp())}, "expired"),
        ({"kid": "missing-key"}, "key"),
        ({"signature_key": "missing-key"}, "signature"),
        ({"malformed_claims": {"sub": ""}}, "subject"),
    ],
)
def test_fake_oidc_rejects_invalid_verified_claims(tmp_path, overrides, message):
    provider = FakeOIDCProvider()
    with pilot_client(tmp_path, provider, lambda: T0) as client:
        _ = client.get("/auth/login/start", follow_redirects=False)
        request = provider.authorization_requests[-1]
        code = provider.issue_authorization_code(
            subject="subject-1",
            nonce=request["nonce"],
            code_challenge=request["code_challenge"],
            **overrides,
        )
        callback = client.get(f"/auth/callback?code={code}&state={request['state']}")
        assert callback.status_code == 401
        assert client.get("/auth/current-user").status_code == 401


def test_unmapped_inactive_and_roleless_users_cannot_obtain_session(tmp_path):
    provider = FakeOIDCProvider()
    with pilot_client(tmp_path, provider, lambda: T0) as client:
        unmapped = login_pilot(client, provider, subject="unmapped")
        assert unmapped.status_code == 403

        store = client.app.state.store
        store.add_user(User("inactive", "Inactive", "lab", is_active=False, created_at=T0))
        store.link_identity(provider.issuer, "inactive-subject", "inactive", created_at=T0)
        assert login_pilot(client, provider, subject="inactive-subject").status_code == 403

        store.add_user(User("roleless", "Roleless", "", created_at=T0))
        store.link_identity(provider.issuer, "roleless-subject", "roleless", created_at=T0)
        assert login_pilot(client, provider, subject="roleless-subject").status_code == 403


def test_pilot_session_idle_expiry_and_csrf_origin_guards(tmp_path):
    provider = FakeOIDCProvider()
    current = [T0]
    with pilot_client(tmp_path, provider, lambda: current[0]) as client:
        assert login_pilot(client, provider).status_code == 200
        csrf = client.cookies.get("careflow_csrf")
        missing_csrf = client.post("/auth/logout", headers={"Origin": ORIGIN})
        assert missing_csrf.status_code == 403
        wrong_origin = client.post(
            "/auth/logout",
            headers={"Origin": "https://evil.example.test", "X-CSRF-Token": csrf},
        )
        assert wrong_origin.status_code == 403
        current[0] += timedelta(seconds=61)
        assert client.get("/auth/current-user").status_code == 401


def test_login_rotates_and_revokes_the_previous_server_session(tmp_path):
    provider = FakeOIDCProvider()
    with pilot_client(tmp_path, provider, lambda: T0) as client:
        assert login_pilot(client, provider).status_code == 200
        first_handle = client.cookies.get("careflow_session")
        assert login_pilot(client, provider).status_code == 200
        second_handle = client.cookies.get("careflow_session")
        assert first_handle != second_handle
        assert client.app.state.store.get_session(first_handle).revoked_at is not None


def test_local_deactivation_and_role_change_are_rechecked_on_each_request(tmp_path):
    provider = FakeOIDCProvider()
    with pilot_client(tmp_path, provider, lambda: T0) as client:
        assert login_pilot(client, provider).status_code == 200
        store = client.app.state.store
        store.add_user(User("local-lab", "Approved Lab", "patient", created_at=T0))
        assert client.get("/auth/current-user").json()["role"] == "patient"
        store.add_user(User("local-lab", "Approved Lab", "patient", is_active=False, created_at=T0))
        assert client.get("/auth/current-user").status_code == 401
