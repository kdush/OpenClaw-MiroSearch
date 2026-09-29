"""M5：按实际可用且健康的 provider 自动分档（范围收窄版）。

路线图 M5 本期只覆盖**检索 provider 与 profile 选择**；LLM 网关、视觉能力与
复杂多源排序留待后续独立设计。设计原则（路线图 §5）：

- 配置参差是常态：同一套代码必须在单 provider 档真跑起来、在多 provider 档
  自动升档，且**禁止出现"所选 profile 的要求在当前配置下结构性不可达"**；
- 显式严格路由（如 ``searxng-only``）保持严格，不静默扩容；
- 自动模式按"实际可用且健康"的 provider 选档，并记录生效档位、原因与降级路径；
- 置信门槛随档位自适应（单 provider 档不得要求 ≥2 个 provider 覆盖）。

"健康"由调用方传入（例如把运行中超时的 provider 排除后重算），本模块不发起
网络探测。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence, Set

TIER_MULTI = "multi-provider"
TIER_SINGLE = "single-provider"
TIER_NONE = "no-provider"

# 单 provider 档可用的 profile；多 provider 档自动升到这里
_SINGLE_PROFILE_BY_PROVIDER = {
    "searxng": "searxng-first",
    "serper": "serp-first",
    "serpapi": "serp-first",
    "tavily": "serp-first",
}
_MULTI_PROFILE = "multi-route"


@dataclass(frozen=True)
class TierDecision:
    """一次分档的完整决策记录（生效档位 + 原因 + 降级路径）。"""

    tier: str
    profile: str
    effective_order: List[str] = field(default_factory=list)
    strict: bool = False
    reason: str = ""
    degraded_from: str = ""
    min_provider_coverage: int = 1

    def to_dict(self) -> dict:
        return {
            "tier": self.tier,
            "profile": self.profile,
            "effective_order": list(self.effective_order),
            "strict": self.strict,
            "reason": self.reason,
            "degraded_from": self.degraded_from,
            "min_provider_coverage": self.min_provider_coverage,
        }


def _normalize(names: Iterable[str]) -> List[str]:
    return [str(n).strip().lower() for n in names if str(n).strip()]


def resolve_provider_tier(
    requested_profile: str,
    available: Sequence[str],
    *,
    requested_order: str = "",
    strict: bool = False,
    healthy: Optional[Set[str]] = None,
) -> TierDecision:
    """解析生效档位。

    ``available`` 是当前可用 provider（有凭据且已注册），``healthy`` 可选地进一步
    排除已知不健康（如刚超时）的 provider；省略时视为全部健康。
    """
    usable = _normalize(available)
    if healthy is not None:
        healthy_set = {h.strip().lower() for h in healthy}
        usable = [name for name in usable if name in healthy_set]

    if strict:
        configured = _normalize(requested_order.split(",")) if requested_order else []
        order = [name for name in configured if name in usable]
        if not order:
            return TierDecision(
                tier=TIER_NONE,
                profile=requested_profile,
                strict=True,
                reason="严格路由在当前配置下没有可用 provider",
                min_provider_coverage=0,
            )
        return TierDecision(
            tier=TIER_SINGLE if len(order) == 1 else TIER_MULTI,
            profile=requested_profile,
            effective_order=order,
            strict=True,
            reason="显式严格路由，保持配置范围",
            min_provider_coverage=min(2, len(order)),
        )

    if not usable:
        return TierDecision(
            tier=TIER_NONE,
            profile=requested_profile,
            reason="无可用 provider（缺少凭据或全部不健康）",
            min_provider_coverage=0,
        )

    if len(usable) >= 2:
        return TierDecision(
            tier=TIER_MULTI,
            profile=_MULTI_PROFILE,
            effective_order=usable,
            reason=f"检测到 {len(usable)} 个可用 provider，自动升档为交叉验真",
            degraded_from=(
                requested_profile
                if requested_profile and requested_profile != _MULTI_PROFILE
                else ""
            ),
            min_provider_coverage=2,
        )

    only = usable[0]
    return TierDecision(
        tier=TIER_SINGLE,
        profile=_SINGLE_PROFILE_BY_PROVIDER.get(only, "serp-first"),
        effective_order=[only],
        reason=f"仅 {only} 可用，按单 provider 档运行",
        degraded_from=(
            requested_profile
            if requested_profile
            and requested_profile != _SINGLE_PROFILE_BY_PROVIDER.get(only)
            else ""
        ),
        min_provider_coverage=1,
    )
