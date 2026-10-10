# Recipe Q&A Service — agent instructions

These are the rules I gave the coding agent (Claude Code) for this repository.
The human author owns every decision; the agent proposes, the author accepts or rewrites.

## Order of work
1. `SPEC.md` is the source of truth. Change the spec first, then the code. If the code
   and the spec disagree, the code is wrong.
2. For each module that has no LLM in it: write the test first, commit it, then write the
   implementation in a separate commit.
3. Behaviour of the LLM pipeline is measured by `evals/`, not by unit tests. A change to the
   prompt, the model or retrieval is not done until `make eval` was run and the report is
   in the commit message or PR.

## Hard rules
- Answers come only from the corpus. The model never writes citations or URLs; the server
  builds citations from retrieved recipe ids and drops any id that was not retrieved.
- Refusals are machine-readable (`refused`, `refusal_reason`). Never signal a refusal only
  in free text.
- No secrets in the repository. `GOOGLE_API_KEY` comes from the environment only.
  Never print it, never log it, never put it in a test fixture.
- No hidden hardcoded behaviour: every special case (allergy handling, thresholds, limits)
  is written in `SPEC.md` and lives in `config.py` or a named constant.
- Tests never call the network. Gemini and the MediaWiki API are faked in tests.

## Stack
- Backend: Python 3.12, FastAPI, pydantic v2, `uv` for dependencies (`uv.lock` is committed).
- Frontend: one page, TypeScript, Vite, no framework.
- LLM and embeddings: Google Gemini through the `google-genai` SDK.
- Deployment: Docker image on Hugging Face Spaces, deployed by GitHub Actions.

## Commands
- `make test` — backend unit tests + lint + type check
- `make ingest` — rebuild `backend/data/corpus.json` from the MediaWiki API
- `make dev` — run API + UI locally on http://localhost:8000
- `make eval BASE_URL=...` — run the golden set against a running service

## Style
- Small commits with conventional prefixes (`feat:`, `test:`, `docs:`, `ci:`, `chore:`).
- No AI attribution lines in commits.
- Type hints everywhere; `ruff` and `mypy --strict` must pass.
- Keep it small. Anything cut goes to the "Known gaps" list in `README.md`.
