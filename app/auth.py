import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.models import User as DbUser


class User:
    """Authentication manager backed by PostgreSQL."""

    sessions = {}

    @classmethod
    def create_session(cls, username: str) -> str:
        token = secrets.token_urlsafe(32)

        cls.sessions[token] = {
            "username": username,
            "expires": datetime.now() + timedelta(days=7),
            "created_at": datetime.now(),
        }

        return token

    @classmethod
    def verify_session(cls, token: str) -> Optional[str]:
        if token not in cls.sessions:
            return None

        session = cls.sessions[token]

        if session["expires"] < datetime.now():
            del cls.sessions[token]
            return None

        return session["username"]

    @classmethod
    def logout(cls, token: str):
        if token in cls.sessions:
            del cls.sessions[token]

    @classmethod
    def authenticate(cls, db: Session, username: str, password: str) -> bool:
        user = db.query(DbUser).filter(DbUser.username == username).first()

        if not user:
            return False

        if not user.is_active:
            return False

        password_hash = hashlib.sha256(password.encode()).hexdigest()

        if user.password_hash != password_hash:
            return False

        user.last_login_at = datetime.utcnow()
        user.last_active_at = datetime.utcnow()

        db.commit()

        return True

    @classmethod
    def create_user(
        cls,
        db: Session,
        *,
        email: str,
        username: str,
        password: str,
        role: str = "user",
        plan: str = "basic",
    ) -> DbUser:
        existing_user = db.query(DbUser).filter(
            (DbUser.username == username) |
            (DbUser.email == email)
        ).first()

        if existing_user:
            raise ValueError("User already exists")

        password_hash = hashlib.sha256(password.encode()).hexdigest()

        user = DbUser(
            email=email,
            username=username,
            password_hash=password_hash,
            role=role,
            plan=plan,
            preferred_model="gpt-3.5-turbo",
            is_active=True,
            last_login_at=datetime.utcnow(),
            last_active_at=datetime.utcnow(),
        )

        db.add(user)
        db.commit()
        db.refresh(user)

        return user

    @classmethod
    def get_user_by_username(cls, db: Session, username: str):
        return db.query(DbUser).filter(DbUser.username == username).first()

    @classmethod
    def get_user_by_id(cls, db: Session, user_id: int):
        return db.query(DbUser).filter(DbUser.id == user_id).first()

    @classmethod
    def get_all_users(cls, db: Session):
        users = db.query(DbUser).order_by(DbUser.created_at.desc()).all()

        result = []

        for user in users:
            result.append({
                "id": user.id,
                "email": user.email,
                "username": user.username,
                "role": user.role,
                "plan": user.plan,
                "preferred_model": user.preferred_model,
                "total_tokens": user.total_tokens,
                "total_cost_usd": user.total_cost_usd,
                "is_active": user.is_active,
                "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
                "last_active_at": user.last_active_at.isoformat() if user.last_active_at else None,
                "created_at": user.created_at.isoformat() if user.created_at else None,
            })

        return result

    @classmethod
    def update_last_active(cls, db: Session, username: str):
        user = db.query(DbUser).filter(DbUser.username == username).first()

        if not user:
            return

        user.last_active_at = datetime.utcnow()
        db.commit()

    @classmethod
    def deactivate_user(cls, db: Session, user_id: int):
        user = db.query(DbUser).filter(DbUser.id == user_id).first()

        if not user:
            return False

        user.is_active = False
        db.commit()

        return True

    @classmethod
    def activate_user(cls, db: Session, user_id: int):
        user = db.query(DbUser).filter(DbUser.id == user_id).first()

        if not user:
            return False

        user.is_active = True
        db.commit()

        return True