"""Model-independent inference gateway contract and safe error categories."""

from typing import Protocol, runtime_checkable

from .models import ChatRequest, ChatResponse, ModelAvailability


@runtime_checkable
class ModelGateway(Protocol):
    """Minimal asynchronous boundary for local model diagnostics and generation."""

    async def check_availability(self) -> ModelAvailability:
        """Return diagnostics for the configured local model."""
        ...

    async def generate(self, request: ChatRequest) -> ChatResponse:
        """Generate visible assistant content from a validated request."""
        ...


class InferenceError(RuntimeError):
    """Base class for safe, caller-visible inference failures."""


class InferenceConnectionError(InferenceError):
    """Raised when the inference backend cannot be reached."""


class InferenceTimeoutError(InferenceError):
    """Raised when an inference operation exceeds its configured timeout."""


class ModelUnavailableError(InferenceError):
    """Raised when the configured model is unavailable for inference."""


class InferenceProtocolError(InferenceError):
    """Raised when backend data violates the AAOS inference contract."""


class InferenceBackendError(InferenceError):
    """Raised for backend failures without a more specific AAOS category."""


class UnsupportedInferenceFeatureError(InferenceError):
    """Raised when a caller requests an unsupported inference capability."""
