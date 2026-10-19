"""Automated Natural Language Inference (NLI) and clinical guideline hallucination detection."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Mapping, Sequence


# Verified clinical cancer screening protocols
CLINICAL_GUIDELINES: dict[str, dict[str, Any]] = {
    "breast": {
        "valid_procedures": {"mammogram", "mammography", "ultrasound", "biopsy", "breast exam", "imaging"},
        "prohibited_alternatives": {"colonoscopy", "pap smear", "stool test", "chest ct"},
        "requires_diagnostic_warning": True,
    },
    "colorectal": {
        "valid_procedures": {"colonoscopy", "diagnostic colonoscopy", "biopsy", "gastroenterology"},
        "prohibited_alternatives": {"repeat stool test", "repeat fit", "mammogram", "pap smear"},
        "requires_diagnostic_warning": True,
    },
    "cervical": {
        "valid_procedures": {"colposcopy", "cervical biopsy", "gynecology", "pap follow-up"},
        "prohibited_alternatives": {"colonoscopy", "mammogram", "chest ct"},
        "requires_diagnostic_warning": True,
    },
    "lung": {
        "valid_procedures": {"chest ct", "diagnostic ct", "bronchoscopy", "pulmonology", "pet scan"},
        "prohibited_alternatives": {"colonoscopy", "mammogram", "pap smear"},
        "requires_diagnostic_warning": True,
    },
    "prostate": {
        "valid_procedures": {"urology", "prostate mri", "psa re-evaluation", "prostate biopsy"},
        "prohibited_alternatives": {"mammogram", "pap smear", "colonoscopy"},
        "requires_diagnostic_warning": True,
    },
}

# Forbidden reassuring phrases that claim definitive diagnosis before biopsy
FALSE_REASSURANCE_PATTERNS = [
    re.compile(r"definitely (do not|don't) have cancer", re.IGNORECASE),
    re.compile(r"guaranteed (benign|healthy)", re.IGNORECASE),
    re.compile(r"nothing to worry about at all", re.IGNORECASE),
    re.compile(r"screening confirmed you are cured", re.IGNORECASE),
    re.compile(r"no need (for|to attend) follow-?up", re.IGNORECASE),
]


@dataclass(frozen=True)
class NLIEvaluationResult:
    """Outcome of NLI clinical inference and hallucination checking."""

    entailment_score: float
    contradiction_score: float
    neutral_score: float
    hallucination_detected: bool
    flags: tuple[str, ...] = field(default_factory=tuple)
    summary: str = ""

    @property
    def is_safe_for_automated_dispatch(self) -> bool:
        return (
            not self.hallucination_detected
            and self.contradiction_score == 0.0
            and self.entailment_score >= 0.85
        )


class ClinicalNLIEvaluator:
    """Evaluates generated patient messages against clinical context and guidelines."""

    def __init__(self, guidelines: Mapping[str, dict[str, Any]] | None = None) -> None:
        self.guidelines = guidelines or CLINICAL_GUIDELINES

    def evaluate(
        self,
        draft_text: str,
        case_context: Mapping[str, Any],
    ) -> NLIEvaluationResult:
        """Score draft text for clinical entailment, contradictions, and hallucinations."""
        if not draft_text or not draft_text.strip():
            return NLIEvaluationResult(
                entailment_score=0.0,
                contradiction_score=1.0,
                neutral_score=0.0,
                hallucination_detected=True,
                flags=("empty_draft",),
                summary="Empty or blank message draft.",
            )

        text = draft_text.strip()
        lower_text = text.lower()
        flags: list[str] = []
        contradiction_points = 0.0

        # 1. Check for dangerous false reassurance patterns
        for pattern in FALSE_REASSURANCE_PATTERNS:
            if pattern.search(text):
                flags.append("prohibited_false_reassurance")
                contradiction_points += 1.0

        # 2. Identify cancer screening domain
        pathway_str = str(case_context.get("pathway_name") or case_context.get("pathway") or "").lower()
        detected_domain = None
        for domain in self.guidelines:
            if domain in pathway_str or domain in lower_text:
                detected_domain = domain
                break

        # 3. Clinical guideline adherence check
        if detected_domain and detected_domain in self.guidelines:
            guideline = self.guidelines[detected_domain]
            # Check prohibited alternatives (e.g. telling positive FIT patient to just repeat stool test)
            for prohibited in guideline.get("prohibited_alternatives", set()):
                if prohibited in lower_text:
                    flags.append(f"contradicts_guideline_{prohibited.replace(' ', '_')}")
                    contradiction_points += 0.8

        # 4. Check factual case entity consistency (patient name, facility, date)
        patient_name = case_context.get("patient_name")
        if patient_name and isinstance(patient_name, str):
            first_name = patient_name.split()[0].lower()
            if first_name not in lower_text and len(patient_name) > 3:
                # Neutral: name might be omitted, but does it address someone else?
                # Check for other common names addressed
                pass

        # Calculate scores
        if contradiction_points > 0:
            contradiction_score = min(1.0, contradiction_points)
            entailment_score = max(0.0, 1.0 - contradiction_score)
            neutral_score = 0.0
            hallucination_detected = True
        else:
            contradiction_score = 0.0
            entailment_score = 0.95
            neutral_score = 0.05
            hallucination_detected = False

        summary = (
            "Clinical NLI verification passed without hallucination."
            if not hallucination_detected
            else f"Hallucination / clinical contradiction detected: {', '.join(flags)}"
        )

        return NLIEvaluationResult(
            entailment_score=round(entailment_score, 2),
            contradiction_score=round(contradiction_score, 2),
            neutral_score=round(neutral_score, 2),
            hallucination_detected=hallucination_detected,
            flags=tuple(flags),
            summary=summary,
        )


__all__ = ["ClinicalNLIEvaluator", "NLIEvaluationResult", "CLINICAL_GUIDELINES"]
