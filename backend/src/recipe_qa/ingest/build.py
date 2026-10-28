import json
from collections.abc import Sequence
from pathlib import Path

from recipe_qa.ingest.mediawiki import MediaWikiClient, Page
from recipe_qa.ingest.wikitext import parse_recipe
from recipe_qa.models import Recipe

SOURCE = "https://en.wikibooks.org/wiki/Cookbook"
LICENSE = "CC BY-SA 4.0"


def to_recipes(pages: Sequence[Page]) -> list[Recipe]:
    recipes = [
        parse_recipe(
            title=p.title,
            url=p.url,
            revision_id=p.revision_id,
            wikitext=p.wikitext,
            categories=p.categories,
        )
        for p in pages
    ]
    return sorted(recipes, key=lambda r: r.id)


def write_corpus(recipes: Sequence[Recipe], out: Path) -> None:
    ids = [r.id for r in recipes]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate recipe ids in corpus")
    data = {
        "source": SOURCE,
        "license": LICENSE,
        "recipes": [r.model_dump() for r in recipes],
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n")


def build_corpus(client: MediaWikiClient, titles: Sequence[str], out: Path) -> list[Recipe]:
    recipes = to_recipes(client.fetch_pages(titles))
    write_corpus(recipes, out)
    return recipes


def rebuild_pinned(client: MediaWikiClient, corpus_path: Path) -> list[Recipe]:
    """Re-fetch the exact revisions listed in an existing corpus file."""
    current = json.loads(corpus_path.read_text())
    revids = [r["revision_id"] for r in current["recipes"]]
    recipes = to_recipes(client.fetch_revisions(revids))
    write_corpus(recipes, corpus_path)
    return recipes
