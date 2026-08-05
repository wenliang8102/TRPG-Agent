import unittest
from unittest.mock import AsyncMock

from pydantic import ValidationError

from app.services.reply_suggestion_service import GeneratedReplySuggestions, ReplySuggestionService


class ReplySuggestionServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_generate_uses_structured_output_schema(self):
        llm_service = AsyncMock()
        llm_service.ainvoke_structured.return_value = GeneratedReplySuggestions(
            suggestions=["我检查门锁。", "我询问守卫。", "我退后观察。"]
        )
        service = ReplySuggestionService(llm_service=llm_service)

        suggestions = await service.generate(
            conversation=[{"role": "assistant", "content": "门后传来脚步声。"}],
            player_identity={"name": "温良", "role_class": "法师", "level": "2"},
        )

        self.assertEqual(["我检查门锁。", "我询问守卫。", "我退后观察。"], suggestions)
        _, schema = llm_service.ainvoke_structured.await_args.args
        self.assertIs(schema, GeneratedReplySuggestions)
        prompt = llm_service.ainvoke_structured.await_args.args[0][1].content
        self.assertIn('"conversation"', prompt)
        self.assertIn('"combat": null', prompt)
        self.assertNotIn("secret_exit", prompt)

    async def test_generate_includes_combat_context(self):
        llm_service = AsyncMock()
        llm_service.ainvoke_structured.return_value = GeneratedReplySuggestions(
            suggestions=["我靠近地精后挥剑。", "我先喝下治疗药水。", "我守住位置观察敌人。"]
        )
        service = ReplySuggestionService(llm_service=llm_service)

        await service.generate(
            conversation=[{"role": "assistant", "content": "轮到你行动。"}],
            player_identity={"name": "英雄"},
            combat_context={"current_actor": {"action_available": True}, "distances_feet": {"goblin_1": 25}},
        )

        prompt = llm_service.ainvoke_structured.await_args.args[0][1].content
        self.assertIn('"distances_feet": {"goblin_1": 25}', prompt)

    async def test_generated_suggestions_must_be_distinct(self):
        with self.assertRaises(ValidationError):
            GeneratedReplySuggestions(suggestions=["继续。", "继续。", "我观察四周。"])


if __name__ == "__main__":
    unittest.main()
