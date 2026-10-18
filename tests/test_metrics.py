from datetime import datetime, timedelta
from pathlib import Path

from careflow.engine import Case, Pathway
from careflow.metrics import compute_metrics
from careflow.persistence import SQLiteStore


PATHWAY = Pathway.load(Path(__file__).parent.parent / "careflow" / "pathways" / "screening_to_care.yaml")
T0 = datetime(2026, 11, 1, 9)


def at(hour: int) -> datetime:
    return T0 + timedelta(hours=hour)


def transition(case: Case, action: str, hour: int, evidence: tuple[str, ...] = (), approval=None) -> None:
    case.transition(action, "audit-test", at(hour), evidence=evidence, approval=approval)


def persist_and_reload(store: SQLiteStore, *cases: Case) -> list[Case]:
    for case in cases:
        store.save_case(case)
    return [store.load_case(case.id, PATHWAY) for case in cases]


def test_metrics_use_persisted_audits_for_durations_rates_and_samples():
    first = Case("FIRST", PATHWAY, T0)
    transition(first, "notify", 2)
    transition(first, "acknowledge", 5, ("ack_record",))
    transition(first, "review", 10, ("clinical_review",))
    transition(first, "accept", 16, ("acceptance",))

    second = Case("SECOND", PATHWAY, T0)
    transition(second, "notify", 3)

    third = Case("THIRD", PATHWAY, T0)
    transition(third, "notify", 1)
    transition(third, "acknowledge", 2, ("ack_record",))
    transition(third, "review", 4, ("clinical_review",))

    with SQLiteStore(":memory:") as store:
        loaded = persist_and_reload(store, first, second, third)
        metrics = compute_metrics(loaded, PATHWAY, at(5))

    assert metrics["time_to_notification_hours"] == 2.0
    assert metrics["time_to_notification_sample_size"] == 3
    assert metrics["observed_metrics"]["time_to_notification"]["median_hours"] == 2.0
    assert metrics["acknowledgement_rate"] == 2 / 3
    assert metrics["acknowledgement_numerator"] == 2
    assert metrics["acknowledgement_denominator"] == 3
    assert metrics["time_to_referral_acceptance_hours"] == 6.0
    assert metrics["time_to_referral_acceptance_sample_size"] == 1
    assert metrics["referral_completion_rate"] == 1 / 2
    assert metrics["referral_completion_numerator"] == 1
    assert metrics["referral_completion_denominator"] == 2


def test_documented_closure_requires_audited_evidence_or_authorized_approval():
    evidence_case = Case("EVIDENCE", PATHWAY, T0)
    for action, hour, evidence in (
        ("notify", 1, ()),
        ("acknowledge", 2, ("ack_record",)),
        ("review", 3, ("clinical_review",)),
        ("accept", 4, ("acceptance",)),
        ("book", 5, ("appointment",)),
        ("attend", 6, ("attendance",)),
    ):
        transition(evidence_case, action, hour, evidence)
    transition(evidence_case, "close", 7, ("closure_note",))

    approval_case = Case("APPROVAL", PATHWAY, T0)
    for action, hour, evidence in (
        ("notify", 1, ()),
        ("acknowledge", 2, ("ack_record",)),
        ("review", 3, ("clinical_review",)),
        ("accept", 4, ("acceptance",)),
        ("book", 5, ("appointment",)),
        ("attend", 6, ("attendance",)),
    ):
        transition(approval_case, action, hour, evidence)
    transition(
        approval_case,
        "close",
        8,
        approval={"role": "care_coordinator", "note": "Closure reviewed"},
    )

    still_open = Case("OPEN", PATHWAY, T0)
    with SQLiteStore(":memory:") as store:
        loaded = persist_and_reload(store, evidence_case, approval_case, still_open)
        metrics = compute_metrics(loaded, PATHWAY, at(9))

    assert metrics["documented_closure_numerator"] == 2
    assert metrics["documented_closure_denominator"] == 3
    assert metrics["documented_closure_rate"] == 2 / 3
    assert metrics["closed_cases"] == 2


def test_overdue_and_dropoff_reasons_use_latest_audit_state_and_pathway_sla():
    overdue = Case("OVERDUE", PATHWAY, T0)
    overdue.assign("reassigned-lab", "audit-test", at(24))

    unreachable = Case("UNREACHABLE", PATHWAY, T0)
    transition(unreachable, "notify", 1)
    transition(unreachable, "unreachable", 2)

    rejected = Case("REJECTED", PATHWAY, T0)
    for action, hour, evidence in (
        ("notify", 1, ()),
        ("acknowledge", 2, ("ack_record",)),
        ("review", 3, ("clinical_review",)),
    ):
        transition(rejected, action, hour, evidence)
    transition(rejected, "reject", 4)

    no_show = Case("NO-SHOW", PATHWAY, T0)
    for action, hour, evidence in (
        ("notify", 1, ()),
        ("acknowledge", 2, ("ack_record",)),
        ("review", 3, ("clinical_review",)),
        ("accept", 4, ("acceptance",)),
        ("book", 5, ("appointment",)),
    ):
        transition(no_show, action, hour, evidence)
    transition(no_show, "no_show", 6)

    with SQLiteStore(":memory:") as store:
        loaded = persist_and_reload(store, overdue, unreachable, rejected, no_show)
        # The audit is authoritative even if an unrelated current snapshot
        # field is changed after rehydration.
        loaded[0].state = "closed"
        metrics = compute_metrics(loaded, PATHWAY, at(25))

    assert metrics["overdue_count"] == 3
    assert metrics["overdue_sample_size"] == 4
    reasons = metrics["reasons_for_delay_or_dropoff"]
    assert reasons["overdue:result_issued"] == 1
    assert reasons["overdue:escalated"] == 2
    assert reasons["unreachable"] == 1
    assert reasons["no_show"] == 1
    assert reasons["reject"] == 1
    assert reasons["referral_rejected"] == 1
    assert reasons["escalated"] == 2


def test_empty_audit_cohort_uses_null_rates_and_durations_and_zero_counts():
    with SQLiteStore(":memory:"):
        metrics = compute_metrics([], PATHWAY, at(25))

    assert metrics["time_to_notification_hours"] is None
    assert metrics["time_to_notification_sample_size"] == 0
    assert metrics["acknowledgement_rate"] is None
    assert metrics["acknowledgement_sample_size"] == 0
    assert metrics["time_to_referral_acceptance_hours"] is None
    assert metrics["time_to_referral_acceptance_sample_size"] == 0
    assert metrics["referral_completion_rate"] is None
    assert metrics["referral_completion_sample_size"] == 0
    assert metrics["overdue_count"] == 0
    assert metrics["documented_closure_rate"] is None
    assert metrics["documented_closure_sample_size"] == 0
    assert metrics["reasons_for_delay_or_dropoff"] == {}
