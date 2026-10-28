# AI usage notes

Tool: Claude Code (CLI), one long session, plus my own review of every diff.
Agent rules: [`CLAUDE.md`](../CLAUDE.md) (repo rules: spec first, tests before code for
non-LLM modules, no secrets, no hidden behaviour, evals for prompt/model changes).

## Constraints I set

- Hosting must be free with no payment card at all, not even a verification hold. This
  ruled out most container hosts and drove the deployment decision (ADR-0003).
- Gemini is the LLM provider, on a free-tier key, so quota is a design input (ADR-0002).
- The Vercel project is never connected to the repository: only CI uploads a checkout of
  a tested commit.

## Workflow the agent followed (and I enforced)

- `SPEC.md` written before any code; behaviour changes after measurement are recorded
  in its changelog (§10), not silently in code.
- Test first for non-LLM logic: each change started with a failing test, then the
  implementation (wikitext, constraints, ingestion, retrieval, API contract,
  fallback/cache, ops endpoints, precomputed embeddings).
- LLM behaviour judged only by `evals/` runs; each prompt/model change was followed by an
  eval run whose numbers are in ADR-0002.

## What I accepted, and what was rewritten

Accepted mostly as proposed: the wikitext parser and its tests, BM25/RRF code, the eval
harness, Dockerfile, CI workflow.

Rewritten or changed after reviews and measurement:
- **Constraint handling:** the first idea was to let the model respect time/diet limits.
  Rejected: not verifiable. Replaced by deterministic parsing + hard filters, and the
  eval checks cited recipes against corpus metadata, not answer text.
- **Diet tags:** trusting Wikibooks categories alone was wrong — 3 "vegetarian" recipes list
  chicken broth or anchovies. Added the lexicon guard.
- **Allergy answers:** the first prompt still let the model repeat a recipe's unsafe claim.
  Moved the allergen check to the server (`allergy_check`) and hid free-text notes from
  the model in allergy mode.
- **Recipes that disagree:** the model cited only one carbonara. Added a deterministic
  "same dish" hint (shared distinctive title word) instead of more prompt wording.
- **Quota:** a parallel probe burned the day's quota of the primary model — my mistake in
  exploration. Result: fallback chain, cache, evals on demand only, and precomputed
  embeddings (startup no longer spends 52 embedding requests).
- **Deployment:** Hugging Face Spaces and Render were implemented first and both failed
  at service creation with HTTP 402 (card required). Their scripts were removed. Final: Vercel + `deploy/vercel.py`.
- **Second review pass:** allergen exclusions ("a dessert without eggs") were not
  filtered, only flagged; fixed as hard filters plus allergy mode. Also: hyphenated
  durations, negated diets, sub-recipe diet tags, cost reporting that depends on the
  answering model, doc drift (deployment target, `.env.example`), deploy-script
  environment, `/ops` rate limiting. Each fix has a failing test first.
- **A CI slip:** one push went out with a lint error because `make lint | tail` hid the
  exit code; CI caught it and the next commit fixed it.

## Where the time went

Deciding on and failing with three hosting providers (HF Spaces, Render, then Vercel),
measuring four models under a tight free quota, and two review rounds (agent code review
and security audit, then a second review pass), each finding fixed test first.

The published repository history is a single squashed commit; the step-by-step
development history is not part of it.

## What the agent did not do

- It did not write citations, URLs or refusal texts into model output — those are server
  side by design.
- Account sign-ups were done by me; the agent created only the deploy tokens and secrets.
