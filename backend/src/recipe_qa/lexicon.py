"""Word lists for diet tagging and allergen checks (SPEC 5.1, 5.4).

Matching is on whole words with an optional plural 's'/'es', case-insensitive.
The lists are deliberately conservative: a false positive (flagging an ingredient that is
not really the allergen) is acceptable, a false negative is not.
"""

import re
from collections.abc import Iterable
from functools import cache

from recipe_qa.models import Diet

MEAT_FISH = (
    "anchovy",
    "anchovies",
    "bacon",
    "beef",
    "chicken",
    "chorizo",
    "clam",
    "cod",
    "crab",
    "duck",
    "fish",
    "gelatin",
    "gelatine",
    "goat",
    "guanciale",
    "ham",
    "lamb",
    "lard",
    "lobster",
    "meat",
    "mussel",
    "mutton",
    "pancetta",
    "pepperoni",
    "pork",
    "prawn",
    "prosciutto",
    "salami",
    "salmon",
    "sausage",
    "seafood",
    "shrimp",
    "squid",
    "steak",
    "tuna",
    "turkey",
    "veal",
    "venison",
    "stockfish",
    "crayfish",
    "oyster",
    "mince",
)

ANIMAL_PRODUCTS = (
    "butter",
    "buttermilk",
    "cheese",
    "cream",
    "egg",
    "ghee",
    "honey",
    "milk",
    "mozzarella",
    "paneer",
    "parmesan",
    "pecorino",
    "ricotta",
    "yogurt",
    "yoghurt",
    "curd",
    "mascarpone",
    "custard",
    "whey",
    "parmigiano",
)

ALLERGENS: dict[str, tuple[str, ...]] = {
    "tree_nut": (
        "nut",
        "almond",
        "walnut",
        "pecan",
        "cashew",
        "pistachio",
        "hazelnut",
        "pine nut",
        "macadamia",
        "brazil nut",
        "chestnut",
        "praline",
        "marzipan",
        "nutella",
        "amaretto",
    ),
    "peanut": ("peanut", "groundnut", "peanut butter"),
    "dairy": (
        "butter",
        "buttermilk",
        "cheese",
        "cream",
        "ghee",
        "milk",
        "mozzarella",
        "paneer",
        "parmesan",
        "pecorino",
        "ricotta",
        "yogurt",
        "yoghurt",
        "curd",
        "mascarpone",
        "whey",
        "parmigiano",
        "lactose",
        "dairy",
        "khoa",
        "khoya",
        "custard",
    ),
    "egg": ("egg", "yolk", "mayonnaise", "meringue", "custard"),
    "gluten": (
        "flour",
        "wheat",
        "barley",
        "rye",
        "spelt",
        "semolina",
        "bread",
        "breadcrumb",
        "pasta",
        "spaghetti",
        "penne",
        "linguine",
        "lasagna",
        "lasagne",
        "noodle",
        "couscous",
        "bulgur",
        "biscuit",
        "cracker",
        "cake",
        "maida",
        "atta",
        "vermicelli",
        "beer",
        "gluten",
        "seitan",
        "ladyfinger",
        "savoiardi",
        "tortilla",
        "pastry",
    ),
    "fish": ("fish", "anchovy", "anchovies", "salmon", "tuna", "cod", "sardine", "stockfish"),
    "shellfish": (
        "shellfish",
        "shrimp",
        "prawn",
        "crab",
        "lobster",
        "clam",
        "mussel",
        "oyster",
        "scallop",
        "crayfish",
        "squid",
    ),
    "soy": ("soy", "soya", "tofu", "edamame", "miso", "tamari"),
    "sesame": ("sesame", "tahini", "gingelly"),
}

# Words in a question that name an allergen group (SPEC 5.4).
ALLERGEN_QUESTION_WORDS: dict[str, tuple[str, ...]] = {
    "tree_nut": ("nut", "tree nut", "almond", "walnut", "cashew", "pistachio", "hazelnut"),
    "peanut": ("peanut",),
    "dairy": ("dairy", "lactose", "milk"),
    "egg": ("egg",),
    "gluten": ("gluten", "celiac", "coeliac", "wheat"),
    "fish": ("fish",),
    "shellfish": ("shellfish", "seafood", "shrimp", "prawn"),
    "soy": ("soy", "soya"),
    "sesame": ("sesame",),
}


# Plant-based phrases that would otherwise match an animal-product word.
PLANT_PHRASES = re.compile(
    r"\b(?:coconut|almond|soy|soya|oat|rice|cashew)\s+(?:milk|cream|yogurt|yoghurt)\b"
    r"|\b(?:peanut|cocoa|nut|almond|shea)\s+butter\b|\bcream of tartar\b|\bbutter ?beans?\b",
    re.IGNORECASE,
)


@cache
def _pattern(terms: tuple[str, ...]) -> re.Pattern[str]:
    alternatives = "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True))
    return re.compile(rf"\b(?:{alternatives})(?:e?s)?\b", re.IGNORECASE)


def contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return _pattern(terms).search(text) is not None


def matching_lines(
    lines: Iterable[str], terms: tuple[str, ...], *, ignore_plant_phrases: bool = False
) -> list[str]:
    pattern = _pattern(terms)
    if ignore_plant_phrases:
        return [line for line in lines if pattern.search(PLANT_PHRASES.sub(" ", line))]
    return [line for line in lines if pattern.search(line)]


def diet_from(categories: Iterable[str], ingredients: list[str]) -> Diet | None:
    """Diet tag from Wikibooks categories, guarded by the ingredient lexicon."""
    cats = set(categories)
    if matching_lines(ingredients, MEAT_FISH):
        return None
    if "Vegan recipes" in cats and not matching_lines(
        ingredients, ANIMAL_PRODUCTS, ignore_plant_phrases=True
    ):
        return "vegan"
    if cats & {"Vegan recipes", "Vegetarian recipes"}:
        return "vegetarian"
    return None
