"""Properties of the committed corpus (SPEC AC6)."""

import json
from pathlib import Path

from recipe_qa.ingest.selection import TITLES
from recipe_qa.models import Recipe

CORPUS = Path(__file__).parents[1] / "data" / "corpus.json"


def _recipes() -> list[Recipe]:
    return [Recipe(**r) for r in json.loads(CORPUS.read_text())["recipes"]]


def test_size_and_variety() -> None:
    recipes = _recipes()
    assert 40 <= len(recipes) <= 60
    assert len(recipes) == len(TITLES)
    assert len({c for r in recipes for c in r.cuisines}) >= 4
    assert len({d for r in recipes for d in r.dish_categories}) >= 4


def test_every_recipe_has_ingredients_steps_and_a_wikibooks_url() -> None:
    for r in _recipes():
        assert r.ingredients, r.id
        assert r.steps, r.id
        assert r.url.startswith("https://en.wikibooks.org/wiki/Cookbook:"), r.id


def test_same_dish_appears_more_than_once() -> None:
    ids = {r.id for r in _recipes()}
    assert {"spaghetti-alla-carbonara", "carbonara-pasta"} <= ids
    assert {"guacamole-i", "chunky-guacamole"} <= ids


def test_constraint_coverage() -> None:
    quick_veg = [
        r
        for r in _recipes()
        if r.diet in ("vegetarian", "vegan")
        and r.total_minutes is not None
        and r.total_minutes <= 30
    ]
    assert len(quick_veg) >= 3


def test_committed_embeddings_match_the_corpus() -> None:
    from recipe_qa.config import Settings
    from recipe_qa.embeddings_file import load_vectors

    s = Settings(google_api_key="unused")  # type: ignore[arg-type]
    vectors = load_vectors(
        CORPUS.with_name("embeddings"), _recipes(), model=s.embedding_model, dim=s.embedding_dim
    )
    assert vectors is not None, "corpus changed: run `make embed`"
