import os
import uuid
import hashlib
import secrets
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

admin_router = APIRouter(prefix="/admin", tags=["admin"])

# --- Security & Password Utils ---
def hash_password(password: str) -> str:
    salt = os.urandom(16)
    key = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100000)
    return f"{salt.hex()}:{key.hex()}"

def verify_password(password: str, hashed: str) -> bool:
    try:
        salt_hex, key_hex = hashed.split(":")
        salt = bytes.fromhex(salt_hex)
        key = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100000)
        return secrets.compare_digest(key.hex(), key_hex)
    except Exception:
        return False

# --- Admin Auth Dependency ---
def get_current_admin(request: Request):
    """Dependency that reads the session_token cookie and verifies it against Redis."""
    from app.cache import get_cached_response
    token = request.cookies.get("session_token")
    if not token:
        raise HTTPException(status_code=401, detail="Not logged in")
    user_data = get_cached_response(f"admin_session:{token}")
    if not user_data:
        raise HTTPException(status_code=401, detail="Session expired")
    return user_data.get("user_id")

# --- Schemas ---
class AdminAuthReq(BaseModel):
    email: str
    password: str

class ApiKeyCreate(BaseModel):
    owner: str

class ApiKeyResponse(BaseModel):
    id: int
    key: str
    owner: str
    created_at: str
    class Config:
        orm_mode = True

# --- HTML Page ---
@admin_router.get("", response_class=HTMLResponse)
def get_admin_page():
    # Return the HTML. The JS inside will handle the 401s and show the Auth View if needed.
    file_path = os.path.join(os.path.dirname(__file__), "admin.html")
    with open(file_path, "r") as f:
        return f.read()

# --- Auth Endpoints ---
@admin_router.post("/signup")
def signup(req: AdminAuthReq, response: Response):
    from app.main import AdminUser, SessionLocal
    from app.cache import set_cached_response
    db = SessionLocal()
    try:
        # Check if email exists
        if db.query(AdminUser).filter(AdminUser.email == req.email).first():
            raise HTTPException(status_code=400, detail="Email already registered")
        
        # Create User
        new_user = AdminUser(email=req.email, password_hash=hash_password(req.password))
        db.add(new_user)
        db.commit()
        db.refresh(new_user)
        
        # Auto-login
        token = secrets.token_hex(32)
        set_cached_response(f"admin_session:{token}", {"user_id": new_user.id}, ttl=86400)
        response.set_cookie(key="session_token", value=token, httponly=True, samesite="lax")
        return {"status": "success", "message": "Signed up and logged in"}
    finally:
        db.close()

@admin_router.post("/login")
def login(req: AdminAuthReq, response: Response):
    from app.main import AdminUser, SessionLocal
    from app.cache import set_cached_response
    db = SessionLocal()
    try:
        user = db.query(AdminUser).filter(AdminUser.email == req.email).first()
        if not user or not verify_password(req.password, user.password_hash):
            raise HTTPException(status_code=401, detail="Invalid email or password")
        
        token = secrets.token_hex(32)
        set_cached_response(f"admin_session:{token}", {"user_id": user.id}, ttl=86400)
        response.set_cookie(key="session_token", value=token, httponly=True, samesite="lax")
        return {"status": "success", "message": "Logged in"}
    finally:
        db.close()

@admin_router.post("/logout")
def logout(request: Request, response: Response):
    from app.cache import redis_client # We directly invalidate with redis client
    token = request.cookies.get("session_token")
    if token:
        try:
            redis_client.delete(f"admin_session:{token}")
        except Exception:
            pass
    response.delete_cookie("session_token")
    return {"status": "success"}

# --- Protected Endpoints ---
@admin_router.get("/api-keys")
def list_api_keys(admin_id: int = Depends(get_current_admin)):
    from app.main import ApiKey, SessionLocal
    db = SessionLocal()
    try:
        keys = db.query(ApiKey).order_by(ApiKey.created_at.desc()).all()
        return [{"id": k.id, "key": k.key, "owner": k.owner, "created_at": k.created_at.isoformat()} for k in keys]
    finally:
        db.close()

@admin_router.post("/api-keys")
def create_api_key(req: ApiKeyCreate, admin_id: int = Depends(get_current_admin)):
    from app.main import ApiKey, SessionLocal
    db = SessionLocal()
    try:
        new_key = str(uuid.uuid4())
        db_key = ApiKey(key=new_key, owner=req.owner)
        db.add(db_key)
        db.commit()
        db.refresh(db_key)
        return {"id": db_key.id, "key": db_key.key, "owner": db_key.owner, "created_at": db_key.created_at.isoformat()}
    finally:
        db.close()

@admin_router.delete("/api-keys/{key_id}")
def delete_api_key(key_id: int, admin_id: int = Depends(get_current_admin)):
    from app.main import ApiKey, SessionLocal
    db = SessionLocal()
    try:
        db_key = db.query(ApiKey).filter(ApiKey.id == key_id).first()
        if not db_key:
            raise HTTPException(status_code=404, detail="API Key not found")
        db.delete(db_key)
        db.commit()
        return {"status": "ok", "deleted_id": key_id}
    finally:
        db.close()

@admin_router.get("/logs")
def list_logs(limit: int = 50, admin_id: int = Depends(get_current_admin)):
    from app.main import RequestLog, SessionLocal
    db = SessionLocal()
    try:
        logs = db.query(RequestLog).order_by(RequestLog.created_at.desc()).limit(limit).all()
        return [{
            "id": l.id,
            "api_key_id": l.api_key_id,
            "endpoint": l.endpoint,
            "provider": l.provider,
            "model": l.model,
            "status_code": l.status_code,
            "latency_ms": l.latency_ms,
            "created_at": l.created_at.isoformat() if l.created_at else None
        } for l in logs]
    finally:
        db.close()
