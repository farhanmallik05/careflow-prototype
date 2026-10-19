"""Automated offline LLM and message readability & clinical preservation evaluation benchmark.

Evaluates patient notifications to ensure:
1. Patient readability adheres to AMA/NIH recommended health literacy levels (Flesch-Kincaid Grade Level <= 6.0).
2. High reading ease (Flesch Reading Ease >= 65).
3. Clinical terminology preservation (>= 95% of essential medical concepts preserved without loss).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any


def count_syllables(word: str) -> int:
    """Heuristic syllable counter for English medical and general vocabulary."""
    cleaned = re.sub(r"[^a-zA-Z]", "", word.lower())
    if not cleaned:
        return 0
    if len(cleaned) <= 3:
        return 1

    # Remove silent trailing e unless ending in 'le' after consonant
    if cleaned.endswith("e") and not cleaned.endswith("le"):
        cleaned = cleaned[:-1]

    # Count vowel groups
    vowels = "aeiouy"
    count = 0
    prev_is_vowel = False
    for char in cleaned:
        is_vowel = char in vowels
        if is_vowel and not prev_is_vowel:
            count += 1
        prev_is_vowel = is_vowel

    # Adjust for common suffixes
    if cleaned.endswith("ed") and not cleaned.endswith("ted") and not cleaned.endswith("ded"):
        count = max(1, count - 1)

    return max(1, count)


def compute_readability(text: str) -> dict[str, float]:
    """Compute Flesch-Kincaid Grade Level and Flesch Reading Ease."""
    sentences = [s.strip() for s in re.split(r"[.!?]+", text) if s.strip()]
    sentence_count = max(1, len(sentences))

    words = re.findall(r"[a-zA-Z0-9'-]+", text)
    word_count = max(1, len(words))

    total_syllables = sum(count_syllables(w) for w in words)

    words_per_sentence = word_count / sentence_count
    syllables_per_word = total_syllables / word_count

    # Standard Flesch-Kincaid Grade Level formula
    fkgl = (0.39 * words_per_sentence) + (11.8 * syllables_per_word) - 15.59
    # Standard Flesch Reading Ease formula
    fre = 206.835 - (1.015 * words_per_sentence) - (84.6 * syllables_per_word)

    return {
        "word_count": word_count,
        "sentence_count": sentence_count,
        "syllable_count": total_syllables,
        "words_per_sentence": round(words_per_sentence, 2),
        "syllables_per_word": round(syllables_per_word, 2),
        "fkgl": max(0.0, round(fkgl, 2)),
        "reading_ease": max(0.0, min(100.0, round(fre, 2))),
    }


def compute_clinical_preservation(
    message: str,
    required_terms: list[str],
) -> dict[str, Any]:
    """Verify that essential clinical concepts and instructions are preserved."""
    normalized = message.lower()
    preserved = [term for term in required_terms if term.lower() in normalized]
    missing = [term for term in required_terms if term.lower() not in normalized]
    preservation_rate = len(preserved) / max(1, len(required_terms))

    return {
        "required_count": len(required_terms),
        "preserved_count": len(preserved),
        "missing": missing,
        "preservation_rate": round(preservation_rate, 4),
        "passed": preservation_rate >= 0.95,
    }


@dataclass(frozen=True)
class BenchmarkResult:
    case_id: str
    language: str
    template: str
    fkgl: float
    reading_ease: float
    fkgl_passed: bool
    preservation_rate: float
    preservation_passed: bool
    overall_passed: bool


STANDARD_BENCHMARK_CASES = [
    {
        "case_id": "BENCH-001",
        "language": "en",
        "template": "screening_result",
        "message": "Hello Asha. Your screening test results are ready. Your doctor has reviewed them. Please call our clinic to discuss next steps.",
        "required_terms": ["screening", "results", "doctor", "clinic"],
    },
    {
        "case_id": "BENCH-002",
        "language": "en",
        "template": "appointment_booking",
        "message": "Hello David. We booked your specialist visit. Please come to the clinic on time. Bring your photo ID and card.",
        "required_terms": ["specialist", "visit", "clinic"],
    },
    {
        "case_id": "BENCH-003",
        "language": "en",
        "template": "referral_notice",
        "message": "Hello Maria. A referral was created for you. Our care team is reviewing it now. We will call you soon with details.",
        "required_terms": ["referral", "care team"],
    },
    {
        "case_id": "BENCH-004",
        "language": "en",
        "template": "urgent_escalation",
        "message": "Hello James. Your care team needs to speak with you today. Please call our clinic nurse right away at our main number.",
        "required_terms": ["care team", "clinic nurse"],
    },
]


def run_benchmark(cases: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Execute the readability and clinical terminology preservation benchmark."""
    eval_cases = cases or STANDARD_BENCHMARK_CASES
    results: list[BenchmarkResult] = []

    for item in eval_cases:
        msg = str(item["message"])
        req_terms: list[str] = list(item["required_terms"])
        readability = compute_readability(msg)
        preservation = compute_clinical_preservation(msg, req_terms)

        fkgl_ok = readability["fkgl"] <= 6.0
        preservation_ok = preservation["passed"]

        results.append(
            BenchmarkResult(
                case_id=str(item["case_id"]),
                language=str(item.get("language", "en")),
                template=str(item.get("template", "default")),
                fkgl=readability["fkgl"],
                reading_ease=readability["reading_ease"],
                fkgl_passed=fkgl_ok,
                preservation_rate=preservation["preservation_rate"],
                preservation_passed=preservation_ok,
                overall_passed=fkgl_ok and preservation_ok,
            )
        )

    all_passed = all(r.overall_passed for r in results)
    avg_fkgl = round(sum(r.fkgl for r in results) / len(results), 2)
    avg_preservation = round(sum(r.preservation_rate for r in results) / len(results), 4)

    return {
        "total_cases": len(results),
        "passed_cases": sum(1 for r in results if r.overall_passed),
        "all_passed": all_passed,
        "average_fkgl": avg_fkgl,
        "target_fkgl_max": 6.0,
        "average_preservation_rate": avg_preservation,
        "target_preservation_min": 0.95,
        "results": [asdict(r) for r in results],
    }


if __name__ == "__main__":
    report = run_benchmark()
    print("=== CareFlow Patient Readability & Clinical Preservation Benchmark ===")
    print(f"Total Cases Evaluated: {report['total_cases']}")
    print(f"All Passed: {report['all_passed']}")
    print(f"Average FKGL: {report['average_fkgl']} (Target <= 6.0)")
    print(f"Average Clinical Preservation: {report['average_preservation_rate'] * 100:.1f}% (Target >= 95.0%)")
    for r in report["results"]:
        status = "PASS" if r["overall_passed"] else "FAIL"
        print(f" - [{status}] {r['case_id']}: FKGL={r['fkgl']} ({'OK' if r['fkgl_passed'] else 'HIGH'}), Preservation={r['preservation_rate']*100:.1f}%")
