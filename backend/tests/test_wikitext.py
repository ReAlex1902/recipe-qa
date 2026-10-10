from pathlib import Path

import pytest

from recipe_qa.ingest.wikitext import (
    parse_minutes,
    parse_recipe,
    parse_summary,
    recipe_id_from_title,
    strip_markup,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("30 minutes", 30),
        ("1 hour", 60),
        ("½ hour", 30),
        ("1½ hours", 90),
        ("1 hour 30 minutes", 90),
        ("2 hours, 45 minutes", 165),
        ("30–60 minutes", 60),  # range: upper bound (SPEC 5.1)
        ("45-60 minutes", 60),
        ("15 minutes + 1 hour", 75),
        ("Prep: 15 minutes<br/>Cook: 1 hour", 75),
        ("40 min", 40),
        ("<10 minutes", 10),
        ("About 2 hours", 120),
        ("~1 hour", 60),
        ("'''Total:''' 20 minutes", 20),
        ("1.5–2 hours", 120),
        ("Soak: overnight<br/>Cook: 1 hour", None),  # 'overnight' cannot be bounded
        ("About 3 weeks", None),
        ("30", None),  # no unit: unknown
        ("", None),
    ],
)
def test_parse_minutes(text: str, expected: int | None) -> None:
    assert parse_minutes(text) == expected


def test_strip_markup_links_formatting_refs_templates() -> None:
    raw = (
        "'''Fry''' the [[Cookbook:Pancetta|pancetta]] in [[Cookbook:Olive Oil]]"
        '<ref name="a">{{cite book |title=X}}</ref> {{convert|5|cm}} <small>gently</small>'
    )
    assert strip_markup(raw) == "Fry the pancetta in Olive Oil gently"


def test_strip_markup_drops_images_and_files() -> None:
    assert strip_markup("[[Image:Foo.jpg|thumb|300px|A photo]] Text") == "Text"
    assert strip_markup("[[File:Foo.jpg|thumb]]Text") == "Text"


def test_parse_summary_both_template_spellings() -> None:
    a = parse_summary((FIXTURES / "carbonara.wiki").read_text())
    assert a["time"] == "1 hour"
    assert a["servings"] == "6"
    assert a["category"] == "Pasta recipes"
    b = parse_summary((FIXTURES / "tarator.wiki").read_text())
    assert b["category"] == "Soup recipes"
    assert "time" not in b


def test_recipe_id_from_title() -> None:
    assert recipe_id_from_title("Cookbook:Spaghetti alla Carbonara") == "spaghetti-alla-carbonara"
    assert (
        recipe_id_from_title("Cookbook:Tarator (Bulgarian Cucumber Soup)")
        == "tarator-bulgarian-cucumber-soup"
    )
    assert recipe_id_from_title("Cookbook:Tiramisù") == "tiramisu"


def test_parse_recipe_structure() -> None:
    r = parse_recipe(
        title="Cookbook:Tarator (Bulgarian Cucumber Soup)",
        url="https://en.wikibooks.org/wiki/Cookbook:Tarator_(Bulgarian_Cucumber_Soup)",
        revision_id=123,
        wikitext=(FIXTURES / "tarator.wiki").read_text(),
        categories=["Soup recipes", "Recipes using yogurt", "Bulgarian recipes"],
    )
    assert r.id == "tarator-bulgarian-cucumber-soup"
    assert r.title == "Tarator (Bulgarian Cucumber Soup)"
    assert r.revision_id == 123
    assert r.total_minutes is None
    assert r.ingredients[0] == "500 g plain yogurt"
    assert "chopped walnuts (optional)" in r.ingredients[-1]
    assert r.steps[0].startswith("Mix all the ingredients")
    assert len(r.steps) == 2
    assert "snezhanka" in r.notes
    assert r.dish_categories == ["Soup"]
    assert r.cuisines == ["Bulgarian"]


def test_parse_recipe_intro_and_time() -> None:
    r = parse_recipe(
        title="Cookbook:Spaghetti alla Carbonara",
        url="https://en.wikibooks.org/wiki/Cookbook:Spaghetti_alla_Carbonara",
        revision_id=1,
        wikitext=(FIXTURES / "carbonara.wiki").read_text(),
        categories=["Italian recipes", "Recipes using bacon"],
    )
    assert r.time_text == "1 hour"
    assert r.total_minutes == 60
    assert r.servings == "6"
    assert r.intro.startswith("Spaghetti alla carbonara is an Italian pasta dish")
    assert "Alberini" not in r.intro
    assert r.ingredients == [
        "150 g guanciale, diced",
        "4 egg yolks",
        "50 g pecorino romano, grated",
        "400 g spaghetti",
    ]
    assert r.steps[1] == "Fry the guanciale until crisp."
    assert r.dish_categories == ["Pasta"]
    assert r.cuisines == ["Italian"]


def test_parse_recipe_subsections_inherit_parent_section() -> None:
    wiki = (
        "{{recipesummary|category=Cookie recipes|time=About 2 hours}}\n"
        "==Ingredients==\n===Waffles===\n*4 cups flour\n===Filling===\n*1 cup butter\n"
        "==Procedure==\n=== Waffles ===\n#Mix well.\n=== Filling ===\n#Melt the butter.\n"
        "==Notes==\nServe warm.\n"
    )
    r = parse_recipe(title="Cookbook:X", url="u", revision_id=1, wikitext=wiki, categories=[])
    assert r.ingredients == ["4 cups flour", "1 cup butter"]
    assert r.steps == ["Mix well.", "Melt the butter."]
    assert r.notes == "Notes: Serve warm."
    assert r.dish_categories == ["Cookie"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [("30 minutes to 1 hour", 60), ("1/2 hour", 30), ("1:30 hours", 90)],
)
def test_parse_minutes_review_cases(text: str, expected: int) -> None:
    assert parse_minutes(text) == expected
