"""LangGraph checkpointer adapter placeholder."""

from typing import Optional


class Checkpointer:
    def save(self, key: str, state: dict) -> None:
        _ = (key, state)

    def load(self, key: str) -> Optional[dict]:
        _ = key
        return None

