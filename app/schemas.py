"""
Pydantic models for request / response validation.
"""

from typing import List, Optional

from pydantic import BaseModel, Field


# ---------- Request ----------

class Message(BaseModel):
    role: str = Field(..., description="Role of the message sender: system | user | assistant")
    content: str = Field(..., description="Content of the message")


class ChatRequest(BaseModel):
    model: str = Field(..., description="Model identifier, e.g. gpt-4, gemini-1.5-flash")
    messages: List[Message] = Field(..., min_length=1, description="Conversation messages")
    temperature: float = Field(0.7, ge=0.0, le=2.0, description="Sampling temperature")
    max_tokens: int = Field(256, ge=1, le=4096, description="Maximum tokens to generate")


# ---------- Response ----------

class UsageStats(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class RouteStep(BaseModel):
    provider: str
    requested_model: str
    resolved_model: Optional[str] = None
    status: str
    source: str = "router"


class CostEstimate(BaseModel):
    openai_usd: float = 0.0
    gemini_usd: float = 0.0
    provider_used_usd: float = 0.0
    currency: str = "USD"
    pricing_basis: str = "estimated_from_tokens"


class ChatResponse(BaseModel):
    content: str
    provider: str
    model: str
    usage: UsageStats
    route_path: List[RouteStep] = Field(default_factory=list)
    cost_estimate: Optional[CostEstimate] = None
