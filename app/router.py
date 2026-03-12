"""
Provider router with failover algorithm.

Primary: OpenAI  →  Fallback: Gemini

The router tries the primary provider first.  If it raises a ProviderError
(i.e. all retries exhausted), the router automatically switches to the
fallback provider and returns its response instead.
"""

import logging

from app.schemas import ChatRequest, ChatResponse
from app.providers import call_openai, call_gemini, ProviderError, MockProvider

logger = logging.getLogger(__name__)


def route_request(request: ChatRequest) -> ChatResponse:
    """
    Route a chat request through the provider chain.

    1. Try OpenAI (primary)
    2. On failure, fall back to Gemini (secondary)
    3. If both fail, fall back to MockProvider
    """

    # --- Primary: OpenAI ---
    try:
        logger.info("Routing to primary provider: openai (model=%s)", request.model)
        return call_openai(request)
    except ProviderError as exc:
        logger.warning("Primary provider failed: %s — switching to fallback", exc)

    # --- Fallback: Gemini ---
    try:
        logger.info("Routing to fallback provider: gemini")
        return call_gemini(request)
    except ProviderError as exc:
        logger.warning("Fallback provider also failed: %s — switching to mock provider", exc)
        
    # --- Final Fallback: Mock ---
    try:
        logger.info("Routing to mock provider")
        return MockProvider().call(request)
    except Exception as exc:
        logger.error("Mock provider failed: %s", exc)
        raise ProviderError("mock", str(exc))
