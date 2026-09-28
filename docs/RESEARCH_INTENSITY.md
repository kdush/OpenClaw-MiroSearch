# Research Intensity Control

## Overview

Research intensity controls the depth and thoroughness of research tasks by presetting budgets for search rounds, result quantities, and clue-chasing behavior. It composes with existing `mode` and `search_profile` parameters without replacing them.

## Intensity Levels

### `light` - Fast, focused research

**Use cases:**
- Quick fact lookups
- Simple questions with clear answers
- Cost-sensitive queries
- Time-critical research

**Characteristics:**
- Reduced search rounds (~70% of base)
- Fewer max_turns (~70% of base)
- Minimal follow-up searches
- Lead tracking disabled
- Optimized for speed over completeness

**Budget presets:**
- Max turns: 70% of mode baseline (min 5)
- Verification rounds: 75% of requested (min 1)
- Lead tracking: OFF
- Scrape budget: `max_scrape_per_task=4`

### `standard` (default) - Balanced research

**Use cases:**
- General research tasks
- Default behavior
- Balanced cost/quality trade-off

**Characteristics:**
- Baseline behavior from mode configuration
- No adjustments applied
- Standard search depth
- Moderate follow-up behavior

**Budget presets:**
- Max turns: Mode baseline
- Verification rounds: As requested
- Lead tracking: OFF unless `enable_lead_tracking` is set explicitly
- Scrape budget: `max_scrape_per_task=12`

### `deep` - Thorough, comprehensive research

**Use cases:**
- Critical fact-checking
- Comprehensive research reports
- Complex multi-faceted questions
- High-stakes decisions

**Characteristics:**
- Extended search rounds (+30-50%)
- More max_turns (+50%)
- Encouraged follow-up searches
- Lead tracking enabled by default
- Optimized for completeness over speed

**Budget presets:**
- Max turns: 150% of mode baseline (max 30)
- Verification rounds: 130% of requested (max 8)
- Lead tracking: ON (Phase 4)
- Scrape budget: `max_scrape_per_task=8` (follows clues instead of scraping broadly)
- **Round 6 efficiency caps** (see [`DEEP_EFFICIENCY.md`](./DEEP_EFFICIENCY.md)):
  clue Top-K=`max_lead_follow_ups=2`, `max_scrape_per_task=8`,
  early-stop on ≥2 independent sources, parallel tool calls ON

## API Usage

### Request Parameter

```json
{
  "query": "Research question",
  "mode": "verified",
  "search_profile": "parallel-trusted",
  "research_intensity": "deep"
}
```

### Default Behavior

If `research_intensity` is omitted or null, defaults to `standard`.

### Environment Variable Override

```bash
DEFAULT_RESEARCH_INTENSITY=deep
```

## Implementation Details

### How Intensity Affects Behavior

1. **Max Turns Adjustment**
   - `light`: `max_turns = max(5, baseline_turns * 0.7)`
   - `standard`: `max_turns = baseline_turns`
   - `deep`: `max_turns = min(30, baseline_turns * 1.5)`

2. **Verification Rounds Adjustment** (verified mode only)
   - `light`: `rounds = max(1, requested_rounds * 0.75)`
   - `standard`: `rounds = requested_rounds`
   - `deep`: `rounds = min(8, requested_rounds * 1.3)`

3. **Lead Tracking** (Phase 4)
   - `light`: Explicitly disabled via `agent.enable_lead_tracking=false`
   - `standard`: Mode default
   - `deep`: Explicitly enabled via `agent.enable_lead_tracking=true`

### Interaction with Mode

Intensity adjusts the execution budget AFTER mode-specific settings are applied:

```
Final Config = Base Settings + Mode Overrides + Intensity Adjustments
```

Example flow for `mode=verified` + `intensity=deep`:

1. Base settings: `max_turns=10`, `min_search_rounds=3`
2. Mode overrides: `max_turns=14`, `agent=demo_verified_search`
3. Intensity adjustments: `max_turns=21` (14 * 1.5), `min_search_rounds=4` (3 * 1.3)

### Metrics Impact

All intensity-adjusted parameters are captured in `effective_config` and recorded in `RunMetrics.effective_config`, enabling analysis of:

- Light vs. standard vs. deep monotonicity: `light.search_rounds ≤ standard.search_rounds ≤ deep.search_rounds`
- Cost efficiency per intensity level
- Quality/completeness vs. speed trade-offs

## Backward Compatibility

- Existing requests without `research_intensity` continue to work (default `standard`)
- No breaking changes to existing parameters
- Intensity is additive, not replacement
- Cache keys include intensity to prevent mismatched result reuse

## Validation

Valid values: `"light"`, `"standard"`, `"deep"`

Invalid values are rejected with HTTP 422:

```json
{
  "detail": "research_intensity must be one of ('light', 'standard', 'deep'), got 'invalid'"
}
```

## Future Extensions

Potential enhancements:

1. **Custom intensity profiles**: Allow users to define custom budget presets
2. **Auto-intensity**: Automatically select intensity based on query complexity
3. **Adaptive intensity**: Dynamically adjust intensity based on intermediate results
4. **Per-source intensity**: Different intensity for different search providers

## Examples

### Light Intensity - Quick Fact

```bash
curl -X POST http://localhost:8090/v1/research \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Capital of France?",
    "mode": "balanced",
    "research_intensity": "light",
    "output_detail_level": "compact"
  }'
```

Expected: ≤2 search rounds, <10 turns, quick answer

### Deep Intensity - Comprehensive Research

```bash
curl -X POST http://localhost:8090/v1/research \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Comprehensive analysis of quantum computing advancements in 2024",
    "mode": "verified",
    "verification_min_search_rounds": 4,
    "research_intensity": "deep",
    "output_detail_level": "detailed"
  }'
```

Expected: ≥5 search rounds, 15-25 turns, lead tracking enabled, detailed report

## Testing

See `apps/api-server/tests/test_research_quality_acceptance.py` for acceptance tests covering:

- A. Simple fact, light+compact
- B. Contested numeric, verified+deep+detailed
- E. Same query, compact vs detailed

Metrics validation:
```python
assert metrics["effective_config"]["research_intensity"] == "deep"
assert metrics["search_rounds"] >= standard_metrics["search_rounds"]
```
