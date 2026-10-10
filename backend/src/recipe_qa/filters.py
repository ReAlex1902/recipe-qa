"""Hard constraint filters (SPEC 5.1). A recipe passes only if it provably fits."""

from recipe_qa.lexicon import exclusion_terms, has_sub_recipe, matching_lines
from recipe_qa.models import Constraints, Recipe

_DIET_SATISFIES = {"vegetarian": {"vegetarian", "vegan"}, "vegan": {"vegan"}}


def satisfies(recipe: Recipe, constraints: Constraints) -> bool:
    if constraints.max_total_minutes is not None and (
        recipe.total_minutes is None or recipe.total_minutes > constraints.max_total_minutes
    ):
        return False
    if constraints.diet is not None and recipe.diet not in _DIET_SATISFIES[constraints.diet]:
        return False
    # A line like "1 recipe Flaky Pie Crust" hides its ingredients: no exclusion is provable.
    if constraints.exclude_ingredients and has_sub_recipe(recipe.ingredients):
        return False
    for word in constraints.exclude_ingredients:
        terms, ignore_plant = exclusion_terms(word)
        if matching_lines(recipe.ingredients, terms, ignore_plant_phrases=ignore_plant):
            return False
    return True
