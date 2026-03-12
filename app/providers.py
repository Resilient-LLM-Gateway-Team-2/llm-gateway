"""
LLM provider adapters with retry logic.

Each adapter normalises the provider-specific response into a ChatResponse.
Retries use exponential backoff: 1 s → 2 s → 4 s (3 attempts total).
"""

import os
import time
import logging
from typing import Callable

import openai
import google.generativeai as genai

from app.schemas import ChatRequest, ChatResponse, UsageStats
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)

class BaseProvider(ABC):
    """Interface for LLM Providers"""
    
    @abstractmethod
    def call(self, request: ChatRequest) -> ChatResponse:
        pass

class MockProvider(BaseProvider):
    """A mock provider that simulates a response when real models fail or for testing."""
    
    def call(self, request: ChatRequest) -> ChatResponse:
        return ChatResponse(
            content="This is a simulated response from the MockProvider. The real providers (OpenAI/Gemini) were unavailable or out of quota.",
            provider="mock",
            model="mock-model",
            usage=UsageStats(prompt_tokens=10, completion_tokens=15, total_tokens=25)
        )

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
MAX_RETRIES = 3
BACKOFF_BASE = 1  # seconds


class ProviderError(Exception):
    """Raised when a provider fails after all retry attempts."""

    def __init__(self, provider: str, message: str):
        self.provider = provider
        super().__init__(f"[{provider}] {message}")


# ---------------------------------------------------------------------------
# Retry helper
# ---------------------------------------------------------------------------

def _retry(fn: Callable, provider_name: str):
    """Execute *fn* with up to MAX_RETRIES attempts and exponential backoff."""
    last_exc: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            wait = BACKOFF_BASE * (2 ** (attempt - 1))
            logger.warning(
                "%s attempt %d/%d failed (%s). Retrying in %ds …",
                provider_name,
                attempt,
                MAX_RETRIES,
                exc,
                wait,
            )
            if attempt < MAX_RETRIES:
                time.sleep(wait)

    raise ProviderError(provider_name, f"All {MAX_RETRIES} attempts failed: {last_exc}")


# ---------------------------------------------------------------------------
# OpenAI adapter
# ---------------------------------------------------------------------------

def call_openai(request: ChatRequest) -> ChatResponse:
    """Call the OpenAI Chat Completions API and return a normalised response."""

    client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    def _call():
        response = client.chat.completions.create(
            model=request.model,
            messages=[{"role": m.role, "content": m.content} for m in request.messages],
            temperature=request.temperature,
            max_tokens=request.max_tokens,
        )
        choice = response.choices[0]
        usage = response.usage
        return ChatResponse(
            content=choice.message.content or "",
            provider="openai",
            model=response.model,
            usage=UsageStats(
                prompt_tokens=usage.prompt_tokens if usage else 0,
                completion_tokens=usage.completion_tokens if usage else 0,
                total_tokens=usage.total_tokens if usage else 0,
            ),
        )

    return _retry(_call, "openai")


# ---------------------------------------------------------------------------
# Gemini adapter
# ---------------------------------------------------------------------------

def call_gemini(request: ChatRequest) -> ChatResponse:
    """Call the Google Gemini API and return a normalised response."""

    genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

    # Map the requested model to a Gemini model name
    gemini_model = request.model if request.model.startswith("gemini") else "gemini-2.0-flash"

    def _call():
        model = genai.GenerativeModel(gemini_model)

        # Combine messages into a single prompt (Gemini uses a simpler interface)
        prompt_parts = []
        for m in request.messages:
            prefix = f"[{m.role}] " if m.role != "user" else ""
            prompt_parts.append(f"{prefix}{m.content}")
        prompt = "\n".join(prompt_parts)

        response = model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(
                temperature=request.temperature,
                max_output_tokens=request.max_tokens,
            ),
        )

        text = response.text or ""

        # Estimate token counts from Gemini metadata when available
        prompt_tokens = 0
        completion_tokens = 0
        if hasattr(response, "usage_metadata") and response.usage_metadata:
            prompt_tokens = getattr(response.usage_metadata, "prompt_token_count", 0) or 0
            completion_tokens = getattr(response.usage_metadata, "candidates_token_count", 0) or 0

        return ChatResponse(
            content=text,
            provider="gemini",
            model=gemini_model,
            usage=UsageStats(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
            ),
        )

    return _retry(_call, "gemini")
