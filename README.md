# Recipe Q&A Service

Answers natural-language questions about 52 recipes from the
[Wikibooks Cookbook](https://en.wikibooks.org/wiki/Cookbook), with citations, and refuses
— in a machine-readable way — what the corpus cannot answer.

| | |
|---|---|
| UI | https://recipe-qa.vercel.app |
| API | `POST https://recipe-qa.vercel.app/ask` · OpenAPI: `/docs` · health: `/healthz` |
| Container status / logs | `GET /ops/status`, `GET /ops/logs` with `Authorization: Bearer <OPS_TOKEN>` (token sent with the submission) |
| Spec · ADRs · Evals | [SPEC.md](SPEC.md) · [docs/adr](docs/adr) · [evals](evals) |
| AI usage notes | [docs/ai-usage.md](docs/ai-usage.md), [CLAUDE.md](CLAUDE.md) |

> The Gemini key is on the free tier (~20 model calls per model per day, 4 models in the
> fallback chain); after that the API returns `503 upstream_unavailable` until 07:00 UTC.

```bash
curl -s https://recipe-qa.vercel.app/ask -H 'content-type: application/json' \
  -d '{"question": "What is a vegetarian dinner I can make in under 30 minutes?"}' | jq
```

## For reviewers

1. Open the UI: https://recipe-qa.vercel.app (or call the API, example above).
2. Read in this order: [SPEC.md](SPEC.md), [docs/adr](docs/adr), [evals](evals), then the
   sections below. Deploy history and build logs: the repository's Actions tab.
3. Logs and runtime status of the running service (the token is sent with the submission,
   it is not in the repository):

```bash
export OPS_TOKEN=...   # from the submission e-mail

# deployed git SHA, instance, uptime, memory, CPU, embeddings, model cooldowns, LLM budget
curl -s -H "Authorization: Bearer $OPS_TOKEN" https://recipe-qa.vercel.app/ops/status

# last log lines: one JSON line per request (question, filters, retrieved recipes, model,
# tokens, per-stage timings), find a request by the id shown in the UI
curl -s -H "Authorization: Bearer $OPS_TOKEN" "https://recipe-qa.vercel.app/ops/logs?limit=50"
```

The token is read-only: it opens only these two GET endpoints, and logs contain no
secrets. Without it both return 401. Logs live in the memory of the function instance
that serves the request, so after a cold start the list can be short.

4. Run the golden set yourself: `make eval BASE_URL=https://recipe-qa.vercel.app`
   (uses ~13 model calls of the free daily quota; see the note above).

## How it works

```
question ─► validate (422) ─► parse constraints (regex) ─► safety pre-check ──► refused: safety
        ─► hybrid retrieval: BM25 + Gemini embeddings, RRF, hard filters first
        ─► no candidate left ──► refused: out_of_corpus   (no LLM call)
        ─► Gemini, JSON schema output {decision, answer, recipe_ids}
        ─► server post-check: only retrieved ids, citations from corpus, refusal templates
```

- One recipe = one retrieval unit; constraints (time, diet, excluded ingredient) are hard
  metadata filters, so every cited recipe provably fits ([ADR-0001](docs/adr/0001-retrieval.md)).
- Refusals are decided in layers, cheapest first; refusal texts are server templates
  ([ADR-0002](docs/adr/0002-model-refusals-and-quota.md)).
- Allergy questions: the server lists matching ingredient lines (`allergy_check`) and adds a
  fixed `safety_notice`; the service never claims a dish is allergen-free (SPEC 5.4).
- Recipes that disagree (two carbonaras, two guacamoles) are presented as separate
  versions, each cited (SPEC 5.3).

Repository layout:

```
backend/            FastAPI service (Python 3.12, uv)
  src/recipe_qa/    api, pipeline, retrieval, constraints, lexicon, llm, ingest/
  data/corpus.json  the corpus (built by `make ingest`, committed for review)
  tests/            unit + API contract tests (no network)
frontend/           one-page UI, TypeScript + Vite, served by the backend
evals/              golden set, eval harness, retrieval ablation, response JSON Schema
deploy/vercel.py    deployment as code (Vercel API + CLI)
backend/index.py    Vercel entrypoint; backend/vercel.json function config
docs/adr/           architecture decision records
```

## Run locally

Requires a Gemini API key in `.env` (see `.env.example`).

```bash
docker compose up --build          # http://localhost:8000
```

Without Docker (uv ≥ 0.11, Node 22):

```bash
cd frontend && npm ci && cd ..
make dev                           # builds the UI, runs the API with reload on :8000
make test                          # ruff, mypy --strict, pytest, tsc
make eval BASE_URL=http://localhost:8000   # golden set against a running service
make ingest                        # rebuild the corpus from the MediaWiki API
make ingest-pinned                 # rebuild byte-identically from the recorded revisions
make embed                         # recompute corpus embeddings after a corpus change (needs the key)
```

## Corpus

`python -m recipe_qa.ingest` fetches a curated list of 52 pages
([selection.py](backend/src/recipe_qa/ingest/selection.py)) from the MediaWiki Action API,
parses the wikitext (summary template, ingredient/step sections and subsections, notes),
derives `total_minutes` and diet tags, and writes `corpus.json` sorted and deterministic.
16 cuisines, 13 dish categories, 18 vegetarian/vegan recipes, 49 with a stated time, and
overlapping dishes (2 carbonaras, 3 marinara sauces, 2 guacamoles, 2 rice puddings).
`--pinned` re-fetches the exact revision ids recorded in the file. Content: CC BY-SA 4.0.

## Quality: evals and tests

- `evals/golden.yaml` — 15 questions with expected properties (source cited, refusal and
  reason, constraint satisfied against corpus metadata, allergy check, forbidden phrases)
  plus 4 HTTP contract cases. `evals/run_evals.py` validates every response against the
  JSON Schema and invariants I1–I7, prints a report, writes JSON to `evals/reports/`, and
  exits non-zero below the SPEC AC2 thresholds.
- The harness checks excluded ingredients with the service's own lexicon (the word lists,
  not the filter code). Deliberate: the eval verifies the promise the service makes; a word
  missing from the lexicon is caught by neither, which is why the lists are conservative.
- `evals/retrieval_eval.py` — retrieval-only ablation (BM25 / embeddings / hybrid).
- `backend/tests` — 188 tests without network: wikitext parsing, time parsing, ingestion
  (mocked MediaWiki), constraints, lexicon, filters, BM25/RRF/hybrid and its degradation,
  prompt building, model fallback, API contract and invariants, rate limit, budget, cache,
  ops endpoints.

**Latest production run (2026-10-10, commit `c55d43a`, the last code change): 15/15 golden
cases, 4/4 contract cases, p50 1.9 s / p95 3.6 s end to end; refusals without a model call
0.2 s.** Reports cited here are committed in [`evals/results/`](evals/results).

What these runs do and do not show:
- All three production runs were answered by the first fallback, gemini-3.5-flash-lite:
  the primary's free daily quota was used up by the per-model runs. **The primary model has
  not been measured in production**; its only golden run is the local per-model run in
  ADR-0002 (13/15, before the allergy and prompt fixes). Re-run `eval` after 07:00 UTC to
  measure it.
- To stay at 15 questions (the brief asks for 12-15), `tom-yum-time`, `tiramisu-ingredients`,
  `guacamole` and `sushi` were replaced by cases for allergen exclusion, three marinara
  versions, a similar dish not in the corpus and a "nut-free dessert" request. Checks of
  factual content in the answer text are now `guanciale` (carbonara), `tomato` (marinara)
  and `pine nut` (pesto); everything else is checked structurally (sources, refusal,
  constraints against corpus metadata, allergy check). Per-model runs: see the table in
[ADR-0002](docs/adr/0002-model-refusals-and-quota.md#measurements). Evals run on demand
(GitHub Actions → `eval` → Run workflow), not on every push, because one run uses ~13 of
the 20 free daily calls of a model.

## Deployment

**Where:** Vercel Hobby — the FastAPI app runs as one Vercel Function (Python 3.12) at
https://recipe-qa.vercel.app. **Why:** the owner's constraint is free with no payment card,
not even a verification hold. Hugging Face Spaces (`402`, Docker Spaces need PRO) and
Render (`402`, card required for this account) were tried and failed at service creation;
Cloud Run, Fly.io and Koyeb need a card; Back4app's free URL expires after 60 minutes.
Full comparison and trade-offs: [ADR-0003](docs/adr/0003-deployment.md).

**How a new deployment works:** push to `main` → GitHub Actions `ci`:
lint + mypy + tests, UI build, gitleaks secret scan, Docker build + container smoke test
(non-root, healthz, UI, 422, safety refusal with a dummy key) → job `deploy` (environment
`production`) runs `deploy/vercel.py deploy`: creates the Vercel project if it does not
exist, upserts its env vars, skips if this commit is already the live production
deployment, otherwise uploads the checkout and deploys it to production (Vercel builds it
on Linux from `uv.lock`); then `deploy/vercel.py verify` smoke-tests the public URL
(version = commit SHA, UI, 422, safety refusal, ops auth, embeddings loaded). The repo is
not connected to Vercel, so only CI-tested commits go live; deploys never run in parallel;
re-running is a no-op.

**Docker:** `Dockerfile` (base images pinned by digest, `uv.lock`, `package-lock.json`,
non-root) is the reproducible build for local runs and CI. The hosted function is built by
Vercel from the same lock files, not from the image.

**Secrets** (`GOOGLE_API_KEY`, `VERCEL_TOKEN`, `OPS_TOKEN`) are GitHub environment secrets;
the deploy script writes the app's secrets to Vercel as `sensitive` env vars and passes the
token to the CLI only through the environment. Nothing secret is in the repo or the image
(gitleaks on every push; `.env` is git-ignored).

**One-time bootstrap** (done): a Vercel Hobby account, an access token for the team, the
three secrets in the GitHub `production` environment.

**Container-level access (chosen option: logs and runtime status):** Vercel Hobby has no
team seats, so the service exposes, behind a bearer token:

```bash
curl -s -H "Authorization: Bearer $OPS_TOKEN" https://recipe-qa.vercel.app/ops/status | jq
curl -s -H "Authorization: Bearer $OPS_TOKEN" "https://recipe-qa.vercel.app/ops/logs?limit=50" | jq
```

`status` shows the deployed git SHA, instance id, uptime, memory, CPU time, embedding
state, model cooldowns, daily LLM budget and cache size; `logs` returns the last 2,000
JSON log lines of the instance that serves the request. Build and deploy logs of every
release are in its Actions run.

## Cost & latency

| | Value | Source |
|---|---|---|
| Tokens per question that reaches the model | ≈ 3.3k in, 0.2k out | eval reports |
| Cost per question, primary model (gemini-2.5-flash-lite list price) | **≈ $0.00042** | 3,343 × $0.10/M + 213 × $0.40/M |
| Cost per 1,000 questions, primary model | **≈ $0.42** (+ $0.002 embeddings); refusals before the model and cache hits: $0 | |
| Cost per 1,000 questions, first fallback (gemini-3.5-flash-lite) | **≈ $1.4-1.8**, above the SPEC target of $1 | production eval reports |
| Cost today | $0: free-tier key, free hosting | |
| Latency, model questions (local, warm) | p50 1.3 s, p95 2.5 s | eval, 2.5-flash-lite |
| Latency, refusals without model | p50 4 ms | eval |
| Stage split | constraints + BM25 < 0.1 ms, query embedding ~300 ms, LLM ~1.0–2.5 s | logs (`timings_ms`) |

Actual cost depends on which model in the chain answered (header `X-LLM-Model`, logged per
request). The production eval run was served by the fallback, because the primary's free
daily quota was already used; on a paid key the primary serves everything.

**Models and why:** `gemini-2.5-flash-lite` is primary — cheapest and fastest of the four
measured and the only one well inside the $1/1,000 target. On a quota error the service
falls back to `gemini-3.5-flash-lite` (best measured pass rate, $1.52/1,000), then
`gemini-3.1-flash-lite`, then `gemini-2.5-flash`. Embeddings: `gemini-embedding-001`, 768 d.

**When to change model:** to a cheaper one never below ~90% golden pass rate (no cheaper
Gemini option exists today); to a more capable one (3.5-flash-lite primary) when the
primary fails the golden set twice in a row, when allergy or multi-version answers degrade,
or when the key moves to a paid tier and +$1.1 per 1,000 questions buys measurably better
answers. Re-run `make eval` for every candidate before switching.

**Current bottleneck:** availability, not speed — the free Gemini quota (≈ 80 model calls
per day across the chain). On latency, the LLM call is
~80% of a request; the query embedding is the next ~20%.

**Next optimizations:** (1) a paid key or Vertex AI quota — removes the hard daily limit;
(2) skip the embedding call when BM25 already has a strong match (saves ~300 ms on most
questions; the ablation shows BM25 alone gets 17/18); (3) stream the answer to the UI;
(4) persist the response cache (Redis) so restarts and multiple instances share it.

## Debugging a bad answer in production

Every `/ask` writes one JSON log line (`event: "ask"`) with the `request_id` that the
client also receives (body and `X-Request-ID` header, shown in the UI). Given a bad answer,
take its request id and read that line (`/ops/logs`, or the Vercel runtime logs): it holds
the question, the parsed constraints and allergy groups, the pre-check result, the number
of candidates after filtering, the retrieved recipe ids with their BM25 and vector ranks,
whether retrieval was degraded, the model that answered, its raw decision and recipe ids,
ids dropped by the post-check, tokens, and per-stage timings. That tells which stage
failed: wrong constraints (parser), right recipe not retrieved (ranking/filters), right
recipe retrieved but ignored or misread (prompt/model), or a post-check that should have
caught it. Then add the question to `evals/golden.yaml` with the expected property, fix
the stage, and run `make eval` to prove the fix and check for regressions. What is not
logged today and would be next: the full prompt and raw model output (kept out to limit
log size), sampled and stored with a short retention.

## Production readiness

Done: spec-first contract with machine-checked invariants; eval harness; 188 offline
tests; strict typing and linting; pinned, non-root, read-only-compatible Docker image;
CI with secret scanning and container smoke tests; deploy of tested commits only,
idempotent and serialized; secrets only in the environment; structured logs with request
ids; per-IP rate limit, daily LLM budget, input size limit, security headers (CSP,
nosniff); graceful degradation (BM25 fallback, model fallback chain, retries).

**Not production grade yet — gaps and how to close them:**

| Gap | Close it by |
|---|---|
| Free LLM quota (~80 calls/day) | Paid Gemini tier or Vertex AI |
| Rate limit, budget, cache, log buffer are per function instance | Redis (e.g. Upstash) for limits/cache; a log drain or OpenTelemetry backend with retention |
| Reviewers cannot see the platform dashboard (Hobby has no seats) | Vercel Pro, or Cloud Run with IAM viewer roles |
| No metrics/alerts (only logs) | OpenTelemetry traces + metrics (latency per stage, refusal rate by reason, fallback rate); alert on 5xx and quota exhaustion |
| Golden set is small (15) and English-only | Grow to 100+ from production questions; LLM-as-judge for groundedness of the answer text |
| Answer text groundedness is checked only indirectly (citations, phrases) | Add a claim-level check (each quantity/step present in the cited recipe) in evals |
| Hosted runtime is not the CI-tested Docker image | Container host (Cloud Run) deploying the CI image by digest |
| No staging environment | Vercel preview deployment per PR + smoke tests; promote by SHA |
| No auth on `/ask` (public demo) | API keys or OAuth in front, per-key quotas |

## Scope cut on purpose

No conversation history, no streaming, no user accounts, no admin UI, no multilingual
support, no vector database, no UI framework. The corpus is fixed per deployment
(rebuilding it is a reviewed commit). The UI got one design pass only after the core was
complete, deployed and evaluated (assignment: extras only on a finished core); it changed
markup and CSS, not the API contract.
