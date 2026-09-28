"""M3 接线契约：orchestrator._adjudicate_report_claims 的开关与 fail-closed。

这段接线决定 M3 是否真的在出稿前跑、以及跑挂了会不会拖垮整份报告。
真实 pipeline 验收需要凭据（LLM 配额），所以这里把接线逻辑本身钉死。
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.claim_verification import (  # noqa: E402
    ClaimSupportMap,
    ClaimVerdict,
)
from src.core.orchestrator import Orchestrator  # noqa: E402

pytestmark = pytest.mark.asyncio

REGISTRY = {"entries": [{"source_id": 1, "normalized_url": "https://a.com/x"}]}
REPORT = "## 结论\n\n- 营收同比增长 12%。\n"


class _FakeCfg(dict):
    """支持属性访问的配置桩，模仿 OmegaConf 的读取方式。"""

    def __getattr__(self, item):
        try:
            return self[item]
        except KeyError as exc:
            raise AttributeError(item) from exc


class _FakeLog:
    def __init__(self):
        self.steps = []

    def log_step(self, level, name, content, *args, **kwargs):
        self.steps.append({"level": level, "name": name, "content": content})

    @property
    def last(self):
        return self.steps[-1] if self.steps else None


class _FakeAnswerGenerator:
    def __init__(self, *, result=None, exc=None):
        self.result = result
        self.exc = exc
        self.calls = []

    async def generate_claim_support_map(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc is not None:
            raise self.exc
        return self.result


def _fake_self(*, agent_overrides=None, answer_generator=None, log=None):
    agent = {"research_intensity": "deep"}
    agent.update(agent_overrides or {})
    return SimpleNamespace(
        cfg=_FakeCfg(agent=_FakeCfg(agent)),
        answer_generator=answer_generator or _FakeAnswerGenerator(),
        task_log=log or _FakeLog(),
    )


async def _adjudicate(fake, report=REPORT, **kwargs):
    params = {
        "system_prompt": "sp",
        "message_history": [],
        "turn_count": 3,
        "registry": REGISTRY,
    }
    params.update(kwargs)
    return await Orchestrator._adjudicate_report_claims(fake, report, **params)


class TestAdjudicateReportClaims:
    async def test_disabled_returns_none_without_calling_llm(self):
        """light 档默认不开启：一次 LLM 调用都不该发出去。"""
        gen = _FakeAnswerGenerator(result=ClaimSupportMap())
        fake = _fake_self(
            agent_overrides={"research_intensity": "light"}, answer_generator=gen
        )

        assert await _adjudicate(fake) is None
        assert gen.calls == []
        assert fake.task_log.steps == []

    async def test_explicit_opt_out_beats_deep_default(self):
        gen = _FakeAnswerGenerator(result=ClaimSupportMap())
        fake = _fake_self(
            agent_overrides={"claim_verification": False}, answer_generator=gen
        )

        assert await _adjudicate(fake) is None
        assert gen.calls == []

    async def test_enabled_but_no_extractable_claims_skips_call(self):
        """抽不到主张就不核验——宁可不做，也不编造主张。"""
        gen = _FakeAnswerGenerator(result=ClaimSupportMap())
        fake = _fake_self(answer_generator=gen)

        out = await _adjudicate(fake, report="# 报告\n\n## 背景\n\n正文。\n")

        assert out is None
        assert gen.calls == []

    async def test_enabled_with_claims_calls_and_logs_info(self):
        claim_map = ClaimSupportMap(
            claims=[ClaimVerdict(claim="营收同比增长 12%。", support=[1])]
        )
        gen = _FakeAnswerGenerator(result=claim_map)
        fake = _fake_self(answer_generator=gen)

        out = await _adjudicate(fake)

        assert out is claim_map
        assert len(gen.calls) == 1
        assert gen.calls[0]["claims"] == ["营收同比增长 12%。"]
        assert gen.calls[0]["turn_count"] == 3
        assert fake.task_log.last["level"] == "info"
        assert "Claim Verification" in fake.task_log.last["name"]

    async def test_llm_failure_is_fail_closed(self):
        """核验挂掉只能降级成 warning + None，绝不能把报告一起拖挂。"""
        gen = _FakeAnswerGenerator(exc=RuntimeError("boom"))
        fake = _fake_self(answer_generator=gen)

        out = await _adjudicate(fake)

        assert out is None
        assert fake.task_log.last["level"] == "warning"
        assert "boom" in fake.task_log.last["content"]

    async def test_config_read_failure_is_also_fail_closed(self):
        """配置读取本身抛错（如 cfg 结构异常）也必须 fail-closed。"""

        class _BoomCfg:
            @property
            def agent(self):
                raise RuntimeError("cfg broken")

        gen = _FakeAnswerGenerator(result=ClaimSupportMap())
        fake = SimpleNamespace(
            cfg=_BoomCfg(), answer_generator=gen, task_log=_FakeLog()
        )

        assert await _adjudicate(fake) is None
        assert gen.calls == []
        assert fake.task_log.last["level"] == "warning"

    async def test_max_claims_cap_is_respected(self):
        gen = _FakeAnswerGenerator(result=ClaimSupportMap())
        fake = _fake_self(
            agent_overrides={"claim_verification_max_claims": 2}, answer_generator=gen
        )
        report = "## 结论\n\n" + "".join(
            f"- 第 {i} 条可核验主张内容。\n" for i in range(8)
        )

        await _adjudicate(fake, report=report)

        assert len(gen.calls[0]["claims"]) == 2
