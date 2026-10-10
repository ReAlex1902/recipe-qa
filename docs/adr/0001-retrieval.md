# ADR-0001: Recipe as the retrieval unit, hybrid BM25 + embeddings, hard filters first

Status: accepted · Date: 2026-10-10

## Context

The service must answer only from 52 Wikibooks recipes, cite them, and obey constraints
(time, diet, excluded ingredient) in the question. The corpus is small and static:
91k characters, **23.5k tokens** in total (Gemini tokenizer), 1.8k characters per recipe
on average, 7.2k at most. Questions are short and mostly name a dish, an ingredient or a
constraint; some paraphrase ("fried dough sticks rolled in cinnamon sugar").

Three decisions: (1) the retrieval unit, (2) how to rank, (3) how to apply constraints.

## Criteria

1. Correct constraint handling — a "vegetarian, under 30 minutes" answer must never cite a
   recipe that is not, and this must be checkable without reading the answer.
2. Citations at recipe level (title + URL is what the contract asks for).
3. Recall of the right recipes in the top 5 passed to the model.
4. Latency and cost per question; operational simplicity on a free, single instance.

## Options considered

**Retrieval unit**

| Option | For | Against |
|---|---|---|
| **A. One recipe = one unit** (chosen) | Citations are recipe-level anyway; the model always sees the ingredients *and* the steps together, so "how do I make X" and "does X contain Y" are answerable from one unit; no reassembly logic | A 7k-char recipe costs ~1.8k tokens in the prompt |
| B. Separate chunks for ingredients, steps, notes | Finer ranking | The model can get the steps without the ingredient list (bad for allergy questions); needs reassembly to cite; no gain at this corpus size |
| C. No retrieval: whole corpus in every prompt | Simplest, no recall risk | 23.5k input tokens per question: **$2.35 per 1,000 questions** with gemini-2.5-flash-lite vs **$0.34** measured with top-5 retrieval (7× more), slower, and constraints would again be the model's job. Breaks completely at a few hundred recipes |

**Ranking**

| Option | Measured recall@5 (18 queries*) | Extra latency | Notes |
|---|---|---|---|
| BM25 only | 17/18 | 0.04 ms | Misses paraphrases with no shared words ("fried dough sticks…" → Churros) |
| Embeddings only (gemini-embedding-001, 768 d) | 18/18 | ~300 ms (one query embedding call; 262–411 ms measured) | Exact names (titles, "Tom Yum Gai") rely on the embedding model |
| **Hybrid: BM25 + embeddings, reciprocal rank fusion (k=60)** (chosen) | 18/18 | ~300 ms | Robust to both; degrades to BM25 if the embedding call fails |

\* 8 golden-set questions with expected sources + 10 paraphrased questions that avoid
title words; `evals/retrieval_eval.py`. On the golden set alone all three get 8/8, so the
difference is only visible on paraphrases — the set is small, and the evidence for
embeddings is modest.

**Constraints**

| Option | For | Against |
|---|---|---|
| **A. Parse deterministically (regex), filter metadata *before* ranking** (chosen) | Predictable, unit-tested, free; the eval can verify every cited recipe against corpus metadata; impossible requests are refused with no LLM call | Patterns miss unusual phrasings ("something I can make before my train") |
| B. Ask the LLM to extract constraints (extra call) | Handles any phrasing | +1 model call (~1 s, double cost, half the free-tier quota); non-deterministic |
| C. Let the answering model respect constraints | No code | Not verifiable; the model may cite a 60-minute recipe for "under 30 minutes" |

Unknown values are treated as *not satisfying* a constraint (a recipe without a stated
time is not "under 30 minutes"; a recipe without a diet tag is not vegetarian). Diet tags
come from Wikibooks categories, checked against a meat/fish lexicon: this removed 3
recipes that Wikibooks tags as vegetarian but list chicken broth or anchovies
(`tuscan-soup-meatless`, `tuscan-mushroom-risotto`, `bruschetta`).

## Decision

One recipe per unit; hybrid BM25 + embedding retrieval fused with RRF; deterministic
constraint parsing and hard metadata filters applied before ranking; top 5 to the model.
Index in process memory (numpy), built at startup: corpus embedding takes 1.3 s.

## Consequences

- p50 retrieval ≈ 300 ms, dominated by the query embedding call (the LLM call is
  ≈ 1.3 s, so retrieval is ~20% of the latency budget).
- Startup embeds 52 documents. On the free Gemini tier this counts as 52 requests against
  the per-minute embedding quota; if it fails, the service runs on BM25 and retries the
  corpus embedding at most once a minute (`HybridRetriever._ensure_doc_vectors`).
- No vector database to run or pay for.

## This decision is invalid when

- The corpus grows past ~5–10k recipes: startup embedding and the in-memory index become
  slow; move to a persistent vector index (pgvector, Qdrant) with precomputed embeddings.
- Recipes get much longer than ~2k tokens, or answers need paragraph-level citations:
  switch to section chunks with recipe-level grouping.
- Questions become conversational or multilingual: the regex constraint parser will miss
  too much; replace it with an LLM extraction step behind the same `Constraints` model,
  keeping the deterministic filter.
- Paraphrase recall stays at parity on a larger eval set: drop embeddings and save
  ~300 ms and one external dependency per question.
