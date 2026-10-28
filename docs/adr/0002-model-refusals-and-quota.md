# ADR-0002: Gemini 2.5 Flash-Lite with a fallback chain, layered refusals, response cache

Status: accepted · Date: 2026-10-10

## Context

- The LLM provider is given: the project has a Google AI Studio key.
  It is a **free-tier** key. Measured limits: `generate_content_free_tier_requests`
  **limit: 20 per day per model** (gemini-2.5-flash-lite, from the 429 error body), a low
  per-minute limit on gemini-2.5-flash, and a per-minute embedding limit where one
  52-document batch counts as 52 requests. The owner will not attach a card.
- The model must answer only from the 5 retrieved recipes, return a machine-readable
  decision, and never invent citations.
- SPEC targets: p50 ≤ 3 s, p95 ≤ 8 s server side; ≤ $1.00 per 1,000 questions at list price.

## Criteria

1. Golden-set pass rate and correct `refused` flag (SPEC AC2).
2. Latency p50/p95 on the free tier (what visitors of the demo see).
3. Cost per 1,000 questions at list price (what production would pay).
4. Availability under a 20-requests-per-day quota.

## Measurements

Same golden set (15 questions), same prompt family, one run per model on 2026-10-10
(`evals/run_evals.py`; quota allows ~1 run per model per day). Cost uses Google list
prices per 1M tokens: 2.5-flash-lite $0.10 in / $0.40 out, 3.1-flash-lite $0.25 / $1.50,
3.5-flash-lite $0.30 / $2.50, 2.5-flash $0.30 / $2.50 (output includes thinking tokens).

| Model | Golden pass | `refused` correct | LLM latency p50 / p95 | Tokens in / out per call | $ / 1,000 questions* |
|---|---|---|---|---|---|
| **gemini-2.5-flash-lite** | 13/15 | 14/15 | 1.27 s / 2.49 s | 3,343 / 213 | **$0.42** |
| gemini-3.5-flash-lite | 14/15 | 14/15 | 1.36 s / 2.55 s | 3,189 / 226 | $1.52 |
| gemini-3.1-flash-lite | 14/15 | 15/15 | 9.81 s / 18.5 s | 3,102 / 167 | $1.03 |
| gemini-2.5-flash | 11/15 | 12/15 | 1.66 s / 8.21 s | 3,345 / 581 | $2.46 |

\* per question that reaches the model: tokens × list price. Refusals decided before the
model (safety pre-check, empty filter result) and cache hits cost $0; the query embedding
adds ~$0.000002.

**Production run after the third hardening round** (2026-10-10 13:28 UTC): **15/15
golden, 4/4 contract**, p50 1.9 s / p95 3.6 s, fallback model, $1.84 / 1,000 at its list
price. Report: `evals/results/2026-10-10T1328Z-production.json`. The primary model
is not yet measured in production (its free quota was used up the same day).

**Production run after the second hardening round** (2026-10-10 13:10 UTC, golden set
with allergen exclusion, three marinara versions and a similar-dish case): **15/15 golden,
4/4 contract**, p50 1.5 s / p95 3.5 s, again served by the fallback model.
Report: `evals/results/2026-10-10T1310Z-production.json`.

**Production run after the first fixes** (2026-10-10 12:03 UTC, `https://recipe-qa.vercel.app`,
report `evals/results/2026-10-10T1203Z-production.json`): **15/15 golden, 4/4 contract, `refused` 15/15**; end-to-end latency
from the client p50 1.6 s / p95 2.9 s; refusals without a model call 0.24 s. The primary
model's daily free quota was already used, so every answer came from the first fallback,
gemini-3.5-flash-lite (≈ $1.44 / 1,000 at its list price) — the fallback chain working as
designed.

**How much these runs prove:** one run per model, 15 questions each, temperature 0.
13/15 vs 14/15 is within noise; the runs separate models on latency and price (large,
stable differences), not on quality. The primary was chosen on price and latency within
the quality bar, and the chain falls back to the model with the best observed pass rate.

What the failures in the per-model runs were:
- 2.5-flash-lite: one free-tier quota error (HTTP 503 to the client) and one allergy answer
  that repeated a wrong claim from the Pesto recipe's notes ("necessary for people with nut
  allergies"). The second led to the allergy-mode change below; that run predates it.
- 3.5-flash-lite: one malformed JSON output → now retried once.
- 3.1-flash-lite and 2.5-flash: "What is the capital of France?" refused as
  `out_of_corpus` instead of `out_of_domain` → prompt rule added.
- 2.5-flash: three per-minute quota errors on the free tier; thinking tokens triple output.

## Options considered

| Option | For | Against |
|---|---|---|
| **A. 2.5-flash-lite primary + fallback chain on quota errors** (chosen) | Cheapest and fastest; the only model clearly inside the $1/1,000 target; chain multiplies the free daily quota (~4 × 20 requests) | Quality slightly below 3.5-flash-lite on one case; four models to keep evaluated |
| B. 3.5-flash-lite only | Best measured pass rate at similar latency | 3.6× the cost; 20 requests/day on the free tier is not enough for a review |
| C. 2.5-flash (thinking) | Stronger reasoning | 6× cost, p95 8.2 s, per-minute limits on free tier; no quality gain on this task |
| D. Local / open model (Gemma 4 via the same API) | Larger free quota | 36 s for one call in a probe: fails the latency target |

## Decision

1. **Models:** chain `gemini-2.5-flash-lite → gemini-3.5-flash-lite → gemini-3.1-flash-lite
   → gemini-2.5-flash` (`LLM_MODELS`). A model that returns HTTP 429 is skipped for the
   cooldown in the error's `RetryInfo` (hours for a daily quota, seconds for a per-minute
   one). Transient errors (5xx, timeouts, malformed JSON) get one retry on the same model.
   Temperature 0, JSON output enforced with `response_schema`.
2. **Refusals in layers, cheapest first** (SPEC 4):
   1. request validation → 422, not a refusal;
   2. deterministic safety pre-check (symptoms, reactions, dangerous food handling) →
      `safety`, no retrieval, no model call;
   3. hard filters leave no candidate → `out_of_corpus`, no model call;
   4. the model chooses `answer | out_of_corpus | out_of_domain | safety`;
   5. server post-check: an `answer` without at least one *retrieved* recipe id becomes
      `out_of_corpus`; ids not retrieved are dropped; titles and URLs come from the corpus.
   Refusal texts are fixed server templates, never model text, so a refusal is always
   polite and the same for the same reason.
3. **Allergy mode** (SPEC 5.4): the server, not the model, lists matching ingredient lines
   (`allergy_check`) and adds a fixed `safety_notice`. The model sees only structured
   fields (ingredients, steps) in allergy mode — the free-text notes of community recipes
   contain unverified safety claims (Pesto I says replacing pine nuts with walnuts or
   almonds is "necessary for people with nut allergies").
4. **Cache:** responses are cached in memory by normalized question (LRU 512, TTL 24 h).
   The pipeline is deterministic for a fixed corpus and model chain, and visitors will
   repeat the example questions. A cache hit costs no quota.

## Consequences

- Cost at list price **depends on which model in the chain answered**: ≈ $0.42 per 1,000
  questions on the primary (≈ 3.3k input + 0.2k output tokens), ≈ $1.44-1.52 per 1,000 on
  the first fallback ($1.77 in the 13:10 UTC run, longer answers), above the SPEC target of $1. Every answer reports its model
  in `X-LLM-Model` and the logs, so the real cost is measurable per request. The production
  eval run on 2026-10-10 was served entirely by the fallback because the primary's free
  quota was used up; on a paid key that would not happen. Free-tier capacity: ~80 model calls per day before the chain is
  exhausted, then 503 `upstream_unavailable` until the quota resets (07:00 UTC).
- A fallback answer may come from a different model than the primary; the model used is
  in the logs and in the `X-LLM-Model` header.
- Each eval run uses ~13 of the 20 daily calls of a model, so evals run on demand
  (`eval` workflow), not on every push.

## This decision is invalid when

- The key moves to a paid tier: drop the free-tier fallback rationale; re-run the eval on
  all candidates and pick by pass rate within the cost target. If 3.5-flash-lite stays ahead
  on quality, it becomes primary at ≈ $1.5 / 1,000.
- Primary golden pass rate falls below 90% on two consecutive runs, or p95 > 8 s.
- List prices change by > 2×, or a model in the chain is deprecated.
- Traffic exceeds ~1 question/minute sustained: the free tier cannot serve it; billing is
  required, and cache + per-IP limits are no longer the main protection.
- Answers must cite steps or sentences, not recipes: the output schema needs spans and a
  stronger model is likely needed.
