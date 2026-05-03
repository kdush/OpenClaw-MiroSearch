# Output Robustness Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 收紧研究任务的成功/失败判定和最终输出格式契约，避免异常、未收敛或缓存回放被误展示为正常完成结果。

**Architecture:** 将“任务运行状态”和“最终答案格式质量”拆开处理：pipeline 返回结构化运行结果，worker 根据结构化状态落库；final answer 解析返回结构化判定，前端只负责展示清洗，不参与成功判定。先补失败测试，再做最小实现，保持现有 API 和 UI 尽量兼容。

**Tech Stack:** Python 3.12+、FastAPI、arq、Gradio 5、pytest、pytest-asyncio、uv。

---

## 执行原则

- 始终使用中文注释和中文提交信息。
- 禁止硬编码新增业务阈值；需要阈值时放到环境变量、settings 或已有配置中。
- 每个任务先写失败测试，再做最小实现，再跑对应测试。
- 不改无关 UI 样式，不重构大模块，不调整搜索策略。
- 未经明确指示不要执行 `git commit`。
- 如需查 Gradio/FastAPI/arq 新用法，先用 Context7 查官方文档。

## 当前问题摘要

1. `apps/miroflow-agent/src/core/pipeline.py` 捕获异常后返回错误字符串，`apps/api-server/workers/research_worker.py` 只要 pipeline 正常返回就写 `completed`，可能造成失败任务显示完成。
2. `apps/miroflow-agent/src/io/output_formatter.py` 在没有 `\boxed{}` 时把全文作为 fallback，`apps/miroflow-agent/src/core/answer_generator.py` 因此会把任意非空总结判为成功。
3. `apps/gradio-demo/main.py` 未处理 `final_output` 事件，缓存命中或 SSE 回放时可能没有最终正文。
4. `apps/api-server/services/task_event_sink.py` 读取的事件字段与实际事件不一致，导致 `current_stage` 不准。

---

### Task 1: 为 pipeline/worker 增加结构化任务结果契约

**Files:**
- Modify: `apps/miroflow-agent/src/core/pipeline.py`
- Modify: `apps/api-server/workers/research_worker.py`
- Test: `apps/api-server/tests/test_research_worker.py`

**目标行为:**
- pipeline 返回包含 `status`、`final_summary`、`final_boxed_answer`、`log_file_path`、`failure_experience_summary`、`error` 的结构化对象。
- worker 不再凭“函数正常返回”判定 completed。
- pipeline 内部异常应让 worker 落库为 `failed`，并追加 `error` 事件。
- 取消仍然落库为 `cancelled`。

**Step 1: 写失败测试**

在 `apps/api-server/tests/test_research_worker.py` 新增测试：

```python
@pytest.mark.asyncio
async def test_run_research_job_marks_pipeline_failed_result_as_failed(
    mock_task_store, mock_pipeline_runtime
):
    payload = _make_payload("test-task-pipeline-failed-result")

    async def failed_pipeline(*_args, **_kwargs):
        return {
            "status": "failed",
            "final_summary": "Error executing task test-task-pipeline-failed-result",
            "final_boxed_answer": "",
            "log_file_path": "/logs/task.log",
            "failure_experience_summary": None,
            "error": "LLM timeout",
        }

    with patch("workers.research_worker.TaskStore.create", return_value=mock_task_store), \
         patch("workers.research_worker.get_pipeline_runtime", return_value=mock_pipeline_runtime), \
         patch("workers.research_worker._execute_pipeline", side_effect=failed_pipeline):
        from workers.research_worker import run_research_job

        result = await run_research_job({}, payload.to_dict())

    assert result["status"] == "failed"
    mock_task_store.update_task_status.assert_any_await(
        "test-task-pipeline-failed-result",
        TaskStatus.FAILED,
        error="LLM timeout",
    )
    mock_task_store.append_event.assert_any_await(
        "test-task-pipeline-failed-result",
        "error",
        {"error": "LLM timeout"},
    )
```

**Step 2: 运行测试确认失败**

Run:

```bash
cd apps/api-server && uv run pytest tests/test_research_worker.py::test_run_research_job_marks_pipeline_failed_result_as_failed -q
```

Expected: FAIL，因为 worker 当前会写 `TaskStatus.COMPLETED`。

**Step 3: 最小实现**

在 `apps/miroflow-agent/src/core/pipeline.py` 中新增轻量 helper，统一返回 dict。不要引入新依赖。

```python
def _build_pipeline_result(
    *,
    status: str,
    final_summary: str,
    final_boxed_answer: str,
    log_file_path: str,
    failure_experience_summary: Optional[str] = None,
    error: Optional[str] = None,
) -> dict:
    return {
        "status": status,
        "final_summary": final_summary,
        "final_boxed_answer": final_boxed_answer,
        "log_file_path": log_file_path,
        "failure_experience_summary": failure_experience_summary,
        "error": error,
    }
```

替换 `execute_task_pipeline()` 的三个 return：

- 成功路径返回 `status="completed"`。
- `asyncio.CancelledError` 路径返回 `status="cancelled"`。
- 通用异常路径返回 `status="failed"`，`error=error_details`。

在 `apps/api-server/workers/research_worker.py` 中修改 `_execute_pipeline()`：兼容 dict 和旧 tuple，返回 dict。worker 读取 `pipeline_result["status"]`：

- `completed`：存储 `final_summary`，写 `COMPLETED`。
- `failed`：写 `FAILED`，追加 `error` 事件，不存成功结果。
- `cancelled`：写 `CANCELLED`，追加 `cancelled` 事件。
- 未知状态：按 failed 处理，错误信息为 `Unknown pipeline status: <status>`。

**Step 4: 运行测试**

Run:

```bash
cd apps/api-server && uv run pytest tests/test_research_worker.py -q
```

Expected: PASS。

**Step 5: 回归检查**

Run:

```bash
cd apps/miroflow-agent && uv run pytest -q
cd apps/api-server && uv run pytest tests/test_research_worker.py tests/test_research_queue_api.py tests/test_sse_stream.py -q
```

Expected: PASS。若 agent 全量测试太慢或依赖外部服务，记录跳过原因，并至少跑相关单测。

---

### Task 2: 拆分最终答案解析和格式有效性判定

**Files:**
- Modify: `apps/miroflow-agent/src/io/output_formatter.py`
- Modify: `apps/miroflow-agent/src/core/answer_generator.py`
- Test: `apps/miroflow-agent/tests/` 下新增或扩展 output formatter/answer generator 测试；若当前测试目录不同，先用 `rg --files apps/miroflow-agent | rg 'test_.*formatter|test_.*answer'` 查找合适位置。

**目标行为:**
- `\boxed{}` 命中时：`format_valid=True`，`extracted_answer` 为 boxed 内容。
- 没有 `\boxed{}` 但有正文时：可以展示 fallback 正文，但 `format_valid=False`。
- answer generator 的“是否重试/是否收敛”基于 `format_valid`，不是基于 fallback 文本是否非空。
- 最后一轮仍可返回可展示正文，但必须带质量标记，不能伪装成严格格式成功。

**Step 1: 写失败测试**

新增测试示例：

```python
def test_format_final_summary_marks_missing_boxed_as_format_invalid():
    formatter = OutputFormatter()

    summary, boxed, usage_log, quality = formatter.format_final_summary_and_log(
        "这是完整正文，但没有 boxed。",
        client=None,
    )

    assert boxed == "这是完整正文，但没有 boxed。"
    assert quality["format_valid"] is False
    assert quality["fallback_used"] is True
    assert "model did not use \\boxed{} format" in summary
```

如果不想破坏现有三元组调用，可先新增 `format_final_summary_payload()`，让旧函数包装新函数。

**Step 2: 运行测试确认失败**

Run:

```bash
cd apps/miroflow-agent && uv run pytest <新增测试文件>::test_format_final_summary_marks_missing_boxed_as_format_invalid -q
```

Expected: FAIL，因为当前函数只返回三元组，没有 `quality`。

**Step 3: 最小实现**

推荐新增 dataclass 或普通 dict。为了最小改动，优先用 dict：

```python
quality = {
    "format_valid": bool(boxed_result_from_boxed),
    "fallback_used": False,
    "issues": [],
}
```

实现要点：

- `_extract_boxed_content()` 只负责提取，不改变。
- `format_final_summary_and_log()` 保持兼容三元组时，新增一个新函数：

```python
def format_final_summary_payload(self, final_answer_text: str, client=None) -> dict:
    ...
    return {
        "summary": summary,
        "boxed_answer": boxed_result,
        "usage_log": log_string,
        "quality": quality,
    }
```

- `answer_generator.generate_final_answer_with_retries()` 改用 payload。
- 当 `quality["format_valid"] is False` 时：
  - 如果还有重试次数，追加格式修复提示并重试。
  - 如果没有重试次数，保留 `final_summary` 用于展示，但 `final_boxed_answer` 不应被日志称为 “Boxed answer found”。

**Step 4: 加 answer generator 行为测试**

测试重点不是 mock 整个 LLM，而是验证“缺 boxed 不记录为格式成功”。如果当前 AnswerGenerator 不便测，先测试一个新 helper：

```python
def _is_final_answer_format_valid(payload: dict) -> bool:
    return bool((payload.get("quality") or {}).get("format_valid"))
```

测试：

```python
def test_missing_boxed_payload_requires_retry():
    payload = {"quality": {"format_valid": False}}
    assert _is_final_answer_format_valid(payload) is False
```

**Step 5: 运行相关测试**

Run:

```bash
cd apps/miroflow-agent && uv run pytest <新增测试文件> -q
cd apps/gradio-demo && uv run pytest tests/test_render_markdown.py -q
```

Expected: PASS。

---

### Task 3: 前端支持 `final_output` 事件，保证缓存/回放一致

**Files:**
- Modify: `apps/gradio-demo/main.py`
- Test: `apps/gradio-demo/tests/test_render_markdown.py`
- Test: `apps/gradio-demo/tests/test_reconnect_or_init.py`

**目标行为:**
- 收到 `{"event": "final_output", "data": {"markdown": "# Done"}}` 时，最终报告进入 `Final Summary` agent，能被 `_render_markdown()` 展示。
- 缓存命中的 SSE 回放不再停留在“等待开始研究”。
- 不影响已有 `show_text`/`message` 渲染。

**Step 1: 写失败测试**

在 `apps/gradio-demo/tests/test_render_markdown.py` 新增：

```python
def test_update_state_with_final_output_renders_summary():
    demo_main = _load_demo_main()
    state = demo_main._init_render_state()

    state = demo_main._update_state_with_event(
        state,
        {"event": "final_output", "data": {"markdown": "# 缓存结果\n\n正文"}},
    )
    markdown = demo_main._render_markdown(state)

    assert "## 📋 研究总结" in markdown
    assert "# 缓存结果" in markdown
    assert "等待开始研究" not in markdown
```

**Step 2: 运行测试确认失败**

Run:

```bash
cd apps/gradio-demo && uv run pytest tests/test_render_markdown.py::test_update_state_with_final_output_renders_summary -q
```

Expected: FAIL。

**Step 3: 最小实现**

在 `_update_state_with_event()` 中增加 `final_output` 分支：

```python
elif event == "final_output":
    markdown = ""
    if isinstance(data, dict):
        markdown = str(data.get("markdown") or "")
    elif isinstance(data, str):
        markdown = data
    if not markdown.strip():
        return state
    agent_id = "final-output"
    if agent_id not in state["agents"]:
        state["agents"][agent_id] = {
            "agent_name": "Final Summary",
            "tool_call_order": [],
            "tools": {},
        }
        state["agent_order"].append(agent_id)
    call_id = "final-output-message"
    agent = state["agents"][agent_id]
    if call_id not in agent["tools"]:
        agent["tools"][call_id] = {"tool_name": "message", "content": ""}
        agent["tool_call_order"].append(call_id)
    agent["tools"][call_id]["content"] = markdown
    runtime_stage = state.setdefault("runtime_stage", {})
    runtime_stage["phase"] = "完成"
    runtime_stage["detail"] = "最终结果已生成"
    runtime_stage["updated_at"] = time.time()
```

注意：不要用随机 ID，否则回放多次会重复显示。

**Step 4: 运行测试**

Run:

```bash
cd apps/gradio-demo && uv run pytest tests/test_render_markdown.py tests/test_reconnect_or_init.py -q
```

Expected: PASS。

---

### Task 4: 修正 TaskEventSink 阶段字段映射

**Files:**
- Modify: `apps/api-server/services/task_event_sink.py`
- Test: 新增 `apps/api-server/tests/test_task_event_sink.py`

**目标行为:**
- `stage_heartbeat` 使用 `phase` 和 `detail` 更新 `current_stage`。
- `start_of_agent` 使用 `agent_name`。
- `tool_call` 使用 `tool_name`。
- 空字段不覆盖已有阶段。

**Step 1: 写失败测试**

新增：

```python
@pytest.mark.asyncio
async def test_task_event_sink_maps_actual_event_fields_to_stage():
    store = AsyncMock()
    sink = TaskEventSink(store, "task-1")

    await sink.put({"event": "stage_heartbeat", "data": {"phase": "检索", "detail": "第 1 轮"}})
    await sink.put({"event": "start_of_agent", "data": {"agent_name": "Final Summary"}})
    await sink.put({"event": "tool_call", "data": {"tool_name": "google_search"}})

    store.update_task_stage.assert_any_await("task-1", "检索:第 1 轮")
    store.update_task_stage.assert_any_await("task-1", "agent:Final Summary")
    store.update_task_stage.assert_any_await("task-1", "tool:google_search")
```

**Step 2: 运行测试确认失败**

Run:

```bash
cd apps/api-server && uv run pytest tests/test_task_event_sink.py -q
```

Expected: FAIL。

**Step 3: 最小实现**

修改 `_handle_event_side_effects()`：

```python
if event_type == "stage_heartbeat":
    phase = str(data.get("phase") or "").strip()
    detail = str(data.get("detail") or "").strip()
    if phase and detail:
        await self._store.update_task_stage(self._task_id, f"{phase}:{detail}")
    elif phase:
        await self._store.update_task_stage(self._task_id, phase)
elif event_type == "start_of_agent":
    agent_name = str(data.get("agent_name") or data.get("agent") or "unknown").strip()
    await self._store.update_task_stage(self._task_id, f"agent:{agent_name}")
elif event_type == "tool_call":
    tool_name = str(data.get("tool_name") or data.get("tool") or "unknown").strip()
    await self._store.update_task_stage(self._task_id, f"tool:{tool_name}")
```

**Step 4: 运行测试**

Run:

```bash
cd apps/api-server && uv run pytest tests/test_task_event_sink.py tests/test_sse_stream.py -q
```

Expected: PASS。

---

### Task 5: 统一 API 状态响应中的结果质量信息

**Files:**
- Modify: `apps/api-server/models.py`
- Modify: `apps/api-server/routers/research.py`
- Modify: `apps/api-server/services/task_store.py`
- Test: `apps/api-server/tests/test_research_queue_api.py`

**目标行为:**
- `GET /v1/research/{task_id}` 返回 `result_quality`。
- 最少包含：
  - `format_valid: bool`
  - `fallback_used: bool`
  - `issues: list[str]`
- 缺旧数据时返回默认值，不破坏旧任务。

**Step 1: 写失败测试**

在 `apps/api-server/tests/test_research_queue_api.py` 新增：

```python
async def test_get_task_status_includes_result_quality(...):
    ...
    resp = await client.get("/v1/research/task-1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["result_quality"] == {
        "format_valid": True,
        "fallback_used": False,
        "issues": [],
    }
```

如果现有 fixture 不方便，优先写 `ResearchTaskStatusResponse` 模型序列化测试。

**Step 2: 运行测试确认失败**

Run:

```bash
cd apps/api-server && uv run pytest tests/test_research_queue_api.py::test_get_task_status_includes_result_quality -q
```

Expected: FAIL。

**Step 3: 最小实现**

- 在 `models.py` 增加 `ResultQuality` Pydantic model。
- 在 `ResearchTaskStatusResponse` 加 `result_quality: ResultQuality = Field(default_factory=ResultQuality)`。
- 在 `TaskStore` 增加可选存取方法：
  - `store_result_quality(task_id: str, quality: dict)`
  - `get_result_quality(task_id: str) -> dict`
- worker 成功存储结果时同步存质量信息。
- `routers/research.py` 读取质量信息并返回；读不到时用默认值。

**Step 4: 运行测试**

Run:

```bash
cd apps/api-server && uv run pytest tests/test_research_queue_api.py tests/test_task_store.py -q
```

Expected: PASS。

---

### Task 6: 增加端到端回归测试清单

**Files:**
- Modify: `apps/gradio-demo/tests/test_api_client.py`
- Modify: `apps/api-server/tests/test_sse_stream.py`
- Modify: `apps/api-server/tests/test_research_worker.py`

**目标行为:**
- 缓存命中：SSE `final_output + done` 能在前端渲染最终总结。
- pipeline failed：API 状态为 `failed`，SSE 有 `error + done(status=failed)`。
- missing boxed：任务可以有展示文本，但 `result_quality.format_valid=False`。

**Step 1: 补 SSE failed 测试**

确认已有 `test_stream_failed_task_emits_done_failed`，如已有只补断言 error 事件；如没有则新增。

**Step 2: 补 gradio final_output 渲染测试**

复用 Task 3 的测试即可。

**Step 3: 补 worker missing boxed 质量测试**

使用 mock pipeline 返回：

```python
{
    "status": "completed",
    "final_summary": "正文",
    "final_boxed_answer": "正文",
    "result_quality": {
        "format_valid": False,
        "fallback_used": True,
        "issues": ["missing_boxed"],
    },
}
```

断言 `store_result_quality` 被调用。

**Step 4: 运行组合测试**

Run:

```bash
cd apps/api-server && uv run pytest tests/test_research_worker.py tests/test_research_queue_api.py tests/test_sse_stream.py -q
cd apps/gradio-demo && uv run pytest tests/test_render_markdown.py tests/test_reconnect_or_init.py tests/test_api_client.py -q
```

Expected: PASS。

---

## AI 执行提示词模板

把下面这段直接给执行 AI：

```text
你在 /Users/ray/workspace/MiroThinker 执行 docs/plans/2026-05-03-output-robustness.md。

要求：
1. 严格按 Task 顺序执行，一次只做一个 Task。
2. 每个 Task 必须先写失败测试并运行确认失败，再写最小实现，再运行通过。
3. 不要做计划外重构，不要改 UI 样式，不要调整搜索策略。
4. 代码注释使用中文；不要硬编码新增业务阈值。
5. 不要执行 git commit。
6. 每完成一个 Task，汇报：
   - 修改文件
   - 新增/修改测试
   - 执行命令和结果
   - 遗留风险
```

## 最终验收标准

- pipeline 异常不会被 API 标记为 completed。
- 无 `\boxed{}` 的最终正文不会被记录为严格格式成功。
- Gradio 能渲染 `final_output`，缓存命中和 SSE 回放一致。
- API task snapshot 能反映结果质量。
- 阶段快照能正确显示排队、检索、总结、失败。
- 以下命令通过或给出明确跳过原因：

```bash
cd apps/api-server && uv run pytest tests/test_research_worker.py tests/test_research_queue_api.py tests/test_sse_stream.py tests/test_task_store.py -q
cd apps/gradio-demo && uv run pytest tests/test_render_markdown.py tests/test_reconnect_or_init.py tests/test_api_client.py tests/test_layout_structure.py -q
cd apps/miroflow-agent && uv run pytest -q
```

## 回滚策略

- 如 Task 2 对返回值兼容性影响过大，保留旧 `format_final_summary_and_log()` 三元组接口，新增 `format_final_summary_payload()` 给新链路使用。
- 如 Task 5 改动范围过大，可先只在 worker 内记录 `result_quality` 到日志，API 字段延后，但 Task 1-4 必须完成。
- 如全量 agent 测试依赖外部服务失败，至少保留 formatter/answer generator 单测和 api/gradio 相关单测。
