import asyncio

from aaos.inference import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    InferenceBackendError,
    InferenceConnectionError,
    InferenceError,
    InferenceProtocolError,
    InferenceTimeoutError,
    ModelAvailability,
    ModelGateway,
    ModelUnavailableError,
    UnsupportedInferenceFeatureError,
)


class FakeGateway:
    async def check_availability(self) -> ModelAvailability:
        return ModelAvailability(
            reachable=True,
            available=True,
            configured_model="qwen/qwen3.5-4b",
            available_models=("qwen/qwen3.5-4b",),
            loaded_context_lengths=(8192,),
        )

    async def generate(self, request: ChatRequest) -> ChatResponse:
        return ChatResponse(content="ready", model_identifier="qwen/qwen3.5-4b")


def test_two_operation_gateway_satisfies_protocol() -> None:
    assert isinstance(FakeGateway(), ModelGateway)


def test_gateway_operations_return_model_independent_values() -> None:
    async def scenario() -> tuple[ModelAvailability, ChatResponse]:
        gateway = FakeGateway()
        availability = await gateway.check_availability()
        response = await gateway.generate(
            ChatRequest(messages=(ChatMessage(role="user", content="Are you ready?"),))
        )
        return availability, response

    availability, response = asyncio.run(scenario())

    assert availability.available is True
    assert availability.loaded_context_lengths == (8192,)
    assert response == ChatResponse(content="ready", model_identifier="qwen/qwen3.5-4b")


def test_specific_gateway_errors_share_the_safe_base_type() -> None:
    specific_errors = (
        InferenceConnectionError,
        InferenceTimeoutError,
        ModelUnavailableError,
        InferenceProtocolError,
        InferenceBackendError,
        UnsupportedInferenceFeatureError,
    )

    assert all(issubclass(error, InferenceError) for error in specific_errors)
