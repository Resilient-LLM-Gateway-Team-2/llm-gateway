"""Authentication and user management module."""
import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Optional

class User:
    """Simple in-memory user storage for demo."""
    users = {
        "admin": {
            "username": "admin",
            "password_hash": hashlib.sha256(b"admin").hexdigest(),
            "role": "admin",
            "created_at": datetime.now(),
        }
    }
    
    sessions = {}  # {session_token: {username, expires}}

    @classmethod
    def create_session(cls, username: str) -> str:
        """Create a new session token for a user."""
        token = secrets.token_urlsafe(32)
        cls.sessions[token] = {
            "username": username,
            "expires": datetime.now() + timedelta(days=7),
            "created_at": datetime.now(),
        }
        return token

    @classmethod
    def verify_session(cls, token: str) -> Optional[str]:
        """Verify session token and return username if valid."""
        if token not in cls.sessions:
            return None
        session = cls.sessions[token]
        if session["expires"] < datetime.now():
            del cls.sessions[token]
            return None
        return session["username"]

    @classmethod
    def authenticate(cls, username: str, password: str) -> bool:
        """Authenticate user with username and password."""
        if username not in cls.users:
            return False
        password_hash = hashlib.sha256(password.encode()).hexdigest()
        return cls.users[username]["password_hash"] == password_hash

    @classmethod
    def logout(cls, token: str):
        """Log out a user by removing their session."""
        if token in cls.sessions:
            del cls.sessions[token]
