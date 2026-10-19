"""FastAPI HTTP surface for the CareFlow case engine.

The API deliberately keeps orchestration in :mod:`careflow.engine`.  Routes
load a persisted ``Case``, call an engine mutation, and save the resulting
snapshot; they never assign a new case state themselves.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import BaseModel, Field, field_validator, model_validator

from careflow.ai import (
    AIProvider,
    DEFAULT_TEMPLATE,
    ProviderError,
    ProviderUnavailableError,
    REVIEW_FLAG,
    SUPPORTED_TEMPLATES,
    create_message_provider,
    normalize_language,
    normalize_template,
)
from careflow.config import CareFlowSettings, ConfigurationError
from careflow.engine import Case, Pathway, TransitionError, explain, worklist
from careflow.metrics import compute_metrics
from careflow.oidc import (
    OIDCProvider,
    OIDCProviderUnavailable,
    OIDCValidationError,
    UnavailableOIDCProvider,
    pkce_challenge,
)
from careflow.persistence import (
    CURRENT_SCHEMA_VERSION,
    ConcurrencyConflict,
    Consent,
    MessageDraftRecord,
    SessionRecord,
    SQLiteStore,
    User,
)
from careflow.sim import compare
from careflow.worker import OutboxWorker


PATHWAY_FILE = Path(__file__).parent / "pathways" / "screening_to_care.yaml"
TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"
Clock = Callable[[], datetime]
SESSION_COOKIE = "careflow_session"
CSRF_COOKIE = "careflow_csrf"
DEFAULT_SESSION_MAX_AGE = 3600
DEFAULT_SESSION_IDLE_AGE = 3600
DEFAULT_SESSION_ABSOLUTE_AGE = 28800
CSRF_HEADER = "x-csrf-token"

# Every protected surface names its policy explicitly.  Route aliases point at
# the same surface, so JSON and HTML cannot accidentally drift into different
# authorization decisions.  Unknown surfaces are denied by the helper below.
ROUTE_ACTION_POLICY: dict[str, dict[str, str]] = {
    "case": {"patient": "linked-safe-read", "staff": "assigned-or-team-read"},
    "case-mutation": {"staff": "assigned-owner-or-approver"},
    "patient-collection": {"patient": "self-only-safe", "staff": "assigned-or-team-read"},
    "worklist": {"staff": "assigned-or-team-collection"},
    "metrics": {"staff": "assigned-or-team-collection"},
    "draft": {"staff": "assigned-owner-or-approver"},
    "draft-read": {"staff": "assigned-or-team-read"},
    "audit": {"staff": "assigned-or-team-read"},
    "access-log": {"staff": "assigned-or-team-read"},
    "consent": {"patient": "linked-self-mutation", "staff": "assigned-owner-or-approver"},
}


_template_environment = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR),
    autoescape=select_autoescape(("html", "xml"), default_for_string=True),
)
templates = Jinja2Templates(env=_template_environment)


def _default_clock() -> datetime:
    """Return a timezone-aware UTC timestamp for all application operations."""

    return datetime.now(timezone.utc)


def _clock_factory(clock: Clock | datetime | None) -> Clock:
    if clock is None:
        return _default_clock
    if isinstance(clock, datetime):
        normalized = clock if clock.tzinfo is not None else clock.replace(tzinfo=timezone.utc)
        normalized = normalized.astimezone(timezone.utc)
        return lambda: normalized
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


class MessageDraftInput(BaseModel):
    """Input for deterministic or configured patient-message drafting."""

    language: str = "en"
    template: str = DEFAULT_TEMPLATE

    @field_validator("language")
    @classmethod
    def supported_language(cls, value: str) -> str:
        return normalize_language(_required_text(value, "language"))

    @field_validator("template")
    @classmethod
    def supported_template(cls, value: str) -> str:
        normalized = normalize_template(_required_text(value, "template"))
        if normalized not in SUPPORTED_TEMPLATES:  # pragma: no cover - defensive
            raise ValueError("unsupported message template")
        return normalized


class MessageApprovalInput(BaseModel):
    """Optional note recorded alongside a human draft approval."""

    note: str = "Message draft reviewed and approved by authenticated staff"

    @field_validator("note")
    @classmethod
    def non_blank_note(cls, value: str) -> str:
        return _required_text(value, "note")


class ConsentInput(BaseModel):
    """A patient-controlled case-scoped consent decision."""

    status: str
    consent_type: str = "care_coordination"
    expires_at: datetime | None = None

    @field_validator("status", "consent_type")
    @classmethod
    def non_blank_consent_text(cls, value: str, info: Any) -> str:
        return _required_text(value, info.field_name).lower()

    @field_validator("status")
    @classmethod
    def supported_status(cls, value: str) -> str:
        if value not in {"granted", "withdrawn", "pending"}:
            raise ValueError("consent status must be granted, withdrawn, or pending")
        return value


class EHRAppointmentWebhookInput(BaseModel):
    resourceType: str = "Appointment"
    id: str | None = None
    appointment_id: str | None = None
    status: str
    patient_id: str | None = None
    case_id: str | None = None
    cancellation_reason: str | None = None
    rescheduled_start: str | None = None
    note: str | None = None


class DeliveryReceiptInput(BaseModel):
    MessageSid: str | None = None
    id: str | None = None
    notification_id: str | None = None
    MessageStatus: str | None = None
    status: str | None = None
    ErrorCode: str | None = None
    error_code: str | None = None
    ErrorMessage: str | None = None


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


def _request_wants_html(request: Request) -> bool:
    """Choose HTML only when the caller explicitly prefers it.

    ``TestClient`` and most API clients send ``*/*`` or ``application/json``;
    those requests continue through the existing JSON response path.  Browsers
    send an explicit ``text/html`` preference, which opts them into templates.
    """

    accept = request.headers.get("accept", "").strip().lower()
    if not accept or accept == "*/*":
        return False

    explicit: dict[str, float] = {}
    wildcard = 0.0
    for item in accept.split(","):
        parts = [part.strip() for part in item.split(";")]
        media_type = parts[0]
        quality = 1.0
        for parameter in parts[1:]:
            name, _, value = parameter.partition("=")
            if name.strip() == "q":
                try:
                    quality = max(0.0, float(value))
                except ValueError:
                    quality = 0.0
        if media_type == "*/*":
            wildcard = quality
        elif media_type in {"text/html", "application/json"}:
            explicit[media_type] = quality

    html_quality = explicit.get("text/html", wildcard)
    json_quality = explicit.get("application/json", wildcard)
    return html_quality > 0 and html_quality >= json_quality


def _label(value: Any) -> str:
    """Turn internal snake-case values into readable labels for the UI."""

    if value is None or value == "":
        return "—"
    return str(value).replace("_", " ").strip().title()


def _format_datetime(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            return value
    return str(value)


def _datetime_attr(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _owner_view(store: SQLiteStore, owner_id: str | None) -> dict[str, str]:
    owner = store.get_user(owner_id) if owner_id else None
    if owner is None:
        return {
            "id": owner_id or "",
            "name": owner_id or "Unassigned",
            "role": "",
            "label": owner_id or "Unassigned",
        }
    return {
        "id": owner.id,
        "name": owner.name,
        "role": owner.role,
        "label": f"{owner.name} ({_label(owner.role)})",
    }


def _next_action_views(next_actions: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "id": action,
            "label": _label(action),
            "evidence": [
                {"id": evidence, "label": _label(evidence)}
                for evidence in (required or [])
            ],
        }
        for action, required in next_actions.items()
    ]


def _worklist_view_rows(store: SQLiteStore, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        next_actions = _next_action_views(row.get("next", {}))
        result.append(
            {
                **row,
                "state_label": _label(row.get("state")),
                "owner_view": _owner_view(store, row.get("owner")),
                "next_actions": next_actions,
                "recommended": next_actions[0] if next_actions else None,
                "case_url": f"/cases/{quote(str(row['case']), safe='')}",
            }
        )
    return result


def _patient_status(state: str, closed: bool) -> str:
    if closed:
        return "Your care journey is complete, and the closure has been recorded."
    return {
        "result_issued": "Your screening result is ready and the care team is preparing the next step.",
        "patient_notified": "Your care team has sent a notification and is waiting for your acknowledgement.",
        "patient_acknowledged": "Your acknowledgement is recorded and a primary care review is next.",
        "referral_created": "Your referral is being reviewed by the care coordination team.",
        "referral_rejected": "Your referral needs another review before care can continue.",
        "referral_accepted": "Your referral was accepted and a specialist appointment is next.",
        "appointment_booked": "An appointment is booked and the team is waiting for the visit.",
        "care_received": "Your visit is recorded and the care team is preparing closure.",
        "escalated": "A care team member is reviewing the next step with priority.",
    }.get(state, "Your care team is reviewing the next step.")


def _case_view_context(store: SQLiteStore, payload: dict[str, Any], case: Case) -> dict[str, Any]:
    record = store.get_case_record(case.id) or {}
    patient = store.get_user(str(record["patient_user_id"])) if record.get("patient_user_id") else None
    timeline = []
    for event in case.audit:
        approval = event.get("approval")
        timeline.append(
            {
                "ts": _format_datetime(event.get("ts")),
                "ts_iso": _datetime_attr(event.get("ts")),
                "actor": event.get("actor", "—"),
                "action": _label(event.get("action")),
                "from_state": _label(event.get("from")),
                "to_state": _label(event.get("to")),
                "evidence": [_label(item) for item in event.get("evidence", [])],
                "approval": approval if isinstance(approval, Mapping) else None,
                "owner": event.get("owner"),
            }
        )

    evidence = [
        {
            "id": item.id,
            "kind": item.kind,
            "kind_label": _label(item.kind),
            "recorded_at": _format_datetime(item.recorded_at),
            "recorded_by": item.recorded_by or "—",
        }
        for item in store.list_evidence(case.id)
    ]
    next_actions = _next_action_views(payload.get("next", {}))
    return {
        **payload,
        "state_label": _label(payload.get("state")),
        "owner_view": _owner_view(store, payload.get("owner")),
        "patient_name": patient.name if patient else "Not linked",
        "patient_id": patient.id if patient else None,
        "patient_url": f"/patient/{quote(patient.id, safe='')}" if patient else None,
        "next_actions": next_actions,
        "timeline": timeline,
        "evidence_records": evidence,
        "case_url": f"/cases/{quote(case.id, safe='')}",
    }


def _patient_case_view(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": payload["id"],
        "case_url": f"/cases/{quote(payload['id'], safe='')}",
        "state_label": _label(payload.get("state")),
        "plain_status": _patient_status(payload.get("state", ""), bool(payload.get("closed"))),
        "updated_at": _format_datetime(payload.get("updated_at")),
        "urgent": bool(payload.get("urgent")),
        "closed": bool(payload.get("closed")),
    }


def _patient_safe_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return only the patient-facing status projection."""

    return _patient_case_view(payload)


def _patient_identity_payload(user: User) -> dict[str, Any]:
    """Expose only identity fields needed by the patient's own status page."""

    return {"name": user.name, "role": "patient"}


def _simulation_view(simulation: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "name": "Baseline" if name == "baseline" else _label(name),
            "cases": values["cases"],
            "closed": values["closed"],
            "closure_rate": values["closure_rate"],
            "unresolved": values["unresolved"],
        }
        for name, values in simulation.items()
    ]


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


def _message_draft_payload(draft: MessageDraftRecord) -> dict[str, Any]:
    """Expose normalized draft status and its human-readable equivalent."""

    return {
        "id": draft.id,
        "draft_id": draft.id,
        "case_id": draft.case_id,
        "patient_user_id": draft.patient_user_id,
        "language": draft.language,
        "template": draft.template,
        "message": draft.message,
        "provider": draft.provider,
        "review_required": draft.review_required,
        "review_flag": draft.review_flag,
        "status": draft.status,
        "status_label": draft.status.replace("_", " "),
        "created_by": draft.created_by,
        "created_at": draft.created_at.isoformat(),
        "approved_by": draft.approved_by,
        "approved_at": draft.approved_at.isoformat() if draft.approved_at else None,
        "approval_note": draft.approval_note,
        "notification_id": draft.notification_id,
        "created_role": draft.created_role,
        "created_state": draft.created_state,
        "created_provenance": _jsonable(draft.created_provenance),
        "approved_role": draft.approved_role,
        "approved_principal_id": draft.approved_principal_id,
        "approved_state": draft.approved_state,
        "approved_provenance": _jsonable(draft.approved_provenance),
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
        "version": case.version,
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
    session_idle_age: int | None = None,
    session_absolute_age: int | None = None,
    message_provider: AIProvider | None = None,
    oidc_provider: OIDCProvider | None = None,
    settings: CareFlowSettings | None = None,
) -> FastAPI:
    """Build an isolated CareFlow API application.

    ``db_path``, ``clock`` and ``pathway`` are explicit inputs so tests can use
    a temporary SQLite file, a fixed timestamp, and a small deterministic
    pathway without patching module globals.
    """

    resolved_settings = settings or CareFlowSettings.from_env()
    resolved_settings.validate()
    resolved_pathway = _resolve_pathway(pathway)
    resolved_clock = _clock_factory(clock)
    resolved_db_path = (
        resolved_settings.database_path
        if str(db_path) == "careflow.db" and settings is not None
        else db_path
    )
    store = SQLiteStore(resolved_db_path)
    resolved_session_secret = (
        session_secret
        or resolved_settings.session_secret
        or (secrets.token_urlsafe(32) if resolved_settings.demo_mode else "")
    )
    resolved_session_max_age = session_max_age
    if resolved_session_max_age is None:
        resolved_session_max_age = resolved_settings.session_max_age or DEFAULT_SESSION_MAX_AGE
    if not resolved_session_secret:
        raise ConfigurationError("CAREFLOW_SESSION_SECRET must not be blank")
    if resolved_session_secret.lower() in {
        "replace-with-a-long-random-secret",
        "secret",
        "password",
        "changeme",
        "admin",
        "default",
        "test",
    }:
        raise ConfigurationError("CAREFLOW_SESSION_SECRET must not use a default placeholder or insecure secret")
    if resolved_session_max_age <= 0:
        raise ValueError("session_max_age must be positive")
    resolved_session_idle_age = session_idle_age
    if resolved_session_idle_age is None:
        resolved_session_idle_age = resolved_settings.session_idle_age or DEFAULT_SESSION_IDLE_AGE
    resolved_session_absolute_age = session_absolute_age
    if resolved_session_absolute_age is None:
        resolved_session_absolute_age = (
            resolved_settings.session_absolute_age or DEFAULT_SESSION_ABSOLUTE_AGE
        )
    if resolved_session_idle_age <= 0 or resolved_session_absolute_age <= 0:
        raise ValueError("session idle and absolute ages must be positive")
    if resolved_session_idle_age > resolved_session_absolute_age:
        raise ValueError("session idle age must not exceed absolute session age")
    resolved_oidc_provider = oidc_provider or UnavailableOIDCProvider()
    resolved_message_provider = message_provider or create_message_provider()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        worker = OutboxWorker(store, poll_interval=1.0)
        await worker.start()
        app.state.outbox_worker = worker
        try:
            yield
        finally:
            await worker.stop()
            store.close()

    app = FastAPI(title="CareFlow API", version="2.0", lifespan=lifespan)
    app.state.store = store
    app.state.pathway = resolved_pathway
    app.state.clock = resolved_clock
    app.state.session_secret = resolved_session_secret
    app.state.session_max_age = resolved_session_max_age
    app.state.session_idle_age = resolved_session_idle_age
    app.state.session_absolute_age = resolved_session_absolute_age
    app.state.session_cookie = SESSION_COOKIE
    app.state.settings = resolved_settings
    app.state.mode = resolved_settings.mode
    app.state.safe_diagnostics = resolved_settings.safe_diagnostics()
    app.state.message_provider = resolved_message_provider
    app.state.ai_provider = resolved_message_provider
    app.state.oidc_provider = resolved_oidc_provider
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/health", include_in_schema=False)
    @app.get("/health/live", include_in_schema=False)
    def health_live() -> dict[str, Any]:
        return {"status": "ok", "mode": resolved_settings.mode}

    @app.get("/ready", include_in_schema=False)
    @app.get("/health/ready", include_in_schema=False)
    def health_ready() -> Response:
        checks: dict[str, Any] = {
            "mode": resolved_settings.mode,
            "configuration": "ok",
            "pathway": "ok",
            "database": "ok",
            "schema_version": None,
            "supported_schema_version": CURRENT_SCHEMA_VERSION,
            "foreign_keys": "on",
            "integrity": "ok",
            "writable_storage": "ok",
        }
        try:
            checks["schema_version"] = store.schema_version
            store.verify_readiness()
        except Exception as exc:
            checks["database"] = "failed"
            checks["integrity"] = "failed" if "integrity" in str(exc).lower() else checks["integrity"]
            checks["foreign_keys"] = "failed" if "foreign" in str(exc).lower() else checks["foreign_keys"]
            checks["writable_storage"] = "failed" if "writable" in str(exc).lower() else checks["writable_storage"]
            checks["reason"] = type(exc).__name__.lower()
            return JSONResponse(status_code=503, content={"status": "not_ready", "checks": checks})
        return JSONResponse(status_code=200, content={"status": "ready", "checks": checks})

    @app.exception_handler(TransitionError)
    async def transition_error_handler(_: Any, exc: TransitionError) -> JSONResponse:
        message = str(exc)
        status_code = 422 if message.startswith("missing evidence") else 409
        return JSONResponse(
            status_code=status_code,
            content={"error": "transition_error", "detail": message},
        )

    @app.exception_handler(ConcurrencyConflict)
    async def concurrency_error_handler(_: Any, exc: ConcurrencyConflict) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={
                "error": "concurrency_conflict",
                "detail": str(exc),
                "action": "reload the current case and retry the mutation",
            },
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> Response:
        if _request_wants_html(request):
            return templates.TemplateResponse(
                request=request,
                name="error.html",
                context={"status_code": exc.status_code, "detail": str(exc.detail)},
                status_code=exc.status_code,
                headers=exc.headers,
            )
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=exc.headers,
        )

    def now() -> datetime:
        value = resolved_clock()
        if not isinstance(value, datetime):
            raise RuntimeError("clock must return datetime")
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    _rate_limit_lock = threading.Lock()
    _request_counts: dict[str, list[float]] = {}

    def check_rate_limit(request: Request, max_requests: int = 120, window_seconds: float = 60.0) -> None:
        client_ip = request.client.host if request.client else "unknown"
        current_ts = time.time()
        with _rate_limit_lock:
            history = _request_counts.setdefault(client_ip, [])
            cutoff = current_ts - window_seconds
            _request_counts[client_ip] = [ts for ts in history if ts > cutoff]
            if len(_request_counts[client_ip]) >= max_requests:
                raise HTTPException(status_code=429, detail="too many requests, rate limit exceeded")
            _request_counts[client_ip].append(current_ts)

    def authenticated_session(request: Request) -> tuple[User, SessionRecord]:
        handle = request.cookies.get(SESSION_COOKIE)
        if not handle:
            raise HTTPException(status_code=401, detail="authentication required")
        session = store.get_session(handle)
        current = now()
        if session is None or session.revoked_at is not None:
            raise HTTPException(status_code=401, detail="invalid or expired session")
        if resolved_settings.mode == "pilot" and (
            current >= session.idle_expires_at or current >= session.absolute_expires_at
        ):
            store.revoke_session(handle, revoked_at=current)
            raise HTTPException(status_code=401, detail="invalid or expired session")
        user = store.get_user(session.user_id)
        if user is None or not user.is_active or not user.role:
            raise HTTPException(status_code=401, detail="user is inactive or unknown")
        if resolved_settings.mode == "pilot":
            idle_deadline = min(
                current + timedelta(seconds=resolved_session_idle_age),
                session.absolute_expires_at,
            )
            store.touch_session(session.id, last_seen_at=current, idle_expires_at=idle_deadline)
        return user, session

    def authenticated_user(request: Request) -> User:
        return authenticated_session(request)[0]

    def operational_user(user: User) -> None:
        if user.role == "patient":
            raise HTTPException(status_code=403, detail="patients cannot access operational views")

    def _staff_case_in_scope(user: User, record: Mapping[str, Any]) -> bool:
        """Documented pilot team scope: assigned owner, current owner team, or approver team."""

        if user.role == "patient":
            return False
        state = str(record.get("state", ""))
        owner_role = resolved_pathway.states.get(state, {}).get("owner")
        team_roles = {
            str(state_data.get("owner"))
            for state_data in resolved_pathway.states.values()
            if state_data.get("owner")
        }
        return bool(
            record.get("owner") == user.id
            or user.role == owner_role
            or user.role in team_roles
            or user.role in resolved_pathway.approvers
        )

    def _staff_can_mutate_case(user: User, record: Mapping[str, Any]) -> bool:
        """Mutations require the assigned principal or a declared approver role."""

        return user.role != "patient" and bool(
            record.get("owner") == user.id or user.role in resolved_pathway.approvers
        )

    def authorize_surface(user: User, surface: str, *, record: Mapping[str, Any] | None = None, mutation: bool = False) -> None:
        """Apply the explicit deny-by-default route/action policy."""

        policy = ROUTE_ACTION_POLICY.get(surface)
        if policy is None:
            raise HTTPException(status_code=403, detail="route surface is not authorized")
        if record is None:
            if user.role == "patient" and "patient" not in policy:
                raise HTTPException(status_code=403, detail="patients cannot access this surface")
            if user.role != "patient" and "staff" not in policy:
                raise HTTPException(status_code=403, detail="staff cannot access this surface")
            return
        if user.role == "patient":
            if mutation and policy.get("patient") != "linked-self-mutation":
                raise HTTPException(status_code=403, detail="patients cannot mutate this surface")
            if policy.get("patient") in {"linked-safe-read", "self-only-safe", "linked-self-mutation"}:
                if record.get("patient_user_id") != user.id:
                    raise HTTPException(status_code=403, detail="patients may access only their own case")
                return
            raise HTTPException(status_code=403, detail="patients cannot access this surface")
        allowed = _staff_can_mutate_case(user, record) if mutation else _staff_case_in_scope(user, record)
        if not allowed:
            raise HTTPException(status_code=403, detail="case is outside the authenticated staff scope")

    def authorize_case_access(user: User, case_id: str, *, mutation: bool = False) -> dict[str, Any]:
        record = store.get_case_record(case_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"case not found: {case_id}")
        authorize_surface(user, "case-mutation" if mutation else "case", record=record, mutation=mutation)
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
        if user.role == owner_role and case.owner == user.id:
            return
        if user.role not in case.pw.approvers:
            raise HTTPException(status_code=403, detail="only the assigned owner or an authorized approver may transition this case")
        if not approval or approval_role != user.role or not approval.get("note"):
            raise HTTPException(
                status_code=403,
                detail="an authorized approver must submit a matching documented approval",
            )

    def authorize_message_approval(
        user: User, case: Case, draft: MessageDraftRecord | None = None
    ) -> None:
        """Keep message approval under the same owner/approver role boundary and enforce language credentials."""

        record = store.get_case_record(case.id)
        if record is None:
            raise HTTPException(status_code=404, detail="case not found")
        if not _staff_can_mutate_case(user, record):
            raise HTTPException(
                status_code=403,
                detail=f"the authenticated principal cannot approve a message draft for {case.state}",
            )
        if draft is not None and draft.language not in {"en", "en-US", ""}:
            certified_langs = getattr(user, "certified_languages", ("en",))
            is_certified = (
                draft.language in certified_langs
                or "all" in certified_langs
                or user.role in {"medical_interpreter", "certified_translator"}
            )
            if not is_certified:
                raise HTTPException(
                    status_code=403,
                    detail=(
                        f"approving {draft.language} message drafts requires certified language qualification; "
                        f"user '{user.id}' is certified for: {', '.join(certified_langs)}"
                    ),
                )

    def server_approval(user: User, case: Case, request_approval: ApprovalInput | None, timestamp: datetime) -> dict[str, Any] | None:
        """Build approval provenance from the verified session, never caller labels."""

        if request_approval is None:
            return None
        return {
            "role": user.role,
            "name": user.name,
            "principal_id": user.id,
            "state": case.state,
            "timestamp": timestamp.isoformat(),
            "note": request_approval.note,
            "provenance": {
                "source": "authenticated_session",
                "principal_id": user.id,
                "role": user.role,
                "state": case.state,
                "timestamp": timestamp.isoformat(),
            },
        }

    def server_evidence(
        user: User,
        case: Case,
        action: str,
        evidence: list[str],
        timestamp: datetime,
    ) -> list[dict[str, Any]]:
        return [
            {
                "kind": kind,
                "case_id": case.id,
                "action": action,
                "state": case.state,
                "role": user.role,
                "principal_id": user.id,
                "recorded_at": timestamp.isoformat(),
                "provenance": {
                    "source": "authenticated_session",
                    "principal_id": user.id,
                    "action": action,
                },
            }
            for kind in evidence
        ]

    def log_case_access(
        user: User,
        case_id: str,
        action: str,
        endpoint: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        store.record_access(
            user_id=user.id,
            case_id=case_id,
            action=action,
            occurred_at=now(),
            endpoint=endpoint,
            metadata=metadata,
        )

    def user_response(user: User) -> dict[str, Any]:
        payload = _user_payload(user)
        return {"user": payload, **payload}

    def case_response(user: User, case: Case, timestamp: datetime) -> dict[str, Any]:
        payload = _case_payload(store, case, timestamp)
        return _patient_safe_payload(payload) if user.role == "patient" else payload

    def set_session_cookie(response: Response, user: User) -> str:
        timestamp = now()
        handle, csrf_token, _ = store.create_session(
            user_id=user.id,
            created_at=timestamp,
            idle_expires_at=timestamp
            + timedelta(seconds=resolved_session_idle_age),
            absolute_expires_at=timestamp
            + timedelta(seconds=resolved_session_absolute_age),
        )
        response.set_cookie(
            SESSION_COOKIE,
            handle,
            max_age=resolved_session_absolute_age,
            httponly=True,
            secure=not resolved_settings.demo_mode,
            samesite="lax",
            path="/",
        )
        response.set_cookie(
            CSRF_COOKIE,
            csrf_token,
            max_age=resolved_session_absolute_age,
            httponly=False,
            secure=not resolved_settings.demo_mode,
            samesite="lax",
            path="/",
        )
        response.headers["X-CSRF-Token"] = csrf_token
        return csrf_token

    def _trusted_origin(request: Request) -> bool:
        origin = request.headers.get("origin", "").strip().rstrip("/")
        return bool(origin) and origin in resolved_settings.trusted_origins

    def _csrf_valid(request: Request) -> bool:
        handle = request.cookies.get(SESSION_COOKIE)
        token = request.headers.get(CSRF_HEADER, "")
        if not handle or not token:
            return False
        session = store.get_session(handle)
        if session is None:
            return False
        return secrets.compare_digest(
            hashlib.sha256(token.encode("utf-8")).hexdigest(), session.csrf_token_hash
        )

    @app.middleware("http")
    async def cookie_mutation_guard(request: Request, call_next: Any) -> Response:
        if resolved_settings.mode == "pilot" and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            if request.url.path not in {"/auth/login/start", "/auth/start", "/auth/callback"}:
                if not _trusted_origin(request):
                    return JSONResponse(status_code=403, content={"detail": "trusted origin required"})
                if request.cookies.get(SESSION_COOKIE) and not _csrf_valid(request):
                    return JSONResponse(status_code=403, content={"detail": "valid CSRF token required"})
        return await call_next(request)

    def landing_context(request: Request) -> dict[str, Any]:
        try:
            user = authenticated_user(request)
        except HTTPException:
            user = None
        demo_links = []
        if resolved_settings.demo_mode and resolved_settings.allow_demo_login:
            for demo_user in store.list_users(active_only=True):
                if not demo_user.id.startswith("demo-"):
                    continue
                target = (
                    f"/patient/{quote(demo_user.id, safe='')}"
                    if demo_user.role == "patient"
                    else "/worklist"
                )
                demo_links.append(
                    {
                        "id": demo_user.id,
                        "name": demo_user.name,
                        "role": _label(demo_user.role),
                        "login_url": f"/demo-login/{quote(demo_user.id, safe='')}",
                        "target": target,
                    }
                )
        return {
            "user": user,
            "demo_links": demo_links,
            "mode": resolved_settings.mode,
            "synthetic_only": resolved_settings.synthetic_only,
        }

    @app.get("/", status_code=200, include_in_schema=False)
    @app.get("/login", status_code=200, include_in_schema=False)
    def landing(request: Request) -> Response:
        return templates.TemplateResponse(
            request=request,
            name="landing.html",
            context=landing_context(request),
        )

    @app.get("/demo-login/{user_id}", status_code=303, include_in_schema=False)
    def demo_login(user_id: str) -> Response:
        if not resolved_settings.demo_mode or not resolved_settings.allow_demo_login:
            raise HTTPException(status_code=404, detail="demo login is disabled in pilot mode")
        if not user_id.startswith("demo-"):
            raise HTTPException(status_code=404, detail="demo user not found")
        user = store.get_user(user_id)
        if user is None or not user.is_active or not user.role:
            raise HTTPException(status_code=404, detail="demo user not found")
        target = f"/patient/{quote(user.id, safe='')}" if user.role == "patient" else "/worklist"
        response = RedirectResponse(url=target, status_code=303)
        set_session_cookie(response, user)
        return response

    @app.get("/auth/login/start", status_code=303)
    @app.get("/auth/start", status_code=303, include_in_schema=False)
    def oidc_login_start() -> Response:
        if resolved_settings.mode != "pilot":
            raise HTTPException(status_code=404, detail="OIDC login is available only in pilot mode")
        provider_issuer = getattr(resolved_oidc_provider, "issuer", "").rstrip("/")
        if not provider_issuer or provider_issuer != resolved_settings.oidc_issuer.rstrip("/"):
            raise HTTPException(status_code=503, detail="configured OIDC provider is unavailable")
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        code_verifier = secrets.token_urlsafe(48)
        try:
            code_challenge = pkce_challenge(code_verifier)
            authorization_url = resolved_oidc_provider.authorization_url(
                client_id=resolved_settings.oidc_client_id,
                redirect_uri=resolved_settings.oidc_redirect_uri,
                state=state,
                nonce=nonce,
                code_challenge=code_challenge,
            )
            store.create_oidc_login_state(
                state=state,
                nonce=nonce,
                code_verifier=code_verifier,
                redirect_uri=resolved_settings.oidc_redirect_uri,
                created_at=now(),
                expires_at=now() + timedelta(minutes=10),
            )
        except (OIDCProviderUnavailable, OIDCValidationError, sqlite3.IntegrityError) as exc:
            raise HTTPException(status_code=503, detail="OIDC login is unavailable") from exc
        return RedirectResponse(url=authorization_url, status_code=303)

    @app.get("/auth/callback", status_code=200)
    def oidc_callback(
        request: Request,
        response: Response,
        code: str | None = None,
        state: str | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        if resolved_settings.mode != "pilot":
            raise HTTPException(status_code=404, detail="OIDC callback is available only in pilot mode")
        if error or not state or not code:
            raise HTTPException(status_code=401, detail="invalid OIDC callback")
        timestamp = now()
        login_state = store.consume_oidc_login_state(state, consumed_at=timestamp)
        if login_state is None or timestamp >= login_state.expires_at:
            raise HTTPException(status_code=401, detail="invalid or expired OIDC state")
        if login_state.redirect_uri != resolved_settings.oidc_redirect_uri:
            raise HTTPException(status_code=401, detail="OIDC redirect URI mismatch")
        try:
            token = resolved_oidc_provider.exchange_code(
                code=code,
                client_id=resolved_settings.oidc_client_id,
                redirect_uri=login_state.redirect_uri,
                code_verifier=login_state.code_verifier,
            )
            claims = resolved_oidc_provider.verify_id_token(
                token,
                client_id=resolved_settings.oidc_client_id,
                nonce=login_state.nonce,
                now=int(timestamp.timestamp()),
            )
        except (OIDCProviderUnavailable, OIDCValidationError) as exc:
            raise HTTPException(status_code=401, detail="OIDC identity verification failed") from exc
        if claims.issuer.rstrip("/") != resolved_settings.oidc_issuer.rstrip("/"):
            raise HTTPException(status_code=401, detail="OIDC issuer is not trusted")
        user = store.resolve_identity(claims.issuer, claims.subject)
        if user is None or not user.is_active or not user.role:
            raise HTTPException(status_code=403, detail="OIDC identity is not approved")
        set_session_cookie(response, user)
        return user_response(user)

    @app.post("/auth/login", status_code=200)
    @app.post("/login", status_code=200, include_in_schema=False)
    def login(request: LoginInput, response: Response) -> dict[str, Any]:
        if not resolved_settings.demo_mode or not resolved_settings.allow_demo_login:
            raise HTTPException(status_code=503, detail="demo login is disabled; use the configured OIDC flow")
        user = store.get_user(request.identifier)
        if user is None or not user.is_active or not user.role:
            raise HTTPException(status_code=401, detail="unknown or inactive user")
        set_session_cookie(response, user)
        return user_response(user)

    @app.post("/auth/logout", status_code=200)
    @app.post("/logout", status_code=200, include_in_schema=False)
    def logout(request: Request, response: Response) -> dict[str, bool]:
        handle = request.cookies.get(SESSION_COOKIE)
        if handle:
            store.revoke_session(handle, revoked_at=now())
        response.delete_cookie(SESSION_COOKIE, path="/")
        response.delete_cookie(CSRF_COOKIE, path="/")
        return {"ok": True}

    @app.get("/auth/current-user", status_code=200)
    @app.get("/auth/me", status_code=200, include_in_schema=False)
    @app.get("/current-user", status_code=200, include_in_schema=False)
    def current_user(request: Request) -> dict[str, Any]:
        return user_response(authenticated_user(request))

    @app.post("/cases", status_code=201)
    def create_case(request: CreateCaseInput, http_request: Request) -> dict[str, Any]:
        user = authenticated_user(http_request)
        authorize_surface(user, "case-mutation", mutation=True)
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
        case.audit[0].update(
            {
                "actor_role": user.role,
                "provenance": {
                    "source": "authenticated_session",
                    "principal_id": user.id,
                    "role": user.role,
                    "state": case.state,
                    "timestamp": timestamp.isoformat(),
                },
            }
        )
        try:
            store.save_case(case, patient_user_id=patient_user_id)
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=422, detail=f"invalid case references: {exc}") from exc
        log_case_access(user, case.id, "mutation", "/cases")
        return case_response(user, case, timestamp)

    @app.post("/cases/{case_id}/transition", status_code=200)
    @app.post("/cases/{case_id}/transitions", status_code=200, include_in_schema=False)
    def transition_case(case_id: str, request: TransitionInput, http_request: Request) -> dict[str, Any]:
        user = authenticated_user(http_request)
        case = _load_case(store, resolved_pathway, case_id)
        authorize_case_access(user, case_id, mutation=True)
        timestamp = now()
        approval = server_approval(user, case, request.approval, timestamp)
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
            evidence_provenance=server_evidence(user, case, request.action, request.evidence, timestamp),
        )
        store.save_case(case, expected_version=case.version)
        log_case_access(user, case.id, "mutation", f"/cases/{case_id}/transition")
        return case_response(user, case, timestamp)

    @app.post("/cases/{case_id}/assign", status_code=200)
    def assign_case(case_id: str, request: AssignInput, http_request: Request) -> dict[str, Any]:
        user = authenticated_user(http_request)
        case = _load_case(store, resolved_pathway, case_id)
        authorize_case_access(user, case_id, mutation=True)
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
        case.assign(
            target.id,
            user.id,
            timestamp,
            actor_role=user.role,
            provenance={"source": "authenticated_session", "principal_id": user.id},
        )
        store.save_case(case, expected_version=case.version)
        log_case_access(user, case.id, "mutation", f"/cases/{case_id}/assign")
        return case_response(user, case, timestamp)

    @app.post("/cases/{case_id}/message-drafts", status_code=201)
    @app.post("/cases/{case_id}/drafts", status_code=201, include_in_schema=False)
    @app.post("/cases/{case_id}/draft-message", status_code=201, include_in_schema=False)
    def create_message_draft(
        case_id: str,
        http_request: Request,
        request: MessageDraftInput | None = None,
    ) -> dict[str, Any]:
        """Generate and persist a review-controlled patient message.

        This route only reads the case context and writes ``message_drafts``;
        it deliberately does not call ``Case.transition`` or ``save_case``.
        """

        user = authenticated_user(http_request)
        case = _load_case(store, resolved_pathway, case_id)
        record = authorize_case_access(user, case_id)
        authorize_case_access(user, case_id, mutation=True)
        request = request or MessageDraftInput()
        patient = store.get_user(str(record["patient_user_id"])) if record.get("patient_user_id") else None
        context = {
            "case_id": case.id,
            "state": case.state,
            "urgent": case.urgent,
            "patient_name": patient.name if patient else None,
        }
        try:
            generated = app.state.message_provider.draft(
                context,
                language=request.language,
                template=request.template,
            )
        except ProviderUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ProviderError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        timestamp = now()
        review_required = generated.review_required or generated.language != "en"
        review_flag = generated.review_flag
        if generated.language != "en":
            review_flag = REVIEW_FLAG
        try:
            draft_id = store.add_message_draft(
                case_id=case.id,
                patient_user_id=record.get("patient_user_id"),
                language=generated.language,
                template=generated.template,
                message=generated.message,
                provider=generated.provider,
                review_required=review_required,
                review_flag=review_flag,
                created_by=user.id,
                created_at=timestamp,
                created_role=user.role,
                created_state=case.state,
                created_provenance={
                    "source": "authenticated_session",
                    "principal_id": user.id,
                    "role": user.role,
                    "state": case.state,
                    "timestamp": timestamp.isoformat(),
                },
            )
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        draft = store.get_message_draft(draft_id)
        assert draft is not None
        log_case_access(
            user,
            case.id,
            "mutation",
            f"/cases/{case_id}/message-drafts",
            metadata={"draft_id": draft.id, "language": draft.language, "provider": draft.provider},
        )
        return _message_draft_payload(draft)

    @app.get("/cases/{case_id}/message-drafts", status_code=200)
    @app.get("/cases/{case_id}/drafts", status_code=200, include_in_schema=False)
    def list_case_message_drafts(
        case_id: str,
        http_request: Request,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        user = authenticated_user(http_request)
        _load_case(store, resolved_pathway, case_id)
        authorize_case_access(user, case_id)
        if user.role == "patient":
            raise HTTPException(status_code=403, detail="patients cannot access message drafts")
        normalized_status = status.strip().lower().replace(" ", "_") if status else None
        try:
            drafts = store.list_message_drafts(case_id=case_id, status=normalized_status)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        log_case_access(user, case_id, "read", f"/cases/{case_id}/message-drafts")
        return [_message_draft_payload(draft) for draft in drafts]

    @app.get("/message-drafts", status_code=200)
    @app.get("/drafts", status_code=200, include_in_schema=False)
    def list_message_drafts(
        http_request: Request,
        status: str | None = None,
        case_id: str | None = None,
    ) -> list[dict[str, Any]]:
        user = authenticated_user(http_request)
        authorize_surface(user, "draft-read")
        if user.role == "patient":
            raise HTTPException(status_code=403, detail="patients cannot access the draft collection")
        if case_id is not None:
            _load_case(store, resolved_pathway, case_id)
            authorize_case_access(user, case_id)
        normalized_status = status.strip().lower().replace(" ", "_") if status else None
        try:
            drafts = store.list_message_drafts(case_id=case_id, status=normalized_status)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        visible = []
        for draft in drafts:
            record = store.get_case_record(draft.case_id)
            if record is not None and _staff_case_in_scope(user, record):
                visible.append(draft)
        for draft_case_id in sorted({draft.case_id for draft in visible}):
            log_case_access(user, draft_case_id, "read", "/message-drafts")
        return [_message_draft_payload(draft) for draft in visible]

    @app.get("/message-drafts/{draft_id}", status_code=200)
    def get_message_draft(draft_id: str, http_request: Request) -> dict[str, Any]:
        user = authenticated_user(http_request)
        draft = store.get_message_draft(draft_id)
        if draft is None:
            raise HTTPException(status_code=404, detail=f"message draft not found: {draft_id}")
        if user.role == "patient":
            raise HTTPException(status_code=403, detail="patients cannot access message drafts")
        _load_case(store, resolved_pathway, draft.case_id)
        authorize_case_access(user, draft.case_id)
        log_case_access(user, draft.case_id, "read", f"/message-drafts/{draft_id}")
        return _message_draft_payload(draft)

    @app.post("/message-drafts/{draft_id}/approve", status_code=200)
    @app.post("/drafts/{draft_id}/approve", status_code=200, include_in_schema=False)
    @app.post(
        "/cases/{case_id}/message-drafts/{draft_id}/approve",
        status_code=200,
        include_in_schema=False,
    )
    def approve_message_draft(
        draft_id: str,
        http_request: Request,
        request: MessageApprovalInput | None = None,
        case_id: str | None = None,
    ) -> dict[str, Any]:
        user = authenticated_user(http_request)
        draft = store.get_message_draft(draft_id)
        if draft is None:
            raise HTTPException(status_code=404, detail=f"message draft not found: {draft_id}")
        if case_id is not None and case_id != draft.case_id:
            raise HTTPException(status_code=404, detail="message draft is not attached to this case")
        case = _load_case(store, resolved_pathway, draft.case_id)
        authorize_case_access(user, draft.case_id)
        authorize_message_approval(user, case, draft=draft)
        patient_user_id = draft.patient_user_id or (store.get_case_record(draft.case_id) or {}).get(
            "patient_user_id"
        )
        patient = store.get_user(patient_user_id) if patient_user_id else None
        if patient is None or not patient.is_active or patient.role != "patient":
            raise HTTPException(
                status_code=422,
                detail="a linked active patient is required before a message can be approved",
            )
        approval = request or MessageApprovalInput()
        timestamp = now()
        try:
            updated, notification = store.approve_message_draft(
                draft.id,
                approved_by=user.id,
                approved_at=timestamp,
                approval_note=approval.note,
                recipient=patient.email or patient.id,
                consent_user_id=patient.id,
                approved_role=user.role,
                approved_state=case.state,
                approved_provenance={
                    "source": "authenticated_session",
                    "principal_id": user.id,
                    "role": user.role,
                    "state": case.state,
                    "timestamp": timestamp.isoformat(),
                },
                payload={
                    "patient_user_id": patient.id,
                    "review_required": draft.review_required,
                },
            )
        except ValueError as exc:
            detail = str(exc)
            if "granted, unexpired" in detail.lower() or "requires a granted" in detail.lower():
                raise HTTPException(status_code=403, detail=detail) from exc
            raise HTTPException(status_code=409, detail=detail) from exc
        log_case_access(
            user,
            draft.case_id,
            "mutation",
            f"/message-drafts/{draft_id}/approve",
            metadata={"draft_id": draft.id, "notification_id": notification["id"]},
        )
        result = _message_draft_payload(updated)
        result["notification"] = _jsonable(notification)
        return result

    @app.get("/cases/{case_id}/audit", status_code=200)
    @app.get("/audit/{case_id}", status_code=200, include_in_schema=False)
    def get_case_audit(case_id: str, http_request: Request) -> list[dict[str, Any]]:
        user = authenticated_user(http_request)
        case = _load_case(store, resolved_pathway, case_id)
        authorize_case_access(user, case_id)
        if user.role == "patient":
            raise HTTPException(status_code=403, detail="patients cannot access operational audit history")
        log_case_access(user, case_id, "read", f"/cases/{case_id}/audit")
        return _jsonable(case.audit)

    @app.get("/cases/{case_id}/access-logs", status_code=200)
    @app.get("/cases/{case_id}/access-log", status_code=200, include_in_schema=False)
    def get_case_access_logs(case_id: str, http_request: Request) -> list[dict[str, Any]]:
        user = authenticated_user(http_request)
        _load_case(store, resolved_pathway, case_id)
        authorize_case_access(user, case_id)
        if user.role == "patient":
            raise HTTPException(status_code=403, detail="patients cannot access operational access logs")
        logs = store.list_access_logs(case_id=case_id)
        log_case_access(user, case_id, "read", f"/cases/{case_id}/access-logs")
        return _jsonable([log.__dict__ for log in logs])

    @app.get("/access-logs", status_code=200)
    @app.get("/access-log", status_code=200, include_in_schema=False)
    def get_access_logs(http_request: Request, case_id: str | None = None) -> list[dict[str, Any]]:
        user = authenticated_user(http_request)
        if user.role == "patient":
            raise HTTPException(status_code=403, detail="patients cannot access operational access logs")
        if case_id is not None:
            _load_case(store, resolved_pathway, case_id)
            authorize_case_access(user, case_id)
        logs = store.list_access_logs(case_id=case_id)
        visible = [
            log for log in logs
            if (record := store.get_case_record(log.case_id)) is not None and _staff_case_in_scope(user, record)
        ]
        for visible_case_id in sorted({log.case_id for log in visible}):
            log_case_access(user, visible_case_id, "read", "/access-logs")
        return _jsonable([log.__dict__ for log in visible])

    @app.post("/cases/{case_id}/consent", status_code=200)
    @app.post("/consents/{case_id}", status_code=200, include_in_schema=False)
    def set_case_consent(case_id: str, request: ConsentInput, http_request: Request) -> dict[str, Any]:
        user = authenticated_user(http_request)
        record = authorize_case_access(user, case_id, mutation=user.role != "patient")
        patient_id = record.get("patient_user_id")
        if not patient_id:
            raise HTTPException(status_code=422, detail="case has no linked patient")
        if user.role == "patient" and user.id != patient_id:
            raise HTTPException(status_code=403, detail="patients may manage consent only for their linked case")
        timestamp = now()
        consent_id = f"consent:{case_id}:{patient_id}:{request.consent_type}"
        try:
            store.add_consent(
                Consent(
                    id=consent_id,
                    user_id=patient_id,
                    case_id=case_id,
                    consent_type=request.consent_type,
                    status=request.status,
                    granted_at=timestamp,
                    expires_at=request.expires_at,
                    source="authenticated_session",
                    principal_id=user.id,
                    provenance={
                        "source": "authenticated_session",
                        "principal_id": user.id,
                        "role": user.role,
                        "case_id": case_id,
                        "timestamp": timestamp.isoformat(),
                    },
                )
            )
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        log_case_access(user, case_id, "mutation", f"/cases/{case_id}/consent")
        current = store.list_consents(user_id=patient_id, case_id=case_id)
        return {
            "consent": _jsonable(current[-1].__dict__ if current else {}),
            "history": _jsonable([item.__dict__ for item in store.list_consent_history(case_id=case_id)]),
        }

    @app.get("/cases/{case_id}/consent-history", status_code=200)
    @app.get("/consents/{case_id}/history", status_code=200, include_in_schema=False)
    def get_case_consent_history(case_id: str, http_request: Request) -> list[dict[str, Any]]:
        user = authenticated_user(http_request)
        record = authorize_case_access(user, case_id)
        if user.role == "patient" and record.get("patient_user_id") != user.id:
            raise HTTPException(status_code=403, detail="patients may access only their own consent history")
        log_case_access(user, case_id, "read", f"/cases/{case_id}/consent-history")
        return _jsonable([item.__dict__ for item in store.list_consent_history(case_id=case_id)])

    @app.get("/cases/{case_id}", status_code=200, response_model=None)
    def get_case(case_id: str, http_request: Request) -> dict[str, Any] | Response:
        user = authenticated_user(http_request)
        case = _load_case(store, resolved_pathway, case_id)
        record = authorize_case_access(user, case_id)
        payload = _case_payload(store, case, now())
        log_case_access(user, case.id, "read", f"/cases/{case_id}")
        if user.role == "patient":
            safe = _patient_safe_payload(payload)
            if _request_wants_html(http_request):
                patient_id = str(record.get("patient_user_id"))
                patient = store.get_user(patient_id)
                return templates.TemplateResponse(
                    request=http_request,
                    name="patient.html",
                    context={
                        "user": user,
                        "patient": patient,
                        "cases": [safe],
                    },
                )
            return safe
        if _request_wants_html(http_request):
            return templates.TemplateResponse(
                request=http_request,
                name="case.html",
                context={
                    "user": user,
                    "case": _case_view_context(store, payload, case),
                },
            )
        return payload

    @app.get("/patient/{patient_id}", status_code=200, response_model=None)
    def get_patient(patient_id: str, http_request: Request) -> dict[str, Any] | Response:
        user = authenticated_user(http_request)
        authorize_surface(user, "patient-collection")
        patient = store.get_user(patient_id)
        if patient is None or not patient.is_active or patient.role != "patient":
            raise HTTPException(status_code=404, detail=f"patient not found: {patient_id}")
        if user.role == "patient" and user.id != patient.id:
            raise HTTPException(status_code=403, detail="patients may access only their own patient status")

        timestamp = now()
        records = [
            record for record in store.list_case_records() if record.get("patient_user_id") == patient.id
        ]
        case_payloads = []
        patient_cases = []
        for record in records:
            case = _load_case(store, resolved_pathway, record["id"])
            payload = _case_payload(store, case, timestamp)
            if user.role != "patient" and not _staff_case_in_scope(user, record):
                continue
            safe = _patient_safe_payload(payload)
            case_payloads.append(safe if user.role == "patient" else payload)
            patient_cases.append(safe)
            log_case_access(user, case.id, "read", f"/patient/{patient_id}")

        result = {
            "patient": _patient_identity_payload(patient) if user.role == "patient" else _user_payload(patient),
            "cases": case_payloads,
        }
        if _request_wants_html(http_request):
            return templates.TemplateResponse(
                request=http_request,
                name="patient.html",
                context={"user": user, "patient": patient, "cases": patient_cases},
            )
        return result

    @app.get("/worklist", status_code=200, response_model=None)
    def get_worklist(
        http_request: Request,
        page: int = 1,
        limit: int = 50,
        owner: str | None = None,
        urgent: bool | None = None,
        state: str | None = None,
    ) -> list[dict[str, Any]] | Response:
        user = authenticated_user(http_request)
        if user.role == "patient":
            raise HTTPException(status_code=403, detail="patients cannot access operational views")
        authorize_surface(user, "worklist")
        timestamp = now()
        offset = max(0, (page - 1) * limit)
        sql_records = store.get_worklist_cases(
            owner=owner, urgent=urgent, state=state, limit=limit, offset=offset
        )
        records = [record for record in sql_records if _staff_case_in_scope(user, record)]
        cases = [_load_case(store, resolved_pathway, record["id"]) for record in records]
        rows = _jsonable(worklist(cases, timestamp))
        for record in records:
            log_case_access(user, record["id"], "worklist", "/worklist")
        if _request_wants_html(http_request):
            return templates.TemplateResponse(
                request=http_request,
                name="worklist.html",
                context={
                    "user": user,
                    "rows": _worklist_view_rows(store, rows),
                    "as_of": _format_datetime(timestamp),
                    "page": page,
                    "limit": limit,
                    "total_open": store.count_worklist_cases(owner=owner, urgent=urgent, state=state),
                },
            )
        return rows

    @app.get("/metrics", status_code=200, response_model=None)
    def get_metrics(http_request: Request) -> dict[str, Any] | Response:
        user = authenticated_user(http_request)
        if user.role == "patient":
            raise HTTPException(status_code=403, detail="patients cannot access operational views")
        authorize_surface(user, "metrics")
        timestamp = now()
        records = [record for record in store.list_case_records() if _staff_case_in_scope(user, record)]
        cases = [_load_case(store, resolved_pathway, record["id"]) for record in records]
        rows = worklist(cases, timestamp)
        result = compute_metrics(cases, resolved_pathway, timestamp)
        result["worklist"] = len(rows)
        result["worklist_cases"] = len(rows)
        for record in records:
            log_case_access(user, record["id"], "metrics", "/metrics")
        if _request_wants_html(http_request):
            return templates.TemplateResponse(
                request=http_request,
                name="metrics.html",
                context={
                    "user": user,
                    "metrics": result,
                    "state_counts": [
                        {"label": _label(state), "count": count}
                        for state, count in result["by_state"].items()
                    ],
                    "observed_metrics": result["observed_metrics"],
                    "simulation": _simulation_view(compare()),
                    "as_of": _format_datetime(timestamp),
                },
            )
        return result

    @app.post("/api/ehr/appointments/webhook", status_code=200)
    @app.post("/ehr/appointments/webhook", status_code=200, include_in_schema=False)
    def handle_ehr_appointment_webhook(
        payload: EHRAppointmentWebhookInput,
        http_request: Request,
    ) -> dict[str, Any]:
        """Ingest FHIR R4 appointment updates from EHR and trigger automated case workflows."""
        check_rate_limit(http_request)
        case_id = payload.case_id
        if not case_id and payload.patient_id:
            records = store.list_case_records()
            for r in records:
                if r.get("patient_user_id") == payload.patient_id and not r.get("closed"):
                    case_id = r.get("id")
                    break
        if not case_id:
            raise HTTPException(status_code=404, detail="case not found for ehr appointment sync")

        case = _load_case(store, resolved_pathway, case_id)
        current_time = now()
        action_taken = "none"

        status_norm = (payload.status or "").lower()
        if status_norm == "cancelled":
            if case.state == "appointment_booked":
                reason = payload.cancellation_reason or payload.note or "EHR cancellation"
                case.transition(
                    "cancel",
                    "ehr-sync",
                    current_time,
                    evidence=["cancellation_reason"],
                    evidence_provenance={"cancellation_reason": reason},
                    next_owner=case.owner,
                )
                store.save_case(case)
                action_taken = "cancelled_reschedule_queued"
            else:
                action_taken = f"ignored_state_{case.state}"
        elif status_norm in ("rescheduled", "booked"):
            if case.state == "appointment_booked":
                new_time = payload.rescheduled_start or "TBD"
                case.transition(
                    "reschedule",
                    "ehr-sync",
                    current_time,
                    evidence=["reschedule_record"],
                    evidence_provenance={"new_time": new_time, "note": payload.note or "EHR Reschedule"},
                )
                store.save_case(case)
                action_taken = "rescheduled"
            elif case.state == "referral_accepted":
                case.transition(
                    "book",
                    "ehr-sync",
                    current_time,
                    evidence=["appointment"],
                    evidence_provenance={"time": payload.rescheduled_start or "TBD"},
                )
                store.save_case(case)
                action_taken = "booked"
            else:
                action_taken = f"ignored_state_{case.state}"
        elif status_norm in ("noshow", "no_show"):
            if case.state == "appointment_booked":
                case.transition("no_show", "ehr-sync", current_time)
                store.save_case(case)
                action_taken = "escalated_no_show"
            else:
                action_taken = f"ignored_state_{case.state}"
        else:
            action_taken = f"unsupported_status_{status_norm}"

        return {
            "status": "processed",
            "case_id": case.id,
            "current_state": case.state,
            "action_taken": action_taken,
            "timestamp": current_time.isoformat(),
        }

    @app.post("/webhooks/delivery-receipt", status_code=200)
    @app.post("/api/webhooks/delivery-receipt", status_code=200, include_in_schema=False)
    def handle_delivery_receipt(
        payload: DeliveryReceiptInput,
        http_request: Request,
    ) -> dict[str, Any]:
        """Ingest telephony/SMS carrier delivery receipts and record status."""
        check_rate_limit(http_request)
        target_id = payload.notification_id or payload.id or payload.MessageSid
        if not target_id:
            raise HTTPException(status_code=400, detail="notification identifier is required")

        status_val = (payload.MessageStatus or payload.status or "").lower()
        error_val = payload.ErrorCode or payload.error_code or payload.ErrorMessage

        if status_val in ("delivered", "sent", "success"):
            new_status = "sent"
        elif status_val in ("failed", "undelivered", "rejected", "carrier_rejected"):
            new_status = "failed"
        else:
            new_status = "sent"

        with store.connection:
            store.connection.execute(
                """
                UPDATE notification_outbox
                SET status = ?, sent_at = COALESCE(sent_at, ?)
                WHERE id = ? OR dedupe_key = ?
                """,
                (new_status, now().isoformat(), target_id, target_id),
            )

        return {
            "status": "acknowledged",
            "notification_id": target_id,
            "carrier_status": status_val,
            "outbox_status": new_status,
            "error": error_val,
        }

    return app


app = create_app()

__all__ = ["app", "create_app"]
