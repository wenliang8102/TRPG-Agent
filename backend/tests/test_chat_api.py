import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app


class _FakeSessionService:
    def __init__(self):
        self.calls = []

    async def process_turn(
        self,
        message: str | None = None,
        session_id: str | None = None,
        resume_action: str | None = None,
    ) -> dict:
        self.calls.append(
            {
                "message": message,
                "session_id": session_id,
                "resume_action": resume_action,
            }
        )
        response_text = f"echo:{message}" if message else "resumed"
        return {
            "reply": response_text,
            "plan": None,
            "session_id": session_id or "generated-session",
            "pending_action": None,
        }


class _RuntimeFailSessionService:
    async def process_turn(
        self,
        message: str | None = None,
        session_id: str | None = None,
        resume_action: str | None = None,
    ) -> dict:
        raise RuntimeError("LLM upstream timeout")


class ChatApiTests(unittest.TestCase):
    def test_chat_endpoint_returns_service_result(self):
        fake = _FakeSessionService()
        with patch("app.api.chat.CHAT_SESSION_SERVICE", fake):
            client = TestClient(app)
            resp = client.post(
                "/api/chat",
                json={"message": "你好", "session_id": "demo-1"},
            )

        self.assertEqual(200, resp.status_code)
        data = resp.json()
        self.assertEqual("echo:你好", data["reply"])
        self.assertEqual("demo-1", data["session_id"])
        self.assertIn("plan", data)
        self.assertEqual("你好", fake.calls[0]["message"])
        self.assertEqual("demo-1", fake.calls[0]["session_id"])
        self.assertIsNone(fake.calls[0]["resume_action"])

    def test_chat_endpoint_accepts_missing_session_id(self):
        fake = _FakeSessionService()
        with patch("app.api.chat.CHAT_SESSION_SERVICE", fake):
            client = TestClient(app)
            resp = client.post("/api/chat", json={"message": "hello"})

        self.assertEqual(200, resp.status_code)
        data = resp.json()
        self.assertEqual("generated-session", data["session_id"])

    def test_chat_endpoint_accepts_resume_action(self):
        fake = _FakeSessionService()
        with patch("app.api.chat.CHAT_SESSION_SERVICE", fake):
            client = TestClient(app)
            resp = client.post(
                "/api/chat",
                json={"session_id": "demo-2", "resume_action": "confirmed"},
            )

        self.assertEqual(200, resp.status_code)
        data = resp.json()
        self.assertEqual("demo-2", data["session_id"])
        self.assertEqual("resumed", data["reply"])
        self.assertEqual("confirmed", fake.calls[0]["resume_action"])

    def test_chat_endpoint_returns_structured_error_detail(self):
        with patch("app.api.chat.CHAT_SESSION_SERVICE", _RuntimeFailSessionService()):
            client = TestClient(app)
            resp = client.post("/api/chat", json={"message": "hello"})

        self.assertEqual(503, resp.status_code)
        data = resp.json()
        self.assertIn("detail", data)
        self.assertEqual("upstream_unavailable", data["detail"]["code"])
        self.assertEqual("LLM upstream timeout", data["detail"]["message"])
        self.assertTrue(data["detail"]["request_id"])


if __name__ == "__main__":
    unittest.main()
