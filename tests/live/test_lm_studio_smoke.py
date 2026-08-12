"""Explicit opt-in smoke test for a user-managed local LM Studio server."""

import asyncio
import logging
import os
import time

import httpx2
import pytest

from aaos.config import DEFAULT_SETTINGS
from aaos.inference.lm_studio import LMStudioGateway
from aaos.inference.models import ChatMessage, ChatRequest, ChatResponse

LOGGER = logging.getLogger(__name__)

pytestmark = pytest.mark.skipif(
    os.environ.get("AAOS_RUN_LM_STUDIO_SMOKE") != "1",
    reason="set AAOS_RUN_LM_STUDIO_SMOKE=1 for the local LM Studio smoke test",
)


def test_live_lm_studio_plain_generation() -> None:
    """Verify the manually prepared local model accepts one plain request."""

    async def scenario() -> ChatResponse:
        gateway = LMStudioGateway(DEFAULT_SETTINGS, httpx2.AsyncHTTPTransport())
        try:
            availability = await gateway.check_availability()
            assert availability.reachable
            assert availability.configured_model == DEFAULT_SETTINGS.lm_studio_model
            assert availability.available
            assert any(
                context_length >= DEFAULT_SETTINGS.lm_studio_context_length
                for context_length in availability.loaded_context_lengths
            )

            started = time.perf_counter()
            response = await gateway.generate(
                ChatRequest(
                    messages=(ChatMessage(role="user", content="Reply with exactly: AAOS ready"),),
                    max_output_tokens=16,
                    reasoning=False,
                )
            )
            elapsed_seconds = time.perf_counter() - started
            if response.usage is None:
                LOGGER.info("LM Studio smoke elapsed_seconds=%.3f", elapsed_seconds)
            else:
                LOGGER.info(
                    "LM Studio smoke elapsed_seconds=%.3f input_tokens=%d "
                    "output_tokens=%d total_tokens=%d",
                    elapsed_seconds,
                    response.usage.input_tokens,
                    response.usage.output_tokens,
                    response.usage.total_tokens,
                )
            return response
        finally:
            await gateway.aclose()

    response = asyncio.run(scenario())
    assert response.content.strip()
    assert response.model_identifier == DEFAULT_SETTINGS.lm_studio_model
