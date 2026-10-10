"""Hard constraint filters (SPEC 5.1). A recipe passes only if it provably fits."""

from recipe_qa.lexicon import matching_lines
from recipe_qa.models import Constraints, Recipe

_DIET_SATISFIES = {"vegetarian": {"vegetarian", "vegan"}, "vegan": {"vegan"}}


def satisfies(recipe: Recipe, constraints: Constraints) -> bool:
    if constraints.max_total_minutes is not None and (
        recipe.total_minutes is None or recipe.total_minutes > constraints.max_total_minutes
    ):
        return False
    if constraints.diet is not None and recipe.diet not in _DIET_SATISFIES[constraints.diet]:
        return False
    return not any(
        matching_lines(recipe.ingredients, (word,)) for word in constraints.exclude_ingredients
    )
