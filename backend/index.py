"""Vercel entrypoint (ADR-0003): the whole FastAPI app runs as one Vercel Function."""

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "src"))
os.environ.setdefault("CORPUS_PATH", str(HERE / "data" / "corpus.json"))
os.environ.setdefault("STATIC_DIR", str(HERE / "static_ui"))

from recipe_qa.main import app  # noqa: E402

__all__ = ["app"]
