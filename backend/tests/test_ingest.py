import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from recipe_qa.ingest.build import build_corpus
from recipe_qa.ingest.mediawiki import MediaWikiClient, PageNotFoundError

FIXTURES = Path(__file__).parent / "fixtures"


def _page(title: str, revid: int, wiki: str, cats: list[str]) -> dict[str, Any]:
    return {
        "title": title,
        "fullurl": "https://en.wikibooks.org/wiki/" + title.replace(" ", "_"),
        "revisions": [{"revid": revid, "slots": {"main": {"content": wiki}}}],
        "categories": [{"title": "Category:" + c} for c in cats],
    }


def _client(handler: Any) -> MediaWikiClient:
    return MediaWikiClient(http=httpx.Client(transport=httpx.MockTransport(handler)))


def test_fetch_pages_merges_category_continuation() -> None:
    wiki = (FIXTURES / "tarator.wiki").read_text()
    calls: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        calls.append(params)
        assert request.headers["user-agent"].startswith("RecipeQA-ingest/")
        title = "Cookbook:Tarator"
        if "clcontinue" not in params:
            page = _page(title, 7, wiki, ["Soup recipes"])
            return httpx.Response(
                200,
                json={
                    "continue": {"clcontinue": "1|x", "continue": "||"},
                    "query": {"pages": [page]},
                },
            )
        page = {"title": title, "categories": [{"title": "Category:Bulgarian recipes"}]}
        return httpx.Response(200, json={"query": {"pages": [page]}})

    pages = _client(handler).fetch_pages(["Cookbook:Tarator"])
    assert len(calls) == 2
    assert pages[0].categories == ["Soup recipes", "Bulgarian recipes"]
    assert pages[0].revision_id == 7
    assert pages[0].wikitext == wiki


def test_fetch_pages_raises_on_missing_page() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        page = {"title": "Cookbook:Nope", "missing": True}
        return httpx.Response(200, json={"query": {"pages": [page]}})

    with pytest.raises(PageNotFoundError, match="Cookbook:Nope"):
        _client(handler).fetch_pages(["Cookbook:Nope"])


def test_fetch_revisions_uses_revids() -> None:
    wiki = (FIXTURES / "tarator.wiki").read_text()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["revids"] == "11|12"
        assert "titles" not in request.url.params
        pages = [_page("Cookbook:A", 11, wiki, []), _page("Cookbook:B", 12, wiki, [])]
        return httpx.Response(200, json={"query": {"pages": pages}})

    pages = _client(handler).fetch_revisions([11, 12])
    assert [p.revision_id for p in pages] == [11, 12]


def test_build_corpus_is_sorted_and_deterministic(tmp_path: Path) -> None:
    wiki = (FIXTURES / "carbonara.wiki").read_text()

    def handler(request: httpx.Request) -> httpx.Response:
        titles = request.url.params["titles"].split("|")
        pages = [_page(t, len(t), wiki, ["Italian recipes"]) for t in titles]
        return httpx.Response(200, json={"query": {"pages": pages}})

    out = tmp_path / "corpus.json"
    build_corpus(_client(handler), ["Cookbook:Zeta", "Cookbook:Alpha"], out)
    first = out.read_text()
    build_corpus(_client(handler), ["Cookbook:Alpha", "Cookbook:Zeta"], out)
    assert out.read_text() == first  # same input set -> byte-identical file
    data = json.loads(first)
    assert [r["id"] for r in data["recipes"]] == ["alpha", "zeta"]
    assert data["license"] == "CC BY-SA 4.0"
    assert data["source"] == "https://en.wikibooks.org/wiki/Cookbook"
