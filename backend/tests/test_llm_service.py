import unittest
from unittest.mock import MagicMock, patch

from langchain_core.messages import AIMessage, HumanMessage

from app.services.llm_service import LLMService


def configure_llm_settings(mock_settings: MagicMock, *, model: str = "test-model", thinking_mode: str | None = None) -> None:
    # 测试只关心 LLM 客户端构造，显式列出配置能避免 MagicMock 默认值污染分支判断。
    mock_settings.llm_provider = "openai"
    mock_settings.llm_api_key = "test-key"
    mock_settings.llm_model = model
    mock_settings.llm_temperature = 0.1
    mock_settings.llm_thinking_mode = thinking_mode
    mock_settings.llm_timeout_seconds = 20.0
    mock_settings.llm_max_retries = 1
    mock_settings.llm_base_url = None
    mock_settings.memory_summary_model = None
    mock_settings.memory_summary_temperature = 0.2
    mock_settings.memory_summary_timeout_seconds = 10.0
    mock_settings.memory_summary_max_retries = 1


class LLMServiceTests(unittest.TestCase):
    def test_invoke_without_tools_does_not_bind_tools(self):
        # 空 tools 场景应走纯文本调用，不能触发 bind_tools([])。
        mock_client = MagicMock()
        mock_client.invoke.return_value = AIMessage(content="summary")

        with patch("app.services.llm_service.ChatOpenAI", return_value=mock_client):
            with patch("app.services.llm_service.settings") as mock_settings:
                configure_llm_settings(mock_settings)

                service = LLMService()
                result = service.invoke_with_tools(
                    messages=[HumanMessage(content="请总结")],
                    tools=[],
                    system_prompt="你是总结助手",
                )

        self.assertEqual("summary", result.content)
        mock_client.bind_tools.assert_not_called()
        mock_client.invoke.assert_called_once()

    def test_invoke_with_tools_binds_tools(self):
        # 存在 tools 时仍应走官方 tool-calling。
        mock_client = MagicMock()
        runnable = MagicMock()
        runnable.invoke.return_value = AIMessage(content="", tool_calls=[{"name": "roll_dice", "args": {"formula": "1d20"}, "id": "c1"}])
        mock_client.bind_tools.return_value = runnable

        with patch("app.services.llm_service.ChatOpenAI", return_value=mock_client):
            with patch("app.services.llm_service.settings") as mock_settings:
                configure_llm_settings(mock_settings)

                service = LLMService()
                result = service.invoke_with_tools(
                    messages=[HumanMessage(content="投骰")],
                    tools=[{"type": "function", "function": {"name": "roll_dice"}}],
                    system_prompt="你是 TRPG 助手",
                )

        self.assertEqual(1, len(result.tool_calls))
        mock_client.bind_tools.assert_called_once_with(
            [{"type": "function", "function": {"name": "roll_dice"}}],
            parallel_tool_calls=False,
        )
        runnable.invoke.assert_called_once()

    def test_invoke_summary_uses_summary_client(self):
        mock_main_client = MagicMock()
        mock_summary_client = MagicMock()
        mock_summary_client.invoke.return_value = AIMessage(content="洞穴入口暴露，英雄决定立刻撤离。")

        with patch("app.services.llm_service.ChatOpenAI", side_effect=[mock_main_client, mock_summary_client]):
            with patch("app.services.llm_service.settings") as mock_settings:
                configure_llm_settings(mock_settings)
                mock_settings.memory_summary_model = "summary-model"

                service = LLMService()
                result = service.invoke_summary(
                    '{"assistant_reply": "你们听见洞穴深处的怒吼。"}',
                    system_prompt="压缩近期情节记忆",
                )

        self.assertEqual("洞穴入口暴露，英雄决定立刻撤离。", result)
        mock_summary_client.invoke.assert_called_once()
        mock_main_client.invoke.assert_not_called()

    def test_deepseek_v4_defaults_to_disabled_thinking(self):
        # DeepSeek V4 thinking 模式的工具链路要求回传 reasoning_content，默认关闭以保持 LangChain 消息流稳定。
        with patch("app.services.llm_service.ChatOpenAI") as mock_chat_openai:
            with patch("app.services.llm_service.settings") as mock_settings:
                configure_llm_settings(mock_settings, model="ds v4pro")

                LLMService()

        self.assertEqual(
            {"thinking": {"type": "disabled"}},
            mock_chat_openai.call_args_list[0].kwargs["extra_body"],
        )

    def test_explicit_enabled_keeps_provider_default_thinking(self):
        with patch("app.services.llm_service.ChatOpenAI") as mock_chat_openai:
            with patch("app.services.llm_service.settings") as mock_settings:
                configure_llm_settings(mock_settings, model="deepseek-v4-pro", thinking_mode="enabled")

                LLMService()

        self.assertNotIn("extra_body", mock_chat_openai.call_args_list[0].kwargs)

    def test_any_model_defaults_to_disabled_thinking(self):
        with patch("app.services.llm_service.ChatOpenAI") as mock_chat_openai:
            with patch("app.services.llm_service.settings") as mock_settings:
                configure_llm_settings(mock_settings, model="gpt-4.1")

                LLMService()

        self.assertEqual(
            {"thinking": {"type": "disabled"}},
            mock_chat_openai.call_args_list[0].kwargs["extra_body"],
        )

    def test_explicit_disabled_applies_to_any_model(self):
        with patch("app.services.llm_service.ChatOpenAI") as mock_chat_openai:
            with patch("app.services.llm_service.settings") as mock_settings:
                configure_llm_settings(mock_settings, model="custom-thinking-model", thinking_mode="disabled")

                LLMService()

        self.assertEqual(
            {"thinking": {"type": "disabled"}},
            mock_chat_openai.call_args_list[0].kwargs["extra_body"],
        )


if __name__ == "__main__":
    unittest.main()
