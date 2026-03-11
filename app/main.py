import os
import time
import logging
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, create_engine, func
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from app.schemas import ChatRequest, ChatResponse
from app.providers import ProviderError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


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


# ---------- Routes ----------
@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
def chat(
    body: ChatRequest,
    api_key: ApiKey = Depends(verify_api_key),
):
    """
    Main chat endpoint.

    1. Pydantic validates the request body automatically
    2. verify_api_key enforces auth via the X-API-Key header
    3. route_request tries OpenAI, falls back to Gemini
    4. log_request captures observability data to PostgreSQL
    5. Returns a standardised ChatResponse
    """
    # Lazy import to avoid circular dependency at module load time
    from app.router import route_request
    from app.cache import get_cached_response, set_cached_response
    import hashlib

    start = time.perf_counter()

    # Create a unique cache key based on the api_key and message content
    prompt_text = "".join([m.content for m in body.messages])
    prompt_hash = hashlib.sha256(prompt_text.encode('utf-8')).hexdigest()
    prompt_key = f"session:{api_key.id}:{prompt_hash}"

    # Check the Redis Cache first
    cached_data = get_cached_response(prompt_key)
    if cached_data:
        response = ChatResponse(**cached_data)
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
        response: ChatResponse = route_request(body)
        status_code = 200
        
        # Cache the successful response
        response_dict = response.model_dump() if hasattr(response, 'model_dump') else response.dict()
        set_cached_response(prompt_key, response_dict)
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