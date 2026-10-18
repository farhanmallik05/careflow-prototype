"""FastAPI HTTP surface for the CareFlow case engine.

The API deliberately keeps orchestration in :mod:`careflow.engine`.  Routes
load a persisted ``Case``, call an engine mutation, and save the resulting
snapshot; they never assign a new case state themselves.
"""

from __future__ import annotations

from collections import Counter
from contextlib import asynccontextmanager
from datetime import datetime
import os
import secrets
import sqlite3
from pathlib import Path
from typing import Any, Callable, Mapping

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from itsdangerous import BadSignature, BadTimeSignature, URLSafeTimedSerializer
from pydantic import BaseModel, Field, field_validator, model_validator

from careflow.engine import Case, Pathway, TransitionError, explain, worklist
from careflow.persistence import SQLiteStore, User


PATHWAY_FILE = Path(__file__).parent / "pathways" / "screening_to_care.yaml"
Clock = Callable[[], datetime]
SESSION_COOKIE = "careflow_session"
SESSION_SALT = "careflow-session"
DEFAULT_SESSION_MAX_AGE = 3600


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


class LoginInput(BaseModel):
    """Signed-session login for a deterministic local/demo user."""

    user_id: str | None = None
    id: str | None = None

    @model_validator(mode="after")
    def validate_user_id(self) -> "LoginInput":
        values = [value for value in (self.user_id, self.id) if value is not None]
        if not values:
            raise ValueError("user_id is required")
        cleaned = [_required_text(value, "user_id") for value in values]
        if len(set(cleaned)) != 1:
            raise ValueError("user_id and id must match when repeated")
        return self

    @property
    def identifier(self) -> str:
        value = self.user_id or self.id
        assert value is not None
        return value.strip()


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


def _user_payload(user: User) -> dict[str, Any]:
    """Return a stable user shape for login and current-user clients."""

    return {
        "id": user.id,
        "user_id": user.id,
        "name": user.name,
        "role": user.role,
        "email": user.email,
        "is_active": user.is_active,
    }


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
    *,
    session_secret: str | None = None,
    session_max_age: int | None = None,
) -> FastAPI:
    """Build an isolated CareFlow API application.

    ``db_path``, ``clock`` and ``pathway`` are explicit inputs so tests can use
    a temporary SQLite file, a fixed timestamp, and a small deterministic
    pathway without patching module globals.
    """

    resolved_pathway = _resolve_pathway(pathway)
    resolved_clock = _clock_factory(clock)
    store = SQLiteStore(db_path)
    resolved_session_secret = (
        session_secret
        or os.getenv("CAREFLOW_SESSION_SECRET")
        or os.getenv("SESSION_SECRET")
        or secrets.token_urlsafe(32)
    )
    resolved_session_max_age = session_max_age
    if resolved_session_max_age is None:
        raw_max_age = os.getenv("CAREFLOW_SESSION_MAX_AGE") or os.getenv("SESSION_MAX_AGE")
        resolved_session_max_age = int(raw_max_age) if raw_max_age else DEFAULT_SESSION_MAX_AGE
    if not resolved_session_secret:
        raise ValueError("CAREFLOW_SESSION_SECRET must not be blank")
    if resolved_session_max_age <= 0:
        raise ValueError("session_max_age must be positive")
    signer = URLSafeTimedSerializer(resolved_session_secret, salt=SESSION_SALT)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        store.close()

    app = FastAPI(title="CareFlow API", version="2.0", lifespan=lifespan)
    app.state.store = store
    app.state.pathway = resolved_pathway
    app.state.clock = resolved_clock
    app.state.session_secret = resolved_session_secret
    app.state.session_max_age = resolved_session_max_age
    app.state.session_cookie = SESSION_COOKIE

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

    def authenticated_user(request: Request) -> User:
        token = request.cookies.get(SESSION_COOKIE)
        if not token:
            raise HTTPException(status_code=401, detail="authentication required")
        try:
            payload = signer.loads(token, max_age=resolved_session_max_age)
        except (BadSignature, BadTimeSignature, TypeError, ValueError):
            raise HTTPException(status_code=401, detail="invalid or expired session") from None
        if not isinstance(payload, Mapping) or not isinstance(payload.get("user_id"), str):
            raise HTTPException(status_code=401, detail="invalid session payload")
        user = store.get_user(payload["user_id"])
        if user is None or not user.is_active:
            raise HTTPException(status_code=401, detail="user is inactive or unknown")
        return user

    def operational_user(user: User) -> None:
        if user.role == "patient":
            raise HTTPException(status_code=403, detail="patients cannot access operational views")

    def authorize_case_access(user: User, case_id: str) -> dict[str, Any]:
        record = store.get_case_record(case_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"case not found: {case_id}")
        if user.role == "patient" and record.get("patient_user_id") != user.id:
            raise HTTPException(status_code=403, detail="patients may access only their own case")
        return record

    def active_owner_for_state(state: str, preferred_user_id: str | None = None) -> str:
        role = resolved_pathway.states[state]["owner"]
        if preferred_user_id is not None:
            preferred = store.get_user(preferred_user_id)
            if preferred is not None and preferred.is_active and preferred.role == role:
                return preferred.id
        candidates = store.list_users(role=role, active_only=True)
        if not candidates:
            raise HTTPException(status_code=422, detail=f"no active user is available for role '{role}'")
        return candidates[0].id

    def next_owner_for_transition(case: Case, action: str) -> str | None:
        transition = case.pw.states[case.state]["next"].get(action)
        if transition is None:
            return None
        target_state = transition["to"]
        if target_state in case.pw.terminal:
            return None
        return active_owner_for_state(target_state)

    def authorize_transition(user: User, case: Case, approval: dict[str, Any] | None) -> None:
        if case.closed:
            return
        owner_role = case.pw.states[case.state]["owner"]
        approval_role = approval.get("role") if approval else None
        if approval is not None and approval_role != user.role:
            raise HTTPException(status_code=403, detail="documented approval must match the authenticated user's role")
        if user.role == owner_role:
            return
        if user.role not in case.pw.approvers:
            raise HTTPException(status_code=403, detail=f"role '{user.role}' cannot transition from {case.state}")
        if not approval or approval_role != user.role or not approval.get("note"):
            raise HTTPException(
                status_code=403,
                detail="an authorized approver must submit a matching documented approval",
            )

    def log_case_access(user: User, case_id: str, action: str, endpoint: str) -> None:
        store.record_access(
            user_id=user.id,
            case_id=case_id,
            action=action,
            occurred_at=now(),
            endpoint=endpoint,
        )

    def user_response(user: User) -> dict[str, Any]:
        payload = _user_payload(user)
        return {"user": payload, **payload}

    @app.post("/auth/login", status_code=200)
    @app.post("/login", status_code=200, include_in_schema=False)
    def login(request: LoginInput, response: Response) -> dict[str, Any]:
        user = store.get_user(request.identifier)
        if user is None or not user.is_active:
            raise HTTPException(status_code=401, detail="unknown or inactive user")
        token = signer.dumps({"user_id": user.id})
        response.set_cookie(
            SESSION_COOKIE,
            token,
            max_age=resolved_session_max_age,
            httponly=True,
            samesite="lax",
            path="/",
        )
        return user_response(user)

    @app.post("/auth/logout", status_code=200)
    @app.post("/logout", status_code=200, include_in_schema=False)
    def logout(response: Response) -> dict[str, bool]:
        response.delete_cookie(SESSION_COOKIE, path="/")
        return {"ok": True}

    @app.get("/auth/current-user", status_code=200)
    @app.get("/auth/me", status_code=200, include_in_schema=False)
    @app.get("/current-user", status_code=200, include_in_schema=False)
    def current_user(request: Request) -> dict[str, Any]:
        return user_response(authenticated_user(request))

    @app.post("/cases", status_code=201)
    def create_case(request: CreateCaseInput, http_request: Request) -> dict[str, Any]:
        user = authenticated_user(http_request)
        case_id = request.identifier
        if store.get_case_record(case_id) is not None:
            raise HTTPException(status_code=409, detail=f"case already exists: {case_id}")
        patient_user_id = request.patient_user_id
        if user.role == "patient":
            if patient_user_id is not None and patient_user_id != user.id:
                raise HTTPException(status_code=403, detail="patients may create only their own case")
            patient_user_id = user.id
        if patient_user_id is not None:
            patient = store.get_user(patient_user_id)
            if patient is None or not patient.is_active or patient.role != "patient":
                raise HTTPException(status_code=422, detail="patient_user_id must identify an active patient")
        timestamp = now()
        initial_role = resolved_pathway.states[resolved_pathway.start]["owner"]
        initial_owner = active_owner_for_state(
            resolved_pathway.start,
            preferred_user_id=user.id if user.role == initial_role else None,
        )
        case = Case(
            case_id,
            resolved_pathway,
            timestamp,
            urgent=request.urgent,
            owner=initial_owner,
            actor=user.id,
        )
        try:
            store.save_case(case, patient_user_id=patient_user_id)
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=422, detail=f"invalid case references: {exc}") from exc
        log_case_access(user, case.id, "mutation", "/cases")
        return _case_payload(store, case, timestamp)

    @app.post("/cases/{case_id}/transition", status_code=200)
    @app.post("/cases/{case_id}/transitions", status_code=200, include_in_schema=False)
    def transition_case(case_id: str, request: TransitionInput, http_request: Request) -> dict[str, Any]:
        user = authenticated_user(http_request)
        case = _load_case(store, resolved_pathway, case_id)
        authorize_case_access(user, case_id)
        timestamp = now()
        approval = request.approval.model_dump(exclude_none=True) if request.approval else None
        authorize_transition(user, case, approval)
        # The engine is the only component allowed to validate or mutate the
        # state machine.  In particular, do not set case.state in this route.
        case.transition(
            request.action,
            user.id,
            timestamp,
            evidence=request.evidence,
            approval=approval,
            actor_role=user.role,
            next_owner=next_owner_for_transition(case, request.action),
        )
        store.save_case(case)
        log_case_access(user, case.id, "mutation", f"/cases/{case_id}/transition")
        return _case_payload(store, case, timestamp)

    @app.post("/cases/{case_id}/assign", status_code=200)
    def assign_case(case_id: str, request: AssignInput, http_request: Request) -> dict[str, Any]:
        user = authenticated_user(http_request)
        case = _load_case(store, resolved_pathway, case_id)
        authorize_case_access(user, case_id)
        operational_user(user)
        timestamp = now()
        if case.closed:
            raise HTTPException(status_code=409, detail="case is closed")
        target = store.get_user(request.target)
        if target is None or not target.is_active:
            raise HTTPException(status_code=422, detail="assignment target must be an active user")
        expected_role = case.pw.states[case.state]["owner"]
        if target.role != expected_role:
            raise HTTPException(
                status_code=422,
                detail=f"assignment target must have role '{expected_role}'",
            )
        case.assign(target.id, user.id, timestamp)
        store.save_case(case)
        log_case_access(user, case.id, "mutation", f"/cases/{case_id}/assign")
        return _case_payload(store, case, timestamp)

    @app.get("/cases/{case_id}", status_code=200)
    def get_case(case_id: str, http_request: Request) -> dict[str, Any]:
        user = authenticated_user(http_request)
        case = _load_case(store, resolved_pathway, case_id)
        authorize_case_access(user, case_id)
        payload = _case_payload(store, case, now())
        log_case_access(user, case.id, "read", f"/cases/{case_id}")
        return payload

    @app.get("/worklist", status_code=200)
    def get_worklist(http_request: Request) -> list[dict[str, Any]]:
        user = authenticated_user(http_request)
        operational_user(user)
        timestamp = now()
        records = store.list_case_records()
        cases = [_load_case(store, resolved_pathway, record["id"]) for record in records]
        rows = _jsonable(worklist(cases, timestamp))
        for record in records:
            log_case_access(user, record["id"], "worklist", "/worklist")
        return rows

    @app.get("/metrics", status_code=200)
    def get_metrics(http_request: Request) -> dict[str, Any]:
        user = authenticated_user(http_request)
        operational_user(user)
        timestamp = now()
        records = store.list_case_records()
        cases = [_load_case(store, resolved_pathway, record["id"]) for record in records]
        rows = worklist(cases, timestamp)
        state_counts = Counter(case.state for case in cases)
        overdue = sum(1 for case in cases if (details := explain(case, timestamp)) and details["overdue"])
        attention = sum(1 for case in cases if (details := explain(case, timestamp)) and details["attention"])
        total = len(cases)
        closed = sum(case.closed for case in cases)
        open_cases = total - closed
        urgent = sum(case.urgent for case in cases)
        result = {
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
        for record in records:
            log_case_access(user, record["id"], "metrics", "/metrics")
        return result

    return app


app = create_app()

__all__ = ["app", "create_app"]
