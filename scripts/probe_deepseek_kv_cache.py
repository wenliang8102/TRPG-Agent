"""用稳定长前缀探测 DeepSeek/OpenAI-compatible 语境缓存是否间歇 miss。"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# 中文注释：settings 会读取 backend/.env；脚本从仓库根目录运行也应使用同一份配置。
os.chdir(BACKEND_DIR)

from openai import OpenAI  # noqa: E402

from app.config.settings import settings  # noqa: E402


# 中文注释：构造足够长且完全稳定的系统前缀，尾部只变化一个很短的问题。
def build_stable_prefix(repeat: int) -> str:
    block = (
        "你是语境缓存探针。以下是稳定前缀材料，只用于测试缓存，不要复述。\n"
        "凡戴尔道路、松林、补给车、地精脚印、友方战士、法师护甲、魔法飞弹、战斗归档、"
        "剧情线索、NPC 承诺、未解决风险、地图坐标、工具返回优先级。\n"
        "请始终只根据最后一条用户消息回答一个短词。\n"
    )
    return block * repeat


def read_cache_usage(response: Any) -> tuple[int, int]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return 0, 0

    hit = getattr(usage, "prompt_cache_hit_tokens", None)
    miss = getattr(usage, "prompt_cache_miss_tokens", None)
    if hit is not None or miss is not None:
        return int(hit or 0), int(miss or 0)

    if hasattr(usage, "model_dump"):
        data = usage.model_dump()
    elif isinstance(usage, dict):
        data = usage
    else:
        data = {}
    return int(data.get("prompt_cache_hit_tokens") or 0), int(data.get("prompt_cache_miss_tokens") or 0)


def make_client() -> OpenAI:
    client_kwargs: dict[str, str] = {"api_key": settings.llm_api_key.strip()}
    if settings.llm_base_url and settings.llm_base_url.strip():
        client_kwargs["base_url"] = settings.llm_base_url.strip()
    return OpenAI(**client_kwargs)


def invoke_probe(
    client: OpenAI,
    *,
    model: str,
    stable_prefix: str,
    index: int,
    temperature: float,
    max_tokens: int,
) -> dict[str, Any]:
    started = time.perf_counter()
    response = client.chat.completions.create(
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": stable_prefix},
            {"role": "user", "content": f"探针轮次 {index}：只回复 OK"},
        ],
        extra_body={"thinking": {"type": "disabled"}},
    )
    elapsed = time.perf_counter() - started
    hit, miss = read_cache_usage(response)
    total = hit + miss
    choice = response.choices[0].message.content if response.choices else ""
    return {
        "index": index,
        "hit": hit,
        "miss": miss,
        "total": total,
        "hit_ratio": hit / total if total else None,
        "elapsed_seconds": elapsed,
        "reply": choice,
        "model": getattr(response, "model", model),
        "system_fingerprint": getattr(response, "system_fingerprint", None),
    }


def format_ratio(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2%}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe provider-side KV cache with a stable prefix.")
    parser.add_argument("--rounds", type=int, default=12, help="探测轮数。")
    parser.add_argument("--prefix-repeat", type=int, default=180, help="稳定前缀重复块数量。")
    parser.add_argument("--sleep", type=float, default=1.0, help="每轮之间等待秒数，给服务端写入缓存。")
    parser.add_argument("--model", default=settings.llm_model, help="覆盖模型名；默认使用项目 LLM 配置。")
    parser.add_argument("--temperature", type=float, default=0.0, help="探针温度。")
    parser.add_argument("--max-tokens", type=int, default=8, help="最大输出 token。")
    parser.add_argument("--jsonl", type=Path, default=None, help="可选：把逐轮结果写入 JSONL。")
    args = parser.parse_args()

    if not settings.llm_api_key.strip():
        raise RuntimeError("Missing LLM API key. Set TRPG_LLM_API_KEY or OPENAI_API_KEY.")

    stable_prefix = build_stable_prefix(args.prefix_repeat)
    client = make_client()
    rows: list[dict[str, Any]] = []

    print("DeepSeek KV cache probe")
    print(f"  model: {args.model}")
    print(f"  base_url: {settings.llm_base_url or 'default OpenAI endpoint'}")
    print(f"  stable_prefix_chars: {len(stable_prefix)}")
    print(f"  rounds: {args.rounds}")
    print()
    print("| # | hit | miss | hit_ratio | seconds | fingerprint | reply |")
    print("|---:|---:|---:|---:|---:|---|---|")

    output_file = None
    if args.jsonl is not None:
        args.jsonl.parent.mkdir(parents=True, exist_ok=True)
        output_file = args.jsonl.open("w", encoding="utf-8")

    try:
        for index in range(1, args.rounds + 1):
            row = invoke_probe(
                client,
                model=args.model,
                stable_prefix=stable_prefix,
                index=index,
                temperature=args.temperature,
                max_tokens=args.max_tokens,
            )
            rows.append(row)
            if output_file is not None:
                output_file.write(json.dumps(row, ensure_ascii=False) + "\n")
                output_file.flush()
            print(
                f"| {row['index']} | {row['hit']} | {row['miss']} | {format_ratio(row['hit_ratio'])} "
                f"| {row['elapsed_seconds']:.2f} | {row['system_fingerprint'] or ''} | {str(row['reply']).strip()} |",
                flush=True,
            )
            if index < args.rounds and args.sleep > 0:
                time.sleep(args.sleep)
    finally:
        if output_file is not None:
            output_file.close()

    ratios = [row["hit_ratio"] for row in rows if row["hit_ratio"] is not None]
    totals = [row["total"] for row in rows]
    print()
    print("Summary")
    if ratios:
        print(f"  median_hit_ratio: {statistics.median(ratios):.2%}")
        print(f"  weighted_hit_ratio: {sum(row['hit'] for row in rows) / sum(totals):.2%}")
        print(f"  low_hit_under_10pct: {sum(1 for value in ratios if value < 0.1)} / {len(ratios)}")
        print(f"  high_hit_over_90pct: {sum(1 for value in ratios if value >= 0.9)} / {len(ratios)}")
    else:
        print("  no prompt_cache_* usage fields returned")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
