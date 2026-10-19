"""Unit and benchmark tests for patient message readability and clinical terminology preservation."""

import pytest

from careflow.eval_benchmark import (
    compute_clinical_preservation,
    compute_readability,
    count_syllables,
    run_benchmark,
)


def test_count_syllables():
    assert count_syllables("test") == 1
    assert count_syllables("doctor") == 2
    assert count_syllables("screening") == 2
    assert count_syllables("referral") == 3
    assert count_syllables("coordination") >= 4


def test_compute_readability_metrics():
    # Simple plain-language sentence at <= 6th grade level
    simple_text = "Hello John. Your screening result is ready. Please call our clinic."
    metrics = compute_readability(simple_text)
    assert metrics["word_count"] > 0
    assert metrics["sentence_count"] == 3
    assert metrics["fkgl"] <= 6.0
    assert metrics["reading_ease"] >= 65.0


def test_clinical_preservation_accuracy():
    message = "Your screening report is complete. Please consult your primary care doctor."
    result = compute_clinical_preservation(message, ["screening", "doctor", "primary care"])
    assert result["passed"] is True
    assert result["preservation_rate"] == 1.0

    missing_result = compute_clinical_preservation(message, ["mammogram", "biopsy", "urgent"])
    assert missing_result["passed"] is False
    assert missing_result["preservation_rate"] == 0.0


def test_full_benchmark_passes_all_targets():
    report = run_benchmark()
    assert report["all_passed"] is True
    assert report["average_fkgl"] <= 6.0
    assert report["average_preservation_rate"] >= 0.95
    assert len(report["results"]) >= 4
