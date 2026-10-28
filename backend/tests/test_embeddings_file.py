"""Precomputed corpus embeddings (backend/data/embeddings.*) must match the corpus."""

from pathlib import Path

import numpy as np

from recipe_qa.embeddings_file import corpus_fingerprint, load_vectors, save_vectors

from .conftest import make_recipe


def test_roundtrip_and_fingerprint_check(tmp_path: Path) -> None:
    recipes = [make_recipe("a"), make_recipe("b")]
    vectors = np.arange(6, dtype=np.float32).reshape(2, 3)
    save_vectors(tmp_path / "emb", recipes, vectors, model="m", dim=3)
    loaded = load_vectors(tmp_path / "emb", recipes, model="m", dim=3)
    assert loaded is not None and np.array_equal(loaded, vectors)
    # different model, dim or corpus content -> not usable
    assert load_vectors(tmp_path / "emb", recipes, model="other", dim=3) is None
    assert load_vectors(tmp_path / "emb", recipes, model="m", dim=4) is None
    changed = [make_recipe("a"), make_recipe("b", ingredients=["pepper"])]
    assert load_vectors(tmp_path / "emb", changed, model="m", dim=3) is None
    assert corpus_fingerprint(recipes) != corpus_fingerprint(changed)


def test_missing_file_returns_none(tmp_path: Path) -> None:
    assert load_vectors(tmp_path / "nope", [make_recipe("a")], model="m", dim=3) is None
