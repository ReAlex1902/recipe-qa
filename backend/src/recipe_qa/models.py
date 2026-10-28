"""Domain models shared by ingestion, retrieval and the API."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

Diet = Literal["vegetarian", "vegan"]


class Recipe(BaseModel):
    """One Wikibooks Cookbook page, the unit of retrieval (ADR-0001)."""

    model_config = ConfigDict(frozen=True)

    id: str
    title: str
    url: str
    revision_id: int
    categories: list[str]
    dish_categories: list[str]
    cuisines: list[str]
    time_text: str | None
    total_minutes: int | None
    servings: str | None
    diet: Diet | None
    intro: str
    ingredients: list[str]
    steps: list[str]
    notes: str

    def as_prompt_text(self, include_free_text: bool = True) -> str:
        """Plain-text form given to the model and the embedder.

        `include_free_text=False` drops the description and notes: in allergy mode the model
        sees only the structured lists, because free text holds unverified safety claims
        (SPEC 5.4).
        """
        parts = [f"Title: {self.title}"]
        if self.cuisines:
            parts.append(f"Cuisine: {', '.join(self.cuisines)}")
        if self.dish_categories:
            parts.append(f"Category: {', '.join(self.dish_categories)}")
        parts.append(f"Total time: {self.time_text or 'not stated'}")
        parts.append(f"Servings: {self.servings or 'not stated'}")
        parts.append(f"Diet: {self.diet or 'not tagged'}")
        if self.intro and include_free_text:
            parts.append(f"Description: {self.intro}")
        parts.append("Ingredients:\n" + "\n".join(f"- {i}" for i in self.ingredients))
        parts.append("Steps:\n" + "\n".join(f"{n}. {s}" for n, s in enumerate(self.steps, 1)))
        if self.notes and include_free_text:
            parts.append(f"Notes: {self.notes}")
        return "\n".join(parts)


class Constraints(BaseModel):
    """Hard filters parsed from a question (SPEC 5.1)."""

    max_total_minutes: int | None = None
    diet: Diet | None = None
    exclude_ingredients: list[str] = []
