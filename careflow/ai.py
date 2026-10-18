"""Safe provider boundary for patient message drafting.

The provider receives a read-only case context and returns message content.  It
does not receive a store or a :class:`~careflow.engine.Case` instance, so a
provider cannot transition a case, close it, or otherwise mutate engine state.
The default provider is deliberately deterministic and offline.  An optional
LLM boundary is available only when ``CAREFLOW_MESSAGE_PROVIDER=llm`` is set;
this prototype does not make a network request or require an API key for it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
import os
import re
from typing import Any, Mapping


REVIEW_FLAG = "needs clinical-language review"
DEFAULT_TEMPLATE = "care_update"
SUPPORTED_LANGUAGES = frozenset({"en", "hi", "bn"})
LANGUAGE_ALIASES = {
    "en": "en",
    "eng": "en",
    "english": "en",
    "hi": "hi",
    "hin": "hi",
    "hindi": "hi",
    "bn": "bn",
    "ben": "bn",
    "bengali": "bn",
}
SUPPORTED_TEMPLATES = frozenset({DEFAULT_TEMPLATE})


class ProviderError(RuntimeError):
    """Base error raised when a message provider cannot produce a draft."""


class ProviderUnavailableError(ProviderError):
    """Raised when an optional provider is selected but not configured."""


def normalize_language(language: str) -> str:
    """Return a supported language code or raise a clear input error."""

    normalized = language.strip().lower().replace("_", "-")
    normalized = normalized.split("-", 1)[0]
    try:
        return LANGUAGE_ALIASES[normalized]
    except KeyError:
        supported = ", ".join(sorted(SUPPORTED_LANGUAGES))
        raise ValueError(f"language must be one of: {supported}") from None


def normalize_template(template: str) -> str:
    """Return the supported template id or raise a clear input error."""

    normalized = template.strip().lower().replace("-", "_")
    if normalized not in SUPPORTED_TEMPLATES:
        supported = ", ".join(sorted(SUPPORTED_TEMPLATES))
        raise ValueError(f"template must be one of: {supported}")
    return normalized


@dataclass(frozen=True)
class DraftContent:
    """Provider output before it is persisted as an approval-controlled draft."""

    message: str
    language: str
    template: str
    provider: str
    review_required: bool
    review_flag: str | None


class AIProvider(ABC):
    """Read-only interface implemented by patient-message providers.

    ``context`` is a plain mapping copied from persisted case data.  Providers
    must return content only; persistence, consent, approvals, notifications,
    and all engine mutations remain outside this interface.
    """

    name = "provider"

    @abstractmethod
    def draft(
        self,
        context: Mapping[str, Any],
        *,
        language: str = "en",
        template: str = DEFAULT_TEMPLATE,
    ) -> DraftContent:
        """Create a patient-facing draft without changing application state."""

    def generate(
        self,
        context: Mapping[str, Any],
        *,
        language: str = "en",
        template: str = DEFAULT_TEMPLATE,
    ) -> DraftContent:
        """Compatibility spelling for callers that use ``generate``."""

        return self.draft(context, language=language, template=template)


class RuleBasedProvider(AIProvider):
    """Deterministic, offline provider backed by reviewed message templates."""

    name = "rule_based"

    TEMPLATES: Mapping[str, Mapping[str, str]] = {
        "en": {
            DEFAULT_TEMPLATE: (
                "Hello {patient_name}. Your care team has an update about your care journey. "
                "Please contact your care team for the next step. This message is for information only."
            )
        },
        "hi": {
            DEFAULT_TEMPLATE: (
                "नमस्ते {patient_name}। आपकी देखभाल टीम के पास आपकी देखभाल यात्रा के बारे में एक अपडेट है। "
                "अगले कदम के लिए कृपया अपनी देखभाल टीम से संपर्क करें। यह संदेश केवल जानकारी के लिए है।"
            )
        },
        "bn": {
            DEFAULT_TEMPLATE: (
                "হ্যালো {patient_name}। আপনার যত্ন দলের কাছে আপনার যত্নের যাত্রা সম্পর্কে একটি আপডেট আছে। "
                "পরবর্তী ধাপের জন্য আপনার যত্ন দলের সঙ্গে যোগাযোগ করুন। এই বার্তাটি শুধু তথ্যের জন্য।"
            )
        },
    }

    @staticmethod
    def _patient_name(context: Mapping[str, Any]) -> str:
        value = str(context.get("patient_name") or "there").strip()
        # Keep a patient-provided name plain and on one line in every channel.
        return re.sub(r"\s+", " ", value) or "there"

    def draft(
        self,
        context: Mapping[str, Any],
        *,
        language: str = "en",
        template: str = DEFAULT_TEMPLATE,
    ) -> DraftContent:
        normalized_language = normalize_language(language)
        normalized_template = normalize_template(template)
        try:
            template_text = self.TEMPLATES[normalized_language][normalized_template]
        except KeyError as exc:  # pragma: no cover - protects future template additions
            raise ProviderError("the selected language/template is not available") from exc

        review_required = normalized_language != "en"
        return DraftContent(
            message=template_text.format(patient_name=self._patient_name(context)),
            language=normalized_language,
            template=normalized_template,
            provider=self.name,
            review_required=review_required,
            review_flag=REVIEW_FLAG if review_required else None,
        )


class LLMProvider(AIProvider):
    """Optional provider boundary for a future configured LLM integration.

    The prototype intentionally has no network client.  Tests and a future
    integration may inject a callable, while normal application construction
    with ``CAREFLOW_MESSAGE_PROVIDER=llm`` fails clearly instead of silently
    making an external request.
    """

    name = "llm"

    def __init__(self, generator: Any | None = None) -> None:
        self._generator = generator

    def draft(
        self,
        context: Mapping[str, Any],
        *,
        language: str = "en",
        template: str = DEFAULT_TEMPLATE,
    ) -> DraftContent:
        if self._generator is None:
            raise ProviderUnavailableError(
                "the optional LLM message provider is not configured; use CAREFLOW_MESSAGE_PROVIDER=rule_based"
            )
        result = self._generator(context, language=language, template=template)
        if not isinstance(result, DraftContent):
            raise ProviderError("the configured LLM provider returned invalid draft content")
        return result


def create_message_provider() -> AIProvider:
    """Build the provider selected by configuration, defaulting to offline rules."""

    configured = os.getenv("CAREFLOW_MESSAGE_PROVIDER", "rule_based").strip().lower()
    if configured in {"", "rule_based", "rule-based", "rules", "offline"}:
        return RuleBasedProvider()
    if configured in {"llm", "language_model", "language-model"}:
        return LLMProvider()
    supported = "rule_based, llm"
    raise ValueError(f"CAREFLOW_MESSAGE_PROVIDER must be one of: {supported}")


__all__ = [
    "AIProvider",
    "DEFAULT_TEMPLATE",
    "DraftContent",
    "LLMProvider",
    "ProviderError",
    "ProviderUnavailableError",
    "REVIEW_FLAG",
    "RuleBasedProvider",
    "SUPPORTED_LANGUAGES",
    "SUPPORTED_TEMPLATES",
    "create_message_provider",
    "normalize_language",
    "normalize_template",
]
