import os
import time
import logging
import secrets
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Form, Response
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Float, create_engine, func
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from app.schemas import ChatRequest, ChatResponse, CostEstimate
from app.providers import ProviderError
from app.auth import User

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ---------- Cost estimation (USD per 1K tokens; can be overridden via env) ----------
OPENAI_PROMPT_USD_PER_1K = float(os.getenv("OPENAI_PROMPT_USD_PER_1K", "0.005"))
OPENAI_COMPLETION_USD_PER_1K = float(os.getenv("OPENAI_COMPLETION_USD_PER_1K", "0.015"))
GEMINI_PROMPT_USD_PER_1K = float(os.getenv("GEMINI_PROMPT_USD_PER_1K", "0.00125"))
GEMINI_COMPLETION_USD_PER_1K = float(os.getenv("GEMINI_COMPLETION_USD_PER_1K", "0.00375"))


def _cost_from_tokens(
    *,
    prompt_tokens: int,
    completion_tokens: int,
    prompt_rate_per_1k: float,
    completion_rate_per_1k: float,
) -> float:
    prompt_cost = (prompt_tokens / 1000.0) * prompt_rate_per_1k
    completion_cost = (completion_tokens / 1000.0) * completion_rate_per_1k
    return round(prompt_cost + completion_cost, 8)


def _build_cost_estimate(response: ChatResponse) -> CostEstimate:
    prompt_tokens = response.usage.prompt_tokens if response.usage else 0
    completion_tokens = response.usage.completion_tokens if response.usage else 0

    openai_cost = _cost_from_tokens(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        prompt_rate_per_1k=OPENAI_PROMPT_USD_PER_1K,
        completion_rate_per_1k=OPENAI_COMPLETION_USD_PER_1K,
    )
    gemini_cost = _cost_from_tokens(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        prompt_rate_per_1k=GEMINI_PROMPT_USD_PER_1K,
        completion_rate_per_1k=GEMINI_COMPLETION_USD_PER_1K,
    )

    provider_name = (response.provider or "").lower()
    if "openai" in provider_name:
        provider_used_cost = openai_cost
    elif "gemini" in provider_name:
        provider_used_cost = gemini_cost
    elif "cache" in provider_name or "redis" in provider_name:
        provider_used_cost = min(openai_cost, gemini_cost)
    elif "mock" in provider_name:
        # Mock provider has no cost (simulated data)
        provider_used_cost = 0.0
    else:
        # For unknown providers, keep the value usage-derived and conservative.
        provider_used_cost = min(openai_cost, gemini_cost)

    return CostEstimate(
        openai_usd=openai_cost,
        gemini_usd=gemini_cost,
        provider_used_usd=round(provider_used_cost, 8),
        currency="USD",
        pricing_basis="estimated_from_tokens",
    )

app = FastAPI(title="Resilient LLM Gateway - Sprint 2")

# ---------- DB setup ----------
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://gateway:gatewaypass@postgres:5432/gatewaydb",  # docker-compose default connects via internal docker network port 5432
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


# ---------- ORM models (match your Alembic tables) ----------
class ApiKey(Base):
    __tablename__ = "api_keys"

    id = Column(Integer, primary_key=True)
    key = Column(String(255), unique=True, nullable=False)
    owner = Column(String(255), nullable=False)
    created_at = Column(DateTime, server_default=func.now())


class RequestLog(Base):
    __tablename__ = "requests"

    id = Column(Integer, primary_key=True)
    api_key_id = Column(Integer, ForeignKey("api_keys.id"), nullable=True)
    endpoint = Column(String(255), nullable=False)
    provider = Column(String(255), nullable=True)
    model = Column(String(255), nullable=True)
    status_code = Column(Integer, nullable=True)
    latency_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class BudgetConfig(Base):
    __tablename__ = "budget_configs"

    id = Column(Integer, primary_key=True)
    api_key_id = Column(Integer, ForeignKey("api_keys.id"), nullable=False, unique=False)
    provider = Column(String(255), nullable=False)  # "openai", "gemini", "all"
    monthly_budget_usd = Column(Float, nullable=False)  # e.g., 10.0
    warning_threshold_percent = Column(Float, default=80.0)  # e.g., 80% = warn at $8
    hard_limit_percent = Column(Float, default=100.0)  # e.g., 100% = block at $10
    is_enabled = Column(String(255), default="true")  # "true" or "false"
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now())


class CostLog(Base):
    __tablename__ = "cost_logs"

    id = Column(Integer, primary_key=True)
    api_key_id = Column(Integer, ForeignKey("api_keys.id"), nullable=False)
    provider = Column(String(255), nullable=False)
    cost_usd = Column(Float, nullable=False)
    tokens_used = Column(Integer, default=0)
    month = Column(String(7), nullable=False)  # "YYYY-MM" format
    created_at = Column(DateTime, server_default=func.now())


# ---------- DB dependency ----------
def get_db():
    """Yield a SQLAlchemy session; ensures close after request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------- Request logger ----------
def log_request(
    *,
    endpoint: str,
    status_code: int,
    latency_ms: int,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    api_key_id: Optional[int] = None,
) -> None:
    """Write an observability record to the requests table."""
    db: Session = SessionLocal()
    try:
        row = RequestLog(
            api_key_id=api_key_id,
            endpoint=endpoint,
            provider=provider,
            model=model,
            status_code=status_code,
            latency_ms=latency_ms,
        )
        db.add(row)
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error("Failed to log request: %s", exc)
    finally:
        db.close()


# ---------- Cost tracking and budget management ----------
def log_cost(
    *,
    api_key_id: int,
    provider: str,
    cost_usd: float,
    tokens_used: int = 0,
) -> None:
    """Log cost for a request."""
    from datetime import datetime
    current_month = datetime.now().strftime("%Y-%m")
    
    db: Session = SessionLocal()
    try:
        row = CostLog(
            api_key_id=api_key_id,
            provider=provider.lower(),
            cost_usd=cost_usd,
            tokens_used=tokens_used,
            month=current_month,
        )
        db.add(row)
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error("Failed to log cost: %s", exc)
    finally:
        db.close()


def get_monthly_cost(
    api_key_id: int,
    provider: str,
    db: Session,
) -> float:
    """Get total cost for a provider in the current month."""
    from datetime import datetime
    from sqlalchemy import and_
    
    current_month = datetime.now().strftime("%Y-%m")
    result = db.query(func.sum(CostLog.cost_usd)).filter(
        and_(
            CostLog.api_key_id == api_key_id,
            CostLog.provider == provider.lower(),
            CostLog.month == current_month,
        )
    ).scalar()
    
    return float(result or 0.0)


def get_budget_status(
    api_key_id: int,
    db: Session,
) -> dict:
    """Get budget status for all providers."""
    from datetime import datetime
    
    budgets = db.query(BudgetConfig).filter(
        BudgetConfig.api_key_id == api_key_id,
        BudgetConfig.is_enabled == "true"
    ).all()
    
    status = {}
    for budget in budgets:
        provider = budget.provider.lower()
        current_cost = get_monthly_cost(api_key_id, provider, db)
        limit = budget.monthly_budget_usd
        percent = (current_cost / limit * 100) if limit > 0 else 0
        
        status[provider] = {
            "budget": limit,
            "spent": round(current_cost, 8),
            "percent": round(percent, 1),
            "warning_threshold": budget.warning_threshold_percent,
            "hard_limit_percent": budget.hard_limit_percent,
            "status": "critical" if percent >= budget.hard_limit_percent else (
                "warning" if percent >= budget.warning_threshold_percent else "ok"
            ),
        }
    
    return status


def check_budget_allowed(
    api_key_id: int,
    provider: str,
    db: Session,
) -> tuple[bool, Optional[str]]:
    """Check if a request is allowed under budget constraints.
    
    Returns: (is_allowed, warning_message)
    """
    budget = db.query(BudgetConfig).filter(
        BudgetConfig.api_key_id == api_key_id,
        BudgetConfig.provider == provider.lower(),
        BudgetConfig.is_enabled == "true"
    ).first()
    
    if not budget:
        return True, None  # No budget set, always allowed
    
    current_cost = get_monthly_cost(api_key_id, provider, db)
    limit = budget.monthly_budget_usd
    percent = (current_cost / limit * 100) if limit > 0 else 0
    
    # Check hard limit
    if percent >= budget.hard_limit_percent:
        return False, f"Budget limit reached for {provider}: ${current_cost:.8f} / ${limit:.2f}"
    
    # Return warning if approaching threshold
    if percent >= budget.warning_threshold_percent:
        warning = f"Approaching budget for {provider}: {percent:.1f}% (${current_cost:.8f} / ${limit:.2f})"
        return True, warning
    
    return True, None


# ---------- Auth helper (inline to avoid circular import) ----------
def verify_api_key(
    x_api_key: str = Header(None, alias="X-API-Key"),
    db: Session = Depends(get_db),
) -> ApiKey:
    """Validate X-API-Key header against the api_keys table."""
    if x_api_key is None:
        raise HTTPException(status_code=403, detail="Missing X-API-Key header")

    api_key = db.query(ApiKey).filter(ApiKey.key == x_api_key).first()
    if api_key is None:
        raise HTTPException(status_code=403, detail="Invalid API key")

    return api_key


def verify_session(request: Request) -> Optional[str]:
    """Extract and verify session token from cookie."""
    session_token = request.cookies.get("session_token")
    if not session_token:
        return None
    
    # Verify the session token
    username = User.verify_session(session_token)
    return username


# ---------- Routes ----------
# ---------- Authentication Routes ----------
@app.get("/login", response_class=HTMLResponse)
async def serve_login():
    """Serve the login page."""
    try:
        with open("app/login.html", "r") as f:
            return HTMLResponse(content=f.read())
    except Exception as e:
        logger.error("Failed to load login page: %s", e)
        raise HTTPException(status_code=500, detail="Login page not found")


@app.post("/login")
async def handle_login(request: Request):
    """Handle login form submission (both JSON and form-encoded)."""
    try:
        # Parse request - handle both JSON and form-encoded data
        content_type = request.headers.get("content-type", "")
        
        if "json" in content_type:
            data = await request.json()
            username = data.get("username", "")
            password = data.get("password", "")
        else:
            # Form-encoded data
            form_data = await request.form()
            username = form_data.get("username", "")
            password = form_data.get("password", "")
        
        # Authenticate the user
        if not username or not password or not User.authenticate(username, password):
            if "json" in content_type:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Invalid username or password"}
                )
            return RedirectResponse(
                url="/login?error=Invalid username or password",
                status_code=303
            )
        
        # Create a session token
        session_token = User.create_session(username)
        
        if "json" in content_type:
            # For JSON requests, return success with cookie
            response = JSONResponse(
                status_code=200,
                content={
                    "status": "success",
                    "redirect": "/dashboard",
                    "message": "Login successful"
                }
            )
            response.set_cookie(
                key="session_token",
                value=session_token,
                max_age=7 * 24 * 60 * 60,  # 7 days
                httponly=True,
                samesite="Lax",
            )
            return response
        else:
            # For form requests, redirect with cookie
            response = RedirectResponse(url="/dashboard", status_code=303)
            response.set_cookie(
                key="session_token",
                value=session_token,
                max_age=7 * 24 * 60 * 60,  # 7 days
                httponly=True,
                samesite="Lax",
            )
            return response
    except Exception as e:
        logger.error("Login error: %s", e)
        if "json" in request.headers.get("content-type", ""):
            return JSONResponse(
                status_code=400,
                content={"detail": f"Login failed: {str(e)}"}
            )
        return RedirectResponse(
            url="/login?error=Login failed",
            status_code=303
        )


@app.get("/", response_class=HTMLResponse)
async def serve_landing(request: Request):
    """Serve the landing page or redirect to dashboard if logged in."""
    session_token = request.cookies.get("session_token")
    if session_token and User.verify_session(session_token):
        # User is logged in, redirect to dashboard
        return RedirectResponse(url="/dashboard", status_code=303)
    
    try:
        with open("app/landing.html", "r") as f:
            return HTMLResponse(content=f.read())
    except Exception as e:
        logger.error("Failed to load landing page: %s", e)
        raise HTTPException(status_code=500, detail="Landing page not found")


@app.get("/features", response_class=HTMLResponse)
async def serve_features():
    """Serve the features page."""
    try:
        with open("app/features.html", "r") as f:
            return HTMLResponse(content=f.read())
    except Exception as e:
        logger.error("Failed to load features page: %s", e)
        raise HTTPException(status_code=500, detail="Features page not found")


@app.get("/providers", response_class=HTMLResponse)
async def serve_providers():
    """Serve the providers page."""
    try:
        with open("app/providers.html", "r") as f:
            return HTMLResponse(content=f.read())
    except Exception as e:
        logger.error("Failed to load providers page: %s", e)
        raise HTTPException(status_code=500, detail="Providers page not found")


@app.get("/resources", response_class=HTMLResponse)
async def serve_resources():
    """Serve the resources page."""
    try:
        with open("app/resources.html", "r") as f:
            return HTMLResponse(content=f.read())
    except Exception as e:
        logger.error("Failed to load resources page: %s", e)
        raise HTTPException(status_code=500, detail="Resources page not found")


@app.get("/api-docs", response_class=HTMLResponse)
async def serve_api_docs():
    """Serve the API documentation page."""
    try:
        with open("app/api-docs.html", "r") as f:
            return HTMLResponse(content=f.read())
    except Exception as e:
        logger.error("Failed to load API docs page: %s", e)
        raise HTTPException(status_code=500, detail="API docs page not found")


@app.get("/status", response_class=HTMLResponse)
async def serve_status():
    """Serve the system status page."""
    try:
        with open("app/status.html", "r") as f:
            return HTMLResponse(content=f.read())
    except Exception as e:
        logger.error("Failed to load status page: %s", e)
        raise HTTPException(status_code=500, detail="Status page not found")


@app.get("/contact", response_class=HTMLResponse)
async def serve_contact():
    """Serve the contact & support page."""
    try:
        with open("app/contact.html", "r") as f:
            return HTMLResponse(content=f.read())
    except Exception as e:
        logger.error("Failed to load contact page: %s", e)
        raise HTTPException(status_code=500, detail="Contact page not found")


@app.get("/privacy", response_class=HTMLResponse)
async def serve_privacy():
    """Serve the privacy policy page."""
    try:
        with open("app/privacy.html", "r") as f:
            return HTMLResponse(content=f.read())
    except Exception as e:
        logger.error("Failed to load privacy page: %s", e)
        raise HTTPException(status_code=500, detail="Privacy page not found")


@app.get("/terms", response_class=HTMLResponse)
async def serve_terms():
    """Serve the terms of service page."""
    try:
        with open("app/terms.html", "r") as f:
            return HTMLResponse(content=f.read())
    except Exception as e:
        logger.error("Failed to load terms page: %s", e)
        raise HTTPException(status_code=500, detail="Terms page not found")


@app.get("/dashboard", response_class=HTMLResponse)
async def serve_dashboard(request: Request):
    """Serve the dashboard. Requires valid session."""
    session_token = request.cookies.get("session_token")
    if not session_token or not User.verify_session(session_token):
        # Not authenticated, redirect to login
        return RedirectResponse(url="/login", status_code=303)
    
    try:
        with open("app/dashboard.html", "r") as f:
            return HTMLResponse(content=f.read())
    except Exception as e:
        logger.error("Failed to load dashboard: %s", e)
        raise HTTPException(status_code=500, detail="Dashboard not found")


@app.post("/logout")
async def handle_logout(request: Request):
    """Handle logout."""
    session_token = request.cookies.get("session_token")
    if session_token:
        User.logout(session_token)
    
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(key="session_token")
    return response


@app.post("/api-key")
def generate_api_key(
    db: Session = Depends(get_db),
):
    """Generate a new API key for programmatic access.
    
    Usage:
        curl -X POST https://llm-gateway-api-og50.onrender.com/api-key
        
    Returns:
        {
            "api_key": "llm_key_...",
            "message": "Save this key securely. You won't see it again."
        }
    """
    try:
        # Generate a random 32-byte key encoded as base64url
        random_bytes = secrets.token_urlsafe(32)
        api_key_value = f"llm_key_{random_bytes}"
        
        # Store in database
        new_key = ApiKey(
            key=api_key_value,
            owner="user",  # Can be customized later if needed
        )
        db.add(new_key)
        db.commit()
        db.refresh(new_key)
        
        logger.info(f"Generated new API key: {new_key.id}")
        
        usage_instructions = (
            f"curl -X POST https://llm-gateway-api-og50.onrender.com/chat "
            f"-H 'X-API-Key: {api_key_value}' "
            f"-H 'Content-Type: application/json' "
            f"-d '{{\"messages\": [{{\"role\": \"user\", \"content\": \"Hello\"}}], \"model\": \"gpt-4o-mini\"}}'"
        )
        
        return {
            "api_key": api_key_value,
            "message": "Save this key securely. You won't see it again.",
            "usage": usage_instructions
        }
    except Exception as e:
        db.rollback()
        logger.error(f"Error generating API key: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate API key")


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.get("/health/detailed")
def health_detailed():
    """Return connectivity status for API, PostgreSQL, and Redis."""
    result = {"api": "ok", "postgres": "error", "redis": "error"}

    # Check PostgreSQL
    try:
        from sqlalchemy import text
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        db.close()
        result["postgres"] = "ok"
    except Exception:
        pass

    # Check Redis
    try:
        from app.cache import redis_client
        redis_client.ping()
        result["redis"] = "ok"
    except Exception:
        pass

    return result


@app.post("/chat", response_model=ChatResponse)
def chat(
    body: ChatRequest,
    api_key: ApiKey = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    """
    Main chat endpoint.

    1. Pydantic validates the request body automatically
    2. verify_api_key enforces auth via the X-API-Key header
    3. Check budget constraints
    4. route_request tries OpenAI, falls back to Gemini
    5. Log cost and check budget status
    6. log_request captures observability data to PostgreSQL
    7. Returns a standardised ChatResponse
    """
    # Lazy import to avoid circular dependency at module load time
    from app.router import route_request
    from app.cache import get_cached_response, set_cached_response
    import hashlib

    start = time.perf_counter()

    # --- Two-layer cache key strategy ---
    import json

    # Layer 1: Full request state (model + generation settings + full messages array)
    full_request_state = {
        "model": body.model,
        "temperature": body.temperature,
        "max_tokens": body.max_tokens,
        "messages": [{"role": m.role, "content": m.content} for m in body.messages],
    }
    messages_serialized = json.dumps(full_request_state, sort_keys=True)
    exact_hash = hashlib.sha256(messages_serialized.encode("utf-8")).hexdigest()
    exact_key = f"chat:{api_key.id}:{exact_hash}"

    # Layer 2: Last user message only (for repeat-question cache hits)
    last_user_msg = ""
    for m in reversed(body.messages):
        if m.role == "user":
            last_user_msg = m.content
            break
    prompt_cache_basis = f"{body.model}:{last_user_msg}"
    prompt_hash = hashlib.sha256(prompt_cache_basis.encode("utf-8")).hexdigest()
    prompt_key = f"prompt:{api_key.id}:{prompt_hash}"

    # Check cache — exact match first, then prompt-level match
    cached_data = get_cached_response(exact_key) or get_cached_response(prompt_key)
    if cached_data:
        response = ChatResponse(**cached_data)
        response.route_path = [
            {
                "provider": "redis_cache",
                "requested_model": body.model,
                "resolved_model": response.model,
                "status": "success",
                "source": "cache",
            }
        ]
        response.cost_estimate = _build_cost_estimate(response)
        latency_ms = int((time.perf_counter() - start) * 1000)
        log_request(
            endpoint="/chat",
            status_code=200,
            latency_ms=latency_ms,
            provider="redis_cache",
            model=response.model,
            api_key_id=api_key.id,
        )
        return response

    try:
        response, route_path = route_request(body)
        response.route_path = route_path
        response.cost_estimate = _build_cost_estimate(response)
        status_code = 200

        
        # Cache the successful response under both keys
        response_dict = response.model_dump() if hasattr(response, 'model_dump') else response.dict()
        set_cached_response(exact_key, response_dict)
        set_cached_response(prompt_key, response_dict)
        
        # Log cost for budget tracking
        if response.cost_estimate and response.provider:
            cost = response.cost_estimate.provider_used_usd
            tokens = response.usage.total_tokens if response.usage else 0
            log_cost(
                api_key_id=api_key.id,
                provider=response.provider,
                cost_usd=cost,
                tokens_used=tokens,
            )
    except ProviderError as exc:
        latency_ms = int((time.perf_counter() - start) * 1000)
        log_request(
            endpoint="/chat",
            status_code=502,
            latency_ms=latency_ms,
            provider=exc.provider,
            model=body.model,
            api_key_id=api_key.id,
        )
        logger.error("All providers failed: %s", exc)
        raise HTTPException(status_code=502, detail="All LLM providers failed") from exc

    latency_ms = int((time.perf_counter() - start) * 1000)

    # Log the successful request
    log_request(
        endpoint="/chat",
        status_code=status_code,
        latency_ms=latency_ms,
        provider=response.provider,
        model=response.model,
        api_key_id=api_key.id,
    )

    return response


# ---------- Budget management endpoints ----------
class BudgetConfigRequest:
    def __init__(self, provider: str, monthly_budget_usd: float, warning_threshold_percent: float = 80.0, hard_limit_percent: float = 100.0):
        self.provider = provider.lower()
        self.monthly_budget_usd = monthly_budget_usd
        self.warning_threshold_percent = warning_threshold_percent
        self.hard_limit_percent = hard_limit_percent


@app.get("/budget/status")
def get_budget_status_endpoint(
    api_key: ApiKey = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    """Get budget usage status for all configured providers."""
    try:
        status = get_budget_status(api_key.id, db)
        return {"status": status}
    except Exception as e:
        logger.error(f"Error getting budget status: {e}")
        raise HTTPException(status_code=500, detail="Failed to get budget status")


@app.get("/budget")
def get_budgets(
    api_key: ApiKey = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    """Get all budget configurations for this API key."""
    try:
        budgets = db.query(BudgetConfig).filter(
            BudgetConfig.api_key_id == api_key.id
        ).all()
        
        result = []
        for budget in budgets:
            current_cost = get_monthly_cost(api_key.id, budget.provider, db)
            result.append({
                "provider": budget.provider,
                "monthly_budget_usd": budget.monthly_budget_usd,
                "warning_threshold_percent": budget.warning_threshold_percent,
                "hard_limit_percent": budget.hard_limit_percent,
                "current_cost_usd": round(current_cost, 8),
                "is_enabled": budget.is_enabled == "true",
            })
        
        return {"budgets": result}
    except Exception as e:
        logger.error(f"Error getting budgets: {e}")
        raise HTTPException(status_code=500, detail="Failed to get budgets")


@app.post("/budget")
def set_budget(
    provider: str,
    monthly_budget_usd: float,
    warning_threshold_percent: float = 80.0,
    hard_limit_percent: float = 100.0,
    api_key: ApiKey = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    """Set or update a budget for a provider."""
    try:
        provider = provider.lower()
        
        # Check if budget already exists
        existing = db.query(BudgetConfig).filter(
            BudgetConfig.api_key_id == api_key.id,
            BudgetConfig.provider == provider,
        ).first()
        
        if existing:
            # Update
            existing.monthly_budget_usd = monthly_budget_usd
            existing.warning_threshold_percent = warning_threshold_percent
            existing.hard_limit_percent = hard_limit_percent
            existing.is_enabled = "true"
        else:
            # Create new
            new_budget = BudgetConfig(
                api_key_id=api_key.id,
                provider=provider,
                monthly_budget_usd=monthly_budget_usd,
                warning_threshold_percent=warning_threshold_percent,
                hard_limit_percent=hard_limit_percent,
                is_enabled="true",
            )
            db.add(new_budget)
        
        db.commit()
        
        return {
            "provider": provider,
            "monthly_budget_usd": monthly_budget_usd,
            "warning_threshold_percent": warning_threshold_percent,
            "hard_limit_percent": hard_limit_percent,
            "status": "updated" if existing else "created",
        }
    except Exception as e:
        db.rollback()
        logger.error(f"Error setting budget: {e}")
        raise HTTPException(status_code=500, detail="Failed to set budget")



@app.delete("/budget/{provider}")
def delete_budget(
    provider: str,
    api_key: ApiKey = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    """Disable/delete a budget for a provider."""
    try:
        provider = provider.lower()
        
        budget = db.query(BudgetConfig).filter(
            BudgetConfig.api_key_id == api_key.id,
            BudgetConfig.provider == provider,
        ).first()
        
        if not budget:
            raise HTTPException(status_code=404, detail="Budget not found")
        
        db.delete(budget)
        db.commit()
        
        return {"provider": provider, "status": "deleted"}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error deleting budget: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete budget")


@app.get("/analytics/costs")
def get_cost_analytics(
    api_key: ApiKey = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    """Get cost data for analytics visualization."""
    from sqlalchemy import func
    from datetime import datetime, timedelta
    
    try:
        # Provider breakdown (Total cost per provider)
        provider_data = db.query(
            CostLog.provider, 
            func.sum(CostLog.cost_usd).label("total_cost"),
            func.sum(CostLog.tokens_used).label("total_tokens")
        ).filter(CostLog.api_key_id == api_key.id).group_by(CostLog.provider).all()
        
        # Recent daily trends (Last 7 days) - Cost, Tokens, and Messages
        seven_days_ago = datetime.now() - timedelta(days=7)
        daily_data = db.query(
            func.date(CostLog.created_at).label("day"),
            func.sum(CostLog.cost_usd).label("daily_cost"),
            func.sum(CostLog.tokens_used).label("daily_tokens"),
            func.count(CostLog.id).label("daily_messages")
        ).filter(
            CostLog.api_key_id == api_key.id,
            CostLog.created_at >= seven_days_ago
        ).group_by(func.date(CostLog.created_at)).order_by(func.date(CostLog.created_at)).all()
        
        # Provider status breakdown (Success vs Failure)
        status_data = db.query(
            RequestLog.provider,
            RequestLog.status_code,
            func.count(RequestLog.id).label("count")
        ).filter(
            RequestLog.api_key_id == api_key.id,
            RequestLog.created_at >= seven_days_ago
        ).group_by(RequestLog.provider, RequestLog.status_code).all()
        
        # Format status data
        statuses = {}
        for s in status_data:
            prov = s.provider or "unknown"
            if prov not in statuses:
                statuses[prov] = {"success": 0, "failure": 0}
            
            if s.status_code == 200:
                statuses[prov]["success"] += s.count
            else:
                statuses[prov]["failure"] += s.count

        return {
            "providers": [
                {"provider": p.provider, "cost": round(p.total_cost or 0.0, 8), "tokens": p.total_tokens or 0} 
                for p in provider_data
            ],
            "trends": [
                {
                    "day": str(d.day), 
                    "cost": round(d.daily_cost or 0.0, 8),
                    "tokens": int(d.daily_tokens or 0),
                    "messages": int(d.daily_messages or 0)
                } 
                for d in daily_data
            ],
            "status_breakdown": [
                {"provider": k, "success": v["success"], "failure": v["failure"]}
                for k, v in statuses.items()
            ]
        }
    except Exception as e:
        logger.error(f"Error getting cost analytics: {e}")
        raise HTTPException(status_code=500, detail="Failed to get cost analytics")


# ---------- User Management Routes ----------
@app.get("/users")
def get_users(username: str = Depends(verify_session)):
    if not username:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if username != "admin":
        raise HTTPException(status_code=403, detail="Forbidden: Only admin can manage users")
    return User.get_all_users()

@app.post("/users")
async def create_user(
    request: Request,
    username: str = Depends(verify_session)
):
    if not username or username != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    
    try:
        data = await request.json()
        new_user = data.get("username")
        password = data.get("password")
        role = data.get("role", "user")
        
        if not new_user or not password:
            raise HTTPException(status_code=400, detail="Missing username or password")
            
        if User.create_user(new_user, password, role):
            return {"status": "success", "username": new_user}
        else:
            raise HTTPException(status_code=400, detail="User already exists")
    except Exception as e:
        logger.error(f"Error creating user: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.delete("/users/{target_username}")
def delete_user(
    target_username: str,
    username: str = Depends(verify_session)
):
    if not username or username != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    
    if User.delete_user(target_username):
        return {"status": "success"}
    else:
        raise HTTPException(status_code=400, detail="Cannot delete user (might be admin or not exist)")

