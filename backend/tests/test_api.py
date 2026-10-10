"""API contract tests (SPEC 3). The LLM is replaced by a scripted fake."""

from collections.abc import Sequence
from dataclasses import dataclass, field

import jsonschema
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from recipe_qa.api import create_app
from recipe_qa.config import Settings
from recipe_qa.llm import Generation, UpstreamError
from recipe_qa.models import Constraints, Recipe
from recipe_qa.pipeline import Pipeline
from recipe_qa.ratelimit import DailyBudget, RateLimiter
from recipe_qa.retrieval import HybridRetriever
from recipe_qa.schema import AskResponse

from .conftest import FakeEmbedder, make_recipe

SCHEMA = AskResponse.model_json_schema()


@dataclass
class ScriptedGenerator:
    decision: str = "answer"
    answer: str = "Boil the pasta, then mix eggs and cheese."
    recipe_ids: Sequence[str] | None = None  # None = cite the first retrieved recipe
    fail: bool = False
    calls: list[list[str]] = field(default_factory=list)
    model: str = "fake-model"

    def generate(
        self,
        question: str,
        recipes: Sequence[Recipe],
        constraints: Constraints,
        allergy_mode: bool,
    ) -> Generation:
        self.calls.append([r.id for r in recipes])
        if self.fail:
            raise UpstreamError("boom")
        ids = list(self.recipe_ids) if self.recipe_ids is not None else [recipes[0].id]
        return Generation(
            decision=self.decision,  # type: ignore[arg-type]
            answer=self.answer,
            recipe_ids=ids,
            input_tokens=100,
            output_tokens=20,
            model=self.model,
        )


@pytest.fixture
def corpus(small_corpus: list[Recipe]) -> list[Recipe]:
    pesto = make_recipe(
        "pesto",
        title="Pesto",
        cuisines=["Italian"],
        diet="vegetarian",
        total_minutes=45,
        ingredients=["2 cups basil", "¼ cup pine nuts", "½ cup parmesan", "olive oil"],
    )
    return [*small_corpus, pesto]


def _client(
    corpus: list[Recipe],
    gen: ScriptedGenerator,
    *,
    rate_per_minute: int = 100,
    daily_budget: int = 100,
) -> TestClient:
    settings = Settings(google_api_key=SecretStr("test"), rate_limit_per_minute=rate_per_minute)
    pipeline = Pipeline(
        retriever=HybridRetriever(corpus, FakeEmbedder()),
        generator=gen,
        budget=DailyBudget(daily_budget),
    )
    app = create_app(settings, pipeline=pipeline, limiter=RateLimiter(rate_per_minute))
    return TestClient(app)


def _ask(client: TestClient, question: object) -> dict[str, object]:
    resp = client.post("/ask", json={"question": question})
    assert resp.status_code == 200, resp.text
    body: dict[str, object] = resp.json()
    jsonschema.validate(body, SCHEMA)
    assert resp.headers["x-request-id"] == body["request_id"]  # I7
    return body


def test_answer_has_citations_from_corpus(corpus: list[Recipe]) -> None:
    gen = ScriptedGenerator()
    body = _ask(_client(corpus, gen), "How do I make carbonara?")
    assert body["refused"] is False
    assert body["refusal_reason"] is None
    assert body["answer"]
    citations = body["citations"]
    assert isinstance(citations, list) and len(citations) == 1
    by_id = {r.id: r for r in corpus}
    cited = by_id[citations[0]["recipe_id"]]
    assert citations[0] == {"recipe_id": cited.id, "title": cited.title, "url": cited.url}
    assert body["allergy_check"] is None
    assert body["safety_notice"] is None


def test_ids_not_retrieved_are_dropped(corpus: list[Recipe]) -> None:
    gen = ScriptedGenerator(recipe_ids=["carbonara-pasta", "made-up-recipe"])
    body = _ask(_client(corpus, gen), "carbonara")
    citations = body["citations"]
    assert isinstance(citations, list)
    assert [c["recipe_id"] for c in citations] == ["carbonara-pasta"]


def test_answer_without_valid_citation_becomes_out_of_corpus(corpus: list[Recipe]) -> None:
    gen = ScriptedGenerator(recipe_ids=["made-up-recipe"])
    body = _ask(_client(corpus, gen), "carbonara")
    assert body["refused"] is True
    assert body["refusal_reason"] == "out_of_corpus"
    assert body["citations"] == []


@pytest.mark.parametrize("decision", ["out_of_corpus", "out_of_domain", "safety"])
def test_model_refusals_use_server_templates(corpus: list[Recipe], decision: str) -> None:
    gen = ScriptedGenerator(decision=decision, answer="model text that must not be shown")
    body = _ask(_client(corpus, gen), "What is the capital of France?")
    assert body["refused"] is True
    assert body["refusal_reason"] == decision
    assert body["citations"] == []
    assert body["answer"] and "model text" not in str(body["answer"])


def test_safety_precheck_refuses_without_model_call(corpus: list[Recipe]) -> None:
    gen = ScriptedGenerator()
    body = _ask(_client(corpus, gen), "I ate peanuts and my throat is swelling, what do I do?")
    assert body["refusal_reason"] == "safety"
    assert gen.calls == []


def test_empty_filter_result_refuses_without_model_call(corpus: list[Recipe]) -> None:
    gen = ScriptedGenerator()
    body = _ask(_client(corpus, gen), "a vegan dessert in under 5 minutes")
    assert body["refusal_reason"] == "out_of_corpus"
    assert body["constraints"] == {
        "max_total_minutes": 5,
        "diet": "vegan",
        "exclude_ingredients": [],
    }
    assert gen.calls == []


def test_only_constraint_satisfying_recipes_reach_the_model(corpus: list[Recipe]) -> None:
    gen = ScriptedGenerator()
    _ask(_client(corpus, gen), "vegetarian curry in under 30 minutes")
    assert set(gen.calls[0]) == {"paneer-butter-masala", "potato-curry"}


def test_allergy_question_gets_check_and_notice(corpus: list[Recipe]) -> None:
    gen = ScriptedGenerator(recipe_ids=["pesto"], answer="The pesto lists pine nuts.")
    body = _ask(_client(corpus, gen), "Is the pesto nut-free?")
    assert body["refused"] is False
    assert body["safety_notice"]
    assert body["allergy_check"] == {
        "allergens": ["tree_nut"],
        "matches": [
            {"recipe_id": "pesto", "allergen": "tree_nut", "ingredient": "¼ cup pine nuts"}
        ],
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"question": ""},
        {"question": "   "},
        {"question": "x" * 501},
        {"question": 42},
        {},
        {"question": "ok", "extra": 1},
    ],
)
def test_invalid_requests_are_422(corpus: list[Recipe], payload: dict[str, object]) -> None:
    resp = _client(corpus, ScriptedGenerator()).post("/ask", json=payload)
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["code"] == "invalid_request"
    assert body["request_id"] == resp.headers["x-request-id"]


def test_invalid_json_is_422(corpus: list[Recipe]) -> None:
    resp = _client(corpus, ScriptedGenerator()).post(
        "/ask", content=b"{not json", headers={"content-type": "application/json"}
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_request"


def test_upstream_failure_is_503(corpus: list[Recipe]) -> None:
    resp = _client(corpus, ScriptedGenerator(fail=True)).post(
        "/ask", json={"question": "carbonara"}
    )
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "upstream_unavailable"


def test_rate_limit_is_429(corpus: list[Recipe]) -> None:
    client = _client(corpus, ScriptedGenerator(), rate_per_minute=2)
    codes = [client.post("/ask", json={"question": "carbonara"}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    assert client.post("/ask", json={"question": "x"}).json()["error"]["code"] == "rate_limited"


def test_daily_budget_is_503(corpus: list[Recipe]) -> None:
    client = _client(corpus, ScriptedGenerator(), daily_budget=1)
    assert client.post("/ask", json={"question": "carbonara"}).status_code == 200
    resp = client.post("/ask", json={"question": "carbonara with lemon"})
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "budget_exhausted"


def test_incoming_request_id_is_kept_if_safe(corpus: list[Recipe]) -> None:
    client = _client(corpus, ScriptedGenerator())
    ok = client.post("/ask", json={"question": "carbonara"}, headers={"x-request-id": "abc-123"})
    assert ok.json()["request_id"] == "abc-123"
    bad = client.post("/ask", json={"question": "carbonara"}, headers={"x-request-id": "a\nb"})
    assert bad.json()["request_id"] != "a\nb"


def test_healthz(corpus: list[Recipe]) -> None:
    body = _client(corpus, ScriptedGenerator()).get("/healthz").json()
    assert body["status"] == "ok"
    assert body["corpus_size"] == len(corpus)


def test_response_model_rejects_broken_invariants() -> None:
    with pytest.raises(ValueError):
        AskResponse(
            answer="x",
            citations=[],
            refused=False,
            refusal_reason=None,
            safety_notice=None,
            allergy_check=None,
            constraints={"max_total_minutes": None, "diet": None, "exclude_ingredients": []},  # type: ignore[arg-type]
            request_id="r",
        )


def test_committed_schema_file_is_current() -> None:
    from recipe_qa.export_schema import SCHEMA_PATH, render

    assert SCHEMA_PATH.read_text() == render(), "run: make schema"


def test_usage_headers(corpus: list[Recipe]) -> None:
    resp = _client(corpus, ScriptedGenerator()).post("/ask", json={"question": "carbonara"})
    assert resp.headers["x-llm-model"] == "fake-model"
    assert resp.headers["x-llm-input-tokens"] == "100"
    assert resp.headers["x-llm-output-tokens"] == "20"


def test_repeated_question_is_served_from_cache(corpus: list[Recipe]) -> None:
    gen = ScriptedGenerator()
    client = _client(corpus, gen)
    first = _ask(client, "How do I make carbonara?")
    second = _ask(client, "  how do I make CARBONARA? ")
    assert len(gen.calls) == 1
    assert first["request_id"] != second["request_id"]
    assert {**first, "request_id": ""} == {**second, "request_id": ""}


def test_failures_are_not_cached(corpus: list[Recipe]) -> None:
    gen = ScriptedGenerator(fail=True)
    client = _client(corpus, gen)
    client.post("/ask", json={"question": "carbonara"})
    client.post("/ask", json={"question": "carbonara"})
    assert len(gen.calls) == 2
