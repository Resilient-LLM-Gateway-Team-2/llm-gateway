import os
import time
import logging
import secrets
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy import create_engine, func
from sqlalchemy.orm import Session, sessionmaker

from app.schemas import ChatRequest, ChatResponse, CostEstimate
from app.providers import ProviderError
from app.auth import User as AuthUser
from app.models import (
    ApiKey,
    BudgetConfig,
    Chat,
    CostLog,
    Message,
    RequestLog,
    User as DbUser,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

OPENAI_PROMPT_USD_PER_1K = float(os.getenv("OPENAI_PROMPT_USD_PER_1K", "0.005"))
OPENAI_COMPLETION_USD_PER_1K = float(os.getenv("OPENAI_COMPLETION_USD_PER_1K", "0.015"))
GEMINI_PROMPT_USD_PER_1K = float(os.getenv("GEMINI_PROMPT_USD_PER_1K", "0.00125"))
GEMINI_COMPLETION_USD_PER_1K = float(os.getenv("GEMINI_COMPLETION_USD_PER_1K", "0.00375"))

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://gateway:gatewaypass@postgres:5432/gatewaydb",
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI(title="Resilient LLM Gateway - Sprint 2")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


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
        provider_used_cost = 0.0
    else:
        provider_used_cost = min(openai_cost, gemini_cost)

    return CostEstimate(
        openai_usd=openai_cost,
        gemini_usd=gemini_cost,
        provider_used_usd=round(provider_used_cost, 8),
        currency="USD",
        pricing_basis="estimated_from_tokens",
    )


def verify_session(request: Request) -> Optional[str]:
    session_token = request.cookies.get("session_token")
    if not session_token:
        return None
    return AuthUser.verify_session(session_token)


def get_current_user(request: Request, db: Session = Depends(get_db)) -> DbUser:
    username = verify_session(request)
    if not username:
        raise HTTPException(status_code=401, detail="Not authenticated")

    user = db.query(DbUser).filter(DbUser.username == username).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="User account is disabled")

    user.last_active_at = datetime.utcnow()
    db.commit()
    db.refresh(user)

    return user


def require_admin(request: Request, db: Session = Depends(get_db)) -> DbUser:
    user = get_current_user(request, db)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def get_or_create_user_api_key(user: DbUser, db: Session) -> ApiKey:
    api_key = (
        db.query(ApiKey)
        .filter(ApiKey.user_id == user.id, ApiKey.is_active == True)
        .order_by(ApiKey.created_at.asc())
        .first()
    )

    if api_key:
        return api_key

    api_key = ApiKey(
        user_id=user.id,
        key=f"llm_key_{secrets.token_urlsafe(32)}",
        name="Default API Key",
        is_active=True,
    )
    db.add(api_key)
    db.commit()
    db.refresh(api_key)

    return api_key


def verify_api_key(
    x_api_key: str = Header(None, alias="X-API-Key"),
    db: Session = Depends(get_db),
) -> ApiKey:
    if not x_api_key:
        raise HTTPException(status_code=403, detail="Missing X-API-Key header")

    api_key = (
        db.query(ApiKey)
        .filter(ApiKey.key == x_api_key, ApiKey.is_active == True)
        .first()
    )

    if not api_key:
        raise HTTPException(status_code=403, detail="Invalid API key")

    api_key.last_used_at = datetime.utcnow()
    db.commit()
    db.refresh(api_key)

    return api_key


def resolve_chat_api_key(
    request: Request,
    x_api_key: str = Header(None, alias="X-API-Key"),
    db: Session = Depends(get_db),
) -> ApiKey:
    username = verify_session(request)

    if username:
        user = db.query(DbUser).filter(DbUser.username == username).first()
        if not user or not user.is_active:
            raise HTTPException(status_code=401, detail="User not found or disabled")
        return get_or_create_user_api_key(user, db)

    if not x_api_key:
        raise HTTPException(status_code=403, detail="Missing X-API-Key header")

    return verify_api_key(x_api_key=x_api_key, db=db)


def log_request(
    *,
    endpoint: str,
    status_code: int,
    latency_ms: int,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    api_key_id: Optional[int] = None,
    user_id: Optional[int] = None,
    tokens_used: int = 0,
    cost_usd: float = 0.0,
) -> None:
    db: Session = SessionLocal()
    try:
        row = RequestLog(
            user_id=user_id,
            api_key_id=api_key_id,
            endpoint=endpoint,
            provider=provider,
            model=model,
            status_code=status_code,
            latency_ms=latency_ms,
            tokens_used=tokens_used or 0,
            cost_usd=cost_usd or 0.0,
        )
        db.add(row)
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error("Failed to log request: %s", exc)
    finally:
        db.close()


def log_cost(
    *,
    user_id: int,
    api_key_id: int,
    provider: str,
    model: Optional[str],
    cost_usd: float,
    tokens_used: int = 0,
) -> None:
    current_month = datetime.utcnow().strftime("%Y-%m")

    db: Session = SessionLocal()
    try:
        row = CostLog(
            user_id=user_id,
            api_key_id=api_key_id,
            provider=(provider or "unknown").lower(),
            model=model,
            cost_usd=cost_usd or 0.0,
            tokens_used=tokens_used or 0,
            month=current_month,
        )
        db.add(row)

        user = db.query(DbUser).filter(DbUser.id == user_id).first()
        if user:
            user.total_cost_usd = float(user.total_cost_usd or 0.0) + float(cost_usd or 0.0)
            user.total_tokens = int(user.total_tokens or 0) + int(tokens_used or 0)
            user.last_active_at = datetime.utcnow()

        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error("Failed to log cost: %s", exc)
    finally:
        db.close()


def get_monthly_cost(api_key_id: int, provider: str, db: Session) -> float:
    current_month = datetime.utcnow().strftime("%Y-%m")
    result = (
        db.query(func.sum(CostLog.cost_usd))
        .filter(
            CostLog.api_key_id == api_key_id,
            CostLog.provider == provider.lower(),
            CostLog.month == current_month,
        )
        .scalar()
    )
    return float(result or 0.0)


def get_budget_status(api_key_id: int, db: Session) -> dict:
    budgets = (
        db.query(BudgetConfig)
        .filter(BudgetConfig.api_key_id == api_key_id, BudgetConfig.is_enabled == True)
        .all()
    )

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


def check_budget_allowed(api_key_id: int, provider: str, db: Session) -> tuple[bool, Optional[str]]:
    budget = (
        db.query(BudgetConfig)
        .filter(
            BudgetConfig.api_key_id == api_key_id,
            BudgetConfig.provider == provider.lower(),
            BudgetConfig.is_enabled == True,
        )
        .first()
    )

    if not budget:
        return True, None

    current_cost = get_monthly_cost(api_key_id, provider, db)
    limit = budget.monthly_budget_usd
    percent = (current_cost / limit * 100) if limit > 0 else 0

    if percent >= budget.hard_limit_percent:
        return False, f"Budget limit reached for {provider}: ${current_cost:.8f} / ${limit:.2f}"

    if percent >= budget.warning_threshold_percent:
        return True, f"Approaching budget for {provider}: {percent:.1f}% (${current_cost:.8f} / ${limit:.2f})"

    return True, None


def _html_response(file_path: str, missing_message: str) -> HTMLResponse:
    try:
        with open(file_path, "r") as f:
            return HTMLResponse(content=f.read())
    except Exception as exc:
        logger.error("Failed to load %s: %s", file_path, exc)
        raise HTTPException(status_code=500, detail=missing_message)


@app.get("/login", response_class=HTMLResponse)
async def serve_login():
    return _html_response("app/login.html", "Login page not found")


@app.get("/signup-page", response_class=HTMLResponse)
async def serve_signup_page():
    return _html_response("app/signup.html", "Signup page not found")


@app.post("/login")
async def handle_login(request: Request, db: Session = Depends(get_db)):
    try:
        content_type = request.headers.get("content-type", "")

        if "json" in content_type:
            data = await request.json()
            username = data.get("username", "").strip()
            password = data.get("password", "")
        else:
            form_data = await request.form()
            username = str(form_data.get("username", "")).strip()
            password = str(form_data.get("password", ""))

        if not username or not password or not AuthUser.authenticate(db, username, password):
            if "json" in content_type:
                return JSONResponse(status_code=401, content={"detail": "Invalid username or password"})
            return RedirectResponse(url="/login?error=Invalid username or password", status_code=303)

        user = db.query(DbUser).filter(DbUser.username == username).first()
        if not user:
            return JSONResponse(status_code=401, content={"detail": "User not found"})

        session_token = AuthUser.create_session(username)
        redirect_url = "/dashboard" if user.role == "admin" else "/user-dashboard"

        if "json" in content_type:
            response = JSONResponse(
                status_code=200,
                content={
                    "status": "success",
                    "redirect": redirect_url,
                    "message": "Login successful",
                },
            )
        else:
            response = RedirectResponse(url=redirect_url, status_code=303)

        response.set_cookie(
            key="session_token",
            value=session_token,
            max_age=7 * 24 * 60 * 60,
            httponly=True,
            samesite="Lax",
        )
        return response

    except Exception as exc:
        logger.error("Login error: %s", exc)
        if "json" in request.headers.get("content-type", ""):
            return JSONResponse(status_code=400, content={"detail": "Login failed"})
        return RedirectResponse(url="/login?error=Login failed", status_code=303)


@app.post("/signup")
async def handle_signup(request: Request, db: Session = Depends(get_db)):
    try:
        content_type = request.headers.get("content-type", "")

        if "json" in content_type:
            data = await request.json()
            email = data.get("email", "").strip().lower()
            username = data.get("username", "").strip()
            password = data.get("password", "")
        else:
            form_data = await request.form()
            email = str(form_data.get("email", "")).strip().lower()
            username = str(form_data.get("username", "")).strip()
            password = str(form_data.get("password", ""))

        if not email or not username or not password:
            return JSONResponse(status_code=400, content={"detail": "Missing fields"})

        user = AuthUser.create_user(
            db,
            email=email,
            username=username,
            password=password,
            role="user",
            plan="pro",
        )

        session_token = AuthUser.create_session(user.username)

        response = JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "redirect": "/user-dashboard",
                "message": "Signup successful",
            },
        )
        response.set_cookie(
            key="session_token",
            value=session_token,
            max_age=7 * 24 * 60 * 60,
            httponly=True,
            samesite="Lax",
        )
        return response

    except ValueError as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc)})
    except Exception as exc:
        db.rollback()
        logger.error("Signup error: %s", exc)
        return JSONResponse(status_code=500, content={"detail": "Signup failed"})


@app.get("/", response_class=HTMLResponse)
async def serve_landing(request: Request, db: Session = Depends(get_db)):
    username = verify_session(request)
    if username:
        user = db.query(DbUser).filter(DbUser.username == username).first()
        if user:
            return RedirectResponse(
                url="/dashboard" if user.role == "admin" else "/user-dashboard",
                status_code=303,
            )
    return _html_response("app/landing.html", "Landing page not found")


@app.get("/features", response_class=HTMLResponse)
async def serve_features():
    return _html_response("app/features.html", "Features page not found")


@app.get("/providers", response_class=HTMLResponse)
async def serve_providers():
    return _html_response("app/providers.html", "Providers page not found")


@app.get("/resources", response_class=HTMLResponse)
async def serve_resources():
    return _html_response("app/resources.html", "Resources page not found")


@app.get("/api-docs", response_class=HTMLResponse)
async def serve_api_docs():
    return _html_response("app/api-docs.html", "API docs page not found")


@app.get("/status", response_class=HTMLResponse)
async def serve_status():
    return _html_response("app/status.html", "Status page not found")


@app.get("/contact", response_class=HTMLResponse)
async def serve_contact():
    return _html_response("app/contact.html", "Contact page not found")


@app.get("/privacy", response_class=HTMLResponse)
async def serve_privacy():
    return _html_response("app/privacy.html", "Privacy page not found")


@app.get("/terms", response_class=HTMLResponse)
async def serve_terms():
    return _html_response("app/terms.html", "Terms page not found")


@app.get("/dashboard", response_class=HTMLResponse)
async def serve_dashboard(admin: DbUser = Depends(require_admin)):
    return _html_response("app/dashboard.html", "Dashboard not found")


@app.get("/user-dashboard", response_class=HTMLResponse)
async def serve_user_dashboard(user: DbUser = Depends(get_current_user)):
    return _html_response("app/user_dashboard.html", "User dashboard not found")


@app.post("/logout")
async def handle_logout(request: Request):
    session_token = request.cookies.get("session_token")
    if session_token:
        AuthUser.logout(session_token)

    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(key="session_token")
    return response


@app.get("/me")
def get_me(user: DbUser = Depends(get_current_user)):
    return {
        "id": user.id,
        "email": user.email,
        "username": user.username,
        "role": user.role,
        "plan": user.plan,
        "preferred_model": user.preferred_model,
        "profile_image": user.profile_image,
        "total_tokens": user.total_tokens,
        "total_cost_usd": user.total_cost_usd,
        "last_login_at": user.last_login_at,
        "last_active_at": user.last_active_at,
        "created_at": user.created_at,
    }


@app.put("/me")
async def update_me(request: Request, db: Session = Depends(get_db), user: DbUser = Depends(get_current_user)):
    data = await request.json()

    new_email = data.get("email")
    new_username = data.get("username")
    preferred_model = data.get("preferred_model")
    profile_image = data.get("profile_image")

    if new_email and new_email != user.email:
        existing = db.query(DbUser).filter(DbUser.email == new_email).first()
        if existing and existing.id != user.id:
            raise HTTPException(status_code=400, detail="Email already exists")
        user.email = new_email.strip().lower()

    if new_username and new_username != user.username:
        existing = db.query(DbUser).filter(DbUser.username == new_username).first()
        if existing and existing.id != user.id:
            raise HTTPException(status_code=400, detail="Username already exists")
        user.username = new_username.strip()

    if preferred_model:
        user.preferred_model = preferred_model

    if profile_image is not None:
        user.profile_image = profile_image

    user.last_active_at = datetime.utcnow()
    db.commit()
    db.refresh(user)

    return get_me(user)


@app.post("/api-key")
def generate_api_key(user: DbUser = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        api_key = ApiKey(
            user_id=user.id,
            key=f"llm_key_{secrets.token_urlsafe(32)}",
            name="Generated API Key",
            is_active=True,
        )
        db.add(api_key)
        db.commit()
        db.refresh(api_key)

        return {
            "api_key": api_key.key,
            "message": "Save this key securely. You won't see it again.",
            "usage": (
                "curl -X POST http://localhost:8000/chat "
                f"-H 'X-API-Key: {api_key.key}' "
                "-H 'Content-Type: application/json' "
                "-d '{\"messages\": [{\"role\": \"user\", \"content\": \"Hello\"}], \"model\": \"gpt-3.5-turbo\"}'"
            ),
        }
    except Exception as exc:
        db.rollback()
        logger.error("Error generating API key: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to generate API key")


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.get("/health/detailed")
def health_detailed():
    result = {"api": "ok", "postgres": "error", "redis": "error"}

    try:
        from sqlalchemy import text
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        db.close()
        result["postgres"] = "ok"
    except Exception:
        pass

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
    request: Request,
    api_key: ApiKey = Depends(resolve_chat_api_key),
    db: Session = Depends(get_db),
):
    from app.router import route_request
    from app.cache import get_cached_response, set_cached_response
    import hashlib
    import json

    start = time.perf_counter()

    user = db.query(DbUser).filter(DbUser.id == api_key.user_id).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="API key owner not found or disabled")

    provider_name = "openai" if "gpt" in body.model.lower() else "gemini"
    allowed, warning = check_budget_allowed(api_key.id, provider_name, db)
    if not allowed:
        raise HTTPException(status_code=403, detail=warning)

    chat_id = getattr(body, "chat_id", None)
    if chat_id:
        chat_row = db.query(Chat).filter(Chat.id == chat_id, Chat.user_id == user.id).first()
        if not chat_row:
            raise HTTPException(status_code=404, detail="Chat not found")
    else:
        chat_row = Chat(user_id=user.id, title="New Chat")
        db.add(chat_row)
        db.commit()
        db.refresh(chat_row)

    full_request_state = {
        "model": body.model,
        "temperature": body.temperature,
        "max_tokens": body.max_tokens,
        "messages": [{"role": m.role, "content": m.content} for m in body.messages],
    }
    messages_serialized = json.dumps(full_request_state, sort_keys=True)
    exact_hash = hashlib.sha256(messages_serialized.encode("utf-8")).hexdigest()
    exact_key = f"chat:{api_key.id}:{exact_hash}"

    last_user_msg = ""
    for m in reversed(body.messages):
        if m.role == "user":
            last_user_msg = m.content
            break

    if last_user_msg:
        user_message = Message(chat_id=chat_row.id, role="user", content=last_user_msg)
        db.add(user_message)
        if chat_row.title == "New Chat":
            chat_row.title = last_user_msg[:40]
        chat_row.updated_at = datetime.utcnow()
        user.last_active_at = datetime.utcnow()
        db.commit()

    prompt_cache_basis = f"{body.model}:{last_user_msg}"
    prompt_hash = hashlib.sha256(prompt_cache_basis.encode("utf-8")).hexdigest()
    prompt_key = f"prompt:{api_key.id}:{prompt_hash}"

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
        tokens = response.usage.total_tokens if response.usage else 0
        cost = response.cost_estimate.provider_used_usd if response.cost_estimate else 0.0

        assistant_message = Message(
            chat_id=chat_row.id,
            role="assistant",
            content=response.content,
            provider="redis_cache",
            model=response.model,
            latency_ms=latency_ms,
            tokens=tokens,
            cost_usd=cost,
        )
        db.add(assistant_message)
        db.commit()

        log_request(
            endpoint="/chat",
            status_code=200,
            latency_ms=latency_ms,
            provider="redis_cache",
            model=response.model,
            api_key_id=api_key.id,
            user_id=user.id,
            tokens_used=tokens,
            cost_usd=cost,
        )
        return response

    try:
        response, route_path = route_request(body)
        response.route_path = route_path
        response.cost_estimate = _build_cost_estimate(response)
        status_code = 200

        latency_ms = int((time.perf_counter() - start) * 1000)
        tokens = response.usage.total_tokens if response.usage else 0
        cost = response.cost_estimate.provider_used_usd if response.cost_estimate else 0.0

        assistant_message = Message(
            chat_id=chat_row.id,
            role="assistant",
            content=response.content,
            provider=response.provider,
            model=response.model,
            latency_ms=latency_ms,
            tokens=tokens,
            cost_usd=cost,
        )
        db.add(assistant_message)
        db.commit()

        response_dict = response.model_dump() if hasattr(response, "model_dump") else response.dict()
        set_cached_response(exact_key, response_dict)
        set_cached_response(prompt_key, response_dict)

        if response.cost_estimate and response.provider:
            log_cost(
                user_id=user.id,
                api_key_id=api_key.id,
                provider=response.provider,
                model=response.model,
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
            user_id=user.id,
        )
        logger.error("All providers failed: %s", exc)
        raise HTTPException(status_code=502, detail="All LLM providers failed") from exc

    latency_ms = int((time.perf_counter() - start) * 1000)
    log_request(
        endpoint="/chat",
        status_code=status_code,
        latency_ms=latency_ms,
        provider=response.provider,
        model=response.model,
        api_key_id=api_key.id,
        user_id=user.id,
        tokens_used=tokens,
        cost_usd=cost,
    )

    return response


@app.get("/my-chats")
def get_my_chats(user: DbUser = Depends(get_current_user), db: Session = Depends(get_db)):
    chats = (
        db.query(Chat)
        .join(Message)
        .filter(Chat.user_id == user.id)
        .group_by(Chat.id)
        .order_by(Chat.updated_at.desc())
        .all()
    )

    return {
        "chats": [
            {
                "id": chat.id,
                "title": chat.title,
                "created_at": chat.created_at,
                "updated_at": chat.updated_at,
            }
            for chat in chats
        ]
    }


@app.post("/my-chats")
def create_new_chat(user: DbUser = Depends(get_current_user), db: Session = Depends(get_db)):
    chat_row = Chat(user_id=user.id, title="New Chat")
    db.add(chat_row)
    db.commit()
    db.refresh(chat_row)

    return {
        "id": chat_row.id,
        "title": chat_row.title,
        "created_at": chat_row.created_at,
        "updated_at": chat_row.updated_at,
    }


@app.get("/my-chats/{chat_id}/messages")
def get_chat_messages(chat_id: int, user: DbUser = Depends(get_current_user), db: Session = Depends(get_db)):
    chat_row = db.query(Chat).filter(Chat.id == chat_id, Chat.user_id == user.id).first()
    if not chat_row:
        raise HTTPException(status_code=404, detail="Chat not found")

    messages = (
        db.query(Message)
        .filter(Message.chat_id == chat_id)
        .order_by(Message.created_at.asc())
        .all()
    )

    return {
        "chat_id": chat_row.id,
        "title": chat_row.title,
        "messages": [
            {
                "id": message.id,
                "role": message.role,
                "content": message.content,
                "provider": message.provider,
                "model": message.model,
                "latency_ms": message.latency_ms,
                "tokens": message.tokens,
                "cost_usd": message.cost_usd,
                "created_at": message.created_at,
            }
            for message in messages
        ],
    }


@app.get("/users")
def get_users(admin: DbUser = Depends(require_admin), db: Session = Depends(get_db)):
    users = db.query(DbUser).order_by(DbUser.created_at.desc()).all()

    result = []
    for user in users:
        chat_count = db.query(func.count(Chat.id)).filter(Chat.user_id == user.id).scalar() or 0
        message_count = (
            db.query(func.count(Message.id))
            .join(Chat, Message.chat_id == Chat.id)
            .filter(Chat.user_id == user.id)
            .scalar()
            or 0
        )
        api_key_count = db.query(func.count(ApiKey.id)).filter(ApiKey.user_id == user.id).scalar() or 0

        result.append({
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "role": user.role,
            "plan": user.plan,
            "preferred_model": user.preferred_model,
            "is_active": user.is_active,
            "total_tokens": user.total_tokens,
            "total_cost_usd": round(float(user.total_cost_usd or 0.0), 8),
            "chat_count": chat_count,
            "message_count": message_count,
            "api_key_count": api_key_count,
            "last_login_at": user.last_login_at,
            "last_active_at": user.last_active_at,
            "created_at": user.created_at,
        })

    return result


@app.get("/users/{user_id}")
def get_user_detail(user_id: int, admin: DbUser = Depends(require_admin), db: Session = Depends(get_db)):
    user = db.query(DbUser).filter(DbUser.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    chat_count = db.query(func.count(Chat.id)).filter(Chat.user_id == user.id).scalar() or 0
    message_count = (
        db.query(func.count(Message.id))
        .join(Chat, Message.chat_id == Chat.id)
        .filter(Chat.user_id == user.id)
        .scalar()
        or 0
    )
    api_key_count = db.query(func.count(ApiKey.id)).filter(ApiKey.user_id == user.id).scalar() or 0
    request_count = db.query(func.count(RequestLog.id)).filter(RequestLog.user_id == user.id).scalar() or 0

    provider_rows = (
        db.query(
            CostLog.provider,
            func.sum(CostLog.cost_usd).label("cost"),
            func.sum(CostLog.tokens_used).label("tokens"),
        )
        .filter(CostLog.user_id == user.id)
        .group_by(CostLog.provider)
        .all()
    )

    recent_chats = (
        db.query(Chat)
        .filter(Chat.user_id == user.id)
        .order_by(Chat.updated_at.desc())
        .limit(10)
        .all()
    )

    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "role": user.role,
        "plan": user.plan,
        "preferred_model": user.preferred_model,
        "is_active": user.is_active,
        "total_tokens": user.total_tokens,
        "total_cost_usd": round(float(user.total_cost_usd or 0.0), 8),
        "chat_count": chat_count,
        "message_count": message_count,
        "api_key_count": api_key_count,
        "request_count": request_count,
        "last_login_at": user.last_login_at,
        "last_active_at": user.last_active_at,
        "created_at": user.created_at,
        "provider_usage": [
            {
                "provider": row.provider,
                "cost": round(float(row.cost or 0.0), 8),
                "tokens": int(row.tokens or 0),
            }
            for row in provider_rows
        ],
        "recent_chats": [
            {
                "id": chat.id,
                "title": chat.title,
                "created_at": chat.created_at,
                "updated_at": chat.updated_at,
            }
            for chat in recent_chats
        ],
    }


@app.post("/users")
async def create_admin_user(request: Request, admin: DbUser = Depends(require_admin), db: Session = Depends(get_db)):
    data = await request.json()
    try:
        user = AuthUser.create_user(
            db,
            email=data.get("email", "").strip().lower(),
            username=data.get("username", "").strip(),
            password=data.get("password", ""),
            role=data.get("role", "user"),
            plan=data.get("plan", "basic"),
        )
        return {"status": "success", "id": user.id, "username": user.username}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.patch("/users/{user_id}")
async def update_user(user_id: int, request: Request, admin: DbUser = Depends(require_admin), db: Session = Depends(get_db)):
    user = db.query(DbUser).filter(DbUser.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    data = await request.json()

    if "role" in data:
        user.role = data["role"]
    if "plan" in data:
        user.plan = data["plan"]
    if "preferred_model" in data:
        user.preferred_model = data["preferred_model"]
    if "is_active" in data:
        user.is_active = bool(data["is_active"])

    db.commit()
    db.refresh(user)

    return {"status": "success", "id": user.id}


@app.delete("/users/{user_id}")
def deactivate_user(user_id: int, admin: DbUser = Depends(require_admin), db: Session = Depends(get_db)):
    if admin.id == user_id:
        raise HTTPException(status_code=400, detail="Admin cannot deactivate own account")

    user = db.query(DbUser).filter(DbUser.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.is_active = False
    db.commit()

    return {"status": "success"}


@app.get("/analytics/costs")
def get_cost_analytics(admin: DbUser = Depends(require_admin), db: Session = Depends(get_db)):
    seven_days_ago = datetime.utcnow() - timedelta(days=7)

    provider_data = (
        db.query(
            CostLog.provider,
            func.sum(CostLog.cost_usd).label("total_cost"),
            func.sum(CostLog.tokens_used).label("total_tokens"),
        )
        .group_by(CostLog.provider)
        .all()
    )

    daily_data = (
        db.query(
            func.date(CostLog.created_at).label("day"),
            func.sum(CostLog.cost_usd).label("daily_cost"),
            func.sum(CostLog.tokens_used).label("daily_tokens"),
            func.count(CostLog.id).label("daily_messages"),
        )
        .filter(CostLog.created_at >= seven_days_ago)
        .group_by(func.date(CostLog.created_at))
        .order_by(func.date(CostLog.created_at))
        .all()
    )

    status_data = (
        db.query(
            RequestLog.provider,
            RequestLog.status_code,
            func.count(RequestLog.id).label("count"),
        )
        .filter(RequestLog.created_at >= seven_days_ago)
        .group_by(RequestLog.provider, RequestLog.status_code)
        .all()
    )

    statuses = {}
    for item in status_data:
        provider = item.provider or "unknown"
        if provider not in statuses:
            statuses[provider] = {"success": 0, "failure": 0}
        if item.status_code == 200:
            statuses[provider]["success"] += item.count
        else:
            statuses[provider]["failure"] += item.count

    return {
        "providers": [
            {
                "provider": row.provider,
                "cost": round(float(row.total_cost or 0.0), 8),
                "tokens": int(row.total_tokens or 0),
            }
            for row in provider_data
        ],
        "trends": [
            {
                "day": str(row.day),
                "cost": round(float(row.daily_cost or 0.0), 8),
                "tokens": int(row.daily_tokens or 0),
                "messages": int(row.daily_messages or 0),
            }
            for row in daily_data
        ],
        "status_breakdown": [
            {
                "provider": provider,
                "success": values["success"],
                "failure": values["failure"],
            }
            for provider, values in statuses.items()
        ],
    }


@app.get("/budget/status")
def get_budget_status_endpoint(
    api_key: ApiKey = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    try:
        status = get_budget_status(api_key.id, db)
        return {"status": status}
    except Exception as exc:
        logger.error("Error getting budget status: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to get budget status")


@app.get("/budget")
def get_budgets(
    api_key: ApiKey = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    try:
        budgets = db.query(BudgetConfig).filter(BudgetConfig.api_key_id == api_key.id).all()

        return {
            "budgets": [
                {
                    "provider": budget.provider,
                    "monthly_budget_usd": budget.monthly_budget_usd,
                    "warning_threshold_percent": budget.warning_threshold_percent,
                    "hard_limit_percent": budget.hard_limit_percent,
                    "current_cost_usd": round(get_monthly_cost(api_key.id, budget.provider, db), 8),
                    "is_enabled": bool(budget.is_enabled),
                }
                for budget in budgets
            ]
        }
    except Exception as exc:
        logger.error("Error getting budgets: %s", exc)
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
    try:
        provider = provider.lower()
        existing = (
            db.query(BudgetConfig)
            .filter(BudgetConfig.api_key_id == api_key.id, BudgetConfig.provider == provider)
            .first()
        )

        if existing:
            existing.monthly_budget_usd = monthly_budget_usd
            existing.warning_threshold_percent = warning_threshold_percent
            existing.hard_limit_percent = hard_limit_percent
            existing.is_enabled = True
        else:
            new_budget = BudgetConfig(
                user_id=api_key.user_id,
                api_key_id=api_key.id,
                provider=provider,
                monthly_budget_usd=monthly_budget_usd,
                warning_threshold_percent=warning_threshold_percent,
                hard_limit_percent=hard_limit_percent,
                is_enabled=True,
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
    except Exception as exc:
        db.rollback()
        logger.error("Error setting budget: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to set budget")


@app.delete("/budget/{provider}")
def delete_budget(
    provider: str,
    api_key: ApiKey = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    try:
        budget = (
            db.query(BudgetConfig)
            .filter(BudgetConfig.api_key_id == api_key.id, BudgetConfig.provider == provider.lower())
            .first()
        )
        if not budget:
            raise HTTPException(status_code=404, detail="Budget not found")

        db.delete(budget)
        db.commit()
        return {"provider": provider.lower(), "status": "deleted"}
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        logger.error("Error deleting budget: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to delete budget")
