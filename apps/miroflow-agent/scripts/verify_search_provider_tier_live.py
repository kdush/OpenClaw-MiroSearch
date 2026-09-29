#!/usr/bin/env python3
# Copyright (c) 2025 MiroMind
"""M5 分档的真实检索验证（不依赖 LLM，可单独跑）。

完整验收 harness 需要 LLM 凭据；本脚本只打真实检索这一层，用于在 LLM 不可用
（欠费/限流）时仍能验证 M5 的核心不变量：

1. ``searchParameters.provider_tier`` 真的被记录（tier/profile/reason/门槛）；
2. 生效档位的置信门槛在当前配置下**可达**（单 provider 档不得被套上
   多 provider 的 ≥2 要求，否则门控永远过不了、白白耗尽补检轮次）；
3. 真实检索确实返回了结果。

凭据来源：``--credentials`` 指定的 JSON，缺省回退到 gitignored 的
``apps/gradio-demo/.env``。**任何密钥都不会被打印。**

用法::

    python scripts/verify_search_provider_tier_live.py
    python scripts/verify_search_provider_tier_live.py --query "..." --num 8
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
DEFAULT_ENV = REPO / "apps" / "gradio-demo" / ".env"

DEFAULT_QUERY = "What were the global CO2 emissions in 2023?"


def _load_env_file(path: Path) -> list[str]:
    """把 .env 载入 os.environ，返回被设置的键名（绝不返回值）。"""
    loaded: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if value:
            os.environ[key] = value
            loaded.append(key)
    return sorted(loaded)


def _load_credentials(credentials: str) -> list[str]:
    path = Path(credentials)
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        loaded = []
        for key, value in data.items():
            if value not in (None, ""):
                os.environ[str(key)] = str(value)
                loaded.append(str(key))
        return sorted(loaded)
    if not DEFAULT_ENV.exists():
        raise FileNotFoundError(
            f"凭据文件不存在: {path}；也没有 .env 回退（{DEFAULT_ENV}）"
        )
    print(f"[creds] JSON 不存在: {path}；使用 {DEFAULT_ENV} 回退")
    return _load_env_file(DEFAULT_ENV)


async def _run(args: argparse.Namespace) -> int:
    loaded = _load_credentials(args.credentials)
    print(f"[creds] 已加载 {len(loaded)} 个键: {', '.join(loaded)}")

    # 必须在 import 前完成 env 注入：模块级常量在 import 时读取。
    from miroflow_tools.dev_mcp_servers import search_and_scrape_webpage as ssw

    available = ssw._registry.available_names()
    print(f"[env] 可用 provider: {available}")
    print(f"[env] SEARCH_PROVIDER_ORDER = {ssw.SEARCH_PROVIDER_ORDER!r}")
    print(f"[env] SEARCH_PROFILE = {ssw.SEARCH_PROFILE!r}")
    print(f"[env] STRICT = {ssw.SEARCH_PROVIDER_ORDER_STRICT}")
    print(f"[env] 默认门槛 = {ssw.SEARCH_CONFIDENCE_MIN_PROVIDER_COVERAGE}")

    if not available:
        print("[FATAL] 没有可用 provider（缺少搜索凭据）")
        return 2

    print(f"\n[live] 真实检索: {args.query!r} (num={args.num})")
    raw = await ssw.google_search(q=args.query, num=args.num)
    data = json.loads(raw)

    params = data.get("searchParameters") or {}
    tier = params.get("provider_tier")
    confidence = data.get("confidence") or {}
    constraints = confidence.get("constraints") or {}
    metrics = confidence.get("metrics") or {}

    print("\n[live] 结果:")
    print(
        json.dumps(
            {
                "provider": data.get("provider"),
                "provider_order": params.get("provider_order"),
                "provider_tier": tier,
                "confidence": {
                    "score": confidence.get("score"),
                    "passed": confidence.get("passed"),
                    "constraints": constraints,
                    "metrics": metrics,
                },
                "retrieval_quality": data.get("retrieval_quality"),
                "organic_count": len(data.get("organic") or []),
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    failures: list[str] = []
    if not (data.get("organic") or []):
        failures.append("真实检索没有返回任何结果")
    if tier is None:
        failures.append("searchParameters.provider_tier 缺失（M5 未接线）")
    else:
        coverage = tier.get("min_provider_coverage")
        order = tier.get("effective_order") or []
        if not tier.get("tier"):
            failures.append("provider_tier.tier 为空")
        if not tier.get("reason"):
            failures.append("provider_tier.reason 为空（缺少归因）")
        # 核心不变量：门槛必须在本档可达
        if isinstance(coverage, int) and coverage > len(order):
            failures.append(
                f"门槛结构性不可达: min_provider_coverage={coverage} > "
                f"effective_order={order}"
            )
        if constraints.get("min_provider_coverage") != coverage:
            failures.append(
                "confidence 门槛未跟随档位: "
                f"constraints={constraints.get('min_provider_coverage')!r} "
                f"tier={coverage!r}"
            )
        if args.expect_tier and tier.get("tier") != args.expect_tier:
            failures.append(
                f"期望 tier={args.expect_tier!r}，实际 {tier.get('tier')!r}"
            )

    print("\n[verdict]", "FAIL" if failures else "PASS")
    for item in failures:
        print("  -", item)
    return 1 if failures else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="M5 provider tier live check")
    parser.add_argument(
        "--credentials",
        default=str(ROOT / "conf" / "nonexistent_credentials.json"),
        help="凭据 JSON 路径；不存在则回退 apps/gradio-demo/.env",
    )
    parser.add_argument("--query", default=DEFAULT_QUERY)
    parser.add_argument("--num", type=int, default=5)
    parser.add_argument(
        "--expect-tier",
        default="",
        help="可选：断言生效档位（single-provider / multi-provider / no-provider）",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
