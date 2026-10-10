# AI usage notes

Tool: Claude Code (CLI), one long session, plus my own review of every diff.
Agent rules: [`CLAUDE.md`](../CLAUDE.md) (repo rules: spec first, tests before code for
non-LLM modules, no secrets, no hidden behaviour, evals for prompt/model changes).
Important prompts below are quoted from the session (translated from Russian where needed).

## Prompts that shaped the work

1. Kick-off: "The take-home assignment and a Google API key for Gemini are in this folder.
   Use exactly these inputs and solve the case. All files must stay in this folder."
2. Hosting: "I don't care where, it must be free and work — do whatever is needed and ask
   me for what you need." Later: "No cards at all, not even a trial. Find any free way."
3. Source privacy: when the agent proposed a public Hugging Face Space I asked why it
   mattered; we chose to keep source private (it later moved to Vercel, where the repo is
   never connected and only CI uploads a checkout).

## Workflow the agent followed (and I enforced)

- `SPEC.md` committed before any code; behaviour changes after measurement are recorded
  in its changelog (§10), not silently in code.
- Test-first commits for non-LLM logic: see `git log` — every `test: … (failing)` commit
  precedes its `feat:` commit (wikitext, constraints, ingestion, retrieval, API contract,
  fallback/cache, ops endpoints, precomputed embeddings).
- LLM behaviour judged only by `evals/` runs; each prompt/model change was followed by an
  eval run whose numbers are in ADR-0002.

## What I accepted, and what was rewritten

Accepted mostly as proposed: the wikitext parser and its tests, BM25/RRF code, the eval
harness, Dockerfile, CI workflow.

Rewritten or changed after review / measurement:
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
  at service creation with HTTP 402 (card required). Their scripts were deleted; the
  history keeps them. Final: Vercel + `deploy/vercel.py`.
- **A CI slip:** one push went out with a lint error because `make lint | tail` hid the
  exit code; CI caught it and the next commit fixed it.

## What the agent did not do

- It did not write citations, URLs or refusal texts into model output — those are server
  side by design.
- Account sign-ups were done by me; the agent created only the deploy tokens and secrets.
