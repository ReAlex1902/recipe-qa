"""Deploy to Render through its API. Safe to run any number of times (ADR-0003).

    python deploy/render.py deploy   # reconcile service config, deploy GIT_SHA, wait for live
    python deploy/render.py verify   # smoke test the live service

The service definition below is the source of truth: every run creates the service if it
is missing, or overwrites its settings and environment with these values. Deploys are
triggered for an exact commit (the one CI just tested), never "latest on branch".
If that commit is already live, nothing is redeployed.

Environment:
    RENDER_API_KEY   Render API key
    GIT_SHA          commit to deploy
    GOOGLE_API_KEY   Gemini API key                        (deploy)
    OPS_TOKEN        bearer token for /ops/* endpoints     (deploy, verify)
    GITHUB_REPO      owner/name of the repository          (deploy)
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any

API = "https://api.render.com/v1"
SERVICE_NAME = "recipe-qa"
BRANCH = "main"
REGION = "frankfurt"

# Non-secret runtime configuration of the production instance.
PLAIN_ENV = {
    "TRUSTED_PROXY_HOPS": "1",
    "RATE_LIMIT_PER_MINUTE": "10",
    "DAILY_LLM_BUDGET": "2000",
    "LOG_LEVEL": "INFO",
}
SECRET_ENV = ("GOOGLE_API_KEY", "OPS_TOKEN")

TERMINAL_OK = {"live"}
TERMINAL_FAILED = {
    "build_failed",
    "update_failed",
    "canceled",
    "pre_deploy_failed",
    "deactivated",
}


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        sys.exit(f"missing environment variable {name}")
    return value


def api(method: str, path: str, body: object | None = None) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    for attempt in range(5):
        req = urllib.request.Request(  # noqa: S310 - fixed https host
            f"{API}{path}",
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {_env('RENDER_API_KEY')}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
                raw = resp.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504) and attempt < 4:
                time.sleep(2**attempt)
                continue
            sys.exit(f"Render API {method} {path} -> {exc.code}: {exc.read().decode()[:500]}")
    raise AssertionError("unreachable")


def service_details() -> dict[str, Any]:
    return {
        "runtime": "docker",
        "plan": "free",
        "region": REGION,
        "healthCheckPath": "/healthz",
        "numInstances": 1,
        "envSpecificDetails": {"dockerfilePath": "./Dockerfile", "dockerContext": "."},
    }


def env_vars() -> list[dict[str, str]]:
    values = {**PLAIN_ENV, **{k: _env(k) for k in SECRET_ENV}}
    return [{"key": k, "value": v} for k, v in sorted(values.items())]


def find_service() -> dict[str, Any] | None:
    for item in api("GET", f"/services?name={SERVICE_NAME}&limit=20") or []:
        if item["service"]["name"] == SERVICE_NAME:
            return dict(item["service"])
    return None


def owner_id() -> str:
    owners = api("GET", "/owners?limit=1")
    return str(owners[0]["owner"]["id"])


def ensure_service() -> dict[str, Any]:
    """Create the service, or reconcile its settings with this file."""
    service = find_service()
    if service is None:
        created = api(
            "POST",
            "/services",
            {
                "type": "web_service",
                "name": SERVICE_NAME,
                "ownerId": owner_id(),
                "repo": f"https://github.com/{_env('GITHUB_REPO')}",
                "branch": BRANCH,
                "autoDeployTrigger": "off",  # CI deploys tested commits only
                "envVars": env_vars(),
                "serviceDetails": service_details(),
            },
        )
        print(f"created service {created['service']['id']}")
        return dict(created["service"])
    details = service_details()
    api(
        "PATCH",
        f"/services/{service['id']}",
        {
            "branch": BRANCH,
            "autoDeployTrigger": "off",
            "serviceDetails": {
                "plan": details["plan"],
                "healthCheckPath": details["healthCheckPath"],
                "numInstances": details["numInstances"],
                "envSpecificDetails": details["envSpecificDetails"],
            },
        },
    )
    api("PUT", f"/services/{service['id']}/env-vars", env_vars())
    print(f"reconciled service {service['id']}")
    return service


def latest_deploys(service_id: str) -> list[dict[str, Any]]:
    return [d["deploy"] for d in api("GET", f"/services/{service_id}/deploys?limit=10") or []]


def wait_for_deploy(service_id: str, deploy_id: str, timeout_s: float = 1500) -> None:
    deadline = time.monotonic() + timeout_s
    status = "unknown"
    while time.monotonic() < deadline:
        status = api("GET", f"/services/{service_id}/deploys/{deploy_id}")["status"]
        print(f"deploy {deploy_id}: {status}")
        if status in TERMINAL_OK:
            return
        if status in TERMINAL_FAILED:
            sys.exit(f"deploy {deploy_id} ended with {status}")
        time.sleep(15)
    sys.exit(f"deploy {deploy_id} still {status} after {timeout_s:.0f}s")


def deploy() -> None:
    git_sha = _env("GIT_SHA")
    service = ensure_service()
    service_id = service["id"]
    deploys = latest_deploys(service_id)
    live = next((d for d in deploys if d["status"] == "live"), None)
    if live and live.get("commit", {}).get("id") == git_sha:
        print(f"{git_sha} is already live; nothing to deploy")
    else:
        # A deploy for this commit may already be running (e.g. right after creation).
        running = next(
            (
                d
                for d in deploys
                if d.get("commit", {}).get("id") == git_sha and d["status"] not in TERMINAL_FAILED
            ),
            None,
        )
        deploy_id = (
            running["id"]
            if running
            else api("POST", f"/services/{service_id}/deploys", {"commitId": git_sha})["id"]
        )
        wait_for_deploy(service_id, deploy_id)
    url = find_service()["serviceDetails"]["url"]  # type: ignore[index]
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
        with urllib.request.urlopen(req, timeout=90) as resp:  # noqa: S310
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def verify() -> None:
    git_sha = _env("GIT_SHA")
    service = find_service()
    if service is None:
        sys.exit("service not found")
    url = service["serviceDetails"]["url"]
    status, body = _request(f"{url}/healthz")  # also wakes a sleeping free instance
    version = json.loads(body).get("version") if status == 200 else None
    checks = [
        (f"healthz version == {git_sha[:7]}", status == 200 and version == git_sha),
        ("UI served", _request(f"{url}/")[0] == 200),
        ("empty question -> 422", _request(f"{url}/ask", {"question": ""})[0] == 422),
    ]
    status, body = _request(f"{url}/ask", {"question": "I ate peanuts and my throat is swelling"})
    checks.append(("safety refusal", status == 200 and '"refusal_reason":"safety"' in body))
    checks.append(("ops requires token", _request(f"{url}/ops/status")[0] == 401))
    status, _ = _request(f"{url}/ops/status", token=_env("OPS_TOKEN"))
    checks.append(("ops status with token", status == 200))
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
