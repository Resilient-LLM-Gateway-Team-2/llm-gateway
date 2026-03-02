import os
import time
from typing import Optional

from fastapi import FastAPI
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, create_engine, func
from sqlalchemy.orm import Session, declarative_base, sessionmaker

app = FastAPI(title="Resilient LLM Gateway - Sprint 1")

# ---------- DB setup ----------
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://gateway:gatewaypass@postgres:5432/gatewaydb",  # docker-compose default
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


def log_request(
    *,
    endpoint: str,
    status_code: int,
    latency_ms: int,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    api_key_id: Optional[int] = None,
) -> None:
    """Minimal request logger for Sprint 1."""
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


# ---------- Routes ----------
@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.post("/chat")
def chat():
    start = time.perf_counter()

    # Sprint 1 placeholder response
    response = {"reply": "Mock response from gateway"}

    latency_ms = int((time.perf_counter() - start) * 1000)
    log_request(endpoint="/chat", status_code=200, latency_ms=latency_ms)

    return response