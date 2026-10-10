"""The /ask pipeline (SPEC 4): constraints -> safety -> retrieve -> filter -> LLM -> validate."""

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from recipe_qa.constraints import (
    allergens_in_question,
    is_allergy_question,
    parse_constraints,
    safety_precheck,
)
from recipe_qa.lexicon import ALLERGENS, matching_lines
from recipe_qa.llm import Generation
from recipe_qa.models import Constraints, Recipe
from recipe_qa.ratelimit import DailyBudget
from recipe_qa.retrieval import HybridRetriever
from recipe_qa.schema import AllergyCheck, AllergyMatch, AskResponse, Citation, RefusalReason

REFUSAL_MESSAGES: dict[RefusalReason, str] = {
    "out_of_corpus": (
        "Sorry, I couldn't find a recipe in my collection that answers this. I can only "
        "answer from the Wikibooks recipes I have (soups, pasta, curries, desserts and more)."
    ),
    "out_of_domain": ("Sorry, I can only help with questions about the recipes in my collection."),
    "safety": (
        "Sorry, I can't help with this one safely. If someone is having an allergic reaction "
        "or feels unwell, contact emergency services or a medical professional now."
    ),
}

SAFETY_NOTICE = (
    "This check only reads the ingredient list of a community-edited Wikibooks recipe. "
    "Packaged ingredients (stock, sauces, chocolate, spices) can contain allergens that are "
    "not listed, and cross-contamination is not covered. If you have an allergy, check every "
    "label and ask a medical professional."
)

# Groups where plant phrases such as "coconut milk" must not count as a match.
_PLANT_PHRASE_GROUPS = {"dairy", "egg"}


class BudgetExhaustedError(RuntimeError):
    pass


class Generator(Protocol):
    def generate(
        self,
        question: str,
        recipes: Sequence[Recipe],
        constraints: Constraints,
        allergy_mode: bool,
    ) -> Generation: ...


@dataclass
class Trace:
    """Everything needed to debug one answer; logged as one JSON line (README, Debugging)."""

    constraints: dict[str, Any] = field(default_factory=dict)
    allergy_groups: list[str] = field(default_factory=list)
    precheck: str | None = None
    candidates_after_filter: int | None = None
    retrieved: list[dict[str, Any]] = field(default_factory=list)
    retrieval_degraded: bool = False
    model: str | None = None
    model_decision: str | None = None
    model_recipe_ids: list[str] = field(default_factory=list)
    dropped_recipe_ids: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    outcome: str | None = None
    timings_ms: dict[str, float] = field(default_factory=dict)


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 1)


def allergy_matches(recipes: Sequence[Recipe], groups: Sequence[str]) -> list[AllergyMatch]:
    return [
        AllergyMatch(recipe_id=r.id, allergen=g, ingredient=line)  # type: ignore[arg-type]
        for r in recipes
        for g in groups
        for line in matching_lines(
            r.ingredients, ALLERGENS[g], ignore_plant_phrases=g in _PLANT_PHRASE_GROUPS
        )
    ]


class Pipeline:
    def __init__(
        self, retriever: HybridRetriever, generator: Generator, budget: DailyBudget
    ) -> None:
        self.retriever = retriever
        self.generator = generator
        self.budget = budget

    def _refuse(
        self, reason: RefusalReason, constraints: Constraints, request_id: str, trace: Trace
    ) -> AskResponse:
        trace.outcome = f"refused:{reason}"
        return AskResponse(
            answer=REFUSAL_MESSAGES[reason],
            citations=[],
            refused=True,
            refusal_reason=reason,
            safety_notice=None,
            allergy_check=None,
            constraints=constraints,
            request_id=request_id,
        )

    def ask(self, question: str, request_id: str) -> tuple[AskResponse, Trace]:
        trace = Trace()
        start = time.perf_counter()
        constraints = parse_constraints(question)
        allergy_mode = is_allergy_question(question)
        groups = allergens_in_question(question)
        trace.constraints = constraints.model_dump()
        trace.allergy_groups = groups

        if safety_precheck(question):
            trace.precheck = "safety"
            return self._refuse("safety", constraints, request_id, trace), trace

        t = time.perf_counter()
        result = self.retriever.retrieve(question, constraints)
        trace.timings_ms["retrieve"] = _ms(t)
        trace.candidates_after_filter = result.candidates_after_filter
        trace.retrieval_degraded = result.degraded
        trace.retrieved = [
            {"id": h.recipe.id, "bm25_rank": h.bm25_rank, "vector_rank": h.vector_rank}
            for h in result.hits
        ]
        if not result.hits:
            trace.precheck = "no_candidates"
            return self._refuse("out_of_corpus", constraints, request_id, trace), trace

        if not self.budget.consume():
            raise BudgetExhaustedError
        recipes = [h.recipe for h in result.hits]
        t = time.perf_counter()
        gen = self.generator.generate(question, recipes, constraints, allergy_mode)
        trace.timings_ms["llm"] = _ms(t)
        trace.model = gen.model
        trace.model_decision = gen.decision
        trace.model_recipe_ids = gen.recipe_ids
        trace.input_tokens, trace.output_tokens = gen.input_tokens, gen.output_tokens

        if gen.decision != "answer":
            response = self._refuse(gen.decision, constraints, request_id, trace)
            trace.timings_ms["total"] = _ms(start)
            return response, trace

        retrieved = {r.id: r for r in recipes}
        cited = [retrieved[i] for i in dict.fromkeys(gen.recipe_ids) if i in retrieved]
        trace.dropped_recipe_ids = [i for i in gen.recipe_ids if i not in retrieved]
        if not cited or not gen.answer:
            response = self._refuse("out_of_corpus", constraints, request_id, trace)
            trace.outcome = "refused:out_of_corpus(no_valid_citation)"
            trace.timings_ms["total"] = _ms(start)
            return response, trace

        allergy_check = (
            AllergyCheck(allergens=groups, matches=allergy_matches(cited, groups))  # type: ignore[arg-type]
            if allergy_mode
            else None
        )
        trace.outcome = "answered"
        trace.timings_ms["total"] = _ms(start)
        return AskResponse(
            answer=gen.answer,
            citations=[Citation(recipe_id=r.id, title=r.title, url=r.url) for r in cited],
            refused=False,
            refusal_reason=None,
            safety_notice=SAFETY_NOTICE if allergy_mode else None,
            allergy_check=allergy_check,
            constraints=constraints,
            request_id=request_id,
        ), trace
