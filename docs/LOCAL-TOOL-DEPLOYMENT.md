# OpenClaw-MiroSearch 本地工具部署指南

本文档说明如何部署可选的本地工具服务，并接入 OpenClaw-MiroSearch。

目标：

- 降低商业 API 依赖
- 在内网/本地环境可持续运行
- 按需替换默认云工具

## 可选本地工具

- 音频转写：`tool-transcribe-os`
- 视觉问答：`tool-vqa-os`
- 推理服务：`tool-reasoning-os`

这些工具均为可选，不是 Demo 最小启动必需项。

## 先决条件

- NVIDIA GPU（按模型显存需求准备）
- Python 3.10+
- 可用的 CUDA 环境

## 1) 音频转写（`tool-transcribe-os`）

模型：`openai/whisper-large-v3-turbo`

```bash
pip install vllm==0.10.0
pip install 'vllm[audio]'

vllm serve openai/whisper-large-v3-turbo \
  --served-model-name whisper-large-v3-turbo \
  --task transcription \
  --host 0.0.0.0 \
  --port 8000
```

`.env` 示例：

```bash
WHISPER_MODEL_NAME="openai/whisper-large-v3-turbo"
WHISPER_BASE_URL="http://127.0.0.1:8000/v1"
WHISPER_API_KEY="<optional_key>"
```

## 2) 视觉问答（`tool-vqa-os`）

模型：`Qwen/Qwen2.5-VL-72B-Instruct`

```bash
pip install 'sglang[all]'

python3 -m sglang.launch_server \
  --model-path Qwen/Qwen2.5-VL-72B-Instruct \
  --tp 8 \
  --host 0.0.0.0 \
  --port 8001 \
  --trust-remote-code
```

`.env` 示例：

```bash
VISION_MODEL_NAME="Qwen/Qwen2.5-VL-72B-Instruct"
VISION_BASE_URL="http://127.0.0.1:8001/v1/chat/completions"
VISION_API_KEY="<optional_key>"
```

## 3) 推理服务（`tool-reasoning-os`）

模型：`Qwen/Qwen3-235B-A22B-Thinking-2507`

```bash
pip install 'sglang[all]'

python3 -m sglang.launch_server \
  --model-path Qwen/Qwen3-235B-A22B-Thinking-2507 \
  --tp 8 \
  --host 0.0.0.0 \
  --port 8002 \
  --trust-remote-code \
  --context-length 131072
```

`.env` 示例：

```bash
REASONING_MODEL_NAME="Qwen/Qwen3-235B-A22B-Thinking-2507"
REASONING_BASE_URL="http://127.0.0.1:8002/v1/chat/completions"
REASONING_API_KEY="<optional_key>"
```

## 接入方式

在 `apps/miroflow-agent/conf/agent/*.yaml` 中按需启用：

```yaml
main_agent:
  tools:
    - search_and_scrape_webpage
    - jina_scrape_llm_summary
    - tool-transcribe-os
    - tool-vqa-os
    - tool-reasoning-os
```

然后确保 `apps/miroflow-agent/.env` 填好对应地址与密钥。

## 备注

- 如果不部署本地版本，可继续使用默认商业工具版本（不带 `-os` 后缀）。
- 若以检索为主，优先保证 `search_and_scrape_webpage` 路径稳定，再逐步加本地多模态工具。
