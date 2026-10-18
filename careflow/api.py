"""FastAPI HTTP surface for the CareFlow case engine.

The API deliberately keeps orchestration in :mod:`careflow.engine`.  Routes
load a persisted ``Case``, call an engine mutation, and save the resulting
snapshot; they never assign a new case state themselves.
"""

from __future__ import annotations

from collections import Counter
from contextlib import asynccontextmanager
from datetime import datetime
import sqlite3
from pathlib import Path
from typing import Any, Callable, Mapping

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator, model_validator

from careflow.engine import Case, Pathway, TransitionError, explain, worklist
from careflow.persistence import SQLiteStore


PATHWAY_FILE = Path(__file__).parent / "pathways" / "screening_to_care.yaml"
Clock = Callable[[], datetime]


def _default_clock() -> datetime:
    """Return a naive local timestamp, matching the existing demo seed."""

    return datetime.now()


def _clock_factory(clock: Clock | datetime | None) -> Clock:
    if clock is None:
        return _default_clock
    if isinstance(clock, datetime):
        return lambda: clock
    if not callable(clock):
        raise TypeError("clock must be a callable returning datetime")
    return clock


def _resolve_pathway(pathway: Pathway | Path | str | Mapping[str, Any] | None) -> Pathway:
    if pathway is None:
        return Pathway.load(PATHWAY_FILE)
    if isinstance(pathway, Pathway):
        return pathway
    if isinstance(pathway, Mapping):
        return Pathway(pathway)
    return Pathway.load(pathway)


def _required_text(value: str, field_name: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError(f"{field_name} must not be blank")
    return value


class ApprovalInput(BaseModel):
    """A documented human approval accepted by ``Case.transition``."""

    role: str
    note: str
    name: str | None = None

    @field_validator("role", "note")
    @classmethod
    def non_blank(cls, value: str, info: Any) -> str:
        return _required_text(value, info.field_name)


class CreateCaseInput(BaseModel):
    """Input for opening a new case.

    Both ``id`` and ``case_id`` are accepted so API clients can use either
    common spelling without changing the engine's canonical ``Case.id``.
    """

    id: str | None = None
    case_id: str | None = None
    patient_user_id: str | None = None
    urgent: bool = False

    @model_validator(mode="after")
    def validate_identifier(self) -> "CreateCaseInput":
        if not self.id and not self.case_id:
            raise ValueError("id or case_id is required")
        if self.id and self.case_id and self.id != self.case_id:
            raise ValueError("id and case_id must match when both are provided")
        identifier = self.case_id or self.id
        assert identifier is not None
        _required_text(identifier, "id")
        return self

    @property
    def identifier(self) -> str:
        identifier = self.case_id or self.id
        assert identifier is not None
        return identifier.strip()


class TransitionInput(BaseModel):
    action: str
    actor: str = "api"
    evidence: list[str] = Field(default_factory=list)
    approval: ApprovalInput | None = None

    @field_validator("action", "actor")
    @classmethod
    def non_blank_text(cls, value: str, info: Any) -> str:
        return _required_text(value, info.field_name)

    @field_validator("evidence")
    @classmethod
    def non_blank_evidence(cls, value: list[str]) -> list[str]:
        return [_required_text(item, "evidence item") for item in value]


class AssignInput(BaseModel):
    """Input for changing an open case's owner."""

    person: str | None = None
    owner: str | None = None
    assignee: str | None = None
    actor: str = "api"

    @model_validator(mode="after")
    def validate_assignment(self) -> "AssignInput":
        values = [value for value in (self.person, self.owner, self.assignee) if value is not None]
        if not values:
            raise ValueError("person, owner, or assignee is required")
        cleaned = [_required_text(value, "assignee") for value in values]
        if len(set(cleaned)) != 1:
            raise ValueError("person, owner, and assignee must match when repeated")
        _required_text(self.actor, "actor")
        return self

    @property
    def target(self) -> str:
        target = self.person or self.owner or self.assignee
        assert target is not None
        return target.strip()


def _jsonable(value: Any) -> Any:
    """Convert engine values to JSON without changing their meaning."""

    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, set):
        return sorted(_jsonable(item) for item in value)
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _load_case(store: SQLiteStore, pathway: Pathway, case_id: str) -> Case:
    try:
        return store.load_case(case_id, pathway)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"case not found: {case_id}") from None
    except ValueError as exc:
        # A persisted snapshot that cannot be rehydrated is not a client
        # transition failure; expose a clear response rather than a traceback.
        raise HTTPException(status_code=422, detail=f"invalid persisted case: {exc}") from exc


def _case_payload(store: SQLiteStore, case: Case, now: datetime) -> dict[str, Any]:
    record = store.get_case_record(case.id) or {}
    details = explain(case, now)
    payload: dict[str, Any] = {
        "id": case.id,
        "case_id": case.id,
        "pathway": case.pw.name,
        "state": case.state,
        "owner": case.owner,
        "urgent": case.urgent,
        "closed": case.closed,
        "patient_user_id": record.get("patient_user_id"),
        "entered_at": case.entered,
        "created_at": case.audit[0]["ts"],
        "updated_at": case.audit[-1]["ts"],
        "evidence": sorted(case.evidence),
        "audit": case.audit,
    }
    if details is None:
        payload.update(
            {
                "elapsed_h": None,
                "sla_h": None,
                "overdue": False,
                "attention": False,
                "next": {},
            }
        )
    else:
        payload.update(
            {
                "elapsed_h": details["elapsed_h"],
                "sla_h": details["sla_h"],
                "overdue": details["overdue"],
                "attention": details["attention"],
                "next": details["next"],
            }
        )
    return _jsonable(payload)


def create_app(
    db_path: str | Path = "careflow.db",
    clock: Clock | datetime | None = None,
    pathway: Pathway | Path | str | Mapping[str, Any] | None = None,
) -> FastAPI:
    """Build an isolated CareFlow API application.

    ``db_path``, ``clock`` and ``pathway`` are explicit inputs so tests can use
    a temporary SQLite file, a fixed timestamp, and a small deterministic
    pathway without patching module globals.
    """

    resolved_pathway = _resolve_pathway(pathway)
    resolved_clock = _clock_factory(clock)
    store = SQLiteStore(db_path)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        store.close()

    app = FastAPI(title="CareFlow API", version="2.0", lifespan=lifespan)
    app.state.store = store
    app.state.pathway = resolved_pathway
    app.state.clock = resolved_clock

    @app.exception_handler(TransitionError)
    async def transition_error_handler(_: Any, exc: TransitionError) -> JSONResponse:
        message = str(exc)
        status_code = 422 if message.startswith("missing evidence") else 409
        return JSONResponse(
            status_code=status_code,
            content={"error": "transition_error", "detail": message},
        )

    def now() -> datetime:
        value = resolved_clock()
        if not isinstance(value, datetime):
            raise RuntimeError("clock must return datetime")
        return value

    @app.post("/cases", status_code=201)
    def create_case(request: CreateCaseInput) -> dict[str, Any]:
        case_id = request.identifier
        if store.get_case_record(case_id) is not None:
            raise HTTPException(status_code=409, detail=f"case already exists: {case_id}")
        timestamp = now()
        case = Case(case_id, resolved_pathway, timestamp, urgent=request.urgent)
        try:
            store.save_case(case, patient_user_id=request.patient_user_id)
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=422, detail=f"invalid case references: {exc}") from exc
        return _case_payload(store, case, timestamp)

    @app.post("/cases/{case_id}/transition", status_code=200)
    @app.post("/cases/{case_id}/transitions", status_code=200, include_in_schema=False)
    def transition_case(case_id: str, request: TransitionInput) -> dict[str, Any]:
        case = _load_case(store, resolved_pathway, case_id)
        timestamp = now()
        approval = request.approval.model_dump(exclude_none=True) if request.approval else None
        # The engine is the only component allowed to validate or mutate the
        # state machine.  In particular, do not set case.state in this route.
        case.transition(
            request.action,
            request.actor,
            timestamp,
            evidence=request.evidence,
            approval=approval,
        )
        store.save_case(case)
        return _case_payload(store, case, timestamp)

    @app.post("/cases/{case_id}/assign", status_code=200)
    def assign_case(case_id: str, request: AssignInput) -> dict[str, Any]:
        case = _load_case(store, resolved_pathway, case_id)
        timestamp = now()
        case.assign(request.target, request.actor, timestamp)
        store.save_case(case)
        return _case_payload(store, case, timestamp)

    @app.get("/cases/{case_id}", status_code=200)
    def get_case(case_id: str) -> dict[str, Any]:
        case = _load_case(store, resolved_pathway, case_id)
        return _case_payload(store, case, now())

    @app.get("/worklist", status_code=200)
    def get_worklist() -> list[dict[str, Any]]:
        timestamp = now()
        cases = [
            _load_case(store, resolved_pathway, record["id"])
            for record in store.list_case_records()
        ]
        return _jsonable(worklist(cases, timestamp))

    @app.get("/metrics", status_code=200)
    def get_metrics() -> dict[str, Any]:
        timestamp = now()
        cases = [
            _load_case(store, resolved_pathway, record["id"])
            for record in store.list_case_records()
        ]
        rows = worklist(cases, timestamp)
        state_counts = Counter(case.state for case in cases)
        overdue = sum(1 for case in cases if (details := explain(case, timestamp)) and details["overdue"])
        attention = sum(1 for case in cases if (details := explain(case, timestamp)) and details["attention"])
        total = len(cases)
        closed = sum(case.closed for case in cases)
        open_cases = total - closed
        urgent = sum(case.urgent for case in cases)
        return {
            "total": total,
            "open": open_cases,
            "closed": closed,
            "urgent": urgent,
            "overdue": overdue,
            "attention": attention,
            "worklist": len(rows),
            "by_state": dict(sorted(state_counts.items())),
            # Explicit aliases make the payload self-documenting for clients
            # while retaining concise names for dashboards.
            "total_cases": total,
            "open_cases": open_cases,
            "closed_cases": closed,
            "worklist_cases": len(rows),
        }

    return app


app = create_app()

__all__ = ["app", "create_app"]
