#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx", "rich"]
# ///
"""SearXNG CLI - 使用本地或自托管实例执行简单检索。"""

import argparse
import json
import os
import sys
import warnings
from urllib.parse import urlparse

import httpx
from rich import print as rprint
from rich.console import Console
from rich.table import Table

# 对关闭 TLS 校验的场景隐藏无证书告警。
warnings.filterwarnings("ignore", message="Unverified HTTPS request")

console = Console()
DEFAULT_SEARXNG_URL = "http://127.0.0.1:27080"
VERIFY_SSL_ENV_NAME = "SEARXNG_VERIFY_SSL"
SEARXNG_URL_ENV_NAMES = ("SEARXNG_URL", "SEARXNG_BASE_URL")


def _resolve_searxng_url() -> str:
    """解析 SearXNG 地址，优先兼容仓库现有环境变量。"""
    for env_name in SEARXNG_URL_ENV_NAMES:
        value = os.getenv(env_name, "").strip()
        if value:
            return value.rstrip("/")
    return DEFAULT_SEARXNG_URL


def _env_flag(name: str) -> bool | None:
    """将环境变量解析为布尔值，未设置时返回 None。"""
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return None
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return None


def _should_verify_ssl(searxng_url: str) -> bool:
    """决定是否校验证书。"""
    explicit_value = _env_flag(VERIFY_SSL_ENV_NAME)
    if explicit_value is not None:
        return explicit_value

    parsed = urlparse(searxng_url)
    hostname = (parsed.hostname or "").lower()
    local_hostnames = {"localhost", "127.0.0.1", "::1", "searxng"}

    if parsed.scheme == "https" and hostname not in local_hostnames:
        return True
    return False

def search_searxng(
    query: str,
    limit: int = 10,
    category: str = "general",
    language: str = "auto",
    time_range: str = None,
) -> dict:
    """调用 SearXNG JSON API 并返回结果。"""
    searxng_url = _resolve_searxng_url()
    verify_ssl = _should_verify_ssl(searxng_url)
    params = {
        "q": query,
        "format": "json",
        "categories": category,
    }
    
    if language != "auto":
        params["language"] = language
    
    if time_range:
        params["time_range"] = time_range
    
    try:
        response = httpx.get(
            f"{searxng_url}/search",
            params=params,
            timeout=30,
            verify=verify_ssl,
        )
        response.raise_for_status()
        
        data = response.json()
        
        # Limit results
        if "results" in data:
            data["results"] = data["results"][:limit]
        
        return data
        
    except httpx.HTTPError as e:
        console.print(f"[red]连接 SearXNG 失败：[/red]{e}", file=sys.stderr)
        return {"error": str(e), "results": []}
    except Exception as e:
        console.print(f"[red]发生异常：[/red]{e}", file=sys.stderr)
        return {"error": str(e), "results": []}


def display_results_table(data: dict, query: str):
    """以表格形式展示检索结果。"""
    results = data.get("results", [])
    
    if not results:
        rprint(f"[yellow]未找到结果：[/yellow]{query}")
        return
    
    table = Table(title=f"SearXNG 搜索：{query}", show_lines=False)
    table.add_column("#", style="dim", width=3)
    table.add_column("Title", style="bold")
    table.add_column("URL", style="blue", width=50)
    table.add_column("Engines", style="green", width=20)
    
    for i, result in enumerate(results, 1):
        title = result.get("title", "No title")[:70]
        url = result.get("url", "")[:45] + "..."
        engines = ", ".join(result.get("engines", []))[:18]
        
        table.add_row(
            str(i),
            title,
            url,
            engines
        )
    
    console.print(table)
    
    # Show additional info
    if data.get("number_of_results"):
        rprint(f"\n[dim]可用结果总数：{data['number_of_results']}[/dim]")
    
    # 展示前三条摘要，便于快速判断质量。
    rprint("\n[bold]前三条结果：[/bold]")
    for i, result in enumerate(results[:3], 1):
        title = result.get("title", "No title")
        url = result.get("url", "")
        content = result.get("content", "")[:200]
        
        rprint(f"\n[bold cyan]{i}. {title}[/bold cyan]")
        rprint(f"   [blue]{url}[/blue]")
        if content:
            rprint(f"   [dim]{content}...[/dim]")


def display_results_json(data: dict):
    """以 JSON 形式输出结果，便于程序消费。"""
    print(json.dumps(data, indent=2))


def main():
    parser = argparse.ArgumentParser(
        description="SearXNG CLI - 通过本地或自托管实例执行简单搜索",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
示例:
  %(prog)s search "python asyncio"
  %(prog)s search "climate change" -n 20
  %(prog)s search "cute cats" --category images
  %(prog)s search "breaking news" --category news --time-range day
  %(prog)s search "rust tutorial" --format json

环境变量:
  SEARXNG_URL / SEARXNG_BASE_URL: SearXNG 实例地址（默认: {DEFAULT_SEARXNG_URL})
  {VERIFY_SSL_ENV_NAME}: 是否校验证书，支持 true/false
        """
    )
    
    subparsers = parser.add_subparsers(dest="command", help="命令")
    
    # 搜索命令
    search_parser = subparsers.add_parser("search", help="执行网页搜索")
    search_parser.add_argument("query", nargs="+", help="搜索词")
    search_parser.add_argument(
        "-n", "--limit",
        type=int,
        default=10,
        help="结果数量（默认：10）"
    )
    search_parser.add_argument(
        "-c", "--category",
        default="general",
        choices=["general", "images", "videos", "news", "map", "music", "files", "it", "science"],
        help="搜索分类（默认：general）"
    )
    search_parser.add_argument(
        "-l", "--language",
        default="auto",
        help="语言代码（auto、en、zh 等）"
    )
    search_parser.add_argument(
        "-t", "--time-range",
        choices=["day", "week", "month", "year"],
        help="时间范围过滤"
    )
    search_parser.add_argument(
        "-f", "--format",
        choices=["table", "json"],
        default="table",
        help="输出格式（默认：table）"
    )
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    if args.command == "search":
        query = " ".join(args.query)
        
        data = search_searxng(
            query=query,
            limit=args.limit,
            category=args.category,
            language=args.language,
            time_range=args.time_range,
        )
        
        if args.format == "json":
            display_results_json(data)
        else:
            display_results_table(data, query)


if __name__ == "__main__":
    main()
