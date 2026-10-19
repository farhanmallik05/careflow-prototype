"""Native OpenTelemetry distributed tracing and span management for CareFlow."""

from __future__ import annotations

from contextlib import contextmanager
import functools
import os
from typing import Any, Callable, Generator, Mapping, TypeVar

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


_EXPORTER: InMemorySpanExporter | None = None
_INITIALIZED: bool = False

F = TypeVar("F", bound=Callable[..., Any])


def setup_telemetry(
    service_name: str = "careflow-service",
    *,
    in_memory: bool = True,
) -> InMemorySpanExporter | None:
    """Initialize OpenTelemetry tracer provider with span processors."""
    global _EXPORTER, _INITIALIZED
    if _INITIALIZED:
        return _EXPORTER
    provider = TracerProvider()
    if in_memory:
        _EXPORTER = InMemorySpanExporter()
        provider.add_span_processor(SimpleSpanProcessor(_EXPORTER))
    try:
        trace.set_tracer_provider(provider)
    except Exception:
        pass
    _INITIALIZED = True
    return _EXPORTER


def get_tracer(name: str = "careflow") -> trace.Tracer:
    """Return configured OpenTelemetry tracer."""
    if not _INITIALIZED:
        setup_telemetry()
    return trace.get_tracer(name)


def get_in_memory_spans() -> list[Any]:
    """Retrieve all finished spans from the in-memory exporter."""
    if _EXPORTER is not None:
        return list(_EXPORTER.get_finished_spans())
    return []


def clear_in_memory_spans() -> None:
    """Clear all recorded spans in memory exporter."""
    if _EXPORTER is not None:
        _EXPORTER.clear()


@contextmanager
def trace_span(
    name: str,
    attributes: Mapping[str, Any] | None = None,
) -> Generator[trace.Span, None, None]:
    """Context manager executing a block within a named OpenTelemetry span."""
    tracer = get_tracer()
    with tracer.start_as_current_span(name) as span:
        if attributes:
            for key, val in attributes.items():
                if val is not None:
                    span.set_attribute(key, str(val) if not isinstance(val, (int, float, bool, str)) else val)
        yield span


def traced(span_name: str | None = None, **default_attributes: Any) -> Callable[[F], F]:
    """Decorator wrapping a synchronous or asynchronous function in an OpenTelemetry span."""

    def decorator(func: F) -> F:
        name = span_name or f"careflow.{func.__name__}"

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            with trace_span(name, default_attributes):
                return func(*args, **kwargs)

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            with trace_span(name, default_attributes):
                return await func(*args, **kwargs)

        import asyncio

        if asyncio.iscoroutinefunction(func):
            return async_wrapper  # type: ignore[return-value]
        return sync_wrapper  # type: ignore[return-value]

    return decorator


__all__ = [
    "setup_telemetry",
    "get_tracer",
    "get_in_memory_spans",
    "clear_in_memory_spans",
    "trace_span",
    "traced",
]
