"""SMART on FHIR production client and Epic Hyperspace integration harness."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import secrets
from typing import Any, Mapping
from urllib.parse import urlencode


@dataclass(frozen=True)
class SmartLaunchContext:
    """OAuth 2.0 PKCE launch context for SMART on FHIR transactions."""

    client_id: str
    fhir_base_url: str
    redirect_uri: str
    state: str
    code_verifier: str
    code_challenge: str
    launch_token: str | None = None
    scope: str = (
        "launch launch/patient openid fhirUser patient/Patient.read "
        "patient/Appointment.read patient/Observation.read"
    )


class SmartFHIRClient:
    """Production client implementing SMART on FHIR R4 application launch."""

    def __init__(
        self,
        fhir_base_url: str = "https://fhir.epic.com/interconnect-fhir-oauth/api/FHIR/R4",
        client_id: str = "careflow-epic-client-id",
        redirect_uri: str = "https://careflow.hospital.org/auth/smart/callback",
        auth_endpoint: str | None = None,
        token_endpoint: str | None = None,
    ) -> None:
        self.fhir_base_url = fhir_base_url.rstrip("/")
        self.client_id = client_id
        self.redirect_uri = redirect_uri
        self.auth_endpoint = auth_endpoint or f"{self.fhir_base_url}/oauth2/authorize"
        self.token_endpoint = token_endpoint or f"{self.fhir_base_url}/oauth2/token"

    def create_launch_context(
        self,
        *,
        launch: str | None = None,
        scope: str | None = None,
    ) -> tuple[SmartLaunchContext, str]:
        """Generate PKCE challenge and authorization URL for SMART launch."""
        state = secrets.token_urlsafe(24)
        code_verifier = secrets.token_urlsafe(48)
        digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
        code_challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")

        context = SmartLaunchContext(
            client_id=self.client_id,
            fhir_base_url=self.fhir_base_url,
            redirect_uri=self.redirect_uri,
            state=state,
            code_verifier=code_verifier,
            code_challenge=code_challenge,
            launch_token=launch,
            scope=scope or (
                "launch launch/patient openid fhirUser patient/Patient.read "
                "patient/Appointment.read patient/Observation.read"
            ),
        )

        params: dict[str, str] = {
            "response_type": "code",
            "client_id": context.client_id,
            "redirect_uri": context.redirect_uri,
            "scope": context.scope,
            "state": context.state,
            "code_challenge": context.code_challenge,
            "code_challenge_method": "S256",
            "aud": self.fhir_base_url,
        }
        if launch:
            params["launch"] = launch

        auth_url = f"{self.auth_endpoint}?{urlencode(params)}"
        return context, auth_url

    def build_case_from_screening_observation(
        self,
        patient_resource: Mapping[str, Any],
        observation_resource: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Convert standard FHIR R4 Patient and Observation resources to CareFlow case payload."""
        patient_id = patient_resource.get("id", "UNKNOWN-PATIENT")
        name_entries = patient_resource.get("name", [{}])
        first_name = ""
        family_name = ""
        if name_entries:
            first_name = " ".join(name_entries[0].get("given", []))
            family_name = name_entries[0].get("family", "")
        patient_name = f"{first_name} {family_name}".strip() or "Unnamed Patient"

        # Observation coding
        obs_code = ""
        obs_display = ""
        coding = observation_resource.get("code", {}).get("coding", [{}])
        if coding:
            obs_code = coding[0].get("code", "")
            obs_display = coding[0].get("display", "")

        case_id = f"CF-FHIR-{patient_id[:8]}"
        return {
            "id": case_id,
            "patient_user_id": patient_id,
            "patient_name": patient_name,
            "observation_code": obs_code,
            "observation_display": obs_display,
            "urgent": True,
            "status": "result_issued",
        }


__all__ = ["SmartFHIRClient", "SmartLaunchContext"]
