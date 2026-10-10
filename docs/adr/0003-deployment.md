# ADR-0003: Vercel (Hobby) serverless function, deployed by CI from tested commits

Status: accepted · Date: 2026-10-10 · Two earlier choices (Hugging Face Spaces, Render)
failed on the "no payment card" constraint and are recorded below.

## Context

Requirements (assignment §07): public URL for UI and API; a way for reviewers to see how
the service runs at container level; infrastructure as code; a new deployment from the
repository without manual UI steps; secrets in the environment; reproducible builds;
deploying twice is safe. Owner constraint: **free, and no payment card anywhere — not even
a verification hold**. The repository is private and must stay private.

## Criteria

1. Free without a card. 2. Runs the FastAPI service with our pinned dependencies.
3. Source stays private. 4. Deploy fully scriptable from CI. 5. Reviewer visibility into
logs and runtime state. 6. Acceptable cold start and limits for a demo-scale service.

## Options considered (all tried or checked on 2026-10-10)

| Option | Free w/o card | Docker | Private source | API / CI deploy | Reviewer access | Result |
|---|---|---|---|---|---|---|
| Hugging Face Spaces (Docker) | **No** — `402: hosting Docker Spaces on cpu-basic requires PRO` | yes | needs a build-time clone | yes | org members | tried, failed at create |
| Render free web service | **No for this account** — `402: Payment information is required` on service create (anti-fraud $1 hold) | yes | yes | REST API | paid workspace for invites | tried, failed at create |
| Google Cloud Run + Terraform | billing account (card) | yes | yes | best | IAM viewer roles | excluded |
| Fly.io / Koyeb | card required (Koyeb closed its free plan in Feb 2026) | yes | yes | yes | org invite | excluded |
| Back4app Containers | yes | yes | yes | weak | no | free URL expires 60 min after each deploy |
| **Vercel Hobby, Python runtime** (chosen) | **yes** (sign-up with GitHub) | no — runs as a Vercel Function | yes (CLI upload, repo never connected) | CLI + REST API | Hobby has no team seats → own ops endpoints | deployed, smoke tests pass |

## Decision

- **Host:** the FastAPI app is one Vercel Function (Python 3.12, Fluid compute,
  `backend/index.py` entrypoint, `maxDuration` 60 s). Vercel builds the uploaded source on
  Linux from `uv.lock`. The UI build is bundled and served by the same app.
  Production URL: `https://recipe-qa.vercel.app`.
- **Docker stays the reproducible build for local runs and CI:** CI builds the pinned
  image and smoke-tests the container (non-root, healthz, UI, 422, safety refusal). The
  hosted runtime is not that image; both use the same lock files.
- **IaC = `deploy/vercel.py` + `backend/vercel.json`.** On each push to `main`, after lint,
  mypy, tests, UI build, gitleaks and the container test pass, CI:
  1. creates the Vercel project if missing; upserts its env vars from the code
     (plain values) and GitHub secrets (`GOOGLE_API_KEY`, `OPS_TOKEN`, type `sensitive`);
  2. if the commit is already the READY production deployment (deployment meta
     `gitSha`), does nothing; otherwise uploads the checkout and deploys it to production;
  3. smoke-tests the production URL: version = commit SHA, UI, 422, safety refusal, ops
     auth, precomputed embeddings loaded.
  The Git repository is not connected to Vercel, so nothing deploys except commits that
  passed CI; `concurrency` serializes deploys. The Vercel token lives only in the GitHub
  `production` environment and is passed to the CLI through the environment.
- **Serverless-specific changes:** corpus embeddings are precomputed and committed
  (`backend/data/embeddings.npy`, fingerprinted against the corpus) so a cold start makes
  no API call; previously startup spent 52 embedding requests of the free per-minute quota.
- **Container-level access for reviewers:** Vercel Hobby cannot add team members, so the
  function exposes two bearer-token endpoints (constant-time compare; 404 if unset):
  - `GET /ops/status` — deployed SHA, instance id, start time, uptime, RSS memory, CPU
    time, Python version, embeddings state, model cooldowns, LLM budget, cache size;
  - `GET /ops/logs?limit=N` — last ≤ 2,000 structured log lines of that instance.
  The deploy log of every release is in its GitHub Actions run (build output, deployment
  URL, smoke-test results).

## Consequences

- No 30–60 s cold start as on free container hosts; a cold function start is a few
  seconds (dependency import + corpus load).
- **State is per function instance:** rate limiter, daily budget, response cache and the
  log ring buffer live in memory; Fluid compute reuses instances, but parallel instances
  do not share them, and `/ops/*` shows the instance that served the request.
- Logs in the Vercel dashboard are visible to the owner only (Hobby); reviewers get the
  `/ops/logs` view.
- Hobby is for non-commercial use, which fits a take-home; production would be Pro.
- Measured on the deployed function: `X-Forwarded-For` has 1 entry → `TRUSTED_PROXY_HOPS=1`.

## This decision is invalid when

- A card or budget is available: Cloud Run (or Render paid) runs the CI-tested image by
  digest, with platform-level logs and IAM viewer access for reviewers.
- More than one instance must share limits/cache: move them to Redis (Upstash) and logs to
  a log drain (Vercel Pro) or OpenTelemetry backend.
- Requests need > 60 s, streaming over minutes, or background work: a long-running
  container is the better runtime.
- The use becomes commercial: Vercel Pro or another host.
