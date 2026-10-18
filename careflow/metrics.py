"""Pure, audit-derived operational metrics for CareFlow.

The functions in this module intentionally accept rehydrated ``Case`` objects
and a validated ``Pathway`` rather than reading the database or a current-state
counter.  ``SQLiteStore.load_case`` supplies the ordered audit events persisted
for each case, so observed measures can be recomputed without maintaining a
second metrics state store.

Metric semantics
----------------

* Durations use the timestamps on audit events.  ``time_to_notification`` is
  the mean hours from the opening event to the first valid notification event.
  ``time_to_referral_acceptance`` is the mean hours from the first valid
  referral-created event before an acceptance to that acceptance event.
* A notification is an audit action of ``notify`` (or an audited entry into
  ``patient_notified``).  An acknowledgement, referral creation, referral
  acceptance, and closure must satisfy the pathway transition's required
  evidence in that audit event or carry a documented authorized approval.
  This prevents a current state snapshot from masquerading as evidence.
* Acknowledgement rate is acknowledged cases divided by cases with a valid
  notification.  Referral completion rate is accepted referrals divided by
  cases with a valid referral-created event.  Documented closure rate is
  documented closure events divided by all cases.  Each rate returns ``None``
  with a zero denominator, while numerator, denominator, and sample size stay
  visible in ``observed_metrics``.
* Current overdue status uses the latest audited state-entry event (an event
  whose ``to`` state differs from ``from``; same-state assignments do not reset
  the clock), the state's ``sla_hours`` from the supplied pathway, and the
  supplied ``now``.  The comparison is strict, matching ``engine.explain``:
  elapsed time must be greater than the SLA.
* Delay/drop-off reasons are counted once per case from overdue current states,
  pathway attention states, and audited actions such as ``unreachable``,
  ``no_show``, and ``reject``.  A transition into ``escalated`` contributes an
  ``escalated`` reason.  Keys are deliberately machine-readable (for example
  ``overdue:patient_notified``) so a caller can choose its own labels.

Empty duration/rate samples expose ``None`` values and a zero sample size;
count metrics remain zero.  The returned mapping includes a rich
``observed_metrics`` section plus flat aliases used by the HTTP API.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import mean, median
from typing import Any, Iterable, Mapping

from careflow.engine import Case, Pathway


AuditEvent = Mapping[str, Any]
_DROP_OFF_ACTIONS = frozenset({"unreachable", "no_show", "reject", "escalated", "escalate"})


@dataclass(frozen=True)
class _StateEntry:
    """One audited entry into a pathway state."""

    state: str
    occurred_at: datetime
    event: AuditEvent


@dataclass(frozen=True)
class _Timeline:
    """The audit-only view of one case used by the metric calculations."""

    case_id: str
    events: tuple[AuditEvent, ...]
    opened_at: datetime | None
    state_entries: tuple[_StateEntry, ...]

    @property
    def current_entry(self) -> _StateEntry | None:
        return self.state_entries[-1] if self.state_entries else None


def _timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    raise TypeError("audit timestamps must be datetime values or ISO strings")


def _instant(value: datetime) -> datetime:
    """Normalize naive and timezone-aware timestamps for safe comparisons."""

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _hours_between(start: datetime, end: datetime) -> float | None:
    elapsed = (_instant(end) - _instant(start)).total_seconds() / 3600
    return elapsed if elapsed >= 0 else None


def _action(event: AuditEvent) -> str:
    value = event.get("action", "")
    return str(value).strip().lower().replace("-", "_").replace(" ", "_")


def _state(event: AuditEvent) -> str:
    return str(event.get("to", "")).strip()


def _evidence(event: AuditEvent) -> set[str]:
    values = event.get("evidence", ())
    if isinstance(values, str):
        return {values}
    if values is None:
        return set()
    try:
        return {str(value) for value in values}
    except TypeError:
        return {str(values)}


def _valid_approval(event: AuditEvent, pathway: Pathway) -> bool:
    approval = event.get("approval")
    if not isinstance(approval, Mapping):
        return False
    role = str(approval.get("role", ""))
    note = str(approval.get("note", "")).strip()
    return bool(note) and role in pathway.approvers


def _transition_rule(pathway: Pathway, event: AuditEvent) -> Mapping[str, Any] | None:
    from_state = str(event.get("from", ""))
    transition_action = _action(event)
    state_definition = pathway.states.get(from_state)
    if not isinstance(state_definition, Mapping):
        return None
    next_actions = state_definition.get("next", {})
    if not isinstance(next_actions, Mapping):
        return None
    rule = next_actions.get(transition_action)
    return rule if isinstance(rule, Mapping) else None


def _event_has_required_proof(event: AuditEvent, pathway: Pathway) -> bool:
    """Return whether a persisted transition carries its required proof."""

    rule = _transition_rule(pathway, event)
    required = rule.get("requires", ()) if rule is not None else ()
    required_set = {str(item) for item in (required or ())}
    return not required_set or required_set.issubset(_evidence(event)) or _valid_approval(event, pathway)


def _timeline(case: Case) -> _Timeline:
    events = tuple(dict(event) for event in case.audit)
    state_entries: list[_StateEntry] = []
    opened_at: datetime | None = None
    for event in events:
        occurred_at = _timestamp(event["ts"])
        action = _action(event)
        if opened_at is None and action == "open":
            opened_at = occurred_at

        target_state = _state(event)
        if not target_state:
            continue
        from_state = event.get("from")
        if action == "open" or from_state != target_state:
            state_entries.append(_StateEntry(target_state, occurred_at, event))
            if opened_at is None:
                opened_at = occurred_at

    return _Timeline(case.id, events, opened_at, tuple(state_entries))


def _matches_event(event: AuditEvent, *, actions: set[str], states: set[str], evidence: set[str]) -> bool:
    state_match = _state(event) in states and event.get("from") != event.get("to")
    return (
        _action(event) in actions
        or state_match
        or bool(_evidence(event) & evidence)
    )


def _first_event(
    timeline: _Timeline,
    pathway: Pathway,
    *,
    actions: set[str],
    states: set[str],
    evidence: set[str],
    after: datetime | None = None,
) -> AuditEvent | None:
    for event in timeline.events:
        if not _matches_event(event, actions=actions, states=states, evidence=evidence):
            continue
        if not _event_has_required_proof(event, pathway):
            continue
        if after is not None and _instant(_timestamp(event["ts"])) < _instant(after):
            continue
        return event
    return None


def _referral_created_events(timeline: _Timeline, pathway: Pathway) -> list[AuditEvent]:
    return [
        event
        for event in timeline.events
        if _matches_event(
            event,
            actions={"review"},
            states={"referral_created"},
            evidence={"clinical_review", "referral"},
        )
        and _event_has_required_proof(event, pathway)
    ]


def _first_acceptance_after(
    timeline: _Timeline,
    pathway: Pathway,
    created_at: datetime,
) -> AuditEvent | None:
    return _first_event(
        timeline,
        pathway,
        actions={"accept"},
        states={"referral_accepted"},
        evidence={"acceptance", "referral_accepted"},
        after=created_at,
    )


def _duration_summary(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"mean_hours": None, "median_hours": None, "sample_size": 0}
    return {
        "mean_hours": float(mean(values)),
        "median_hours": float(median(values)),
        "sample_size": len(values),
    }


def _rate_summary(numerator: int, denominator: int) -> dict[str, Any]:
    return {
        "rate": None if denominator == 0 else numerator / denominator,
        "numerator": numerator,
        "denominator": denominator,
        "sample_size": denominator,
    }


def _reason_keys(timeline: _Timeline, pathway: Pathway) -> set[str]:
    reasons: set[str] = set()
    for event in timeline.events:
        action = _action(event)
        if action in _DROP_OFF_ACTIONS:
            reasons.add("escalated" if action == "escalate" else action)
        target_state = _state(event)
        if target_state == "escalated":
            reasons.add("escalated")

    current = timeline.current_entry
    if current is not None:
        state_definition = pathway.states.get(current.state, {})
        if isinstance(state_definition, Mapping) and state_definition.get("attention"):
            reasons.add(current.state)
    return reasons


def compute_metrics(
    cases: Iterable[Case],
    pathway: Pathway,
    now: datetime,
) -> dict[str, Any]:
    """Compute observed metrics from ordered persisted audit events.

    ``cases`` should normally be the result of ``SQLiteStore.load_case``.  The
    current snapshot fields are intentionally not used to identify milestones
    or to establish state age; the ordered ``Case.audit`` collection is the
    source of evidence.  ``pathway`` supplies required evidence, terminal
    states, attention flags, and state SLAs.  The function has no database or
    HTTP dependencies and is deterministic for the same inputs.

    The flat aliases in the return value are convenient for clients:
    durations are mean hours (or ``None``), rates are fractions (or ``None``),
    ``overdue_count`` is an integer, and reason counts are a mapping.  The
    ``observed_metrics`` section retains medians and all numerator/denominator
    details so callers do not have to infer what a value means.
    """

    case_list = list(cases)
    timelines = [_timeline(case) for case in case_list]
    total = len(timelines)
    current_entries = [timeline.current_entry for timeline in timelines]
    current_states = [entry.state for entry in current_entries if entry is not None]
    state_counts = Counter(current_states)
    closed = sum(state in pathway.terminal for state in current_states)
    attention = sum(
        bool(pathway.states.get(state, {}).get("attention"))
        for state in current_states
    )

    notification_hours: list[float] = []
    acknowledgement_denominator = 0
    acknowledgement_numerator = 0
    referral_created_denominator = 0
    referral_accepted_numerator = 0
    referral_acceptance_hours: list[float] = []
    documented_closures = 0
    overdue_count = 0
    reasons = Counter()
    reason_case_count = 0

    for timeline in timelines:
        notification = _first_event(
            timeline,
            pathway,
            actions={"notify"},
            states={"patient_notified"},
            evidence={"notification", "notified"},
        )
        if notification is not None and timeline.opened_at is not None:
            duration = _hours_between(timeline.opened_at, _timestamp(notification["ts"]))
            if duration is not None:
                notification_hours.append(duration)

        acknowledgement = _first_event(
            timeline,
            pathway,
            actions={"acknowledge", "acknowledgement"},
            states={"patient_acknowledged"},
            evidence={"ack_record", "acknowledgement", "acknowledged"},
            after=_timestamp(notification["ts"]) if notification is not None else None,
        )
        if notification is not None:
            acknowledgement_denominator += 1
            if acknowledgement is not None:
                acknowledgement_numerator += 1

        referral_created = _referral_created_events(timeline, pathway)
        if referral_created:
            referral_created_denominator += 1
            accepted: AuditEvent | None = None
            accepted_from: datetime | None = None
            for created in referral_created:
                created_at = _timestamp(created["ts"])
                candidate = _first_acceptance_after(timeline, pathway, created_at)
                if candidate is None:
                    continue
                candidate_at = _timestamp(candidate["ts"])
                if accepted is None or _instant(candidate_at) < _instant(_timestamp(accepted["ts"])):
                    accepted = candidate
                    accepted_from = created_at
            if accepted is not None and accepted_from is not None:
                referral_accepted_numerator += 1
                duration = _hours_between(accepted_from, _timestamp(accepted["ts"]))
                if duration is not None:
                    referral_acceptance_hours.append(duration)

        closure = _first_event(
            timeline,
            pathway,
            actions={"close", "closure"},
            states=set(pathway.terminal),
            evidence={"closure_note", "closure", "closed"},
        )
        if closure is not None:
            documented_closures += 1

        current = timeline.current_entry
        if current is not None and current.state not in pathway.terminal:
            state_definition = pathway.states.get(current.state)
            if isinstance(state_definition, Mapping):
                sla_hours = state_definition.get("sla_hours")
                if isinstance(sla_hours, (int, float)):
                    elapsed = _hours_between(current.occurred_at, now)
                    if elapsed is not None and elapsed > float(sla_hours):
                        overdue_count += 1
                        reasons[f"overdue:{current.state}"] += 1

        case_reasons = _reason_keys(timeline, pathway)
        if case_reasons:
            reason_case_count += 1
            for reason in case_reasons:
                reasons[reason] += 1

    acknowledgement = _rate_summary(acknowledgement_numerator, acknowledgement_denominator)
    referral_completion = _rate_summary(referral_accepted_numerator, referral_created_denominator)
    documented_closure = _rate_summary(documented_closures, total)
    notification = _duration_summary(notification_hours)
    referral_acceptance = _duration_summary(referral_acceptance_hours)
    observed_metrics = {
        "time_to_notification": notification,
        "acknowledgement_rate": acknowledgement,
        "time_to_referral_acceptance": referral_acceptance,
        "referral_completion_rate": referral_completion,
        "overdue_count": {"count": overdue_count, "sample_size": total},
        "documented_closure_rate": documented_closure,
        "reasons_for_delay_or_dropoff": {
            "counts": dict(sorted(reasons.items())),
            "sample_size": reason_case_count,
        },
    }

    return {
        "total": total,
        "open": total - closed,
        "closed": closed,
        "urgent": sum(bool(case.urgent) for case in case_list),
        "attention": attention,
        "overdue": overdue_count,
        "by_state": dict(sorted(state_counts.items())),
        "total_cases": total,
        "open_cases": total - closed,
        "closed_cases": closed,
        "overdue_count": overdue_count,
        "overdue_sample_size": total,
        "observed_metrics": observed_metrics,
        "time_to_notification": notification["mean_hours"],
        "time_to_notification_hours": notification["mean_hours"],
        "time_to_notification_sample_size": notification["sample_size"],
        "acknowledgement_rate": acknowledgement["rate"],
        "acknowledgement_sample_size": acknowledgement["sample_size"],
        "acknowledgement_numerator": acknowledgement["numerator"],
        "acknowledgement_denominator": acknowledgement["denominator"],
        "acknowledgment_rate": acknowledgement["rate"],
        "time_to_referral_acceptance": referral_acceptance["mean_hours"],
        "time_to_referral_acceptance_hours": referral_acceptance["mean_hours"],
        "time_to_referral_acceptance_sample_size": referral_acceptance["sample_size"],
        "referral_completion_rate": referral_completion["rate"],
        "referral_completion_sample_size": referral_completion["sample_size"],
        "referral_completion_numerator": referral_completion["numerator"],
        "referral_completion_denominator": referral_completion["denominator"],
        "documented_closure_rate": documented_closure["rate"],
        "documented_closure_sample_size": documented_closure["sample_size"],
        "documented_closure_numerator": documented_closure["numerator"],
        "documented_closure_denominator": documented_closure["denominator"],
        "reasons_for_delay_or_dropoff": dict(sorted(reasons.items())),
        "reasons_for_delay_or_dropoff_sample_size": reason_case_count,
    }


__all__ = ["compute_metrics"]
