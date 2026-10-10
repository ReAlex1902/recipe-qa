"""Minimal MediaWiki Action API client for en.wikibooks.org."""

import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx

API_URL = "https://en.wikibooks.org/w/api.php"
USER_AGENT = "RecipeQA-ingest/1.0 (take-home assignment; https://github.com/ReAlex1902)"
BATCH_SIZE = 50  # MediaWiki limit for titles/revids per request (anonymous)


class PageNotFoundError(LookupError):
    pass


@dataclass
class Page:
    title: str
    url: str
    revision_id: int
    wikitext: str
    categories: list[str] = field(default_factory=list)


class MediaWikiClient:
    def __init__(self, http: httpx.Client | None = None, pause_s: float = 0.0) -> None:
        self._http = http or httpx.Client(timeout=30)
        self._pause_s = pause_s

    def _get(self, params: dict[str, Any]) -> dict[str, Any]:
        query = {"action": "query", "format": "json", "formatversion": "2", **params}
        for attempt in range(3):
            resp = self._http.get(API_URL, params=query, headers={"User-Agent": USER_AGENT})
            if resp.status_code < 500 and resp.status_code != 429:
                resp.raise_for_status()
                data: dict[str, Any] = resp.json()
                if "error" in data:
                    raise RuntimeError(f"MediaWiki error: {data['error']}")
                return data
            time.sleep(2**attempt)
        resp.raise_for_status()
        raise RuntimeError("unreachable")

    def _query_all(self, params: dict[str, Any]) -> Iterator[dict[str, Any]]:
        """Follow `continue` until the result set is complete."""
        cont: dict[str, Any] = {}
        while True:
            data = self._get({**params, **cont})
            yield from data.get("query", {}).get("pages", [])
            if "continue" not in data:
                return
            cont = data["continue"]
            if self._pause_s:
                time.sleep(self._pause_s)

    def _fetch(self, key: str, values: Sequence[str]) -> list[Page]:
        pages: dict[str, Page] = {}
        order: list[str] = []
        for i in range(0, len(values), BATCH_SIZE):
            params = {
                key: "|".join(values[i : i + BATCH_SIZE]),
                "prop": "revisions|categories|info",
                "rvprop": "ids|content",
                "rvslots": "main",
                "inprop": "url",
                "cllimit": "max",
                "redirects": "1",
            }
            for raw in self._query_all(params):
                title = raw["title"]
                if raw.get("missing") or raw.get("invalid"):
                    raise PageNotFoundError(title)
                page = pages.get(title)
                if page is None and "revisions" in raw:
                    rev = raw["revisions"][0]
                    page = Page(
                        title=title,
                        url=raw["fullurl"],
                        revision_id=rev["revid"],
                        wikitext=rev["slots"]["main"]["content"],
                    )
                    pages[title] = page
                    order.append(title)
                if page is not None:
                    page.categories.extend(
                        c["title"].removeprefix("Category:") for c in raw.get("categories", [])
                    )
        return [pages[t] for t in order]

    def fetch_pages(self, titles: Sequence[str]) -> list[Page]:
        """Latest revision of each title."""
        return self._fetch("titles", titles)

    def fetch_revisions(self, revision_ids: Sequence[int]) -> list[Page]:
        """Exact revisions, for a byte-reproducible rebuild."""
        return self._fetch("revids", [str(r) for r in revision_ids])
