"""Public API schema (SPEC 3). The JSON Schema for evals is generated from `AskResponse`."""

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from recipe_qa.models import Constraints

RefusalReason = Literal["out_of_corpus", "out_of_domain", "safety"]
AllergenGroup = Literal[
    "tree_nut", "peanut", "dairy", "egg", "gluten", "fish", "shellfish", "soy", "sesame"
]

MAX_QUESTION_CHARS = 500
Question = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_QUESTION_CHARS)
]


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: Question


class Citation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recipe_id: str
    title: str
    url: str


class AllergyMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recipe_id: str
    allergen: AllergenGroup
    ingredient: str


class AllergyCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allergens: list[AllergenGroup]
    matches: list[AllergyMatch]


class AskResponse(BaseModel):
    """Every 200 response from /ask, answer or refusal. Invariants I1-I5 are enforced here."""

    model_config = ConfigDict(extra="forbid")

    answer: str | None
    citations: list[Citation]
    refused: bool
    refusal_reason: RefusalReason | None
    safety_notice: str | None
    allergy_check: AllergyCheck | None
    constraints: Constraints
    request_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _invariants(self) -> Self:
        if not self.refused:
            if not self.answer or not self.citations or self.refusal_reason is not None:
                raise ValueError("I1: an answer needs text, >=1 citation and no refusal_reason")
        elif self.refusal_reason is None or self.citations:
            raise ValueError("I2: a refusal needs a refusal_reason and no citations")
        if self.allergy_check is not None and not self.safety_notice:
            raise ValueError("I5: allergy_check requires safety_notice")
        return self


class ErrorDetail(BaseModel):
    code: Literal[
        "invalid_request",
        "rate_limited",
        "budget_exhausted",
        "upstream_unavailable",
        "internal_error",
    ]
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail
    request_id: str
