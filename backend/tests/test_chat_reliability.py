import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app


class _FlakySessionService:
    """通过可预测的间歇失败模拟高频交互中的不稳定上游。"""

    def __init__(self, fail_every: int = 5):
        self.fail_every = fail_every
        self.counter = 0

    async def process_turn(
        self,
        message: str | None = None,
        session_id: str | None = None,
        resume_action: str | None = None,
    ) -> dict:
        self.counter += 1
        if self.counter % self.fail_every == 0:
            raise RuntimeError("upstream timeout")

        return {
            "reply": f"ok:{message or resume_action}",
            "plan": None,
            "session_id": session_id or "reliability-session",
            "pending_action": None,
        }


class ChatReliabilityTests(unittest.TestCase):
    def test_chat_endpoint_intermittent_failures_map_to_503(self):
        flaky = _FlakySessionService(fail_every=5)

        with patch("app.api.chat.CHAT_SESSION_SERVICE", flaky):
            client = TestClient(app)
            status_codes = []
            for i in range(1, 51):
                resp = client.post(
                    "/api/chat",
                    json={"message": f"ping-{i}", "session_id": "stress-1"},
                )
                status_codes.append(resp.status_code)

        self.assertEqual(50, len(status_codes))
        self.assertEqual(40, status_codes.count(200))
        self.assertEqual(10, status_codes.count(503))
        self.assertEqual(0, status_codes.count(500))


if __name__ == "__main__":
    unittest.main()
