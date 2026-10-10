"""Build the corpus: `python -m recipe_qa.ingest [--pinned] [--out PATH]`.

Default: fetch the latest revision of every title in `selection.TITLES`.
`--pinned`: re-fetch the exact revisions recorded in the existing corpus file.
"""

import argparse
import sys
from collections import Counter
from pathlib import Path

from recipe_qa.ingest.build import build_corpus, rebuild_pinned
from recipe_qa.ingest.mediawiki import MediaWikiClient
from recipe_qa.ingest.selection import TITLES

DEFAULT_OUT = Path(__file__).resolve().parents[3] / "data" / "corpus.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--pinned", action="store_true", help="re-fetch recorded revisions")
    args = parser.parse_args(argv)

    client = MediaWikiClient(pause_s=0.5)
    recipes = (
        rebuild_pinned(client, args.out) if args.pinned else build_corpus(client, TITLES, args.out)
    )

    cuisines = Counter(c for r in recipes for c in r.cuisines)
    dishes = Counter(d for r in recipes for d in r.dish_categories)
    diets = Counter(r.diet or "unknown" for r in recipes)
    timed = sum(r.total_minutes is not None for r in recipes)
    no_ingredients = [r.id for r in recipes if not r.ingredients]
    print(f"wrote {len(recipes)} recipes to {args.out}")
    print(f"cuisines: {dict(cuisines)}")
    print(f"dish categories: {dict(dishes)}")
    print(f"diet: {dict(diets)}; with stated time: {timed}")
    if no_ingredients:
        print(f"WARNING: no ingredient list parsed for {no_ingredients}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
