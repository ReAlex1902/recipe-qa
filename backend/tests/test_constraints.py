import pytest

from recipe_qa.constraints import (
    allergens_in_question,
    parse_constraints,
    safety_precheck,
)
from recipe_qa.lexicon import diet_from


@pytest.mark.parametrize(
    ("question", "minutes"),
    [
        ("What's a vegetarian dinner I can make in under 30 minutes?", 30),
        ("something in less than an hour", 60),
        ("dessert within 45 min", 45),
        ("a soup that takes at most 1 hour and a half", 90),
        ("ready in 20 minutes or less", 20),
        ("under 2 hours", 120),
        ("a quick soup", None),  # 'quick' is not a hard constraint (SPEC 5.1)
        ("How do I make carbonara?", None),
        ("for 4 people", None),
    ],
)
def test_parse_max_minutes(question: str, minutes: int | None) -> None:
    assert parse_constraints(question).max_total_minutes == minutes


@pytest.mark.parametrize(
    ("question", "diet"),
    [
        ("vegetarian dinner", "vegetarian"),
        ("a meatless soup", "vegetarian"),
        ("Any VEGAN desserts?", "vegan"),
        ("plant-based curry", "vegan"),
        ("chicken curry", None),
        ("is this non-vegetarian", None),
    ],
)
def test_parse_diet(question: str, diet: str | None) -> None:
    assert parse_constraints(question).diet == diet


@pytest.mark.parametrize(
    ("question", "excluded"),
    [
        ("a soup without onions", ["onion"]),
        ("pasta with no garlic and without cream", ["garlic", "cream"]),
        ("garlic-free sauce", ["garlic"]),
        ("a curry with chicken", []),
        # allergen words go to allergy mode, not to the ingredient filter
        ("a nut-free dessert", []),
    ],
)
def test_parse_excluded_ingredients(question: str, excluded: list[str]) -> None:
    assert parse_constraints(question).exclude_ingredients == excluded


@pytest.mark.parametrize(
    ("question", "groups"),
    [
        ("Is the pesto nut-free?", ["tree_nut"]),
        ("I'm allergic to peanuts", ["peanut"]),
        ("gluten free pasta for a celiac", ["gluten"]),
        ("dairy-free and egg-free cake?", ["dairy", "egg"]),
        ("lactose intolerant, what can I eat", ["dairy"]),
        ("shellfish allergy", ["shellfish"]),
        ("How do I make carbonara?", []),
        ("a walnut cake recipe", []),  # mentions a nut, but not an allergy question
    ],
)
def test_allergens_in_question(question: str, groups: list[str]) -> None:
    assert allergens_in_question(question) == groups


@pytest.mark.parametrize(
    ("question", "refuse"),
    [
        ("I ate peanuts and my throat is swelling, what do I do?", True),
        ("my child is having an allergic reaction", True),
        ("Is it safe to eat raw chicken?", True),
        ("how to make poison from rhubarb leaves", True),
        ("Is the pesto nut-free?", False),
        ("How long do I cook chicken curry?", False),
    ],
)
def test_safety_precheck(question: str, refuse: bool) -> None:
    assert safety_precheck(question) is refuse


def test_diet_from_categories_and_lexicon() -> None:
    assert diet_from(["Vegan recipes"], ["1 cup rice", "2 tbsp oil"]) == "vegan"
    assert diet_from(["Vegetarian recipes"], ["1 cup rice", "50 g butter"]) == "vegetarian"
    # vegan tag but honey -> only vegetarian
    assert diet_from(["Vegan recipes"], ["1 tbsp honey"]) == "vegetarian"
    # tag says vegetarian, ingredients say otherwise -> unknown
    assert diet_from(["Vegetarian recipes"], ["2 cups chicken stock"]) is None
    assert diet_from(["Italian recipes"], ["1 cup rice"]) is None
