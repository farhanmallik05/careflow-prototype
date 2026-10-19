from datetime import datetime, timedelta
from pathlib import Path

from careflow.engine import Case, Pathway, TransitionError, worklist
from careflow.sim import compare

PW = Pathway.load(Path(__file__).parent / "pathways" / "screening_to_care.yaml")

def main():
    t0 = datetime(2026, 11, 1, 9)
    c, urgent = Case("C-001", PW, t0), Case("C-002", PW, t0, urgent=True)
    c.transition("notify", "lab_system", t0 + timedelta(hours=2))
    c.transition("acknowledge", "patient", t0 + timedelta(hours=20), evidence=["ack_record"])
    c.transition("review", "dr_rao", t0 + timedelta(hours=40), evidence=["clinical_review"])
    print("Worklist at +120h (C-001 referral stalled, C-002 never notified):")
    for r in worklist([c, urgent], t0 + timedelta(hours=120)):
        print(" ", r)
    c.transition("accept", "dr_iyer", t0 + timedelta(hours=125), evidence=["acceptance"])
    c.transition("book", "clinic", t0 + timedelta(hours=130), evidence=["appointment"])
    c.transition("attend", "clinic", t0 + timedelta(hours=200), evidence=["attendance"])
    try:
        c.transition("close", "bot", t0 + timedelta(hours=210))
    except TransitionError as e:
        print("Blocked:", e)
    c.transition("close", "coord_ana", t0 + timedelta(hours=211),
                 approval={"role": "care_coordinator", "name": "Ana", "note": "Closure note filed on paper chart"})
    print("Closed. Audit events:", len(c.audit))
    print("Simulation (illustrative assumptions):")
    for k, v in compare().items():
        print(f"  {k:8} closure_rate={v['closure_rate']} unresolved={v['unresolved']}")

if __name__ == "__main__":
    main()
