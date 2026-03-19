from pathlib import Path
import sys


# 确保测试可直接导入项目源码
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.orchestrator import Orchestrator
from src.utils.prompt_utils import generate_cross_verification_prompt


BANNED_TERMS = ("导弹", "无人机", "火箭弹", "美以")


def test_cross_verification_prompt_should_not_include_domain_specific_terms():
    prompt = generate_cross_verification_prompt(
        task_description="阿里悟空是什么产品？",
        min_search_rounds=3,
        min_high_conf_sources=2,
        high_conf_domains=["example.com"],
    )
    for term in BANNED_TERMS:
        assert term not in prompt


def test_followup_prompt_should_not_include_domain_specific_terms():
    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator.verification_enabled = True
    orchestrator.verification_min_search_rounds = 3
    orchestrator.verification_search_rounds = 1
    orchestrator.verification_min_high_conf_sources = 2
    orchestrator.verification_high_conf_source_domains = set()
    orchestrator.verification_high_conf_domains = {
        "reuters.com",
        "apnews.com",
        "bbc.com",
    }

    prompt = orchestrator._build_verification_followup_prompt("阿里悟空是什么产品？")

    for term in BANNED_TERMS:
        assert term not in prompt
    assert "阿里悟空是什么产品？" in prompt
