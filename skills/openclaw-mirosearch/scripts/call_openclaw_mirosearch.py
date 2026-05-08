#!/usr/bin/env python3
"""调用 OpenClaw-MiroSearch 统一脚本，支持 FastAPI（推荐）和 Gradio 两种 API 模式。"""
import argparse
import json
import os
import sys
import time
from urllib import error, parse, request

DEFAULT_API_MODE = os.getenv("MIRO_API_MODE", "fastapi")
DEFAULT_BASE_URL_FASTAPI = "http://127.0.0.1:8090"
DEFAULT_BASE_URL_GRADIO = "http://127.0.0.1:8080"
DEFAULT_BASE_URL = os.getenv("MIRO_SEARCH_BASE_URL", "")
UNIFIED_API_NAME = "run_research_once"
DEFAULT_SEARCH_RESULT_NUM = int(os.getenv("MIRO_SEARCH_RESULT_NUM", "20"))
DEFAULT_VERIFICATION_MIN_SEARCH_ROUNDS = int(
    os.getenv("MIRO_VERIFICATION_MIN_SEARCH_ROUNDS", "3")
)
DEFAULT_OUTPUT_DETAIL_LEVEL = os.getenv("MIRO_OUTPUT_DETAIL_LEVEL", "balanced")
VALID_MODES = (
    "production-web",
    "verified",
    "research",
    "balanced",
    "quota",
    "thinking",
)
VALID_SEARCH_PROFILES = (
    "searxng-first",
    "serp-first",
    "multi-route",
    "parallel",
    "parallel-trusted",
    "searxng-only",
)
VALID_OUTPUT_DETAIL_LEVELS = ("compact", "balanced", "detailed")
VALID_API_MODES = ("fastapi", "gradio")
DEFAULT_SEARCH_PROFILE = "parallel-trusted"

# 标记 "任务未完全收敛" 的哨兵文本
_INCOMPLETE_MARKER = "Task incomplete - reached maximum turns"
_FORMAT_ERROR_MARKER = "No \\boxed{} content found in the final answer."

# 降级重试顺序（mode → search_profile 回退）
_DEGRADE_STEPS = [
    ("verified", "searxng-first"),
    ("research", "parallel-trusted"),
    ("balanced", "parallel-trusted"),
    ("balanced", "searxng-first"),
    ("quota", "searxng-only"),
]


def _next_degrade_step(current_mode: str, current_profile: str) -> tuple[str, str] | None:
    for index, (mode, profile) in enumerate(_DEGRADE_STEPS):
        if current_mode == mode and current_profile == profile:
            next_index = index + 1
            return (
                _DEGRADE_STEPS[next_index]
                if next_index < len(_DEGRADE_STEPS)
                else None
            )

    for mode, profile in _DEGRADE_STEPS:
        if current_mode == mode or current_profile == profile:
            return (mode, profile)

    return "balanced", "searxng-first"


def _normalize_base_url(base_url: str) -> str:
    return base_url.rstrip("/")


def _http_post_json(url: str, payload: dict, timeout: int) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body)
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"网络错误: {exc}") from exc


def _http_get_text(url: str, timeout: int) -> str:
    req = request.Request(url, method="GET")
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"网络错误: {exc}") from exc


def _parse_sse_events(text: str) -> list[tuple[str, str]]:
    events: list[tuple[str, str]] = []
    event_name = ""
    data_lines: list[str] = []

    for raw_line in text.splitlines():
        line = raw_line.strip("\r")
        if not line:
            if event_name or data_lines:
                events.append((event_name, "\n".join(data_lines)))
            event_name = ""
            data_lines = []
            continue

        if line.startswith("event: "):
            event_name = line[len("event: ") :].strip()
            continue

        if line.startswith("data: "):
            data_lines.append(line[len("data: ") :])

    if event_name or data_lines:
        events.append((event_name, "\n".join(data_lines)))

    return events


def run_research_fastapi(
    base_url: str,
    query: str,
    mode: str,
    search_profile: str,
    search_result_num: int,
    verification_min_search_rounds: int,
    output_detail_level: str,
    timeout: int,
    caller_id: str | None = None,
    bearer_token: str | None = None,
) -> str:
    """通过 FastAPI API Server 提交异步任务、SSE 流式追踪进度，并兜底重试。"""
    base_url = _normalize_base_url(base_url)
    submit_url = f"{base_url}/v1/research"

    payload = {
        "query": query,
        "mode": mode,
        "search_profile": search_profile,
        "search_result_num": search_result_num,
        "verification_min_search_rounds": verification_min_search_rounds,
        "output_detail_level": output_detail_level,
    }
    if caller_id:
        payload["caller_id"] = caller_id

    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"

    def _make_auth_headers():
        return {"Authorization": f"Bearer {bearer_token}"} if bearer_token else {}

    req = request.Request(submit_url, data=data, headers=headers, method="POST")
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            submit_resp = json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc

    task_id = submit_resp.get("task_id")
    if not task_id:
        raise RuntimeError(f"提交任务失败，未返回 task_id: {submit_resp}")

    status = submit_resp.get("status")
    if status == "cached" and submit_resp.get("result"):
        return str(submit_resp["result"])

    print(f"任务已提交: task_id={task_id}, status={status}", file=sys.stderr)

    result = _wait_task_fastapi(base_url, task_id, timeout, _make_auth_headers())

    if result is None:
        raise TimeoutError(f"等待结果超时（{timeout}s），task_id={task_id}")

    if _is_incomplete_result(result):
        print(f"  检测到未完全收敛的结果，尝试降级重试...", file=sys.stderr)
        return _retry_with_degrade(
            base_url, query, mode, search_profile, search_result_num,
            verification_min_search_rounds, output_detail_level, timeout,
            caller_id, bearer_token, first_result=result,
        )

    return result


def _wait_task_fastapi(
    base_url: str,
    task_id: str,
    timeout: int,
    auth_headers: callable,
) -> str | None:
    """通过 SSE 流式等待任务完成，返回结果文本或 None（超时/异常）。"""
    stream_url = f"{base_url}/v1/research/{task_id}/stream"
    poll_url = f"{base_url}/v1/research/{task_id}"

    deadline = time.time() + timeout
    last_heartbeat = ""

    # 先尝试 SSE 流式
    try:
        req = request.Request(stream_url, headers=auth_headers(), method="GET")
        with request.urlopen(req, timeout=min(30, max(5, int(timeout * 0.3)))) as resp:
            chunks: list[bytes] = []
            while time.time() < deadline:
                chunk = resp.read(4096)
                if not chunk:
                    break
                chunks.append(chunk)
                text = b"".join(chunks).decode("utf-8", errors="replace")
                events = _parse_sse_events(text)
                for ev_name, payload in events:
                    if ev_name == "done":
                        done_data = json.loads(payload)
                        done_status = done_data.get("status", "")
                        if done_status == "completed":
                            result = _fetch_poll_result(poll_url, auth_headers())
                            return result
                        elif done_status in ("failed", "cancelled"):
                            return str(done_data)
                        else:
                            return _fetch_poll_result(poll_url, auth_headers())
                    if ev_name == "stage_heartbeat":
                        hb = json.loads(payload) if payload else {}
                        key = f"{hb.get('phase','?')}/{hb.get('turn',0)}+{hb.get('detail','')}"
                        if key != last_heartbeat:
                            print(f"  → {hb.get('phase','')}: {hb.get('detail','')}", file=sys.stderr)
                            last_heartbeat = key
                time.sleep(0.5)
    except Exception as exc:
        print(f"  SSE 流式不可用，回退到轮询模式: {exc}", file=sys.stderr)

    # 回退到轮询
    return _poll_task_result(poll_url, timeout, auth_headers, deadline)


def _poll_task_result(
    poll_url: str,
    timeout: int,
    auth_headers: callable,
    deadline: float,
) -> str | None:
    """轮询任务状态直到完成或超时。"""
    last_stage = ""
    while time.time() < deadline:
        poll_req = request.Request(poll_url, headers=auth_headers(), method="GET")
        try:
            with request.urlopen(poll_req, timeout=30) as resp:
                poll_resp = json.loads(resp.read().decode("utf-8"))
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"轮询失败 HTTP {exc.code}: {detail}") from exc

        current_status = poll_resp.get("status", "")
        if current_status == "completed":
            result = poll_resp.get("result")
            return str(result) if result else None
        elif current_status == "failed":
            result = poll_resp.get("result")
            return str(result) if result else f"任务执行失败: {poll_resp}"
        elif current_status == "cancelled":
            return "任务已被取消"

        meta = poll_resp.get("meta", {})
        stage = meta.get("current_stage", "")
        event_count = poll_resp.get("event_count", 0)
        if stage != last_stage:
            print(f"  状态: {current_status}, 阶段: {stage}, 事件: {event_count}", file=sys.stderr)
            last_stage = stage

        time.sleep(3)

    return None


def _fetch_poll_result(poll_url: str, auth_headers: dict) -> str | None:
    """从轮询端点拉取最新结果。"""
    try:
        req = request.Request(poll_url, headers=auth_headers, method="GET")
        with request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("status") == "completed":
                return str(data.get("result", ""))
    except Exception:
        pass
    return None


def _is_incomplete_result(result: str) -> bool:
    """判断结果是否标记为未完全收敛。"""
    return bool(result) and (_INCOMPLETE_MARKER in result or _FORMAT_ERROR_MARKER in result)


def _retry_with_degrade(
    base_url: str,
    query: str,
    current_mode: str,
    current_profile: str,
    search_result_num: int,
    verification_min_search_rounds: int,
    output_detail_level: str,
    timeout: int,
    caller_id: str | None,
    bearer_token: str | None,
    first_result: str,
) -> str:
    """按降级顺序重试：每次降低 mode 严格度或切换到更宽容的 profile。"""
    degrade_target = _next_degrade_step(current_mode, current_profile)
    if degrade_target is None:
        return first_result
    new_mode, new_profile = degrade_target

    print(
        f"  降级重试: {current_mode}/{current_profile} → {new_mode}/{new_profile}",
        file=sys.stderr,
    )

    try:
        retry_result = run_research_fastapi(
            base_url=base_url,
            query=query,
            mode=new_mode,
            search_profile=new_profile,
            search_result_num=search_result_num,
            verification_min_search_rounds=verification_min_search_rounds,
            output_detail_level=output_detail_level,
            timeout=timeout,
            caller_id=caller_id,
            bearer_token=bearer_token,
        )
        return retry_result
    except Exception as exc:
        print(f"  降级重试也失败: {exc}，返回首次结果", file=sys.stderr)
        return first_result


def run_research_gradio(
    base_url: str,
    query: str,
    mode: str,
    search_profile: str,
    search_result_num: int,
    verification_min_search_rounds: int,
    output_detail_level: str,
    timeout: int,
    caller_id: str | None = None,
) -> str:
    """通过 Gradio API 发起研究并轮询结果（兼容模式）。"""
    base_url = _normalize_base_url(base_url)
    start_url = f"{base_url}/gradio_api/call/{parse.quote(UNIFIED_API_NAME)}"
    start_resp = _http_post_json(
        start_url,
        {
            "data": [
                query,
                mode,
                search_profile,
                search_result_num,
                verification_min_search_rounds,
                output_detail_level,
                None,  # render_mode
                caller_id,
            ]
        },
        timeout=timeout,
    )

    event_id = start_resp.get("event_id")
    if not event_id:
        raise RuntimeError(f"启动调用失败，未返回 event_id: {start_resp}")

    deadline = time.time() + timeout
    poll_url = f"{base_url}/gradio_api/call/{parse.quote(UNIFIED_API_NAME)}/{event_id}"

    while time.time() < deadline:
        sse_text = _http_get_text(poll_url, timeout=max(10, min(60, timeout)))
        events = _parse_sse_events(sse_text)

        for event_name, payload in events:
            if event_name == "complete":
                parsed = json.loads(payload)
                if isinstance(parsed, list) and parsed:
                    return str(parsed[0])
                raise RuntimeError(f"complete 事件格式异常: {payload}")

            if event_name == "error":
                raise RuntimeError(f"服务返回 error 事件: {payload}")

        time.sleep(1)

    raise TimeoutError("等待结果超时")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="调用 OpenClaw-MiroSearch API 并输出最终 Markdown（支持 FastAPI 和 Gradio 两种模式）"
    )
    parser.add_argument(
        "--api-mode",
        default=DEFAULT_API_MODE,
        choices=VALID_API_MODES,
        help="API 模式：fastapi（推荐）或 gradio（兼容）",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help="服务基础地址（留空则根据 api-mode 自动选择默认值）",
    )
    parser.add_argument("--query", required=True, help="研究问题")
    parser.add_argument("--mode", default="balanced", choices=VALID_MODES, help="检索模式")
    parser.add_argument(
        "--search-profile",
        default=DEFAULT_SEARCH_PROFILE,
        choices=VALID_SEARCH_PROFILES,
        help="检索源策略",
    )
    parser.add_argument(
        "--search-result-num",
        type=int,
        default=DEFAULT_SEARCH_RESULT_NUM,
        choices=(10, 20, 30),
        help="单轮检索条数（建议 20 或 30）",
    )
    parser.add_argument(
        "--verification-min-search-rounds",
        type=int,
        default=DEFAULT_VERIFICATION_MIN_SEARCH_ROUNDS,
        help="最少检索轮次（verified 模式生效）",
    )
    parser.add_argument(
        "--output-detail-level",
        default=DEFAULT_OUTPUT_DETAIL_LEVEL,
        choices=VALID_OUTPUT_DETAIL_LEVELS,
        help="输出篇幅档位：compact/balanced/detailed",
    )
    parser.add_argument(
        "--caller-id",
        default=None,
        help="调用方标识，用于定向取消",
    )
    parser.add_argument(
        "--bearer-token",
        default=os.getenv("MIRO_BEARER_TOKEN"),
        help="Bearer Token（仅 fastapi 模式，也可通过 MIRO_BEARER_TOKEN 环境变量设置）",
    )
    parser.add_argument("--timeout", type=int, default=240, help="总超时秒数")
    args = parser.parse_args()

    # 自动推导 base_url
    base_url = args.base_url
    if not base_url:
        base_url = DEFAULT_BASE_URL_FASTAPI if args.api_mode == "fastapi" else DEFAULT_BASE_URL_GRADIO

    try:
        if args.api_mode == "fastapi":
            output = run_research_fastapi(
                base_url=base_url,
                query=args.query,
                mode=args.mode,
                search_profile=args.search_profile,
                search_result_num=args.search_result_num,
                verification_min_search_rounds=args.verification_min_search_rounds,
                output_detail_level=args.output_detail_level,
                timeout=args.timeout,
                caller_id=args.caller_id,
                bearer_token=args.bearer_token,
            )
        else:
            output = run_research_gradio(
                base_url=base_url,
                query=args.query,
                mode=args.mode,
                search_profile=args.search_profile,
                search_result_num=args.search_result_num,
                verification_min_search_rounds=args.verification_min_search_rounds,
                output_detail_level=args.output_detail_level,
                timeout=args.timeout,
                caller_id=args.caller_id,
            )
    except Exception as exc:
        print(f"调用失败: {exc}", file=sys.stderr)
        return 1

    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
