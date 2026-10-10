from collections.abc import Sequence

import numpy as np

from recipe_qa.filters import satisfies
from recipe_qa.models import Constraints, Recipe
from recipe_qa.retrieval import BM25Index, HybridRetriever, reciprocal_rank_fusion, tokenize

from .conftest import FakeEmbedder, make_recipe


def test_tokenize_folds_case_accents_plurals_and_stopwords() -> None:
    assert tokenize("How do I make the Tiramisù with Eggs?") == ["tiramisu", "egg"]


def test_bm25_prefers_title_match(small_corpus: list[Recipe]) -> None:
    index = BM25Index(small_corpus)
    ranked = index.search("carbonara")
    assert [doc_id for doc_id, _ in ranked][:2] == ["carbonara-pasta", "spaghetti-alla-carbonara"]
    assert all(score > 0 for _, score in ranked)


def test_bm25_returns_nothing_for_unknown_words(small_corpus: list[Recipe]) -> None:
    assert BM25Index(small_corpus).search("sushi nigiri") == []


def test_rrf_combines_rankings() -> None:
    fused = reciprocal_rank_fusion([["a", "b", "c"], ["b", "c", "a"]], k=60)
    assert fused[0] == "b"
    assert set(fused) == {"a", "b", "c"}


def test_filters_time_diet_and_exclusions() -> None:
    quick_veg = Constraints(max_total_minutes=30, diet="vegetarian")
    assert satisfies(make_recipe("a", total_minutes=25, diet="vegan"), quick_veg)
    assert satisfies(make_recipe("b", total_minutes=30, diet="vegetarian"), quick_veg)
    assert not satisfies(make_recipe("c", total_minutes=31, diet="vegetarian"), quick_veg)
    # unknown time / unknown diet cannot be proven to fit (SPEC 5.1)
    assert not satisfies(make_recipe("d", total_minutes=None, diet="vegan"), quick_veg)
    assert not satisfies(make_recipe("e", total_minutes=10, diet=None), quick_veg)
    vegan = Constraints(diet="vegan")
    assert not satisfies(make_recipe("f", diet="vegetarian"), vegan)
    no_onion = Constraints(exclude_ingredients=["onion"])
    assert not satisfies(make_recipe("g", ingredients=["2 red onions, sliced"]), no_onion)
    assert satisfies(make_recipe("h", ingredients=["garlic"]), no_onion)
    assert satisfies(make_recipe("i"), Constraints())


def test_hybrid_applies_filters_before_ranking(small_corpus: list[Recipe]) -> None:
    retriever = HybridRetriever(small_corpus, FakeEmbedder())
    result = retriever.retrieve("curry", Constraints(max_total_minutes=30, diet="vegetarian"))
    ids = [h.recipe.id for h in result.hits]
    assert set(ids) == {"paneer-butter-masala", "potato-curry"}
    assert result.candidates_after_filter == 2


def test_hybrid_uses_embeddings_when_words_do_not_match(small_corpus: list[Recipe]) -> None:
    retriever = HybridRetriever(small_corpus, FakeEmbedder())
    # 'dessert' is only in the dish category of tiramisu; BM25 also sees it via category.
    result = retriever.retrieve("something sweet, a dessert", Constraints())
    assert result.hits[0].recipe.id == "tiramisu"
    assert not result.degraded


def test_hybrid_top_k_and_scores_are_reported(small_corpus: list[Recipe]) -> None:
    retriever = HybridRetriever(small_corpus, FakeEmbedder(), top_k=3)
    result = retriever.retrieve("carbonara pasta with egg", Constraints())
    assert len(result.hits) == 3
    assert result.hits[0].recipe.id in {"carbonara-pasta", "spaghetti-alla-carbonara"}
    assert result.hits[0].bm25_rank == 1


class BrokenEmbedder(FakeEmbedder):
    def embed_query(self, text: str) -> np.ndarray:
        raise RuntimeError("provider down")


class BrokenDocsEmbedder(FakeEmbedder):
    def embed_documents(self, texts: Sequence[str]) -> np.ndarray:
        raise RuntimeError("provider down")


def test_hybrid_degrades_to_bm25_when_query_embedding_fails(small_corpus: list[Recipe]) -> None:
    result = HybridRetriever(small_corpus, BrokenEmbedder()).retrieve("carbonara", Constraints())
    assert result.degraded
    assert result.hits[0].recipe.id in {"carbonara-pasta", "spaghetti-alla-carbonara"}


def test_hybrid_works_without_document_embeddings(small_corpus: list[Recipe]) -> None:
    retriever = HybridRetriever(small_corpus, BrokenDocsEmbedder())
    result = retriever.retrieve("carbonara", Constraints())
    assert result.degraded
    assert result.hits


def test_no_candidates_after_filter(small_corpus: list[Recipe]) -> None:
    retriever = HybridRetriever(small_corpus, FakeEmbedder())
    result = retriever.retrieve("dessert", Constraints(max_total_minutes=5, diet="vegan"))
    assert result.hits == []
    assert result.candidates_after_filter == 0
