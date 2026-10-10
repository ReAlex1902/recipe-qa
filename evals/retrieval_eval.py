"""Retrieval-only ablation on the golden set (ADR-0001). No LLM calls, ~20 embedding calls.

    uv run --project backend python evals/retrieval_eval.py

For every golden case with expected sources, reports whether the expected recipes are in
the top-k passed to the model, for BM25 only, embeddings only and the hybrid (RRF).
"""

from pathlib import Path

import yaml

from recipe_qa.api import load_corpus
from recipe_qa.config import Settings
from recipe_qa.constraints import parse_constraints
from recipe_qa.filters import satisfies
from recipe_qa.llm import GeminiEmbedder, make_client
from recipe_qa.retrieval import HybridRetriever, reciprocal_rank_fusion

EVALS = Path(__file__).resolve().parent
TOP_K = 5


def main() -> None:
    settings = Settings()  # type: ignore[call-arg]
    recipes = load_corpus(settings.corpus_path)
    client = make_client(settings.google_api_key.get_secret_value(), 30)
    embedder = GeminiEmbedder(client, settings.embedding_model, settings.embedding_dim)
    retriever = HybridRetriever(recipes, embedder, top_k=TOP_K)
    by_id = {r.id: r for r in recipes}

    cases = yaml.safe_load((EVALS / "golden.yaml").read_text())["cases"]
    totals = {"bm25": 0, "vector": 0, "hybrid": 0}
    n = 0
    print(f"{'case':26} {'bm25':>5} {'vector':>6} {'hybrid':>6}")
    for case in cases:
        expect = case["expect"]
        wanted = expect.get("cite_all") or expect.get("cite_any")
        if not wanted:
            continue
        need_all = "cite_all" in expect
        constraints = parse_constraints(case["question"])
        allowed = [r.id for r in recipes if satisfies(by_id[r.id], constraints)]
        bm25 = [d for d, _ in retriever._bm25.search(case["question"], set(allowed))]
        vector = retriever._vector_ranking(case["question"], allowed) or []
        hybrid = reciprocal_rank_fusion([bm25, vector])
        row = {}
        for name, ranking in (("bm25", bm25), ("vector", vector), ("hybrid", hybrid)):
            top = set(ranking[:TOP_K])
            hit = set(wanted) <= top if need_all else bool(set(wanted) & top)
            row[name] = hit
            totals[name] += hit
        n += 1
        print(f"{case['id']:26} {row['bm25']!s:>5} {row['vector']!s:>6} {row['hybrid']!s:>6}")
    print(f"{'recall@' + str(TOP_K):26} " + " ".join(f"{totals[k]}/{n}".rjust(6) for k in totals))


if __name__ == "__main__":
    main()
