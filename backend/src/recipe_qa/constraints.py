"""Deterministic question analysis: hard constraints, allergy mode, safety pre-check.

No LLM is used here on purpose: these decisions must be predictable, testable and free
(SPEC 4, 5.1, 5.4). Anything the patterns miss falls back to the model's own judgement
(soft ranking via retrieval, `safety` decision in the prompt).
"""

import re

from recipe_qa.lexicon import ALLERGEN_QUESTION_WORDS, contains_any
from recipe_qa.models import Constraints, Diet

_WORD_NUMBERS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
_BOUND = (
    r"(?:under|below|less than|fewer than|within|at most|no more than|not more than|up to|"
    r"max(?:imum)?(?: of)?|in(?: under| less than)?|takes?)"
)
# A number is required ("within minutes" is not a limit); "an hour" counts as 1 hour.
_DUR = (
    r"(?:(?P<half_an>half an? hour)|"
    r"(?:\d+(?:\.\d+)?\s*(?:-|to)\s*)?"  # a range: the upper bound is the limit
    r"(?P<num>\d+(?:\.\d+)?|one|two|three|four|five)[\s-]*(?P<unit>hours?|hrs?|minutes?|mins?)|"
    r"(?P<an>an?) (?P<unit_an>hour))\b(?P<and_half>\s+and\s+a\s+half)?"
)
_TIME_RES = (
    re.compile(rf"\b{_BOUND}\s+{_DUR}", re.IGNORECASE),
    # adjectival form: "a 30-minute dinner"
    re.compile(r"\b(?P<num>\d+(?:\.\d+)?)-(?P<unit>hours?|hrs?|minutes?|mins?)\b", re.IGNORECASE),
    re.compile(rf"\b{_DUR}\s+or\s+(?:less|under|fewer)\b", re.IGNORECASE),
)

_VEGAN_RE = re.compile(r"(?<!non-)(?<!non )\b(?:vegan|plant[- ]based)\b", re.IGNORECASE)
_VEGETARIAN_RE = re.compile(
    r"(?<!non-)(?<!non )\b(?:vegetarian|veggie|meatless|meat[- ]free)\b"
    r"|\b(?:no|without)\s+meat\b",
    re.IGNORECASE,
)

_EXCLUDE_RES = (
    re.compile(r"\b(?:without|no)\s+([a-z]+)", re.IGNORECASE),
    re.compile(r"\b([a-z]+)[- ]free\b", re.IGNORECASE),
)
# Words after "no"/"without" or before "-free" that are not ingredients.
_NOT_INGREDIENTS = frozenset(
    {
        "more",
        "longer",
        "less",
        "time",
        "cooking",
        "oven",
        "stove",
        "meat",
        "fuss",
        "added",
        "fat",
        "the",
        "a",
        "an",
        "any",
        "cook",
        "baking",
        "hassle",
        "frills",
        "allergy",
        "allergen",
        "allergens",
        "risk",
        "problem",
        "need",
        "idea",
        "clue",
        "doubt",
        "using",
        "use",
        "way",
        "rush",
        "worries",
        "special",
        "equipment",
        "experience",
        "having",
        "shop",
        "shopping",
        "going",
        "getting",
        "spending",
        "waiting",
        "much",
        "too",
        "skill",
        "skills",
        "money",
        "recipe",
    }
)

_ALLERGY_WORDS_RE = re.compile(
    r"\b(?:allerg\w*|intoleran\w*|celiac|coeliac|anaphyla\w*)\b", re.IGNORECASE
)

_SAFETY_RES = (
    # Medical situations: symptoms or a reaction happening now.
    re.compile(
        r"\b(?:allergic reaction|anaphyla\w*|epi-?pen|hives|can'?t breathe|"
        r"(?:throat|lips?|tongue|face)\s+(?:is |are )?(?:swelling|swollen|closing|tingling)|"
        r"food poisoning|poisoned|vomiting|overdose)\b",
        re.IGNORECASE,
    ),
    # Dangerous food handling.
    re.compile(
        r"\braw\s+(?:chicken|poultry|pork|kidney beans?)\b|\bpoison\w*\b|"
        r"\bspoiled\s+(?:meat|chicken|fish)\b|\b(?:moldy|mouldy)\b",
        re.IGNORECASE,
    ),
)


def _minutes(m: re.Match[str]) -> int:
    g = m.groupdict()
    if g.get("half_an"):
        return 30
    raw = (g.get("num") or g.get("an") or "1").lower()
    unit = (g.get("unit") or g.get("unit_an") or "minute").lower()
    value = float(_WORD_NUMBERS[raw]) if raw in _WORD_NUMBERS else float(raw)
    if unit.startswith("h"):
        value *= 60
        if g.get("and_half"):
            value += 30
    return round(value)


def _singular(word: str) -> str:
    if word.endswith("ies"):
        return word[:-3] + "y"
    if word.endswith("oes"):
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _is_allergen_word(word: str) -> bool:
    return any(contains_any(word, terms) for terms in ALLERGEN_QUESTION_WORDS.values())


def parse_max_minutes(question: str) -> int | None:
    found = [_minutes(m) for regex in _TIME_RES for m in regex.finditer(question)]
    return min(found) if found else None


# "not vegetarian", "I'm not a vegan": a negated diet is no constraint.
_NEGATED_DIET_RE = re.compile(
    r"\bnot\s+(?:an?\s+)?(?:vegetarian|vegan|veggie|plant[- ]based)\b", re.IGNORECASE
)


def parse_diet(question: str) -> Diet | None:
    question = _NEGATED_DIET_RE.sub(" ", question)
    if _VEGAN_RE.search(question):
        return "vegan"
    if _VEGETARIAN_RE.search(question):
        return "vegetarian"
    return None


# A clause that asks about one dish ("is the pesto nut-free?", "is it ...", "does this
# contain ...", "is the carbonara made without eggs?") is not a request to filter that dish
# out. "Is there a nut-free dessert?" has no such determiner and stays a filter.
_DISH_CLAUSE_RE = re.compile(
    r"\b(?:is|are|does|do)\s+(?:the|this|that|it|these|those)\b[^?.;,]*", re.IGNORECASE
)


# "Is it possible to make a dessert without eggs?": an article between the clause start and
# the exclusion means the user asks for some dish, not about this one.
_REQUEST_MARKER_RE = re.compile(r"\b(?:a|an|any|some)\b", re.IGNORECASE)


def parse_excluded(question: str) -> list[str]:
    found: list[str] = []
    dish_clauses = [m.span() for m in _DISH_CLAUSE_RE.finditer(question)]
    for regex in _EXCLUDE_RES:
        for m in regex.finditer(question):
            if any(
                a <= m.start() < b and not _REQUEST_MARKER_RE.search(question, a, m.start())
                for a, b in dish_clauses
            ):
                continue
            word = m.group(1).lower()
            if word in _NOT_INGREDIENTS:
                continue
            found.append(_singular(word))
    # Keep order of appearance in the question.
    return sorted(dict.fromkeys(found), key=lambda w: question.lower().find(w))


def parse_constraints(question: str) -> Constraints:
    return Constraints(
        max_total_minutes=parse_max_minutes(question),
        diet=parse_diet(question),
        exclude_ingredients=parse_excluded(question),
    )


# "Does X contain nuts?", "is there egg in it?", "any dairy?" (SPEC 5.4.2)
# Containment questions: "does it contain nuts?", "does the naan have any butter?",
# "is there any egg in it?". A bare "any" ("any fish recipes?") is a request, not this.
_CONTAINS_RE = re.compile(
    r"\b(?:contains?|containing|include[sd]?|including)\b"
    r"|\b(?:does|do|did)\b[^?.;]*\bha(?:s|ve)\b"
    r"|\bany\b[^?.;]*\bin\s+(?:it|this|that|the|them|these|those)\b",
    re.IGNORECASE,
)


def is_allergy_question(question: str) -> bool:
    if _ALLERGY_WORDS_RE.search(question):
        return True
    if _CONTAINS_RE.search(question) and any(
        contains_any(question, words) for words in ALLERGEN_QUESTION_WORDS.values()
    ):
        return True
    for regex in _EXCLUDE_RES:
        if any(_is_allergen_word(m.group(1)) for m in regex.finditer(question)):
            return True
    return False


def allergens_in_question(question: str) -> list[str]:
    """Allergen groups named in an allergy question, in lexicon order (SPEC 5.4)."""
    if not is_allergy_question(question):
        return []
    return [
        group for group, words in ALLERGEN_QUESTION_WORDS.items() if contains_any(question, words)
    ]


def safety_precheck(question: str) -> bool:
    """True if the question must be refused with `safety` before retrieval."""
    return any(regex.search(question) for regex in _SAFETY_RES)
