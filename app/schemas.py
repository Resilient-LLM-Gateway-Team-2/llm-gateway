"""
Pydantic models for request / response validation.
"""

from typing import List, Optional
from pydantic import BaseModel, EmailStr, Field


class Message(BaseModel):
    role: str = Field(..., description="Role of the message sender: system | user | assistant")
    content: str = Field(..., description="Content of the message")


class ChatRequest(BaseModel):
    model: str = Field(..., description="Model identifier, e.g. gpt-4o-mini, gemini-1.5-flash")
    messages: List[Message] = Field(..., min_length=1, description="Conversation messages")
    temperature: float = Field(0.7, ge=0.0, le=2.0, description="Sampling temperature")
    max_tokens: int = Field(256, ge=1, le=4096, description="Maximum tokens to generate")
    chat_id: Optional[int] = None


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


class SignupRequest(BaseModel):
    email: EmailStr
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=6)


class LoginRequest(BaseModel):
    username: str
    password: str


class UserProfileResponse(BaseModel):
    id: int
    email: EmailStr
    username: str
    role: str
    plan: str
    preferred_model: Optional[str] = None
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    is_active: bool = True
    last_login_at: Optional[str] = None
    last_active_at: Optional[str] = None
    created_at: Optional[str] = None


class UserProfileUpdate(BaseModel):
    email: Optional[EmailStr] = None
    username: Optional[str] = Field(default=None, min_length=3, max_length=50)
    preferred_model: Optional[str] = None


class AdminUserCreateRequest(BaseModel):
    email: EmailStr
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=6)
    role: str = "user"
    plan: str = "basic"


class AdminUserUpdateRequest(BaseModel):
    email: Optional[EmailStr] = None
    username: Optional[str] = Field(default=None, min_length=3, max_length=50)
    role: Optional[str] = None
    plan: Optional[str] = None
    preferred_model: Optional[str] = None
    is_active: Optional[bool] = None


class BudgetConfigRequest(BaseModel):
    provider: str
    monthly_budget_usd: float = Field(..., gt=0)
    warning_threshold_percent: float = Field(80.0, ge=0, le=100)
    hard_limit_percent: float = Field(100.0, ge=0, le=100)


class ApiKeyResponse(BaseModel):
    id: int
    name: Optional[str] = None
    key: Optional[str] = None
    is_active: bool
    created_at: Optional[str] = None
    last_used_at: Optional[str] = None
