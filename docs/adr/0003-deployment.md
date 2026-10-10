# ADR-0003: Render free web service, deployed by CI through the Render API

Status: accepted · Date: 2026-10-10 (supersedes a first attempt with Hugging Face Spaces)

## Context

Requirements (assignment §07): public URL for UI and API; reviewers can see the service at
container level (dashboard invite, or logs and container status); infrastructure as code;
a new deployment from the repository without manual UI steps; secrets in the environment;
reproducible builds; deploying twice is safe. Constraints from the owner: **free, and no
payment card attached anywhere**. The repository is private and should stay private.

## Criteria

1. Free without a card. 2. Runs our Docker image. 3. Private source stays private.
4. Deploy fully scriptable from CI. 5. Reviewer visibility into logs and container state.
6. Cold start and resource limits acceptable for a demo-scale service.

## Options considered

| Option | Free w/o card | Docker | Private source | API / IaC | Reviewer access | Result |
|---|---|---|---|---|---|---|
| Hugging Face Spaces (Docker) | **No** — since 2026 Docker Spaces on cpu-basic need PRO (`402 Payment Required` on create) | yes | needs a build-time clone trick | yes | org members see logs | tried, rejected |
| Google Cloud Run + Terraform | needs a billing account (card) | yes | yes | best (Terraform, WIF) | IAM roles `run.viewer`, `logging.viewer` | rejected by constraint 1 |
| Fly.io | card required | yes | yes | `fly.toml` | org invite | rejected |
| Koyeb free | card hold often required | yes | yes | API | paid for teams | rejected |
| Back4app Containers | yes | yes | yes | limited | no | 256 MB RAM, weak API |
| **Render free web service** (chosen) | yes (sign-up with GitHub) | yes, builds our Dockerfile | yes (GitHub app, repo-scoped) | REST API | dashboard invites need a paid workspace → see below | chosen |

## Decision

- **Host:** one Render free web service (512 MB RAM, 0.1 CPU, Frankfurt), Docker runtime,
  built by Render from the repository `Dockerfile` (base images pinned by digest,
  `uv.lock`, `package-lock.json`).
- **IaC = `deploy/render.py`.** The desired service (runtime, plan, region, health check,
  env vars, branch, auto-deploy off) is code. Each CI run on `main`, after lint, type check,
  tests, UI build, secret scan and a container smoke test pass:
  1. creates the service if missing, otherwise overwrites its settings and env vars with
     the values in the file (reconcile);
  2. triggers a deploy of **exactly the tested commit** (`commitId`), unless that commit is
     already live, and waits for `live`;
  3. smoke-tests the public URL (version, UI, 422 contract, safety refusal, ops auth).
  Running it twice for one commit is a no-op; GitHub `concurrency` prevents parallel deploys.
- **Why not `render.yaml` Blueprints:** a Blueprint must be created once in the dashboard,
  and it deploys on push whether or not CI passed. The API approach keeps "only tested
  commits go live" and needs no dashboard step after the one-time bootstrap (sign up with
  GitHub, allow the Render GitHub app on this repository, create an API key).
- **Secrets:** `GOOGLE_API_KEY`, `OPS_TOKEN`, `RENDER_API_KEY` live in the GitHub
  `production` environment and are written to Render env vars by the deploy script. They
  are never in the repository or the image. The CI image test runs with a dummy key.
- **Container-level access for reviewers:** a Render Hobby workspace cannot have members,
  so the service exposes two token-protected endpoints (constant-time compare; disabled
  when `OPS_TOKEN` is unset):
  - `GET /ops/status` — version (git sha), instance id, start time, uptime, RSS memory,
    CPU time, embeddings state, model cooldowns, daily LLM budget, cache size;
  - `GET /ops/logs?limit=N` — last ≤ 2,000 structured log lines of this container.
  Deploy history and build logs are in the GitHub Actions run of each commit.

## Consequences

- **Cold start:** a free instance sleeps after 15 minutes without traffic; the next
  request waits ~30–60 s for boot plus ~1.5 s for corpus embedding. Reviewers should
  open `/healthz` first. Not budgeted in the latency SLO (SPEC 7).
- **Single instance:** the rate limiter, daily budget, response cache and log buffer are
  in process memory. Correct for one instance; lost on restart.
- Render builds from source, so the deployed image is not byte-identical to the one CI
  tested; both are built from the same pinned Dockerfile and lock files.
- `/ops/logs` holds questions from users (SPEC A6); the token is shared only with
  reviewers and can be rotated by changing one secret and re-running the deploy.

## This decision is invalid when

- The service needs an SLA or no cold starts: move to a paid instance ($7/month Starter)
  or Cloud Run with min-instances = 1.
- More than one instance is needed: move rate limit, budget and cache to Redis, and logs
  to a log store (Render log streams → Better Stack / Grafana Loki).
- Reviewers or teammates need the platform dashboard itself: Render Pro workspace, or
  Cloud Run with IAM viewer roles.
- A card becomes acceptable: Cloud Run + Terraform + Workload Identity Federation is the
  stronger IaC story (state, plan/apply, no long-lived deploy key).
