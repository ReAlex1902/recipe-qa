"""Turn Wikibooks Cookbook wikitext into a structured `Recipe`.

Wikibooks recipes are semi-structured: most have a `Recipe summary` template and
`Ingredients` / `Procedure` sections, but headings, list markup and fields vary. The parser
keeps what it can identify and puts any other prose into `notes`, so no text is lost.
"""

import re
import unicodedata

from recipe_qa.lexicon import diet_from
from recipe_qa.models import Recipe

# Cuisine names that appear as "<Name> recipes" categories in the selected pages.
CUISINES = frozenset(
    {
        "Afghan",
        "African",
        "Algerian",
        "American",
        "Austrian",
        "Bengali",
        "Bulgarian",
        "Chinese",
        "Dutch",
        "English",
        "French",
        "German",
        "Greek",
        "Hungarian",
        "Igbo",
        "Indian",
        "Irish",
        "Italian",
        "Japanese",
        "Kenyan",
        "Mediterranean",
        "Mexican",
        "Nigerian",
        "Pakistani",
        "Polish",
        "Russian",
        "Spanish",
        "Thai",
        "Turkish",
        "United States",
        "Vietnamese",
    }
)
DISH_CATEGORIES = frozenset(
    {
        "Appetizer",
        "Bread",
        "Curry",
        "Dessert",
        "Main course",
        "Pasta",
        "Rice",
        "Salad",
        "Sauce",
        "Side dish",
        "Snack",
        "Soup",
        "Beverage",
        "Breakfast",
        "Stew",
        "Cookie",
        "Cake",
        "Pie",
        "Candy",
        "Cheesecake",
    }
)

INGREDIENT_HEADINGS = ("ingredient",)
STEP_HEADINGS = ("procedure", "method", "direction", "instruction", "preparation", "steps")
DROP_HEADINGS = ("reference", "see also", "external link", "gallery", "source")

_SUMMARY_RE = re.compile(r"\{\{\s*recipe\s*summary", re.IGNORECASE)
_HEADING_RE = re.compile(r"^(={2,6})\s*(.+?)\s*\1\s*$", re.MULTILINE)

_NUMBER_WORDS = {"½": 0.5, "¼": 0.25, "¾": 0.75, "⅓": 1 / 3, "⅔": 2 / 3}
_NUM = r"\d+(?:[.,]\d+)?[½¼¾⅓⅔]?|[½¼¾⅓⅔]"
_DURATION_RE = re.compile(
    rf"(?P<num>{_NUM})(?:\s*[-–—]\s*(?P<num2>{_NUM}))?\s*"
    r"(?P<unit>hours?|hrs?|h\b|minutes?|mins?|m\b|days?|weeks?)",
    re.IGNORECASE,
)
_UNBOUNDED_RE = re.compile(r"overnight|several|plus|\+\s*$|or more", re.IGNORECASE)


def recipe_id_from_title(title: str) -> str:
    name = title.removeprefix("Cookbook:")
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")


def _to_number(token: str) -> float:
    total = 0.0
    for symbol, value in _NUMBER_WORDS.items():
        if symbol in token:
            total += value
            token = token.replace(symbol, "")
    if token:
        total += float(token.replace(",", "."))
    return total


def parse_minutes(text: str) -> int | None:
    """Total minutes stated in a free-text time field; upper bound for ranges (SPEC 5.1).

    Returns None when the text has no duration with a unit, or contains an unbounded part
    such as "overnight" or "several hours".
    """
    clean = strip_markup(re.sub(r"<br\s*/?>", "; ", text, flags=re.IGNORECASE))
    if not clean or _UNBOUNDED_RE.search(clean):
        return None
    # "1/2 hour" -> "0.5 hour"; "1:30 hours" -> "1.5 hours"
    clean = re.sub(r"\b(\d+)/(\d+)\b", lambda m: f"{int(m[1]) / int(m[2]):g}", clean)
    clean = re.sub(r"\b(\d+):(\d{2})\b", lambda m: f"{int(m[1]) + int(m[2]) / 60:g}", clean)
    matches = list(_DURATION_RE.finditer(clean))
    if not matches:
        return None
    parts: list[float] = []
    for n, m in enumerate(matches):
        value = _to_number(m.group("num2") or m.group("num"))
        unit = m.group("unit").lower()
        if unit.startswith(("d", "w")):
            return None  # multi-day recipes are out of any time constraint we parse
        part = value * 60 if unit.startswith("h") else value
        between = clean[matches[n - 1].end() : m.start()] if n else ""
        if parts and re.fullmatch(r"\s*(?:to|or|[-–—])\s*", between):
            parts[-1] = max(parts[-1], part)  # "30 minutes to 1 hour": a range
        else:
            parts.append(part)
    return round(sum(parts))


def _remove_balanced(text: str, opener: str, closer: str) -> str:
    """Remove every balanced `opener ... closer` span, including nested ones."""
    out: list[str] = []
    depth = 0
    i = 0
    while i < len(text):
        if text.startswith(opener, i):
            depth += 1
            i += len(opener)
        elif depth and text.startswith(closer, i):
            depth -= 1
            i += len(closer)
        else:
            if depth == 0:
                out.append(text[i])
            i += 1
    return "".join(out)


def _link_text(match: re.Match[str]) -> str:
    target, _, label = match.group(1).partition("|")
    return label or target.split(":", 1)[-1]


def strip_markup(text: str) -> str:
    """Wikitext to plain text: links to their labels, no templates, refs, tags, images."""
    text = re.sub(r"<ref[^>]*/>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<ref[^>]*>.*?</ref>", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    text = _remove_balanced(text, "{{", "}}")
    # Images/files may contain nested links in their caption.
    text = re.sub(r"\[\[(?:Image|File):(?:[^\[\]]|\[\[[^\]]*\]\])*\]\]", "", text, flags=re.I)
    text = re.sub(r"\[\[Category:[^\]]*\]\]", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\[\[([^\]]+)\]\]", _link_text, text)
    text = re.sub(r"\[https?://\S+\s+([^\]]+)\]", r"\1", text)
    text = re.sub(r"\[https?://\S+\]", "", text)
    text = re.sub(r"'{2,}", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&nbsp;", " ").replace("__NOTOC__", "")
    return re.sub(r"[ \t]+", " ", text).strip()


def _summary_body(wikitext: str) -> str | None:
    m = _SUMMARY_RE.search(wikitext) or re.search(r"\{\{\s*recipesummary", wikitext, re.I)
    if not m:
        return None
    depth, i = 0, m.start()
    while i < len(wikitext):
        if wikitext.startswith("{{", i):
            depth += 1
            i += 2
        elif wikitext.startswith("}}", i):
            depth -= 1
            i += 2
            if depth == 0:
                return wikitext[m.end() : i - 2]
        else:
            i += 1
    return None


def parse_summary(wikitext: str) -> dict[str, str]:
    """Fields of the `Recipe summary` template, keys lower-cased, empty values dropped."""
    body = _summary_body(wikitext)
    if body is None:
        return {}
    # Split on top-level pipes only (links and nested templates contain pipes too).
    fields: list[str] = []
    depth, start = 0, 0
    for i, ch in enumerate(body):
        if body.startswith(("[[", "{{"), i):
            depth += 1
        elif body.startswith(("]]", "}}"), i):
            depth -= 1
        elif ch == "|" and depth == 0:
            fields.append(body[start:i])
            start = i + 1
    fields.append(body[start:])
    result: dict[str, str] = {}
    for field in fields:
        key, sep, value = field.partition("=")
        if sep and value.strip():
            result[key.strip().lower()] = value.strip()
    return result


def _kind(heading: str) -> str | None:
    if any(word in heading for word in DROP_HEADINGS):
        return "drop"
    if any(word in heading for word in INGREDIENT_HEADINGS):
        return "ingredients"
    if any(word in heading for word in STEP_HEADINGS):
        return "steps"
    return None


def _sections(wikitext: str) -> tuple[str, list[tuple[str, str, str]]]:
    """(text before the first heading, [(heading, kind, body)]).

    A subsection without a kind of its own inherits the kind of its parent, so
    `=== Filling ===` under `== Ingredients ==` is still ingredients.
    """
    headings = list(_HEADING_RE.finditer(wikitext))
    if not headings:
        return wikitext, []
    lead = wikitext[: headings[0].start()]
    sections = []
    parents: list[tuple[int, str]] = []  # (level, kind) of enclosing headings
    for n, h in enumerate(headings):
        level, heading = len(h.group(1)), strip_markup(h.group(2)).lower()
        while parents and parents[-1][0] >= level:
            parents.pop()
        kind = _kind(heading) or (parents[-1][1] if parents else "notes")
        parents.append((level, kind))
        end = headings[n + 1].start() if n + 1 < len(headings) else len(wikitext)
        sections.append((heading, kind, wikitext[h.end() : end]))
    return lead, sections


def _list_items(body: str) -> list[str]:
    items = []
    for line in body.splitlines():
        m = re.match(r"^\s*[*#:]+\s*(.*)$", line)
        if m:
            item = strip_markup(m.group(1))
            if item:
                items.append(item)
    return items


def _prose(body: str) -> str:
    text = "\n".join(re.sub(r"^\s*[*#:]+\s*", "", line) for line in body.splitlines())
    return " ".join(strip_markup(text).split())


def _category_names(categories: list[str], summary_category: str | None) -> list[str]:
    names = [c.removesuffix(" recipes") for c in categories if c.endswith(" recipes")]
    if summary_category:
        names.insert(0, strip_markup(summary_category).removesuffix(" recipes").strip())
    return list(dict.fromkeys(n for n in names if n))


def parse_recipe(
    *, title: str, url: str, revision_id: int, wikitext: str, categories: list[str]
) -> Recipe:
    summary = parse_summary(wikitext)
    lead, sections = _sections(wikitext)

    ingredients: list[str] = []
    steps: list[str] = []
    notes: list[str] = []
    for heading, kind, body in sections:
        if kind == "ingredients":
            ingredients.extend(_list_items(body))
        elif kind == "steps":
            steps.extend(_list_items(body) or ([_prose(body)] if _prose(body) else []))
        elif kind == "notes":
            text = _prose(body)
            if text:
                notes.append(f"{heading.capitalize()}: {text}" if heading else text)

    # The line with the {{recipe}} template is site navigation, not content.
    lead = re.sub(r"^.*\{\{\s*recipe\s*\}\}.*$", "", lead, flags=re.MULTILINE | re.IGNORECASE)
    intro = _prose(lead)

    names = _category_names(categories, summary.get("category"))
    time_text = strip_markup(summary["time"]).strip() if "time" in summary else None
    return Recipe(
        id=recipe_id_from_title(title),
        title=title.removeprefix("Cookbook:"),
        url=url,
        revision_id=revision_id,
        categories=sorted(categories),
        dish_categories=[n for n in names if n in DISH_CATEGORIES],
        cuisines=[n for n in names if n in CUISINES],
        time_text=time_text or None,
        total_minutes=parse_minutes(summary["time"]) if "time" in summary else None,
        servings=strip_markup(summary["servings"]) if "servings" in summary else None,
        diet=diet_from(categories, ingredients),
        intro=intro,
        ingredients=ingredients,
        steps=steps,
        notes=" ".join(notes),
    )
