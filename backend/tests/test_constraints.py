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
        # allergen words are a hard filter AND switch on allergy mode (review 2)
        ("a nut-free dessert", ["nut"]),
        ("a dessert without eggs", ["egg"]),
        ("pasta without cheese", ["cheese"]),
        ("soup with no cream", ["cream"]),
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


# Review findings (2026-10-10)
@pytest.mark.parametrize(
    ("question", "groups"),
    [
        ("Does the pesto contain nuts?", ["tree_nut"]),
        ("I am allergic to cheese, which pasta?", ["dairy"]),
        ("is there pine nut in it? I have an allergy", ["tree_nut"]),
    ],
)
def test_naming_an_allergen_enables_allergy_mode(question: str, groups: list[str]) -> None:
    assert allergens_in_question(question) == groups


@pytest.mark.parametrize(
    ("question", "minutes"),
    [
        ("something I can make within minutes", None),
        ("which dish takes hours?", None),
        ("ready in an hour", 60),
    ],
)
def test_durations_need_a_number(question: str, minutes: int | None) -> None:
    assert parse_constraints(question).max_total_minutes == minutes


def test_non_vegan_is_not_vegan() -> None:
    assert parse_constraints("Give me a non-vegan dessert").diet is None


@pytest.mark.parametrize("question", ["pasta with no meat", "a soup without meat"])
def test_no_meat_means_vegetarian(question: str) -> None:
    assert parse_constraints(question).diet == "vegetarian"


@pytest.mark.parametrize(
    "question", ["I have no idea what to cook", "a cake without using an oven"]
)
def test_non_ingredient_words_are_not_exclusions(question: str) -> None:
    assert parse_constraints(question).exclude_ingredients == []


# Review 2 (2026-10-10)
@pytest.mark.parametrize(
    ("question", "minutes"),
    [("a 30-minute vegetarian dinner", 30), ("a 1-hour soup", 60), ("ready in 45-minutes", 45)],
)
def test_hyphenated_durations(question: str, minutes: int) -> None:
    assert parse_constraints(question).max_total_minutes == minutes


@pytest.mark.parametrize(
    "question", ["I am not vegetarian, what meat dish?", "not vegan please", "I'm not a vegetarian"]
)
def test_negated_diet_is_no_constraint(question: str) -> None:
    assert parse_constraints(question).diet is None


def test_with_an_allergen_food_is_not_an_allergy_question() -> None:
    assert allergens_in_question("I want something with fish") == []


def test_containment_question_on_allergen_food_is_allergy_mode() -> None:
    assert allergens_in_question("Does the naan have any butter?") == ["dairy"]


def test_more_non_ingredient_words() -> None:
    assert parse_constraints("dinner without having to shop").exclude_ingredients == []


def test_allergen_exclusion_also_enables_allergy_mode() -> None:
    assert allergens_in_question("a dessert without eggs") == ["egg"]


def test_sub_recipe_reference_makes_diet_unknown() -> None:
    assert diet_from(["Vegan recipes"], ["1 recipe Flaky Pie Crust", "4 cups rhubarb"]) is None


# Review 3 (2026-10-10)
@pytest.mark.parametrize(
    ("question", "excluded"),
    [
        ("Is there a nut-free dessert?", ["nut"]),
        ("Are there any dairy-free soups?", ["dairy"]),
        ("Is the pesto nut-free?", []),
        ("Can I eat the pesto, is it nut-free?", []),
        ("Is the carbonara made without eggs?", []),
        ("Are these churros dairy-free?", []),
        ("Is there a soup without onions?", ["onion"]),
    ],
)
def test_question_about_a_dish_is_not_a_filter(question: str, excluded: list[str]) -> None:
    assert parse_constraints(question).exclude_ingredients == excluded


@pytest.mark.parametrize(
    "question", ["Any fish recipes?", "Any quick pasta with cheese?", "Do you have any curry?"]
)
def test_any_alone_is_not_an_allergy_question(question: str) -> None:
    assert allergens_in_question(question) == []


@pytest.mark.parametrize(
    ("question", "groups"),
    [("Is there any egg in it?", ["egg"]), ("Does the naan have any butter?", ["dairy"])],
)
def test_containment_questions_still_enable_allergy_mode(question: str, groups: list[str]) -> None:
    assert allergens_in_question(question) == groups


@pytest.mark.parametrize(
    ("question", "minutes"),
    [("dinner in 1-2 hours", 120), ("a soup in 20 to 30 minutes", 30)],
)
def test_time_ranges_in_questions_use_the_upper_bound(question: str, minutes: int) -> None:
    assert parse_constraints(question).max_total_minutes == minutes


def test_sub_recipe_detection_needs_a_quantity() -> None:
    assert diet_from(["Vegan recipes"], ["rice (see recipe note)"]) == "vegan"
    assert diet_from(["Vegan recipes"], ["1 recipe Flaky Pie Crust"]) is None


# Review 4: an article before the exclusion marks a request even inside a dish clause
@pytest.mark.parametrize(
    ("question", "excluded"),
    [
        ("Is it possible to make a dessert without eggs?", ["egg"]),
        ("Does the cookbook have a nut-free dessert?", ["nut"]),
        ("Is this a good soup without cream?", ["cream"]),
        ("Is the pesto nut-free?", []),
        ("Is the carbonara made without eggs?", []),
    ],
)
def test_article_before_exclusion_means_request(question: str, excluded: list[str]) -> None:
    assert parse_constraints(question).exclude_ingredients == excluded
