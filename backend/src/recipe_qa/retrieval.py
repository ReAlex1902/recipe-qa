"""Hybrid retrieval: BM25 + embedding similarity, fused with reciprocal rank fusion.

See ADR-0001. Hard filters run first, so ranking only orders recipes that fit the
constraints. If the embedding provider fails, retrieval degrades to BM25 only and says so.
"""

import logging
import math
import re
import unicodedata
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from recipe_qa.filters import satisfies
from recipe_qa.models import Constraints, Recipe

log = logging.getLogger(__name__)

TITLE_WEIGHT = 3  # title tokens are counted this many times
RRF_K = 60  # standard constant from Cormack et al. (2009)

STOPWORDS = frozenset(
    [
        "a",
        "about",
        "after",
        "all",
        "also",
        "an",
        "and",
        "any",
        "are",
        "as",
        "at",
        "be",
        "been",
        "but",
        "by",
        "can",
        "could",
        "do",
        "does",
        "did",
        "for",
        "from",
        "get",
        "got",
        "have",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "just",
        "like",
        "make",
        "makes",
        "making",
        "me",
        "my",
        "need",
        "of",
        "on",
        "or",
        "our",
        "recipe",
        "recipes",
        "should",
        "so",
        "some",
        "something",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "this",
        "to",
        "under",
        "up",
        "use",
        "using",
        "want",
        "was",
        "we",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
        "without",
        "would",
        "you",
        "your",
        "can't",
        "dont",
        "don't",
        "im",
        "i'm",
        "is",
        "it's",
        "whats",
        "what's",
        "give",
        "tell",
        "cook",
        "cooking",
        "dish",
        "dishes",
        "good",
        "best",
        "minutes",
        "minute",
        "hour",
        "hours",
        "less",
        "than",
        "within",
    ]
)


class Embedder(Protocol):
    def embed_documents(self, texts: Sequence[str]) -> np.ndarray: ...
    def embed_query(self, text: str) -> np.ndarray: ...


def _stem(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith(("oes", "shes", "ches")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def tokenize(text: str) -> list[str]:
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return [_stem(w) for w in re.findall(r"[a-z0-9]+", folded) if w not in STOPWORDS]


class BM25Index:
    def __init__(self, recipes: Sequence[Recipe], k1: float = 1.5, b: float = 0.75) -> None:
        self._ids = [r.id for r in recipes]
        self._docs = [self._doc_tokens(r) for r in recipes]
        self._tf = [Counter(d) for d in self._docs]
        self._len = [len(d) for d in self._docs]
        self._avg = sum(self._len) / max(len(self._len), 1)
        df = Counter(t for d in self._docs for t in set(d))
        n = len(self._docs)
        self._idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self._k1, self._b = k1, b

    @staticmethod
    def _doc_tokens(r: Recipe) -> list[str]:
        meta = " ".join([*r.cuisines, *r.dish_categories, r.diet or ""])
        body = " ".join([r.intro, *r.ingredients, *r.steps, r.notes])
        return tokenize(r.title) * TITLE_WEIGHT + tokenize(meta) + tokenize(body)

    def search(self, query: str, allowed: set[str] | None = None) -> list[tuple[str, float]]:
        terms = tokenize(query)
        scored = []
        for i, doc_id in enumerate(self._ids):
            if allowed is not None and doc_id not in allowed:
                continue
            tf, score = self._tf[i], 0.0
            norm = self._k1 * (1 - self._b + self._b * self._len[i] / self._avg)
            for t in terms:
                if t in tf:
                    score += self._idf[t] * tf[t] * (self._k1 + 1) / (tf[t] + norm)
            if score > 0:
                scored.append((doc_id, score))
        return sorted(scored, key=lambda x: (-x[1], x[0]))


def reciprocal_rank_fusion(rankings: Sequence[Sequence[str]], k: int = RRF_K) -> list[str]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, 1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores, key=lambda d: (-scores[d], d))


@dataclass(frozen=True)
class Hit:
    recipe: Recipe
    rrf_score: float
    bm25_rank: int | None
    vector_rank: int | None


@dataclass(frozen=True)
class RetrievalResult:
    hits: list[Hit]
    candidates_after_filter: int
    degraded: bool  # True if embeddings were not available for this query


class HybridRetriever:
    def __init__(self, recipes: Sequence[Recipe], embedder: Embedder, top_k: int = 5) -> None:
        self._recipes = {r.id: r for r in recipes}
        self._order = [r.id for r in recipes]
        self._bm25 = BM25Index(recipes)
        self._embedder = embedder
        self._top_k = top_k
        self._doc_vectors: np.ndarray | None = None
        try:
            vectors = embedder.embed_documents([r.as_prompt_text() for r in recipes])
            norms = np.linalg.norm(vectors, axis=1, keepdims=True)
            self._doc_vectors = vectors / np.where(norms == 0, 1, norms)
        except Exception:
            log.exception("document embedding failed; retrieval runs on BM25 only")

    @property
    def has_embeddings(self) -> bool:
        return self._doc_vectors is not None

    def _vector_ranking(self, query: str, allowed: list[str]) -> list[str] | None:
        if self._doc_vectors is None:
            return None
        try:
            q = self._embedder.embed_query(query)
        except Exception:
            log.exception("query embedding failed; falling back to BM25 only")
            return None
        q = q / (np.linalg.norm(q) or 1.0)
        index = {doc_id: i for i, doc_id in enumerate(self._order)}
        sims = {doc_id: float(self._doc_vectors[index[doc_id]] @ q) for doc_id in allowed}
        return sorted(allowed, key=lambda d: (-sims[d], d))

    def retrieve(self, query: str, constraints: Constraints) -> RetrievalResult:
        allowed = [i for i in self._order if satisfies(self._recipes[i], constraints)]
        if not allowed:
            return RetrievalResult(hits=[], candidates_after_filter=0, degraded=False)
        bm25 = [doc_id for doc_id, _ in self._bm25.search(query, set(allowed))]
        vector = self._vector_ranking(query, allowed)
        rankings = [bm25] if vector is None else [bm25, vector]
        fused = reciprocal_rank_fusion(rankings)[: self._top_k]
        bm25_rank = {d: n for n, d in enumerate(bm25, 1)}
        vector_rank = {d: n for n, d in enumerate(vector or [], 1)}
        hits = [
            Hit(
                recipe=self._recipes[d],
                rrf_score=sum(1 / (RRF_K + r[d]) for r in (bm25_rank, vector_rank) if d in r),
                bm25_rank=bm25_rank.get(d),
                vector_rank=vector_rank.get(d),
            )
            for d in fused
        ]
        return RetrievalResult(
            hits=hits, candidates_after_filter=len(allowed), degraded=vector is None
        )
