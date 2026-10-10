"""HTTP layer: routing, validation errors, request ids, limits, logging, static UI."""

import dataclasses
import hmac
import json
import logging
import os
import re
import resource
import socket
import sys
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from recipe_qa.config import Settings
from recipe_qa.embeddings_file import load_vectors
from recipe_qa.llm import GeminiEmbedder, GeminiGenerator, UpstreamError, make_client
from recipe_qa.logging_setup import REQUEST_ID, ring_buffer
from recipe_qa.models import Recipe
from recipe_qa.pipeline import BudgetExhaustedError, Pipeline, ResponseCache
from recipe_qa.ratelimit import DailyBudget, RateLimiter
from recipe_qa.retrieval import HybridRetriever
from recipe_qa.schema import AskRequest, AskResponse, ErrorResponse

log = logging.getLogger("recipe_qa")

_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; "
        "frame-ancestors 'none'"
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
        settings.llm_model_chain,
        temperature=settings.llm_temperature,
        max_output_tokens=settings.llm_max_output_tokens,
    )
    return Pipeline(
        retriever=HybridRetriever(
            recipes,
            embedder,
            top_k=settings.top_k,
            doc_vectors=load_vectors(
                settings.corpus_path.with_name("embeddings"),
                recipes,
                model=settings.embedding_model,
                dim=settings.embedding_dim,
            ),
        ),
        generator=generator,
        budget=DailyBudget(settings.daily_llm_budget),
        cache=ResponseCache(settings.cache_size, settings.cache_ttl_s),
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


def _memory_rss_mb() -> float:
    """Resident memory of this process (Linux: current; elsewhere: peak)."""
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024, 1)
    except OSError:
        pass
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(peak / (1024 * 1024 if sys.platform == "darwin" else 1024), 1)


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
                    "models": settings.llm_model_chain,
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
        token = REQUEST_ID.set(request_id)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            REQUEST_ID.reset(token)
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
                        # number of proxies seen; used to set TRUSTED_PROXY_HOPS correctly
                        "xff_entries": len(
                            [p for p in request.headers.get("x-forwarded-for", "").split(",") if p]
                        ),
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

    started = time.monotonic()
    started_at = datetime.now(UTC).isoformat(timespec="seconds")

    def require_ops_token(request: Request) -> None:
        if settings.ops_token is None:
            raise HTTPException(status_code=404)
        expected = f"Bearer {settings.ops_token.get_secret_value()}"
        given = request.headers.get("authorization", "")
        if not hmac.compare_digest(given.encode(), expected.encode()):
            raise HTTPException(status_code=401, headers={"WWW-Authenticate": "Bearer"})

    @app.get("/ops/status", include_in_schema=False)
    async def ops_status(request: Request) -> dict[str, object]:
        require_ops_token(request)
        p: Pipeline = request.app.state.pipeline
        times = os.times()
        generator = p.generator
        return {
            "version": settings.git_sha,
            "instance": os.environ.get("RENDER_INSTANCE_ID", socket.gethostname()),
            "started_at": started_at,
            "process": {
                "pid": os.getpid(),
                "uptime_s": round(time.monotonic() - started),
                "memory_rss_mb": _memory_rss_mb(),
                "cpu_user_s": round(times.user, 2),
                "cpu_system_s": round(times.system, 2),
                "python": sys.version.split()[0],
            },
            "corpus_size": len(p.retriever._recipes),
            "embeddings_ready": p.retriever.has_embeddings,
            "models": settings.llm_model_chain,
            "model_cooldowns_s": generator.cooldowns() if hasattr(generator, "cooldowns") else {},
            "llm_budget": {"used": p.budget.used, "limit": p.budget.limit},
            "cache_entries": len(p.cache),
            "rate_limit_per_minute": settings.rate_limit_per_minute,
        }

    @app.get("/ops/logs", include_in_schema=False)
    async def ops_logs(
        request: Request, limit: int = Query(200, ge=1, le=2000)
    ) -> dict[str, object]:
        require_ops_token(request)
        ring = ring_buffer()
        return {"lines": ring.tail(limit) if ring else []}

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
