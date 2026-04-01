"""Tool service placeholder."""


class ToolService:
    def call(self, tool_name: str, payload: dict) -> dict:
        return {"tool": tool_name, "payload": payload, "status": "placeholder"}

