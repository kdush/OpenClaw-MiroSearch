# Deep Efficiency Knobs (Round 6–8)

Pragmatic caps for `research_intensity=deep` that cut wall-clock waste
without gutting multi-source cross-verification. Complements
[`RESEARCH_INTENSITY.md`](./RESEARCH_INTENSITY.md).

## Goals

| Win | What it does | Default (deep) |
|-----|--------------|----------------|
| **Parallel tool calls** | When the LLM issues ≥2 non-subagent tools in one turn (e.g. multi-search), execute them concurrently via `asyncio.gather` | `parallel_tool_calls=true` |
| **Early-stop** | Stop extra lead follow-ups once ≥N independent source domains exist **and** min search rounds are met (Conflicts can be filled) | `deep_early_stop_on_agreement=true`, `min_sources=2` |
| **Exit after early-stop (R7/R8)** | Cap remaining main-loop LLM turns after early-stop; nudge once then force summary | `deep_exit_on_early_stop=true`, `post_turns=1` (was 2) |
| **Scrape budget** | Prefer search snippets; full-page scrape only until hard cap; further scrapes return a skip message | `max_scrape_per_task=8` |
| **Clue Top-K** | Deep follow only top 1–2 leads (`max_lead_follow_ups`) | `2` |
| **Single summary (R7)** | One structured final-summary LLM pass; Evidence repaired locally via `enforce_structure` | `max_final_answer_retries=1` |
| **Oneshot skeleton (R8)** | Fill a strict markdown skeleton once; skip expand rewrite (deep also folds the separate verification LLM pass into the skeleton) | `oneshot_final_report=true` (deep/standard) |
| **Summary context cap (R8)** | Final summary keeps last N tool dumps only; strip omitted stubs; cap `summary_max_tokens` | `summary_keep_tool_result=2`, `summary_max_tokens_cap=4096` |
| **Surgical structure repair (R8)** | Rename near-miss headings / inject missing heading above body — not full regenerate | local `enforce_structure` |
| **Timeout fail-fast (R7)** | On HTTP/API timeout: one shorter-context degrade retry, then raise (no identical 4×90s retries) | `llm.timeout_fail_fast=true` |

## Hydra / agent knobs

Set on `agent.*` (API `profile_resolver` path) or `agent.main_agent.*` (CLI):

```yaml
# Deep efficiency (also auto-appended by apply_intensity_adjustments for deep)
++agent.max_lead_follow_ups=2
++agent.max_scrape_per_task=8
++agent.deep_early_stop_on_agreement=true
++agent.deep_early_stop_min_sources=2
++agent.parallel_tool_calls=true
# Round 7–8 LLM-path / final draft
++agent.deep_exit_on_early_stop=true
++agent.deep_post_early_stop_turns=1
++agent.max_final_answer_retries=1
++agent.oneshot_final_report=true
++agent.summary_keep_tool_result=2
++agent.summary_max_tokens_cap=4096
++llm.summary_max_tokens=4096
```

| Knob | Type | Notes |
|------|------|-------|
| `max_lead_follow_ups` | int | Clue Top-K. Deep default **2** (was 3). |
| `max_scrape_per_task` | int | `0` = unlimited. Deep **8**, standard **12**, light **4**. |
| `deep_early_stop_on_agreement` | bool | Default **true** when intensity=`deep`. |
| `deep_early_stop_min_sources` | int | Independent domains required (default **2**). |
| `parallel_tool_calls` | bool | Default **true**. Sub-agent calls stay sequential. |
| `deep_exit_on_early_stop` | bool | Round 7. Default **true** for deep. |
| `deep_post_early_stop_turns` | int | Extra LLM turns after early-stop before force-summary (default **1**, was 2). |
| `max_final_answer_retries` | int | Deep default **1** (was 3). |
| `oneshot_final_report` | bool | Round 8. Default **true** for deep/standard, **false** for light. Skeleton fill; no expand rewrite. Only deep additionally skips the separate verification LLM pass. |
| `summary_keep_tool_result` | int | Tool dumps kept for `final_summary` only (default **2** deep). Research turns still use `keep_tool_result`. |
| `summary_max_tokens_cap` | int | Soft cap applied to `llm_client.summary_max_tokens` (default **4096** deep). |

### LLM client knobs (`conf/llm/glm-flash.yaml`)

| Knob | Default | Notes |
|------|---------|-------|
| `max_retries` | **2** (glm-flash) | App-level retries for non-timeout errors. |
| `retry_wait_seconds` | **2.0** | Fixed wait (not exponential). |
| `timeout_fail_fast` | **true** | Timeout → degrade once → raise. |
| `timeout_degrade_keep_tool_results` | **2** | Keep-last-N tool results on degrade retry. |

Env overrides: `LLM_TIMEOUT_FAIL_FAST`, `LLM_TIMEOUT_DEGRADE_KEEP_TOOL_RESULTS`,
`LLM_MAX_RETRIES`, `LLM_RETRY_WAIT_SECONDS`, `LLM_HTTP_TIMEOUT_SECONDS`.

## Behaviour notes

1. **Early-stop does not skip the first verification / search budget.** It only
   suppresses *additional* lead-trail injections once agreement + rounds are met.
2. **Round 7/8 exit** turns early-stop into a hard turn cap: after
   `early_stop_turn + post_turns`, the orchestrator nudges “write the report”
   once, then breaks into final summary — avoiding full `max_turns=12` LLM burn.
3. **Round 8 oneshot** replaces the “禁止压缩 / 12000 chars” detailed overlay with a
   single skeleton-fill prompt. Structure gaps are patched locally
   (`enforce_structure`: rename near-miss headings, inject Timeline/Conflicts
   headings above existing body). The skeleton only serves
   `research_report_mode + output_detail_level=detailed`; elsewhere
   (`balanced`/`compact`) the “summary too short → expand” retry is kept, so
   enabling oneshot for `standard` cannot silently drop that guard.
   Expand-rewrite is skipped only where the skeleton is active; the separate
   verification LLM pass is folded into the skeleton for `deep` only, so
   `standard` keeps the high-model verification round.
4. **Summary context** uses `summary_keep_tool_result` (not research
   `keep_tool_result`) and strips `"Tool result is omitted…"` stubs so the final
   call does not re-summarize condensed placeholders.
5. **Scrape skip is soft.** The model still receives a clear message to rely on
   snippets; conflict-critical pages should be scraped *before* the cap fills.
6. **Parallelism is turn-local.** Tools across different turns remain sequential
   (LLM must request the next batch).
7. **Quality gates unchanged.** Conflicts / Timeline / Evidence / Confirmed
   hard gates still apply for `detailed` reports.
8. **Timeout metrics:** `timeout_count` now increments for OpenAI/httpx timeouts
   (not only `asyncio.TimeoutError`). Also: `http_timeout_count`,
   `wall_timeout_count`, `llm_retry_count`, `summary_passes`,
   `early_stop_triggered`, `early_stop_turn`.
9. **Search-confidence short-circuit.** When a search tool reports
   `confidence.enabled && passed=yes`, the orchestrator treats the
   cross-verification requirement as met and stops appending "keep searching"
   guidance for missing high-confidence sources; the `min_search_rounds` floor
   still applies. Observable via `retrieval_confidence_passed`.

## Code map

| Area | File |
|------|------|
| Knob resolution | `apps/miroflow-agent/src/core/deep_efficiency.py` |
| Lead Top-K default | `apps/miroflow-agent/src/core/lead_tracker.py` |
| Parallel + scrape + early-stop + exit | `apps/miroflow-agent/src/core/orchestrator.py` |
| Oneshot summary + retention + metrics | `apps/miroflow-agent/src/core/answer_generator.py` |
| Surgical structure repair | `apps/miroflow-agent/src/io/report_structure.py` |
| Timeout fail-fast / degrade | `apps/miroflow-agent/src/llm/providers/openai_client.py` |
| Metrics fields | `apps/miroflow-agent/src/logging/task_logger.py` (`RunMetrics`) |
| API intensity wiring | `apps/api-server/services/profile_resolver.py` (`apply_intensity_adjustments`) |

## Measuring

Compare wall-clock plus:

- `scrape_count` / `follow_up_searches` / `search_rounds` /
  `retrieval_confidence_passed`
- `timeout_count` / `http_timeout_count` / `llm_retry_count`
- `summary_passes` / `early_stop_triggered` / `early_stop_turn`
- stage timings: `answer_generator.llm_call.main` vs `llm.request.main` gap
  (retry burn) and `llm.request.final_summary`
