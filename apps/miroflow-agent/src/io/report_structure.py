# Copyright (c) 2025 MiroMind
# This source code is licensed under the Apache 2.0 License.

"""Report structure validation and enforcement for research outputs."""

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class StructureSection:
    """Represents a required section in the report structure."""

    name: str
    pattern: str
    required: bool = True
    min_length: int = 50


# Shared patterns (Chinese + English aliases for hotspot cross-verification reports)
_TLDR = (
    r"(?i)##?\s*(?:tl;?dr(?:\s*/\s*结论[（(]标明置信度[）)])?|"
    r"摘要|核心结论|"
    r"结论\s*[（(]标明置信度[）)]|"
    r"结论（含置信度）)"
)
_CONCLUSION = (
    r"(?i)##?\s*(?:conclusions?\b|主要发现|关键结论(?:速览)?|"
    r"结论(?!\s*[（(]?标明置信度))"
)
# Bilingual Evidence headings (Round 6): EN + CN thematic aliases.
# Accepts e.g. ## Evidence, ## 证据, ## 临床证据, ## 证据与来源, ## 四、临床证据…
_EVIDENCE = (
    r"(?i)##?\s*(?:"
    r"evidence(?:\s*[（(][^）)\n]*[）)])?|"
    r"supporting\s+evidence|"
    r"证据(?:与来源|链|汇总|清单)?|"
    r"来源与证据|证据来源|"
    r"临床证据(?:与安全性争议)?|关键证据|核心证据|"
    r"(?:[一二三四五六七八九十\d]+[、.．]\s*)[^\n]{0,48}证据"
    r")"
)
_CONFLICTS = (
    r"(?i)##?\s*(?:conflicts?\s*(?:&|and|/)?\s*uncertaint(?:y|ies)?|"
    r"disagreements?\s*(?:/|,|&)?\s*uncertaint(?:y|ies)?|"
    r"冲突与不确定|已知[／/]?不确定[／/]?冲突|"
    r"(?:争议|冲突)[与和]不确定|来源分歧|口径冲突|"
    r"what\s+is\s+known\s*/\s*uncertain\s*/\s*conflicting)"
)
_TIMELINE = r"(?i)##?\s*(?:timeline|时间线|chronolog|大事记|时间轴)"
_CONFIRMED = (
    r"(?i)##?\s*(?:confirmed\s+vs\.?\s+unconfirmed|"
    r"what\s+is\s+confirmed|"
    r"已确认\s*(?:vs\.?|versus|与|/)\s*未确认|"
    r"能确认[与和]不能确认|确认与未确认|"
    r"已知与未知)"
)
_REFERENCES = r"(?i)##?\s*(?:references|参考文献|参考资料|来源列表|引用)"


class ReportStructureValidator:
    """Validates and enforces readable report structure."""

    # Expected sections for a well-structured research report.
    # detailed/deep hotspot reports MUST surface Conflicts explicitly (Round 5).
    REQUIRED_SECTIONS = {
        "compact": [
            StructureSection("tldr", _TLDR, required=True, min_length=30),
            StructureSection("conclusion", _CONCLUSION, required=True, min_length=50),
        ],
        "balanced": [
            StructureSection("tldr", _TLDR, required=True, min_length=30),
            StructureSection("conclusion", _CONCLUSION, required=True, min_length=100),
            StructureSection("evidence", _EVIDENCE, required=True, min_length=100),
            StructureSection("conflicts", _CONFLICTS, required=False, min_length=80),
        ],
        "detailed": [
            StructureSection("tldr", _TLDR, required=True, min_length=30),
            StructureSection("conclusion", _CONCLUSION, required=True, min_length=150),
            StructureSection("conflicts", _CONFLICTS, required=True, min_length=120),
            StructureSection("timeline", _TIMELINE, required=True, min_length=80),
            StructureSection("evidence", _EVIDENCE, required=True, min_length=200),
            StructureSection("confirmed", _CONFIRMED, required=True, min_length=80),
            StructureSection("references", _REFERENCES, required=False, min_length=50),
        ],
    }

    @classmethod
    def validate_structure(
        cls, markdown_text: str, detail_level: str = "balanced"
    ) -> Tuple[bool, List[str], Dict[str, Any]]:
        """Validate report structure against expected sections.

        Args:
            markdown_text: The markdown report text to validate
            detail_level: Expected detail level (compact/balanced/detailed)

        Returns:
            Tuple of (is_valid, issues, metadata)
            - is_valid: Whether the structure meets minimum requirements
            - issues: List of validation issue descriptions
            - metadata: Additional structure metadata
        """
        if not markdown_text or not markdown_text.strip():
            return False, ["empty_report"], {}

        sections = cls.REQUIRED_SECTIONS.get(
            detail_level, cls.REQUIRED_SECTIONS["balanced"]
        )
        issues: List[str] = []
        metadata: Dict[str, Any] = {
            "found_sections": [],
            "missing_required": [],
            "section_lengths": {},
            "has_citations": False,
            "citation_count": 0,
            "has_conflicts_section": False,
            "unsourced_number_warnings": [],
        }

        # Check for citations ([1] style or URL/domain mentions)
        citation_pattern = r"\[\d+\]"
        citations = re.findall(citation_pattern, markdown_text)
        metadata["has_citations"] = len(citations) > 0
        metadata["citation_count"] = len(citations)

        # Validate each required section
        for section in sections:
            match = re.search(section.pattern, markdown_text, re.MULTILINE)
            if match:
                metadata["found_sections"].append(section.name)
                if section.name == "conflicts":
                    metadata["has_conflicts_section"] = True
                section_start = match.end()
                next_section = re.search(r"\n##?\s+", markdown_text[section_start:])
                section_end = (
                    section_start + next_section.start()
                    if next_section
                    else len(markdown_text)
                )
                section_content = markdown_text[section_start:section_end].strip()
                section_length = len(section_content)
                metadata["section_lengths"][section.name] = section_length

                if section.required and section_length < section.min_length:
                    issues.append(
                        f"{section.name}_too_short:"
                        f"{section_length}<{section.min_length}"
                    )
            elif section.required:
                metadata["missing_required"].append(section.name)
                issues.append(f"missing_{section.name}")

        # Additional quality checks
        if detail_level in ["balanced", "detailed"] and metadata["citation_count"] < 3:
            # Also accept bare URLs as weak citations for live Chinese sources
            url_hits = re.findall(r"https?://[^\s\)]+", markdown_text)
            if len(url_hits) + metadata["citation_count"] < 3:
                issues.append("insufficient_citations")

        if detail_level == "detailed":
            # heading followed by content (not just an alias mention in prose)
            if not re.search(_REFERENCES.rstrip() + r"\s*\n", markdown_text):
                issues.append("missing_references_section")
            if "conflicts" not in metadata["found_sections"]:
                # Already covered by missing_required when required=True;
                # keep explicit flag for Round-5 gate reporting.
                issues.append("missing_conflicts_section")

        # Soft warning: numbers that look like claims without nearby source cue
        metadata["unsourced_number_warnings"] = cls._find_unsourced_numbers(
            markdown_text
        )

        is_valid = len(metadata["missing_required"]) == 0
        return is_valid, issues, metadata

    UNSOURCED_NUMBER_WARNING_CAP = 8
    # 诊断行/代码块/日期与列表序号都不是“待核数据”，先剔除再扫描
    _NON_CLAIM_LINE_RE = re.compile(
        r"Tokens?\b|Extracted Result|Pricing is disabled", re.IGNORECASE
    )
    _TIME_UNIT_RE = re.compile(r"^\s*(?:[年月日号点时秒岁]|世纪|周年|届|年代|[-/.]\d)")

    @classmethod
    def _claim_scan_text(cls, text: str) -> str:
        """剥离代码块（mermaid 标签）与线索追踪附录后的正文扫描文本。"""
        scan = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
        return re.split(r"(?m)^##\s*(?:线索追踪|Lead Trail)", scan, maxsplit=1)[0]

    @classmethod
    def _is_non_claim_number(cls, text: str, match: re.Match) -> bool:
        """列表序号、日期/年龄与诊断行中的数字不计入待核清单。"""
        line_start = text.rfind("\n", 0, match.start()) + 1
        line_end = text.find("\n", match.end())
        line = text[line_start : len(text) if line_end == -1 else line_end]
        if cls._NON_CLAIM_LINE_RE.search(line):
            return True
        if not text[line_start : match.start()].strip() and re.match(
            r"[.、)）]", text[match.end() : match.end() + 1]
        ):
            return True
        return bool(cls._TIME_UNIT_RE.match(text[match.end() : match.end() + 1]))

    @classmethod
    def _find_unsourced_numbers(cls, text: str) -> List[str]:
        """Heuristic: flag standalone figures lacking nearby source cues.

        Does not fail validation by itself — soft signal for manual review.
        Figures are matched with an ±80-char cue window: reports legitimately
        attribute sources at paragraph level, so this list is a review aid,
        not a gate.
        """
        warnings: List[str] = []
        scan_text = cls._claim_scan_text(text)
        source_cue = re.compile(
            r"(来源|据|报道|称|官方|http|www\.|\[\d+\]|根据)",
            re.IGNORECASE,
        )
        for match in re.finditer(
            r"(?<![\w./-])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?%?|"
            r"\d+\s*(?:万|亿|人|次|吨|美元|元))",
            scan_text,
        ):
            if cls._is_non_claim_number(scan_text, match):
                continue
            start = max(0, match.start() - 80)
            end = min(len(scan_text), match.end() + 80)
            window = scan_text[start:end]
            if not source_cue.search(window):
                warnings.append(match.group(1)[:40])
                if len(warnings) >= cls.UNSOURCED_NUMBER_WARNING_CAP:
                    break
        return warnings

    @classmethod
    def _normalize_near_miss_headings(cls, text: str) -> str:
        """Rename near-miss headings to canonical bilingual forms (Round 8).

        Surgical rename — no LLM rewrite. Only touches lines that look like
        section headings but miss the full Conflicts/Evidence/Timeline aliases.
        """
        renames = [
            (
                r"(?im)^(##?)\s*冲突\s*$",
                r"\1 冲突与不确定 / Conflicts & Uncertainties",
            ),
            (
                r"(?im)^(##?)\s*冲突与分歧\s*$",
                r"\1 冲突与不确定 / Conflicts & Uncertainties",
            ),
            (
                r"(?im)^(##?)\s*不确定(?:性|项)?\s*$",
                r"\1 冲突与不确定 / Conflicts & Uncertainties",
            ),
            (
                r"(?im)^(##?)\s*disagreements?\s*$",
                r"\1 Conflicts & Uncertainties",
            ),
            (
                r"(?im)^(##?)\s*时间(?:线)?\s*$",
                r"\1 时间线 / Timeline",
            ),
            (
                r"(?im)^(##?)\s*chronology\s*$",
                r"\1 Timeline",
            ),
            (
                r"(?im)^(##?)\s*证据清单\s*$",
                r"\1 Evidence",
            ),
            (
                r"(?im)^(##?)\s*supporting\s+materials?\s*$",
                r"\1 Evidence",
            ),
            (
                r"(?im)^(##?)\s*确认事项\s*$",
                r"\1 已确认 vs 未确认 / Confirmed vs Unconfirmed",
            ),
            (
                r"(?im)^(##?)\s*known\s+vs\.?\s+unknown\s*$",
                r"\1 Confirmed vs Unconfirmed",
            ),
        ]
        updated = text
        for pattern, repl in renames:
            updated = re.sub(pattern, repl, updated)
        return updated

    @classmethod
    def _inject_section_before_cues(
        cls,
        text: str,
        heading: str,
        body: str,
        cue_pattern: re.Pattern,
        *,
        heading_only: bool = False,
    ) -> str:
        """Insert ``heading`` (optionally + body) above the first cue paragraph."""
        parts = text.split("\n\n")
        for idx, para in enumerate(parts):
            p = para.strip()
            if not p or p.startswith("#"):
                continue
            if cue_pattern.search(p):
                block = heading if heading_only else f"{heading}\n\n{body}".strip()
                parts.insert(idx, block)
                return "\n\n".join(parts)
        return text.rstrip() + f"\n\n{heading}\n\n{body}\n"

    @classmethod
    def enforce_structure(
        cls,
        markdown_text: str,
        detail_level: str = "balanced",
        citations: Optional[List[str]] = None,
    ) -> str:
        """Attempt to fix structure issues by surgical heading inject/rename.

        Round 8: prefer rename near-miss headings and inject missing headings
        above existing body blocks over full regenerate or stub-at-end dumps.
        """
        if not markdown_text or not markdown_text.strip():
            return markdown_text

        text = cls._normalize_near_miss_headings(markdown_text.strip())
        sections_to_prepend: List[str] = []
        sections_to_append: List[str] = []

        is_valid, issues, metadata = cls.validate_structure(text, detail_level)

        if is_valid:
            return text

        if "tldr" in metadata["missing_required"]:
            first_para = cls._extract_first_paragraph(text)
            if first_para:
                sections_to_prepend.append(f"## TL;DR\n\n{first_para}\n\n")

        if "conclusion" in metadata["missing_required"]:
            conclusion_text = cls._extract_conclusion_text(text)
            if conclusion_text:
                sections_to_append.append(f"\n## Conclusion\n\n{conclusion_text}\n\n")

        # Round-5 hard gate: Conflicts must exist for detailed reports.
        if detail_level == "detailed" and (
            "conflicts" in metadata["missing_required"]
            or "missing_conflicts_section" in issues
        ):
            extracted = cls._extract_conflict_text(text)
            body = extracted or (
                "（结构自动补全）原文未单独列出冲突与不确定项；"
                "以下为从正文抽取的分歧线索，置信度低，需人工复核，"
                "不得视为已核实结论。\n\n"
                + (cls._extract_first_paragraph(text) or "暂无可用冲突摘录。")
            )
            heading = "## 冲突与不确定 / Conflicts & Uncertainties"
            conflict_cues = re.compile(
                r"(冲突|分歧|争议|不一致|相互矛盾|说法不一|"
                r"官方否认|尚未证实|无法确认|conflict|disagre|"
                r"unconfirm|disput)",
                re.IGNORECASE,
            )
            if extracted:
                text = cls._inject_section_before_cues(
                    text,
                    heading,
                    body,
                    conflict_cues,
                    heading_only=True,
                )
            else:
                sections_to_append.append(f"\n{heading}\n\n{body}\n\n")

        if detail_level == "detailed" and "timeline" in metadata["missing_required"]:
            date_cues = re.compile(
                r"\b20\d{2}[-/年.]\d{1,2}|timeline|时间线",
                re.IGNORECASE,
            )
            has_dated = any(
                date_cues.search(p)
                for p in text.split("\n\n")
                if p.strip() and not p.strip().startswith("#")
            )
            if has_dated:
                text = cls._inject_section_before_cues(
                    text,
                    "## 时间线 / Timeline",
                    "",
                    date_cues,
                    heading_only=True,
                )
            else:
                sections_to_append.append(
                    "\n## 时间线 / Timeline\n\n"
                    "（结构自动补全）原文缺少独立时间线章节；"
                    "请勿将此占位视为已核实时间表。\n\n"
                )

        if detail_level == "detailed" and "confirmed" in metadata["missing_required"]:
            sections_to_append.append(
                "\n## 已确认 vs 未确认 / Confirmed vs Unconfirmed\n\n"
                "- **已确认**：见正文有明确多源支持的陈述（若无则标注“无”）。\n"
                "- **未确认**：单方宣称、无法交叉核验或来源冲突的陈述。\n"
                "（结构自动补全占位；禁止据此伪造确定性。）\n\n"
            )

        # Round-6/8: Evidence auto-repair — append dedicated section (do not
        # inject mid-Conflicts; source cues often live inside other sections).
        if detail_level in ("balanced", "detailed") and (
            "evidence" in metadata["missing_required"]
        ):
            evidence_body = cls._extract_evidence_text(text, citations)
            if evidence_body:
                sections_to_append.append(
                    f"\n## Evidence（证据含来源与日期）\n\n{evidence_body}\n\n"
                )

        # references is required=False, so it never lands in missing_required;
        # only the "no References heading at all" case needs repairing.
        if (
            detail_level == "detailed"
            and "missing_references_section" in issues
            and not re.search(_REFERENCES, text)
        ):
            if citations:
                refs_body = "\n".join(
                    f"{i + 1}. {cite}" for i, cite in enumerate(citations)
                )
            else:
                refs_body = "（结构自动补全）请参见正文内联来源与链接。"
            sections_to_append.append(f"\n## References\n\n{refs_body}\n\n")

        if sections_to_prepend or sections_to_append:
            text = "".join(sections_to_prepend) + text + "".join(sections_to_append)

        return text

    @staticmethod
    def _extract_first_paragraph(text: str) -> str:
        """Extract first substantial paragraph as TL;DR candidate."""
        lines = text.strip().split("\n")
        for line in lines:
            line = line.strip()
            if line and not line.startswith("#") and len(line) > 50:
                return line
        return ""

    @staticmethod
    def _extract_conclusion_text(text: str) -> str:
        """Extract conclusion-like content from text."""
        conclusion_patterns = [
            r"(?i)in conclusion[,:\s]+(.*?)(?:\n\n|\Z)",
            r"(?i)to sum(marize|mary)[,:\s]+(.*?)(?:\n\n|\Z)",
            r"(?i)therefore[,:\s]+(.*?)(?:\n\n|\Z)",
            r"(?i)综上[，,:\s]+(.*?)(?:\n\n|\Z)",
        ]

        for pattern in conclusion_patterns:
            match = re.search(pattern, text, re.DOTALL)
            if match:
                conclusion = match.group(1).strip()
                if len(conclusion) > 50:
                    return conclusion[:500]

        paras = [p.strip() for p in text.split("\n\n") if p.strip()]
        for para in reversed(paras):
            if not para.startswith("#") and len(para) > 100:
                return para[:500]

        return ""

    @staticmethod
    def _extract_conflict_text(text: str) -> str:
        """Pull disagreement-like sentences for Conflicts auto-fix."""
        cues = re.compile(
            r"(冲突|分歧|争议|不一致|相互矛盾|说法不一|"
            r"官方否认|尚未证实|无法确认|conflict|disagre|"
            r"unconfirm|disput)",
            re.IGNORECASE,
        )
        hits: List[str] = []
        for para in text.split("\n\n"):
            p = para.strip()
            if p and not p.startswith("#") and cues.search(p):
                hits.append(p[:400])
            if len(hits) >= 3:
                break
        return "\n\n".join(hits)

    @staticmethod
    def _extract_evidence_text(text: str, citations: Optional[List[str]] = None) -> str:
        """Build an Evidence body from inlined sources / URLs / citations.

        Returns empty string when there is nothing source-like to promote —
        avoids inventing evidence just to pass the heading gate.
        """
        source_cue = re.compile(
            r"(https?://|www\.|\[\d+\]|"
            r"来源|据.{0,24}(报道|称|声明|通报|披露)|"
            r"路透|美联社|法新|新华|半岛|BBC|Reuters|AP\b|"
            r"官方[声声]?明?|通讯社)",
            re.IGNORECASE,
        )
        hits: List[str] = []
        for para in text.split("\n\n"):
            p = para.strip()
            if not p or p.startswith("#"):
                continue
            # Skip lead-trail / metrics appendix that are not report evidence
            if "线索追踪" in p or "Lead Trail" in p:
                continue
            if source_cue.search(p):
                hits.append(p[:500])
            if len(hits) >= 5:
                break

        if citations:
            cite_lines = "\n".join(f"- {c}" for c in citations[:8] if str(c).strip())
            if cite_lines:
                hits.append("引用列表：\n" + cite_lines)

        if not hits:
            # Weak fallback: collect bare URLs scattered in the report
            urls = re.findall(r"https?://[^\s\)\]\>\"']+", text)
            uniq: List[str] = []
            for u in urls:
                if u not in uniq:
                    uniq.append(u)
                if len(uniq) >= 5:
                    break
            if uniq:
                hits.append(
                    "（结构自动补全）正文未设独立 Evidence 标题；"
                    "以下从来源链接整理，需人工复核：\n"
                    + "\n".join(f"- {u}" for u in uniq)
                )

        if not hits:
            return ""

        header = (
            "（结构自动补全）原文缺少独立 Evidence / 证据 标题；"
            "以下为从正文抽取的来源线索，禁止据此伪造确定性。\n\n"
        )
        body = header + "\n\n".join(hits)
        # Ensure detailed min_length (200) can be satisfied when content exists
        return body[:2500]

    @classmethod
    def get_structure_template(cls, detail_level: str = "balanced") -> str:
        """Get a template for the expected report structure.

        Args:
            detail_level: The detail level (compact/balanced/detailed)

        Returns:
            Markdown template string
        """
        if detail_level == "compact":
            return """## TL;DR / 结论（标明置信度）

[核心结论，2-3句话；写明置信度：高/中/低]

## Conclusion

[简要总结主要发现和结论；不确定处显式标注]
"""
        elif detail_level == "balanced":
            return """## TL;DR / 结论（标明置信度）

[核心结论，2-3句话；写明置信度]

## Conclusions

[详细结论和主要发现]

## 冲突与不确定 / Conflicts & Uncertainties

[各方说法冲突、口径差异、未核实点 — 若无冲突也需写明“未发现重大冲突”]

## Evidence

[支撑证据，引用来源与日期 [1], [2] 等]

## References

1. [来源1]
2. [来源2]
"""
        else:  # detailed
            return """## TL;DR / 结论（标明置信度）

[核心结论，2-3句话；必须标明置信度：高/中/低及理由]

## Conclusions

[详细结论和主要发现，包含背景和 context]

## 冲突与不确定 / Conflicts & Uncertainties

[必填] 多方说法对照表：谁说了什么、冲突点、可能的口径原因。禁止消抹分歧。

## 时间线 / Timeline

[按绝对日期排列的事件时间线]

## Evidence

[详细支撑证据，含来源与日期 [1], [2] 等；数字必须带来源]
（中文等价标题亦可：`## 证据` / `## 证据与来源` / `## 临床证据`）

## 已确认 vs 未确认 / Confirmed vs Unconfirmed

- **已确认**：…
- **未确认 / 无法确认**：…

## Gaps in Knowledge

[研究中发现的知识空白]

## 内容分析 / Content Analysis

[可选] 角色/主张/时间线要点；有助于舆情与谣言交叉验证阅读。

## 关系拓扑 / Relationship Map

[可选] 用 mermaid flowchart 表达 主张→证据/反驳→缺口（无结构时可不生成）。

## References

1. [来源1 - 完整引用信息与日期]
2. [来源2 - 完整引用信息与日期]
"""
