"""回归测试：reset_last_call_tokens 必须使用 provider 各自的字段命名。

修复前 orchestrator 用 OpenAI 键（prompt/completion）重置 Anthropic 客户端的
last_call_tokens，导致 ensure_summary_context 读 input_tokens 永远拿到 0，
上下文超限检测系统性低估。
"""

import sys
from pathlib import Path

AGENT_SRC = Path(__file__).resolve().parents[1] / "src"
if str(AGENT_SRC.parent) not in sys.path:
    sys.path.insert(0, str(AGENT_SRC.parent))

from src.llm.providers.anthropic_client import AnthropicClient  # noqa: E402
from src.llm.providers.openai_client import OpenAIClient  # noqa: E402


def test_anthropic_reset_uses_input_output_keys():
    client = AnthropicClient.__new__(AnthropicClient)
    client.reset_last_call_tokens()
    assert set(client.last_call_tokens) == {"input_tokens", "output_tokens"}
    assert all(v == 0 for v in client.last_call_tokens.values())


def test_openai_reset_uses_prompt_completion_keys():
    client = OpenAIClient.__new__(OpenAIClient)
    client.reset_last_call_tokens()
    assert set(client.last_call_tokens) == {"prompt_tokens", "completion_tokens"}
    assert all(v == 0 for v in client.last_call_tokens.values())
