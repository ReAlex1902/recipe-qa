"""Precomputed corpus embeddings, committed next to the corpus (ADR-0001).

Serverless instances start often; embedding 52 documents at every cold start would cost
52 requests of the per-minute free-tier quota and ~1.3 s. The vectors are computed once
by `python -m recipe_qa.embeddings_file` and used only if model, dimension and the
corpus fingerprint match; otherwise the service embeds at startup as before.
"""

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

import numpy as np

from recipe_qa.models import Recipe


def corpus_fingerprint(recipes: Sequence[Recipe]) -> str:
    digest = hashlib.sha256()
    for r in recipes:
        digest.update(r.id.encode())
        digest.update(r.as_prompt_text().encode())
    return digest.hexdigest()


def save_vectors(
    base: Path, recipes: Sequence[Recipe], vectors: np.ndarray, *, model: str, dim: int
) -> None:
    np.save(base.with_suffix(".npy"), vectors.astype(np.float32), allow_pickle=False)
    manifest = {
        "model": model,
        "dim": dim,
        "corpus_sha256": corpus_fingerprint(recipes),
        "ids": [r.id for r in recipes],
    }
    base.with_suffix(".json").write_text(json.dumps(manifest, indent=1) + "\n")


def load_vectors(
    base: Path, recipes: Sequence[Recipe], *, model: str, dim: int
) -> np.ndarray | None:
    try:
        manifest = json.loads(base.with_suffix(".json").read_text())
        vectors: np.ndarray = np.load(base.with_suffix(".npy"), allow_pickle=False)
    except (OSError, ValueError):
        return None
    if (
        manifest.get("model") != model
        or manifest.get("dim") != dim
        or manifest.get("corpus_sha256") != corpus_fingerprint(recipes)
        or vectors.shape != (len(recipes), dim)
    ):
        return None
    return vectors


def main() -> None:
    from recipe_qa.api import load_corpus
    from recipe_qa.config import Settings
    from recipe_qa.llm import GeminiEmbedder, make_client

    settings = Settings()  # type: ignore[call-arg]
    recipes = load_corpus(settings.corpus_path)
    client = make_client(settings.google_api_key.get_secret_value(), 60)
    embedder = GeminiEmbedder(client, settings.embedding_model, settings.embedding_dim)
    vectors = embedder.embed_documents([r.as_prompt_text() for r in recipes])
    base = settings.corpus_path.with_name("embeddings")
    save_vectors(base, recipes, vectors, model=settings.embedding_model, dim=settings.embedding_dim)
    print(f"wrote {vectors.shape} to {base}.npy")


if __name__ == "__main__":
    main()
