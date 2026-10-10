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
    "tree_nut": (
        "nut", "tree nut", "almond", "walnut", "cashew", "pistachio", "hazelnut", "pine nut",
        "pecan", "macadamia",
    ),
    "peanut": ("peanut",),
    "dairy": ("dairy", "lactose", "milk", "cheese", "butter", "cream", "yogurt", "yoghurt"),
    "egg": ("egg",),
    "gluten": ("gluten", "celiac", "coeliac", "wheat", "flour"),
    "fish": ("fish",),
    "shellfish": ("shellfish", "seafood", "shrimp", "prawn", "clam", "crab"),
    "soy": ("soy", "soya", "tofu"),
    "sesame": ("sesame", "tahini"),
}  # fmt: skip


# Allergen group names (not single foods). In a question they name the whole group:
# "without nuts" excludes every tree-nut line, "without cheese" only cheese.
ALLERGEN_GROUP_NAMES: dict[str, tuple[str, ...]] = {
    "tree_nut": ("nut", "tree nut"),
    "peanut": ("peanut",),
    "dairy": ("dairy", "lactose"),
    "egg": ("egg",),
    "gluten": ("gluten", "wheat"),
    "fish": ("fish",),
    "shellfish": ("shellfish",),
    "soy": ("soy", "soya"),
    "sesame": ("sesame",),
}

# Groups where plant phrases such as "coconut milk" must not count as a match.
PLANT_PHRASE_GROUPS = frozenset({"dairy", "egg"})

# An ingredient line that is another recipe ("1 recipe Flaky Pie Crust") cannot be
# checked against the lexicon, so the diet of the whole recipe is unknown.
_SUB_RECIPE = re.compile(r"^\s*(?:\d+(?:[./]\d+)?|[½¼¾⅓⅔]|an?|one|two)\s+recipes?\b", re.IGNORECASE)

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
    if matching_lines(ingredients, MEAT_FISH) or has_sub_recipe(ingredients):
        return None
    if "Vegan recipes" in cats and not matching_lines(
        ingredients, ANIMAL_PRODUCTS, ignore_plant_phrases=True
    ):
        return "vegan"
    if cats & {"Vegan recipes", "Vegetarian recipes"}:
        return "vegetarian"
    return None


def has_sub_recipe(ingredients: Iterable[str]) -> bool:
    return any(_SUB_RECIPE.search(i) for i in ingredients)


# Food words that name a family: "without cheese" must also drop paneer and Parmigiano.
# Conservative on purpose (a false match drops a recipe, a miss serves a wrong one).
FOOD_FAMILIES: dict[str, tuple[str, ...]] = {
    "cheese": (
        "cheese", "parmesan", "parmigiano", "pecorino", "mozzarella", "ricotta", "mascarpone",
        "paneer", "feta", "cheddar", "gruyere", "gruyère", "halloumi", "gorgonzola",
        "provolone", "fontina", "asiago", "grana padano", "queso", "cotija",
    ),
    "milk": ("milk", "buttermilk", "condensed milk", "evaporated milk", "khoa", "khoya"),
    "cream": (
        "cream", "sour cream", "creme fraiche", "crème fraîche", "mascarpone", "half-and-half",
    ),
    "butter": ("butter", "ghee"),
    "yogurt": ("yogurt", "yoghurt", "curd", "dahi"),
    "flour": ("flour", "maida", "atta", "semolina"),
    "onion": ("onion", "shallot", "scallion", "spring onion", "leek"),
}  # fmt: skip


def exclusion_terms(word: str) -> tuple[tuple[str, ...], bool]:
    """Lexicon terms for an excluded word, and whether plant phrases are ignored.

    An allergen group name ("nuts", "dairy") expands to the group; a food family word
    ("cheese", "milk") to its family; any other word matches itself.
    """
    for group, names in ALLERGEN_GROUP_NAMES.items():
        if word in names:
            return ALLERGENS[group], group in PLANT_PHRASE_GROUPS
    terms = FOOD_FAMILIES.get(word, (word,))
    in_plant_group = any(t in ALLERGENS[g] for t in terms for g in PLANT_PHRASE_GROUPS)
    return terms, in_plant_group
