from datetime import datetime, timedelta
from pathlib import Path

import pytest

from careflow.engine import Case, Pathway, TransitionError, worklist
from careflow.sim import compare, run

PW = Pathway.load(Path(__file__).parent.parent / "careflow" / "pathways" / "screening_to_care.yaml")
T0 = datetime(2026, 11, 1, 9)

def walk(c, steps):
    for i, (a, ev) in enumerate(steps, 1):
        c.transition(a, "u", T0 + timedelta(hours=i), evidence=ev)
    return c

def at_care_received():
    return walk(Case("C", PW, T0), [("notify", []), ("acknowledge", ["ack_record"]), ("review", ["clinical_review"]),
                                    ("accept", ["acceptance"]), ("book", ["appointment"]), ("attend", ["attendance"])])

def test_cannot_close_without_evidence_or_approval():
    c = at_care_received()
    with pytest.raises(TransitionError):
        c.transition("close", "u", T0 + timedelta(hours=9))
    assert not c.closed

def test_unauthorized_approval_rejected():
    with pytest.raises(TransitionError):
        at_care_received().transition("close", "u", T0, approval={"role": "lab", "note": "x"})

def test_close_with_evidence_or_authorized_approval():
    c = at_care_received()
    c.transition("close", "u", T0 + timedelta(hours=9), evidence=["closure_note"])
    assert c.closed and c.owner is None
    d = at_care_received()
    d.transition("close", "u", T0, approval={"role": "clinician_on_call", "note": "reviewed"})
    assert d.closed

def test_open_case_always_has_owner():
    c = Case("C", PW, T0)
    assert c.owner == "lab"
    with pytest.raises(TransitionError):
        c.assign("", "u", T0)

def test_worklist_flags_overdue_urgent_first():
    a, b = Case("A", PW, T0), Case("B", PW, T0, urgent=True)
    w = worklist([a, b], T0 + timedelta(hours=30))
    assert [r["case"] for r in w] == ["B", "A"] and all(r["overdue"] for r in w)
    assert worklist([a], T0 + timedelta(hours=1)) == []

def test_rejected_referral_surfaces_without_overdue():
    c = walk(Case("C", PW, T0), [("notify", []), ("acknowledge", ["ack_record"]), ("review", ["clinical_review"]), ("reject", [])])
    assert worklist([c], T0 + timedelta(hours=5))[0]["state"] == "referral_rejected"

def test_every_transition_audited_and_invalid_rejected():
    c = Case("C", PW, T0)
    c.transition("notify", "u", T0)
    assert [e["action"] for e in c.audit] == ["open", "notify"]
    with pytest.raises(TransitionError):
        c.transition("close", "u", T0)
    assert len(c.audit) == 2

def test_pathway_validation_rejects_bad_target():
    with pytest.raises(ValueError):
        Pathway({"name": "x", "start": "a", "terminal": ["z"], "approvers": [],
                 "states": {"a": {"owner": "r", "sla_hours": 1, "next": {"go": {"to": "nowhere"}}}}})

def test_simulation_deterministic_and_improves_under_assumptions():
    assert run(seed=7) == run(seed=7)
    r = compare()
    assert r["high"]["closure_rate"] >= r["medium"]["closure_rate"] >= r["low"]["closure_rate"] >= r["baseline"]["closure_rate"]


def test_terminal_exception_branches_and_dual_signoff():
    # 1. Test closed_unreachable from escalated
    c1 = Case("C1", PW, T0)
    c1.transition("notify", "u", T0)
    c1.transition("unreachable", "u", T0 + timedelta(hours=1))
    assert c1.state == "escalated"
    c1.transition("unreachable", "u", T0 + timedelta(hours=2), evidence=["unreachable_exhaustion_note"])
    assert c1.closed and c1.state == "closed_unreachable"

    # 2. Test closed_refused requires dual supervisor sign-off
    c2 = Case("C2", PW, T0)
    c2.transition("notify", "u", T0)
    c2.transition("unreachable", "u", T0 + timedelta(hours=1))
    with pytest.raises(TransitionError, match="dual clinical supervisor sign-off"):
        c2.transition("refuse", "u", T0 + timedelta(hours=2), evidence=["patient_refusal_documentation"])
    # With dual supervisor sign-off
    c2.transition(
        "refuse",
        "u",
        T0 + timedelta(hours=2),
        evidence=["patient_refusal_documentation", "supervisor_signoff"],
        approval={"role": "clinical_supervisor", "note": "verified patient refusal protocol", "co_signer": "dr_lead"},
        actor_role="clinical_supervisor",
    )
    assert c2.closed and c2.state == "closed_refused"

    # 3. Test closed_transferred with co-signer approval
    c3 = Case("C3", PW, T0)
    c3.transition("notify", "u", T0)
    c3.transition("unreachable", "u", T0 + timedelta(hours=1))
    c3.transition(
        "transfer",
        "u",
        T0 + timedelta(hours=2),
        evidence=["transfer_of_care_record", "supervisor_signoff"],
        approval={"role": "clinician_on_call", "note": "transferred to regional oncology", "co_signer": "dr_supervisor"},
        actor_role="clinician_on_call",
    )
    assert c3.closed and c3.state == "closed_transferred"

    # 4. Test closed_unreachable_exhausted
    c4 = Case("C4", PW, T0)
    c4.transition("notify", "u", T0)
    c4.transition("unreachable", "u", T0 + timedelta(hours=1))
    c4.transition(
        "exhaust",
        "u",
        T0 + timedelta(hours=2),
        evidence=["outreach_exhaustion_protocol", "supervisor_signoff"],
        approval={"role": "clinician_on_call", "note": "all outreach exhausted", "co_signer": "clinical_supervisor"},
        actor_role="clinician_on_call",
    )
    assert c4.closed and c4.state == "closed_unreachable_exhausted"
