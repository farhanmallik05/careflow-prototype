from careflow.nli_evaluator import ClinicalNLIEvaluator


def test_valid_clinical_outreach_entailment():
    evaluator = ClinicalNLIEvaluator()
    case_context = {
        "case_id": "CASE-BR-1",
        "pathway_name": "breast_screening_to_care",
        "patient_name": "Maria Hernandez",
    }
    draft = (
        "Hello Maria, this is your care coordinator following up on your recent screening mammogram. "
        "The radiologist noted an area that needs additional diagnostic imaging. Please call us to schedule "
        "your diagnostic ultrasound or diagnostic mammogram."
    )

    result = evaluator.evaluate(draft, case_context)
    assert not result.hallucination_detected
    assert result.contradiction_score == 0.0
    assert result.entailment_score >= 0.90
    assert result.is_safe_for_automated_dispatch


def test_hallucination_false_reassurance_flagged():
    evaluator = ClinicalNLIEvaluator()
    case_context = {
        "case_id": "CASE-BR-2",
        "pathway_name": "breast_screening_to_care",
    }
    dangerous_draft = (
        "Hi Maria, good news! The doctor reviewed your mammogram and confirmed you definitely do not have cancer. "
        "You are guaranteed healthy and there is nothing to worry about at all."
    )

    result = evaluator.evaluate(dangerous_draft, case_context)
    assert result.hallucination_detected
    assert result.contradiction_score > 0.0
    assert "prohibited_false_reassurance" in result.flags
    assert not result.is_safe_for_automated_dispatch


def test_guideline_contradiction_flagged():
    evaluator = ClinicalNLIEvaluator()
    case_context = {
        "case_id": "CASE-CRC-1",
        "pathway_name": "colorectal_screening_to_care",
    }
    contradictory_draft = (
        "Hello, your stool test showed abnormal blood. Don't worry, just take another repeat stool test next week."
    )

    result = evaluator.evaluate(contradictory_draft, case_context)
    assert result.hallucination_detected
    assert any("contradicts_guideline" in flag for flag in result.flags)
    assert not result.is_safe_for_automated_dispatch


def test_empty_draft_rejection():
    evaluator = ClinicalNLIEvaluator()
    result = evaluator.evaluate("   ", {})
    assert result.hallucination_detected
    assert "empty_draft" in result.flags
