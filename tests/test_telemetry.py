import asyncio
import pytest
from careflow.telemetry import (
    clear_in_memory_spans,
    get_in_memory_spans,
    setup_telemetry,
    trace_span,
    traced,
)


@pytest.fixture(autouse=True)
def reset_spans():
    setup_telemetry("careflow-test", in_memory=True)
    clear_in_memory_spans()
    yield
    clear_in_memory_spans()


def test_trace_span_context_manager():
    with trace_span("careflow.case.transition", {"case.id": "CASE-77", "case.state": "scheduled"}):
        val = 1 + 1
        assert val == 2

    spans = get_in_memory_spans()
    assert len(spans) == 1
    assert spans[0].name == "careflow.case.transition"
    assert spans[0].attributes["case.id"] == "CASE-77"
    assert spans[0].attributes["case.state"] == "scheduled"


def test_traced_sync_decorator():
    @traced("careflow.outbox.dispatch", component="worker")
    def mock_dispatch(batch_size: int) -> int:
        return batch_size * 2

    res = mock_dispatch(5)
    assert res == 10

    spans = get_in_memory_spans()
    assert len(spans) == 1
    assert spans[0].name == "careflow.outbox.dispatch"
    assert spans[0].attributes["component"] == "worker"


@pytest.mark.asyncio
async def test_traced_async_decorator():
    @traced("careflow.async_task", priority="high")
    async def mock_async_op():
        await asyncio.sleep(0.01)
        return "done"

    res = await mock_async_op()
    assert res == "done"

    spans = get_in_memory_spans()
    assert len(spans) == 1
    assert spans[0].name == "careflow.async_task"
    assert spans[0].attributes["priority"] == "high"
