import pytest

from careflow.config import CareFlowSettings, ConfigurationError


def test_demo_settings_are_explicit_and_safe_diagnostics_are_redacted():
    settings = CareFlowSettings.from_env(
        {
            "CAREFLOW_MODE": "demo",
            "CAREFLOW_SESSION_SECRET": "super-secret-that-must-not-return",
        }
    )
    assert settings.mode == "demo"
    assert settings.allow_demo_login is True
    diagnostics = settings.safe_diagnostics()
    assert diagnostics["mode"] == "demo"
    assert "session_secret" not in diagnostics
    assert "super-secret" not in str(diagnostics)


def test_pilot_mode_fails_closed_when_identity_and_trusted_origin_are_missing():
    with pytest.raises(ConfigurationError, match="pilot mode requires"):
        CareFlowSettings.from_env({"CAREFLOW_MODE": "pilot"})


def test_pilot_mode_rejects_demo_login_even_when_other_settings_exist():
    env = {
        "CAREFLOW_MODE": "pilot",
        "CAREFLOW_SESSION_SECRET": "long-secret",
        "CAREFLOW_TRUSTED_ORIGINS": "https://pilot.example.test",
        "CAREFLOW_OIDC_ISSUER": "https://issuer.example.test",
        "CAREFLOW_OIDC_CLIENT_ID": "careflow",
        "CAREFLOW_OIDC_CLIENT_SECRET": "client-secret",
        "CAREFLOW_OIDC_REDIRECT_URI": "https://pilot.example.test/auth/callback",
        "CAREFLOW_ALLOW_DEMO_LOGIN": "true",
    }
    with pytest.raises(ConfigurationError, match="ALLOW_DEMO_LOGIN"):
        CareFlowSettings.from_env(env)


def test_invalid_boolean_and_mode_are_rejected():
    with pytest.raises(ConfigurationError, match="must be true or false"):
        CareFlowSettings.from_env({"CAREFLOW_SYNTHETIC_ONLY": "maybe"})
    with pytest.raises(ConfigurationError, match="must be 'demo' or 'pilot'"):
        CareFlowSettings.from_env({"CAREFLOW_MODE": "unsafe"})


def test_insecure_or_placeholder_session_secret_rejected():
    with pytest.raises(ConfigurationError, match="default placeholder or insecure secret"):
        CareFlowSettings.from_env({"CAREFLOW_SESSION_SECRET": "replace-with-a-long-random-secret"})
    with pytest.raises(ConfigurationError, match="default placeholder or insecure secret"):
        CareFlowSettings.from_env({"CAREFLOW_SESSION_SECRET": "password"})

