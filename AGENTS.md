# Repository Guidelines

## Project Structure & Module Organization
- `apps/miroflow-agent/`：主代理运行与配置，`conf/agent/` 放 YAML 配置，`tests/` 放测试。
- `apps/gradio-demo/`：Gradio 演示应用。
- `apps/collect-trace/`：轨迹采集脚本。
- `apps/visualize-trace/`：轨迹可视化工具。
- `apps/lobehub-compatibility/`：LobeHub 兼容与解析器验证。
- `libs/miroflow-tools/`：共享工具框架，测试在 `src/test/`。
- `assets/`：品牌与示意图片素材。

## Build, Test, and Development Commands
- `just lint` / `just format` / `just format-md` / `just precommit`：仓库级 lint、格式化与预提交检查。
- `cd apps/miroflow-agent && uv sync`：安装依赖。
- `uv run python main.py llm=qwen-3 agent=mirothinker_v1.5_keep5_max200 llm.base_url=http://localhost:61002/v1`：本地运行代理（需先启动模型服务）。
- `uv run pytest`：运行当前应用测试（默认生成 `report.html` 与 `htmlcov/` 覆盖率报告）。
- `cd libs/miroflow-tools && uv run pytest`：运行工具库测试。
- 仅有 `requirements.txt` 的应用（如 `apps/visualize-trace/`、`apps/lobehub-compatibility/`）使用 `pip install -r requirements.txt`。

## Coding Style & Naming Conventions
- Python 使用 4 空格缩进，模块/函数 `snake_case`，类 `CapWords`，常量 `UPPER_CASE`。
- 代码风格与格式以 `ruff` 为准，Markdown 用 `mdformat`。
- 配置文件集中在 `conf/agent/*.yaml`，避免在代码中硬编码密钥或模型地址。

## Testing Guidelines
- 统一使用 `pytest` + `pytest-asyncio`，覆盖率由 `pytest-cov` 生成。
- 测试位置：`apps/miroflow-agent/tests/`，`libs/miroflow-tools/src/test/`。
- `libs/miroflow-tools` 支持 `unit`/`integration`/`slow`/`requires_api_key` 标记；快速测试可用 `pytest -m "not slow"`。

## Commit & Pull Request Guidelines
- 提交信息用动词开头、简短明确；可使用 `fix(scope): ...` 形式，历史中也存在 `Update README.md` 这类直述提交。
- PR 需说明动机、影响范围与测试命令；涉及 UI（Gradio/可视化）请附截图；涉及配置改动请列出新增或变更的 `.env` 变量。

## Security & Configuration Tips
- 机密配置放在 `apps/miroflow-agent/.env`，基于 `.env.example` 补齐。
- 不提交 API key 或本地路径；文档示例统一使用占位符。
