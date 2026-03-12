"""
Integration tests for the Resilient LLM Gateway.

All LLM provider calls are mocked — no real API keys needed.
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import ApiKey, SessionLocal, app
from app.schemas import ChatResponse, UsageStats


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_BODY = {
    "model": "gpt-4",
    "messages": [{"role": "user", "content": "Say hello"}],
    "temperature": 0.7,
    "max_tokens": 64,
}

MOCK_RESPONSE = ChatResponse(
    content="Hello! How can I help you?",
    provider="openai",
    model="gpt-4",
    usage=UsageStats(prompt_tokens=10, completion_tokens=8, total_tokens=18),
)

MOCK_GEMINI_RESPONSE = ChatResponse(
    content="Hello from Gemini!",
    provider="gemini",
    model="gemini-2.0-flash",
    usage=UsageStats(prompt_tokens=10, completion_tokens=6, total_tokens=16),
)


def _fake_db_with_key():
    """Return a mock DB session that finds a valid API key."""
    mock_db = MagicMock()
    fake_key = MagicMock(spec=ApiKey)
    fake_key.id = 1
    fake_key.key = "test-key-123"
    fake_key.owner = "tester"
    mock_db.query.return_value.filter.return_value.first.return_value = fake_key
    return mock_db


def _fake_db_no_key():
    """Return a mock DB session that finds NO API key."""
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = None
    return mock_db


# Override the get_db dependency for all tests
def _override_get_db_with_key():
    yield _fake_db_with_key()


def _override_get_db_no_key():
    yield _fake_db_no_key()


# ---------------------------------------------------------------------------
# Auth Tests
# ---------------------------------------------------------------------------

class TestAuth:
    """API-key authentication tests."""

    def test_missing_api_key_returns_403(self):
        """Request without X-API-Key header → 403."""
        from app.main import get_db
        app.dependency_overrides[get_db] = _override_get_db_no_key
        client = TestClient(app)

        r = client.post("/chat", json=VALID_BODY)
        assert r.status_code == 403
        assert "Missing" in r.json()["detail"]

        app.dependency_overrides.clear()

    def test_invalid_api_key_returns_403(self):
        """Request with unknown X-API-Key → 403."""
        from app.main import get_db
        app.dependency_overrides[get_db] = _override_get_db_no_key
        client = TestClient(app)

        r = client.post(
            "/chat",
            json=VALID_BODY,
            headers={"X-API-Key": "bad-key"},
        )
        assert r.status_code == 403
        assert "Invalid" in r.json()["detail"]

        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Validation Tests
# ---------------------------------------------------------------------------

class TestValidation:
    """Pydantic request-body validation tests."""

    def setup_method(self):
        from app.main import get_db
        app.dependency_overrides[get_db] = _override_get_db_with_key

    def teardown_method(self):
        app.dependency_overrides.clear()

    def test_empty_body_returns_422(self):
        client = TestClient(app)
        r = client.post("/chat", json={}, headers={"X-API-Key": "test-key-123"})
        assert r.status_code == 422

    def test_missing_messages_returns_422(self):
        client = TestClient(app)
        r = client.post(
            "/chat",
            json={"model": "gpt-4"},
            headers={"X-API-Key": "test-key-123"},
        )
        assert r.status_code == 422

    def test_invalid_temperature_returns_422(self):
        client = TestClient(app)
        body = {**VALID_BODY, "temperature": 5.0}
        r = client.post("/chat", json=body, headers={"X-API-Key": "test-key-123"})
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# Chat & Fallback Tests
# ---------------------------------------------------------------------------

class TestChat:
    """End-to-end chat tests with mocked providers."""

    def setup_method(self):
        from app.main import get_db
        app.dependency_overrides[get_db] = _override_get_db_with_key

    def teardown_method(self):
        app.dependency_overrides.clear()

    @patch("app.main.log_request")
    @patch("app.router.call_openai", return_value=MOCK_RESPONSE)
    def test_successful_chat_returns_response(self, mock_openai, mock_log):
        client = TestClient(app)
        r = client.post(
            "/chat",
            json=VALID_BODY,
            headers={"X-API-Key": "test-key-123"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["content"] == "Hello! How can I help you?"
        assert data["provider"] == "openai"
        assert data["usage"]["total_tokens"] == 18
        mock_openai.assert_called_once()
        mock_log.assert_called_once()

    @patch("app.main.log_request")
    @patch("app.router.call_gemini", return_value=MOCK_GEMINI_RESPONSE)
    @patch("app.router.call_openai", side_effect=Exception("OpenAI down"))
    def test_fallback_to_gemini_on_openai_failure(self, mock_openai, mock_gemini, mock_log):
        """When OpenAI fails, the router falls back to Gemini."""
        from app.providers import ProviderError

        # Make call_openai raise a ProviderError through the router
        mock_openai.side_effect = ProviderError("openai", "All 3 attempts failed")

        client = TestClient(app)
        r = client.post(
            "/chat",
            json=VALID_BODY,
            headers={"X-API-Key": "test-key-123"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["provider"] == "gemini"
        assert data["content"] == "Hello from Gemini!"

    @patch("app.main.log_request")
    @patch("app.router.call_gemini")
    @patch("app.router.call_openai")
    def test_both_providers_fail_returns_502(self, mock_openai, mock_gemini, mock_log):
        """When both providers fail, return 502."""
        from app.providers import ProviderError

        mock_openai.side_effect = ProviderError("openai", "All 3 attempts failed")
        mock_gemini.side_effect = ProviderError("gemini", "All 3 attempts failed")

        client = TestClient(app)
        r = client.post(
            "/chat",
            json=VALID_BODY,
            headers={"X-API-Key": "test-key-123"},
        )
        assert r.status_code == 502
        assert "All LLM providers failed" in r.json()["detail"]
