"""HTTP layer: routing, validation errors, request ids, limits, logging, static UI."""

import dataclasses
import json
import logging
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from recipe_qa.config import Settings
from recipe_qa.llm import GeminiEmbedder, GeminiGenerator, UpstreamError, make_client
from recipe_qa.models import Recipe
from recipe_qa.pipeline import BudgetExhaustedError, Pipeline
from recipe_qa.ratelimit import DailyBudget, RateLimiter
from recipe_qa.retrieval import HybridRetriever
from recipe_qa.schema import AskRequest, AskResponse, ErrorResponse

log = logging.getLogger("recipe_qa")

_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; frame-ancestors https://huggingface.co"
    ),
}


def load_corpus(path: Path) -> list[Recipe]:
    return [Recipe(**r) for r in json.loads(path.read_text())["recipes"]]


def build_pipeline(settings: Settings) -> Pipeline:
    client = make_client(settings.google_api_key.get_secret_value(), settings.llm_timeout_s)
    recipes = load_corpus(settings.corpus_path)
    embedder = GeminiEmbedder(client, settings.embedding_model, settings.embedding_dim)
    generator = GeminiGenerator(
        client,
        settings.llm_model,
        temperature=settings.llm_temperature,
        max_output_tokens=settings.llm_max_output_tokens,
    )
    return Pipeline(
        retriever=HybridRetriever(recipes, embedder, top_k=settings.top_k),
        generator=generator,
        budget=DailyBudget(settings.daily_llm_budget),
    )


def _error(status: int, code: str, message: str, request_id: str) -> JSONResponse:
    body = ErrorResponse.model_validate(
        {"error": {"code": code, "message": message}, "request_id": request_id}
    )
    return JSONResponse(status_code=status, content=body.model_dump())


def client_ip(request: Request, trusted_hops: int) -> str:
    """Client address; with N trusted proxies, the N-th address from the right of XFF."""
    peer = request.client.host if request.client else "unknown"
    if trusted_hops <= 0:
        return peer
    forwarded = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",")]
    forwarded = [p for p in forwarded if p]
    return forwarded[-trusted_hops] if len(forwarded) >= trusted_hops else peer


def create_app(
    settings: Settings,
    *,
    pipeline: Pipeline | None = None,
    limiter: RateLimiter | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if getattr(app.state, "pipeline", None) is None:
            app.state.pipeline = await run_in_threadpool(build_pipeline, settings)
        log.info(
            "service started",
            extra={
                "fields": {
                    "event": "startup",
                    "version": settings.git_sha,
                    "model": settings.llm_model,
                    "corpus_size": len(app.state.pipeline.retriever._recipes),
                    "embeddings": app.state.pipeline.retriever.has_embeddings,
                }
            },
        )
        yield

    app = FastAPI(
        title="Recipe Q&A",
        version=settings.git_sha,
        lifespan=lifespan,
        responses={422: {"model": ErrorResponse}},
    )
    app.state.pipeline = pipeline  # None: built at startup by `lifespan`
    rate_limiter = limiter or RateLimiter(settings.rate_limit_per_minute)

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get("x-request-id", "")
        request_id = incoming if _SAFE_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        request.state.request_id = request_id
        start = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        if request.url.path != "/healthz":
            log.info(
                "http request",
                extra={
                    "fields": {
                        "event": "http",
                        "request_id": request_id,
                        "method": request.method,
                        "path": request.url.path,
                        "status": response.status_code,
                        "duration_ms": round((time.perf_counter() - start) * 1000, 1),
                    }
                },
            )
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", ()) if p != "body")
        message = f"{where}: {first.get('msg', 'invalid request')}" if where else "invalid JSON"
        return _error(422, "invalid_request", message, request.state.request_id)

    @app.get("/healthz")
    async def healthz(request: Request) -> dict[str, object]:
        return {
            "status": "ok",
            "corpus_size": len(request.app.state.pipeline.retriever._recipes),
            "version": settings.git_sha,
        }

    @app.post(
        "/ask",
        response_model=AskResponse,
        responses={429: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
    )
    async def ask(body: AskRequest, request: Request) -> Response:
        request_id: str = request.state.request_id
        if not rate_limiter.allow(client_ip(request, settings.trusted_proxy_hops)):
            return _error(
                429, "rate_limited", "too many requests, try again in a minute", request_id
            )
        p: Pipeline = request.app.state.pipeline
        try:
            response, trace = await run_in_threadpool(p.ask, body.question, request_id)
        except BudgetExhaustedError:
            return _error(503, "budget_exhausted", "daily question budget is used up", request_id)
        except UpstreamError:
            log.exception("upstream failure", extra={"fields": {"request_id": request_id}})
            return _error(
                503, "upstream_unavailable", "the language model is unavailable", request_id
            )
        except Exception:
            log.exception("unhandled error", extra={"fields": {"request_id": request_id}})
            return _error(500, "internal_error", "internal error", request_id)
        log.info(
            "ask",
            extra={
                "fields": {
                    "event": "ask",
                    "request_id": request_id,
                    "question": body.question,
                    "refused": response.refused,
                    "refusal_reason": response.refusal_reason,
                    "cited": [c.recipe_id for c in response.citations],
                    **dataclasses.asdict(trace),
                }
            },
        )
        usage = {
            "X-LLM-Model": trace.model or "none",
            "X-LLM-Input-Tokens": str(trace.input_tokens),
            "X-LLM-Output-Tokens": str(trace.output_tokens),
        }
        return JSONResponse(content=response.model_dump(), headers=usage)

    if settings.static_dir.is_dir():
        app.mount("/", StaticFiles(directory=settings.static_dir, html=True), name="ui")
    return app
