"""Authentication and user management module."""
import hashlib
import secrets
from app.models import User
from datetime import datetime, timedelta

def get_db_session():
    from app.main import SessionLocal
    return SessionLocal()

def get_user_by_username(username: str):
    db = get_db_session()
    try:
        return db.query(User).filter(User.username == username).first()
    finally:
        db.close()

def verify_password(plain_password: str, stored_hash: str) -> bool:
    return hashlib.sha256(plain_password.encode()).hexdigest() == stored_hash

def authenticate_user(username: str, password: str):
    user = get_user_by_username(username)
    if not user or not verify_password(password, user.password_hash):
        return None
    return user

_sessions = {}  # {session_token: {"user_id": ..., "username": ..., "role": ..., "plan": ..., "expires": ...}}

def create_session(user):
    token = secrets.token_urlsafe(32)
    _sessions[token] = {
        "user_id": user.id,
        "username": user.username,
        "role": user.role,
        "plan": user.plan,
        "expires": datetime.now() + timedelta(days=7),
    }
    return token


def verify_session_token(token: str):
    if not token or token not in _sessions:
        return None

    session = _sessions[token]
    if session["expires"] < datetime.now():
        del _sessions[token]
        return None

    return session


def logout_session(token: str):
    if token in _sessions:
        del _sessions[token]

def create_user(email: str, username: str, password: str, role: str = "user", plan: str = "basic"):
    """Create a new user if username/email not already taken."""
    db = get_db_session()
    try:
        existing_user = db.query(User).filter(
            (User.username == username) | (User.email == email)
        ).first()
        if existing_user:
            return None

        password_hash = hashlib.sha256(password.encode()).hexdigest()

        user = User(
            email=email,
            username=username,
            password_hash=password_hash,
            role=role,
            plan=plan,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user
    finally:
        db.close()

def get_user_by_id(user_id: int):
    db = get_db_session()
    try:
        return db.query(User).filter(User.id == user_id).first()
    finally:
        db.close()