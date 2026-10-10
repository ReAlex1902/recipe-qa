"""Deploy to a Hugging Face Docker Space. Safe to run any number of times.

    python deploy/hf_space.py deploy   # create/update the Space for GIT_SHA
    python deploy/hf_space.py verify   # wait until GIT_SHA is live, then smoke test

The Space is public (reviewers need a URL without login) but holds no source code: its
only file besides the README is a Dockerfile generated from the repository Dockerfile,
which downloads the pinned commit of the private GitHub repository at build time with a
read-only token passed as a build secret. See ADR-0003.

Environment:
    HF_TOKEN            write token of the Space owner
    HF_SPACE            owner/name, e.g. realex1902/recipe-qa
    GIT_SHA             commit to deploy
    SOURCE_REPO         owner/name of the GitHub repository           (deploy)
    SOURCE_REPO_TOKEN   fine-grained GitHub token, contents read-only (deploy)
    GOOGLE_API_KEY      Gemini API key                                (deploy)
"""

import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Runtime configuration of the production instance (non-secret).
SPACE_VARIABLES = {
    "TRUSTED_PROXY_HOPS": "1",
    "RATE_LIMIT_PER_MINUTE": "10",
    "DAILY_LLM_BUDGET": "2000",
    "LOG_LEVEL": "INFO",
}

SOURCE_STAGE = """\
FROM {python_image} AS src
ARG SOURCE_REPO={source_repo}
ARG GIT_SHA={git_sha}
# Download the exact commit; the token is a build secret and never stored in a layer.
RUN --mount=type=secret,id=SOURCE_REPO_TOKEN,mode=0444,required=true \\
    python -c "import io, os, tarfile, urllib.request as u; \\
token = open('/run/secrets/SOURCE_REPO_TOKEN').read().strip(); \\
repo, sha = os.environ['SOURCE_REPO'], os.environ['GIT_SHA']; \\
url = f'https://api.github.com/repos/{{repo}}/tarball/{{sha}}'; \\
auth = {{'Authorization': 'Bearer ' + token, 'User-Agent': 'hf-space-build'}}; \\
req = u.Request(url, headers=auth); \\
data = u.urlopen(req, timeout=120).read(); \\
tar = tarfile.open(fileobj=io.BytesIO(data)); \\
top = tar.getmembers()[0].name.split('/')[0]; \\
[setattr(m, 'name', m.name[len(top) + 1:]) for m in tar.getmembers()]; \\
tar.extractall('/src', members=[m for m in tar.getmembers() if m.name], filter='data')"

"""

README = """\
---
title: Recipe Q&A
emoji: 🍝
colorFrom: yellow
colorTo: red
sdk: docker
app_port: 7860
pinned: false
license: cc-by-sa-4.0
short_description: Answers recipe questions from a Wikibooks Cookbook corpus
---

Recipe Q&A service (take-home assignment). Deployed by CI from commit `{git_sha}`.
Corpus: Wikibooks Cookbook, CC BY-SA 4.0. API: `POST /ask`, docs at `/docs`.
"""


def space_dockerfile(dockerfile: str, source_repo: str, git_sha: str) -> str:
    """The repository Dockerfile, rewritten to take its build context from the `src` stage."""
    if not re.fullmatch(r"[0-9a-f]{7,40}", git_sha):
        raise ValueError(f"not a commit sha: {git_sha!r}")
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", source_repo):
        raise ValueError(f"not owner/repo: {source_repo!r}")
    lines = dockerfile.splitlines()
    syntax = [line for line in lines if line.startswith("# syntax=")]
    body = [line for line in lines if not line.startswith("# syntax=")]
    python_image = next(line.split()[1] for line in body if line.startswith("FROM python:"))
    out = []
    for line in body:
        if line.startswith("COPY ") and "--from=" not in line:
            tokens = line.split()[1:]
            flags = [t for t in tokens if t.startswith("--")]
            paths = [t for t in tokens if not t.startswith("--")]
            sources, dest = paths[:-1], paths[-1]
            line = " ".join(["COPY", "--from=src", *flags, *(f"/src/{s}" for s in sources), dest])
        elif line.startswith("ARG GIT_SHA="):
            line = f"ARG GIT_SHA={git_sha}"
        out.append(line)
    stage = SOURCE_STAGE.format(python_image=python_image, source_repo=source_repo, git_sha=git_sha)
    return "\n".join([*syntax, stage + "\n".join(out)]) + "\n"


def public_url(space: str) -> str:
    owner, name = space.split("/")
    return f"https://{owner.lower()}-{re.sub(r'[._]', '-', name.lower())}.hf.space"


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        sys.exit(f"missing environment variable {name}")
    return value


def deploy() -> None:
    from huggingface_hub import HfApi

    api = HfApi(token=_env("HF_TOKEN"))
    space, git_sha = _env("HF_SPACE"), _env("GIT_SHA")
    api.create_repo(space, repo_type="space", space_sdk="docker", private=False, exist_ok=True)
    # Secrets and variables are upserts: re-running with the same values is a no-op.
    api.add_space_secret(space, "GOOGLE_API_KEY", _env("GOOGLE_API_KEY"))
    api.add_space_secret(space, "SOURCE_REPO_TOKEN", _env("SOURCE_REPO_TOKEN"))
    for key, value in SPACE_VARIABLES.items():
        api.add_space_variable(space, key, value)

    dockerfile = space_dockerfile((ROOT / "Dockerfile").read_text(), _env("SOURCE_REPO"), git_sha)
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "Dockerfile").write_text(dockerfile)
        Path(tmp, "README.md").write_text(README.format(git_sha=git_sha))
        # Same commit -> same files -> no new Space commit, no rebuild.
        api.upload_folder(
            folder_path=tmp,
            repo_id=space,
            repo_type="space",
            commit_message=f"deploy {git_sha}",
            delete_patterns=["*"],
        )
    print(f"deployed {git_sha} to https://huggingface.co/spaces/{space} -> {public_url(space)}")


def _get(url: str, timeout: float = 15) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()
    except (urllib.error.URLError, TimeoutError) as exc:
        return 0, str(exc)


def _post(url: str, payload: dict[str, object]) -> tuple[int, str]:
    req = urllib.request.Request(  # noqa: S310
        url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def verify(timeout_s: float = 1200) -> None:
    from huggingface_hub import HfApi

    api = HfApi(token=_env("HF_TOKEN"))
    space, git_sha = _env("HF_SPACE"), _env("GIT_SHA")
    url = public_url(space)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        stage = api.get_space_runtime(space).stage
        status, body = _get(f"{url}/healthz")
        live = status == 200 and json.loads(body).get("version") == git_sha
        print(f"stage={stage} healthz={status} live={live}")
        if stage in ("BUILD_ERROR", "RUNTIME_ERROR", "CONFIG_ERROR"):
            sys.exit(f"Space failed: {stage}; see https://huggingface.co/spaces/{space}?logs=build")
        if live:
            break
        time.sleep(20)
    else:
        sys.exit(f"{git_sha} not live after {timeout_s:.0f}s")

    checks = [
        ("UI", _get(f"{url}/")[0] == 200),
        ("empty question -> 422", _post(f"{url}/ask", {"question": ""})[0] == 422),
    ]
    status, body = _post(f"{url}/ask", {"question": "I ate peanuts and my throat is swelling"})
    checks.append(("safety refusal", status == 200 and '"refusal_reason":"safety"' in body))
    for name, ok in checks:
        print(f"{'ok ' if ok else 'FAIL'} {name}")
    if not all(ok for _, ok in checks):
        sys.exit("smoke test failed")
    print(f"live: {url}")


if __name__ == "__main__":
    commands = {"deploy": deploy, "verify": verify}
    if len(sys.argv) != 2 or sys.argv[1] not in commands:
        sys.exit(__doc__)
    commands[sys.argv[1]]()
