from collections.abc import Sequence
from typing import Any

import numpy as np
import pytest

from recipe_qa.models import Recipe


def make_recipe(id: str, **kw: Any) -> Recipe:
    defaults: dict[str, Any] = {
        "id": id,
        "title": id.replace("-", " ").title(),
        "url": f"https://en.wikibooks.org/wiki/Cookbook:{id}",
        "revision_id": 1,
        "categories": [],
        "dish_categories": [],
        "cuisines": [],
        "time_text": None,
        "total_minutes": None,
        "servings": None,
        "diet": None,
        "intro": "",
        "ingredients": ["salt"],
        "steps": ["Cook."],
        "notes": "",
    }
    defaults.update(kw)
    return Recipe(**defaults)


@pytest.fixture
def small_corpus() -> list[Recipe]:
    return [
        make_recipe(
            "spaghetti-alla-carbonara",
            title="Spaghetti alla Carbonara",
            cuisines=["Italian"],
            dish_categories=["Pasta"],
            total_minutes=60,
            ingredients=["400 g spaghetti", "150 g guanciale", "4 egg yolks", "pecorino"],
        ),
        make_recipe(
            "carbonara-pasta",
            title="Carbonara Pasta",
            cuisines=["Italian"],
            ingredients=["4 eggs", "pancetta", "lemon zest", "spaghetti"],
        ),
        make_recipe(
            "paneer-butter-masala",
            title="Paneer Butter Masala",
            cuisines=["Indian"],
            diet="vegetarian",
            total_minutes=25,
            ingredients=["200 g paneer", "2 onions", "butter", "tomato"],
        ),
        make_recipe(
            "potato-curry",
            title="Potato Curry (Aloo Masala)",
            cuisines=["Indian"],
            diet="vegan",
            total_minutes=20,
            ingredients=["4 potatoes", "1 onion", "turmeric"],
        ),
        make_recipe(
            "tiramisu",
            title="Tiramisù",
            dish_categories=["Dessert"],
            total_minutes=75,
            diet="vegetarian",
            ingredients=["mascarpone", "ladyfingers", "coffee", "eggs"],
        ),
    ]


class FakeEmbedder:
    """Deterministic embedder: one dimension per keyword in VOCAB."""

    VOCAB = ("pasta", "curry", "dessert", "egg", "soup")

    def __init__(self) -> None:
        self.calls = 0

    def _vec(self, text: str) -> list[float]:
        t = text.lower()
        v = [float(t.count(w)) for w in self.VOCAB]
        return v if any(v) else [0.01] * len(self.VOCAB)

    def embed_documents(self, texts: Sequence[str]) -> np.ndarray:
        self.calls += 1
        return np.array([self._vec(t) for t in texts])

    def embed_query(self, text: str) -> np.ndarray:
        self.calls += 1
        return np.array(self._vec(text))
