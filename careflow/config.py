"""Typed, fail-closed runtime configuration for CareFlow."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


class ConfigurationError(ValueError):
    """Raised when the selected CareFlow mode is unsafe to start."""


def _env(environ: Mapping[str, str], name: str, default: str = "") -> str:
    return environ.get(name, default).strip()


def _bool(value: str, *, name: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} must be true or false")


@dataclass(frozen=True)
class CareFlowSettings:
    """Validated settings with safe, non-secret diagnostics."""

    mode: str = "demo"
    database_path: Path = Path("careflow.db")
    session_secret: str = ""
    session_max_age: int = 3600
    session_idle_age: int = 3600
    session_absolute_age: int = 28800
    message_provider: str = "rule_based"
    trusted_origins: tuple[str, ...] = ()
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_redirect_uri: str = ""
    synthetic_only: bool = True
    allow_demo_login: bool = True

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "CareFlowSettings":
        env = os.environ if environ is None else environ
        origins = tuple(
            origin.strip().rstrip("/")
            for origin in _env(env, "CAREFLOW_TRUSTED_ORIGINS").split(",")
            if origin.strip()
        )
        raw_age = _env(env, "CAREFLOW_SESSION_MAX_AGE", "3600")
        try:
            session_max_age = int(raw_age)
        except ValueError as exc:
            raise ConfigurationError("CAREFLOW_SESSION_MAX_AGE must be an integer") from exc
        raw_idle_age = _env(env, "CAREFLOW_SESSION_IDLE_AGE", raw_age)
        raw_absolute_age = _env(env, "CAREFLOW_SESSION_ABSOLUTE_AGE", "28800")
        try:
            session_idle_age = int(raw_idle_age)
            session_absolute_age = int(raw_absolute_age)
        except ValueError as exc:
            raise ConfigurationError(
                "CAREFLOW_SESSION_IDLE_AGE and CAREFLOW_SESSION_ABSOLUTE_AGE must be integers"
            ) from exc
        settings = cls(
            mode=_env(env, "CAREFLOW_MODE", "demo").lower(),
            database_path=Path(_env(env, "CAREFLOW_DB_PATH", "careflow.db")),
            session_secret=_env(env, "CAREFLOW_SESSION_SECRET") or _env(env, "SESSION_SECRET"),
            session_max_age=session_max_age,
            session_idle_age=session_idle_age,
            session_absolute_age=session_absolute_age,
            message_provider=_env(env, "CAREFLOW_MESSAGE_PROVIDER", "rule_based").lower(),
            trusted_origins=origins,
            oidc_issuer=_env(env, "CAREFLOW_OIDC_ISSUER"),
            oidc_client_id=_env(env, "CAREFLOW_OIDC_CLIENT_ID"),
            oidc_client_secret=_env(env, "CAREFLOW_OIDC_CLIENT_SECRET"),
            oidc_redirect_uri=_env(env, "CAREFLOW_OIDC_REDIRECT_URI"),
            synthetic_only=_bool(_env(env, "CAREFLOW_SYNTHETIC_ONLY", "true"), name="CAREFLOW_SYNTHETIC_ONLY"),
            allow_demo_login=_bool(_env(env, "CAREFLOW_ALLOW_DEMO_LOGIN", "true"), name="CAREFLOW_ALLOW_DEMO_LOGIN"),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.mode not in {"demo", "pilot"}:
            raise ConfigurationError("CAREFLOW_MODE must be 'demo' or 'pilot'")
        if self.session_max_age <= 0:
            raise ConfigurationError("CAREFLOW_SESSION_MAX_AGE must be positive")
        if self.session_idle_age <= 0:
            raise ConfigurationError("CAREFLOW_SESSION_IDLE_AGE must be positive")
        if self.session_absolute_age <= 0:
            raise ConfigurationError("CAREFLOW_SESSION_ABSOLUTE_AGE must be positive")
        if self.session_idle_age > self.session_absolute_age:
            raise ConfigurationError("CAREFLOW_SESSION_IDLE_AGE must not exceed absolute session age")
        if not self.database_path.name:
            raise ConfigurationError("CAREFLOW_DB_PATH must name a database file")
        if self.message_provider not in {"rule_based", "llm", "rules", "offline", ""}:
            raise ConfigurationError("CAREFLOW_MESSAGE_PROVIDER is unsupported")
        INSECURE_SECRETS = {
            "replace-with-a-long-random-secret",
            "secret",
            "password",
            "changeme",
            "admin",
            "default",
            "test",
        }
        if self.session_secret and self.session_secret.lower() in INSECURE_SECRETS:
            raise ConfigurationError(
                "CAREFLOW_SESSION_SECRET must not use a default placeholder or insecure secret"
            )
        if self.mode == "pilot":
            required = {
                "CAREFLOW_SESSION_SECRET": self.session_secret,
                "CAREFLOW_TRUSTED_ORIGINS": self.trusted_origins,
                "CAREFLOW_OIDC_ISSUER": self.oidc_issuer,
                "CAREFLOW_OIDC_CLIENT_ID": self.oidc_client_id,
                "CAREFLOW_OIDC_CLIENT_SECRET": self.oidc_client_secret,
                "CAREFLOW_OIDC_REDIRECT_URI": self.oidc_redirect_uri,
            }
            missing = [name for name, value in required.items() if not value]
            if missing:
                raise ConfigurationError("pilot mode requires: " + ", ".join(missing))
            if self.allow_demo_login:
                raise ConfigurationError("CAREFLOW_ALLOW_DEMO_LOGIN must be false in pilot mode")
            if not self.synthetic_only:
                raise ConfigurationError("pilot mode must explicitly retain CAREFLOW_SYNTHETIC_ONLY=true")
            if len(self.session_secret) < 16 or self.session_secret.lower() in INSECURE_SECRETS:
                raise ConfigurationError(
                    "pilot mode requires a secure CAREFLOW_SESSION_SECRET of at least 16 characters"
                )

    @property
    def demo_mode(self) -> bool:
        return self.mode == "demo"

    def safe_diagnostics(self) -> dict[str, object]:
        """Return startup/readiness details without secrets or patient data."""

        return {
            "mode": self.mode,
            "synthetic_only": self.synthetic_only,
            "demo_login_enabled": self.allow_demo_login and self.demo_mode,
            "message_provider": self.message_provider or "rule_based",
            "session_max_age": self.session_max_age,
            "session_idle_age": self.session_idle_age,
            "session_absolute_age": self.session_absolute_age,
            "database_path_configured": bool(str(self.database_path)),
            "trusted_origins_configured": bool(self.trusted_origins),
            "oidc_configured": all((
                self.oidc_issuer,
                self.oidc_client_id,
                self.oidc_client_secret,
                self.oidc_redirect_uri,
            )),
        }


__all__ = ["CareFlowSettings", "ConfigurationError"]
