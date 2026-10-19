from careflow.oidc import OIDCClaims, map_enterprise_claims_to_role


def test_map_azure_ad_supervisor_group():
    claims = {
        "sub": "usr_998811",
        "email": "dr.patel@hospital.org",
        "groups": ["Hospital-Clinical-Supervisors", "All-Medical-Staff"],
    }
    role = map_enterprise_claims_to_role(claims)
    assert role == "clinical_supervisor"


def test_map_okta_specialist_roles():
    claims = {
        "sub": "usr_776655",
        "email": "dr.chen@cancercenter.org",
        "roles": ["Hospital-Specialists", "Surgical-Oncology"],
    }
    role = map_enterprise_claims_to_role(claims)
    assert role == "specialist"


def test_map_navigator_group():
    claims = {
        "sub": "usr_112233",
        "email": "sarah.nav@hospital.org",
        "groups": ["Hospital-Oncology-Navigators"],
    }
    role = map_enterprise_claims_to_role(claims)
    assert role == "care_coordinator"


def test_map_on_call_clinician():
    claims = {
        "sub": "usr_445566",
        "email": "oncall@hospital.org",
        "groups": ["Hospital-On-Call"],
    }
    role = map_enterprise_claims_to_role(claims)
    assert role == "clinician_on_call"


def test_map_oidc_claims_object():
    claims_obj = OIDCClaims(
        issuer="https://login.microsoftonline.com/tenant-123/v2.0",
        subject="entra-sub-001",
        audience="careflow-app",
        expires_at=1800000000,
        issued_at=1700000000,
        nonce="nonce-abc",
        email="sup@hospital.org",
        groups=("ClinicalDirectors",),
    )
    role = map_enterprise_claims_to_role(claims_obj)
    assert role == "clinical_supervisor"


def test_map_fallback_default_role():
    claims = {"sub": "usr_anon", "groups": ["General-Employees"]}
    role = map_enterprise_claims_to_role(claims, default_role="care_coordinator")
    assert role == "care_coordinator"
