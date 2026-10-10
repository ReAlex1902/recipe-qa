"""Gemini adapter behaviour that does not need the network: fallback, prompt content."""

from types import SimpleNamespace
from typing import Any

import pytest
from google.genai import errors

from recipe_qa.llm import GeminiGenerator, UpstreamError, build_user_prompt
from recipe_qa.models import Constraints

from .conftest import make_recipe

OK_JSON = '{"decision": "answer", "answer": "Boil it.", "recipe_ids": ["a"]}'


def _quota_error() -> errors.APIError:
    return errors.ClientError(
        429, {"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED"}}
    )


class FakeModels:
    def __init__(self, behaviour: dict[str, Any]) -> None:
        self.behaviour = behaviour
        self.calls: list[str] = []

    def generate_content(self, *, model: str, contents: str, config: Any) -> Any:
        self.calls.append(model)
        outcome = self.behaviour[model]
        if isinstance(outcome, Exception):
            raise outcome
        usage = SimpleNamespace(
            prompt_token_count=10, candidates_token_count=5, thoughts_token_count=None
        )
        return SimpleNamespace(
            text=outcome, usage_metadata=usage, model_version=model, candidates=[]
        )


def _generator(behaviour: dict[str, Any]) -> tuple[GeminiGenerator, FakeModels]:
    models = FakeModels(behaviour)
    client = SimpleNamespace(models=models)
    return GeminiGenerator(client, ["m1", "m2"], retry_delay_s=0), models  # type: ignore[arg-type]


def test_quota_error_falls_back_to_next_model() -> None:
    gen, models = _generator({"m1": _quota_error(), "m2": OK_JSON})
    out = gen.generate("q", [make_recipe("a")], Constraints(), allergy_mode=False)
    assert out.model == "m2"
    assert models.calls == ["m1", "m2"]


def test_exhausted_model_is_skipped_during_cooldown() -> None:
    gen, models = _generator({"m1": _quota_error(), "m2": OK_JSON})
    gen.generate("q", [make_recipe("a")], Constraints(), allergy_mode=False)
    gen.generate("q", [make_recipe("a")], Constraints(), allergy_mode=False)
    assert models.calls == ["m1", "m2", "m2"]


def test_all_models_exhausted_raises_upstream_error() -> None:
    gen, _ = _generator({"m1": _quota_error(), "m2": _quota_error()})
    with pytest.raises(UpstreamError):
        gen.generate("q", [make_recipe("a")], Constraints(), allergy_mode=False)


def test_invalid_json_is_retried_once_then_next_model() -> None:
    gen, models = _generator({"m1": "not json", "m2": "not json"})
    with pytest.raises(UpstreamError):
        gen.generate("q", [make_recipe("a")], Constraints(), allergy_mode=False)
    assert models.calls == ["m1", "m1", "m2", "m2"]


def test_allergy_mode_prompt_omits_free_text_notes() -> None:
    pesto = make_recipe(
        "pesto",
        intro="A sauce.",
        ingredients=["pine nuts"],
        notes="Using walnuts is necessary for people with nut allergies.",
    )
    assert "necessary for people" in build_user_prompt("q", [pesto], allergy_mode=False)
    prompt = build_user_prompt("q", [pesto], allergy_mode=True)
    assert "necessary for people" not in prompt
    assert "pine nuts" in prompt
