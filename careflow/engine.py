"""CareFlow engine: pathway-driven case orchestration with human-governed closure."""
from pathlib import Path
import yaml

class TransitionError(Exception):
    pass

class Pathway:
    def __init__(self, spec):
        self.name, self.start = spec["name"], spec["start"]
        self.terminal, self.approvers = set(spec["terminal"]), set(spec["approvers"])
        self.states = spec["states"]
        self.validate()

    @classmethod
    def load(cls, path):
        return cls(yaml.safe_load(Path(path).read_text()))

    def validate(self):
        names = set(self.states) | self.terminal
        if self.start not in names:
            raise ValueError("unknown start state")
        for s, d in self.states.items():
            if not d.get("owner") or d.get("sla_hours", 0) <= 0:
                raise ValueError(f"{s}: owner and positive sla_hours required")
            for a, t in d["next"].items():
                if t["to"] not in names:
                    raise ValueError(f"{s}.{a}: unknown target {t['to']}")

class Case:
    def __init__(self, cid, pathway, now, urgent=False, *, owner=None, actor="system"):
        self.id, self.pw, self.urgent = cid, pathway, urgent
        self.state, self.entered, self.audit, self.evidence = pathway.start, now, [], set()
        self.owner = pathway.states[self.state]["owner"] if owner is None else owner
        self._log(now, actor, "open", None, self.state)

    def _log(self, ts, actor, action, frm, to, **kw):
        self.audit.append({"ts": ts, "actor": actor, "action": action, "from": frm, "to": to, **kw})

    @property
    def closed(self):
        return self.state in self.pw.terminal

    def assign(self, person, actor, now):
        if self.closed:
            raise TransitionError("case is closed")
        if not person:
            raise TransitionError("an open case must always have an owner")
        self._log(now, actor, "assign", self.state, self.state, owner=person)
        self.owner = person

    def transition(self, action, actor, now, evidence=(), approval=None, *, actor_role=None, next_owner=None):
        """Move the case. Missing evidence is only waived by a documented approval
        from an authorized human role. AI components never call this with approval."""
        if self.closed:
            raise TransitionError("case is closed")
        t = self.pw.states[self.state]["next"].get(action)
        if t is None:
            raise TransitionError(f"'{action}' is not allowed from {self.state}")
        if actor_role is not None:
            owner_role = self.pw.states[self.state]["owner"]
            approval_role = approval.get("role") if isinstance(approval, dict) else None
            if approval is not None and approval_role != actor_role:
                raise TransitionError("documented approval must match the authenticated user's role")
            if actor_role != owner_role:
                if actor_role not in self.pw.approvers:
                    raise TransitionError(f"role '{actor_role}' cannot transition from {self.state}")
                if not approval or approval_role != actor_role or not approval.get("note"):
                    raise TransitionError("an authorized approver must submit a matching documented approval")
        have = self.evidence | set(evidence)
        missing = [e for e in t.get("requires", []) if e not in have]
        if missing and not (approval and approval.get("role") in self.pw.approvers and approval.get("note")):
            raise TransitionError(f"missing evidence {missing}: provide it or a documented authorized approval")
        frm, self.evidence = self.state, have
        self.state, self.entered = t["to"], now
        self.owner = None if self.closed else (next_owner if next_owner is not None else self.pw.states[self.state]["owner"])
        self._log(now, actor, action, frm, self.state, evidence=sorted(evidence), approval=approval)

def hours(a, b):
    return (b - a).total_seconds() / 3600

def explain(case, now):
    """What needs to happen next, who owns it, and what evidence is needed."""
    if case.closed:
        return None
    d = case.pw.states[case.state]
    el = hours(case.entered, now)
    return {"case": case.id, "state": case.state, "owner": case.owner, "urgent": case.urgent,
            "elapsed_h": round(el, 1), "sla_h": d["sla_hours"], "overdue": el > d["sla_hours"],
            "attention": bool(d.get("attention")),
            "next": {a: t.get("requires", []) for a, t in d["next"].items()}}

def worklist(cases, now):
    """Overdue, rejected/escalated and unassigned cases; urgent first, then most overdue."""
    rows = [e for c in cases if (e := explain(c, now)) and (e["overdue"] or e["attention"] or not e["owner"])]
    return sorted(rows, key=lambda e: (not e["urgent"], -(e["elapsed_h"] - e["sla_h"])))
