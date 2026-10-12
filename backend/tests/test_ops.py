"""Ops endpoints: container status and recent logs for reviewers (README, Deployment)."""

import logging
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from recipe_qa.api import create_app
from recipe_qa.config import Settings
from recipe_qa.logging_setup import RingBufferHandler
from recipe_qa.models import Recipe
from recipe_qa.pipeline import Pipeline
from recipe_qa.ratelimit import DailyBudget
from recipe_qa.retrieval import HybridRetriever

from .conftest import FakeEmbedder
from .test_api import ScriptedGenerator

TOKEN = "test-ops-token"  # noqa: S105 - test fixture, not a secret


def _client(corpus: list[Recipe], ops_token: str | None) -> TestClient:
    settings = Settings(
        google_api_key=SecretStr("test"),
        ops_token=SecretStr(ops_token) if ops_token else None,
    )
    pipeline = Pipeline(
        retriever=HybridRetriever(corpus, FakeEmbedder()),
        generator=ScriptedGenerator(),
        budget=DailyBudget(5),
    )
    return TestClient(create_app(settings, pipeline=pipeline))


@pytest.mark.parametrize("path", ["/ops/status", "/ops/logs"])
def test_ops_requires_token(small_corpus: list[Recipe], path: str) -> None:
    client = _client(small_corpus, TOKEN)
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get(path, headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200


@pytest.mark.parametrize("path", ["/ops/status", "/ops/logs"])
def test_ops_disabled_without_configured_token(small_corpus: list[Recipe], path: str) -> None:
    client = _client(small_corpus, None)
    assert client.get(path, headers={"Authorization": "Bearer "}).status_code == 404


def test_ops_status_reports_container_and_service_state(small_corpus: list[Recipe]) -> None:
    client = _client(small_corpus, TOKEN)
    client.post("/ask", json={"question": "carbonara"})
    body = client.get("/ops/status", headers={"Authorization": f"Bearer {TOKEN}"}).json()
    assert body["corpus_size"] == len(small_corpus)
    assert body["embeddings_ready"] is True
    assert body["llm_budget"] == {"used": 1, "limit": 5}
    assert body["process"]["memory_rss_mb"] > 0
    assert body["process"]["uptime_s"] >= 0
    assert {"version", "instance", "cache_entries"} <= body.keys()


def test_ops_logs_returns_recent_json_lines(small_corpus: list[Recipe]) -> None:
    handler = RingBufferHandler(capacity=3)
    logging.getLogger().addHandler(handler)
    try:
        client = _client(small_corpus, TOKEN)
        for n in range(5):
            logging.getLogger("recipe_qa").warning("line %d", n)
        body = client.get("/ops/logs?limit=2", headers={"Authorization": f"Bearer {TOKEN}"}).json()
    finally:
        logging.getLogger().removeHandler(handler)
    assert [e["msg"] for e in body["lines"]] == ["line 3", "line 4"]


def test_ops_endpoints_are_rate_limited(small_corpus: list[Recipe]) -> None:
    client = _client(small_corpus, TOKEN)
    auth = {"Authorization": f"Bearer {TOKEN}"}
    codes = [client.get("/ops/status", headers=auth).status_code for _ in range(31)]
    assert codes[:30] == [200] * 30
    assert codes[30] == 429


# Reviewer token with an expiry (SPEC 3.3)
REVIEW = "test-review-token"  # noqa: S105 - test fixture, not a secret
EXPIRES = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)


def _review_client(corpus: list[Recipe], now: datetime, expires: datetime | None) -> TestClient:
    settings = Settings(
        google_api_key=SecretStr("test"),
        ops_token=SecretStr(TOKEN),
        ops_review_token=SecretStr(REVIEW),
        ops_review_token_expires_at=expires,
    )
    pipeline = Pipeline(
        retriever=HybridRetriever(corpus, FakeEmbedder()),
        generator=ScriptedGenerator(),
        budget=DailyBudget(5),
    )
    return TestClient(create_app(settings, pipeline=pipeline, clock=lambda: now))


def test_review_token_works_before_expiry(small_corpus: list[Recipe]) -> None:
    client = _review_client(small_corpus, EXPIRES - timedelta(hours=1), EXPIRES)
    resp = client.get("/ops/logs", headers={"Authorization": f"Bearer {REVIEW}"})
    assert resp.status_code == 200
    status = client.get("/ops/status", headers={"Authorization": f"Bearer {REVIEW}"}).json()
    assert status["review_token_expires_at"] == "2026-10-15T12:00:00+00:00"


def test_review_token_is_rejected_after_expiry(small_corpus: list[Recipe]) -> None:
    client = _review_client(small_corpus, EXPIRES + timedelta(seconds=1), EXPIRES)
    for path in ("/ops/status", "/ops/logs"):
        assert client.get(path, headers={"Authorization": f"Bearer {REVIEW}"}).status_code == 401
    # the owner token is not affected
    assert client.get("/ops/status", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200


def test_review_token_without_expiry_is_disabled(small_corpus: list[Recipe]) -> None:
    client = _review_client(small_corpus, EXPIRES, None)
    assert client.get("/ops/status", headers={"Authorization": f"Bearer {REVIEW}"}).status_code == 401
