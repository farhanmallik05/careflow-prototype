"""Provider-neutral OIDC boundary and deterministic offline provider.

CareFlow owns the local authorization transaction and identity-link decision,
but never owns a password or treats an email address as an identity key.  A
deployment can provide another implementation of :class:`OIDCProvider`; the
fake implementation below is deliberately deterministic and performs the same
authorization-code, PKCE, JWT, issuer, audience, nonce, expiry, and key checks
without making a network request.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from typing import Any, Mapping, Protocol
from urllib.parse import urlencode


class OIDCValidationError(ValueError):
    """Raised when an OIDC transaction or verified token is invalid."""


class OIDCProviderUnavailable(RuntimeError):
    """Raised when pilot OIDC has not been connected to a provider adapter."""


@dataclass(frozen=True)
class OIDCClaims:
    """Minimum claims required to establish a CareFlow local session."""

    issuer: str
    subject: str
    audience: str
    expires_at: int
    issued_at: int
    nonce: str
    email: str | None = None
    name: str | None = None
    groups: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()


def map_enterprise_claims_to_role(
    claims: OIDCClaims | Mapping[str, Any], default_role: str = "care_coordinator"
) -> str:
    """Map enterprise identity provider group and role claims to CareFlow clinical roles."""
    if isinstance(claims, OIDCClaims):
        groups = {str(g).lower() for g in claims.groups}
        roles = {str(r).lower() for r in claims.roles}
    else:
        raw_groups = claims.get("groups") or claims.get("group") or []
        if isinstance(raw_groups, str):
            raw_groups = [raw_groups]
        groups = {str(g).lower() for g in raw_groups}
        raw_roles = claims.get("roles") or claims.get("role") or []
        if isinstance(raw_roles, str):
            raw_roles = [raw_roles]
        roles = {str(r).lower() for r in raw_roles}

    combined = groups | roles

    # High-privilege clinical supervisor
    supervisor_keywords = {
        "clinicalsupervisor", "clinical_supervisor", "clinicaldirectors",
        "hospital-clinical-supervisors", "chiefmedicalofficer", "supervisor"
    }
    if combined & supervisor_keywords or any("supervisor" in x for x in combined):
        return "clinical_supervisor"

    # Clinician on call
    on_call_keywords = {
        "oncallclinicians", "clinician_on_call", "hospital-on-call",
        "physicianoncall", "on_call"
    }
    if combined & on_call_keywords or any("on_call" in x or "on-call" in x for x in combined):
        return "clinician_on_call"

    # Care Coordinator / Navigator
    coordinator_keywords = {
        "navigators", "carecoordinators", "care_coordinator",
        "hospital-oncology-navigators", "navigator", "coordinator", "navigation"
    }
    if combined & coordinator_keywords or any("navigator" in x or "coordinator" in x for x in combined):
        return "care_coordinator"

    # Specialist (Oncology, Surgery, Radiology, etc.)
    specialist_keywords = {
        "specialists", "specialist", "oncologists", "surgeons",
        "hospital-specialists", "oncology"
    }
    if combined & specialist_keywords or any("specialist" in x or "oncolog" in x or "surgeon" in x for x in combined):
        return "specialist"

    # Primary Care
    pcp_keywords = {
        "primarycare", "primary_care", "pcp", "familymedicine", "internalmedicine"
    }
    if combined & pcp_keywords or any("primary" in x for x in combined):
        return "primary_care"

    # Patient
    if "patient" in combined:
        return "patient"

    return default_role


class OIDCProvider(Protocol):
    """Adapter contract for a managed OIDC provider."""

    issuer: str

    def authorization_url(
        self,
        *,
        client_id: str,
        redirect_uri: str,
        state: str,
        nonce: str,
        code_challenge: str,
    ) -> str:
        ...

    def exchange_code(
        self,
        *,
        code: str,
        client_id: str,
        redirect_uri: str,
        code_verifier: str,
    ) -> str:
        ...

    def verify_id_token(
        self,
        token: str,
        *,
        client_id: str,
        nonce: str,
        now: int,
    ) -> OIDCClaims:
        ...


class UnavailableOIDCProvider:
    """Safe default until deployment code supplies a real provider adapter."""

    issuer = ""

    def authorization_url(self, **_: Any) -> str:
        raise OIDCProviderUnavailable("OIDC provider adapter is not configured")

    def exchange_code(self, **_: Any) -> str:
        raise OIDCProviderUnavailable("OIDC provider adapter is not configured")

    def verify_id_token(self, *_: Any, **__: Any) -> OIDCClaims:
        raise OIDCProviderUnavailable("OIDC provider adapter is not configured")


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, TypeError) as exc:
        raise OIDCValidationError("malformed token encoding") from exc


def _json_segment(value: Mapping[str, Any]) -> str:
    return _b64encode(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def pkce_challenge(code_verifier: str) -> str:
    if not code_verifier or len(code_verifier) < 43:
        raise OIDCValidationError("invalid PKCE verifier")
    return _b64encode(hashlib.sha256(code_verifier.encode("ascii")).digest())


class FakeOIDCProvider:
    """Offline OIDC provider used by tests and local pilot demonstrations."""

    def __init__(
        self,
        *,
        issuer: str = "https://issuer.example.test",
        client_id: str = "careflow-test-client",
        redirect_uri: str = "https://careflow.example.test/auth/callback",
        keys: Mapping[str, bytes] | None = None,
    ) -> None:
        self.issuer = issuer
        self.client_id = client_id
        self.redirect_uri = redirect_uri
        self.keys = dict(keys or {"test-key": b"fake-oidc-signing-key"})
        self.authorization_requests: list[dict[str, str]] = []
        self._codes: dict[str, dict[str, Any]] = {}

    def authorization_url(
        self,
        *,
        client_id: str,
        redirect_uri: str,
        state: str,
        nonce: str,
        code_challenge: str,
    ) -> str:
        if client_id != self.client_id or redirect_uri != self.redirect_uri:
            raise OIDCValidationError("OIDC client or redirect URI mismatch")
        request = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
        }
        self.authorization_requests.append(request)
        return "https://fake-oidc.example.test/authorize?" + urlencode(request)

    def issue_authorization_code(
        self,
        *,
        subject: str,
        nonce: str,
        code_challenge: str,
        email: str | None = None,
        name: str | None = None,
        issuer: str | None = None,
        audience: str | list[str] | None = None,
        expires_at: int | None = None,
        kid: str = "test-key",
        signature_key: str | None = None,
        malformed_claims: Mapping[str, Any] | None = None,
    ) -> str:
        expires_at = expires_at if expires_at is not None else 4_000_000_000
        code = "code-" + secrets.token_urlsafe(18)
        self._codes[code] = {
            "subject": subject,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "email": email,
            "name": name,
            "issuer": issuer or self.issuer,
            "audience": audience or self.client_id,
            "expires_at": expires_at,
            "kid": kid,
            "signature_key": signature_key,
            "malformed_claims": dict(malformed_claims or {}),
        }
        return code

    def issue_code_for_request(
        self,
        *,
        subject: str,
        email: str | None = None,
        name: str | None = None,
        **overrides: Any,
    ) -> str:
        if not self.authorization_requests:
            raise OIDCValidationError("no authorization request is pending")
        request = self.authorization_requests[-1]
        return self.issue_authorization_code(
            subject=subject,
            nonce=request["nonce"],
            code_challenge=request["code_challenge"],
            email=email,
            name=name,
            **overrides,
        )

    def exchange_code(
        self,
        *,
        code: str,
        client_id: str,
        redirect_uri: str,
        code_verifier: str,
    ) -> str:
        if client_id != self.client_id or redirect_uri != self.redirect_uri:
            raise OIDCValidationError("authorization request binding failed")
        details = self._codes.pop(code, None)
        if details is None:
            raise OIDCValidationError("invalid or already-used authorization code")
        try:
            challenge = pkce_challenge(code_verifier)
        except (UnicodeEncodeError, OIDCValidationError) as exc:
            raise OIDCValidationError("invalid PKCE verifier") from exc
        if not hmac.compare_digest(challenge, details["code_challenge"]):
            raise OIDCValidationError("PKCE verification failed")
        return self._sign_claims(details)

    def _sign_claims(self, details: Mapping[str, Any]) -> str:
        header = {"alg": "HS256", "kid": details["kid"], "typ": "JWT"}
        claims: dict[str, Any] = {
            "iss": details["issuer"],
            "sub": details["subject"],
            "aud": details["audience"],
            "exp": details["expires_at"],
            "iat": 0,
            "nonce": details["nonce"],
        }
        if details.get("email") is not None:
            claims["email"] = details["email"]
        if details.get("name") is not None:
            claims["name"] = details["name"]
        claims.update(details.get("malformed_claims", {}))
        encoded_header = _json_segment(header)
        encoded_claims = _json_segment(claims)
        signing_input = f"{encoded_header}.{encoded_claims}".encode("ascii")
        key = self.keys.get(str(details.get("signature_key") or details["kid"]))
        signature = b"invalid-key"
        if key is not None:
            signature = hmac.new(key, signing_input, hashlib.sha256).digest()
        return f"{encoded_header}.{encoded_claims}.{_b64encode(signature)}"

    def verify_id_token(
        self,
        token: str,
        *,
        client_id: str,
        nonce: str,
        now: int,
    ) -> OIDCClaims:
        parts = token.split(".")
        if len(parts) != 3:
            raise OIDCValidationError("malformed ID token")
        try:
            header = json.loads(_b64decode(parts[0]))
            claims = json.loads(_b64decode(parts[1]))
            signature = _b64decode(parts[2])
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise OIDCValidationError("malformed ID token") from exc
        if header.get("alg") != "HS256" or not isinstance(header.get("kid"), str):
            raise OIDCValidationError("unsupported ID token algorithm or key")
        key = self.keys.get(header["kid"])
        if key is None:
            raise OIDCValidationError("unknown ID token key")
        expected = hmac.new(
            key, f"{parts[0]}.{parts[1]}".encode("ascii"), hashlib.sha256
        ).digest()
        if not hmac.compare_digest(expected, signature):
            raise OIDCValidationError("invalid ID token signature")
        if claims.get("iss") != self.issuer:
            raise OIDCValidationError("invalid ID token issuer")
        audience = claims.get("aud")
        if audience != client_id and not (
            isinstance(audience, list) and client_id in audience
        ):
            raise OIDCValidationError("invalid ID token audience")
        if not isinstance(claims.get("sub"), str) or not claims["sub"].strip():
            raise OIDCValidationError("required subject claim is missing")
        if not isinstance(claims.get("nonce"), str) or not hmac.compare_digest(
            claims["nonce"], nonce
        ):
            raise OIDCValidationError("invalid ID token nonce")
        if not isinstance(claims.get("exp"), int) or claims["exp"] <= now:
            raise OIDCValidationError("ID token is expired")
        if not isinstance(claims.get("iat"), int) or claims["iat"] > now:
            raise OIDCValidationError("invalid ID token issued-at claim")
        return OIDCClaims(
            issuer=claims["iss"],
            subject=claims["sub"],
            audience=client_id,
            expires_at=claims["exp"],
            issued_at=claims["iat"],
            nonce=claims["nonce"],
            email=claims.get("email") if isinstance(claims.get("email"), str) else None,
            name=claims.get("name") if isinstance(claims.get("name"), str) else None,
        )


__all__ = [
    "FakeOIDCProvider",
    "OIDCClaims",
    "OIDCProvider",
    "OIDCProviderUnavailable",
    "OIDCValidationError",
    "UnavailableOIDCProvider",
    "pkce_challenge",
]
