"""ASGI entrypoint: `uvicorn recipe_qa.main:app`."""

from recipe_qa.api import create_app
from recipe_qa.config import Settings
from recipe_qa.logging_setup import configure

settings = Settings()  # type: ignore[call-arg]  # values come from the environment
configure(settings.log_level)
app = create_app(settings)
