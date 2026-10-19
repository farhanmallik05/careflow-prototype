from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_build_step_docs_exist_and_cover_the_demo_contract():
    required_markers = {
        "README.md": ("requirements.lock", "POST /auth/login", "notification_outbox", "no message is sent"),
        "docs/architecture.md": ("Request and mutation flow", "audit_events", "consent-gated"),
        "docs/safety.md": ("Simulated-only scope", "Human-governed escalation", "synthetic"),
        "docs/demo-script.md": ("JUDGE-001", "HTTP 422", "escalated", '"state":"closed"'),
    }

    for relative_path, markers in required_markers.items():
        document = ROOT / relative_path
        assert document.is_file(), relative_path
        text = document.read_text(encoding="utf-8")
        assert all(marker in text for marker in markers), relative_path
