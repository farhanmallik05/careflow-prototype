"""Deterministic synthetic SQLite fixture for CareFlow demos and API work."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
from typing import Iterable

from careflow.engine import Case, Pathway, explain
from careflow.persistence import Consent, SQLiteStore, User


DEMO_NOW = datetime(2026, 11, 1, 9, 0)
PATHWAY_FILE = Path(__file__).parent / "pathways" / "screening_to_care.yaml"

STAFF = (
    ("demo-lab", "Demo Lab Service", "lab"),
    ("demo-screening", "Demo Screening Program", "screening_program"),
    ("demo-primary-care", "Demo Primary Care", "primary_care"),
    ("demo-coordinator", "Demo Care Coordinator", "care_coordinator"),
    ("demo-specialist", "Demo Specialist", "specialist"),
    ("demo-on-call", "Demo Clinician on Call", "clinician_on_call"),
)
STAFF_BY_ROLE = {role: user_id for user_id, _, role in STAFF}

ROUTES: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "result_issued": (),
    "patient_notified": (("notify", ()),),
    "patient_acknowledged": (("notify", ()), ("acknowledge", ("ack_record",))),
    "referral_created": (
        ("notify", ()),
        ("acknowledge", ("ack_record",)),
        ("review", ("clinical_review",)),
    ),
    "referral_rejected": (
        ("notify", ()),
        ("acknowledge", ("ack_record",)),
        ("review", ("clinical_review",)),
        ("reject", ()),
    ),
    "referral_accepted": (
        ("notify", ()),
        ("acknowledge", ("ack_record",)),
        ("review", ("clinical_review",)),
        ("accept", ("acceptance",)),
    ),
    "appointment_booked": (
        ("notify", ()),
        ("acknowledge", ("ack_record",)),
        ("review", ("clinical_review",)),
        ("accept", ("acceptance",)),
        ("book", ("appointment",)),
    ),
    "care_received": (
        ("notify", ()),
        ("acknowledge", ("ack_record",)),
        ("review", ("clinical_review",)),
        ("accept", ("acceptance",)),
        ("book", ("appointment",)),
        ("attend", ("attendance",)),
    ),
    "closed": (
        ("notify", ()),
        ("acknowledge", ("ack_record",)),
        ("review", ("clinical_review",)),
        ("accept", ("acceptance",)),
        ("book", ("appointment",)),
        ("attend", ("attendance",)),
        ("close", ("closure_note",)),
    ),
    "escalated": (("notify", ()), ("unreachable", ())),
}

STAGES = tuple(ROUTES)
STEP_HOURS = (18, 24, 30, 48, 84, 120, 48, 12)


def _current_delay(stage: str, index: int, pathway: Pathway) -> int:
    """Return varied, deterministic age for the current stage in hours."""

    if stage == "closed":
        return 6 + (index % 4) * 12
    sla = pathway.states[stage]["sla_hours"]
    return max(2, int(sla * 0.35) + ((index * 11) % (sla * 2)))


def _make_case(index: int, pathway: Pathway, now: datetime) -> Case:
    case_id = f"DEMO-{index:03d}"
    stage = STAGES[(index - 1) % len(STAGES)]
    route = ROUTES[stage]
    delay = _current_delay(stage, index, pathway)
    journey_hours = sum(STEP_HOURS[: len(route)])
    started = now - timedelta(hours=delay + journey_hours)
    case = Case(
        case_id,
        pathway,
        started,
        urgent=index % 5 == 0,
        owner=STAFF_BY_ROLE[pathway.states[pathway.start]["owner"]],
        actor="demo-seed",
    )

    elapsed = 0
    for step, (action, evidence) in enumerate(route):
        elapsed += STEP_HOURS[step]
        timestamp = started + timedelta(hours=elapsed)
        next_state = pathway.states[case.state]["next"][action]["to"]
        next_owner = None if next_state in pathway.terminal else STAFF_BY_ROLE[pathway.states[next_state]["owner"]]
        if action == "close" and ((index - 1) // len(STAGES)) % 2 == 0:
            case.transition(
                action,
                "demo-coordinator",
                timestamp,
                approval={
                    "role": "care_coordinator",
                    "name": "Demo Care Coordinator",
                    "note": "Synthetic closure review recorded for demo data",
                },
                next_owner=next_owner,
            )
        else:
            case.transition(action, "demo-seed", timestamp, evidence=evidence, next_owner=next_owner)
    return case


def seed_demo(
    db_path: str | Path = "careflow.db",
    *,
    now: datetime = DEMO_NOW,
    reset: bool = True,
    mode: str | None = None,
) -> dict[str, int]:
    """Create exactly 40 deterministic demo cases and supporting records."""

    selected_mode = (mode or os.getenv("CAREFLOW_MODE", "demo")).strip().lower()
    if selected_mode != "demo":
        raise RuntimeError("demo seed/reset is unavailable outside CAREFLOW_MODE=demo")

    pathway = Pathway.load(PATHWAY_FILE)
    with SQLiteStore(db_path) as store:
        if reset:
            store.clear()

        for position, (user_id, name, role) in enumerate(STAFF, 1):
            store.add_user(
                User(
                    id=user_id,
                    name=name,
                    role=role,
                    email=f"{user_id}@example.test",
                    created_at=now - timedelta(days=position),
                )
            )

        for index in range(1, 41):
            patient_id = f"demo-patient-{index:03d}"
            case_id = f"DEMO-{index:03d}"
            case = _make_case(index, pathway, now)
            store.add_user(
                User(
                    id=patient_id,
                    name=f"Synthetic Patient {index:03d}",
                    role="patient",
                    email=f"patient-{index:03d}@example.test",
                    created_at=case.audit[0]["ts"],
                )
            )
            store.save_case(case, patient_user_id=patient_id)
            store.add_consent(
                Consent(
                    id=f"consent-{index:03d}",
                    user_id=patient_id,
                    case_id=case_id,
                    consent_type="care_coordination",
                    status="granted",
                    granted_at=case.audit[0]["ts"],
                    source="synthetic_demo_seed",
                )
            )
            details = explain(case, now)
            if details and (details["overdue"] or details["attention"]):
                store.enqueue_notification(
                    event="case_attention",
                    case_id=case_id,
                    recipient=case.owner,
                    payload={
                        "state": case.state,
                        "urgent": case.urgent,
                        "overdue": details["overdue"],
                    },
                    notification_id=f"demo-notification-{index:03d}",
                    available_at=now,
                    created_at=now,
                    dedupe_key=f"demo-attention-{case_id}",
                )

        return {
            "users": store.count("users"),
            "cases": store.count("cases"),
            "consents": store.count("consents"),
            "evidence": store.count("evidence"),
            "approvals": store.count("approvals"),
            "audit_events": store.count("audit_events"),
            "notification_outbox": store.count("notification_outbox"),
        }


def main(argv: Iterable[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Create synthetic CareFlow demo data")
    parser.add_argument("--db", default="careflow.db", help="SQLite database path")
    parser.add_argument("--mode", choices=("demo", "pilot"), default=None)
    args = parser.parse_args(list(argv) if argv is not None else None)
    print(json.dumps(seed_demo(args.db, mode=args.mode), sort_keys=True))


if __name__ == "__main__":
    main()
