"""Run the golden set against a running service and report the results.

    uv run --project backend python evals/run_evals.py --base-url http://localhost:8000

Every /ask response is validated against `evals/response.schema.json` and the invariants
I1-I7 of SPEC 3.1, then against the per-case expectations in `golden.yaml`. Constraint
checks use corpus metadata (`backend/data/corpus.json`), not the answer text.

Exit code 0 only if the acceptance thresholds of SPEC AC2 are met.
"""

import argparse
import json
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import jsonschema
import yaml

ROOT = Path(__file__).resolve().parents[1]
EVALS = ROOT / "evals"
SCHEMA = json.loads((EVALS / "response.schema.json").read_text())
_CORPUS_FILE = ROOT / "backend" / "data" / "corpus.json"
CORPUS = {r["id"]: r for r in json.loads(_CORPUS_FILE.read_text())["recipes"]}

PASS_RATE_TARGET = 0.90  # AC2
DIET_OK = {"vegetarian": {"vegetarian", "vegan"}, "vegan": {"vegan"}}

# Gemini list prices, USD per 1M tokens (README, Cost & latency). Unknown model -> no cost.
PRICES = {
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-3.1-flash-lite": (0.25, 1.50),
    "gemini-3.5-flash-lite": (0.30, 2.50),
}


@dataclass
class CaseResult:
    id: str
    kind: str
    passed: bool
    failures: list[str] = field(default_factory=list)
    latency_ms: float = 0.0
    refused: bool | None = None
    refusal_reason: str | None = None
    cited: list[str] = field(default_factory=list)
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    answer: str | None = None


def check_invariants(body: dict[str, Any], headers: httpx.Headers) -> list[str]:
    errors = []
    try:
        jsonschema.validate(body, SCHEMA)
    except jsonschema.ValidationError as exc:
        return [f"schema: {exc.message}"]
    if not body["refused"]:
        if not body["answer"] or not body["citations"] or body["refusal_reason"] is not None:
            errors.append("I1: answer needs text, citations and no refusal_reason")
    elif body["refusal_reason"] is None or body["citations"]:
        errors.append("I2: refusal needs refusal_reason and no citations")
    for c in body["citations"]:
        r = CORPUS.get(c["recipe_id"])
        if r is None or (c["title"], c["url"]) != (r["title"], r["url"]):
            errors.append(f"I3: citation {c['recipe_id']} does not match the corpus")
    if body["constraints"].keys() != {"max_total_minutes", "diet", "exclude_ingredients"}:
        errors.append("I6: constraints must echo the three parsed fields")
    if body["allergy_check"] is not None and not body["safety_notice"]:
        errors.append("I5: allergy_check without safety_notice")
    if headers.get("x-request-id") != body["request_id"]:
        errors.append("I7: X-Request-ID header differs from request_id")
    return errors


def check_expectations(body: dict[str, Any], expect: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    cited = [c["recipe_id"] for c in body["citations"]]
    answer = (body["answer"] or "").lower()

    if body["refused"] != expect["refused"]:
        errors.append(
            f"refused={body['refused']} ({body['refusal_reason']}), expected {expect['refused']}"
        )
    if "refusal_reason" in expect and body["refusal_reason"] != expect["refusal_reason"]:
        errors.append(
            f"refusal_reason={body['refusal_reason']}, expected {expect['refusal_reason']}"
        )
    if "cite_any" in expect and not set(cited) & set(expect["cite_any"]):
        errors.append(f"cited {cited}, expected any of {expect['cite_any']}")
    if "cite_all" in expect:
        missing = set(expect["cite_all"]) - set(cited)
        if missing:
            errors.append(f"cited {cited}, missing {sorted(missing)}")

    if "constraints" in expect:
        want = expect["constraints"]
        got = body["constraints"]
        for key, value in want.items():
            if got.get(key) != value:
                errors.append(f"parsed constraint {key}={got.get(key)}, expected {value}")
        for rid in cited:
            r = CORPUS[rid]
            if "max_total_minutes" in want and (
                r["total_minutes"] is None or r["total_minutes"] > want["max_total_minutes"]
            ):
                errors.append(f"{rid}: total_minutes={r['total_minutes']} breaks constraint")
            if "diet" in want and r["diet"] not in DIET_OK[want["diet"]]:
                errors.append(f"{rid}: diet={r['diet']} breaks constraint")
            for word in want.get("exclude_ingredients", []):
                if any(word in line.lower() for line in r["ingredients"]):
                    errors.append(f"{rid}: contains excluded '{word}'")

    if "allergy_groups" not in expect and body["allergy_check"] is not None:
        errors.append("I4: allergy_check set on a non-allergy question")
    if "allergy_groups" in expect:
        check = body["allergy_check"] or {}
        if check.get("allergens") != expect["allergy_groups"]:
            errors.append(
                f"allergens={check.get('allergens')}, expected {expect['allergy_groups']}"
            )
        matched = {m["recipe_id"] for m in check.get("matches", [])}
        if "allergy_match_any" in expect and not matched & set(expect["allergy_match_any"]):
            errors.append(
                f"allergy matches {sorted(matched)}, expected any of {expect['allergy_match_any']}"
            )
        if not body["safety_notice"]:
            errors.append("allergy question without safety_notice")
    for phrase in expect.get("forbid_phrases", []):
        if phrase.lower() in answer:
            errors.append(f"answer contains forbidden phrase '{phrase}'")
    if "answer_contains_any" in expect and not any(
        p.lower() in answer for p in expect["answer_contains_any"]
    ):
        errors.append(f"answer contains none of {expect['answer_contains_any']}")
    return errors


def run_case(client: httpx.Client, case: dict[str, Any]) -> CaseResult:
    start = time.perf_counter()
    resp = client.post("/ask", json={"question": case["question"]})
    latency = (time.perf_counter() - start) * 1000
    result = CaseResult(id=case["id"], kind="golden", passed=False, latency_ms=latency)
    if resp.status_code != 200:
        result.failures.append(f"HTTP {resp.status_code}: {resp.text[:200]}")
        return result
    body = resp.json()
    result.refused = body.get("refused")
    result.refusal_reason = body.get("refusal_reason")
    result.cited = [c["recipe_id"] for c in body.get("citations", [])]
    result.answer = body.get("answer")
    result.model = resp.headers.get("x-llm-model")
    result.input_tokens = int(resp.headers.get("x-llm-input-tokens", 0))
    result.output_tokens = int(resp.headers.get("x-llm-output-tokens", 0))
    result.failures = check_invariants(body, resp.headers)
    if not result.failures:
        result.failures = check_expectations(body, case["expect"])
    result.passed = not result.failures
    return result


def run_contract(client: httpx.Client, case: dict[str, Any]) -> CaseResult:
    body = dict(case["body"])
    if body.get("question") == "LONG_501":
        body["question"] = "x" * 501
    start = time.perf_counter()
    resp = client.post("/ask", json=body)
    result = CaseResult(
        id=case["id"],
        kind="contract",
        passed=False,
        latency_ms=(time.perf_counter() - start) * 1000,
    )
    if resp.status_code != case["status"]:
        result.failures.append(f"HTTP {resp.status_code}, expected {case['status']}")
    else:
        code = resp.json().get("error", {}).get("code")
        if code != case["error_code"]:
            result.failures.append(f"error code {code}, expected {case['error_code']}")
    result.passed = not result.failures
    return result


def cost_usd(results: list[CaseResult]) -> float | None:
    total = 0.0
    for r in results:
        if r.model is None or r.input_tokens == 0:
            continue
        price = next((p for m, p in PRICES.items() if r.model.startswith(m)), None)
        if price is None:
            return None
        total += r.input_tokens * price[0] / 1e6 + r.output_tokens * price[1] / 1e6
    return total


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--only", nargs="*", help="run only these case ids")
    parser.add_argument(
        "--repeat", type=int, default=1,
        help="run the golden set N times (repeats are served from the server cache)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=6.5,
        help="seconds between requests (service limit: 10/min per IP)",
    )
    parser.add_argument("--report", type=Path, default=EVALS / "reports")
    args = parser.parse_args()

    spec = yaml.safe_load((EVALS / "golden.yaml").read_text())
    cases = [c for c in spec["cases"] if not args.only or c["id"] in args.only]
    results: list[CaseResult] = []
    with httpx.Client(base_url=args.base_url, timeout=60) as client:
        client.get("/healthz").raise_for_status()  # also wakes a sleeping instance
        for n in range(args.repeat):
            for case in cases:
                r = run_case(client, case)
                results.append(r)
                mark = "PASS" if r.passed else "FAIL"
                print(
                    f"[{n + 1}] {mark} {r.id:28} {r.latency_ms:7.0f} ms  "
                    f"{'refused:' + str(r.refusal_reason) if r.refused else r.cited}"
                )
                for f in r.failures:
                    print(f"        - {f}")
                time.sleep(args.delay)
        if not args.only:
            for case in spec["contract"]:
                r = run_contract(client, case)
                results.append(r)
                print(f"    {'PASS' if r.passed else 'FAIL'} {r.id:28} (contract)")
                for f in r.failures:
                    print(f"        - {f}")

    golden = [r for r in results if r.kind == "golden"]
    contract = [r for r in results if r.kind == "contract"]
    expected_refused = {c["id"]: c["expect"]["refused"] for c in cases}
    refusal_ok = [r.refused == expected_refused[r.id] for r in golden]
    # Classify by the X-LLM-Model header: "cache" hits are neither model calls nor
    # pre-model refusals, so they are excluded from both latency groups.
    llm = [r.latency_ms for r in golden if r.model not in (None, "none", "cache")]
    no_llm = [r.latency_ms for r in golden if r.model == "none"]
    pass_rate = sum(r.passed for r in golden) / max(len(golden), 1)
    refusal_rate = sum(refusal_ok) / max(len(refusal_ok), 1)
    cost = cost_usd(golden)
    llm_calls = sum(1 for r in golden if r.input_tokens)
    summary = {
        "base_url": args.base_url,
        "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "models": sorted({r.model for r in golden if r.model}),
        "cache_hits": sum(1 for r in golden if r.model == "cache"),
        "golden_passed": f"{sum(r.passed for r in golden)}/{len(golden)}",
        "golden_pass_rate": round(pass_rate, 3),
        "refused_flag_correct_rate": round(refusal_rate, 3),
        "contract_passed": f"{sum(r.passed for r in contract)}/{len(contract)}",
        "latency_ms_llm": {
            "p50": round(statistics.median(llm)) if llm else None,
            "p95": round(percentile(llm, 0.95)),
        },
        "latency_ms_no_llm": {
            "p50": round(statistics.median(no_llm)) if no_llm else None,
            "p95": round(percentile(no_llm, 0.95)),
        },
        "tokens_per_llm_call": {
            "input": round(sum(r.input_tokens for r in golden) / max(llm_calls, 1)),
            "output": round(sum(r.output_tokens for r in golden) / max(llm_calls, 1)),
        },
        "cost_usd_total": round(cost, 6) if cost is not None else None,
        "cost_usd_per_1000_questions": round(cost / len(golden) * 1000, 3)
        if cost is not None and golden
        else None,
    }
    print(json.dumps(summary, indent=2))

    args.report.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = args.report / f"eval-{stamp}.json"
    out.write_text(
        json.dumps(
            {"summary": summary, "results": [r.__dict__ for r in results]},
            indent=1,
            ensure_ascii=False,
        )
    )
    print(f"report: {out}")

    ok = pass_rate >= PASS_RATE_TARGET and refusal_rate == 1.0 and all(r.passed for r in contract)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
