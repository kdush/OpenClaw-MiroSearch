# Copyright (c) 2025 MiroMind
# This source code is licensed under the Apache 2.0 License.

"""Lead tracking for deep research chained clue following."""

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from .deep_efficiency import (
    default_max_lead_follow_ups_for_intensity,
    resolve_agent_value,
)

logger = logging.getLogger(__name__)


def resolve_lead_tracking_config(cfg: Any) -> Tuple[bool, int]:
    """Resolve enable flag and max follow-ups from Hydra agent config.

    Accepts flags / intensity on either ``agent.*`` (API profile_resolver path)
    or ``agent.main_agent.*`` (CLI harness path used in Round 2).

    Auto-enables when ``research_intensity == "deep"`` unless explicitly disabled.
    """
    explicit = resolve_agent_value(cfg, "enable_lead_tracking")
    intensity_norm = (
        str(resolve_agent_value(cfg, "research_intensity") or "").strip().lower()
    )

    # Round 6: deep defaults to Top-K=2 clue follow-ups (was 3).
    intensity_default_k = default_max_lead_follow_ups_for_intensity(intensity_norm)

    max_follow_ups = resolve_agent_value(cfg, "max_lead_follow_ups")
    if max_follow_ups is None:
        max_follow_ups = intensity_default_k
    try:
        max_follow_ups_int = (
            int(max_follow_ups) if max_follow_ups is not None else intensity_default_k
        )
    except (TypeError, ValueError):
        max_follow_ups_int = intensity_default_k

    if explicit is not None:
        # OmegaConf may yield string "true"/"false"
        if isinstance(explicit, str):
            enabled = explicit.strip().lower() in {"1", "true", "yes", "on"}
        else:
            enabled = bool(explicit)
    else:
        enabled = intensity_norm == "deep"

    return enabled, max(1, max_follow_ups_int)


@dataclass
class Lead:
    """Represents a research lead or open question."""

    question: str
    source: str
    turn: int
    priority: float = 1.0
    followed_up: bool = False
    follow_up_turn: Optional[int] = None
    findings: str = ""


@dataclass
class LeadTrail:
    """Tracks the complete trail of leads and follow-ups."""

    original_query: str
    leads: List[Lead] = field(default_factory=list)
    follow_up_count: int = 0
    max_follow_ups: int = 3
    min_priority: float = 0.5

    def add_lead(
        self, question: str, source: str, turn: int, priority: float = 1.0
    ) -> None:
        """Add a new lead to track."""
        # Deduplicate similar leads
        normalized_q = self._normalize_question(question)
        for existing in self.leads:
            if self._normalize_question(existing.question) == normalized_q:
                # Update priority if higher
                if priority > existing.priority:
                    existing.priority = priority
                return

        lead = Lead(
            question=question,
            source=source,
            turn=turn,
            priority=priority,
        )
        self.leads.append(lead)
        logger.debug("Added lead from turn %d: %s", turn, question[:80])

    def get_top_unfollowed_leads(self, k: int = 3) -> List[Lead]:
        """Get top K unfollowed leads by priority."""
        unfollowed = [
            lead
            for lead in self.leads
            if not lead.followed_up and lead.priority >= self.min_priority
        ]
        unfollowed.sort(key=lambda x: x.priority, reverse=True)
        return unfollowed[:k]

    def mark_followed_up(self, lead: Lead, turn: int, findings: str = "") -> None:
        """Mark a lead as followed up."""
        lead.followed_up = True
        lead.follow_up_turn = turn
        lead.findings = findings
        self.follow_up_count += 1
        logger.info(
            "Marked lead as followed up at turn %d: %s", turn, lead.question[:80]
        )

    def should_continue_following(self) -> bool:
        """Check if we should continue following leads."""
        if self.follow_up_count >= self.max_follow_ups:
            return False
        top_leads = self.get_top_unfollowed_leads(k=1)
        return len(top_leads) > 0

    def format_trail_section(self) -> str:
        """Format the lead trail for inclusion in the final report.

        Followed leads keep full detail. Pending seeds are collapsed into a short
        Chinese summary so the report does not look truncated/unfinished.
        """
        if not self.leads:
            return ""

        followed_leads = [lead for lead in self.leads if lead.followed_up]
        pending_leads = [lead for lead in self.leads if not lead.followed_up]

        lines = ["## 线索追踪 / Lead Trail\n"]
        if followed_leads:
            lines.append("以下是研究过程中已跟进的关键线索及其发现：\n")
            for i, lead in enumerate(followed_leads, 1):
                lines.append(f"\n### Lead {i}: {lead.question}")
                lines.append(f"**来源**: {lead.source} (Turn {lead.turn})")
                lines.append(f"**优先级**: {lead.priority:.2f}")
                lines.append("**状态**: followed")
                if lead.follow_up_turn:
                    lines.append(f"**追踪轮次**: Turn {lead.follow_up_turn}")
                if lead.findings:
                    lines.append(f"\n**发现**:\n{lead.findings}")
                lines.append("")
            if pending_leads:
                lines.append("### 未跟进线索（摘要）\n")
                for i, lead in enumerate(pending_leads, 1):
                    q = lead.question.strip()
                    if len(q) > 80:
                        q = q[:77] + "…"
                    lines.append(f"{i}. {q} — **未跟进**")
                lines.append("")
        else:
            lines.append(
                "本轮未实际跟进额外线索（种子线索仍为 pending，不视为报告未写完）。\n"
            )
            if pending_leads:
                lines.append("### 未跟进线索（摘要）\n")
                for i, lead in enumerate(pending_leads, 1):
                    q = lead.question.strip()
                    if len(q) > 80:
                        q = q[:77] + "…"
                    lines.append(f"{i}. {q} — **未跟进**")
                lines.append("")

        return "\n".join(lines)

    @staticmethod
    def _normalize_question(question: str) -> str:
        """Normalize question for deduplication."""
        normalized = question.lower().strip()
        normalized = re.sub(r"[^\w\s]", "", normalized)
        normalized = re.sub(r"\s+", " ", normalized)
        return normalized

    @staticmethod
    def extract_leads_from_text(
        text: str, turn: int, source: str = "assistant"
    ) -> List[Tuple[str, float]]:
        """Extract potential leads from assistant response text.

        Returns list of (question, priority) tuples.
        """
        leads: List[Tuple[str, float]] = []
        if not text or not text.strip():
            return leads

        # Pattern 1: Explicit investigation / follow-up markers (EN + ZH)
        explicit_patterns = [
            r"(?i)(?:需要|应该|可以)(?:进一步)?(?:调查|研究|了解|查询)[:：]?\s*(.+?)(?:\.|。|$)",
            r"(?i)(?:need(?:s)?\s+to\s+(?:investigate|research|verify|check|explore)|"
            r"further\s+(?:research|investigation|study)\s+needed|"
            r"worth\s+(?:investigating|exploring)|"
            r"should\s+(?:investigate|research|verify)|"
            r"follow[- ]?up[:\s]+|"
            r"investigate)[:：]?\s*(.+?)(?:\.|$)",
            r"(?i)(?:open question|remaining question|unanswered)[:：]?\s*(.+?)(?:\.|$)",
        ]

        for pattern in explicit_patterns:
            matches = re.finditer(pattern, text, re.MULTILINE)
            for match in matches:
                question = match.group(1).strip()
                if 10 < len(question) < 200:
                    leads.append((question, 1.0))

        # Pattern 2: Standalone questions (EN/ZH interrogatives)
        question_pattern = r"(?:^|\n)([^。\.\n]{10,200}[?？])"
        matches = re.finditer(question_pattern, text)
        interrogatives = (
            "如何",
            "怎么",
            "为什么",
            "什么",
            "哪些",
            "是否",
            "how",
            "why",
            "what",
            "when",
            "where",
            "which",
            "who",
            "whom",
            "could",
            "would",
            "should",
            "is there",
            "are there",
            "does",
            "did",
        )
        for match in matches:
            question = match.group(1).strip()
            q_lower = question.lower()
            if not any(word in q_lower for word in interrogatives):
                continue
            if len(question) > 10:
                leads.append((question, 0.75))

        # Pattern 3: Uncertainty / gap markers
        uncertainty_patterns = [
            r"(?i)(?:不确定|不清楚|需要确认|尚不明确)[:：]?\s*(.+?)(?:\.|。|$)",
            r"(?i)(?:uncertain(?:ty)?|unclear|unknown|needs?\s+verification|"
            r"remains?\s+(?:unclear|unknown|unresolved)|"
            r"not\s+(?:yet\s+)?(?:clear|known|established)|"
            r"gap\s+in)[:：\s]+(.+?)(?:\.|$)",
            r"(?i)(?:however|but|尽管|但是)[,，]\s*(.+?(?:存疑|unclear|uncertain|unknown).+?)(?:\.|。|$)",
        ]

        for pattern in uncertainty_patterns:
            matches = re.finditer(pattern, text, re.MULTILINE)
            for match in matches:
                question = match.group(1).strip()
                if 10 < len(question) < 200:
                    leads.append((question, 0.8))

        # Pattern 4: Causal / chronological chain hints (common in "how did X lead to Y")
        chain_patterns = [
            r"(?i)(?:this\s+(?:led|leads)\s+to|which\s+(?:led|enabled)|"
            r"key\s+(?:step|link|bridge)|missing\s+link)[:：]?\s*(.+?)(?:\.|$)",
            r"(?i)(?:关键环节|关键步骤|缺失环节)[:：]?\s*(.+?)(?:\.|。|$)",
        ]
        for pattern in chain_patterns:
            for match in re.finditer(pattern, text, re.MULTILINE):
                question = match.group(1).strip()
                if 10 < len(question) < 200:
                    leads.append((f"Investigate intermediate step: {question}", 0.7))

        # Deduplicate
        seen: Set[str] = set()
        unique_leads: List[Tuple[str, float]] = []
        for question, priority in leads:
            normalized = LeadTrail._normalize_question(question)
            if normalized not in seen and len(normalized) > 5:
                seen.add(normalized)
                unique_leads.append((question, priority))

        return unique_leads

    @staticmethod
    def seed_leads_from_query(query: str) -> List[Tuple[str, float]]:
        """Seed deep-research leads from the original user query.

        Helps English flash models that answer narratively without explicit
        open-question markers (Case C style chained research).
        """
        if not query or not query.strip():
            return []

        q = query.strip()
        seeds: List[Tuple[str, float]] = []

        # "How did X lead to Y?" / "How did X lead to Y"
        m = re.search(
            r"(?i)how\s+did\s+(.+?)\s+lead\s+to\s+(.+?)(?:\?|$)",
            q,
        )
        if m:
            start = m.group(1).strip().rstrip("?.")
            end = m.group(2).strip().rstrip("?.")
            seeds.append(
                (
                    f"What intermediate scientific discoveries connected {start} to {end}?",
                    0.95,
                )
            )
            seeds.append(
                (
                    f"Which specific mechanisms or tools from {start} enabled {end}?",
                    0.9,
                )
            )
            return seeds

        # Generic deep seed: force at least one follow-up angle
        if len(q) > 20:
            seeds.append(
                (
                    f"What are the most important unresolved or contested aspects of: {q[:160]}?",
                    0.65,
                )
            )
        return seeds


class LeadTrackingManager:
    """Manages lead tracking for the orchestrator."""

    def __init__(self, enabled: bool = False, max_follow_ups: int = 3):
        self.enabled = enabled
        self.trail: Optional[LeadTrail] = None
        self.max_follow_ups = max_follow_ups
        self._seeded_from_query = False

    def initialize(self, query: str) -> None:
        """Initialize lead tracking for a new research task."""
        if not self.enabled:
            return
        self.trail = LeadTrail(
            original_query=query,
            max_follow_ups=self.max_follow_ups,
        )
        self._seeded_from_query = False
        # Seed deep chained-query leads so follow-ups can run even when the
        # model answers narratively without explicit "investigate:" markers.
        for question, priority in LeadTrail.seed_leads_from_query(query):
            self.trail.add_lead(question, "query_seed", turn=0, priority=priority)
            self._seeded_from_query = True
        logger.info(
            "Initialized lead tracking for query: %s (seeded=%s, leads=%d)",
            query[:80],
            self._seeded_from_query,
            len(self.trail.leads),
        )

    def process_turn_response(self, assistant_text: str, turn: int) -> List[str]:
        """Process assistant response and extract leads.

        Returns list of lead questions that should be followed up.
        """
        if not self.enabled or not self.trail:
            return []

        extracted_leads = LeadTrail.extract_leads_from_text(assistant_text, turn)
        for question, priority in extracted_leads:
            self.trail.add_lead(question, "assistant_response", turn, priority)

        # Check if we should follow up
        if not self.trail.should_continue_following():
            return []

        # Get top leads to follow
        top_leads = self.trail.get_top_unfollowed_leads(k=1)
        return [lead.question for lead in top_leads]

    def record_follow_up(
        self, lead_question: str, turn: int, findings: str = ""
    ) -> None:
        """Record that a lead was followed up."""
        if not self.enabled or not self.trail:
            return

        # Find the lead
        for lead in self.trail.leads:
            normalized_q = LeadTrail._normalize_question(lead.question)
            normalized_search = LeadTrail._normalize_question(lead_question)
            if normalized_q == normalized_search or normalized_search in normalized_q:
                self.trail.mark_followed_up(lead, turn, findings)
                break

    def get_trail_section(self) -> str:
        """Get the formatted trail section for the final report."""
        if not self.enabled or not self.trail:
            return ""
        return self.trail.format_trail_section()

    def get_stats(self) -> Dict[str, Any]:
        """Get tracking statistics."""
        if not self.enabled or not self.trail:
            return {"enabled": False}

        return {
            "enabled": True,
            "total_leads": len(self.trail.leads),
            "followed_up": self.trail.follow_up_count,
            "unfollowed": len(
                [lead for lead in self.trail.leads if not lead.followed_up]
            ),
            "seeded_from_query": self._seeded_from_query,
        }
