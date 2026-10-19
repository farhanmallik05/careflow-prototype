import pytest
from urllib.parse import parse_qs, urlparse
from careflow.smart_fhir_client import SmartFHIRClient, SmartLaunchContext


def test_smart_fhir_launch_context_and_url():
    client = SmartFHIRClient(
        fhir_base_url="https://fhir.epic.com/interconnect-fhir-oauth/api/FHIR/R4",
        client_id="epic-client-careflow-app",
        redirect_uri="https://careflow.hospital.org/auth/smart/callback",
    )
    context, auth_url = client.create_launch_context(launch="launch-token-12345")

    assert isinstance(context, SmartLaunchContext)
    assert context.client_id == "epic-client-careflow-app"
    assert context.launch_token == "launch-token-12345"
    assert len(context.code_verifier) >= 43
    assert len(context.code_challenge) > 0

    parsed = urlparse(auth_url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "fhir.epic.com"
    params = parse_qs(parsed.query)

    assert params["client_id"] == ["epic-client-careflow-app"]
    assert params["redirect_uri"] == ["https://careflow.hospital.org/auth/smart/callback"]
    assert params["response_type"] == ["code"]
    assert params["code_challenge_method"] == ["S256"]
    assert params["code_challenge"] == [context.code_challenge]
    assert params["launch"] == ["launch-token-12345"]
    assert params["aud"] == ["https://fhir.epic.com/interconnect-fhir-oauth/api/FHIR/R4"]


def test_build_case_from_screening_observation():
    client = SmartFHIRClient()
    patient_resource = {
        "resourceType": "Patient",
        "id": "pat-009944",
        "name": [{"given": ["Eleanor"], "family": "Vance"}],
    }
    observation_resource = {
        "resourceType": "Observation",
        "id": "obs-7788",
        "status": "final",
        "code": {
            "coding": [
                {
                    "system": "http://loinc.org",
                    "code": "24606-6",
                    "display": "Mammogram screening findings abnormal",
                }
            ]
        },
    }

    case_payload = client.build_case_from_screening_observation(
        patient_resource, observation_resource
    )
    assert case_payload["patient_user_id"] == "pat-009944"
    assert case_payload["patient_name"] == "Eleanor Vance"
    assert case_payload["observation_code"] == "24606-6"
    assert "Mammogram" in case_payload["observation_display"]
    assert case_payload["urgent"] is True
