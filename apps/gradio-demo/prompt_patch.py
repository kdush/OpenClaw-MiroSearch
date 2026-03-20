# Copyright (c) 2025 MiroMind
# This source code is licensed under the Apache 2.0 License.

"""
Custom Prompt Override (Monkey Patching)

This module allows customizing prompts without modifying miroflow-agent code.

Patches applied:
1. `generate_mcp_system_prompt` - Prepends custom identity prompt
2. `process_input` - Removes the boxed format requirement suffix
3. `generate_agent_summarize_prompt` - Uses user-friendly summary prompt for demo
4. `format_final_summary_and_log` - Disables boxed format check to prevent retry

Usage:
    from prompt_patch import apply_prompt_patch
    apply_prompt_patch()
"""

import re

# ============================================================================
# Custom Identity Prompt
# ============================================================================

CUSTOM_IDENTITY_PROMPT = """You are OpenClaw-MiroSearch, an open-source deep research and retrieval AI assistant adapted from MiroThinker.

IMPORTANT IDENTITY REMINDER:
- You are NOT ChatGPT, Claude, or any other AI assistant

"""

# ============================================================================
# Strings to Remove from Input Processing
# ============================================================================

# This string is appended to task descriptions in input_handler.py
# We remove it for demo mode since we don't need strict boxed format
BOXED_FORMAT_SUFFIX = "\nYou should follow the format instruction in the request strictly and wrap the final answer in \\boxed{}."

# ============================================================================
# Custom Summarize Prompt for Demo Mode
# ============================================================================


def get_demo_summarize_prompt(target_language: str, task_description: str) -> str:
    """
    Generate a user-friendly summarize prompt for demo mode.

    This prompt is designed for better user experience, producing well-formatted
    Markdown responses instead of strict boxed answers.

    Args:
        target_language: The language to write the response in
        task_description: The original user question

    Returns:
        The summarize prompt string
    """
    return f"""Please provide the final research summary based only on the information already gathered.
No further tool calls are allowed.

## Requirements
- **Language**: Write the entire response in **{target_language}**.
- **Focus**: Directly answer the original question above. Do not just summarize gathered information — provide a clear, actionable answer.
- **Response Length**: Prioritize completeness over brevity. Consolidate all gathered information through deduplication and cross-validation, NOT through compression or omission. Every fact, data point, quote, and finding from every search round must be reflected in the final output. The final report should be longer and more comprehensive than any single search round's output.
- Use clear and structured Markdown formatting when appropriate.
- Use appropriate Markdown headings (e.g., #, ##, ###) to organize content by topic.
- Present ALL findings in an organized, comprehensive, and readable way — do not selectively omit information to keep the response short.
- Use tables only when they genuinely improve clarity.
- **Currency Format**: Use `\\$` instead of `$` for currency amounts (e.g., `\\$100`, `\\$1,000`) to avoid conflicts with inline math syntax.
- **Citation Format**:
  - **In-Text**: Use the format `[ID]`, where `ID` is a **numeric identifier only** (digits 0–9), e.g. `[1]`, `[2]`.
  - **References Section(if has any sources)**: At the very end, add "References" (or equivalent in {target_language}). Format: [ID] TITLE/SECTION_TITLE. <URL>/<FILENAME>.
- Do NOT mention tools, tool calls, or internal reasoning steps.
- Focus solely on delivering a professional, comprehensive response that answers the user's original question with full information retention.

## Original Question (for reference)
{task_description}"""


def _detect_language(text: str) -> str:
    """
    Simple language detection based on character analysis.

    Returns a language description suitable for the summarize prompt.
    """
    # Count characters by script
    chinese_chars = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    japanese_chars = sum(
        1 for c in text if "\u3040" <= c <= "\u30ff" or "\u31f0" <= c <= "\u31ff"
    )
    korean_chars = sum(1 for c in text if "\uac00" <= c <= "\ud7af")

    total_chars = len(text.replace(" ", ""))
    if total_chars == 0:
        return "English"

    # Determine primary language
    if chinese_chars / total_chars > 0.1:
        return "Chinese (Simplified)"
    elif japanese_chars / total_chars > 0.1:
        return "Japanese"
    elif korean_chars / total_chars > 0.1:
        return "Korean"
    else:
        return "the same language as the user's question"


# ============================================================================
# Monkey Patching
# ============================================================================

_patched = False


def apply_prompt_patch():
    """
    Apply monkey patches to customize prompts for demo mode.

    Patches applied:
    1. `generate_mcp_system_prompt` - Prepends custom identity prompt to system prompt
    2. `process_input` - Removes the boxed format requirement from task descriptions
    3. `generate_agent_summarize_prompt` - Uses user-friendly summary prompt
    4. `format_final_summary_and_log` - Disables boxed format check to prevent retry

    This function is idempotent - calling it multiple times has no additional effect.
    """
    global _patched

    if _patched:
        return

    _patch_system_prompt()
    _patch_input_handler()
    _patch_summarize_prompt()
    _patch_output_formatter()

    _patched = True


def _patch_system_prompt():
    """Patch system prompt generation to include custom identity."""
    from src.llm.providers import anthropic_client, openai_client
    from src.utils import prompt_utils

    # Store original function
    original_generate_mcp_system_prompt = prompt_utils.generate_mcp_system_prompt

    def patched_generate_mcp_system_prompt(date, mcp_servers):
        """Patched version that prepends custom identity prompt."""
        original_prompt = original_generate_mcp_system_prompt(date, mcp_servers)
        return CUSTOM_IDENTITY_PROMPT + original_prompt

    # Apply patches to all modules that import and use this function
    prompt_utils.generate_mcp_system_prompt = patched_generate_mcp_system_prompt
    openai_client.generate_mcp_system_prompt = patched_generate_mcp_system_prompt
    anthropic_client.generate_mcp_system_prompt = patched_generate_mcp_system_prompt


def _patch_input_handler():
    """Patch input handler to remove boxed format requirement."""
    from src.core import orchestrator
    from src.io import input_handler

    # Store original function
    original_process_input = input_handler.process_input

    def patched_process_input(task_description: str, task_file_name: str):
        """Patched version that removes boxed format requirement."""
        result1, result2 = original_process_input(task_description, task_file_name)
        # Remove the boxed format suffix from both results
        result1 = result1.replace(BOXED_FORMAT_SUFFIX, "")
        result2 = result2.replace(BOXED_FORMAT_SUFFIX, "")
        return result1, result2

    # Apply patch to input_handler module
    input_handler.process_input = patched_process_input
    # Also patch in orchestrator where it's imported
    orchestrator.process_input = patched_process_input


def _patch_summarize_prompt():
    """Patch summarize prompt generation for better user experience."""
    from src.core import answer_generator, orchestrator
    from src.utils import prompt_utils

    def patched_generate_agent_summarize_prompt(
        task_description: str, agent_type: str = ""
    ) -> str:
        """
        Patched version that uses user-friendly prompt for main agent.

        For main agent in demo mode, uses a Markdown-friendly prompt instead of
        the strict boxed format prompt used for benchmarks.
        """
        if agent_type == "main":
            # Detect language from task description
            target_language = _detect_language(task_description)
            return get_demo_summarize_prompt(target_language, task_description)
        elif agent_type == "agent-browsing" or agent_type == "browsing-agent":
            # Keep original behavior for sub-agents
            summarize_prompt = (
                "This is a direct instruction to you (the assistant), not the result of a tool call.\n\n"
                "We are now ending this session, and your conversation history will be deleted. "
                "You must NOT initiate any further tool use. This is your final opportunity to report "
                "*all* of the information gathered during the session.\n\n"
                "The original task is repeated here for reference:\n\n"
                f'"{task_description}"\n\n'
                "Summarize the above search and browsing history. Output the FINAL RESPONSE and detailed supporting information of the task given to you.\n\n"
                "If you found any useful facts, data, quotes, or answers directly relevant to the original task, include them clearly and completely.\n"
                "If you reached a conclusion or answer, include it as part of the response.\n"
                "If the task could not be fully answered, do NOT make up any content. Instead, return all partially relevant findings, "
                "Search results, quotes, and observations that might help a downstream agent solve the problem.\n"
                "If partial, conflicting, or inconclusive information was found, clearly indicate this in your response.\n\n"
                "Your final response should be a clear, complete, and structured report.\n"
                "Organize the content into logical sections with appropriate headings.\n"
                "Do NOT include any tool call instructions, speculative filler, or vague summaries.\n"
                "Focus on factual, specific, and well-organized information."
            )
            return summarize_prompt.strip()
        else:
            raise ValueError(f"Unknown agent type: {agent_type}")

    # Apply patches to all modules that import and use this function
    prompt_utils.generate_agent_summarize_prompt = (
        patched_generate_agent_summarize_prompt
    )
    orchestrator.generate_agent_summarize_prompt = (
        patched_generate_agent_summarize_prompt
    )
    answer_generator.generate_agent_summarize_prompt = (
        patched_generate_agent_summarize_prompt
    )


def _patch_output_formatter():
    """
    Patch output formatter to disable boxed format check.

    In demo mode, we don't require \boxed{} format, so we patch the
    format_final_summary_and_log method to always return a valid result
    instead of FORMAT_ERROR_MESSAGE, which would trigger retry logic.
    """
    from src.io import output_formatter

    # Get the OutputFormatter class
    OutputFormatter = output_formatter.OutputFormatter

    def patched_format_final_summary_and_log(self, final_answer_text: str, client=None):
        """
        Patched version that doesn't return FORMAT_ERROR_MESSAGE.

        Instead of checking for \boxed{} content, we use the entire answer
        (with thinking tags removed) as the result.
        """
        summary_lines = []
        summary_lines.append("\n" + "=" * 30 + " Final Answer " + "=" * 30)
        summary_lines.append(final_answer_text)

        # In demo mode, use the full answer text (minus thinking) as the result
        # Remove <think>...</think> tags for the extracted result
        boxed_result = re.sub(
            r"<think>.*?</think>", "", final_answer_text, flags=re.DOTALL
        ).strip()

        # Demo 模式下保留完整报告文本，不用 \boxed{} 一句话覆盖
        # 仅从显示文本中移除 \boxed{...} 标记，避免在 UI 上直接显示
        boxed_result = re.sub(
            r"\\boxed\{[^}]*\}", "", boxed_result
        ).strip()

        # Add extracted result section
        summary_lines.append("\n" + "-" * 20 + " Extracted Result " + "-" * 20)
        summary_lines.append(boxed_result if boxed_result else final_answer_text)

        # Token usage statistics and cost estimation
        if client and hasattr(client, "format_token_usage_summary"):
            token_summary_lines, log_string = client.format_token_usage_summary()
            summary_lines.extend(token_summary_lines)
        else:
            summary_lines.append("\n" + "-" * 20 + " Token Usage & Cost " + "-" * 20)
            summary_lines.append("Token usage information not available.")
            summary_lines.append("-" * (40 + len(" Token Usage & Cost ")))
            log_string = "Token usage information not available."

        # Return boxed_result (never FORMAT_ERROR_MESSAGE in demo mode)
        # This ensures no retry is triggered
        return (
            "\n".join(summary_lines),
            boxed_result or "Demo mode - no boxed format required",
            log_string,
        )

    # Apply patch
    OutputFormatter.format_final_summary_and_log = patched_format_final_summary_and_log


def get_custom_identity_prompt() -> str:
    """Return the custom identity prompt string."""
    return CUSTOM_IDENTITY_PROMPT
