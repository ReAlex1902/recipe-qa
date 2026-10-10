"""Deploy to Vercel. Safe to run any number of times (ADR-0003).

    python deploy/vercel.py deploy   # reconcile project + env, build, deploy GIT_SHA to prod
    python deploy/vercel.py verify   # smoke test the production URL

The project definition below is the source of truth. Each run creates the project if it
is missing, upserts its environment variables, then builds this checkout with the Vercel
CLI and deploys it to production — unless GIT_SHA is already the live production
deployment. The repository is never connected to Vercel: only CI deploys, only commits
that passed CI, and the source stays private.

Environment:
    VERCEL_TOKEN     Vercel access token (team scope)
    GIT_SHA          commit being deployed
    GOOGLE_API_KEY   Gemini API key                        (deploy)
    OPS_TOKEN        bearer token for /ops/* endpoints     (deploy, verify)
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "backend"
API = "https://api.vercel.com"
PROJECT = "recipe-qa"
CLI = "vercel@63.1.2"

PLAIN_ENV = {
    "TRUSTED_PROXY_HOPS": "1",
    "RATE_LIMIT_PER_MINUTE": "10",
    "DAILY_LLM_BUDGET": "2000",
    "LOG_LEVEL": "INFO",
}
SECRET_ENV = ("GOOGLE_API_KEY", "OPS_TOKEN")


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        sys.exit(f"missing environment variable {name}")
    return value


def api(method: str, path: str, body: object | None = None, **query: str) -> Any:
    params = {"teamId": team_id(), **query} if team_id() else dict(query)
    qs = urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    for attempt in range(5):
        req = urllib.request.Request(  # noqa: S310 - fixed https host
            f"{API}{path}?{qs}",
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {_env('VERCEL_TOKEN')}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
                raw = resp.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and method == "GET":
                return None
            if exc.code in (429, 500, 502, 503, 504) and attempt < 4:
                time.sleep(2**attempt)
                continue
            sys.exit(f"Vercel API {method} {path} -> {exc.code}: {exc.read().decode()[:500]}")
    raise AssertionError("unreachable")


_TEAM: list[str] = []


def team_id() -> str:
    if not _TEAM:
        req = urllib.request.Request(  # noqa: S310
            f"{API}/v2/user", headers={"Authorization": f"Bearer {_env('VERCEL_TOKEN')}"}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            _TEAM.append(json.loads(resp.read())["user"].get("defaultTeamId") or "")
    return _TEAM[0]


def ensure_project() -> dict[str, Any]:
    project = api("GET", f"/v9/projects/{PROJECT}")
    if project is None:
        project = api("POST", "/v11/projects", {"name": PROJECT, "framework": "fastapi"})
        print(f"created project {project['id']}")
    values = {**PLAIN_ENV, **{k: _env(k) for k in SECRET_ENV}}
    for key, value in sorted(values.items()):
        api(
            "POST",
            f"/v10/projects/{project['id']}/env",
            {
                "key": key,
                "value": value,
                "type": "sensitive" if key in SECRET_ENV else "plain",
                "target": ["production"],
            },
            upsert="true",
        )
    print(f"reconciled project {project['id']} ({len(values)} env vars)")
    return dict(project)


def production_deployment(project_id: str) -> dict[str, Any] | None:
    found = api("GET", "/v6/deployments", projectId=project_id, target="production", limit="5")
    for d in (found or {}).get("deployments", []):
        if d.get("state") == "READY":
            return dict(d)
    return None


def production_url(project_id: str) -> str:
    domains = api("GET", f"/v9/projects/{project_id}/domains") or {}
    names = [d["name"] for d in domains.get("domains", []) if d.get("verified")]
    preferred = sorted(names, key=len)
    return f"https://{preferred[0]}" if preferred else f"https://{PROJECT}.vercel.app"


def run(*args: str) -> None:
    print("+", " ".join(a if not a.startswith("--token=") else "--token=***" for a in args))
    subprocess.run(args, cwd=APP_DIR, check=True)  # noqa: S603


def deploy() -> None:
    git_sha = _env("GIT_SHA")
    project = ensure_project()
    current = production_deployment(project["id"])
    if current and current.get("meta", {}).get("gitSha") == git_sha:
        print(f"{git_sha} is already live in production; nothing to deploy")
    else:
        ui = ROOT / "frontend" / "dist"
        if not (ui / "index.html").is_file():
            sys.exit("frontend/dist is missing: run `npm run build` in frontend/ first")
        shutil.rmtree(APP_DIR / "static_ui", ignore_errors=True)
        shutil.copytree(ui, APP_DIR / "static_ui")
        link = APP_DIR / ".vercel"
        link.mkdir(exist_ok=True)
        (link / "project.json").write_text(
            json.dumps({"orgId": team_id(), "projectId": project["id"]})
        )
        token = f"--token={_env('VERCEL_TOKEN')}"
        npx = ["npx", "--yes", CLI]
        run(*npx, "pull", "--yes", "--environment=production", token)
        run(*npx, "build", "--prod", token)
        run(
            *npx,
            "deploy",
            "--prebuilt",
            "--prod",
            f"--meta=gitSha={git_sha}",
            f"--env=GIT_SHA={git_sha}",
            token,
        )
    url = production_url(project["id"])
    print(f"live: {url}")
    if "GITHUB_OUTPUT" in os.environ:
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write(f"url={url}\n")


def _request(url: str, payload: object | None = None, token: str | None = None) -> tuple[int, str]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers)  # noqa: S310
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def verify() -> None:
    git_sha = _env("GIT_SHA")
    project = api("GET", f"/v9/projects/{PROJECT}")
    if project is None:
        sys.exit("project not found")
    url = production_url(project["id"])
    status, body = _request(f"{url}/healthz")
    version = json.loads(body).get("version") if status == 200 else None
    checks = [
        (f"healthz version == {git_sha[:7]}", version == git_sha),
        ("UI served", _request(f"{url}/")[0] == 200),
        ("empty question -> 422", _request(f"{url}/ask", {"question": ""})[0] == 422),
    ]
    status, body = _request(f"{url}/ask", {"question": "I ate peanuts and my throat is swelling"})
    checks.append(("safety refusal", status == 200 and '"refusal_reason":"safety"' in body))
    checks.append(("ops requires token", _request(f"{url}/ops/status")[0] == 401))
    status, body = _request(f"{url}/ops/status", token=_env("OPS_TOKEN"))
    checks.append(("ops status with token", status == 200))
    if status == 200:
        checks.append(("precomputed embeddings loaded", json.loads(body)["embeddings_ready"]))
    for name, ok in checks:
        print(f"{'ok  ' if ok else 'FAIL'} {name}")
    if not all(ok for _, ok in checks):
        sys.exit("smoke test failed")
    print(f"verified {url}")


if __name__ == "__main__":
    commands = {"deploy": deploy, "verify": verify}
    if len(sys.argv) != 2 or sys.argv[1] not in commands:
        sys.exit(__doc__)
    commands[sys.argv[1]]()
