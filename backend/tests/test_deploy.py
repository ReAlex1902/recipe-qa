"""The generated Space Dockerfile (deploy/hf_space.py) must stay in sync with ./Dockerfile."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("hf_space", ROOT / "deploy" / "hf_space.py")
assert _spec and _spec.loader
hf_space = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hf_space)

SHA = "0123456789abcdef0123456789abcdef01234567"


def test_space_dockerfile_copies_only_from_source_stage() -> None:
    out = hf_space.space_dockerfile((ROOT / "Dockerfile").read_text(), "me/repo", SHA)
    assert out.startswith("# syntax=")
    copies = [line for line in out.splitlines() if line.startswith("COPY ")]
    assert copies
    assert all("--from=" in line for line in copies), "every COPY must use a stage"
    assert "COPY --from=src --chown=app:app /src/backend/data /app/backend/data" in out
    assert f"ARG GIT_SHA={SHA}" in out
    assert "ARG SOURCE_REPO=me/repo" in out
    assert "--mount=type=secret,id=SOURCE_REPO_TOKEN" in out


def test_space_dockerfile_is_deterministic() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()
    a = hf_space.space_dockerfile(dockerfile, "me/repo", SHA)
    assert a == hf_space.space_dockerfile(dockerfile, "me/repo", SHA)


@pytest.mark.parametrize(("repo", "sha"), [("me/repo", "main; rm -rf"), ("me repo", SHA)])
def test_space_dockerfile_rejects_unsafe_input(repo: str, sha: str) -> None:
    with pytest.raises(ValueError):
        hf_space.space_dockerfile("FROM python:3.12\n", repo, sha)


def test_public_url() -> None:
    assert hf_space.public_url("RealEx1902/recipe_qa") == "https://realex1902-recipe-qa.hf.space"
