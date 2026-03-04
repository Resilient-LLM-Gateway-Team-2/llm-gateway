"""
API-key authentication middleware.

Intercepts the X-API-Key header, validates it against the api_keys table,
and returns the owning ApiKey row for downstream use.
"""

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.main import ApiKey, SessionLocal


def get_db():
    """Yield a SQLAlchemy session and ensure it is closed after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def verify_api_key(
    x_api_key: str = Header(None, alias="X-API-Key"),
    db: Session = Depends(get_db),
) -> ApiKey:
    """
    Validate the X-API-Key header.

    Raises:
        HTTPException 403 if the key is missing or not found in the database.
    """
    if x_api_key is None:
        raise HTTPException(status_code=403, detail="Missing X-API-Key header")

    api_key = db.query(ApiKey).filter(ApiKey.key == x_api_key).first()
    if api_key is None:
        raise HTTPException(status_code=403, detail="Invalid API key")

    return api_key
