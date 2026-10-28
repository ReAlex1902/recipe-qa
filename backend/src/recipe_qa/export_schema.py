"""Write the /ask response JSON Schema for the eval harness: `python -m recipe_qa.export_schema`."""

import json
from pathlib import Path

from recipe_qa.config import REPO_DIR
from recipe_qa.schema import AskResponse

SCHEMA_PATH = REPO_DIR / "evals" / "response.schema.json"


def render() -> str:
    return json.dumps(AskResponse.model_json_schema(), indent=2, sort_keys=True) + "\n"


def main(path: Path = SCHEMA_PATH) -> None:
    path.write_text(render())
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
