"""
Provider router with intelligent cost & specialty-based selection.

Routing Strategy:
- Analyzes task type and cost implications
- Selects best provider based on specialty + cost efficiency
- Falls back through available providers on failure
"""

import logging
from typing import List, Tuple

from app.schemas import ChatRequest, ChatResponse, RouteStep
from app.providers import call_openai, call_gemini, ProviderError, MockProvider

logger = logging.getLogger(__name__)

# Task specialties for each provider
# Gemini excels at: summarization, translation, content generation
# OpenAI excels at: reasoning, coding, complex analysis

GEMINI_SPECIALTIES = {
    "summarize", "summary", "rewrite", "rephrase", "translate", "paraphrase", 
    "shorten", "tone", "grammar", "improve", "simplify", "explain", "generate",
    "write", "create", "compose", "describe", "content", "poetry", "story"
}

OPENAI_SPECIALTIES = {
    "code", "debug", "sql", "algorithm", "logic", "analyze", "reason", "python",
    "javascript", "typescript", "java", "problem-solving", "chess", "math", "prove"
}

# Cost optimization: Gemini is ~70% cheaper than OpenAI
COST_SCORES = {
    "gemini": 1.0,    # Cheapest (baseline)
    "openai": 1.7,    # More expensive
}

# Specialty match scores (when task matches provider specialty)
SPECIALTY_BOOST = 2.0


def _last_user_message(request: ChatRequest) -> str:
    for msg in reversed(request.messages):
        if msg.role == "user":
            return msg.content.strip().lower()
    return ""


def _analyze_task_type(request: ChatRequest) -> dict:
    """Analyze the task to determine optimal provider based on specialty & cost."""
    user_text = _last_user_message(request)
    
    # If no user message, default to cost-optimized (Gemini)
    if not user_text:
        return {"preferred": "gemini", "reason": "no_message_default"}
    
    # Analyze task type from user message
    gemini_match = sum(1 for keyword in GEMINI_SPECIALTIES if keyword in user_text)
    openai_match = sum(1 for keyword in OPENAI_SPECIALTIES if keyword in user_text)
    
    # Calculate scores: specialty score + cost score
    gemini_score = gemini_match * SPECIALTY_BOOST + COST_SCORES["gemini"]
    openai_score = openai_match * SPECIALTY_BOOST + COST_SCORES["openai"]
    
    logger.info(f"Task analysis - Gemini matches: {gemini_match}, OpenAI matches: {openai_match}")
    logger.info(f"Task analysis - Gemini score: {gemini_score}, OpenAI score: {openai_score}")
    
    # Compare scores: highest score wins (NOT just any match)
    if openai_score > gemini_score:
        reason = "openai_specialty" if openai_match > 0 else "cost_disadvantage"
        return {"preferred": "openai", "reason": reason}
    
    if gemini_match > 0 or gemini_score > openai_score:
        reason = "gemini_specialty" if gemini_match > 0 else "cost_optimized"
        return {"preferred": "gemini", "reason": reason}
    
    # Default: cost-optimized (Gemini cheaper) for generic requests with no matches
    return {"preferred": "gemini", "reason": "cost_optimized_default"}


def _select_provider_order(request: ChatRequest) -> List[str]:
    """
    Intelligently select provider order based on cost and specialty.
    
    Strategy:
    1. Analyze task type and request characteristics
    2. Score providers by (specialty match + cost efficiency)
    3. Return ordered list with best provider first
    """
    task_analysis = _analyze_task_type(request)
    preferred = task_analysis["preferred"]
    reason = task_analysis["reason"]
    
    logger.info(f"Provider selection: {preferred} ({reason})")
    
    if preferred == "gemini":
        return ["gemini", "openai"]
    else:
        return ["openai", "gemini"]


def _expected_model(provider: str, requested_model: str) -> str:
    if provider == "gemini":
        return requested_model if requested_model.startswith("gemini") else "gemini-1.5-flash"
    return requested_model


def route_request(request: ChatRequest) -> Tuple[ChatResponse, List[RouteStep]]:
    """
    Route a chat request through the provider chain.

    1. Try OpenAI (primary)
    2. On failure, fall back to Gemini (secondary)
    3. If both fail, fall back to MockProvider
    """

    route_path: List[RouteStep] = []

    provider_order = _select_provider_order(request)
    provider_call_map = {
        "openai": call_openai,
        "gemini": call_gemini,
    }

    logger.info("Selected provider order: %s (requested model=%s)", provider_order, request.model)

    for index, provider in enumerate(provider_order):
        provider_callable = provider_call_map[provider]
        step_label = "primary" if index == 0 else "fallback"

        try:
            logger.info("Routing to %s provider: %s (model=%s)", step_label, provider, request.model)
            response = provider_callable(request)
            route_path.append(
                RouteStep(
                    provider=provider,
                    requested_model=request.model,
                    resolved_model=response.model,
                    status="success",
                )
            )
            logger.info(
                "MCP route step provider=%s status=success requested_model=%s resolved_model=%s",
                provider,
                request.model,
                response.model,
            )
            return response, route_path
        except ProviderError as exc:
            route_path.append(
                RouteStep(
                    provider=provider,
                    requested_model=request.model,
                    resolved_model=_expected_model(provider, request.model),
                    status="failed",
                )
            )
            logger.warning(
                "MCP route step provider=%s status=failed requested_model=%s reason=%s",
                provider,
                request.model,
                exc,
            )
            if index < len(provider_order) - 1:
                logger.warning("%s provider failed: %s — switching to fallback", provider, exc)
            else:
                logger.warning("Last provider in chain failed: %s", exc)
        
    # --- Final Fallback: Mock ---
    try:
        logger.info("Routing to mock provider")
        response = MockProvider().call(request)
        route_path.append(
            RouteStep(
                provider="mock",
                requested_model=request.model,
                resolved_model=response.model,
                status="success",
            )
        )
        logger.info(
            "MCP route step provider=mock status=success requested_model=%s resolved_model=%s",
            request.model,
            response.model,
        )
        return response, route_path
    except Exception as exc:
        route_path.append(
            RouteStep(
                provider="mock",
                requested_model=request.model,
                resolved_model="mock-model",
                status="failed",
            )
        )
        logger.error(
            "MCP route step provider=mock status=failed requested_model=%s reason=%s",
            request.model,
            exc,
        )
        logger.error("Mock provider failed: %s", exc)
        raise ProviderError("mock", str(exc))
