"""LangGraph checkpointer adapter placeholder."""


class Checkpointer:
    def save(self, key: str, state: dict) -> None:
        _ = (key, state)

    def load(self, key: str) -> dict | None:
        _ = key
        return None

