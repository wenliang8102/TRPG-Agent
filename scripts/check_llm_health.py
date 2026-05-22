"""直接检测主 LLM 通路是否可用，避开 Agent、工具、记忆与数据库。"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# 中文注释：项目配置文件位于 backend/.env，脚本从任意目录执行时都应读取同一份配置。
import os

os.chdir(BACKEND_DIR)

from langchain_core.messages import HumanMessage  # noqa: E402

from app.config.settings import settings  # noqa: E402
from app.services.llm_service import LLMMode, LLMService  # noqa: E402


# 中文注释：探针只验证真实 LLM 客户端链路，避免把 Agent 编排问题误判成模型不可用。
def check_llm_health(prompt: str, mode: LLMMode) -> str:
    service = LLMService()
    response = service.invoke_with_tools(
        messages=[HumanMessage(content=prompt)],
        tools=[],
        system_prompt="你是连通性探针。只回复 OK，不要解释。",
        mode=mode,
    )
    return str(response.content).strip()


# 中文注释：只输出非敏感配置，方便排查环境变量是否指向了预期的模型服务。
def print_effective_config() -> None:
    print("LLM effective config:", flush=True)
    print(f"  provider: {settings.llm_provider}", flush=True)
    print(f"  model: {settings.llm_model}", flush=True)
    print(f"  base_url: {settings.llm_base_url or 'default OpenAI endpoint'}", flush=True)
    print(f"  timeout_seconds: {settings.llm_timeout_seconds}", flush=True)
    print(f"  max_retries: {settings.llm_max_retries}", flush=True)
    print(f"  has_api_key: {bool(settings.llm_api_key.strip())}", flush=True)


# 中文注释：退出码用于脚本化判断；0 表示 LLM 有响应，1 表示模型链路失败。
def main() -> int:
    parser = argparse.ArgumentParser(description="Check whether the configured LLM endpoint is reachable.")
    parser.add_argument(
        "--prompt",
        default="健康检查：请只回复 OK",
        help="发送给模型的最小探针内容；默认要求模型只回复 OK。",
    )
    parser.add_argument(
        "--mode",
        choices=["narrative", "combat"],
        default="narrative",
        help="复用项目 LLMService 的 mode 参数；当前默认 narrative。",
    )
    args = parser.parse_args()

    print_effective_config()
    print("Sending probe request...", flush=True)
    started_at = time.perf_counter()

    try:
        reply = check_llm_health(args.prompt, args.mode)
    except Exception as exc:
        elapsed = time.perf_counter() - started_at
        print(f"LLM health check failed after {elapsed:.2f}s.", flush=True)
        print(f"{type(exc).__name__}: {exc}", flush=True)
        return 1

    elapsed = time.perf_counter() - started_at
    print(f"LLM health check succeeded after {elapsed:.2f}s.", flush=True)
    print(f"reply: {reply!r}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
