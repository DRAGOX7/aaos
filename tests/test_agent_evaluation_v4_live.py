from __future__ import annotations

from test_agent_evaluation_v4_runner import (
    SECRET,
    _manifest,
)

from aaos.agent.contracts import ToolName
from aaos.agent.evaluation.contracts import EvaluationSplit
from aaos.agent.evaluation.v4_live import (
    V4LocalModelExecutor,
    build_v4_fake_only_registry,
)
from aaos.agent.evaluation.v4_runner import V4ExecutionBatch
from aaos.inference.models import ChatRequest, ChatResponse, ModelAvailability, TokenUsage


class FakeGateway:
    def __init__(self) -> None:
        self.calls = 0

    async def check_availability(self) -> ModelAvailability:
        return ModelAvailability(
            reachable=True,
            available=True,
            configured_model="synthetic-fake",
            available_models=("synthetic-fake",),
            loaded_context_lengths=(1024,),
        )

    async def generate(self, request: ChatRequest) -> ChatResponse:
        self.calls += 1
        return ChatResponse(
            content='{"action":"terminal_text","terminal_text":"Synthetic completion."}',
            model_identifier="fake-local-model",
            usage=TokenUsage(input_tokens=2, output_tokens=4, total_tokens=6),
        )


def test_fake_only_registry_has_no_write_or_live_rag_capability() -> None:
    registry = build_v4_fake_only_registry()
    names = set(registry.names)

    assert names == {ToolName.RAG_QUERY, ToolName.COMPLETE_TASK}
    assert ToolName.WRITE_TASK_STATE not in names
    assert ToolName.READ_TASK_STATE not in names
    rag = registry.get(ToolName.RAG_QUERY)
    assert rag is not None
    assert rag.handler is not None


def test_local_executor_uses_injected_gateway_and_fake_tools_only() -> None:
    manifest = _manifest(EvaluationSplit.CALIBRATION, "live-synthetic")
    gateway = FakeGateway()
    batch = V4LocalModelExecutor(gateway)(manifest)

    assert isinstance(batch, V4ExecutionBatch)
    assert batch.fake_only is True
    assert len(batch.traces) == len(manifest.cases)
    assert batch.counters.model_call_count == gateway.calls
    assert batch.counters.rag_execution_count == 0
    assert batch.counters.unauthorized_write_count == 0
    assert batch.counters.arbitrary_tool_execution_count == 0
    assert SECRET not in repr(batch)
