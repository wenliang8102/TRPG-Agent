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
        reaction_response: dict | None = None,
    ) -> dict:
        self.calls.append(
            {
                "message": message,
                "session_id": session_id,
                "resume_action": resume_action,
                "reaction_response": reaction_response,
            }
        )
        response_text = f"echo:{message}" if message else f"resumed:{reaction_response or resume_action}"
        return {
            "reply": response_text,
            "plan": None,
            "session_id": session_id or "generated-session",
            "pending_action": None,
        }

    async def get_history(self, session_id: str, limit: int = 10) -> dict:
        self.calls.append({
            "history_session_id": session_id,
            "history_limit": limit,
        })
        return {
            "messages": [
                {"role": "user", "content": "我攻击哥布林"},
                {"role": "assistant", "content": "哥布林被你逼退了半步。"},
            ],
            "player": {"name": "英雄"},
            "combat": {"round": 2},
        }

    async def delete_session(self, session_id: str) -> dict:
        self.calls.append({"deleted_session_id": session_id})
        return {"deletedRows": 3, "deletedTraceFiles": 1}


class _RuntimeFailSessionService:
    async def process_turn(
        self,
        message: str | None = None,
        session_id: str | None = None,
        resume_action: str | None = None,
        reaction_response: dict | None = None,
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
        self.assertEqual("resumed:confirmed", data["reply"])
        self.assertEqual("confirmed", fake.calls[0]["resume_action"])

    def test_chat_endpoint_accepts_reaction_response(self):
        fake = _FakeSessionService()
        with patch("app.api.chat.CHAT_SESSION_SERVICE", fake):
            client = TestClient(app)
            resp = client.post(
                "/api/chat",
                json={
                    "session_id": "demo-3",
                    "reaction_response": {"spell_id": "shield", "slot_level": 1},
                },
            )

        self.assertEqual(200, resp.status_code)
        data = resp.json()
        self.assertEqual("demo-3", data["session_id"])
        self.assertEqual("resumed:{'spell_id': 'shield', 'slot_level': 1}", data["reply"])
        self.assertEqual({"spell_id": "shield", "slot_level": 1}, fake.calls[0]["reaction_response"])

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

    def test_chat_history_endpoint_returns_original_transcript_payload(self):
        fake = _FakeSessionService()
        with patch("app.api.chat.CHAT_SESSION_SERVICE", fake):
            client = TestClient(app)
            resp = client.get("/api/chat/history", params={"session_id": "demo-history", "limit": 5})

        self.assertEqual(200, resp.status_code)
        data = resp.json()
        self.assertEqual("我攻击哥布林", data["messages"][0]["content"])
        self.assertEqual("哥布林被你逼退了半步。", data["messages"][1]["content"])
        self.assertEqual("demo-history", fake.calls[-1]["history_session_id"])
        self.assertEqual(5, fake.calls[-1]["history_limit"])

    def test_session_endpoints_create_list_and_delete_sessions(self):
        fake = _FakeSessionService()
        with patch("app.api.sessions.delete_chat_session", side_effect=fake.delete_session):
            with patch("app.api.sessions.create_chat_session", return_value={"id": "new-session", "title": "新的冒险", "preview": "", "messageCount": 0, "createdAt": 1, "lastMessageAt": 1}):
                with patch("app.api.sessions.list_chat_sessions", return_value=[{"id": "new-session", "title": "新的冒险", "preview": "", "messageCount": 0, "createdAt": 1, "lastMessageAt": 1}]):
                    client = TestClient(app)
                    create_resp = client.post("/api/sessions", json={})
                    list_resp = client.get("/api/sessions")
                    delete_resp = client.delete("/api/sessions/new-session")

        self.assertEqual(201, create_resp.status_code)
        self.assertEqual("new-session", create_resp.json()["id"])
        self.assertEqual(["new-session"], [item["id"] for item in list_resp.json()["sessions"]])
        self.assertEqual({"session_id": "new-session", "deletedRows": 3, "deletedTraceFiles": 1}, delete_resp.json())
        self.assertEqual("new-session", fake.calls[-1]["deleted_session_id"])


if __name__ == "__main__":
    unittest.main()
