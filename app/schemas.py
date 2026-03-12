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
    model: str = Field(..., description="Model identifier, e.g. gpt-4, gemini-pro")
    messages: List[Message] = Field(..., min_length=1, description="Conversation messages")
    temperature: float = Field(0.7, ge=0.0, le=2.0, description="Sampling temperature")
    max_tokens: int = Field(256, ge=1, le=4096, description="Maximum tokens to generate")


# ---------- Response ----------

class UsageStats(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatResponse(BaseModel):
    content: str
    provider: str
    model: str
    usage: UsageStats
